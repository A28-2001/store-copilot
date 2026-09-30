"""Demo-mode question library: 16 questions a store manager or analyst actually asks.

Each template carries two queries written independently:
  sql        the answer
  check_sql  a different formulation (julianday instead of date(), subqueries
             instead of joins, raw sales instead of the velocity view) that
             returns ONE number. The verifier compares it with `check_of` of the
             answer: `rows`, `sum:<col>` or `first:<col>`.

The templates double as the gold answers for evaluating LLM mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from copilot import config

STORE = "st.name || ' (' || st.store_id || ')'"
LAST = "last AS (SELECT MAX(sale_date) AS d FROM sales_daily)"
SINCE = "julianday((SELECT MAX(sale_date) FROM sales_daily)) - {days}"  # check-query date filter


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    keywords: tuple[str, ...]
    sql: str
    check_sql: str
    check_of: str
    summary: str
    example: str
    row_format: str = ""
    chart: dict | None = None
    default_days: int | None = None          # None = the question has no time window
    extras: Callable[[pd.DataFrame], dict] | None = None
    needs_comparison_store: bool = False     # assortment gap: flagship vs another store
    summary_one: str = ""                    # used instead of `summary` when there is exactly one row


def _share(df, part, whole):
    return round(100 * (df[part].sum() / df[whole].sum()), 1) if df[whole].sum() else 0.0


def _weighted(df, value, weight):
    return round((df[value] * df[weight]).sum() / df[weight].sum(), 2) if df[weight].sum() else 0.0


def _trend_extras(df: pd.DataFrame) -> dict:
    lines = []
    for store, g in df.groupby("store", sort=False):
        g = g.sort_values("date")
        last7 = g["revenue"].tail(7).mean()
        prev7 = g["revenue"].iloc[-14:-7].mean() if len(g) >= 14 else float("nan")
        change = "" if pd.isna(prev7) else f" ({(last7 / prev7 - 1) * 100:+.1f}% vs the week before)"
        lines.append(f"{store} averaged ${last7:,.0f}/day over the last 7 days{change}")
    return {"trend_lines": "; ".join(lines), "days": df["date"].nunique()}


TEMPLATES: list[Template] = [
    Template(
        id="low_cover",
        title="SKUs under 5 days of cover",
        keywords=("days of cover", "cover", "running low", "run out", "running out", "low stock", "low on",
                  "low inventory", "days of supply", "about to run out", "need restocking", "restock"),
        sql=f"""
SELECT {STORE} AS store,
       m.product_name AS product,
       m.category,
       i.on_hand,
       ROUND(v.avg_daily_units, 1) AS avg_daily_units,
       ROUND(i.on_hand / v.avg_daily_units, 1) AS days_of_cover,
       i.on_order,
       vd.vendor_name AS vendor,
       vd.lead_time_days
FROM inventory_current i
JOIN sku_velocity_14d v ON v.store_id = i.store_id AND v.master_sku_id = i.master_sku_id
JOIN sku_master m ON m.master_sku_id = i.master_sku_id
JOIN vendors vd ON vd.vendor_id = m.vendor_id
JOIN stores st ON st.store_id = i.store_id
WHERE v.avg_daily_units > 0 AND i.on_hand < 5 * v.avg_daily_units
ORDER BY days_of_cover, v.avg_daily_units DESC""",
        check_sql="""
SELECT COUNT(*)
FROM inventory_current i
JOIN (SELECT store_id, master_sku_id, SUM(units) AS units_14d
      FROM sales_daily
      WHERE master_sku_id IS NOT NULL
        AND julianday(sale_date) > julianday((SELECT MAX(sale_date) FROM sales_daily)) - 14
      GROUP BY store_id, master_sku_id) r
  ON r.store_id = i.store_id AND r.master_sku_id = i.master_sku_id
WHERE i.on_hand * 14 < 5 * r.units_14d""",
        check_of="rows",
        summary=("{rows} SKUs have under 5 days of cover in {scope}, {zero} of them already at zero. "
                 "Most urgent: {first[product]} at {first[store]}, selling {first[avg_daily_units]} a day "
                 "({first[vendor]}, {first[lead_time_days]}-day lead time)."),
        extras=lambda df: {"zero": int((df["on_hand"] == 0).sum())},
        example="Which SKUs have less than 5 days of cover?",
    ),
    Template(
        id="stockouts",
        title="Stock-outs by store",
        keywords=("out of stock", "stock-out", "stockout", "stock out", "zero on hand", "empty shelf",
                  "sold out", "already on order", "on order", "we out of", "are out of", "out at", "anything out"),
        sql=f"""
SELECT {STORE} AS store,
       COUNT(*) AS stockouts,
       SUM(CASE WHEN i.on_order > 0 THEN 1 ELSE 0 END) AS already_on_order,
       SUM(CASE WHEN i.on_order = 0 THEN 1 ELSE 0 END) AS not_on_order
