"""Dashboard data: every tile and table on the dashboards runs through the same
guarded, store-scoped database and the same verifier as the chat. No side door."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from copilot import templates
from copilot.db import QueryError, StoreDB
from copilot.verifier import Verification, verify_template


@dataclass
class Panel:
    template_id: str
    question: str
    df: pd.DataFrame
    verification: Verification | None
    summary: str
    error: str | None = None


def run_panel(template_id: str, scope: list[str], question: str | None = None) -> Panel:
    t = templates.BY_ID[template_id]
    question = question or t.example
    prepared = templates.prepare(t, question, scope)
    if prepared.problem:
        return Panel(template_id, question, pd.DataFrame(), None, prepared.problem, prepared.problem)
    db = StoreDB(scope)
    try:
        result = db.run(prepared.sql)
    except QueryError as err:
        return Panel(template_id, question, pd.DataFrame(), None, str(err), str(err))
    verification = verify_template(db, prepared.check_sql, t.check_of, result.df, result.truncated)
    return Panel(template_id, question, result.df, verification, templates.summarize(prepared, result.df))


def overview(scope: list[str]) -> dict[str, Panel]:
    ids = ["revenue_by_store", "basket_members", "low_cover", "waste", "local_brands", "margin_vs_target",
           "daily_trend", "clean_standard", "sku_hygiene", "reorder_by_vendor", "master_data_audit",
           "price_exceptions", "app_sync"]
    return {i: run_panel(i, scope) for i in ids}


def kpis(p: dict[str, Panel]) -> list[dict]:
    """Six headline numbers, each carrying the verification status of the query behind it."""
    rev, basket, cover, waste, local = (p[k] for k in
                                        ("revenue_by_store", "basket_members", "low_cover", "waste", "local_brands"))
    out = []
    if not rev.df.empty:
        total = rev.df["revenue"].sum()
        gm = (rev.df["revenue"] * rev.df["gross_margin_pct"]).sum() / total
        out.append(dict(label="Revenue, last 30 days", value=_money(total), sub=f"{len(rev.df)} store(s)",
                        status=rev.verification.status, tone="neutral"))
        out.append(dict(label="Gross margin", value=f"{gm:.1f}%", sub="blended across categories",
                        status=rev.verification.status, tone="neutral"))
    if not basket.df.empty:
        tx = basket.df["transactions"].sum()
        b = (basket.df["avg_basket"] * basket.df["transactions"]).sum() / tx
        m = (basket.df["member_share_pct"] * basket.df["transactions"]).sum() / tx
        out.append(dict(label="Average basket", value=f"${b:,.2f}", sub=f"members {m:.1f}% of transactions",
                        status=basket.verification.status, tone="neutral"))
    if cover.verification is not None:
        zero = int((cover.df["on_hand"] == 0).sum()) if not cover.df.empty else 0
        out.append(dict(label="SKUs under 5 days of cover", value=f"{len(cover.df):,}", sub=f"{zero} already at zero",
                        status=cover.verification.status, tone="warn" if len(cover.df) else "good"))
    if not waste.df.empty:
        w = 100 * waste.df["waste_cost"].sum() / waste.df["perishable_cogs"].sum()
        out.append(dict(label="Waste, % of perishable cost", value=f"{w:.1f}%", sub="target: under 3%",
                        status=waste.verification.status, tone="warn" if w >= 3 else "good"))
    if not local.df.empty:
        s = 100 * local.df["local_revenue"].sum() / local.df["product_revenue"].sum()
        out.append(dict(label="Local brand share", value=f"{s:.1f}%", sub="of product revenue",
                        status=local.verification.status, tone="neutral"))
    return out


def brief(p: dict[str, Panel]) -> list[dict]:
    """What needs attention today, master data first. Each finding comes from a verified query."""
    items = []
    prices = p["price_exceptions"].df
    if not prices.empty:
        below = int((prices["issue"] == "Price below cost").sum())
        items.append(dict(kind="Pricing", headline=f"{len(prices)} prices need a decision",
                          detail=f"{below} are below cost, {len(prices) - below} lost margin after a vendor cost increase.",
                          question="Which prices fell under the margin floor?"))
    app = p["app_sync"].df
    if not app.empty:
        items.append(dict(kind="App catalog", headline=f"{len(app)} items out of sync with the app",
                          detail="Different prices, missing listings, and Fail items still listed.",
                          question="Which items are out of sync with the app?"))
    hygiene = p["sku_hygiene"].df
    if not hygiene.empty and hygiene["codes_to_fix"].sum():
        items.append(dict(kind="POS codes", headline=f"{int(hygiene['codes_to_fix'].sum())} POS codes need fixing",
                          detail=f"${hygiene['unmapped_sales_30d'].sum():,.0f} of sales in 30 days can't be tied to a product.",
                          question="How messy is our SKU list? Any unmapped or duplicate POS codes?"))
    clean = p["clean_standard"].df
    if not clean.empty and (clean["status"] == "Fail").any():
        items.append(dict(kind="Clean standard", headline=f"{int((clean['status'] == 'Fail').sum())} Fail items still selling",
                          detail="The standard says they come off the shelf within 48 hours.",
                          question="Which Fail or Review items are still selling?"))
    waste = p["waste"].df
    if not waste.empty:
        w = 100 * waste["waste_cost"].sum() / waste["perishable_cogs"].sum()
        if w >= 3:
            items.append(dict(kind="Waste", headline=f"Waste is {w:.1f}% of perishable cost",
                              detail=f"Target is under 3%. That's ${waste['waste_cost'].sum():,.0f} in 30 days.",
                              question="What is our waste as a percent of perishable cost by store?"))
    reorder = p["reorder_by_vendor"].df
    if not reorder.empty:
        items.append(dict(kind="Ordering", headline=f"{len(reorder)} vendor orders to place",
                          detail=f"{int((reorder['minimum_check'] == 'below minimum').sum())} are under the vendor's minimum.",
                          question="Which vendors should we order from, and what are their payment terms?"))
    return items


def _money(x: float) -> str:
    return f"${x / 1e6:,.2f}M" if x >= 1e6 else f"${x / 1e3:,.1f}K" if x >= 1e4 else f"${x:,.0f}"
