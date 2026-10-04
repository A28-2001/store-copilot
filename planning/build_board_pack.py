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

import sys
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
from copilot.figures import DAYS, WASTE_TARGET, figures, money  # noqa: E402,F401

OUT = ROOT / "planning" / "board_pack.pptx"

INK, GREEN, BRIGHT, MUTED, TILE, BAR2 = "1F3A2D", "2E5E45", "2E845A", "6E6A62", "EEF3EF", "C9C4BA"


# ----------------------------------------------------------------------------- slides
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