FROM inventory_current i
JOIN stores st ON st.store_id = i.store_id
WHERE i.on_hand = 0
GROUP BY st.store_id
ORDER BY stockouts DESC""",
        check_sql="SELECT COUNT(*) FROM inventory_current WHERE NOT (on_hand > 0)",
        check_of="sum:stockouts",
        summary=("{total[stockouts]:,.0f} SKUs are out of stock in {scope}. {total[already_on_order]:,.0f} are "
                 "already on order and {total[not_on_order]:,.0f} are not. By store: {lines}."),
        row_format="{store} {stockouts}",
        chart={"type": "bar", "x": "store", "y": ["already_on_order", "not_on_order"]},
        summary_one=("{first[store]} has {first[stockouts]} SKUs out of stock: {first[already_on_order]} already on "
                     "order and {first[not_on_order]} not."),
        example="How many items are out of stock, and how many are already on order?",
    ),
    Template(
        id="revenue_by_store",
        title="Revenue, margin and units by store",
        keywords=("revenue", "sales by store", "how did", "doing", "perform", "performance", "total sales",
                  "how much did we sell", "how much did", "margin by store", "units sold", "top line", "numbers"),
        sql=f"""
WITH {LAST}
SELECT {STORE} AS store,
       ROUND(SUM(s.revenue), 2) AS revenue,
       ROUND(100.0 * SUM(s.revenue - s.cogs) / SUM(s.revenue), 1) AS gross_margin_pct,
       SUM(s.units) AS units
FROM sales_daily s
JOIN stores st ON st.store_id = s.store_id, last
WHERE s.sale_date > date(last.d, '-{{days}} days')
GROUP BY st.store_id
ORDER BY revenue DESC""",
        check_sql=f"SELECT SUM(revenue) FROM sales_daily WHERE julianday(sale_date) > {SINCE}",
        check_of="sum:revenue",
        summary="Over the {period} in {scope}: {lines}. Total ${total[revenue]:,.0f}.",
        row_format="{store} made ${revenue:,.0f} at {gross_margin_pct}% gross margin",
        chart={"type": "bar", "x": "store", "y": "revenue"},
        default_days=30,
        summary_one=("{first[store]} made ${first[revenue]:,.0f} over the {period} at {first[gross_margin_pct]}% "
                     "gross margin, selling {first[units]:,.0f} units."),
        example="What was revenue and margin by store over the last 30 days?",
    ),
    Template(
        id="margin_vs_target",
        title="Category margin vs target",
        keywords=("margin target", "below target", "vs target", "versus target", "against target", "margin by category",
                  "category margin", "target margin", "margin gap", "under target", "below their margin",
                  "missing margin", "hitting margin", "categories"),
        sql=f"""
WITH {LAST},
cat AS (
  SELECT m.category, SUM(s.revenue) AS revenue, SUM(s.revenue - s.cogs) AS gross_margin
  FROM sales_daily s
  JOIN sku_master m ON m.master_sku_id = s.master_sku_id, last
  WHERE s.sale_date > date(last.d, '-{{days}} days')
  GROUP BY m.category)
SELECT c.category,
       ROUND(c.revenue, 2) AS revenue,
       ROUND(100.0 * c.gross_margin / c.revenue, 1) AS margin_pct,
       t.target_margin_pct AS target_pct,
       ROUND(100.0 * c.gross_margin / c.revenue - t.target_margin_pct, 1) AS gap_pts
FROM cat c
JOIN category_targets t ON t.category = c.category
ORDER BY gap_pts""",
        check_sql=f"""
SELECT SUM(revenue) FROM sales_daily
WHERE master_sku_id IN (SELECT master_sku_id FROM sku_master)
  AND julianday(sale_date) > {SINCE}""",
        check_of="sum:revenue",
        summary=("Over the {period} in {scope}, {below} of {rows} categories are below their margin target. "
                 "Furthest below: {first[category]} at {first[margin_pct]}% against a {first[target_pct]:.0f}% "
                 "target ({first[gap_pts]} points)."),
        extras=lambda df: {"below": int((df["gap_pts"] < 0).sum())},
        chart={"type": "diverging", "x": "gap_pts", "y": "category"},
        default_days=30,
        example="Which categories are below their margin target?",
    ),
    Template(
        id="top_margin_skus",
        title="Top 20 SKUs by gross margin",
        keywords=("most profitable", "top products", "top 20", "top skus", "top items", "highest margin",
                  "by gross margin", "margin dollars", "best margin", "most margin", "biggest earners", "make us the most",
                  "the most gross margin", "earned the most", "most margin dollars"),
        sql=f"""
WITH {LAST}
SELECT m.product_name AS product,
       m.category,
       SUM(s.units) AS units,
       ROUND(SUM(s.revenue), 2) AS revenue,
       ROUND(SUM(s.revenue - s.cogs), 2) AS gross_margin
FROM sales_daily s
JOIN sku_master m ON m.master_sku_id = s.master_sku_id, last
WHERE s.sale_date > date(last.d, '-{{days}} days')
GROUP BY m.master_sku_id
ORDER BY gross_margin DESC
LIMIT 20""",
        check_sql=f"""
SELECT MAX(gm) FROM (
  SELECT master_sku_id, SUM(revenue - cogs) AS gm
  FROM sales_daily
  WHERE master_sku_id IS NOT NULL AND julianday(sale_date) > {SINCE}
  GROUP BY master_sku_id)""",
        check_of="first:gross_margin",
        summary=("The most profitable SKU over the {period} in {scope} is {first[product]}: ${first[gross_margin]:,.0f} "
                 "of gross margin on ${first[revenue]:,.0f} of sales. The top 20 together made ${total[gross_margin]:,.0f}."),
        chart={"type": "hbar", "x": "gross_margin", "y": "product"},
        default_days=7,
        example="What are our top 20 products by gross margin this week?",
    ),
    Template(
        id="waste",
        title="Waste vs the 3% target",
        keywords=("waste", "wasted", "wasting", "shrink", "spoilage", "spoiled", "thrown out", "throw away",
                  "throwing away", "discarded", "binned"),
        sql=f"""
