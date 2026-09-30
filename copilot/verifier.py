"""The verifier: a number is only labelled "Verified" when independent checks agree.

Why: in the Yardstick study, 82-97% of wrong LLM-written SQL ran cleanly and
returned plausible numbers (silent errors). So "the query ran" proves nothing.
Two kinds of checks:
  1. Independent query: a second, differently written query must agree within 0.5%.
  2. Sanity rules: not empty, no negative units/revenue/stock/cost/cover, no NULL keys.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from copilot.db import QueryError, StoreDB

TOLERANCE = 0.005
NON_NEGATIVE = re.compile(r"(units|revenue|on_hand|on_order|stock|cost|cogs|cover|transactions|basket|order_usd|waste)")
KEY_COLUMNS = {"store", "store_id", "sku", "master_sku_id", "product", "category", "vendor", "date"}

VERIFIED, DISAGREE, UNVERIFIED = "verified", "disagree", "unverified"
STATUS_TEXT = {
    VERIFIED: "Verified: an independent query and the sanity rules agree.",
    DISAGREE: "The checks don't agree, so treat this number with caution.",
    UNVERIFIED: "Not verified: there was nothing independent to check it against.",
}


@dataclass
class Check:
    name: str
    passed: bool | None  # None = could not run
    detail: str


@dataclass
class Verification:
    status: str
    checks: list[Check] = field(default_factory=list)

    @property
    def message(self) -> str:
        return STATUS_TEXT[self.status]


# --------------------------------------------------------------------------- comparisons
def close(a, b, tol: float = TOLERANCE) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(a - b) <= max(tol * max(abs(a), abs(b)), 0.01)


def value_of(df: pd.DataFrame, check_of: str) -> float:
    if check_of == "rows":
        return float(len(df))
    kind, col = check_of.split(":", 1)
    if kind == "sum":
        return float(pd.to_numeric(df[col]).sum())
    if kind == "first":
        return float(df[col].iloc[0])
    raise ValueError(f"unknown check_of {check_of!r}")


def compare_frames(main: pd.DataFrame, other: pd.DataFrame, tol: float = TOLERANCE) -> tuple[bool, str]:
    """Do two results say the same thing?

    Same shape: every numeric column, sorted, must match within `tol` (column
    names may differ, so columns are paired by position among numeric columns).
    Different shape: compare the grand total of the largest numeric column.
    """
    a_num, b_num = main.select_dtypes("number"), other.select_dtypes("number")
    if a_num.empty or b_num.empty:
        return False, "no numeric columns to compare"
    if main.shape == other.shape and a_num.shape[1] == b_num.shape[1]:
        for ca, cb in zip(a_num.columns, b_num.columns):
            xa = sorted(a_num[ca].fillna(0).astype(float))
            xb = sorted(b_num[cb].fillna(0).astype(float))
            bad = next((i for i, (x, y) in enumerate(zip(xa, xb)) if not close(x, y, tol)), None)
            if bad is not None:
                return False, f"column '{ca}' differs (e.g. {xa[bad]:,.2f} vs {xb[bad]:,.2f})"
        return True, f"all {a_num.shape[1]} numeric columns match on {len(main)} rows"
    col = a_num.abs().sum().idxmax()
    other_col = col if col in b_num.columns else b_num.abs().sum().idxmax()
    ta, tb = a_num[col].sum(), b_num[other_col].sum()
    ok = close(ta, tb, tol)
    return ok, f"total of '{col}' {ta:,.2f} vs {tb:,.2f} (shapes differ: {main.shape} vs {other.shape})"


# --------------------------------------------------------------------------- sanity rules
def sanity_checks(df: pd.DataFrame, truncated: bool = False) -> list[Check]:
    checks = []
    if df.empty:
        return [Check("Result is not empty", None, "No rows came back, so there is nothing to verify.")]
    checks.append(Check("Result is not empty", True, f"{len(df)} rows" + (" (capped at 500)" if truncated else "")))
    bad_neg = [c for c in df.select_dtypes("number").columns
               if NON_NEGATIVE.search(c.lower()) and "gap" not in c.lower() and (df[c] < 0).any()]
    checks.append(Check("No negative units, revenue, stock, cost or cover", not bad_neg,
                        "none found" if not bad_neg else f"negative values in {', '.join(bad_neg)}"))
    keys = [c for c in df.columns if c.lower() in KEY_COLUMNS]
    null_keys = [c for c in keys if df[c].isna().any()]
    checks.append(Check("No missing store, SKU or category keys", not null_keys,
                        f"checked {', '.join(keys) or 'no key columns'}" if not null_keys
                        else f"missing values in {', '.join(null_keys)}"))
    return checks


def verdict(checks: list[Check]) -> str:
    if any(c.passed is False for c in checks):
        return DISAGREE
    independent = [c for c in checks if c.name.startswith("Independent")]
    if independent and all(c.passed for c in independent) and all(c.passed is not None for c in checks):
        return VERIFIED
    return UNVERIFIED


# --------------------------------------------------------------------------- demo mode
def verify_template(db: StoreDB, check_sql: str, check_of: str, df: pd.DataFrame,
                    truncated: bool = False) -> Verification:
    """Compare the answer with the template's hand-written check query."""
    checks = []
    if df.empty:
        checks.append(Check("Independent query agrees", None, "Skipped: the answer has no rows."))
    else:
        try:
            expected = db.scalar(check_sql)
            got = value_of(df, check_of)
            if check_of == "rows" and truncated:
                checks.append(Check("Independent query agrees", None, "Skipped: the answer was capped at 500 rows."))
            else:
                ok = expected is not None and close(got, expected)
                checks.append(Check("Independent query agrees", ok,
                                    f"answer {_describe(check_of)} = {got:,.2f}; independent query = "
                                    f"{float(expected or 0):,.2f} (tolerance 0.5%)"))
        except (QueryError, KeyError, ValueError, TypeError) as err:
            checks.append(Check("Independent query agrees", None, f"The check query could not run: {err}"))
    checks += sanity_checks(df, truncated)
    return Verification(verdict(checks), checks)


def verify_frames(main: pd.DataFrame, other: pd.DataFrame | None, error: str | None = None,
                  truncated: bool = False) -> Verification:
    """LLM mode: compare the answer with an independently written query's result."""
    if other is None:
        checks = [Check("Independent query agrees", None, f"The independent query could not run: {error}")]
    elif main.empty:
        checks = [Check("Independent query agrees", None, "Skipped: the answer has no rows.")]
    else:
        ok, detail = compare_frames(main, other)
        checks = [Check("Independent query agrees", ok, detail)]
    return Verification(verdict(checks + sanity_checks(main, truncated)), checks + sanity_checks(main, truncated))


def _describe(check_of: str) -> str:
    if check_of == "rows":
        return "row count"
    kind, col = check_of.split(":", 1)
    return f"{'sum' if kind == 'sum' else 'first row'} of {col}"
