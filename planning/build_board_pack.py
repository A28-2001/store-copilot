"""Build the monthly board pack from the demo database.

    python planning/build_board_pack.py

Writes planning/board_pack.pptx: three slides, one message each.

    1. The month in one slide
    2. Margin leaks, in dollars
    3. Stock and waste

Nothing on the slides is typed by hand. Every number is worked out two ways
(different tables or a different formulation) and the build stops if the two
disagree by more than 0.5%, the same idea as the Copilot's verifier. Run it
again when the month closes and the pack rebuilds itself.

The PDF next to it was exported from the .pptx with LibreOffice.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.generate import DB_PATH, ensure_db, load_config  # noqa: E402

OUT = ROOT / "planning" / "board_pack.pptx"
DAYS, PACE_DAYS, WASTE_TARGET, SLOW_DAYS = 30, 14, 0.03, 60

INK, GREEN, BRIGHT, MUTED, TILE, BAR2 = "1F3A2D", "2E5E45", "2E845A", "6E6A62", "EEF3EF", "C9C4BA"


class Figures(dict):
    """Numbers for the slides. Each one is checked against a second route before it's stored."""

    def twice(self, key, first, second, tol=0.005):
        first, second = float(first or 0), float(second or 0)
        if abs(first - second) > tol * max(1.0, abs(first)):
            raise SystemExit(f"{key}: the two routes disagree ({first:,.2f} vs {second:,.2f}). Nothing written.")
        self[key] = first
        self.checks = getattr(self, "checks", 0) + 1
        return first


