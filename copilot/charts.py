"""Plotly charts in the app's calm, earthy style.

Palette (checked with a colorblind-safety validator, not eyeballed):
  stores, fixed order: Flagship #2E845A, Store 2 #4D6BC6, Store 3 #A87A1C
  status pair:          on track #355C9C, needs attention #C4704F (terracotta)
Color follows the store, never its rank. Text stays in ink colors, never series colors.
"""
from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go

STORE_COLORS = {"S1": "#2E845A", "S2": "#4D6BC6", "S3": "#A87A1C"}
OTHER_STORE = "#6B7B8C"
GOOD, WARN = "#355C9C", "#C4704F"
SINGLE = "#5E7A5A"  # one-series bars: the app's sage
INK, MUTED, GRID, SURFACE = "#2B2A26", "#6E6A62", "#EFEAE0", "#FFFFFF"
FONT = "Inter, -apple-system, Segoe UI, sans-serif"


def store_id(label) -> str | None:
    m = re.search(r"\((S\d+)\)", str(label))
    return m.group(1) if m else (str(label) if str(label) in STORE_COLORS else None)


def store_color(label) -> str:
    return STORE_COLORS.get(store_id(label) or "", OTHER_STORE)


def _money_col(col: str) -> bool:
    c = col.lower()
    return any(k in c for k in ("revenue", "cost", "cogs", "usd", "margin", "basket", "sales")) and "pct" not in c


def _hover_fmt(col: str) -> str:
    if col.lower().endswith(("pct", "_pts")):
        return "%{VALUE:.1f}"
    return "$%{VALUE:,.0f}" if _money_col(col) else "%{VALUE:,.0f}"


def _layout(fig: go.Figure, height: int = 320, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=10, r=18, t=40 if legend else 14, b=10),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(family=FONT, color=INK, size=12),
        showlegend=legend, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                                       font=dict(color=MUTED, size=12), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E6DFD3", font=dict(family=FONT, color=INK)),
        bargap=0.45,
    )
    fig.update_xaxes(showgrid=False, linecolor="#DDD5C8", tickfont=dict(color=MUTED), title=None, zeroline=False,
                     automargin=True)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, tickfont=dict(color=MUTED), title=None,
                     zeroline=False, linecolor="rgba(0,0,0,0)", automargin=True)
    return fig


# --------------------------------------------------------------------------- dashboard charts
def revenue_trend(df: pd.DataFrame, low_history: dict[str, str] | None = None, height: int = 330,
                  smooth: bool = True) -> go.Figure:
    """Daily revenue per store: a faint daily line under a 2px 7-day average, dot at the end.
    Young stores get a shaded band so nobody over-reads a few weeks of data."""
    fig = go.Figure()
    for store, g in df.groupby("store", sort=False):
        g = g.sort_values("date")
        c = store_color(store)
        avg = g["revenue"].rolling(7, min_periods=1).mean()
        if smooth:
            fig.add_trace(go.Scatter(x=g["date"], y=g["revenue"], mode="lines", showlegend=False, hoverinfo="skip",
                                     line=dict(color=c, width=1), opacity=0.25))
        y = avg if smooth else g["revenue"]
        fig.add_trace(go.Scatter(
            x=g["date"], y=y, mode="lines", name=store, line=dict(color=c, width=2),
            customdata=g["revenue"],
            hovertemplate=("7-day avg %{y:$,.0f} · that day %{customdata:$,.0f}" if smooth else "%{y:$,.0f}")
            + "<extra>" + store + "</extra>"))
        fig.add_trace(go.Scatter(x=[g["date"].iloc[-1]], y=[y.iloc[-1]], mode="markers", showlegend=False,
                                 hoverinfo="skip", marker=dict(size=8, color=c, line=dict(color=SURFACE, width=2))))
    for store, start in (low_history or {}).items():
        end = df["date"].max()
        fig.add_vrect(x0=start, x1=end, fillcolor="#A87A1C", opacity=0.08, line_width=0)
        fig.add_annotation(x=start, y=0.02, yref="paper", xanchor="right", yanchor="bottom", showarrow=False,
                           text=f"{store.split(' (')[0]} opened →<br>low history", align="right",
                           font=dict(size=10, color=MUTED))
    fig = _layout(fig, height)
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(tickprefix="$", tickformat="~s", rangemode="tozero")
    fig.update_xaxes(tickformat="%b %d")
    return fig


