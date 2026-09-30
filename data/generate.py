"""Build the synthetic Store Copilot database: data/store.db.

Everything in this database is synthetic. It models a multi-store clean-label
grocer: 3 stores, 2,000 SKUs, 90 days of sales, with a deliberately messy SKU
list (duplicate and unmapped POS codes) because that is a real-world pain point.

Run:  python data/generate.py            (seed 7, reproducible)
      python data/generate.py --seed 11
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog  # noqa: E402

CONFIG_PATH = ROOT / "config" / "stores.yaml"
DB_PATH = ROOT / "data" / "store.db"

N_SKUS = 2000
DOW_FACTOR = np.array([0.9, 0.85, 0.9, 0.95, 1.1, 1.35, 1.25])  # Mon..Sun
TREND_START, TREND_END = 0.95, 1.08      # chain-level growth over the window
RAMP_START, RAMP_DAYS = 0.55, 30         # a new store starts at 55% and ramps to 100%
VELOCITY_SCALE = 0.33                    # lognormal centred on base x 0.33 ...
VELOCITY_SIGMA = 0.9
VELOCITY_CAP = 4.0                       # ... capped at 4x the category base
N_DUPLICATE_S2 = 60                      # S2 products with a second POS code
DUPLICATE_SHARE = 0.30                   # share of their sales rung up on the duplicate
N_UNMAPPED = {"S2": 15, "S3": 12}        # POS codes with no master SKU (27 total)
CLEAN_FAIL, CLEAN_REVIEW = 60, 100       # 3% Fail, 5% Review of 2,000

# Margin drift from target by category, in points (gives "margin vs target" a story).
MARGIN_DRIFT = {
    "Produce": -2.5, "Meat & Seafood": -3.5, "Dairy & Eggs": -0.5, "Pantry": 0.5, "Snacks": 1.0,
    "Beverages & Juices": 0.5, "Cafe & Hot Bar": -1.5, "Supplements": 1.0, "Beauty & Body": 1.5,
}
WASTE_REASONS = {
    "Cafe & Hot Bar": ["hot bar 4-hour discard", "end-of-day discard"],
    "Produce": ["spoiled", "damaged"],
    "Meat & Seafood": ["past sell-by", "temperature"],
    "Dairy & Eggs": ["past sell-by", "damaged"],
    "Beverages & Juices": ["day-3 juice pull", "damaged"],
}

SCHEMA = """
CREATE TABLE stores (
    store_id TEXT PRIMARY KEY, name TEXT NOT NULL, neighborhood TEXT, open_date TEXT,
    size_factor REAL, assortment_share REAL);
CREATE TABLE vendors (
    vendor_id TEXT NOT NULL, vendor_name TEXT NOT NULL, is_local INTEGER, lead_time_days INTEGER,
    min_order_usd REAL, payment_terms TEXT);
CREATE TABLE category_targets (
    category TEXT PRIMARY KEY, target_margin_pct REAL, perishable INTEGER);
CREATE TABLE sku_master (
    master_sku_id TEXT NOT NULL, product_name TEXT NOT NULL, category TEXT NOT NULL, brand TEXT,
    vendor_id TEXT, is_local_brand INTEGER, perishable INTEGER, shelf_life_days INTEGER,
    unit_cost REAL, unit_price REAL, clean_standard_status TEXT, flagged_ingredient TEXT,
    created_on TEXT, price_changed_on TEXT);
CREATE TABLE cost_changes (
    master_sku_id TEXT NOT NULL, vendor_id TEXT, effective_date TEXT, old_cost REAL, new_cost REAL);
CREATE TABLE app_catalog (
    master_sku_id TEXT NOT NULL, app_price REAL, listed INTEGER);
CREATE TABLE sku_map (
    store_id TEXT NOT NULL, local_sku TEXT NOT NULL, master_sku_id TEXT);
CREATE TABLE sales_daily (
    sale_date TEXT NOT NULL, store_id TEXT NOT NULL, local_sku TEXT NOT NULL, master_sku_id TEXT,
    units INTEGER, revenue REAL, cogs REAL);
CREATE TABLE waste_daily (
    waste_date TEXT NOT NULL, store_id TEXT NOT NULL, master_sku_id TEXT NOT NULL,
    units INTEGER, waste_cost REAL, reason TEXT);
