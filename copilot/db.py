"""Read-only, store-scoped database access. Safety is enforced in code, not in prompts.

Four layers, each of which works even if the others fail:
1. The SQLite file is opened read-only (mode=ro).
2. A sqlglot guard allows exactly one SELECT/WITH statement over allow-listed tables.
3. Store scope: per connection, a TEMP VIEW with the same name shadows every
   store-level table and keeps only the stores this user may see. An authorizer
   then denies any read of the real table that does not come through that view.
4. A 5-second time budget and a 500-row cap.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import sqlglot
from sqlglot import exp

from copilot import config

logging.getLogger("sqlglot").setLevel(logging.ERROR)

STORE_TABLES = {"stores", "sku_map", "sales_daily", "waste_daily", "inventory_current", "transactions_daily"}
SHARED_TABLES = {"sku_master", "vendors", "category_targets", "cost_changes", "app_catalog"}
VIEWS = {"sku_velocity_14d"}
ALLOWED_TABLES = STORE_TABLES | SHARED_TABLES | VIEWS

MAX_ROWS = 500
TIME_BUDGET_S = 5.0

FORBIDDEN_NODES = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Pragma",
    "Attach", "Detach", "Merge", "TruncateTable", "Transaction", "Commit", "Rollback", "Set", "Use",
) if hasattr(exp, name))
BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class QueryError(Exception):
    """Base class. `str(err)` is safe to show to a store manager."""


class QueryRejected(QueryError):
    """The query was blocked for safety (not SELECT, outside store access, too slow...)."""


class QueryFailed(QueryError):
    """The query was allowed but SQLite could not run it (bad column name, syntax...)."""


@dataclass
class QueryResult:
    sql: str
    df: pd.DataFrame
    truncated: bool = False
    elapsed_ms: float = 0.0
    stores: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- guard
def guard_sql(sql: str) -> str:
    """Return the SQL if it is a single read-only query over allowed tables, else raise QueryRejected."""
    text = (sql or "").strip()
    if not text:
        raise QueryRejected("The query was empty.")
    try:
        statements = [s for s in sqlglot.parse(text, read="sqlite") if s is not None]
    except sqlglot.errors.SqlglotError as err:
        raise QueryRejected(f"I could not parse that SQL ({str(err).splitlines()[0][:120]}).") from None
    if len(statements) != 1:
        raise QueryRejected("Only one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise QueryRejected("Only read-only SELECT queries are allowed. I can't change data.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise QueryRejected("Only read-only SELECT queries are allowed. I can't change data.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.args.get("db") or table.args.get("catalog"):
            raise QueryRejected("Schema-qualified table names (like main.sales_daily) are not allowed.")
        name = (table.name or "").lower()
        if name in cte_names:
            continue
        if name not in ALLOWED_TABLES:
            shown = name or "that table function"
            raise QueryRejected(f"'{shown}' is not one of the tables I can read.")
    return text


# --------------------------------------------------------------------------- scoped connection
class StoreDB:
    """Opens a fresh read-only, store-scoped connection for every query.

    A new connection per query costs a few milliseconds and means no state,
    no threads to share and no way for one session to see another's scope.
    """

    def __init__(self, stores: list[str], db_path: Path | str = config.DB_PATH,
                 time_budget_s: float = TIME_BUDGET_S, max_rows: int = MAX_ROWS):
        valid = set(config.store_ids())
        scope = [s for s in stores if s in valid]
        if not scope:
            raise QueryRejected("That data is outside your store access.")
        self.stores = sorted(set(scope))
        self.db_path = Path(db_path)
        self.time_budget_s = time_budget_s
        self.max_rows = max_rows

    # -- public ----------------------------------------------------------------
    def run(self, sql: str) -> QueryResult:
        """Guard, then execute. Raises QueryRejected or QueryFailed."""
        return self._execute(guard_sql(sql))

    def scalar(self, sql: str):
        result = self.run(sql)
        return None if result.df.empty else result.df.iat[0, 0]

    # -- internals -------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
        con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        in_list = ", ".join(f"'{s}'" for s in self.stores)  # ids validated against config above
        for table in sorted(STORE_TABLES):
            con.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE store_id IN ({in_list})")
        # Re-create derived views in TEMP so they read the scoped tables, not the real ones.
        for (view_sql,) in con.execute("SELECT sql FROM main.sqlite_master WHERE type = 'view'").fetchall():
            con.execute(view_sql.replace("CREATE VIEW", "CREATE TEMP VIEW", 1))
        con.set_authorizer(self._authorizer)
        return con

    @staticmethod
    def _authorizer(action, arg1, arg2, db_name, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or "").lower() in BLOCKED_FUNCTIONS else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_RECURSIVE:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            # TEMP holds only the scoping views created in _connect (CREATE is denied afterwards).
            if db_name == "temp" and table in STORE_TABLES | VIEWS:
                return sqlite3.SQLITE_OK
            # db_name is None for SQLite's COUNT(*) shortcut; only shared tables may use it.
            if db_name in ("main", None) and table in SHARED_TABLES:
                return sqlite3.SQLITE_OK
            # A store-level table may only be read through its own scoping view.
            if db_name == "main" and table in STORE_TABLES and source == table:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    def _execute(self, sql: str) -> QueryResult:
        t0 = time.perf_counter()
        con = self._connect()
        deadline = time.monotonic() + self.time_budget_s
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20_000)
        try:
            cur = con.execute(sql)
            rows = cur.fetchmany(self.max_rows + 1)
            cols = [d[0] for d in cur.description or []]
        except sqlite3.DatabaseError as err:
            raise _friendly(err, self.time_budget_s) from None
        finally:
            con.close()
        truncated = len(rows) > self.max_rows
        df = pd.DataFrame(rows[: self.max_rows], columns=cols)
        return QueryResult(sql=sql, df=df, truncated=truncated,
                           elapsed_ms=(time.perf_counter() - t0) * 1000, stores=self.stores)


def _friendly(err: sqlite3.DatabaseError, budget: float) -> QueryError:
    msg = str(err)
    if "not authorized" in msg or "prohibited" in msg:
        return QueryRejected("That data is outside your store access.")
    if "interrupted" in msg:
        return QueryRejected(f"That query took longer than {budget:.0f} seconds, so I stopped it.")
    if "readonly" in msg or "read-only" in msg:
        return QueryRejected("The database is read-only. I can't change data.")
    return QueryFailed(f"SQLite error: {msg}")