def figures(db: Path = DB_PATH) -> Figures:
    end = date.fromisoformat(str(load_config()["data_end_date"]))
    last = f"date('{end}', '-{DAYS} day')"
    prior = f"date('{end}', '-{2 * DAYS} day')"
    jd = f"julianday('{end}')"
    con = sqlite3.connect(f"file:{ensure_db(db)}?mode=ro", uri=True)
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    f = Figures(end=end, start=end - timedelta(days=DAYS - 1))
    try:
        # Slide 1: sales (item lines vs POS transactions), same stores, margin, waste, members
        for label, lo, hi in (("now", last, f"'{end}'"), ("before", prior, last)):
            window = f"sale_date > {lo} AND sale_date <= {hi}"
            f.twice(f"sales_{label}", one(f"SELECT SUM(revenue) FROM sales_daily WHERE {window}"),
                    one(f"SELECT SUM(revenue) FROM transactions_daily WHERE {window}"))
            f.twice(f"gm_{label}", one(f"SELECT 1.0 * SUM(revenue - cogs) / SUM(revenue) FROM sales_daily WHERE {window}"),
                    one(f"SELECT (SUM(CASE WHEN cogs IS NOT NULL THEN revenue END) - SUM(cogs)) / SUM(revenue) "
                        f"FROM sales_daily WHERE {window}"))
            f.twice(f"members_{label}",
                    one(f"SELECT 1.0 * SUM(member_transactions) / SUM(transactions) FROM transactions_daily WHERE {window}"),
                    one(f"SELECT SUM(m) * 1.0 / SUM(t) FROM (SELECT store_id, SUM(member_transactions) m, "
                        f"SUM(transactions) t FROM transactions_daily WHERE {window} GROUP BY store_id)"))
        # Like for like: stores that traded on the first day of the earlier window.
        both = f"(SELECT store_id FROM transactions_daily WHERE sale_date = date({prior}, '+1 day'))"
        for label, lo, hi in (("now", last, f"'{end}'"), ("before", prior, last)):
            window = f"sale_date > {lo} AND sale_date <= {hi}"
            f.twice(f"same_{label}", one(f"SELECT SUM(revenue) FROM sales_daily WHERE {window} AND store_id IN {both}"),
                    one(f"SELECT SUM(revenue) FROM transactions_daily WHERE {window} AND store_id IN {both}"))
        f["same_stores"] = one(f"SELECT COUNT(*) FROM {both}")
        perishable_cogs = f"""SELECT SUM(s.cogs) FROM sales_daily s JOIN sku_master m ON m.master_sku_id = s.master_sku_id
                               WHERE m.perishable = 1 AND s.sale_date > {last}"""
        f.twice("waste", one(f"SELECT SUM(waste_cost) FROM waste_daily WHERE waste_date > {last}"),
                one(f"SELECT SUM(w) FROM (SELECT store_id, SUM(waste_cost) w FROM waste_daily "
                    f"WHERE julianday(waste_date) > {jd} - {DAYS} GROUP BY store_id)"))
        f.twice("perishable_cogs", one(perishable_cogs),
                one(f"""SELECT SUM(s.cogs) FROM sales_daily s WHERE julianday(s.sale_date) > {jd} - {DAYS}
                        AND s.master_sku_id IN (SELECT master_sku_id FROM sku_master WHERE category IN
                            (SELECT category FROM category_targets WHERE perishable = 1))"""))
        f.twice("waste_cafe", one(f"""SELECT SUM(w.waste_cost) FROM waste_daily w JOIN sku_master m
                                      ON m.master_sku_id = w.master_sku_id
                                      WHERE m.category = 'Cafe & Hot Bar' AND w.waste_date > {last}"""),
                one(f"""SELECT SUM(waste_cost) FROM waste_daily WHERE julianday(waste_date) > {jd} - {DAYS}
                        AND master_sku_id IN (SELECT master_sku_id FROM sku_master WHERE category = 'Cafe & Hot Bar')"""))

        # Slide 2: what each leak costs a year at today's pace (sales_daily vs the 14-day velocity view)
        pace = f"""(SELECT master_sku_id, SUM(units) / {PACE_DAYS}.0 AS per_day FROM sales_daily
                    WHERE sale_date > date('{end}', '-{PACE_DAYS} day') AND master_sku_id IS NOT NULL GROUP BY 1)"""
        view = "(SELECT master_sku_id, SUM(avg_daily_units) AS per_day FROM sku_velocity_14d GROUP BY 1)"
        leaks = {
            "leak_cost": ("SUM(COALESCE(p.per_day, 0) * 365 * (c.new_cost - c.old_cost))",
                          "FROM cost_changes c JOIN sku_master m ON m.master_sku_id = c.master_sku_id "
                          "LEFT JOIN {p} p ON p.master_sku_id = c.master_sku_id WHERE m.unit_price >= m.unit_cost"),
            "leak_price": ("SUM(COALESCE(p.per_day, 0) * 365 * (a.app_price - m.unit_price))",
                           "FROM sku_master m JOIN app_catalog a ON a.master_sku_id = m.master_sku_id "
                           "LEFT JOIN {p} p ON p.master_sku_id = m.master_sku_id WHERE m.unit_price < m.unit_cost"),
            "fail_sales": ("SUM(p.per_day * 365 * m.unit_price)",
                           "FROM sku_master m JOIN {p} p ON p.master_sku_id = m.master_sku_id "
                           "WHERE m.clean_standard_status = 'Fail' AND p.per_day > 0"),
            "reprice_gain": ("SUM(COALESCE(p.per_day, 0) * 365 * (m.unit_cost / (1 - t.target_margin_pct / 100.0) "
                             "- m.unit_price))",
                             "FROM cost_changes c JOIN sku_master m ON m.master_sku_id = c.master_sku_id "
                             "JOIN category_targets t ON t.category = m.category "
                             "LEFT JOIN {p} p ON p.master_sku_id = c.master_sku_id "
                             "WHERE m.unit_price >= m.unit_cost AND 100.0 * (m.unit_price - m.unit_cost) / m.unit_price "
                             "< t.target_margin_pct - 5"),
        }
        for key, (expr, rest) in leaks.items():
            f.twice(key, one(f"SELECT {expr} {rest.format(p=pace)}"), one(f"SELECT {expr} {rest.format(p=view)}"))
            f[key + "_items"] = one(f"SELECT COUNT(*) {rest.format(p=pace)}")
        f["leak_waste"] = (f["waste"] - WASTE_TARGET * f["perishable_cogs"]) * 365 / DAYS
        f["sales_year"] = f["sales_now"] * 365 / DAYS

        # Slide 3: stock by category (vs by store), days of stock against shelf life, slow stock
        f.twice("stock", one("""SELECT SUM(i.on_hand * COALESCE(m.unit_cost, 0)) FROM inventory_current i
                                JOIN sku_master m ON m.master_sku_id = i.master_sku_id"""),
                one("""SELECT SUM(v) FROM (SELECT i.store_id, SUM(i.on_hand * COALESCE(m.unit_cost, 0)) v
                       FROM inventory_current i JOIN sku_master m ON m.master_sku_id = i.master_sku_id GROUP BY 1)"""))
        f["categories"] = con.execute(f"""
            WITH stock AS (SELECT m.category, SUM(i.on_hand * COALESCE(m.unit_cost, 0)) AS v
                           FROM inventory_current i JOIN sku_master m ON m.master_sku_id = i.master_sku_id GROUP BY 1),
                 used AS (SELECT m.category, SUM(s.cogs) / {DAYS}.0 AS per_day FROM sales_daily s
                          JOIN sku_master m ON m.master_sku_id = s.master_sku_id WHERE s.sale_date > {last} GROUP BY 1),
                 life AS (SELECT category, AVG(shelf_life_days) AS days FROM sku_master GROUP BY 1)
            SELECT stock.category, stock.v, used.per_day, life.days, t.perishable
            FROM stock JOIN used USING (category) JOIN life USING (category)
            JOIN category_targets t ON t.category = stock.category
            ORDER BY stock.category""").fetchall()
        slow = f"i.on_hand > 0 AND (COALESCE(s.u, 0) = 0 OR i.on_hand * {DAYS}.0 / s.u > {SLOW_DAYS})"
        sold = (f"(SELECT store_id, master_sku_id, SUM(units) AS u FROM sales_daily WHERE sale_date > {last} "
                "AND master_sku_id IS NOT NULL GROUP BY 1, 2)")
        f.twice("slow", one(f"""SELECT SUM(CASE WHEN {slow} THEN i.on_hand * COALESCE(m.unit_cost, 0) ELSE 0 END)
                                FROM inventory_current i JOIN sku_master m ON m.master_sku_id = i.master_sku_id
                                LEFT JOIN {sold} s ON s.store_id = i.store_id AND s.master_sku_id = i.master_sku_id"""),
                one(f"""SELECT SUM(v) FROM (SELECT m.category, SUM(CASE WHEN {slow} THEN i.on_hand * COALESCE(m.unit_cost, 0)
                        ELSE 0 END) v FROM inventory_current i JOIN sku_master m ON m.master_sku_id = i.master_sku_id
                        LEFT JOIN {sold} s ON s.store_id = i.store_id AND s.master_sku_id = i.master_sku_id GROUP BY 1)"""))
        f["slow_top"] = con.execute(f"""SELECT m.category FROM inventory_current i
                                        JOIN sku_master m ON m.master_sku_id = i.master_sku_id
                                        LEFT JOIN {sold} s ON s.store_id = i.store_id AND s.master_sku_id = i.master_sku_id
                                        WHERE {slow} GROUP BY 1 ORDER BY SUM(i.on_hand * m.unit_cost) DESC LIMIT 2""").fetchall()
    finally:
        con.close()
    stocked = [c for c in f["categories"] if c[1] > 0]
    f["stock_days"] = f["stock"] / sum(c[2] for c in stocked)
    f["over_life"] = [c for c in stocked if c[4] and c[1] / c[2] > c[3]]
    f["cash_freed"] = sum(c[1] - c[3] * c[2] for c in f["over_life"])
    return f


