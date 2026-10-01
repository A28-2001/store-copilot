"""The planning workbook, planning/store_planning_model.xlsx.

The committed file carries values calculated by LibreOffice. These tests check
them against the database and against an independent Python rebuild of the
model: the same "work it out a second way" idea as the verifier.
"""
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from data.generate import DB_PATH, ensure_db

openpyxl = pytest.importorskip("openpyxl")
BOOK = Path(__file__).resolve().parents[1] / "planning" / "store_planning_model.xlsx"
ERRORS = ("#DIV/0!", "#NAME?", "#VALUE!", "#REF!", "#N/A", "#NUM!", "#NULL!")


@pytest.fixture(scope="module")
def book():
    return openpyxl.load_workbook(BOOK, data_only=True)


def named(book, name):
    sheet, ref = next(iter(book.defined_names[name].destinations))
    return book[sheet][ref.replace("$", "")].value


def table(ws, header, columns):
    """Rows under the header row whose first cell is `header`, down to the Total row."""
    start = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == header) + 1
    rows = []
    while ws.cell(start, 1).value not in (None, "Total"):
        rows.append([ws.cell(start, c).value for c in columns])
        start += 1
    return rows


def rebuild(book):
    """The whole model again in plain Python, from the workbook's own inputs."""
    v = lambda n: named(book, n)  # noqa: E731
    data, cash_ws = book["Data"], book["Cash"]
    gm, waste = v("gm_actual"), v("waste_actual")

    # New store
    mature = v("sqft_new") * v("flag_runrate") / v("sqft_s1") * v("prod")
    invest = v("sqft_new") * v("buildout_psf") + v("opening_costs")
    start, ramp_months = v("ramp_start"), v("ramp_months")
    cash, payback, years = -invest, None, [0.0] * 5
    for m in range(1, 61):
        ramp = 1.0 if m >= ramp_months else start + (1 - start) * (m - 1) / max(1, ramp_months - 1)
        sales = mature / 12 * ramp
        labor = max(sales * v("labor_pct"), mature / 12 * v("labor_pct") * v("labor_floor"))
        profit = sales * (gm - waste - v("other_pct")) - labor - v("sqft_new") * v("rent_psf") / 12
        cash += profit
        if payback is None and cash >= 0:
            payback = m
        years[(m - 1) // 12] += profit

    # Cash: six weeks of history, then thirteen forecast weeks
    weekly = v("stores_runrate") / 365 * 7 * (1 + v("sales_shock"))
    sales = [cash_ws.cell(14, c).value for c in range(2, 8)] + [weekly] * 13
    bought = [cash_ws.cell(15, c).value for c in range(2, 8)] + [weekly * (1 - gm) + weekly * waste] * 13
    terms = table(data, "Payment terms", (3, 5))                      # cost of goods, weeks until paid
    total = sum(t[0] for t in terms)
    first_day = cash_ws.cell(13, 8).value
    rent = (v("sqft_s1") + v("sqft_s2") + v("sqft_s3")) * v("rent_psf") / 12
    balance, low = v("cash_open"), float("inf")
    for week in range(1, 14):
        i = 5 + week
        out = sum(cogs / total * bought[i - lag] for cogs, lag in terms)
        if week % 2 == 1:
            out += v("labor_pct") * (sales[i - 2] + sales[i - 1])
        if (first_day + timedelta(days=7 * (week - 1) + 6)).day <= 7:
            out += rent
        out += (v("other_pct") + v("ho_pct")) * weekly
        if v("build_start") > 0 and v("build_start") <= week < v("build_start") + v("build_weeks"):
            out += invest / v("build_weeks")
        balance += weekly - out
        low = min(low, balance)

    # Inventory: stocked categories only
    cats = [r for r in table(data, "Category", (3, 10, 11)) if r[1]]   # cost of goods, stock, slow stock
    stock = sum(r[1] for r in cats)
    return {"invest": invest, "payback": payback if payback else "Over 60", "y3_profit": years[2],
            "cash_low": low, "cash_end": balance, "funding": max(0.0, v("cash_cushion") - low),
            "stock": stock, "stock_days": stock / (sum(r[0] for r in cats) / v("days_window")),
            "slow": sum(r[2] for r in cats)}


def workbook_outputs(book):
    v = lambda n: named(book, n)  # noqa: E731
    return {"invest": v("ns_invest"), "payback": v("ns_payback"), "y3_profit": v("ns_y3_profit"),
            "cash_low": v("cash_low"), "cash_end": v("cash_end"), "funding": v("cash_funding"),
            "stock": v("inv_total"), "stock_days": v("inv_days"), "slow": v("slow_total")}


def assert_same(expected, got):
    for key, want in expected.items():
        if isinstance(want, str):
            assert got[key] == want, key
        else:
            assert got[key] == pytest.approx(want, rel=1e-9, abs=1e-6), key


def test_actuals_match_the_database(book):
    con = sqlite3.connect(f"file:{ensure_db(DB_PATH)}?mode=ro", uri=True)
    rows = con.execute("""SELECT store_id, SUM(revenue), SUM(cogs) FROM sales_daily
                          WHERE sale_date > date('2026-09-28', '-30 day') GROUP BY store_id ORDER BY store_id""")
    data = book["Data"]
    for i, (store, revenue, cogs) in enumerate(rows.fetchall()):
        assert data[f"C{7 + i}"].value == pytest.approx(revenue, abs=0.01), store
        assert data[f"E{7 + i}"].value == pytest.approx(cogs, abs=0.01), store
    stock = con.execute("""SELECT SUM(i.on_hand * COALESCE(m.unit_cost, 0)) FROM inventory_current i
                           JOIN sku_master m ON m.master_sku_id = i.master_sku_id""").fetchone()[0]
    assert named(book, "inv_total") == pytest.approx(stock, abs=0.05)
    con.close()


def test_no_formula_errors_and_every_check_passes(book):
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.value not in ERRORS, f"{ws.title}!{cell.coordinate} is {cell.value}"
    assert str(named(book, "checks_status")).startswith("All ")


def test_model_matches_an_independent_rebuild(book):
    assert named(book, "scenario") == "Base"
    assert_same(rebuild(book), workbook_outputs(book))


def test_no_em_dashes(book):
    formulas = openpyxl.load_workbook(BOOK)
    for wb in (book, formulas):
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    assert "\u2014" not in str(cell.value or ""), f"{ws.title}!{cell.coordinate}"