WITH {LAST},
w AS (
  SELECT store_id, SUM(waste_cost) AS waste_cost
  FROM waste_daily, last
  WHERE waste_date > date(last.d, '-{{days}} days')
  GROUP BY store_id),
p AS (
  SELECT s.store_id, SUM(s.cogs) AS perishable_cogs
  FROM sales_daily s
  JOIN sku_master m ON m.master_sku_id = s.master_sku_id, last
  WHERE m.perishable = 1 AND s.sale_date > date(last.d, '-{{days}} days')
  GROUP BY s.store_id)
SELECT {STORE} AS store,
       ROUND(w.waste_cost, 2) AS waste_cost,
       ROUND(p.perishable_cogs, 2) AS perishable_cogs,
       ROUND(100.0 * w.waste_cost / p.perishable_cogs, 1) AS waste_pct,
       3.0 AS target_pct
FROM w
JOIN p ON p.store_id = w.store_id
JOIN stores st ON st.store_id = w.store_id
ORDER BY waste_pct DESC""",
        check_sql=f"SELECT SUM(waste_cost) FROM waste_daily WHERE julianday(waste_date) > {SINCE}",
        check_of="sum:waste_cost",
        summary=("Waste over the {period} in {scope} cost ${total[waste_cost]:,.0f}, {overall}% of perishable cost "
                 "against a target of under 3%. {lines}."),
        row_format="{store} {waste_pct}%",
        extras=lambda df: {"overall": _share(df, "waste_cost", "perishable_cogs")},
        chart={"type": "bar", "x": "store", "y": "waste_pct", "target": 3.0},
        default_days=30,
        summary_one=("Waste at {first[store]} over the {period} cost ${first[waste_cost]:,.0f}, "
                     "{first[waste_pct]}% of perishable cost against a target of under 3%."),
        example="What is our waste as a percent of perishable cost by store?",
    ),
    Template(
        id="clean_standard",
        title="Fail and Review items still selling",
        keywords=("clean standard", "fail", "failed", "review items", "under review", "flagged", "banned",
                  "seed oil", "watchlist", "still selling", "still on the shelf", "non-compliant", "ingredient"),
        sql=f"""
WITH {LAST}
SELECT m.clean_standard_status AS status,
       m.product_name AS product,
       m.category,
       m.flagged_ingredient,
       GROUP_CONCAT(DISTINCT s.store_id) AS stores,
       SUM(s.units) AS units_7d,
       ROUND(SUM(s.revenue), 2) AS revenue_7d
FROM sales_daily s
JOIN sku_master m ON m.master_sku_id = s.master_sku_id, last
WHERE m.clean_standard_status IN ('Fail', 'Review')
  AND s.sale_date > date(last.d, '-7 days')
GROUP BY m.master_sku_id
ORDER BY CASE m.clean_standard_status WHEN 'Fail' THEN 0 ELSE 1 END, revenue_7d DESC""",
        check_sql=f"""
SELECT COUNT(DISTINCT master_sku_id) FROM sales_daily
WHERE master_sku_id IN (SELECT master_sku_id FROM sku_master WHERE clean_standard_status <> 'Pass')
  AND julianday(sale_date) > {SINCE.format(days=7)}""",
        check_of="rows",
        summary=("{fails} Fail items and {reviews} Review items sold in the last 7 days in {scope}. "
                 "Fail items should be pulled within 48 hours. Top Fail seller: {first[product]} "
                 "(flagged for {first[flagged_ingredient]})."),
        extras=lambda df: {"fails": int((df["status"] == "Fail").sum()), "reviews": int((df["status"] == "Review").sum())},
        example="Which Fail or Review items are still selling?",
    ),
    Template(
        id="sku_hygiene",
        title="Unmapped and duplicate POS codes",
        keywords=("sku list", "messy", "mess", "duplicate", "unmapped", "pos code", "codes", "mapping", "mapped",
                  "sku hygiene", "data quality", "clean up", "two codes", "double"),
        sql=f"""
WITH {LAST},
unmapped AS (
  SELECT store_id, COUNT(*) AS n FROM sku_map WHERE master_sku_id IS NULL GROUP BY store_id),
dup AS (
  SELECT store_id, COUNT(*) AS skus, SUM(codes) - COUNT(*) AS extra_codes
  FROM (SELECT store_id, master_sku_id, COUNT(*) AS codes
        FROM sku_map WHERE master_sku_id IS NOT NULL
        GROUP BY store_id, master_sku_id HAVING COUNT(*) > 1)
  GROUP BY store_id),
unmapped_sales AS (
  SELECT s.store_id, SUM(s.revenue) AS revenue
  FROM sales_daily s, last
  WHERE s.master_sku_id IS NULL AND s.sale_date > date(last.d, '-30 days')
  GROUP BY s.store_id)
SELECT {STORE} AS store,
       COALESCE(u.n, 0) AS unmapped_codes,
       COALESCE(d.skus, 0) AS skus_with_duplicate_codes,
       COALESCE(u.n, 0) + COALESCE(d.extra_codes, 0) AS codes_to_fix,
       ROUND(COALESCE(us.revenue, 0), 2) AS unmapped_sales_30d
FROM stores st
LEFT JOIN unmapped u ON u.store_id = st.store_id
LEFT JOIN dup d ON d.store_id = st.store_id
LEFT JOIN unmapped_sales us ON us.store_id = st.store_id
ORDER BY codes_to_fix DESC""",
        check_sql="""