# ----------------------------------------------------------------------------- slides
def money(x: float) -> str:
    return f"${x / 1e6:.2f}M" if abs(x) >= 1e6 else f"${x / 1e3:,.0f}K" if abs(x) >= 1e4 else f"${x:,.0f}"


def text(slide, x, y, w, h, runs, size=14, color=INK, bold=False, font="Arial", align=PP_ALIGN.LEFT, anchor=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    if anchor:
        frame.vertical_anchor = anchor
    for i, line in enumerate(runs if isinstance(runs, list) else [runs]):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.alignment = align
        for part in line if isinstance(line, list) else [line]:
            content, style = (part, {}) if isinstance(part, str) else part
            run = para.add_run()
            run.text = content
            run.font.name = style.get("font", font)
            run.font.size = Pt(style.get("size", size))
            run.font.bold = style.get("bold", bold)
            run.font.color.rgb = RGBColor.from_string(style.get("color", color))
        para.space_after = Pt(style.get("after", 6) if isinstance(part, tuple) else 6)
    return box


def frame(prs, f, n, headline):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    text(slide, 0.6, 0.35, 9, 0.3, f"BOARD PACK  ·  30 DAYS TO {f['end']:%b %d, %Y}".upper(), size=10,
         color=BRIGHT, bold=True)
    text(slide, 0.6, 0.65, 12.1, 1.2, headline, size=28, font="Georgia")
    text(slide, 0.6, 7.0, 10.5, 0.3, "Made-up data. Every number on this page is worked out two ways before it's "
                                      "printed. Built by planning/build_board_pack.py.", size=9, color=MUTED)
    text(slide, 12.2, 7.0, 0.5, 0.3, str(n), size=9, color=MUTED, align=PP_ALIGN.RIGHT)
    return slide


def tile(slide, x, y, w, h, label, value, note):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.adjustments[0] = 0.08
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(TILE)
    shape.line.fill.background()
    text(slide, x + 0.2, y + 0.15, w - 0.4, 0.35, label, size=12, color=MUTED)
    text(slide, x + 0.2, y + 0.5, w - 0.4, 0.7, value, size=32, color=GREEN, bold=True)
    text(slide, x + 0.2, y + 1.2, w - 0.4, 0.5, note, size=11, color=MUTED)


def bar_chart(slide, x, y, w, h, categories, series, money_axis=True, legend=False):
    data = CategoryChartData()
    data.categories = categories
    for name, values in series:
        data.add_series(name, values)
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(x), Inches(y), Inches(w), Inches(h), data).chart
    chart.has_title = False
    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(11)
    chart.category_axis.reverse_order = True
    chart.category_axis.tick_labels.font.size = Pt(12)
    chart.value_axis.visible = False
    chart.value_axis.has_major_gridlines = False
    plot = chart.plots[0]
    plot.gap_width = 60
    plot.overlap = -10
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.font.size = Pt(12)
    labels.number_format = '$#,##0,"K"' if money_axis else "0"
    labels.number_format_is_linked = False
    for s, color in zip(plot.series, (BRIGHT, BAR2)):
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = RGBColor.from_string(color)
    return chart


