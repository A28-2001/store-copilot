"""The planning workbook, planning/store_planning_model.xlsx.

The committed file carries values calculated by LibreOffice. These tests check
them against the database and against an independent Python rebuild of the
model: the same "work it out a second way" idea as the verifier.
"""
import sqlite3
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


def rebuild(v):
    """The whole model again in plain Python, from the workbook's own inputs."""
    mature = v("sqft_new") * v("flag_runrate") / v("sqft_s1") * v("prod")
    gm = v("gm_actual") + v("gm_delta")
    invest = v("sqft_new") * v("buildout_psf") + v("preopen") + v("inventory")
    start, ramp_months = v("ramp_start"), v("ramp_months")
    cash, payback = -invest, None
    years = [{"sales": 0.0, "ebitda": 0.0} for _ in range(5)]
    for m in range(1, 61):
        ramp = 1.0 if m >= ramp_months else start + (1 - start) * (m - 1) / max(1, ramp_months - 1)
        sales = mature / 12 * ramp
        labor = max(sales * v("labor_pct"), mature / 12 * v("labor_pct") * v("labor_floor"))
        ebitda = sales * (gm - v("waste_pct") - v("other_pct")) - labor - v("sqft_new") * v("rent_psf") / 12
        cash += ebitda
        if payback is None and cash >= 0:
            payback = m
        years[(m - 1) // 12]["sales"] += sales
        years[(m - 1) // 12]["ebitda"] += ebitda

    opens = [v("open_2027"), v("open_2028"), v("open_2029")]
    app_ramp = [v("app_ramp_27"), v("app_ramp_28"), v("app_ramp_29")]
    revenue, company, cumulative = [], [], 0.0
    for t in range(3):
        grocery = v("grocery_runrate") * (1 + v("g_grocery")) ** (t + 1)
        cafe = v("cafe_runrate") * (1 + v("g_cafe")) ** (t + 1)
        new_sales = sum(opens[k] * years[t - k]["sales"] for k in range(t + 1))
        new_ebitda = sum(opens[k] * years[t - k]["ebitda"] for k in range(t + 1))
        catering = v("cat_y1") * (1 + v("g_cat")) ** t
        app = (grocery + cafe + new_sales) * v("app_pct") * app_ramp[t]
        fees = v("member_tx") / v("visits") * (1 + v("g_members")) ** (t + 1) * v("paid_conv") * v("fee")
        total = grocery + cafe + new_sales + catering + app + fees
        existing = (grocery * (v("grocery_gm") + v("gm_delta")) + cafe * (v("cafe_gm") + v("gm_delta"))
                    - (grocery + cafe) * (v("waste_pct") + v("labor_pct") + v("other_pct"))
                    - (v("sqft_s1") + v("sqft_s2") + v("sqft_s3")) * v("rent_psf"))
        ebitda = (existing + new_ebitda + catering * v("cat_margin") + app * (gm - v("app_cost"))
                  + fees * (1 - v("perks_cost")) - total * v("ho_pct"))
        revenue.append(total)
        company.append(ebitda)
        cumulative += ebitda - opens[t] * invest
    return {"invest": invest, "payback": payback if payback else "Over 60", "y3": years[2]["ebitda"],
            "revenue": revenue, "ebitda": company, "cumulative": cumulative}


def workbook_outputs(book):
    plan = book["3-Year Plan"]
    return {"invest": named(book, "ns_invest"), "payback": named(book, "ns_payback"),
            "y3": named(book, "ns_y3_ebitda"), "revenue": [plan[f"{c}20"].value for c in "CDE"],
            "ebitda": [plan[f"{c}35"].value for c in "CDE"], "cumulative": plan["E41"].value}


def assert_same(expected, got):
    for key, want in expected.items():
        have = got[key]
        if isinstance(want, list):
            assert have == pytest.approx(want, rel=1e-9), key
        elif isinstance(want, str):
            assert have == want, key
        else:
            assert have == pytest.approx(want, rel=1e-9), key


def test_actuals_match_the_database(book):
    con = sqlite3.connect(f"file:{ensure_db(DB_PATH)}?mode=ro", uri=True)
    rows = con.execute("""SELECT store_id, SUM(revenue), SUM(cogs) FROM sales_daily
                          WHERE sale_date > date('2026-09-28', '-30 day') GROUP BY store_id ORDER BY store_id""")
    actuals = book["Actuals"]
    for i, (store, revenue, cogs) in enumerate(rows.fetchall()):
        assert actuals[f"C{7 + i}"].value == pytest.approx(revenue, abs=0.01), store
        assert actuals[f"E{7 + i}"].value == pytest.approx(cogs, abs=0.01), store
    con.close()


def test_no_formula_errors_and_every_check_passes(book):
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.value not in ERRORS, f"{ws.title}!{cell.coordinate} is {cell.value}"
    assert str(named(book, "checks_status")).startswith("All ")


def test_model_matches_an_independent_rebuild(book):
    assert named(book, "scenario") == "Base"
    assert_same(rebuild(lambda n: named(book, n)), workbook_outputs(book))


def test_no_em_dashes(book):
    formulas = openpyxl.load_workbook(BOOK)
    for wb in (book, formulas):
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    assert "\u2014" not in str(cell.value or ""), f"{ws.title}!{cell.coordinate}"