SELECT (SELECT COUNT(*) FROM sku_map WHERE master_sku_id IS NULL)
     + (SELECT COUNT(*) FROM sku_map WHERE master_sku_id IS NOT NULL)
     - (SELECT COUNT(*) FROM (SELECT DISTINCT store_id, master_sku_id FROM sku_map WHERE master_sku_id IS NOT NULL))""",
        check_of="sum:codes_to_fix",
        summary=("{total[codes_to_fix]:,.0f} POS codes need fixing in {scope}: {total[unmapped_codes]:,.0f} codes "
                 "have no product behind them (${total[unmapped_sales_30d]:,.0f} of sales in 30 days that no report "
                 "can attribute) and {total[skus_with_duplicate_codes]:,.0f} products ring up under two codes. {lines}."),
        row_format="{store}: {unmapped_codes} unmapped, {skus_with_duplicate_codes} duplicated",
        chart={"type": "bar", "x": "store", "y": ["skus_with_duplicate_codes", "unmapped_codes"]},
        summary_one=("{first[store]} has {first[codes_to_fix]} POS codes to fix: {first[unmapped_codes]} with no "
                     "product behind them (${first[unmapped_sales_30d]:,.0f} of sales in 30 days) and "
                     "{first[skus_with_duplicate_codes]} products ringing up under two codes."),
        example="How messy is our SKU list? Any unmapped or duplicate POS codes?",
    ),
    Template(
        id="local_brands",
        title="Local brand share",
        keywords=("local brand", "local", "south florida", "emerging brand", "local share", "local vendors",
                  "local products"),
        sql=f"""
WITH {LAST}
SELECT {STORE} AS store,
       ROUND(SUM(CASE WHEN m.is_local_brand = 1 THEN s.revenue ELSE 0 END), 2) AS local_revenue,
       ROUND(SUM(s.revenue), 2) AS product_revenue,
       ROUND(100.0 * SUM(CASE WHEN m.is_local_brand = 1 THEN s.revenue ELSE 0 END) / SUM(s.revenue), 1) AS local_share_pct
FROM sales_daily s
JOIN sku_master m ON m.master_sku_id = s.master_sku_id
JOIN stores st ON st.store_id = s.store_id, last
WHERE s.sale_date > date(last.d, '-{{days}} days')
GROUP BY st.store_id
ORDER BY local_share_pct DESC""",
        check_sql=f"""
SELECT SUM(revenue) FROM sales_daily
WHERE master_sku_id IN (SELECT master_sku_id FROM sku_master WHERE is_local_brand = 1)
  AND julianday(sale_date) > {SINCE}""",
        check_of="sum:local_revenue",
        summary=("Local brands made ${total[local_revenue]:,.0f} over the {period} in {scope}, {overall}% of product "
                 "revenue. {lines}."),
        row_format="{store} {local_share_pct}%",
        extras=lambda df: {"overall": _share(df, "local_revenue", "product_revenue")},
        chart={"type": "bar", "x": "store", "y": "local_share_pct"},
        default_days=30,
        summary_one=("Local brands made ${first[local_revenue]:,.0f} at {first[store]} over the {period}, "
                     "{first[local_share_pct]}% of product revenue."),
        example="What share of revenue comes from local brands?",
    ),
    Template(
        id="basket_members",
        title="Transactions, basket and member share",
        keywords=("basket", "member", "membership", "transactions", "loyalty", "customers", "shoppers",
                  "ticket", "average order", "visits", "foot traffic"),
        sql=f"""
WITH last AS (SELECT MAX(sale_date) AS d FROM transactions_daily)
SELECT {STORE} AS store,
       SUM(t.transactions) AS transactions,
       ROUND(SUM(t.revenue) / SUM(t.transactions), 2) AS avg_basket,
       ROUND(100.0 * SUM(t.member_transactions) / SUM(t.transactions), 1) AS member_share_pct
FROM transactions_daily t
JOIN stores st ON st.store_id = t.store_id, last
WHERE t.sale_date > date(last.d, '-{{days}} days')
GROUP BY st.store_id
ORDER BY transactions DESC""",
        check_sql="""
SELECT SUM(transactions) FROM transactions_daily
WHERE julianday(sale_date) > julianday((SELECT MAX(sale_date) FROM transactions_daily)) - {days}""",
        check_of="sum:transactions",
        summary=("Over the {period} in {scope}: {total[transactions]:,.0f} transactions, an average basket of "
                 "${basket:,.2f}, and members made {members}% of transactions. {lines}."),
        row_format="{store} ${avg_basket:,.2f} basket, {member_share_pct}% members",
        extras=lambda df: {"basket": _weighted(df, "avg_basket", "transactions"),
                           "members": round(_weighted(df, "member_share_pct", "transactions"), 1)},
        chart={"type": "bar", "x": "store", "y": "avg_basket"},
        default_days=30,
        summary_one=("Over the {period}, {first[store]} had {first[transactions]:,.0f} transactions with an average "
                     "basket of ${first[avg_basket]:,.2f}; members made {first[member_share_pct]}% of them."),
        example="What's the average basket and member share by store?",
    ),
    Template(
        id="assortment_gap",
        title="Flagship top sellers a store doesn't carry",
        keywords=("not carried", "isn't carried", "aren't carried", "don't carry", "doesn't carry", "not carry",
                  "not stocked", "missing from", "assortment", "gap", "should carry", "flagship top sellers",
                  "top sellers"),
        sql="""