CREATE TABLE inventory_current (
    as_of_date TEXT, store_id TEXT NOT NULL, master_sku_id TEXT NOT NULL,
    on_hand INTEGER, on_order INTEGER);
CREATE TABLE transactions_daily (
    sale_date TEXT NOT NULL, store_id TEXT NOT NULL, transactions INTEGER,
    member_transactions INTEGER, revenue REAL);
"""

# Days of cover and reorder logic read this view. It always sums a SKU across
# all of its POS codes because it groups by master_sku_id.
VELOCITY_VIEW = """
CREATE VIEW sku_velocity_14d AS
SELECT store_id,
       master_sku_id,
       SUM(units) AS units_14d,
       SUM(units) / 14.0 AS avg_daily_units
FROM sales_daily
WHERE master_sku_id IS NOT NULL
  AND sale_date > date((SELECT MAX(sale_date) FROM sales_daily), '-14 days')
GROUP BY store_id, master_sku_id
"""

INDEXES = """
CREATE INDEX ix_sales_store_date ON sales_daily(store_id, sale_date);
CREATE INDEX ix_sales_sku ON sales_daily(master_sku_id);
CREATE UNIQUE INDEX ux_sku_master ON sku_master(master_sku_id);
CREATE UNIQUE INDEX ux_vendors ON vendors(vendor_id);
CREATE INDEX ix_waste_store_sku ON waste_daily(store_id, master_sku_id);
CREATE INDEX ix_waste_date ON waste_daily(waste_date);
CREATE INDEX ix_inventory_store_sku ON inventory_current(store_id, master_sku_id);
CREATE INDEX ix_sku_map_store_local ON sku_map(store_id, local_sku);
CREATE INDEX ix_sku_map_store_sku ON sku_map(store_id, master_sku_id);
CREATE INDEX ix_transactions_store_date ON transactions_daily(store_id, sale_date);
CREATE UNIQUE INDEX ux_app_catalog ON app_catalog(master_sku_id);
CREATE INDEX ix_cost_changes_sku ON cost_changes(master_sku_id);
"""


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def price_99(x: np.ndarray) -> np.ndarray:
    """Round a price to the nearest .99 (never below 0.99)."""
    return np.maximum(np.round(x) - 0.01, 0.99)


# --------------------------------------------------------------------------- catalog
def build_vendors(rng: np.random.Generator) -> pd.DataFrame:
    rows = [dict(vendor_id=v[0], vendor_name=v[1], is_local=v[2], lead_time_days=v[3],
                 min_order_usd=float(v[4]), payment_terms=v[5], focus=v[6]) for v in catalog.KEY_VENDORS]
    names = set()
    focus_cycle = ["Produce", "Meat & Seafood", "Dairy & Eggs", "Pantry", "Snacks",
                   "Beverages & Juices", "Supplements", "Beauty & Body"]
    for i in range(30):
        while True:
            name = f"{rng.choice(catalog.VENDOR_WORDS_A)} {rng.choice(catalog.VENDOR_WORDS_B)}"
            if name not in names:
                names.add(name)
                break
        rows.append(dict(
            vendor_id=f"V{6 + i:03d}", vendor_name=name, is_local=int(rng.random() < 0.3),
            lead_time_days=int(rng.integers(1, 11)), min_order_usd=float(rng.choice([200, 300, 400, 500, 750, 1000, 1500, 2000])),
            payment_terms=str(rng.choice(["Net 15", "Net 30", "Net 45"])), focus=focus_cycle[i % len(focus_cycle)]))
    return pd.DataFrame(rows)


def build_sku_master(cfg: dict, vendors: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    cats = cfg["categories"]
    counts = {c["name"]: int(round(c["share"] * N_SKUS)) for c in cats}
    counts["Pantry"] += N_SKUS - sum(counts.values())  # absorb rounding

    rows = []
    for c in cats:
        name, n = c["name"], counts[c["name"]]
        if name == "Cafe & Hot Bar":
            items = [(f"{prod}: {var}", "House Kitchen") for prod, vs in catalog.CAFE_ITEMS.items() for var in vs]
            chosen = items[:n]
        else:
            combos = [(f"{b} {p}, {s}", b) for b in catalog.CATEGORY_BRANDS[name]
                      for p, sizes in catalog.PRODUCTS[name] for s in sizes]
            idx = rng.choice(len(combos), size=n, replace=False)
            chosen = [combos[i] for i in idx]
        lo, hi = catalog.SHELF_LIFE_DAYS[name]
        for product_name, brand in chosen:
            price = float(price_99(np.exp(rng.uniform(np.log(c["price_min"]), np.log(c["price_max"])))))
            margin = np.clip(c["target_margin"] + MARGIN_DRIFT[name] + rng.normal(0, 3.5), 8, 85) / 100
            rows.append(dict(
                product_name=product_name, category=name, brand=brand,
                is_local_brand=catalog.BRANDS.get(brand, 0), perishable=int(c["perishable"]),
                shelf_life_days=int(rng.integers(lo, hi + 1)), unit_price=price,
                unit_cost=round(price * (1 - margin), 2)))
    df = pd.DataFrame(rows)
    df.insert(0, "master_sku_id", [f"M{i + 1:04d}" for i in range(len(df))])

    # Vendor assignment: key vendors carry a big share of their category.
    key_share = {"Produce": ("V001", 0.55), "Meat & Seafood": ("V002", 0.55), "Supplements": ("V003", 0.5),
                 "Pantry": ("V005", 0.4), "Snacks": ("V005", 0.4)}
    juice_words = ("Juice", "Shot", "Lemonade", "Tepache")
    vendor_ids = []
    for _, r in df.iterrows():
        cat = r["category"]
        others = vendors.loc[(vendors["focus"] == cat) & (vendors["vendor_id"] > "V005"), "vendor_id"].tolist()
        if cat == "Cafe & Hot Bar":
            vendor_ids.append("V000")
        elif cat == "Beverages & Juices" and any(w in r["product_name"] for w in juice_words) and rng.random() < 0.8:
            vendor_ids.append("V004")
        elif cat in key_share and rng.random() < key_share[cat][1]:
            vendor_ids.append(key_share[cat][0])
        else:
            vendor_ids.append(str(rng.choice(others)))
    df["vendor_id"] = vendor_ids

    # Clean Ingredient Standard: whole foods and the house kitchen always pass.
    df["clean_standard_status"] = "Pass"
    df["flagged_ingredient"] = None
    eligible = df.index[df["category"].isin(catalog.FLAGGED_INGREDIENTS)].to_numpy()
    flagged = rng.choice(eligible, size=CLEAN_FAIL + CLEAN_REVIEW, replace=False)
    df.loc[flagged[:CLEAN_FAIL], "clean_standard_status"] = "Fail"
    df.loc[flagged[CLEAN_FAIL:], "clean_standard_status"] = "Review"
    for i in flagged:
        df.at[i, "flagged_ingredient"] = str(rng.choice(catalog.FLAGGED_INGREDIENTS[df.at[i, "category"]]))

    cols = ["master_sku_id", "product_name", "category", "brand", "vendor_id", "is_local_brand", "perishable",
            "shelf_life_days", "unit_cost", "unit_price", "clean_standard_status", "flagged_ingredient"]
    return df[cols]


def draw_velocities(cfg: dict, skus: pd.DataFrame, dates: pd.DatetimeIndex, rng: np.random.Generator):
    """Units/day at the flagship: lognormal around base x 0.33, capped at 4x base,
    then scaled so the flagship's expected revenue matches the calibration target."""
    base = skus["category"].map({c["name"]: c["base_velocity"] for c in cfg["categories"]}).to_numpy()
    raw = base * VELOCITY_SCALE * rng.lognormal(0.0, VELOCITY_SIGMA, size=len(skus))
    price = skus["unit_price"].to_numpy()
    day_factor = DOW_FACTOR[dates.dayofweek] * np.linspace(TREND_START, TREND_END, len(dates))
    target = cfg["flagship_revenue_per_day"]
    k = 1.0
    for _ in range(6):  # a few fixed-point steps: the cap shifts expected revenue slightly
        vel = np.minimum(raw * k, base * VELOCITY_CAP)
        expected = (vel * price).sum() * day_factor.mean()
        k *= target / expected
    vel = np.minimum(raw * k, base * VELOCITY_CAP)
    return vel, k