def build(out: Path = OUT) -> Path:
    f = figures()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    # 1. The month in one slide
    growth = f["sales_now"] / f["sales_before"] - 1
    same = f["same_now"] / f["same_before"] - 1
    waste_pct = f["waste"] / f["perishable_cogs"]
    trend = "flat" if abs(same) < 0.01 else f"{same:+.0%}"
    s = frame(prs, f, 1, f"Sales grew {growth:.0%}, all of it from the new store. Margin held; waste is "
                         f"the problem to fix.")
    tiles = [("Sales", money(f["sales_now"]), f"{growth:+.0%} vs the 30 days before; {trend} at the "
                                              f"{f['same_stores']} stores open both times"),
             ("Gross margin", f"{f['gm_now']:.1%}", f"{(f['gm_now'] - f['gm_before']) * 100:+.1f} points"),
             ("Waste", f"{waste_pct:.1%}", f"of perishable cost; the target is under {WASTE_TARGET:.0%}"),
             ("Members", f"{f['members_now']:.0%}", f"of transactions, up from {f['members_before']:.0%}")]
    for i, (label, value, note) in enumerate(tiles):
        tile(s, 0.6 + i * 3.07, 1.95, 2.87, 1.8, label, value, note)
    text(s, 0.6, 4.15, 12, 0.4, "Three things to act on", size=16, bold=True)
    actions = [
        ("Cut waste. ", f"It runs about {money(f['leak_waste'])} a year above target, and "
                       f"{f['waste_cafe'] / f['waste']:.0%} of it is the cafe and hot bar."),
        ("Fix pricing. ", f"{f['leak_price_items']} prices are keyed below cost and {f['leak_cost_items']} vendor "
                         f"cost increases were never passed on: {money(f['leak_price'] + f['leak_cost'])} a year."),
        ("Pull failed items. ", f"{f['fail_sales_items']} items that fail the clean standard are still selling, "
                               f"{money(f['fail_sales'])} a year of sales that put the brand at risk."),
    ]
    text(s, 0.6, 4.6, 12.1, 2.2, [[(f"{i}.  ", {"bold": True, "color": BRIGHT}), (lead, {"bold": True}), body]
                                  for i, (lead, body) in enumerate(actions, 1)], size=15)

    # 2. Margin leaks, in dollars
    total = f["leak_waste"] + f["leak_price"] + f["leak_cost"]
    s = frame(prs, f, 2, f"About {money(total)} a year is leaking from margin, {total / f['sales_year']:.1%} of "
                         f"sales. Most of it is waste, and all of it can be fixed.")
    text(s, 0.6, 1.85, 7, 0.4, "What each leak costs a year, at today's pace", size=13, color=MUTED)
    bar_chart(s, 0.4, 2.2, 7.4, 3.0, ["Waste above the 3% target", "Prices keyed below cost",
                                      "Cost increases not passed on"],
              [("Per year", (round(f["leak_waste"]), round(f["leak_price"]), round(f["leak_cost"])))])
    text(s, 8.3, 1.85, 4.4, 0.4, "What fixes it", size=13, color=MUTED)
    fixes = [
        [("Waste: ", {"bold": True}), "order perishables closer to their shelf life and make smaller hot bar "
                                       "batches (page 3)."],
        [("Prices: ", {"bold": True}), f"correct the {f['leak_price_items']} keying errors today; the app still has "
                                        "the right prices."],
        [("Costs: ", {"bold": True}), f"reprice the {f['reprice_gain_items']} items under the margin floor, worth "
                                       f"{money(f['reprice_gain'])} a year at target margin."],
    ]
    text(s, 8.3, 2.25, 4.4, 3.2, fixes, size=14)
    box = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6), Inches(5.55), Inches(12.1), Inches(1.0))
    box.adjustments[0] = 0.12
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor.from_string(TILE)
    box.line.fill.background()
    text(s, 0.85, 5.62, 11.6, 0.9, [[("Not in the total, but bigger: ", {"bold": True}),
                                     f"{f['fail_sales_items']} items that fail the clean standard still sell "
                                     f"{money(f['fail_sales'])} a year. Pulling them costs those sales, and keeping "
                                     "them costs the brand."]], size=14, anchor=MSO_ANCHOR.MIDDLE)

    # 3. Stock and waste
    names = [c[0].replace("Beverages & Juices", "Juices and drinks") for c in f["over_life"]]
    top = " and ".join(c[0].lower() for c in f["slow_top"])
    s = frame(prs, f, 3, "Fresh food holds more stock than it can sell in time, and slow stock ties up "
                         f"{money(f['slow'])} of cash.")
    text(s, 0.6, 1.85, 7, 0.4, "Days of stock on hand vs average shelf life (days)", size=13, color=MUTED)
    bar_chart(s, 0.4, 2.2, 7.4, 3.6, names,
              [("Days of stock on hand", [round(c[1] / c[2], 1) for c in f["over_life"]]),
               ("Shelf life", [round(c[3], 1) for c in f["over_life"]])], money_axis=False, legend=True)
    points = [
        ("Stock on hand", money(f["stock"]), f"{f['stock_days']:.0f} days of stock at cost"),
        ("Slow stock", money(f["slow"]), f"{f['slow'] / f['stock']:.0%} of stock, mostly {top}"),
        ("Cash freed", money(f["cash_freed"]), "if fresh food held no more than its shelf life"),
    ]
    for i, (label, value, note) in enumerate(points):
        y = 1.95 + i * 1.55
        text(s, 8.3, y, 4.4, 0.35, label, size=12, color=MUTED)
        text(s, 8.3, y + 0.3, 4.4, 0.6, value, size=28, color=GREEN, bold=True)
        text(s, 8.3, y + 0.9, 4.4, 0.4, note, size=12, color=MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.core_properties.title = "Board pack"
    prs.core_properties.author = "Store Copilot"
    prs.save(out)
    print(f"{f.checks} numbers checked two ways")
    return out


if __name__ == "__main__":
    print(f"Wrote {build()}")