WITH last AS (SELECT MAX(sale_date) AS d FROM sales_daily),
flagship AS (
  SELECT s.master_sku_id, SUM(s.revenue) AS revenue, SUM(s.units) AS units
  FROM sales_daily s, last
  WHERE s.store_id = '{flagship}' AND s.master_sku_id IS NOT NULL
    AND s.sale_date > date(last.d, '-30 days')
  GROUP BY s.master_sku_id)
SELECT m.product_name AS product,
       m.category,
       m.brand,
       ROUND(f.revenue, 2) AS flagship_revenue_30d,
       f.units AS flagship_units_30d
FROM flagship f
JOIN sku_master m ON m.master_sku_id = f.master_sku_id
WHERE f.master_sku_id NOT IN (SELECT master_sku_id FROM sku_map
                              WHERE store_id = '{target}' AND master_sku_id IS NOT NULL)
ORDER BY f.revenue DESC
LIMIT 25""",
        check_sql="""
SELECT MAX(r) FROM (
  SELECT master_sku_id, SUM(revenue) AS r FROM sales_daily
  WHERE store_id = '{flagship}' AND master_sku_id IS NOT NULL
    AND julianday(sale_date) > julianday((SELECT MAX(sale_date) FROM sales_daily)) - 30
  GROUP BY master_sku_id) g
WHERE NOT EXISTS (SELECT 1 FROM sku_map mp WHERE mp.store_id = '{target}' AND mp.master_sku_id = g.master_sku_id)""",
        check_of="first:flagship_revenue_30d",
        summary=("These are the Flagship's best sellers over the last 30 days that {target_label} doesn't carry. "
                 "The biggest gap is {first[product]} (${first[flagship_revenue_30d]:,.0f} at the Flagship), "
                 "and the top {rows} together sold ${total[flagship_revenue_30d]:,.0f} there."),
        chart={"type": "hbar", "x": "flagship_revenue_30d", "y": "product"},
        needs_comparison_store=True,
        example="Which flagship top sellers aren't carried at Store 3?",
    ),
    Template(
        id="reorder_by_vendor",
        title="What to order, by vendor",
        keywords=("order from", "reorder", "re-order", "purchase order", "what to order", "what should we order",
                  "which vendors", "place orders", "suggested order", "ordering", "orders", "vendors"),
        sql=f"""
WITH line AS (
  SELECT i.store_id, m.vendor_id, m.unit_cost, i.on_hand, i.on_order, v.avg_daily_units, vd.lead_time_days,
         CASE WHEN i.on_hand < 5 * v.avg_daily_units THEN 1 ELSE 0 END AS is_low
  FROM inventory_current i
  JOIN sku_velocity_14d v ON v.store_id = i.store_id AND v.master_sku_id = i.master_sku_id
  JOIN sku_master m ON m.master_sku_id = i.master_sku_id
  JOIN vendors vd ON vd.vendor_id = m.vendor_id
  WHERE v.avg_daily_units > 0 AND m.vendor_id <> 'V000'),
need AS (
  SELECT *, CAST(MAX(0, (lead_time_days + 14) * avg_daily_units - on_hand - on_order) + 0.999 AS INTEGER) AS order_units
  FROM line)
SELECT {STORE} AS store,
       vd.vendor_name AS vendor,
       SUM(n.is_low) AS low_skus,
       SUM(CASE WHEN n.order_units > 0 THEN 1 ELSE 0 END) AS skus_to_order,
       ROUND(SUM(n.order_units * n.unit_cost), 2) AS suggested_order_usd,
       vd.min_order_usd,
       vd.lead_time_days,
       vd.payment_terms,
       CASE WHEN SUM(n.order_units * n.unit_cost) >= vd.min_order_usd
            THEN 'meets minimum' ELSE 'below minimum' END AS minimum_check
FROM need n
JOIN vendors vd ON vd.vendor_id = n.vendor_id
JOIN stores st ON st.store_id = n.store_id
GROUP BY n.store_id, n.vendor_id
HAVING SUM(n.is_low) > 0
ORDER BY suggested_order_usd DESC""",
        check_sql="""
SELECT COUNT(*)
FROM inventory_current i
JOIN (SELECT store_id, master_sku_id, SUM(units) AS units_14d FROM sales_daily
      WHERE julianday(sale_date) > julianday((SELECT MAX(sale_date) FROM sales_daily)) - 14
      GROUP BY store_id, master_sku_id) r
  ON r.store_id = i.store_id AND r.master_sku_id = i.master_sku_id
WHERE i.on_hand * 14 < 5 * r.units_14d
  AND i.master_sku_id NOT IN (SELECT master_sku_id FROM sku_master WHERE vendor_id = 'V000')""",
        check_of="sum:low_skus",
        summary=("{rows} vendor orders are due in {scope}, about ${total[suggested_order_usd]:,.0f} at cost. "
                 "Biggest: {first[vendor]} for {first[store]}, ${first[suggested_order_usd]:,.0f} "
                 "({first[payment_terms]}). {below} are under the vendor's minimum."),
        extras=lambda df: {"below": int((df["minimum_check"] == "below minimum").sum())},
        example="Which vendors should we order from today?",
    ),
    Template(
        id="daily_trend",
        title="Daily revenue trend by store",
        keywords=("trend", "daily revenue", "over time", "by day", "each day", "daily sales", "day by day",
                  "trajectory", "week over week", "chart", "growing", "per day", "plot"),
        sql=f"""