def margin_gap(df: pd.DataFrame, height: int = 330) -> go.Figure:
    """Diverging bars around the target: terracotta below, navy at or above."""
    d = df.sort_values("gap_pts", ascending=False)
    fig = go.Figure(go.Bar(
        x=d["gap_pts"], y=d["category"], orientation="h", marker=dict(color=[WARN if g < 0 else GOOD for g in d["gap_pts"]],
                                                                     cornerradius=4),
        text=[f"{g:+.1f}" for g in d["gap_pts"]], textposition="outside", textfont=dict(color=INK, size=12),
        customdata=d[["margin_pct", "target_pct"]].to_numpy(),
        hovertemplate="%{y}<br>%{customdata[0]:.1f}% vs %{customdata[1]:.0f}% target<br>%{x:+.1f} pts<extra></extra>"))
    lim = max(1.0, d["gap_pts"].abs().max()) * 1.6
    fig = _layout(fig, height, legend=False)
    fig.add_vline(x=0, line_color="#B9B1A4", line_width=1)
    fig.update_xaxes(range=[-lim, lim], showgrid=True, gridcolor=GRID, ticksuffix=" pts")
    fig.update_yaxes(showgrid=False)
    return fig


# --------------------------------------------------------------------------- answer charts
def from_spec(df: pd.DataFrame, spec: dict | None) -> go.Figure | None:
    """Turn a template's (or the auto-guessed) chart spec into a figure."""
    if not spec or df is None or df.empty or spec.get("x") not in df.columns:
        return None
    kind, x, y = spec["type"], spec["x"], spec["y"]
    ys = y if isinstance(y, list) else [y]
    if any(col not in df.columns for col in ys):
        return None
    if kind == "line":
        if spec.get("color") in df.columns and spec["color"] == "store":
            return revenue_trend(df.rename(columns={x: "date", ys[0]: "revenue"})[["date", "store", "revenue"]])
        fig = go.Figure(go.Scatter(x=df[x], y=df[ys[0]], mode="lines", line=dict(color=SINGLE, width=2)))
        return _layout(fig, 300, legend=False)
    if kind == "diverging":
        return margin_gap(df)
    if kind == "hbar":
        d = df.head(15).iloc[::-1]
        fig = go.Figure(go.Bar(x=d[x], y=d[y], orientation="h", marker=dict(color=SINGLE, cornerradius=4),
                               hovertemplate="%{y}<br>" + _hover_fmt(x).replace("VALUE", "x") + "<extra></extra>"))
        fig = _layout(fig, max(260, 26 * len(d) + 40), legend=False)
        fig.update_yaxes(showgrid=False, automargin=True)
        fig.update_xaxes(showgrid=True, gridcolor=GRID, tickprefix="$" if _money_col(x) else "")
        return fig
    # vertical bars
    if len(ys) > 1:  # stacked measures: status pair, legend + 2px gaps
        colors = [GOOD, WARN]
        fig = go.Figure([go.Bar(x=df[x], y=df[c], name=c.replace("_", " "), marker=dict(color=colors[i % 2],
                                line=dict(color=SURFACE, width=2)),
                                hovertemplate="%{x}<br>" + c.replace("_", " ") + ": %{y:,.0f}<extra></extra>")
                        for i, c in enumerate(ys)])
        fig = _layout(fig, 300)
        fig.update_layout(barmode="stack", legend_traceorder="reversed")
        fig.update_traces(width=0.32 if len(df) <= 4 else None)
        return fig
    is_store = x == "store"
    colors = [store_color(v) for v in df[x]] if is_store else SINGLE
    fig = go.Figure(go.Bar(x=df[x], y=df[ys[0]], marker=dict(color=colors, cornerradius=4),
                           text=[_fmt(v, ys[0]) for v in df[ys[0]]], textposition="outside",
                           textfont=dict(color=INK), cliponaxis=False,
                           hovertemplate="%{x}<br>" + _hover_fmt(ys[0]).replace("VALUE", "y") + "<extra></extra>"))
    fig = _layout(fig, 300, legend=False)
    fig.update_traces(width=0.32 if len(df) <= 4 else None)
    if spec.get("target") is not None:
        fig.add_hline(y=spec["target"], line_color=WARN, line_width=1.5,
                      annotation_text=f"target {spec['target']:g}%", annotation_font_color=MUTED,
                      annotation_position="top left")
    top = max(df[ys[0]].max(), spec.get("target") or 0)
    fig.update_yaxes(range=[0, top * 1.22], tickprefix="$" if _money_col(ys[0]) else "")
    return fig


def _fmt(v, col: str) -> str:
    if col.lower().endswith("pct"):
        return f"{v:.1f}%"
    if _money_col(col):
        return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"
    return f"{v:,.0f}"