# --------------------------------------------------------------------------- master data issues
N_NEW_ITEMS, N_MISSING_COST = 12, 8        # items set up in the last two weeks; some without a cost
N_COST_UP_BIG, N_COST_UP_SMALL = 16, 8     # vendor cost increases; the big ones break the margin floor
N_PRICE_TYPOS = 5                          # prices keyed at a tenth of the right value
N_APP_STALE, N_APP_HIDDEN, N_APP_FAIL = 15, 6, 6


def inject_master_data_issues(skus, vendors, sales, waste, dates, pulled, rng):
    """The kind of mess a senior analyst's weekly master data audit catches.

    Runs after the sales are generated, with its own random stream, so the rest of
    the data stays exactly as it was. Returns (skus, vendors, sales, waste, cost_changes, app_catalog).
    """
    end = dates[-1]
    day = lambda back: (end - pd.Timedelta(days=int(back))).strftime("%Y-%m-%d")  # noqa: E731
    skus = skus.copy()
    skus["created_on"] = "2026-03-02"
    skus["price_changed_on"] = None
    fail = set(skus.index[skus["clean_standard_status"] == "Fail"])
    in_house = set(skus.index[skus["category"] == "Cafe & Hot Bar"])
    shelf_stable = set(skus.index[skus["perishable"] == 0]) - fail
    taken = set(pulled) | fail | in_house

    def pick(pool, n):
        pool = sorted(set(pool) - taken)
        chosen = list(rng.choice(pool, size=n, replace=False))
        taken.update(chosen)
        return chosen

    # New items: set up in the last two weeks (no sales before), not yet in the app; most have no cost.
    new_items = pick(shelf_stable, N_NEW_ITEMS)
    for i in new_items:
        created = day(rng.integers(3, 15))
        skus.at[i, "created_on"] = created
        sid = skus.at[i, "master_sku_id"]
        sales = sales[~((sales["master_sku_id"] == sid) & (sales["sale_date"] < created))]
    for i in new_items[:N_MISSING_COST]:
        skus.at[i, "unit_cost"] = np.nan
        sales.loc[sales["master_sku_id"] == skus.at[i, "master_sku_id"], "cogs"] = np.nan

    # Vendor cost increases. The big ones push margin 6-11 points under the category target.
    targets = skus["category"].map(COST_TARGETS)
    margin = 100 * (1 - skus["unit_cost"] / skus["unit_price"])
    healthy = [i for i in skus.index if margin[i] >= targets[i] - 3 and skus.at[i, "unit_price"] >= 4]
    changes = []
    for n, i in enumerate(pick(healthy, N_COST_UP_BIG + N_COST_UP_SMALL)):
        old = float(skus.at[i, "unit_cost"])
        if n < N_COST_UP_BIG:
            new = round(float(skus.at[i, "unit_price"]) * (1 - (targets[i] - rng.uniform(6, 11)) / 100), 2)
        else:
            new = round(old * rng.uniform(1.02, 1.05), 2)
        effective = day(rng.integers(2, 25))
        sid = skus.at[i, "master_sku_id"]
        after = (sales["master_sku_id"] == sid) & (sales["sale_date"] >= effective)
        sales.loc[after, "cogs"] = (sales.loc[after, "units"] * new).round(2)
        w_after = (waste["master_sku_id"] == sid) & (waste["waste_date"] >= effective)
        waste.loc[w_after, "waste_cost"] = (waste.loc[w_after, "units"] * new).round(2)
        skus.at[i, "unit_cost"] = new
        changes.append((sid, skus.at[i, "vendor_id"], effective, old, new))
    cost_changes = pd.DataFrame(changes, columns=["master_sku_id", "vendor_id", "effective_date", "old_cost", "new_cost"])

    # Price typos: a price keyed at a tenth of its value a few days ago; the app still has the right one.
    right_price = {}
    for i in pick([i for i in skus.index if skus.at[i, "unit_price"] >= 15], N_PRICE_TYPOS):
        right_price[i] = float(skus.at[i, "unit_price"])
        wrong = float(price_99(np.array(right_price[i] / 10)))
        changed = day(rng.integers(1, 7))
        sid = skus.at[i, "master_sku_id"]
        after = (sales["master_sku_id"] == sid) & (sales["sale_date"] >= changed)
        sales.loc[after, "revenue"] = (sales.loc[after, "units"] * wrong).round(2)
        skus.at[i, "unit_price"] = wrong
        skus.at[i, "price_changed_on"] = changed

    # Vendor records with missing terms.
    vendors = vendors.copy()
    used = set(skus["vendor_id"])
    for n, v in enumerate(rng.choice(sorted(x for x in used if x > "V005"), size=3, replace=False)):
        vendors.loc[vendors["vendor_id"] == v, "payment_terms"] = None
        if n == 0:
            vendors.loc[vendors["vendor_id"] == v, "min_order_usd"] = np.nan

    # The app catalog: one price everywhere, except where it drifted.
    app = pd.DataFrame({"master_sku_id": skus["master_sku_id"], "app_price": skus["unit_price"], "listed": 1})
    app.loc[list(right_price), "app_price"] = list(right_price.values())
    stale = pick(skus.index, N_APP_STALE)
    app.loc[stale, "app_price"] = (skus.loc[stale, "unit_price"] + rng.choice([-1.0, 1.0], size=len(stale))).round(2)
    app.loc[sorted(fail), "listed"] = 0
    app.loc[list(rng.choice(sorted(fail), size=N_APP_FAIL, replace=False)), "listed"] = 1
    app.loc[pick(skus.index, N_APP_HIDDEN), "listed"] = 0
    app = app.drop(index=new_items)
    return skus, vendors, sales, waste, cost_changes, app


