"""What the LLM is told about the database: tables, IDs, and business definitions.

Business definitions live here once, so "gross margin" or "days of cover" means
the same thing in every generated query.
"""
from __future__ import annotations

from copilot import config

TABLES = """
stores(store_id, name, neighborhood, open_date, size_factor, assortment_share)
sku_master(master_sku_id, product_name, category, brand, vendor_id, is_local_brand 0/1, perishable 0/1,
           shelf_life_days, unit_cost (NULL = missing), unit_price, clean_standard_status 'Pass'|'Review'|'Fail',
           flagged_ingredient (text, e.g. 'canola oil'; NULL for Pass), created_on, price_changed_on)
cost_changes(master_sku_id, vendor_id, effective_date, old_cost, new_cost)   -- vendor cost increases
app_catalog(master_sku_id, app_price, listed 0/1)   -- the membership app's product catalog
sku_map(store_id, local_sku, master_sku_id)            -- POS codes per store; master_sku_id NULL = unmapped code
sales_daily(sale_date 'YYYY-MM-DD', store_id, local_sku, master_sku_id, units, revenue, cogs)  -- one row per POS code per day
waste_daily(waste_date, store_id, master_sku_id, units, waste_cost, reason)   -- perishables only
inventory_current(as_of_date, store_id, master_sku_id, on_hand, on_order)     -- latest snapshot; no cafe items
transactions_daily(sale_date, store_id, transactions, member_transactions, revenue)
vendors(vendor_id, vendor_name, is_local 0/1, lead_time_days, min_order_usd, payment_terms)
category_targets(category, target_margin_pct, perishable 0/1)
sku_velocity_14d(store_id, master_sku_id, units_14d, avg_daily_units)   -- view: last 14 days of sales
""".strip()

DEFINITIONS = """
- gross margin % = 100 * SUM(revenue - cogs) / SUM(revenue)
- days of cover = on_hand / avg_daily_units, joining inventory_current to sku_velocity_14d on store_id and master_sku_id
- stock-out = on_hand = 0
- waste % = SUM(waste_cost) / SUM(cogs of perishable SKUs) for the same period, as a percent (target: under 3%)
- average basket = SUM(revenue) / SUM(transactions) from transactions_daily
- member share = SUM(member_transactions) / SUM(transactions)
- local brand share = revenue of SKUs with is_local_brand = 1 / total revenue
- "last week" / "last N days" are relative to (SELECT MAX(sale_date) FROM sales_daily), never date('now').
  Example: sale_date > date((SELECT MAX(sale_date) FROM sales_daily), '-7 days')
- A product can have several POS codes: always aggregate by master_sku_id, never by local_sku.
- Unmapped POS codes have master_sku_id NULL: they count in store revenue but not in product or category reports.
- Duplicate POS codes = a master_sku_id with more than one local_sku at the same store (in sku_map).
- "Running low" / "low stock" = under 5 days of cover. Suggested reorder = top up to (lead_time_days + 14)
  days of cover, minus on_hand and on_order.
- Banned ingredient = clean_standard_status 'Fail'. Review = under review, allowed on shelf up to 30 days.
- Margin floor: a SKU whose current margin, 100 * (unit_price - unit_cost) / unit_price, is more than 5 points
  under its category target after a cost increase (in cost_changes) needs a new price within 3 days.
- Price below cost = unit_price < unit_cost (likely a keying error).
- App out of sync = listed in app_catalog at a price different from sku_master.unit_price; or selling in stores
  but not listed (listed = 0 or no row); or a Fail item still listed.
- Default time windows when the question doesn't give one: last 30 days for revenue, margin, waste, local
  brand share, basket and member share; last 7 days for top-product rankings and for items still selling;
  inventory questions use the current snapshot; trends use every day available.
""".strip()


def schema_notes(scope: list[str]) -> str:
    stores = "\n".join(f"  {s['store_id']} = {s['name']} ({s['neighborhood']}, opened {s['open_date']})"
                       for s in config.stores() if s["store_id"] in scope)
    categories = ", ".join(c["name"] for c in config.load_config()["categories"])
    return (f"Tables:\n{TABLES}\n\nStores you can see:\n{stores}\n\nCategories: {categories}\n"
            f"Data ends on {config.load_config()['data_end_date']}.\n\nBusiness definitions:\n{DEFINITIONS}")
