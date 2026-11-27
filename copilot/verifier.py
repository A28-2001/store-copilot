"""The verifier: a number is only labelled "Verified" when independent checks agree.

Why: in the Yardstick study, 93 of the 104 wrong LLM-written queries ran without
any error (silent errors). So "the query ran" proves nothing.
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
CHANGE = re.compile(r"(change|delta|diff|growth|gap|variance|trend|_vs|vs_|pts)")  # these may go below zero
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
    """The figure a check query recomputes: 'rows', 'sum:col', 'first:col',
    'wavg:col:weight' (a blended rate) or 'share:part:whole' (a percent)."""
    if check_of == "rows":
        return float(len(df))
    kind, *cols = check_of.split(":")
    if kind == "sum":
        return float(pd.to_numeric(df[cols[0]]).sum())
    if kind == "first":
        return float(df[cols[0]].iloc[0])
    if kind == "wavg":
        value, weight = (pd.to_numeric(df[c]) for c in cols)
        return float((value * weight).sum() / weight.sum())
    if kind == "share":
        part, whole = (pd.to_numeric(df[c]).sum() for c in cols)
        return float(100.0 * part / whole)
    raise ValueError(f"unknown check_of {check_of!r}")


def _labels(df: pd.DataFrame) -> pd.Series | None:
    """The first text column, normalised, if it names each row exactly once (store, product, date...)."""
    text = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if not text:
        return None
    labels = df[text[0]].astype(str).str.strip().str.lower()
    return labels if labels.is_unique else None


def _aligned(main: pd.DataFrame, other: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Put both results' rows in the same order, keeping each row's numbers together.

    When both name their rows the same way (the same set of stores, products or dates), rows are
    matched by name, so a number credited to the wrong store is caught. Otherwise rows are sorted
    by their numbers.
    """
    la, lb = _labels(main), _labels(other)
    if la is not None and lb is not None and set(la) == set(lb):
        return (main.iloc[la.argsort(kind="stable")].select_dtypes("number"),
                other.iloc[lb.argsort(kind="stable")].select_dtypes("number"))
    a, b = main.select_dtypes("number").fillna(0), other.select_dtypes("number").fillna(0)
    return (a.sort_values(list(a.columns), kind="stable"), b.sort_values(list(b.columns), kind="stable"))


def compare_frames(main: pd.DataFrame, other: pd.DataFrame, tol: float = TOLERANCE) -> tuple[bool | None, str]:
    """Do two results say the same thing?

    Same shape: rows are aligned (by name when both name them the same way), then every
    numeric value must match within `tol`; columns are paired by position, since names
    may differ. Different shape: compare the grand total of the largest numeric column.
    Names only (no numbers): the two must list the same names. None means nothing
    comparable came back.
    """
    a_num, b_num = main.select_dtypes("number"), other.select_dtypes("number")
    if a_num.empty and b_num.empty:
        la, lb = _labels(main), _labels(other)
        if la is None or lb is None:
            return None, "no numbers or row names to compare"
        same = set(la) == set(lb)
        return same, (f"both list the same {len(la)} name{'' if len(la) == 1 else 's'}" if same
                      else f"the names differ ({len(la)} vs {len(lb)} rows)")
    if a_num.empty or b_num.empty:
        return None, "only one of the two queries returned numbers"
    if main.shape == other.shape and a_num.shape[1] == b_num.shape[1]:
        xa, xb = _aligned(main, other)
        for (ca, col_a), (_, col_b) in zip(xa.items(), xb.items()):
            for x, y in zip(col_a.fillna(0).astype(float), col_b.fillna(0).astype(float)):
                if not close(x, y, tol):
                    return False, f"column '{ca}' differs (e.g. {x:,.2f} vs {y:,.2f})"
        cols, rows = a_num.shape[1], len(main)
        return True, (f"{'the' if cols == 1 else 'all ' + str(cols)} numeric column{'' if cols == 1 else 's'} "
                      f"match{'es' if cols == 1 else ''} on {rows} row{'' if rows == 1 else 's'}")
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
    checks.append(Check("Result is not empty", True, f"{len(df)} row{'' if len(df) == 1 else 's'}"
                        + (" (capped at 500)" if truncated else "")))
    bad_neg = [c for c in df.select_dtypes("number").columns
               if NON_NEGATIVE.search(c.lower()) and not CHANGE.search(c.lower()) and (df[c] < 0).any()]
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
                    truncated: bool = False, more: list[tuple[str, str]] | tuple = ()) -> Verification:
    """Compare the answer with the template's hand-written check queries, one per key figure."""
    checks = []
    pairs = [(check_sql, check_of), *more]
    for sql, of in pairs:
        name = "Independent query agrees" + (f" ({_describe(of)})" if len(pairs) > 1 else "")
        if df.empty:
            checks.append(Check(name, None, "Skipped: the answer has no rows."))
            continue
        try:
            expected = db.scalar(sql)
            got = value_of(df, of)
            if of == "rows" and truncated:
                checks.append(Check(name, None, "Skipped: the answer was capped at 500 rows."))
            else:
                ok = expected is not None and close(got, expected)
                checks.append(Check(name, ok, f"answer {_describe(of)} = {got:,.2f}; independent query = "
                                              f"{float(expected or 0):,.2f} (tolerance 0.5%)"))
        except (QueryError, KeyError, ValueError, TypeError, ZeroDivisionError) as err:
            checks.append(Check(name, None, f"The check query could not run: {err}"))
    checks += sanity_checks(df, truncated)
    return Verification(verdict(checks), checks)


def verify_frames(main: pd.DataFrame, other: pd.DataFrame | None, error: str | None = None,
                  truncated: bool = False, identical: bool = False) -> Verification:
    """LLM mode: compare the answer with an independently written query's result."""
    if identical:
        checks = [Check("Independent query agrees", None,
                        "Skipped: the second query was the same as the first, so it isn't an independent check.")]
    elif other is None:
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
    kind, *cols = check_of.split(":")
    if kind == "wavg":
        return f"{cols[0]} across rows, weighted by {cols[1]}"
    if kind == "share":
        return f"{cols[0]} as a % of {cols[1]}"
    return f"{'sum' if kind == 'sum' else 'first row'} of {cols[0]}"
