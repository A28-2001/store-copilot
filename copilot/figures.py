"""The month's headline numbers, each worked out two ways.

Shared by the board pack (planning/build_board_pack.py) and the app's Story tab,
so the slides and the story can never disagree. figures() raises SystemExit if
any pair of routes differs by more than 0.5%.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from data.generate import DB_PATH, ensure_db, load_config

DAYS, PACE_DAYS, WASTE_TARGET, SLOW_DAYS = 30, 14, 0.03, 60


def money(x: float) -> str:
    return f"${x / 1e6:.2f}M" if abs(x) >= 1e6 else f"${x / 1e3:,.0f}K" if abs(x) >= 1e4 else f"${x:,.0f}"


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
        for label, lo, hi in (("now", last, f"'{end}'"), ("before", prior, last)):
            rows = con.execute(f"""SELECT st.name, COALESCE(SUM(s.revenue), 0) FROM stores st
                                   LEFT JOIN sales_daily s ON s.store_id = st.store_id
                                    AND s.sale_date > {lo} AND s.sale_date <= {hi}
                                   GROUP BY st.store_id ORDER BY st.store_id""").fetchall()
            f[f"stores_{label}"] = rows
            f.twice(f"stores_add_up_{label}", sum(v for _, v in rows), f[f"sales_{label}"])
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
