"""Build the planning workbook from the demo database.

    python planning/build_workbook.py

Writes planning/store_planning_model.xlsx. Each tab answers one question:

    New Store   should we open one, and when does it pay back?
    Cash        can we afford it? (13 weeks, using the vendors' payment terms)
    Inventory   where is cash tied up?
    Pricing     what does a price change or a promotion do to gross profit?

The last 30 days of actuals are pasted as values; every other number is an
Excel formula, so changing an input recalculates the whole model. Before
writing, the actuals are tied out a second way (stores vs categories, item
lines vs POS transactions), the same idea as the Copilot's verifier.

Excel calculates the file on open. The committed copy was also recalculated
with LibreOffice so file previews show numbers.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.generate import DB_PATH, ensure_db, load_config  # noqa: E402

OUT = ROOT / "planning" / "store_planning_model.xlsx"
DAYS = 30
MONTHS = 60
WEEKS, HISTORY = 13, 6           # the cash forecast, and the weeks of history that vendor terms reach back to
SLOW_DAYS = 60                   # slow stock: more than this many days on hand, or no sales in the window
SCENARIOS = ("Base", "Upside", "Downside")
UNMAPPED = "Unmapped POS codes"
DEFAULT_CATEGORY = "Meat & Seafood"

# ----------------------------------------------------------------------------- styles
BLUE, BLACK, GREEN, GRAY, WHITE, ACCENT = "FF0000FF", "FF000000", "FF008000", "FF6B6B6B", "FFFFFFFF", "FF2E5E45"
BRIGHT = "2E845A"
YELLOW = PatternFill("solid", fgColor="FFFFFF00")
BAND = PatternFill("solid", fgColor=ACCENT)
TILE = PatternFill("solid", fgColor="FFEEF3EF")
LINE = Side(style="thin", color="FFBFBFBF")
USD = '$#,##0;[Red]($#,##0);"-"'
USD_K = '$#,##0,"K";[Red]($#,##0,"K");"-"'
USD2 = '$#,##0.00;[Red]($#,##0.00);"-"'
PCT = '0.0%;[Red](0.0%);"-"'
PCT0 = '0%;[Red](0%);"-"'
PTS = '+0.0%;[Red]-0.0%;0.0%'
NUM = '#,##0;[Red](#,##0);"-"'
ONE = '0.0;[Red]-0.0;"-"'
COLORS = {"in": BLUE, "f": BLACK, "link": GREEN, "text": BLACK, "note": GRAY}


def put(ws, ref, value, kind="f", fmt=None, bold=False, fill=None, wrap=False, align=None, size=10):
    c = ws[ref]
    c.value = value
    c.font = Font(name="Arial", size=size, bold=bold, color=COLORS[kind])
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    indent = 1 if kind == "note" and not ref.startswith("A") else 0
    if wrap or align or indent:
        c.alignment = Alignment(wrap_text=wrap, horizontal=align, vertical="top" if wrap else None, indent=indent)
    return c


def title(ws, text, sub=None, tab=BRIGHT):
    put(ws, "A1", text, "text").font = Font(name="Arial", size=14, bold=True, color=ACCENT)
    if sub:
        put(ws, "A2", sub, "note")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = tab


def section(ws, row, text, last_col):
    for c in range(1, last_col + 1):
        ws.cell(row, c).fill = BAND
    put(ws, f"A{row}", text, "text").font = Font(name="Arial", size=10, bold=True, color=WHITE)


def headers(ws, row, labels, start=1, left=("Unit", "Notes", "From", "To")):
    for i, label in enumerate(labels):
        c = put(ws, f"{L(start + i)}{row}", label, "text", bold=True, wrap=True,
                align="left" if i == 0 or label in left else "right")
        c.border = Border(bottom=LINE)
        if label == "Notes":
            c.alignment = Alignment(wrap_text=True, horizontal="left", vertical="top", indent=1)


def rule(ws, row, last_col):
    for c in range(1, last_col + 1):
        ws.cell(row, c).border = Border(top=LINE)


def widths(ws, spec):
    for letter, w in spec.items():
        ws.column_dimensions[letter].width = w


def quote(sheet):
    return f"'{sheet}'" if " " in sheet else sheet


def name(wb, nm, sheet, ref):
    cells = ":".join("$" + re.sub(r"(\d+)", r"$\1", part) for part in ref.split(":"))
    wb.defined_names[nm] = DefinedName(nm, attr_text=f"{quote(sheet)}!{cells}")


# ----------------------------------------------------------------------------- data
def load_actuals(db: Path) -> dict:
    cfg = load_config()
    end = date.fromisoformat(str(cfg["data_end_date"]))
    since = f"date('{end}', '-{DAYS} day')"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
        stores = q("SELECT store_id, name, neighborhood FROM stores ORDER BY store_id")
        items = {r[0]: r[1:] for r in q(f"""SELECT store_id, SUM(revenue), SUM(cogs) FROM sales_daily
                                            WHERE sale_date > {since} GROUP BY store_id""")}
        tx = {r[0]: r[1:] for r in q(f"""SELECT store_id, COUNT(*), SUM(revenue), SUM(transactions)
                                         FROM transactions_daily WHERE sale_date > {since} GROUP BY store_id""")}
        waste = dict(q(f"SELECT store_id, SUM(waste_cost) FROM waste_daily WHERE waste_date > {since} GROUP BY store_id"))
        stock_by_store = dict(q("""SELECT i.store_id, SUM(i.on_hand * COALESCE(m.unit_cost, 0))
                                   FROM inventory_current i JOIN sku_master m ON m.master_sku_id = i.master_sku_id
                                   GROUP BY i.store_id"""))
        targets = q("SELECT category, target_margin_pct / 100.0 FROM category_targets ORDER BY rowid")
        cats = {r[0]: r[1:] for r in q(f"""SELECT COALESCE(m.category, '{UNMAPPED}'), SUM(sd.revenue), SUM(sd.cogs),
                                                  SUM(sd.units)
                                           FROM sales_daily sd LEFT JOIN sku_master m
                                             ON m.master_sku_id = sd.master_sku_id
                                           WHERE sd.sale_date > {since} GROUP BY 1""")}
        # Stock per category. Slow = on hand with no sales in the window, or more than SLOW_DAYS days of stock.
        slow = f"i.on_hand > 0 AND (COALESCE(s.u, 0) = 0 OR i.on_hand * {DAYS}.0 / s.u > {SLOW_DAYS})"
        stock = {r[0]: r[1:] for r in q(f"""
            WITH sold AS (SELECT store_id, master_sku_id, SUM(units) AS u FROM sales_daily
                          WHERE sale_date > {since} AND master_sku_id IS NOT NULL GROUP BY 1, 2)
            SELECT m.category,
                   SUM(i.on_hand * COALESCE(m.unit_cost, 0)),
                   SUM(CASE WHEN {slow} THEN i.on_hand * COALESCE(m.unit_cost, 0) ELSE 0 END),
                   SUM(CASE WHEN {slow} THEN 1 ELSE 0 END),
                   SUM(CASE WHEN i.on_hand = 0 THEN 1 ELSE 0 END)
            FROM inventory_current i
            JOIN sku_master m ON m.master_sku_id = i.master_sku_id
            LEFT JOIN sold s ON s.store_id = i.store_id AND s.master_sku_id = i.master_sku_id
            GROUP BY m.category""")}
        shelf = dict(q("SELECT category, AVG(shelf_life_days) FROM sku_master GROUP BY category"))
        weeks = []
        for back in range(HISTORY, 0, -1):
            start, stop = end - timedelta(days=7 * back - 1), end - timedelta(days=7 * (back - 1))
            between = f"BETWEEN '{start}' AND '{stop}'"
            sales, cogs = q(f"SELECT SUM(revenue), SUM(cogs) FROM sales_daily WHERE sale_date {between}")[0]
            wasted = q(f"SELECT SUM(waste_cost) FROM waste_daily WHERE waste_date {between}")[0][0]
            weeks.append(dict(back=-back, start=start, stop=stop, sales=sales, cogs=cogs, waste=wasted or 0.0))
        terms = q(f"""SELECT CASE WHEN v.payment_terms IS NULL THEN 'missing' ELSE v.payment_terms END,
                             COUNT(DISTINCT v.vendor_id), SUM(sd.cogs)
                      FROM sales_daily sd
                      LEFT JOIN sku_master m ON m.master_sku_id = sd.master_sku_id
                      LEFT JOIN vendors v ON v.vendor_id = m.vendor_id
                      WHERE sd.sale_date > {since} GROUP BY 1""")
    finally:
        con.close()

    store_rows = []
    for sid, sname, hood in stores:
        days, tx_rev, n_tx = tx[sid]
        rev, cogs = items[sid]
        label = f"{sname} ({hood.replace(' (hypothetical)', ', hypothetical')})"
        store_rows.append(dict(id=sid, label=label, days=days, rev=rev, tx_rev=tx_rev, cogs=cogs, tx=n_tx,
                               waste=waste.get(sid, 0.0), stock=stock_by_store.get(sid, 0.0)))
    cat_rows = []
    for c, t in targets:
        st = stock.get(c, (0.0, 0.0, 0, 0))
        cat_rows.append(dict(name=c, target=t, rev=cats[c][0], cogs=cats[c][1], units=cats[c][2], stock=st[0],
                             slow=st[1], slow_skus=st[2], stockouts=st[3], shelf=shelf[c]))
    if UNMAPPED in cats:
        cat_rows.append(dict(name=UNMAPPED, target=None, rev=cats[UNMAPPED][0], cogs=cats[UNMAPPED][1],
                             units=cats[UNMAPPED][2], stock=None))

    # Vendor payment terms: "Net 30" is paid about 4 weeks after delivery.
    term_rows = []
    for label, vendors, cogs in terms:
        if label == "n/a":
            term_rows.append(dict(label="Paid on delivery (in-house kitchen)", vendors=vendors, cogs=cogs, lag=0))
        elif label == "missing":
            term_rows.append(dict(label="Terms missing on the vendor record", vendors=vendors, cogs=cogs, lag=4))
        else:
            term_rows.append(dict(label=label, vendors=vendors, cogs=cogs, lag=round(int(label.split()[-1]) / 7)))
    term_rows.sort(key=lambda r: (r["label"].startswith("Terms missing"), r["lag"]))

    # Tie out before writing anything: different routes to the same totals.
    def tie(what, a, b):
        if abs(a - b) > 0.005 * abs(a):
            raise SystemExit(f"Actuals don't tie on {what}: {a:,.0f} vs {b:,.0f}")

    by_store = sum(s["rev"] for s in store_rows)
    tie("sales, stores vs POS transactions", by_store, sum(s["tx_rev"] for s in store_rows))
    tie("sales, stores vs categories", by_store, sum(c["rev"] for c in cat_rows))
    tie("cost of goods, stores vs categories", sum(s["cogs"] for s in store_rows), sum(c["cogs"] for c in cat_rows))
    tie("cost of goods, stores vs payment terms", sum(s["cogs"] for s in store_rows), sum(t["cogs"] for t in term_rows))
    tie("stock, stores vs categories", sum(s["stock"] for s in store_rows), sum(c["stock"] or 0 for c in cat_rows))
    return dict(stores=store_rows, cats=cat_rows, weeks=weeks, terms=term_rows, end=end)


# ----------------------------------------------------------------------------- Data
def build_data(wb, ws, a):
    start = a["end"] - timedelta(days=DAYS - 1)
    title(ws, "Data and checks", f"Last {DAYS} days of the demo data, {start} to {a['end']}. Made-up data. Pasted from "
                                 "store.db by planning/build_workbook.py. Blue = pasted value, black = formula.", "9E9E9E")
    put(ws, "A3", "Days in window", "text")
    put(ws, "B3", DAYS, "in", NUM)
    name(wb, "days_window", ws.title, "B3")

    section(ws, 5, "By store", 14)
    headers(ws, 6, ["Store", "Days open", "Sales, item lines", "Sales, POS transactions", "Cost of goods",
                    "Gross margin", "Transactions", "Average basket", "Waste cost", "Waste, % of sales",
                    "Sales per open day", "Annual run-rate", "Stock at cost"])
    r0 = 7
    for i, s in enumerate(a["stores"]):
        r = r0 + i
        put(ws, f"A{r}", s["label"], "text")
        put(ws, f"B{r}", s["days"], "in", NUM)
        put(ws, f"C{r}", round(s["rev"], 2), "in", USD)
        put(ws, f"D{r}", round(s["tx_rev"], 2), "in", USD)
        put(ws, f"E{r}", round(s["cogs"], 2), "in", USD)
        put(ws, f"F{r}", f"=IF(C{r}=0,0,(C{r}-E{r})/C{r})", "f", PCT)
        put(ws, f"G{r}", s["tx"], "in", NUM)
        put(ws, f"H{r}", f"=IF(G{r}=0,0,C{r}/G{r})", "f", USD2)
        put(ws, f"I{r}", round(s["waste"], 2), "in", USD)
        put(ws, f"J{r}", f"=IF(C{r}=0,0,I{r}/C{r})", "f", PCT)
        put(ws, f"K{r}", f"=IF(B{r}=0,0,C{r}/B{r})", "f", USD)
        put(ws, f"L{r}", f"=K{r}*365", "f", USD)
        put(ws, f"M{r}", round(s["stock"], 2), "in", USD)
    rt = r0 + len(a["stores"])
    put(ws, f"A{rt}", "Total", "text", bold=True)
    for c in "CDEGIKLM":
        put(ws, f"{c}{rt}", f"=SUM({c}{r0}:{c}{rt - 1})", "f", NUM if c == "G" else USD, bold=True)
    put(ws, f"F{rt}", f"=IF(C{rt}=0,0,(C{rt}-E{rt})/C{rt})", "f", PCT, bold=True)
    put(ws, f"H{rt}", f"=IF(G{rt}=0,0,C{rt}/G{rt})", "f", USD2, bold=True)
    put(ws, f"J{rt}", f"=IF(C{rt}=0,0,I{rt}/C{rt})", "f", PCT, bold=True)
    rule(ws, rt, 13)
    newest = a["stores"][-1]
    put(ws, f"A{rt + 1}", f"{newest['label'].split(' (')[0]} opened recently: {newest['days']} days in the window, "
                          "so its run-rate understates a full year.", "note")
    row_of = {s["id"]: r0 + i for i, s in enumerate(a["stores"])}
    name(wb, "s1_per_day", ws.title, f"K{row_of['S1']}")
    name(wb, "s3_per_day", ws.title, f"K{row_of['S3']}")
    name(wb, "flag_runrate", ws.title, f"L{row_of['S1']}")
    name(wb, "stores_runrate", ws.title, f"L{rt}")
    name(wb, "gm_actual", ws.title, f"F{rt}")
    name(wb, "waste_actual", ws.title, f"J{rt}")

    cs = rt + 3
    section(ws, cs, "By category", 14)
    headers(ws, cs + 1, ["Category", "Sales", "Cost of goods", "Units", "Gross margin", "Target margin",
                         "Average price", "Average unit cost", "Units per week", "Stock at cost",
                         "Slow stock at cost", "Slow SKUs", "Stock-outs", "Average shelf life (days)"])
    c0 = cs + 2
    for i, c in enumerate(a["cats"]):
        r = c0 + i
        put(ws, f"A{r}", c["name"], "text")
        put(ws, f"B{r}", round(c["rev"], 2), "in", USD)
        put(ws, f"C{r}", round(c["cogs"], 2), "in", USD)
        put(ws, f"D{r}", c["units"], "in", NUM)
        put(ws, f"E{r}", f"=IF(B{r}=0,0,(B{r}-C{r})/B{r})", "f", PCT)
        put(ws, f"G{r}", f"=IF(D{r}=0,0,B{r}/D{r})", "f", USD2)
        put(ws, f"H{r}", f"=IF(D{r}=0,0,C{r}/D{r})", "f", USD2)
        put(ws, f"I{r}", f"=D{r}/days_window*7", "f", NUM)
        if c["target"] is not None:
            put(ws, f"F{r}", c["target"], "in", PCT)
            put(ws, f"J{r}", round(c["stock"], 2), "in", USD)
            put(ws, f"K{r}", round(c["slow"], 2), "in", USD)
            put(ws, f"L{r}", c["slow_skus"], "in", NUM)
            put(ws, f"M{r}", c["stockouts"], "in", NUM)
            put(ws, f"N{r}", round(c["shelf"], 1), "in", ONE)
    ct = c0 + len(a["cats"])
    put(ws, f"A{ct}", "Total", "text", bold=True)
    for c in "BCDJKLM":
        put(ws, f"{c}{ct}", f"=SUM({c}{c0}:{c}{ct - 1})", "f", NUM if c in "DLM" else USD, bold=True)
    put(ws, f"E{ct}", f"=IF(B{ct}=0,0,(B{ct}-C{ct})/B{ct})", "f", PCT, bold=True)
    rule(ws, ct, 14)
    put(ws, f"A{ct + 1}", f"Slow stock: more than {SLOW_DAYS} days of stock on hand, or no sales in {DAYS} days. "
                          "Cafe and hot bar items are made fresh each day, so they hold no stock.", "note")
    n_named = sum(1 for c in a["cats"] if c["target"] is not None)
    name(wb, "category_list", ws.title, f"A{c0}:A{c0 + n_named - 1}")

    hs = ct + 4
    section(ws, hs, f"The last {HISTORY} weeks", 14)
    headers(ws, hs + 1, ["Week", "From", "To", "Sales", "Cost of goods", "Waste", "Purchases"])
    w0 = hs + 2
    for i, w in enumerate(a["weeks"]):
        r = w0 + i
        put(ws, f"A{r}", w["back"], "in", "0", align="left")
        put(ws, f"B{r}", w["start"], "in", "mmm d", align="left")
        put(ws, f"C{r}", w["stop"], "in", "mmm d", align="left")
        put(ws, f"D{r}", round(w["sales"], 2), "in", USD)
        put(ws, f"E{r}", round(w["cogs"], 2), "in", USD)
        put(ws, f"F{r}", round(w["waste"], 2), "in", USD)
        put(ws, f"G{r}", f"=E{r}+F{r}", "f", USD)
    put(ws, f"A{w0 + HISTORY}", "Purchases = what was sold, at cost, plus what was wasted (stock held level).", "note")

    ts = w0 + HISTORY + 3
    section(ws, ts, "Vendor payment terms", 14)
    headers(ws, ts + 1, ["Payment terms", "Vendors", "Cost of goods", "Share", "Paid after (weeks)"])
    t0 = ts + 2
    for i, t in enumerate(a["terms"]):
        r = t0 + i
        put(ws, f"A{r}", t["label"], "text")
        put(ws, f"B{r}", t["vendors"], "in", NUM)
        put(ws, f"C{r}", round(t["cogs"], 2), "in", USD)
        put(ws, f"E{r}", t["lag"], "in", "0")
    tt = t0 + len(a["terms"])
    for i in range(len(a["terms"])):
        put(ws, f"D{t0 + i}", f"=IF($C${tt}=0,0,C{t0 + i}/$C${tt})", "f", PCT)
    put(ws, f"A{tt}", "Total", "text", bold=True)
    put(ws, f"C{tt}", f"=SUM(C{t0}:C{tt - 1})", "f", USD, bold=True)
    put(ws, f"D{tt}", f"=SUM(D{t0}:D{tt - 1})", "f", PCT, bold=True)
    rule(ws, tt, 5)
    put(ws, f"A{tt + 1}", "Net 30 means the vendor is paid about 4 weeks after delivery. Records with no terms, "
                          "and sales on unmapped POS codes, are assumed to be 4 weeks.", "note")

    widths(ws, {"A": 40, **{L(i): 14 for i in range(2, 15)}})
    for r in (6, cs + 1):
        ws.row_dimensions[r].height = 30
    stocked_rows = [c0 + i for i, c in enumerate(a["cats"]) if c.get("stock")]
    return dict(store_first=r0, store_total=rt, cat_first=c0, cat_total=ct, cat_named=n_named, week_first=w0,
                stocked_rows=stocked_rows,
                term_first=t0, term_total=tt, checks_at=tt + 4, newest=newest, end=a["end"])


# (name, label, unit, (base, upside, downside), format, note)
SCENARIO_INPUTS = [
    ("New store", [
        ("prod", "Sales per sq ft vs flagship", "% of flagship", (1.00, 1.10, 0.85), PCT0,
         "100% means the same sales per sq ft as the flagship."),
        ("ramp_start", "Month 1 sales", "% of mature", (0.60, 0.70, 0.50), PCT0, None),
        ("ramp_months", "Months to reach mature sales", "months", (18, 12, 24), NUM,
         "The demo data ramps a store in 30 days, which is fast. Real stores take 12 to 24 months."),
        ("buildout_psf", "Build-out", "$ per sq ft", (250, 225, 300), USD, "Construction, equipment, fixtures."),
    ]),
    ("Store running costs", [
        ("labor_pct", "Store labor", "% of sales", (0.19, 0.18, 0.21), PCT,
         "Not in the data. The cafe and hot bar make this higher than a typical grocery store."),
        ("rent_psf", "Rent", "$ per sq ft per year", (60, 55, 70), USD, "Not in the data."),
        ("other_pct", "Other operating costs", "% of sales", (0.08, 0.075, 0.09), PCT,
         "Card fees, utilities, supplies, repairs, local marketing. Not in the data."),
    ]),
    ("Cash forecast", [
        ("sales_shock", "Sales vs today's run-rate", "%", (0.0, 0.05, -0.10), PTS,
         "0% means the next 13 weeks sell at the last 30 days' pace."),
    ]),
]

# (name, label, unit, value, format, note)
FIXED_INPUTS = [
    ("sqft_s1", "Flagship size", "sq ft", 12000, NUM, "Store sizes are not in the data."),
    ("sqft_s2", "Store 2 size", "sq ft", 8500, NUM, None),
    ("sqft_s3", "Store 3 size", "sq ft", 6500, NUM, None),
    ("sqft_new", "New store size", "sq ft", 8000, NUM, None),
    ("opening_costs", "Opening costs", "$", 350000, USD, "Hiring, training, launch marketing and the first stock."),
    ("labor_floor", "Labor floor while a store ramps", "% of mature labor", 0.85, PCT0,
     "A new store can't cut staff in line with lower early sales."),
    ("ho_pct", "Head office costs", "% of sales", 0.05, PCT, "Finance, buying, marketing, leadership."),
    ("cash_open", "Cash in the bank today", "$", 750000, USD, "Not in the data."),
    ("cash_cushion", "Cash cushion to keep", "$", 250000, USD, "The lowest balance you are comfortable with."),
    ("build_start", "New store build starts in week", "week", 5, "0", "Enter 0 for no new store."),
    ("build_weeks", "Build is paid over", "weeks", 10, "0", None),
]


def build_inputs(wb, ws, d):
    title(ws, "Inputs", "Change the blue cells. The scenario is switched on the Summary tab; the Live column feeds "
                        "every other tab.")
    put(ws, "A4", "Scenario", "text", bold=True)
    put(ws, "C4", "=scenario", "link", bold=True, align="right")
    headers(ws, 6, ["Assumption", "Unit", "Live", *SCENARIOS, "Last 30 days", "Notes"])
    actual = {"ramp_start": "=IF(s1_per_day=0,0,(s3_per_day/sqft_s3)/(s1_per_day/sqft_s1))"}
    r = 7
    for sec, items in SCENARIO_INPUTS:
        section(ws, r, sec, 8)
        r += 1
        for key, label, unit, values, fmt, text in items:
            put(ws, f"A{r}", label, "text")
            put(ws, f"B{r}", unit, "note")
            put(ws, f"C{r}", f"=INDEX(D{r}:F{r},MATCH(scenario,$D$6:$F$6,0))", "f", fmt, bold=True)
            for j, v in enumerate(values):
                put(ws, f"{'DEF'[j]}{r}", v, "in", fmt)
            if key in actual:
                put(ws, f"G{r}", actual[key], "link", PCT0)
                text = (f"Last 30 days: {d['newest']['label'].split(' (')[0]}'s sales per sq ft in its first "
                        f"{d['newest']['days']} days, vs the flagship.")
            if text:
                put(ws, f"H{r}", text, "note")
            name(wb, key, ws.title, f"C{r}")
            r += 1
        r += 1
    section(ws, r, "Same in every scenario", 8)
    r += 1
    for key, label, unit, value, fmt, text in FIXED_INPUTS:
        put(ws, f"A{r}", label, "text")
        put(ws, f"B{r}", unit, "note")
        put(ws, f"C{r}", value, "in", fmt, bold=True)
        if text:
            put(ws, f"H{r}", text, "note")
        name(wb, key, ws.title, f"C{r}")
        r += 1
    put(ws, f"A{r + 1}", "Gross margin and waste are not inputs: the model uses the last 30 days from the Data tab.",
        "note")
    widths(ws, {"A": 36, "B": 20, "C": 11, "D": 11, "E": 11, "F": 11, "G": 12, "H": 78})
    ws.freeze_panes = "C7"


# ----------------------------------------------------------------------------- New Store
def build_new_store(wb, ws):
    title(ws, "New store: should we open one?")
    put(ws, "A2", '="One new store, month by month for 5 years. Scenario: "&scenario', "note")
    section(ws, 4, "What it costs and what it sells", 6)
    drivers = [
        ("Store size (sq ft)", "=sqft_new", "link", NUM),
        ("Flagship sales per sq ft", "=IF(sqft_s1=0,0,flag_runrate/sqft_s1)", "f", USD),
        ("Sales per sq ft vs flagship", "=prod", "link", PCT0),
        ("Mature annual sales", "=B5*B6*B7", "f", USD),
        ("Gross margin, last 30 days", "=gm_actual", "link", PCT),
        ("Build-out", "=B5*buildout_psf", "f", USD),
        ("Opening costs", "=opening_costs", "link", USD),
        ("Total investment", "=B10+B11", "f", USD),
    ]
    for i, (label, formula, kind, fmt) in enumerate(drivers):
        last_row = label == "Total investment"
        put(ws, f"A{5 + i}", label, "text", bold=last_row)
        put(ws, f"B{5 + i}", formula, kind, fmt, bold=last_row)
    name(wb, "ns_mature_sales", ws.title, "B8")
    name(wb, "ns_gm", ws.title, "B9")
    name(wb, "ns_invest", ws.title, "B12")

    # Month by month (written first so the summaries above can point at it).
    m0, first, last = 43, 4, 4 + MONTHS - 1   # month 0 in column C, months 1..60 in D..BK
    end = L(last)
    section(ws, m0 - 1, "Month by month", 6)
    put(ws, f"A{m0}", "Month", "text", bold=True)
    put(ws, f"A{m0 + 1}", "Store year", "text")
    put(ws, f"C{m0}", 0, "in", "0", bold=True)
    put(ws, f"C{m0 + 1}", 0, "in", "0")
    labels = ["Sales ramp, % of mature", "Sales", "Gross profit", "Waste", "Labor", "Rent", "Other operating costs",
              "Store profit", "Opening investment", "Cash flow", "Cumulative cash", "Paid back (month)"]
    R = {label: m0 + 2 + i for i, label in enumerate(labels)}
    for label, r in R.items():
        put(ws, f"A{r}", label, "note" if label == "Paid back (month)" else "text",
            bold=label in ("Store profit", "Cumulative cash"))
    put(ws, f"C{R['Opening investment']}", "=-ns_invest", "f", USD_K)
    put(ws, f"C{R['Cash flow']}", f"=C{R['Store profit']}+C{R['Opening investment']}", "f", USD_K)
    put(ws, f"C{R['Cumulative cash']}", f"=C{R['Cash flow']}", "f", USD_K, bold=True)
    for c in range(first, last + 1):
        x, p, s = L(c), L(c - 1), R["Sales"]
        put(ws, f"{x}{m0}", f"={p}{m0}+1", "f", "0", bold=True)
        put(ws, f"{x}{m0 + 1}", f"=ROUNDUP({x}{m0}/12,0)", "f", "0")
        put(ws, f"{x}{R['Sales ramp, % of mature']}",
            f"=IF({x}${m0}>=ramp_months,1,ramp_start+(1-ramp_start)*({x}${m0}-1)/MAX(1,ramp_months-1))", "f", PCT0)
        put(ws, f"{x}{s}", f"=ns_mature_sales/12*{x}{R['Sales ramp, % of mature']}", "f", USD_K)
        put(ws, f"{x}{R['Gross profit']}", f"={x}{s}*ns_gm", "f", USD_K)
        put(ws, f"{x}{R['Waste']}", f"={x}{s}*waste_actual", "f", USD_K)
        put(ws, f"{x}{R['Labor']}", f"=MAX({x}{s}*labor_pct,ns_mature_sales/12*labor_pct*labor_floor)", "f", USD_K)
        put(ws, f"{x}{R['Rent']}", "=sqft_new*rent_psf/12", "f", USD_K)
        put(ws, f"{x}{R['Other operating costs']}", f"={x}{s}*other_pct", "f", USD_K)
        put(ws, f"{x}{R['Store profit']}",
            f"={x}{R['Gross profit']}-{x}{R['Waste']}-{x}{R['Labor']}-{x}{R['Rent']}-{x}{R['Other operating costs']}",
            "f", USD_K, bold=True)
        put(ws, f"{x}{R['Cash flow']}", f"={x}{R['Store profit']}+{x}{R['Opening investment']}", "f", USD_K)
        put(ws, f"{x}{R['Cumulative cash']}", f"={p}{R['Cumulative cash']}+{x}{R['Cash flow']}", "f", USD_K, bold=True)
        put(ws, f"{x}{R['Paid back (month)']}", f"=IF({x}{R['Cumulative cash']}>=0,{x}${m0},999)", "note", "0")
    rule(ws, R["Store profit"], last)
    span = lambda label: f"$D{R[label]}:${end}{R[label]}"  # noqa: E731
    months, years = f"$D${m0}:${end}${m0}", f"$D${m0 + 1}:${end}${m0 + 1}"

    section(ws, 14, "Results", 6)
    payback = f"MIN({span('Paid back (month)')})"
    results = [
        ("Payback (months after opening)", f'=IF({payback}>{MONTHS},"Over {MONTHS}",{payback})', NUM),
        ("Year 3 store profit", "=D29", USD),
        ("Year 3 return on the investment", "=IF(ns_invest=0,0,D29/ns_invest)", PCT),
        ("Store profit margin at maturity",
         "=IF(ns_mature_sales=0,0,ns_gm-waste_actual-labor_pct-other_pct-sqft_new*rent_psf/ns_mature_sales)", PCT),
    ]
    for i, (label, formula, fmt) in enumerate(results):
        put(ws, f"A{15 + i}", label, "text")
        put(ws, f"B{15 + i}", formula, "f", fmt, bold=True, align="right")
    name(wb, "ns_payback", ws.title, "B15")
    name(wb, "ns_y3_profit", ws.title, "B16")

    section(ws, 20, "By store year", 6)
    for j in range(5):
        put(ws, f"{L(2 + j)}22", j + 1, "text", '"Year "0', bold=True, align="right").border = Border(bottom=LINE)
    yearly = ["Sales", "Gross profit", "Waste", "Labor", "Rent", "Other operating costs", "Store profit"]
    for i, label in enumerate(yearly):
        r = 23 + i
        put(ws, f"A{r}", label, "text", bold=label == "Store profit")
        for j in range(5):
            x = L(2 + j)
            put(ws, f"{x}{r}", f"=SUMIF({years},{x}$22,{span(label)})", "f", USD, bold=label == "Store profit")
    put(ws, "A30", "Store profit margin", "text")
    put(ws, "A31", "Cumulative cash at year end", "text")
    for j in range(5):
        x = L(2 + j)
        put(ws, f"{x}30", f"=IF({x}23=0,0,{x}29/{x}23)", "f", PCT)
        put(ws, f"{x}31", f"=INDEX({span('Cumulative cash')},MATCH({x}22*12,{months},0))", "f", USD)
    rule(ws, 29, 6)

    section(ws, 33, "What if: store profit in a mature year", 6)
    put(ws, "A34", "Rows move sales per sq ft, columns move gross margin, around the live scenario (center).", "note")
    put(ws, "A35", "Sales per sq ft vs flagship  /  gross margin", "text", bold=True)
    for j, dlt in enumerate((-0.02, -0.01, 0, 0.01, 0.02)):
        put(ws, f"{L(2 + j)}35", f"=ns_gm{dlt:+.2f}" if dlt else "=ns_gm", "f", PCT, bold=True,
            align="right").border = Border(bottom=LINE)
    for i, dlt in enumerate((-0.2, -0.1, 0, 0.1, 0.2)):
        r = 36 + i
        put(ws, f"A{r}", f"=prod{dlt:+.1f}" if dlt else "=prod", "f", PCT0, bold=True, align="right")
        for j in range(5):
            x = L(2 + j)
            put(ws, f"{x}{r}", f"=$B$5*$B$6*$A{r}*({x}$35-waste_actual-labor_pct-other_pct)-$B$5*rent_psf", "f", USD_K)
    ws["D38"].font = Font(name="Arial", size=10, bold=True)
    ws["D38"].border = Border(left=LINE, right=LINE, top=LINE, bottom=LINE)

    widths(ws, {"A": 44, **{L(c): 13 if c <= 6 else 10 for c in range(2, last + 1)}})
    ws.freeze_panes = "B1"
    return dict(month_row=m0, R=R, last_col=last, yearly_profit_row=29)


# ----------------------------------------------------------------------------- Cash
def build_cash(wb, ws, d):
    cols = HISTORY + WEEKS
    first_fc, last = 2 + HISTORY, 1 + cols          # history in B.., forecast after it
    F, T = L(first_fc), L(last)
    title(ws, "Cash: can we afford it?")
    put(ws, "A2", f'="The next {WEEKS} weeks for the stores open today, plus the new store build. Scenario: "&scenario',
        "note")

    wk, st, sales, purch = 12, 13, 14, 15
    rows = {"receipts": 18, "suppliers": 20, "payroll": 21, "rent": 22, "other": 23, "head": 24, "operating": 25,
            "build": 26, "net": 27, "ending": 28, "cushion": 29, "flag": 30}

    section(ws, 4, "Key numbers", 6)
    key = [
        ("Cash in the bank today", "=cash_open", "link"),
        (f"Lowest cash in the next {WEEKS} weeks", f"=MIN({F}{rows['ending']}:{T}{rows['ending']})", "f"),
        (f"Cash at the end of week {WEEKS}", f"={T}{rows['ending']}", "f"),
        ("Funding needed to stay above the cushion", "=MAX(0,cash_cushion-B6)", "f"),
        (f"Cash from the stores over {WEEKS} weeks, before the build",
         f"=SUM({F}{rows['operating']}:{T}{rows['operating']})", "f"),
    ]
    for i, (label, formula, kind) in enumerate(key):
        put(ws, f"A{5 + i}", label, "text", bold=i == 3)
        put(ws, f"B{5 + i}", formula, kind, USD, bold=True)
    put(ws, "C6", f'="in week "&INDEX({F}{wk}:{T}{wk},MATCH(B6,{F}{rows["ending"]}:{T}{rows["ending"]},0))', "note")
    name(wb, "cash_low", ws.title, "B6")
    name(wb, "cash_end", ws.title, "B7")
    name(wb, "cash_funding", ws.title, "B8")

    section(ws, 11, "Week by week (weeks -6 to -1 are history: vendors are still owed for part of them)", last)
    put(ws, f"A{wk}", "Week", "text", bold=True)
    put(ws, f"A{st}", "Week starting", "text")
    put(ws, f"A{sales}", "Sales", "text")
    put(ws, f"A{purch}", "Purchases (goods sold at cost, plus waste)", "text")
    owed = "+".join(f"Data!$D${r}*INDEX($B${purch}:${T}${purch},COLUMN()-1-Data!$E${r})"
                    for r in range(d["term_first"], d["term_total"]))
    for i in range(cols):
        x = L(2 + i)
        week = i - HISTORY + (0 if i < HISTORY else 1)      # -6..-1, then 1..13
        put(ws, f"{x}{wk}", week, "text", "0", bold=True, align="right").border = Border(bottom=LINE)
        if i == HISTORY:
            put(ws, f"{x}{st}", d["end"] + timedelta(days=1), "in", "mmm d", align="right")
        elif i < HISTORY:
            put(ws, f"{x}{st}", f"={L(3 + i)}{st}-7", "f", "mmm d", align="right")
        else:
            put(ws, f"{x}{st}", f"={L(1 + i)}{st}+7", "f", "mmm d", align="right")
        if i < HISTORY:
            put(ws, f"{x}{sales}", f"=Data!D{d['week_first'] + i}", "link", USD_K)
            put(ws, f"{x}{purch}", f"=Data!G{d['week_first'] + i}", "link", USD_K)
            continue
        put(ws, f"{x}{sales}", "=stores_runrate/365*7*(1+sales_shock)", "f", USD_K)
        put(ws, f"{x}{purch}", f"={x}{sales}*(1-gm_actual)+{x}{sales}*waste_actual", "f", USD_K)
        put(ws, f"{x}{rows['receipts']}", f"={x}{sales}", "f", USD_K)
        put(ws, f"{x}{rows['suppliers']}", f"={owed}", "f", USD_K)
        put(ws, f"{x}{rows['payroll']}", f"=IF(ISODD({x}${wk}),labor_pct*({L(i)}{sales}+{L(1 + i)}{sales}),0)", "f", USD_K)
        put(ws, f"{x}{rows['rent']}", f"=IF(DAY({x}${st}+6)<=7,(sqft_s1+sqft_s2+sqft_s3)*rent_psf/12,0)", "f", USD_K)
        put(ws, f"{x}{rows['other']}", f"=other_pct*{x}{sales}", "f", USD_K)
        put(ws, f"{x}{rows['head']}", f"=ho_pct*{x}{sales}", "f", USD_K)
        put(ws, f"{x}{rows['operating']}", f"={x}{rows['receipts']}-SUM({x}{rows['suppliers']}:{x}{rows['head']})",
            "f", USD_K, bold=True)
        put(ws, f"{x}{rows['build']}",
            f"=IF(AND(build_start>0,{x}${wk}>=build_start,{x}${wk}<build_start+build_weeks),ns_invest/build_weeks,0)",
            "f", USD_K)
        put(ws, f"{x}{rows['net']}", f"={x}{rows['operating']}-{x}{rows['build']}", "f", USD_K)
        before = "cash_open" if i == HISTORY else f"{L(1 + i)}{rows['ending']}"
        put(ws, f"{x}{rows['ending']}", f"={before}+{x}{rows['net']}", "f", USD_K, bold=True)
        put(ws, f"{x}{rows['cushion']}", "=cash_cushion", "note", USD_K)
        put(ws, f"{x}{rows['flag']}", f'=IF({x}{rows["ending"]}<cash_cushion,"below","")', "f", align="right")
    for r, label in {17: "Cash in", 19: "Cash out"}.items():
        put(ws, f"A{r}", label, "text", bold=True)
    for key_, label in (("receipts", "Sales receipts"), ("suppliers", "Suppliers, on their payment terms"),
                        ("payroll", "Payroll, every two weeks"), ("rent", "Rent, first week of the month"),
                        ("other", "Other operating costs"), ("head", "Head office"),
                        ("operating", "Cash from the stores"), ("build", "New store build"),
                        ("net", "Net cash flow"), ("ending", "Cash at the end of the week"),
                        ("cushion", "Cash cushion"), ("flag", "Below the cushion?")):
        put(ws, f"A{rows[key_]}", label, "note" if key_ == "cushion" else "text", bold=key_ in ("operating", "ending"))
    rule(ws, rows["operating"], last)
    rule(ws, rows["ending"], last)
    ws.conditional_formatting.add(f"{F}{rows['flag']}:{T}{rows['flag']}", CellIsRule(
        operator="equal", formula=['"below"'], font=Font(name="Arial", bold=True, color="FFC0392B")))
    put(ws, f"A{rows['flag'] + 2}", "How to read it: sales come in the same week. Vendors are paid 0 to 6 weeks after "
                                    "delivery, by their terms on the Data tab. Payroll and rent are lumpy, so some "
                                    "weeks dip.", "note")
    widths(ws, {"A": 46, "B": 13, **{L(c): 10 for c in range(3, last + 1)}})
    ws.freeze_panes = "B13"
    return dict(rows=rows, wk=wk, sales=sales, purch=purch, first=F, last=T, first_col=first_fc, last_col=last)


# ----------------------------------------------------------------------------- Inventory
def build_inventory(wb, ws, d):
    title(ws, "Inventory: where is cash tied up?",
          "Stock on hand today, at cost, against the last 30 days of sales. From the Data tab.")
    stocked = d["stocked_rows"]
    section(ws, 9, "By category", 11)
    headers(ws, 10, ["Category", "Stock at cost", "Cost of goods, 30 days", "Days of stock", "Turns a year",
                     "Gross profit a year per $1 of stock", "Average shelf life (days)", "Stock vs shelf life",
                     "Slow stock", "Slow stock, % of stock", "Stock-outs"], left=("Stock vs shelf life",))
    r0, r = 11, 11
    for src in stocked:
        put(ws, f"A{r}", f"=Data!A{src}", "link")
        put(ws, f"B{r}", f"=Data!J{src}", "link", USD)
        put(ws, f"C{r}", f"=Data!C{src}", "link", USD)
        put(ws, f"D{r}", f"=IF(C{r}=0,0,B{r}/(C{r}/days_window))", "f", ONE)
        put(ws, f"E{r}", f"=IF(D{r}=0,0,365/D{r})", "f", ONE)
        put(ws, f"F{r}", f"=IF(B{r}=0,0,(Data!B{src}-Data!C{src})*365/days_window/B{r})", "f", USD2)
        put(ws, f"G{r}", f"=Data!N{src}", "link", ONE)
        put(ws, f"H{r}", f'=IF(D{r}>G{r},"More stock than it can sell in time","OK")', "f")
        put(ws, f"I{r}", f"=Data!K{src}", "link", USD)
        put(ws, f"J{r}", f"=IF(B{r}=0,0,I{r}/B{r})", "f", PCT0)
        put(ws, f"K{r}", f"=Data!M{src}", "link", NUM)
        r += 1
    rt = r
    put(ws, f"A{rt}", "Total", "text", bold=True)
    for c in "BCIK":
        put(ws, f"{c}{rt}", f"=SUM({c}{r0}:{c}{rt - 1})", "f", NUM if c == "K" else USD, bold=True)
    put(ws, f"D{rt}", f"=IF(C{rt}=0,0,B{rt}/(C{rt}/days_window))", "f", ONE, bold=True)
    put(ws, f"E{rt}", f"=IF(D{rt}=0,0,365/D{rt})", "f", ONE, bold=True)
    put(ws, f"J{rt}", f"=IF(B{rt}=0,0,I{rt}/B{rt})", "f", PCT0, bold=True)
    rule(ws, rt, 11)
    ws.conditional_formatting.add(f"H{r0}:H{rt - 1}", FormulaRule(
        formula=[f'LEFT(H{r0},4)="More"'], font=Font(name="Arial", bold=True, color="FFC0392B")))

    section(ws, 4, "Key numbers", 11)
    key = [("Stock on hand, at cost", f"=B{rt}", USD, "inv_total"),
           ("Days of stock", f"=D{rt}", ONE, "inv_days"),
           ("Slow stock, at cost", f"=I{rt}", USD, "slow_total"),
           ("Slow stock, share of all stock", f"=J{rt}", PCT0, "slow_share")]
    for i, (label, formula, fmt, nm) in enumerate(key):
        put(ws, f"A{5 + i}", label, "text")
        put(ws, f"B{5 + i}", formula, "f", fmt, bold=True)
        name(wb, nm, ws.title, f"B{5 + i}")
    put(ws, f"A{rt + 2}", "Most slow stock is in", "text")
    put(ws, f"B{rt + 2}", f"=INDEX(A{r0}:A{rt - 1},MATCH(MAX(I{r0}:I{rt - 1}),I{r0}:I{rt - 1},0))", "f", bold=True)
    name(wb, "slow_top", ws.title, f"B{rt + 2}")
    put(ws, f"A{rt + 4}", f"Slow stock: more than {SLOW_DAYS} days on hand, or no sales in 30 days. Days of stock = "
                          "stock divided by one day's cost of goods. Turns a year = 365 divided by days of stock. "
                          "Cafe and hot bar items are made fresh each day and hold no stock, so they are left out.", "note")
    widths(ws, {"A": 34, "B": 14, "C": 14, "D": 12, "E": 12, "F": 16, "G": 14, "H": 34, "I": 13, "J": 13, "K": 11})
    ws.row_dimensions[10].height = 42
    return dict(first=r0, total=rt)


# ----------------------------------------------------------------------------- Pricing
def build_pricing(wb, ws, d):
    title(ws, "Pricing: what does a price move do?",
          "Test a price change or a promotion on one category, using its last-30-day price, cost and volume.")
    put(ws, "A4", "Category", "text", bold=True)
    put(ws, "B4", DEFAULT_CATEGORY, "in", fill=YELLOW, bold=True)
    ws.merge_cells("B4:C4")
    dv = DataValidation(type="list", formula1="=category_list", allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("B4")
    put(ws, "D4", "Pick a category from the list.", "note")
    name(wb, "category", ws.title, "B4")
    first, last = d["cat_first"], d["cat_first"] + d["cat_named"] - 1
    look = lambda col: f"=INDEX(Data!${col}${first}:${col}${last},MATCH(category,category_list,0))"  # noqa: E731

    section(ws, 6, "Price change", 6)
    rows = [
        (7, "Average shelf price", look("G"), "link", USD2, None),
        (8, "Average unit cost", look("H"), "link", USD2, None),
        (9, "Units per week, all stores", look("I"), "link", NUM, None),
        (10, "Gross margin now", "=IF(B7=0,0,(B7-B8)/B7)", "f", PCT, None),
        (11, "Target margin", look("F"), "link", PCT, "From the Pricing and Margin Policy."),
        (12, "Gap to target", "=B10-B11", "f", PTS, None),
        (13, "Price change", 0.03, "in", PTS, None),
        (14, "Price elasticity", -1.2, "in", "0.0",
         "-1.2 means a 1% price rise loses 1.2% of units. Not measured in the demo data; grocery is often "
         "-0.5 to -2.5."),
        (15, "New price", "=B7*(1+B13)", "f", USD2, None),
        (16, "Units per week after", "=MAX(0,B9*(1+B14*B13))", "f", NUM, None),
        (17, "Gross profit per week, now", "=(B7-B8)*B9", "f", USD, None),
        (18, "Gross profit per week, after", "=(B15-B8)*B16", "f", USD, None),
        (19, "Change in gross profit per year", "=(B18-B17)*52", "f", USD, None),
        (20, "New gross margin", "=IF(B15=0,0,(B15-B8)/B15)", "f", PCT, None),
        (21, "Break-even change in units", '=IF(B15-B8<=0,"Below cost",(B7-B8)/(B15-B8)-1)', "f", PTS,
         "The unit change that leaves gross profit flat."),
    ]
    for r, label, value, kind, fmt, text in rows:
        put(ws, f"A{r}", label, "text", bold=r == 19)
        put(ws, f"B{r}", value, kind, fmt, bold=r == 19, align="right")
        if text:
            put(ws, f"C{r}", text, "note")
    ws["B13"].fill = YELLOW
    ws["B14"].fill = YELLOW
    name(wb, "price_change", ws.title, "B13")
    name(wb, "price_gp_year", ws.title, "B19")

    put(ws, "A23", "Change in gross profit per year: price change (rows) by elasticity (columns)", "text", bold=True)
    for j, e in enumerate((-0.5, -1.0, -1.5, -2.0, -2.5)):
        put(ws, f"{'BCDEF'[j]}24", e, "in", "0.0", bold=True, align="right").border = Border(bottom=LINE)
    for i, p in enumerate((-0.06, -0.04, -0.02, 0, 0.02, 0.04, 0.06)):
        r = 25 + i
        put(ws, f"A{r}", p, "in", '+0%;-0%;0%', bold=True, align="right")
        for j in range(5):
            x = "BCDEF"[j]
            put(ws, f"{x}{r}", f"=(($B$7*(1+$A{r})-$B$8)*MAX(0,$B$9*(1+{x}$24*$A{r}))-($B$7-$B$8)*$B$9)*52", "f", USD)

    section(ws, 33, "Promotion", 6)
    promo = [
        (34, "Discount", 0.20, "in", PCT0, None),
        (35, "Promotion length (weeks)", 2, "in", NUM, None),
        (36, "Expected unit lift", 0.50, "in", PCT0, "Extra units during the promotion vs a normal week."),
        (37, "Share of the discount the vendor pays", 0.50, "in", PCT0, None),
        (38, "Promotion price", "=B7*(1-B34)", "f", USD2, None),
        (39, "Our margin per unit during the promotion", "=B38-B8+B7*B34*B37", "f", USD2, None),
        (40, "Extra gross profit from the promotion", "=(B39*B9*(1+B36)-(B7-B8)*B9)*B35", "f", USD, None),
        (41, "Break-even unit lift", '=IF(B39<=0,"Sells below cost",(B7-B8)/B39-1)', "f", PCT0,
         "The lift the promotion needs to pay for itself."),
        (42, "Markdown policy (Pricing and Margin Policy)",
         '=IF(B34>0.3,"Over the 30% cap: needs COO approval","Within the 30% cap")', "f", None, None),
    ]
    for r, label, value, kind, fmt, text in promo:
        put(ws, f"A{r}", label, "text", bold=r == 40)
        put(ws, f"B{r}", value, kind, fmt, bold=r == 40, align="left" if r == 42 else "right")
        if text:
            put(ws, f"C{r}", text, "note")
    for r in (34, 36):
        ws[f"B{r}"].fill = YELLOW
    widths(ws, {"A": 44, "B": 13, "C": 13, "D": 13, "E": 13, "F": 13})


# ----------------------------------------------------------------------------- Checks (on the Data tab)
def build_checks(wb, ws, d, ns, cash, inv):
    top = d["checks_at"]
    section(ws, top, "Checks: each one works a number out a second way", 14)
    headers(ws, top + 1, ["Check", "Value", "Worked out again", "Difference", "Status"])
    st, ct, tt = d["store_total"], d["cat_total"], d["term_total"]
    R, end = ns["R"], L(ns["last_col"])
    rows, F, T = cash["rows"], cash["first"], cash["last"]
    profit = f"'New Store'!$D${R['Store profit']}:${end}${R['Store profit']}"
    checks = [
        ("Sales: item lines tie to POS transactions", f"=C{st}", f"=D{st}", USD),
        ("Sales: categories tie to stores", f"=B{ct}", f"=C{st}", USD),
        ("Cost of goods: categories tie to stores", f"=C{ct}", f"=E{st}", USD),
        ("Cost of goods: payment terms cover every vendor", f"=C{tt}", f"=E{st}", USD),
        ("Stock: categories tie to stores", f"=J{ct}", f"=M{st}", USD),
        ("New store: 60 months tie to the 5 yearly totals", f"=SUM({profit})",
         f"=SUM('New Store'!B{ns['yearly_profit_row']}:F{ns['yearly_profit_row']})", USD),
        ("Cash: week 13 equals today's cash plus every week's net flow", f"=Cash!{T}{rows['ending']}",
         f"=cash_open+SUM(Cash!{F}{rows['net']}:{T}{rows['net']})", USD),
        ("Inventory: the total matches the stock in this tab", f"=Inventory!B{inv['total']}", f"=M{st}", USD),
        ("Pricing: a 0% price change changes nothing", "=SUM(Pricing!B28:F28)", 0, USD),
    ]
    r0 = top + 2
    for i, (label, a, b, fmt) in enumerate(checks):
        r = r0 + i
        put(ws, f"A{r}", label, "text")
        put(ws, f"B{r}", a, "f", fmt)
        put(ws, f"C{r}", b, "f" if isinstance(b, str) else "in", fmt)
        put(ws, f"D{r}", f"=B{r}-C{r}", "f", fmt)
        put(ws, f"E{r}", f'=IF(ABS(D{r})<=0.005*MAX(1,ABS(C{r})),"OK","CHECK")', "f", align="right", bold=True)
    lags = f"E{d['term_first']}:E{tt - 1}"
    logic = [
        ("Scenario name is valid", '=IF(ISNUMBER(MATCH(scenario,Inputs!$D$6:$F$6,0)),"OK","CHECK")'),
        ("Ramp and labor floor are between 0% and 100%",
         '=IF(AND(ramp_start>0,ramp_start<=1,ramp_months>=1,labor_floor>=0,labor_floor<=1),"OK","CHECK")'),
        (f"Vendor payment delays fit the {HISTORY} weeks of history",
         f'=IF(AND(MIN({lags})>=0,MAX({lags})<={HISTORY}),"OK","CHECK")'),
        ("Payment shares add up to 100%", f'=IF(ABS(D{tt}-1)<0.0001,"OK","CHECK")'),
        ("Pricing category is in the list", '=IF(ISNUMBER(MATCH(category,category_list,0)),"OK","CHECK")'),
    ]
    r = r0 + len(checks)
    for label, formula in logic:
        put(ws, f"A{r}", label, "text")
        put(ws, f"E{r}", formula, "f", align="right", bold=True)
        r += 1
    rng = f"E{r0}:E{r - 1}"
    put(ws, f"A{r + 1}", "Result", "text", bold=True)
    put(ws, f"B{r + 1}", f'=IF(COUNTIF({rng},"OK")=ROWS({rng}),"All "&ROWS({rng})&" checks pass",'
                         f'ROWS({rng})-COUNTIF({rng},"OK")&" of "&ROWS({rng})&" checks need a look")', "f", bold=True)
    name(wb, "checks_status", ws.title, f"B{r + 1}")
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"OK"'],
                                                  font=Font(name="Arial", bold=True, color="FF" + BRIGHT)))
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"CHECK"'],
                                                  font=Font(name="Arial", bold=True, color=WHITE),
                                                  fill=PatternFill("solid", fgColor="FFC0392B")))


# ----------------------------------------------------------------------------- Summary
def chart_style(ch, heading, money=True):
    ch.title = heading
    ch.height, ch.width = 6.6, 8.2
    ch.legend = None
    ch.x_axis.delete = ch.y_axis.delete = False
    ch.x_axis.tickLblPos = "low"
    if money:
        ch.y_axis.number_format = '$#,##0,"K"'


def build_summary(wb, ws, ns, cash, inv):
    title(ws, "Store planning model", "Should we open a store, can we afford it, where is cash tied up, and what "
                                      "does a price move do? Built on the Store Copilot demo data, which is made up.",
          "2E5E45")
    put(ws, "A4", "Scenario", "text", bold=True)
    put(ws, "B4", "Base", "in", fill=YELLOW, bold=True, align="center")
    dv = DataValidation(type="list", formula1='"Base,Upside,Downside"', allow_blank=False)
    dv.error, dv.errorTitle = "Pick Base, Upside or Downside.", "Scenario"
    ws.add_data_validation(dv)
    dv.add("B4")
    name(wb, "scenario", ws.title, "B4")
    put(ws, "C4", "Pick Base, Upside or Downside. Every tab updates.", "note")
    ws.merge_cells("G4:H4")
    put(ws, "G4", "=checks_status", "link", bold=True, align="right")
    ws.conditional_formatting.add("G4", FormulaRule(formula=['LEFT($G$4,3)="All"'],
                                                    font=Font(name="Arial", bold=True, color="FF" + BRIGHT)))
    ws.conditional_formatting.add("G4", FormulaRule(formula=['LEFT($G$4,3)<>"All"'],
                                                    font=Font(name="Arial", bold=True, color="FFC0392B")))

    tiles = [
        ("A new store pays back in", '=IF(ISNUMBER(ns_payback),ns_payback&" months","Over 60 months")',
         '="on an investment of "&TEXT(ns_invest,"$#,##0")', None),
        ("Funding needed to open it", "=cash_funding",
         '="lowest cash: "&TEXT(cash_low,"$#,##0;-$#,##0")', USD),
        ("Cash tied up in slow stock", "=slow_total",
         '=TEXT(slow_share,"0%")&" of stock; "&TEXT(inv_days,"0")&" days on hand"', USD),
        ("Price test, gross profit a year", "=price_gp_year",
         '=category&" at "&TEXT(price_change,"+0%;-0%")', USD),
    ]
    for i, (label, value, sub, fmt) in enumerate(tiles):
        a, b = L(1 + 2 * i), L(2 + 2 * i)
        for r in (6, 7, 8):
            ws.merge_cells(f"{a}{r}:{b}{r}")
            for col in (a, b):
                ws[f"{col}{r}"].fill = TILE
        put(ws, f"{a}6", label, "note", fill=TILE, size=9, align="left")
        c = put(ws, f"{a}7", value, "f", fmt, fill=TILE, align="left")
        c.font = Font(name="Arial", size=20, bold=True, color=ACCENT)
        put(ws, f"{a}8", sub, "note", fill=TILE, size=9, align="left")
        for r in (6, 7, 8):
            ws[f"{a}{r}"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[7].height = 32

    # Charts
    new = wb["New Store"]
    R, last = ns["R"], ns["last_col"]
    line = LineChart()
    chart_style(line, "New store: cumulative cash")
    line.add_data(Reference(new, min_col=3, max_col=last, min_row=R["Cumulative cash"]), from_rows=True,
                  titles_from_data=False)
    line.set_categories(Reference(new, min_col=3, max_col=last, min_row=ns["month_row"]))
    line.x_axis.title = "Months after opening"
    line.x_axis.tickLblSkip = 12
    line.series[0].graphicalProperties.line.solidFill = BRIGHT
    line.series[0].graphicalProperties.line.width = 28000
    line.series[0].smooth = False
    ws.add_chart(line, "A10")

    cs, rows = wb["Cash"], cash["rows"]
    weeks = LineChart()
    chart_style(weeks, "Cash in the bank, 13 weeks")
    for key, color, width in (("ending", BRIGHT, 28000), ("cushion", "9E9E9E", 12000)):
        weeks.add_data(Reference(cs, min_col=cash["first_col"], max_col=cash["last_col"], min_row=rows[key]),
                       from_rows=True, titles_from_data=False)
        s = weeks.series[-1]
        s.graphicalProperties.line.solidFill = color
        s.graphicalProperties.line.width = width
        s.smooth = False
    weeks.series[1].graphicalProperties.line.dashStyle = "dash"
    weeks.set_categories(Reference(cs, min_col=cash["first_col"], max_col=cash["last_col"], min_row=cash["wk"]))
    weeks.x_axis.title = "Week (dashed line: the cash cushion)"
    ws.add_chart(weeks, "D10")

    stock = wb["Inventory"]
    bars = BarChart()
    bars.type = "bar"
    chart_style(bars, "Days of stock on hand", money=False)
    bars.add_data(Reference(stock, min_col=4, min_row=inv["first"], max_row=inv["total"] - 1), titles_from_data=False)
    bars.set_categories(Reference(stock, min_col=1, min_row=inv["first"], max_row=inv["total"] - 1))
    bars.series[0].graphicalProperties.solidFill = BRIGHT
    bars.y_axis.number_format = "0"
    bars.gapWidth = 60
    bars.x_axis.scaling.orientation = "maxMin"
    ws.add_chart(bars, "G10")

    section(ws, 25, "What this says", 9)
    says = [
        '="A new store costs "&TEXT(ns_invest,"$#,##0")&" and "&IF(ISNUMBER(ns_payback),"pays back in "&ns_payback'
        '&" months.","does not pay back within 5 years.")',
        '=IF(build_start=0,"No new store build is planned in the next 13 weeks.",IF(cash_funding>0,"Starting the build '
        'in week "&build_start&" needs about "&TEXT(cash_funding,"$#,##0")&" of outside funding to keep "'
        '&TEXT(cash_cushion,"$#,##0")&" in the bank.","The build starting in week "&build_start&" can be paid from '
        'cash, staying above the "&TEXT(cash_cushion,"$#,##0")&" cushion."))',
        '=TEXT(slow_total,"$#,##0")&" of stock is slow, "&TEXT(slow_share,"0%")&" of everything on the shelf. Most of '
        'it is in "&slow_top&"."',
        '="Changing "&category&" prices by "&TEXT(price_change,"+0%;-0%")&" changes gross profit by "'
        '&TEXT(price_gp_year,"$#,##0;-$#,##0")&" a year."',
    ]
    for i, s in enumerate(says):
        put(ws, f"A{26 + i}", s, "f")
    section(ws, 31, "How to use it", 9)
    put(ws, "A32", "Blue: an input you can change (Inputs tab).", "in")
    put(ws, "A33", "Black: a formula.      Green: pulled from another tab.", "f")
    ws.merge_cells("A34:B34")
    put(ws, "A34", "Yellow: the main switches.", "text", fill=YELLOW)
    put(ws, "A35", "Sales, cost of goods, waste and stock come from the demo data. Store sizes, labor, rent, opening "
                   "costs and cash in the bank are assumptions, each with a note on Inputs.", "note")
    widths(ws, {L(i): 14.5 for i in range(1, 10)})


def build(db: Path = DB_PATH, out: Path = OUT) -> Path:
    act = load_actuals(ensure_db(db))
    wb = Workbook()
    names = ["Summary", "Inputs", "New Store", "Cash", "Inventory", "Pricing", "Data"]
    wb.active.title = names[0]
    for n in names[1:]:
        wb.create_sheet(n)
    d = build_data(wb, wb["Data"], act)
    build_inputs(wb, wb["Inputs"], d)
    ns = build_new_store(wb, wb["New Store"])
    cash = build_cash(wb, wb["Cash"], d)
    inv = build_inventory(wb, wb["Inventory"], d)
    build_pricing(wb, wb["Pricing"], d)
    build_checks(wb, wb["Data"], d, ns, cash, inv)
    build_summary(wb, wb["Summary"], ns, cash, inv)
    for w in wb.worksheets:
        w.page_setup.orientation = "landscape"
        if w.title not in ("New Store", "Cash"):  # wide monthly and weekly grids print across pages at full size
            w.page_setup.fitToWidth, w.page_setup.fitToHeight = 1, 0
            w.sheet_properties.pageSetUpPr.fitToPage = True
    wb.calculation.fullCalcOnLoad = True
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


if __name__ == "__main__":
    print(f"Wrote {build()}")
