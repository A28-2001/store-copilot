"""Store Copilot: plain-English questions about a multi-store grocer's numbers and policies,
answered with checked numbers and cited sources. All data is synthetic.

Run locally:  pip install -r requirements.txt && streamlit run streamlit_app.py
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
PLAN_BOOK = ROOT / "planning" / "store_planning_model.xlsx"
BOARD_PACK = ROOT / "planning" / "board_pack.pdf"
# Fingerprint of the code, docs and config. A redeploy can update the files while the server process
# keeps running with the old modules in memory, so when the fingerprint changes we drop our own
# modules and import them fresh, and every cache key includes it too.
CODE_VERSION = hashlib.sha256(b"".join(
    f.read_bytes() for f in sorted([*ROOT.glob("copilot/*.py"), *ROOT.glob("data/*.py"), *ROOT.glob("docs/**/*.md"),
                                    ROOT / "config" / "stores.yaml"]))).hexdigest()[:12]
if "copilot" in sys.modules and getattr(sys.modules["copilot"], "__code_version__", None) != CODE_VERSION:
    for _name in [m for m in sys.modules if m.split(".")[0] in ("copilot", "data", "catalog")]:
        del sys.modules[_name]

import copilot as copilot_pkg  # noqa: E402
from copilot import charts, config, dashboard, templates  # noqa: E402
from copilot.db import QueryError, QueryRejected, StoreDB, guard_sql  # noqa: E402
from copilot.engine import STEP_LABELS, Answer, Copilot  # noqa: E402
from copilot.llm import PRIMARY_MODEL, check_key, make_llm, resolve_key  # noqa: E402
from data.generate import ensure_db  # noqa: E402

copilot_pkg.__code_version__ = CODE_VERSION
st.set_page_config(page_title="Store Copilot", page_icon="🌿", layout="wide", initial_sidebar_state="auto")
ensure_db()

TABS = ["Overview", "Ask the Copilot", "Master data", "How it works"]
YARDSTICK_URL = "https://a28-2001.github.io/yardstick/"

# Six to start with; the rest of the built-in questions sit behind "More questions".
SUGGESTED = {
    "Master data audit": "What needs fixing in master data?",
    "Prices under the margin floor": "Which prices fell under the margin floor?",
    "Out of sync with the app": "Which items are out of sync with the app?",
    "Fail items still selling": "Which Fail or Review items are still selling?",
    "What to order + payment terms": "Which vendors should we order from, and what are their payment terms?",
    "Item setup rules": "What fields must be set before a new item goes live?",
}
MORE_QUESTIONS = {
    "Under 5 days of cover": "Which SKUs have less than 5 days of cover?",
    "Stock-outs": "How many items are out of stock, and how many are already on order?",
    "Revenue by store": "What was revenue and margin by store over the last 30 days?",
    "Margin vs target": "Which categories are below their margin target?",
    "Top 20 by margin": "What are our top 20 products by gross margin this week?",
    "Waste vs 3% target": "What is our waste as a percent of perishable cost by store?",
    "POS codes": "How messy is our SKU list? Any unmapped or duplicate POS codes?",
    "Local brand share": "What share of revenue comes from local brands?",
    "Basket and members": "What's the average basket and member share by store?",
    "Store 3 assortment gap": "Which flagship top sellers aren't carried at Store 3?",
    "Daily trend": "Show me the daily revenue trend by store.",
    "How did Store 3 do?": "How did store 3 do last week?",
    "Palmetto minimum order": "What's the minimum order for Palmetto Ranch?",
    "Hot bar discard rule": "How long can hot bar food sit out before we discard it?",
    "Damaged produce credit": "What credit do we get from Biscayne for damaged produce?",
    "Unsold juice": "What happens to unsold Seagrape juice?",
}

CSS = """
<style>
.block-container {max-width: 1180px; padding-top: 1.4rem; padding-bottom: 4rem;}
h1, h2, h3 {letter-spacing: -0.01em;}
.hero {background: radial-gradient(120% 140% at 100% 0%, #2F4F3C 0%, #1F3A2D 55%, #1A3126 100%);
       color: #F3EEE4; border-radius: 18px; padding: 30px 34px 26px; margin-bottom: 18px; position: relative;
       overflow: hidden;}
.hero:after {content: ""; position: absolute; right: -60px; top: -60px; width: 260px; height: 260px;
       border-radius: 50%; border: 1px solid rgba(243,238,228,.12);}
.hero .eyebrow {font-size: .72rem; letter-spacing: .14em; text-transform: uppercase; color: #B8CBB3; font-weight: 600;}
.hero h1 {font-family: Fraunces, Georgia, serif; font-weight: 500; font-size: 2.15rem; line-height: 1.15;
       color: #F7F2E9; margin: .45rem 0 .6rem; padding: 0;}
.hero p {color: #D9D3C6; max-width: 720px; font-size: .98rem; line-height: 1.55; margin: 0;}
.hero .chips {margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px;}
.hero .chips span {border: 1px solid rgba(243,238,228,.22); border-radius: 999px; padding: 4px 12px;
       font-size: .78rem; color: #EDE7DB; background: rgba(255,255,255,.04);}
.hero .chips b {color: #FFFFFF; font-weight: 600;}
.kpi {background: #FFFFFF; border: 1px solid #E6DFD3; border-radius: 12px; padding: 16px 18px 14px; height: 100%;}
.kpi-label {color: #6E6A62; font-size: .8rem; font-weight: 500;}
.kpi-value {font-size: 1.85rem; font-weight: 600; color: #2B2A26; line-height: 1.25; margin-top: 2px;
       font-variant-numeric: tabular-nums;}
.kpi-sub {font-size: .78rem; color: #6E6A62; margin-top: 2px; display: flex; gap: 8px; align-items: center;
       flex-wrap: wrap;}
.dot {display: inline-block; width: 8px; height: 8px; border-radius: 50%;}
.pill {display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px; border-radius: 999px;
       font-size: .74rem; font-weight: 600; line-height: 1.6; white-space: nowrap;}
.pill.numbers {background: #E3EDE4; color: #24452F;} .pill.documents {background: #E3E8F4; color: #2A3C66;}
.pill.both {background: #F0E6D6; color: #5A4020;} .pill.refuse {background: #F4E2DA; color: #7A3A22;}
.pill.verified {background: #DCEBDD; color: #1F4D2B;} .pill.disagree {background: #F7E3D6; color: #86391A;}
.pill.unverified {background: #ECE8E1; color: #57534B;} .pill.warn {background: #F7E3D6; color: #86391A;}
.pill.muted {background: #F1ECE3; color: #6E6A62; font-weight: 500;}
.meta {color: #8A857B; font-size: .76rem;}
.q {font-family: Fraunces, Georgia, serif; font-size: 1.12rem; color: #2B2A26; margin: 2px 0 8px;}
.answer {font-size: 1.0rem; line-height: 1.62; color: #2B2A26; margin: 10px 0 4px;}
.answer p {margin: 0 0 .6rem;}
.answer sup {color: #4E6A4A; font-weight: 600; font-size: .7rem;}
.note {background: #FBF0E3; border-left: 3px solid #C4704F; border-radius: 8px; padding: 9px 13px;
       color: #5C4424; font-size: .86rem; margin: 8px 0;}
.src {font-size: .82rem; color: #57534B; margin: 2px 0;}
.src b {color: #2B2A26; font-weight: 600;}
.brief-kind {font-size: .7rem; letter-spacing: .1em; text-transform: uppercase; color: #8A6A42; font-weight: 600;}
.brief-head {font-family: Fraunces, Georgia, serif; font-size: 1.15rem; color: #2B2A26; margin: 2px 0 2px;}
.brief-detail {font-size: .86rem; color: #6E6A62; margin-bottom: 4px;}
.section-sub {color: #6E6A62; font-size: .92rem; margin: -6px 0 10px;}
.card-title {font-weight: 600; font-size: .95rem; margin-bottom: 0;}
.card-sub {color: #8A857B; font-size: .78rem;}
.layer {border-left: 2px solid #D9D1C3; padding: 2px 0 2px 12px; margin: 8px 0;}
.layer b {font-weight: 600;}
.side-title {font-family: Fraunces, Georgia, serif; font-size: 1.45rem; color: #1F3A2D; font-weight: 500;}
.side-sub {color: #6E6A62; font-size: .85rem; margin-bottom: 6px;}
.disclaimer {font-size: .76rem; color: #7A756B; line-height: 1.5;}
[data-testid="stVerticalBlockBorderWrapper"] {background: #FFFFFF;}
[data-testid="stExpander"] details {background: #FFFFFF;}
div[data-testid="stTabs"] button p {font-size: .95rem;}
@media (max-width: 640px) {
  .hero {padding: 22px 20px;} .hero h1 {font-size: 1.6rem;} .kpi-value {font-size: 1.5rem;}
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- helpers
def esc(text) -> str:
    """HTML-escape, and escape $ so Streamlit never reads money as LaTeX."""
    return html.escape(str(text)).replace("$", "&#36;")


def answer_html(text: str) -> str:
    paras = [esc(p).replace("\n", "<br>") for p in text.split("\n\n") if p.strip()]
    body = "".join(f"<p>{p}</p>" for p in paras)
    return re.sub(r"\[(\d)\]", r"<sup>[\1]</sup>", body)


ROUTE_PILL = {"numbers": ("numbers", "Numbers · SQL"), "documents": ("documents", "Documents · cited"),
              "both": ("both", "Numbers + documents"), "refuse": ("refuse", "Declined")}
VERIFY_PILL = {"verified": ("verified", "✓ Verified"), "disagree": ("disagree", "⚠ Checks disagree"),
               "unverified": ("unverified", "○ Not verified")}


def pill(kind: str, label: str) -> str:
    return f"<span class='pill {kind}'>{esc(label)}</span>"


def verify_pill(status: str | None) -> str:
    return pill(*VERIFY_PILL[status]) if status in VERIFY_PILL else ""


def money_config(df: pd.DataFrame) -> dict:
    cfg = {}
    for c in df.columns:
        lc = c.lower()
        if lc.endswith("pct") or lc.endswith("_pts"):
            cfg[c] = st.column_config.NumberColumn(c.replace("_", " "), format="%.1f")
        elif df[c].dtype.kind == "f" and charts._money_col(c):
            cfg[c] = st.column_config.NumberColumn(c.replace("_", " "), format="dollar")
        else:
            cfg[c] = st.column_config.Column(c.replace("_", " "))
    return cfg


def show_table(df: pd.DataFrame, height: int | None = None) -> None:
    st.dataframe(df, hide_index=True, column_config=money_config(df), height=height or "auto")


def key_fingerprint(key: str | None) -> str | None:
    return hashlib.sha256(key.encode()).hexdigest()[:12] if key else None


@st.cache_resource(show_spinner=False)
def get_copilot(allowed: tuple[str, ...], key_fp: str | None, _key: str | None, version: str = CODE_VERSION) -> Copilot:
    """One Copilot per role and key. The key itself is not part of the cache key, only its hash."""
    return Copilot(list(allowed), llm=make_llm(_key) if _key else None)


@st.cache_data(show_spinner=False, ttl=3600)
def key_status(key_fp: str, _key: str) -> tuple[bool, str]:
    return check_key(_key)


@st.cache_data(show_spinner=False)
def load_overview(scope: tuple[str, ...], version: str = CODE_VERSION) -> dict:
    return dashboard.overview(list(scope))


@st.cache_data(show_spinner=False)
def load_detail(scope: tuple[str, ...], sql: str, version: str = CODE_VERSION) -> pd.DataFrame:
    return StoreDB(list(scope)).run(sql).df


def go_ask(question: str) -> None:
    st.session_state.pending = (question, "button")
    st.session_state.nav = TABS[1]


def pick_suggestion(widget_key: str) -> None:
    value = st.session_state.get(widget_key)
    if value:
        st.session_state.pending = (value, "button")
        st.session_state[widget_key] = None


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("<div class='side-title'>Store Copilot</div>"
                "<div class='side-sub'>A checked answer, with its source.</div>", unsafe_allow_html=True)
    role = st.radio("Who's asking?", ["Owner", "Store manager"], key="role",
                    captions=["Sees every store", "Sees one store only"])
    if role == "Owner":
        allowed = config.store_ids()
        view = st.multiselect("Stores in view", allowed, default=allowed, format_func=config.store_label,
                              key="view") or allowed
    else:
        mine = st.selectbox("Your store", config.store_ids(), index=1, format_func=config.store_label, key="mine")
        allowed = view = [mine]
        st.caption("Ask about another store and the Copilot will tell you it's outside your access.")

    st.divider()
    with st.expander("AI settings"):
        typed_key = st.text_input("Groq API key (optional)", type="password", key="groq_key",
                                  help="Free at console.groq.com. Kept in this browser session only, never stored.")
        key = resolve_key(typed_key)
        key_ok, key_msg = key_status(key_fingerprint(key), key) if key else (False, "")
        if key and not key_ok:
            where = "" if (typed_key or "").strip() else " (the key found in secrets or the environment)"
            st.caption(f"⚠️ {key_msg[:-1]}{where}. Running in demo mode.")
            key = None
        use_llm = st.toggle("Ask anything (LLM mode)", value=bool(key), disabled=not key, key="use_llm",
                            help="Without a working key the Copilot runs in demo mode: a fixed library of verified "
                                 "questions.")
    llm_on = bool(key and use_llm)
    if llm_on:
        st.markdown(pill("verified", f"LLM mode · {PRIMARY_MODEL.split('/')[-1]} on Groq"), unsafe_allow_html=True)
    else:
        st.markdown(pill("muted", "Demo mode · no key needed"), unsafe_allow_html=True)

    if PLAN_BOOK.exists():
        st.divider()
        st.markdown("<div class='side-sub'><b>Planning model (Excel)</b><br>New store payback, a 13-week cash "
                    "forecast, inventory and pricing tests.</div>", unsafe_allow_html=True)
        st.download_button("Download the model", PLAN_BOOK.read_bytes(), file_name=PLAN_BOOK.name, key="plan_book",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if BOARD_PACK.exists():
        st.markdown("<div class='side-sub'><b>Board pack (PDF)</b><br>Three slides, built from the data: the month, "
                    "margin leaks in dollars, stock and waste.</div>", unsafe_allow_html=True)
        st.download_button("Download the board pack", BOARD_PACK.read_bytes(), file_name=BOARD_PACK.name,
                           key="board_pack", mime="application/pdf")

    st.divider()
    st.markdown("<div class='disclaimer'><b>All data is made up.</b> A portfolio project, not affiliated with "
                "any company.</div>", unsafe_allow_html=True)

copilot = get_copilot(tuple(allowed), key_fingerprint(key) if llm_on else None, key if llm_on else None, CODE_VERSION)
history_key = f"history::{','.join(allowed)}::{'llm' if llm_on else 'demo'}"
st.session_state.setdefault(history_key, [])
st.session_state.setdefault("nav", TABS[0])

# --------------------------------------------------------------------------- hero
mode_label = "LLM mode" if llm_on else "Demo mode"
st.markdown(f"""
<div class="hero">
  <div class="eyebrow">Store Copilot &nbsp;·&nbsp; synthetic data</div>
  <h1>Ask your stores anything.<br>Get a checked answer.</h1>
  <p>Every number is checked by a second query before it's called verified. Every policy answer is quoted
  from its source.</p>
  <div class="chips"><span>Asking as <b>{esc(role)}</b></span><span>Viewing <b>{esc(templates.scope_label(view))}</b></span>
  <span><b>{mode_label}</b></span></div>
</div>""", unsafe_allow_html=True)

tabs = st.tabs(TABS, key="nav", on_change="rerun")

# --------------------------------------------------------------------------- overview
with tabs[0]:
    panels = load_overview(tuple(view), CODE_VERSION)
    cards = dashboard.kpis(panels)
    for row in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, k in zip(cols, cards[row:row + 3]):
            flag = pill("warn", "needs attention") if k["tone"] == "warn" else ""
            col.markdown(f"""<div class="kpi"><div class="kpi-label">{esc(k['label'])}</div>
                <div class="kpi-value">{esc(k['value'])}</div>
                <div class="kpi-sub">{verify_pill(k['status'])}{flag}<span>{esc(k['sub'])}</span></div></div>""",
                         unsafe_allow_html=True)
        st.write("")
    young = config.low_history_stores(view)
    for s in young:
        st.markdown(f"<div class='note'>{esc(config.store_label(s))} opened {config.days_of_history(s)} days ago, "
                    f"so its trends are low confidence until day {config.load_config()['low_history_days']}.</div>",
                    unsafe_allow_html=True)

    items = dashboard.brief(panels)[:4]
    if items:
        st.subheader("What needs attention")
        st.markdown("<div class='section-sub'>Each one opens the full answer, with the data and the checks behind "
                    "it.</div>", unsafe_allow_html=True)
        for row in range(0, len(items), 2):
            cols = st.columns(2)
            for i, (col, it) in enumerate(zip(cols, items[row:row + 2])):
                with col.container(border=True):
                    st.markdown(f"<div class='brief-kind'>{esc(it['kind'])}</div>"
                                f"<div class='brief-head'>{esc(it['headline'])}</div>"
                                f"<div class='brief-detail'>{esc(it['detail'])}</div>", unsafe_allow_html=True)
                    st.button("Ask the Copilot →", key=f"brief_{row + i}", on_click=go_ask, args=(it["question"],))

    st.subheader("How the stores are trending")
    trend = panels["daily_trend"]
    with st.container(border=True):
        st.markdown(f"<div class='card-title'>Daily revenue by store</div><div class='card-sub'>7-day average, last 90 "
                    f"days · {verify_pill(trend.verification.status if trend.verification else None)}</div>",
                    unsafe_allow_html=True)
        if not trend.df.empty:
            bands = {config.store_label(s): str(config.store_by_id(s)["open_date"]) for s in young}
            st.plotly_chart(charts.revenue_trend(trend.df, bands), theme=None, config={"displayModeBar": False})
    margin = panels["margin_vs_target"]
    with st.expander("Margin vs target by category"):
        st.markdown(f"<div class='card-sub'>Points above or below target, last 30 days · "
                    f"{verify_pill(margin.verification.status if margin.verification else None)}</div>",
                    unsafe_allow_html=True)
        if not margin.df.empty:
            st.plotly_chart(charts.margin_gap(margin.df), theme=None, config={"displayModeBar": False})


# --------------------------------------------------------------------------- ask
def render_answer(a: Answer) -> None:
    with st.container(border=True):
        st.markdown(f"<div class='q'>{esc(a.question)}</div>", unsafe_allow_html=True)
        status = a.verification.status if a.verification else None
        meta = f"{templates.scope_label(a.scope)} · " if a.scope else ""
        meta += f"{'LLM' if a.mode == 'llm' else 'demo'} mode · {a.latency_ms / 1000:.1f}s"
        st.markdown(f"<div style='display:flex;gap:6px;flex-wrap:wrap;align-items:center'>{pill(*ROUTE_PILL[a.route])}"
                    f"{verify_pill(status)}<span class='meta'>{esc(meta)}</span></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='answer'>{answer_html(a.text)}</div>", unsafe_allow_html=True)
        for n in a.notes:
            st.markdown(f"<div class='note'>{esc(n)}</div>", unsafe_allow_html=True)
        if a.verification and status == "disagree":
            st.markdown(f"<div class='note'>{esc(a.verification.message)}</div>", unsafe_allow_html=True)
        fig = charts.from_spec(a.data, a.chart) if a.data is not None else None
        if fig is not None:
            st.plotly_chart(fig, theme=None, config={"displayModeBar": False}, key=f"chart_{id(a)}")
        if a.sources and a.route in ("documents", "both"):
            st.markdown("".join(f"<div class='src'><b>[{i}]</b> {esc(s['citation'])}</div>"
                                for i, s in enumerate(a.sources[:1], 1)), unsafe_allow_html=True)

        if a.data is not None:
            label = f"Data · {len(a.data)} rows" + (" (capped at 500)" if a.truncated else "")
            with st.expander(label):
                show_table(a.data, height=min(420, 38 + 35 * len(a.data)))
        if a.sql or a.verification:
            label = "How it was checked"
            if a.verification:
                passed = sum(1 for c in a.verification.checks if c.passed)
                label += f" · {passed} of {len(a.verification.checks)} checks passed"
            with st.expander(label):
                if a.verification:
                    st.caption(a.verification.message)
                    for c in a.verification.checks:
                        icon = "✅" if c.passed else ("⚠️" if c.passed is False else "○")
                        st.markdown(f"{icon} **{c.name}**: {esc(c.detail)}", unsafe_allow_html=True)
                if a.sql:
                    st.caption("The query:")
                    st.code(a.sql, language="sql")
                if a.check_sql:
                    st.caption("The independent query:")
                    st.code(a.check_sql, language="sql")
                st.caption("Steps: " + " → ".join(STEP_LABELS.get(n, n) for n in a.trace))
        if a.sources:
            with st.expander(f"Sources · {len(a.sources)}"):
                for i, s in enumerate(a.sources, 1):
                    tag = "" if s.get("status") == "current" else " (superseded)"
                    st.markdown(f"<div class='src'><b>[{i}] {esc(s['citation'])}{tag}</b> · illustrative</div>"
                                f"<div class='src' style='margin-bottom:10px'>{esc(s['text'])}</div>",
                                unsafe_allow_html=True)


with tabs[1]:
    st.markdown("<div class='section-sub' style='margin-top:4px'>Try a suggestion, or ask your own. Numbers are "
                "re-checked; policies come back with the section cited.</div>", unsafe_allow_html=True)
    label_of = {v: k for k, v in {**SUGGESTED, **MORE_QUESTIONS}.items()}
    st.pills("Try one", list(SUGGESTED.values()), format_func=label_of.get, key="pills_main",
             on_change=pick_suggestion, args=("pills_main",), label_visibility="collapsed")
    with st.expander("More questions"):
        st.pills("More", list(MORE_QUESTIONS.values()), format_func=label_of.get, key="pills_more",
                 on_change=pick_suggestion, args=("pills_more",), label_visibility="collapsed")
    typed = st.chat_input("Ask about sales, stock, waste, vendors or policies…", key="chat")
    if typed:
        st.session_state.pending = (typed, "typed")

    pending = st.session_state.pop("pending", None)
    if pending:
        question, source = pending
        with st.status(f"“{question}”", expanded=True) as status:
            # Buttons use the verified question library (instant); typed questions use the LLM when it's on.
            answer = copilot.ask(question, stores=view, use_llm=(source == "typed"),
                                 on_step=lambda node: status.write(f"✓ {STEP_LABELS.get(node, node)}"))
            status.update(label=f"Answered in {answer.latency_ms / 1000:.1f}s", state="complete", expanded=False)
        st.session_state[history_key].insert(0, answer)

    history: list[Answer] = st.session_state[history_key]
    if not history:
        st.markdown("<div class='meta' style='padding:18px 2px'>Answers appear here, newest first. Each one shows "
                    "whether its numbers were verified, and why.</div>", unsafe_allow_html=True)
    for a in history:
        render_answer(a)


# --------------------------------------------------------------------------- master data
UNMAPPED_SQL = """
SELECT m.store_id, m.local_sku AS pos_code, MIN(s.sale_date) AS first_sale, SUM(s.units) AS units,
       ROUND(SUM(s.revenue), 2) AS revenue
FROM sku_map m JOIN sales_daily s ON s.store_id = m.store_id AND s.local_sku = m.local_sku
WHERE m.master_sku_id IS NULL
GROUP BY m.store_id, m.local_sku ORDER BY revenue DESC"""
DUPLICATE_SQL = """
SELECT mp.store_id, sm.product_name AS product, GROUP_CONCAT(mp.local_sku, ', ') AS pos_codes
FROM sku_map mp JOIN sku_master sm ON sm.master_sku_id = mp.master_sku_id
GROUP BY mp.store_id, mp.master_sku_id HAVING COUNT(*) > 1 ORDER BY mp.store_id, product"""


def panel_header(title: str | None, panel: dashboard.Panel, sub: str = "") -> None:
    if title:
        st.subheader(title)
    status = panel.verification.status if panel.verification else None
    st.markdown(f"<div style='display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:-6px 0 8px'>"
                f"{verify_pill(status)}<span class='meta'>{esc(sub)}</span></div>"
                f"<div class='answer' style='margin-top:0'>{answer_html(panel.summary)}</div>", unsafe_allow_html=True)


with tabs[2]:
    panels = load_overview(tuple(view), CODE_VERSION)
    audit = panels["master_data_audit"]
    panel_header("Master data audit", audit, "9 checks from the Item Setup and Master Data SOP")
    if not audit.df.empty:
        show_table(audit.df)

    st.divider()
    prices = panels["price_exceptions"]
    panel_header("Prices that need a decision", prices, "below cost, or under the margin floor after a cost increase")
    if not prices.df.empty:
        show_table(prices.df, height=320)

    st.divider()
    app = panels["app_sync"]
    panel_header("POS vs app catalog", app, "one price in the stores and the app")
    if not app.df.empty:
        show_table(app.df, height=320)

    st.divider()
    hygiene = panels["sku_hygiene"]
    with st.expander("Duplicate and unmapped POS codes"):
        panel_header(None, hygiene, "by store")
        if not hygiene.df.empty:
            show_table(hygiene.df)
            st.caption("Unmapped codes with sales (detail list, not independently checked)")
            show_table(load_detail(tuple(view), UNMAPPED_SQL, CODE_VERSION), height=240)
            st.caption("Products with two POS codes (detail list, not independently checked)")
            show_table(load_detail(tuple(view), DUPLICATE_SQL, CODE_VERSION), height=240)
    clean = panels["clean_standard"]
    with st.expander("Clean-standard watchlist"):
        panel_header(None, clean, "Fail first · sold in the last 7 days")
        if not clean.df.empty:
            show_table(clean.df, height=320)


# --------------------------------------------------------------------------- how it works
NODE_LABELS = {"screen": "screen\nwrite requests stop here", "route": "route\nnumbers · documents · both",
               "write_sql": "write_sql\ntemplate or LLM", "run_sql": "run_sql\nguarded, read-only, scoped",
               "verify": "verify\nindependent query + sanity", "explain": "explain\nsummary + chart",
               "retrieve": "retrieve\nBM25 over sections", "answer_docs": "answer_docs\ncited [1]",
               "compose": "compose", "refuse": "refuse"}
EDGE_LABELS = {("route", "write_sql"): "numbers / both", ("route", "retrieve"): "documents",
               ("run_sql", "write_sql"): "retry once\n(LLM mode)", ("explain", "retrieve"): "both",
               ("screen", "refuse"): "write request", ("route", "refuse"): "outside access"}


def graph_dot(cp: Copilot) -> str:
    g = cp.graph.get_graph()
    out = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; pad=0.2; nodesep=0.35; ranksep=0.32;',
           'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, color="#D9D1C3", '
           'fillcolor="#FFFFFF", fontcolor="#2B2A26", margin="0.14,0.06"];',
           'edge [color="#9C9486", arrowsize=0.6, fontname="Helvetica", fontsize=8, fontcolor="#6E6A62"];']
    fills = {"route": "#E3EDE4", "verify": "#E3E8F4", "refuse": "#F4E2DA", "screen": "#F1ECE3"}
    for node in g.nodes:
        if node in ("__start__", "__end__"):
            out.append(f'"{node}" [label="{"question" if node == "__start__" else "answer"}", shape=plaintext, '
                       'style="", fontcolor="#6E6A62"];')
        else:
            out.append(f'"{node}" [label="{NODE_LABELS.get(node, node)}", fillcolor="{fills.get(node, "#FFFFFF")}"];')
    for e in g.edges:
        label = EDGE_LABELS.get((e.source, e.target), "")
        style = "dashed" if e.conditional else "solid"
        out.append(f'"{e.source}" -> "{e.target}" [style={style}, label="{label}"];')
    out.append("}")
    return "\n".join(out)


EVAL_RUNS = [("demo_first_run", "Demo, first run"), ("demo", "Demo, after fixes"),
             ("llm_first_run", "LLM, first run"), ("llm_keys_as_written", "LLM run 2, keys as first written"),
             ("llm", "LLM run 2, keys after review")]


def load_eval() -> list[tuple[str, dict]]:
    out = []
    for name, label in EVAL_RUNS:
        f = ROOT / "eval" / f"results_{name}.json"
        if f.exists():
            out.append((label, json.loads(f.read_text())))
    return out


ATTACKS = {
    "Delete data": "DELETE FROM sales_daily",
    "Read the real table": "SELECT store_id, SUM(revenue) AS revenue FROM main.sales_daily GROUP BY 1",
    "Peek at the schema": "SELECT name, sql FROM sqlite_master",
    "Sneak in a second statement": "SELECT 1; DROP TABLE stores",
    "Runaway query": "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT MAX(x) FROM c",
    "Another store's revenue": "SELECT store_id, ROUND(SUM(revenue), 2) AS revenue FROM sales_daily "
                               "WHERE store_id = 'S1' GROUP BY 1",
}

with tabs[3]:
    st.subheader("The path of a question")
    st.markdown("<div class='section-sub'>LangChain supplies the parts: prompts, the model, document search. "
                "LangGraph controls the flow: route, retry once, verify. This diagram is drawn from the graph the "
                "app is running.</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.graphviz_chart(graph_dot(copilot))
    with c2:
        st.markdown("**Numbers go to SQL, policies go to documents.** A database computes a number exactly; a "
                    "language model asked to recall one will guess. A policy is wording, so the job is to find the "
                    "right section and quote it.")
        st.markdown(f"**The verifier.** In my [Yardstick]({YARDSTICK_URL}) study, 82-97% of wrong AI-written queries "
                    "ran without error and returned plausible numbers. So every number here is worked out again by "
                    "a second, differently written query and must match within 0.5% before it's labelled Verified.")

    st.divider()
    st.subheader("Safety, enforced in code")
    st.markdown("<div class='section-sub'>None of this depends on the prompt. A model that ignores its instructions "
                "still can't get past these.</div>", unsafe_allow_html=True)
    s1, s2 = st.columns([1, 1.15])
    with s1:
        for title, body in [
            ("1 · Read-only file", "SQLite opens with mode=ro. Writes are impossible at the file level."),
            ("2 · SQL guard", "sqlglot parses the query: exactly one SELECT/WITH, no PRAGMA or ATTACH, no "
                              "schema-qualified names, allow-listed tables only."),
            ("3 · Store scope", "Each connection creates TEMP VIEWs that shadow every store-level table with "
                                "only your stores, and an authorizer denies any read of the real tables that "
                                "doesn't come through those views."),
            ("4 · Budgets", "5 seconds per query and at most 500 rows."),
            ("Before any LLM call", "Requests to delete, update or re-price are refused by the first node.")]:
            st.markdown(f"<div class='layer'><b>{esc(title)}</b><br><span class='meta' style='font-size:.85rem'>"
                        f"{esc(body)}</span></div>", unsafe_allow_html=True)
    with s2, st.container(border=True):
        st.markdown(f"<div class='card-title'>Try to break it</div><div class='card-sub'>Runs as "
                    f"{esc(role.lower())} · {esc(templates.scope_label(allowed))}</div>", unsafe_allow_html=True)
        choice = st.pills("Attack", list(ATTACKS), key="attack", label_visibility="collapsed")
        sql = st.text_area("SQL", value=ATTACKS.get(choice or "", ATTACKS["Read the real table"]), height=96,
                           key=f"attack_sql_{choice}", label_visibility="collapsed")
        if st.button("Run it", key="attack_run", type="primary"):
            try:
                guard_sql(sql)
                result = StoreDB(allowed, time_budget_s=2.0).run(sql)
                if result.df.empty:
                    st.info(f"It ran, but store scope filtered it: this role only sees "
                            f"{templates.scope_label(allowed)}, so the query found 0 rows. Nothing leaked.")
                else:
                    st.success(f"Allowed: a read-only query inside your access ({len(result.df)} rows).")
                    show_table(result.df.head(10))
            except QueryRejected as err:
                layer = ("layer 4, the time budget" if "longer than" in str(err) else
                         "layer 3, store scope" if "outside your store access" in str(err) else "layer 2, the SQL guard")
                st.error(f"Blocked by {layer}: {err}")
            except QueryError as err:
                st.warning(str(err))

    st.divider()
    st.subheader("Evaluation")
    results = load_eval()
    st.markdown("<div class='section-sub'>40 test questions, written before the first run. Each mode was run twice "
                "and both runs are shown, misses included.</div>", unsafe_allow_html=True)
    if not results:
        st.caption("Run `python eval/run_eval.py --mode demo` to fill this in.")
    if results:
        names = list(dict.fromkeys(m["metric"] for _, r in results for m in r["metrics"]))
        table = pd.DataFrame({"metric": names})
        for label, r in results:
            values = {m["metric"]: m["value"] for m in r["metrics"]}
            table[label] = [values.get(n, "not measured") for n in names]
        show_table(table)
        st.caption("In LLM run 2, every answer labelled Verified was correct and both wrong answers were flagged.")
        with st.expander("How it was graded"):
            st.markdown("20 number questions, 12 policy, 4 mixed and 4 traps. The first runs exposed gaps (phrasing "
                        "the keyword router didn't know; business conventions the LLM was never told). Those were "
                        "fixed, so the second runs are optimistic. LLM answers are graded against per-question "
                        "answer keys; 'Exact table match' is the strict version, which penalises different but "
                        "valid column choices. 56 of the 121 model calls in run 2 fell back to gpt-oss-20b under "
                        "free-tier rate limits.")

    st.divider()
    st.subheader("Limits")
    st.markdown("""
- **Made-up data.** Three stores, 2,000 SKUs, 90 days. No real sales, prices or policies.
- **Verified is not proven.** Two queries agreeing makes a silent error much less likely, not impossible.
- **A small evaluation.** 40 questions catch regressions; they aren't a benchmark.
- **Not production.** No logins, no audit log, no live POS feed. Real data would come from nightly POS exports.
""")