SELECT s.sale_date AS date,
       {STORE} AS store,
       ROUND(SUM(s.revenue), 2) AS revenue
FROM sales_daily s
JOIN stores st ON st.store_id = s.store_id
GROUP BY s.sale_date, s.store_id
ORDER BY s.sale_date, s.store_id""",
        check_sql="SELECT SUM(revenue) FROM sales_daily",
        check_of="sum:revenue",
        summary="Daily revenue for {scope} over the last {days} days. {trend_lines}.",
        extras=_trend_extras,
        chart={"type": "line", "x": "date", "y": "revenue", "color": "store"},
        example="Show me the daily revenue trend by store.",
    ),
]

MASTER_DATA_CHECKS = [
    ("Margin 5+ pts under target after a cost increase", "New price due within 3 days", """
     SELECT COUNT(*) FROM cost_changes c
     JOIN sku_master m ON m.master_sku_id = c.master_sku_id
     JOIN category_targets t ON t.category = m.category
     WHERE 100.0 * (m.unit_price - m.unit_cost) / m.unit_price < t.target_margin_pct - 5"""),
    ("Price below cost", "Likely a keying error, fix before the next sale",
     "SELECT COUNT(*) FROM sku_master WHERE unit_price < unit_cost"),
    ("Missing cost", "No cost, no price", "SELECT COUNT(*) FROM sku_master WHERE unit_cost IS NULL"),
    ("Vendor record missing terms", "Needed before the next order",
     "SELECT COUNT(*) FROM vendors WHERE payment_terms IS NULL OR min_order_usd IS NULL"),
    ("Unmapped POS code", "Fix within 48 hours", "SELECT COUNT(*) FROM sku_map WHERE master_sku_id IS NULL"),
    ("Product with two POS codes", "Merge into one code", """
     SELECT COUNT(*) FROM (SELECT 1 FROM sku_map WHERE master_sku_id IS NOT NULL
                           GROUP BY store_id, master_sku_id HAVING COUNT(*) > 1)"""),
    ("App price differs from POS", "One price in stores and the app", """
     SELECT COUNT(*) FROM app_catalog a JOIN sku_master m ON m.master_sku_id = a.master_sku_id
     WHERE a.listed = 1 AND m.clean_standard_status <> 'Fail' AND ABS(a.app_price - m.unit_price) > 0.005"""),
    ("Selling in stores, missing from the app", "List within 2 days of setup", """
     SELECT COUNT(DISTINCT s.master_sku_id) FROM sales_daily s
     JOIN sku_master m ON m.master_sku_id = s.master_sku_id, last
     WHERE s.sale_date > date(last.d, '-30 days') AND m.clean_standard_status <> 'Fail'
       AND s.master_sku_id NOT IN (SELECT master_sku_id FROM app_catalog WHERE listed = 1)"""),
    ("Fail item still in the app", "Delist within 48 hours", """
     SELECT COUNT(*) FROM app_catalog a JOIN sku_master m ON m.master_sku_id = a.master_sku_id
     WHERE a.listed = 1 AND m.clean_standard_status = 'Fail'"""),
]
AUDIT_SQL = f"""
WITH {LAST},
issues AS (
""" + "\n  UNION ALL\n".join(
    f"  SELECT {n} AS n, '{issue}' AS issue, ({sql.strip()}) AS items, '{rule}' AS rule"
    for n, (issue, rule, sql) in enumerate(MASTER_DATA_CHECKS, 1)) + """)
SELECT issue, items, rule FROM issues WHERE items > 0 ORDER BY n"""

AUDIT_CHECK_SQL = """
SELECT
  (SELECT COUNT(*) FROM sku_master m
   WHERE m.master_sku_id IN (SELECT master_sku_id FROM cost_changes)
     AND (1 - m.unit_cost / m.unit_price) * 100 + 5
         < (SELECT target_margin_pct FROM category_targets t WHERE t.category = m.category))
+ (SELECT COUNT(*) FROM sku_master WHERE unit_cost - unit_price > 0)
+ (SELECT SUM(CASE WHEN unit_cost IS NULL THEN 1 ELSE 0 END) FROM sku_master)
+ (SELECT SUM(CASE WHEN payment_terms IS NULL OR min_order_usd IS NULL THEN 1 ELSE 0 END) FROM vendors)
+ (SELECT SUM(CASE WHEN master_sku_id IS NULL THEN 1 ELSE 0 END) FROM sku_map)
+ (SELECT COUNT(DISTINCT mp.store_id || '|' || mp.master_sku_id) FROM sku_map mp
   WHERE mp.master_sku_id IS NOT NULL AND EXISTS (
     SELECT 1 FROM sku_map o WHERE o.store_id = mp.store_id AND o.master_sku_id = mp.master_sku_id
                               AND o.local_sku <> mp.local_sku))
+ (SELECT COUNT(*) FROM app_catalog a WHERE a.listed = 1 AND EXISTS (
     SELECT 1 FROM sku_master m WHERE m.master_sku_id = a.master_sku_id AND m.clean_standard_status <> 'Fail'
                                  AND ROUND(m.unit_price, 2) <> ROUND(a.app_price, 2)))