COST_TARGETS: dict = {}


# --------------------------------------------------------------------------- sales
def store_day_factors(store: dict, dates: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    open_date = pd.Timestamp(store["open_date"])
    days_open = (dates - open_date).days.to_numpy()
    ramp = np.where(days_open < 0, 0.0, RAMP_START + (1 - RAMP_START) * np.minimum(days_open, RAMP_DAYS) / RAMP_DAYS)
    trend = np.linspace(TREND_START, TREND_END, len(dates))
    noise = rng.lognormal(0.0, 0.06, size=len(dates))
    return store["size_factor"] * DOW_FACTOR[dates.dayofweek] * trend * ramp * noise


def choose_assortment(store: dict, vel: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Indices of SKUs this store carries. Smaller stores lean slightly toward better sellers."""
    n = len(vel)
    if store["assortment_share"] >= 1.0:
        return np.arange(n)
    k = int(round(store["assortment_share"] * n))
    w = vel ** 0.25 / (vel ** 0.25).sum()
    return np.sort(rng.choice(n, size=k, replace=False, p=w))


def generate(seed: int = 7, db_path: Path = DB_PATH, verbose: bool = True) -> dict:
    t0 = time.time()
    cfg = load_config()
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(cfg["data_end_date"])
    dates = pd.date_range(end=end, periods=cfg["history_days"], freq="D")
    date_str = dates.strftime("%Y-%m-%d").to_numpy()
    stores = cfg["stores"]
    waste_rate = cfg["waste_rates"]

    vendors = build_vendors(rng)
    skus = build_sku_master(cfg, vendors, rng)
    vel, k = draw_velocities(cfg, skus, dates, rng)
    price = skus["unit_price"].to_numpy()
    cost = skus["unit_cost"].to_numpy()
    cat = skus["category"].to_numpy()
    sku_ids = skus["master_sku_id"].to_numpy()

    # Half of the Fail SKUs were pulled from sale on some date; the rest are still selling.
    fail_idx = np.flatnonzero(skus["clean_standard_status"].to_numpy() == "Fail")
    pulled = rng.choice(fail_idx, size=len(fail_idx) // 2, replace=False)
    pull_day = dict(zip(pulled, rng.integers(20, len(dates) - 5, size=len(pulled))))

    sku_map_rows, sales_parts, waste_parts, inv_parts, tx_parts = [], [], [], [], []
    for store in stores:
        sid = store["store_id"]
        carried = choose_assortment(store, vel, rng)
        local_codes = {i: f"{sid}-{10000 + i + 1}" for i in carried}
        sku_map_rows += [(sid, local_codes[i], sku_ids[i]) for i in carried]

        factors = store_day_factors(store, dates, rng)                    # (days,)
        lam = vel[carried][:, None] * factors[None, :]                    # (skus, days)
        for j, i in enumerate(carried):
            if i in pull_day:
                lam[j, pull_day[i]:] = 0.0
        units = rng.poisson(lam)

        # Duplicate POS codes at S2: ~30% of those products' units ring up on a second code.
        dup_units = np.zeros_like(units)
        dup_codes = {}
        if sid == "S2":
            non_cafe = [j for j, i in enumerate(carried) if cat[i] != "Cafe & Hot Bar"]
            dup_rows = rng.choice(non_cafe, size=N_DUPLICATE_S2, replace=False)
            dup_units[dup_rows] = rng.binomial(units[dup_rows], DUPLICATE_SHARE)
            units = units - dup_units
            for n, j in enumerate(sorted(dup_rows)):
                dup_codes[j] = f"S2-DUP-{n + 1:03d}"
                sku_map_rows.append((sid, dup_codes[j], sku_ids[carried[j]]))

        for u_matrix, code_of in ((units, lambda j: local_codes[carried[j]]), (dup_units, lambda j: dup_codes[j])):
            r, d = np.nonzero(u_matrix)
            if len(r) == 0:
                continue
            idx = carried[r]
            u = u_matrix[r, d]
            sales_parts.append(pd.DataFrame({
                "sale_date": date_str[d], "store_id": sid, "local_sku": [code_of(j) for j in r],
                "master_sku_id": sku_ids[idx], "units": u,
                "revenue": np.round(u * price[idx], 2), "cogs": np.round(u * cost[idx], 2)}))

        # Unmapped POS codes: created at the register, never linked to the master list.
        n_new = N_UNMAPPED.get(sid, 0)
        first_open = max(0, (pd.Timestamp(store["open_date"]) - dates[0]).days)
        for n in range(n_new):
            code = f"{sid}-NEW-{n + 1:03d}"
            sku_map_rows.append((sid, code, None))
            start = int(rng.integers(max(first_open, len(dates) - 25), len(dates) - 2))
            u = rng.poisson(0.9, size=len(dates) - start)
            p = float(price_99(rng.uniform(6, 30)))
            days = np.flatnonzero(u) + start
            if len(days):
                uu = u[u > 0]
                sales_parts.append(pd.DataFrame({
                    "sale_date": date_str[days], "store_id": sid, "local_sku": code, "master_sku_id": None,
                    "units": uu, "revenue": np.round(uu * p, 2), "cogs": np.round(uu * p * 0.6, 2)}))

        # Waste: perishables only, a Poisson rate of expected sales.
        rates = np.array([waste_rate.get(cat[i], 0.0) for i in carried])
        w_units = rng.poisson(lam * rates[:, None])
        r, d = np.nonzero(w_units)
        idx = carried[r]
        waste_parts.append(pd.DataFrame({
            "waste_date": date_str[d], "store_id": sid, "master_sku_id": sku_ids[idx], "units": w_units[r, d],
            "waste_cost": np.round(w_units[r, d] * cost[idx], 2),
            "reason": [str(rng.choice(WASTE_REASONS[cat[i]])) for i in idx]}))

        # Inventory as of the end date, from the realised 14-day velocity.
        total_units = units + dup_units
        v14 = total_units[:, -14:].sum(axis=1) / 14.0
        on_hand = np.maximum(np.round(v14 * rng.gamma(2.2, 4.5, size=len(carried))),
                             rng.integers(3, 14, size=len(carried))).astype(int)
        on_hand[rng.random(len(carried)) < 0.03] = 0
        low = (on_hand == 0) | (on_hand < 5 * v14)
        on_order = np.where(low, np.ceil(10 * v14), 0).astype(int)
        # Pulled SKUs are off the shelf; cafe and hot bar items are made fresh daily, not stocked.
        keep = np.array([i not in pull_day and cat[i] != "Cafe & Hot Bar" for i in carried])
        inv_parts.append(pd.DataFrame({
            "as_of_date": date_str[-1], "store_id": sid, "master_sku_id": sku_ids[carried][keep],
            "on_hand": on_hand[keep], "on_order": on_order[keep]}))

        # Transactions: revenue / ~$46 basket; members grow from 8% to 22% (app launch).
        day_rev = (total_units * price[carried][:, None]).sum(axis=0)
        open_mask = day_rev > 0
        basket = cfg["average_basket"] * {"S1": 1.03, "S2": 0.98, "S3": 0.95}.get(sid, 1.0) \
            * rng.lognormal(0, 0.03, size=len(dates))
        tx = np.round(day_rev / basket).astype(int)
        member_share = np.linspace(0.08, 0.22, len(dates)) + rng.normal(0, 0.01, size=len(dates))
        tx_parts.append(pd.DataFrame({
            "sale_date": date_str[open_mask], "store_id": sid, "transactions": tx[open_mask],
            "member_transactions": rng.binomial(tx[open_mask], np.clip(member_share[open_mask], 0, 1)),
            "revenue": 0.0}))

    sales = pd.concat(sales_parts, ignore_index=True).sort_values(["sale_date", "store_id", "local_sku"])
    waste = pd.concat(waste_parts, ignore_index=True).sort_values(["waste_date", "store_id", "master_sku_id"])
    COST_TARGETS.update({c["name"]: c["target_margin"] for c in cfg["categories"]})
    skus, vendors, sales, waste, cost_changes, app_catalog = inject_master_data_issues(
        skus, vendors, sales, waste, dates, pulled, np.random.default_rng(seed + 1000))
    inventory = pd.concat(inv_parts, ignore_index=True)
    transactions = pd.concat(tx_parts, ignore_index=True)
    # Transaction revenue = register revenue for the day (includes unmapped codes).
    day_totals = sales.groupby(["sale_date", "store_id"])["revenue"].sum().round(2)
    transactions["revenue"] = [day_totals.get((d, s), 0.0) for d, s in zip(transactions["sale_date"], transactions["store_id"])]
    sku_map = pd.DataFrame(sku_map_rows, columns=["store_id", "local_sku", "master_sku_id"])

    stores_df = pd.DataFrame([{k2: s[k2] for k2 in ("store_id", "name", "neighborhood", "open_date",
                                                   "size_factor", "assortment_share")} for s in stores])
    stores_df["open_date"] = stores_df["open_date"].astype(str)
    targets = pd.DataFrame([{"category": c["name"], "target_margin_pct": float(c["target_margin"]),
                             "perishable": int(c["perishable"])} for c in cfg["categories"]])

    # ---- write
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    tables = {"stores": stores_df, "vendors": vendors.drop(columns="focus"), "category_targets": targets,
              "sku_master": skus, "sku_map": sku_map, "sales_daily": sales, "waste_daily": waste,
              "inventory_current": inventory, "transactions_daily": transactions,
              "cost_changes": cost_changes, "app_catalog": app_catalog}
    for name, df in tables.items():
        df.to_sql(name, con, if_exists="append", index=False)
    con.executescript(VELOCITY_VIEW + ";" + INDEXES + "ANALYZE;")
    con.commit()
    con.close()

    stats = summarize(db_path, cfg)
    stats.update(velocity_calibration=k, seconds=round(time.time() - t0, 1), seed=seed)
    if verbose:
        print_summary(stats)
    return stats


# --------------------------------------------------------------------------- report
def summarize(db_path: Path, cfg: dict) -> dict:
    con = sqlite3.connect(db_path)
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    per_store = q("""SELECT store_id, COUNT(DISTINCT sale_date), SUM(revenue)/COUNT(DISTINCT sale_date)
                     FROM sales_daily GROUP BY store_id ORDER BY store_id""")
    gm = q("SELECT 100.0*SUM(revenue-cogs)/SUM(revenue) FROM sales_daily")[0][0]
    basket = q("SELECT SUM(revenue)/SUM(transactions) FROM transactions_daily")[0][0]
    waste_pct = q("""SELECT 100.0*(SELECT SUM(waste_cost) FROM waste_daily) /
                     (SELECT SUM(s.cogs) FROM sales_daily s JOIN sku_master m USING (master_sku_id) WHERE m.perishable=1)""")[0][0]
    members = q("""SELECT MIN(sale_date), 100.0*SUM(member_transactions)/SUM(transactions) FROM transactions_daily
                   GROUP BY sale_date ORDER BY sale_date""")
    out = dict(
        skus=q("SELECT COUNT(*) FROM sku_master")[0][0],
        stores=q("SELECT COUNT(*) FROM stores")[0][0],
        sales_rows=q("SELECT COUNT(*) FROM sales_daily")[0][0],
        waste_rows=q("SELECT COUNT(*) FROM waste_daily")[0][0],
        inventory_rows=q("SELECT COUNT(*) FROM inventory_current")[0][0],
        vendors=q("SELECT COUNT(*) FROM vendors")[0][0],
        per_store={s: dict(days=d, revenue_per_day=round(r)) for s, d, r in per_store},
        gross_margin_pct=round(gm, 1), avg_basket=round(basket, 2), waste_pct=round(waste_pct, 1),
        member_share_first=round(members[0][1], 1), member_share_last=round(members[-1][1], 1),
        duplicate_codes=q("SELECT COUNT(*) FROM sku_map WHERE local_sku LIKE '%-DUP-%'")[0][0],
        unmapped_codes=q("SELECT COUNT(*) FROM sku_map WHERE master_sku_id IS NULL")[0][0],
        stockouts=q("SELECT COUNT(*) FROM inventory_current WHERE on_hand = 0")[0][0],
        clean=dict(q("SELECT clean_standard_status, COUNT(*) FROM sku_master GROUP BY 1")),
        db_mb=round(Path(db_path).stat().st_size / 1e6, 1),
    )
    con.close()
    return out


def print_summary(s: dict) -> None:
    print(f"Built {DB_PATH.relative_to(ROOT)} in {s['seconds']}s (seed {s['seed']}, {s['db_mb']} MB). All data is synthetic.")
    print(f"  {s['skus']:,} SKUs, {s['stores']} stores, {s['vendors']} vendors")
    print(f"  {s['sales_rows']:,} sales rows, {s['waste_rows']:,} waste rows, {s['inventory_rows']:,} inventory rows")
    for sid, v in s["per_store"].items():
        print(f"  {sid}: {v['days']} days of history, ${v['revenue_per_day']:,}/day")
    print(f"  gross margin {s['gross_margin_pct']}%, avg basket ${s['avg_basket']}, waste {s['waste_pct']}% of perishable COGS")
    print(f"  member share {s['member_share_first']}% -> {s['member_share_last']}%")
    print(f"  SKU mess: {s['duplicate_codes']} duplicate codes, {s['unmapped_codes']} unmapped codes; {s['stockouts']} stock-outs")
    print(f"  clean standard: {s['clean']}; velocity calibration x{s['velocity_calibration']:.3f}")


_BUILD_LOCK = threading.Lock()


@contextmanager
def _file_lock(path: Path):
    """Cross-process lock (POSIX). On a hosted app, two first visits can arrive at once."""
    try:
        import fcntl
    except ImportError:  # Windows: the thread lock alone still covers a single process
        yield
        return
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


EXPECTED_TABLES = set(re.findall(r"CREATE TABLE (\w+)", SCHEMA)) | {"sku_velocity_14d"}


def _is_complete(db_path: Path) -> bool:
    """True if the file has every table and view and some sales (a half-built file does not)."""
    if not db_path.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
            return EXPECTED_TABLES <= names and con.execute("SELECT COUNT(*) FROM sales_daily").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


def ensure_db(db_path: Path = DB_PATH) -> Path:
    """Build the database on first run (it is git-ignored).

    Only one build runs at a time, and it writes to a temporary file that is swapped
    into place in one step, so no session ever opens a half-built database.
    """
    db_path = Path(db_path)
    if _is_complete(db_path):
        return db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _BUILD_LOCK, _file_lock(db_path.with_name(db_path.name + ".lock")):
        if not _is_complete(db_path):  # someone else may have finished while we waited
            tmp = db_path.with_name(f".{db_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                generate(db_path=tmp, verbose=False)
                os.replace(tmp, db_path)
            finally:
                tmp.unlink(missing_ok=True)
    return db_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=load_config().get("seed", 7))
    ap.add_argument("--out", type=Path, default=DB_PATH)
    args = ap.parse_args()
    generate(seed=args.seed, db_path=args.out)