+ (SELECT COUNT(*) FROM sku_master m WHERE m.clean_standard_status <> 'Fail'
     AND NOT EXISTS (SELECT 1 FROM app_catalog a WHERE a.master_sku_id = m.master_sku_id AND a.listed = 1)
     AND m.master_sku_id IN (SELECT master_sku_id FROM sales_daily WHERE
         julianday(sale_date) > julianday((SELECT MAX(sale_date) FROM sales_daily)) - 30))
+ (SELECT COUNT(*) FROM sku_master m WHERE m.clean_standard_status = 'Fail'
     AND m.master_sku_id IN (SELECT master_sku_id FROM app_catalog WHERE listed = 1))"""

TEMPLATES += [
    Template(
        id="master_data_audit",
        title="Master data audit",
        keywords=("master data", "audit", "needs fixing", "need fixing", "data issues", "item setup",
                  "data problems", "what should i fix", "catalog issues"),
        sql=AUDIT_SQL,
        check_sql=AUDIT_CHECK_SQL,
        check_of="sum:items",
        summary=("The master data audit found {total[items]:,.0f} open issues across {rows} checks in {scope}. "
                 "The biggest: {top}."),
        extras=lambda df: {"top": " and ".join(
            f"{r.issue[0].lower() + r.issue[1:]} ({r.items})"
            for r in df.sort_values("items", ascending=False).head(2).itertuples())},
        example="What needs fixing in master data?",
    ),
    Template(
        id="price_exceptions",
        title="Prices that need a decision",
        keywords=("margin floor", "under the floor", "below cost", "price below cost", "cost increase",
                  "cost went up", "costs went up", "raised their cost", "reprice", "repricing", "new price",
                  "price exceptions", "pricing exceptions", "keying error", "typo"),
        sql=f"""
WITH {LAST}
SELECT m.product_name AS product,
       m.category,
       v.vendor_name AS vendor,
       m.unit_price AS price,
       m.unit_cost AS cost,
       ROUND(100.0 * (m.unit_price - m.unit_cost) / m.unit_price, 1) AS margin_pct,
       t.target_margin_pct AS target_pct,
       CASE WHEN m.unit_price < m.unit_cost THEN 'Price below cost' ELSE 'Cost went up, not repriced' END AS issue,
       COALESCE(c.effective_date, m.price_changed_on) AS changed_on,
       CAST(julianday(last.d) - julianday(COALESCE(c.effective_date, m.price_changed_on)) AS INTEGER) AS days_open
FROM sku_master m
JOIN category_targets t ON t.category = m.category
JOIN vendors v ON v.vendor_id = m.vendor_id
LEFT JOIN cost_changes c ON c.master_sku_id = m.master_sku_id, last
WHERE m.unit_price < m.unit_cost
   OR (c.master_sku_id IS NOT NULL
       AND 100.0 * (m.unit_price - m.unit_cost) / m.unit_price < t.target_margin_pct - 5)
ORDER BY CASE WHEN m.unit_price < m.unit_cost THEN 0 ELSE 1 END, days_open DESC""",
        check_sql="""
SELECT (SELECT COUNT(*) FROM sku_master WHERE unit_cost - unit_price > 0)
     + (SELECT COUNT(*) FROM cost_changes c WHERE EXISTS (
          SELECT 1 FROM sku_master m WHERE m.master_sku_id = c.master_sku_id AND m.unit_price >= m.unit_cost
            AND (1 - m.unit_cost / m.unit_price) * 100 + 5
                < (SELECT target_margin_pct FROM category_targets t WHERE t.category = m.category)))""",
        check_of="rows",
        summary=("{rows} prices need a decision: {below} below cost (likely keying errors) and {floor} under the "
                 "margin floor after a vendor cost increase, {overdue} of them past the 3-day window. Worst: "
                 "{first[product]} at ${first[price]:,.2f} against a ${first[cost]:,.2f} cost."),
        extras=lambda df: {"below": int((df["issue"] == "Price below cost").sum()),
                           "floor": int((df["issue"] != "Price below cost").sum()),
                           "overdue": int(((df["issue"] != "Price below cost") & (df["days_open"] > 3)).sum())},
        example="Which prices fell under the margin floor?",
    ),
    Template(
        id="app_sync",
        title="POS vs app catalog",
        keywords=("app", "in sync", "out of sync", "sync", "app catalog", "app price", "in the app",
                  "missing from the app", "listed", "listing", "digital catalog"),
        sql=f"""
WITH {LAST},
selling AS (
  SELECT DISTINCT s.master_sku_id FROM sales_daily s, last
  WHERE s.master_sku_id IS NOT NULL AND s.sale_date > date(last.d, '-30 days'))
SELECT m.product_name AS product,
       m.category,
       m.unit_price AS pos_price,
       a.app_price,
       CASE WHEN m.clean_standard_status = 'Fail' THEN 'Fail item still in the app'
            WHEN a.listed = 1 THEN 'App price differs'
            ELSE 'Missing from the app' END AS issue
FROM sku_master m
LEFT JOIN app_catalog a ON a.master_sku_id = m.master_sku_id
WHERE (a.listed = 1 AND m.clean_standard_status = 'Fail')
   OR (a.listed = 1 AND m.clean_standard_status <> 'Fail' AND ABS(a.app_price - m.unit_price) > 0.005)
   OR (COALESCE(a.listed, 0) = 0 AND m.clean_standard_status <> 'Fail'
       AND m.master_sku_id IN (SELECT master_sku_id FROM selling))
ORDER BY CASE WHEN m.clean_standard_status = 'Fail' THEN 0 WHEN a.listed = 1 THEN 1 ELSE 2 END, product""",
        check_sql="""
SELECT (SELECT COUNT(*) FROM app_catalog a WHERE a.listed = 1
          AND a.master_sku_id IN (SELECT master_sku_id FROM sku_master WHERE clean_standard_status = 'Fail'))
     + (SELECT COUNT(*) FROM app_catalog a WHERE a.listed = 1 AND EXISTS (
          SELECT 1 FROM sku_master m WHERE m.master_sku_id = a.master_sku_id AND m.clean_standard_status <> 'Fail'
            AND ROUND(m.unit_price, 2) <> ROUND(a.app_price, 2)))
     + (SELECT COUNT(*) FROM sku_master m WHERE m.clean_standard_status <> 'Fail'
          AND NOT EXISTS (SELECT 1 FROM app_catalog a WHERE a.master_sku_id = m.master_sku_id AND a.listed = 1)
          AND m.master_sku_id IN (SELECT master_sku_id FROM sales_daily WHERE
              julianday(sale_date) > julianday((SELECT MAX(sale_date) FROM sales_daily)) - 30))""",
        check_of="rows",
        summary=("{rows} items are out of sync between the POS and the app: {diff} with a different price, "
                 "{missing} selling in stores but missing from the app, and {fail} Fail items still listed. "
                 "The policy is one price in the stores and the app."),
        extras=lambda df: {"diff": int((df["issue"] == "App price differs").sum()),
                           "missing": int((df["issue"] == "Missing from the app").sum()),
                           "fail": int((df["issue"] == "Fail item still in the app").sum())},
        example="Which items are out of sync with the app?",
    ),
]


BY_ID = {t.id: t for t in TEMPLATES}


# --------------------------------------------------------------------------- matching
def _kw_hit(keyword: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?:s|es)?(?![a-z0-9])", text) is not None


def score(template: Template, question: str) -> int:
    """Keyword hits weighted by keyword length, so 'daily revenue' beats 'revenue'."""
    q = question.lower()
    return sum(len(k) for k in template.keywords if _kw_hit(k, q))


def match(question: str) -> tuple[Template | None, int]:
    best, best_score = None, 0
    for t in TEMPLATES:
        s = score(t, question)
        if s > best_score:
            best, best_score = t, s
    return best, best_score


PERIODS = [
    (r"\b(yesterday|today)\b", 1),
    (r"\b(two weeks|2 weeks|fortnight)\b", 14),
    (r"\b(week|weekly|7 days|seven days)\b", 7),
    (r"\b(quarter|90 days|three months|3 months)\b", 90),
    (r"\b(month|monthly|30 days|thirty days)\b", 30),
]


def detect_days(question: str, default: int | None) -> int | None:
    """Time window from the question ('this week' -> 7), else the template default."""
    if default is None:
        return None
    q = question.lower()
    m = re.search(r"\blast (\d{1,2}) days\b", q)
    if m:
        return max(1, min(90, int(m.group(1))))
    for pattern, days in PERIODS:
        if re.search(pattern, q):
            return days
    return default


def period_label(days: int | None) -> str:
    return {1: "last day", 7: "last 7 days", 14: "last 14 days", 30: "last 30 days", 90: "last 90 days"}.get(
        days, f"last {days} days")


def scope_label(scope: list[str]) -> str:
    if len(scope) == len(config.store_ids()) and len(scope) > 1:
        return f"all {len(scope)} stores"
    labels = [config.store_label(s) for s in scope]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


@dataclass
class Prepared:
    """A template filled in for one question and one store scope."""
    template: Template
    sql: str
    check_sql: str
    days: int | None
    context: dict = field(default_factory=dict)
    problem: str | None = None  # set when the template can't run for this scope


def prepare(template: Template, question: str, scope: list[str]) -> Prepared:
    days = detect_days(question, template.default_days)
    params = {"days": days if days is not None else 30}
    context = {"scope": scope_label(scope), "period": period_label(days)}
    problem = None
    if template.needs_comparison_store:
        flag = config.flagship()
        mentioned = [s for s in scope if s != flag]
        target = config.newest_store() if config.newest_store() in mentioned else (mentioned[-1] if mentioned else None)
        if flag not in scope or target is None:
            problem = "This comparison needs access to the Flagship and at least one other store."
            target = target or config.newest_store()
        params.update(flagship=flag, target=target)
        context["target_label"] = config.store_label(target)
    sql = template.sql.format(**params).strip() if ("{" in template.sql) else template.sql.strip()
    check = template.check_sql.format(**params).strip() if ("{" in template.check_sql) else template.check_sql.strip()
    return Prepared(template, sql, check, days, context, problem)


def summarize(prepared: Prepared, df: pd.DataFrame) -> str:
    t = prepared.template
    if df.empty:
        return f"No matching rows for {prepared.context['scope']}."
    numeric = df.select_dtypes("number")
    ctx = dict(prepared.context)
    ctx.update(rows=len(df), first=df.iloc[0].to_dict(), total={c: numeric[c].sum() for c in numeric.columns})
    if t.row_format:
        ctx["lines"] = "; ".join(t.row_format.format(**r) for r in df.head(6).to_dict("records"))
    if t.extras:
        ctx.update(t.extras(df))
    fmt = t.summary_one if (t.summary_one and len(df) == 1) else t.summary
    try:
        return fmt.format(**ctx)
    except (KeyError, ValueError, IndexError):
        return f"{t.title}: {len(df)} rows for {ctx['scope']}."
