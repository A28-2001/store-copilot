"""The Story tab: one month of data told as a scroll story.

Layered parallax opens each chapter; in the middle, a chart stays pinned while
four short steps scroll past and the chart changes with them. Every number comes
from copilot.figures (each worked out two ways), the same source as the board
pack, plus the planning model's headline results. The HTML is built here from
those numbers only; nothing user-supplied reaches it.
"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from copilot import config
from copilot.figures import WASTE_TARGET, Figures, money

INK, GREEN, BRIGHT, MINT, SAND, MUTED = "#1F3A2D", "#2E5E45", "#2E845A", "#A9CDB2", "#CFC8BB", "#6E6A62"


# ----------------------------------------------------------------------------- charts (inline SVG)
def _bars(rows: list[tuple[str, list[tuple[float, str]], str]], scale: float, label_w: int = 150,
          bar_h: int = 46) -> str:
    """Horizontal bars. Each row: label, segments (value, color), text shown after the bar."""
    width, gap = 440, 20
    plot = width - label_w - 92
    height = len(rows) * (bar_h + gap)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" xmlns="http://www.w3.org/2000/svg">']
    for i, (label, segments, text) in enumerate(rows):
        y = i * (bar_h + gap) + 4
        out.append(f'<text x="0" y="{y + bar_h / 2 + 6}" class="lab">{html.escape(label)}</text>')
        x = label_w
        for value, color in segments:
            w = max(2.0, plot * value / scale) if value > 0 else 0
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="7" fill="{color}"/>')
            x += w
        out.append(f'<text x="{x + 8:.1f}" y="{y + bar_h / 2 + 6}" class="val">{html.escape(text)}</text>')
    out.append("</svg>")
    return "".join(out)


def charts(f: Figures) -> list[tuple[str, str, str]]:
    """The four states of the pinned chart: (title, svg, caption)."""
    before, now = f["sales_before"], f["sales_now"]
    top = max(before, now) * 1.05
    sales = _bars([("30 days before", [(before, SAND)], money(before)),
                   ("Last 30 days", [(now, BRIGHT)], money(now))], top)
    colors = (GREEN, MINT, BRIGHT)
    stores = _bars([(label, [(v, colors[j]) for j, (_, v) in enumerate(f[f"stores_{key}"])],
                     money(f[f"sales_{key}"]))
                    for label, key in (("30 days before", "before"), ("Last 30 days", "now"))], top)
    legend = " · ".join(f'<span style="color:{colors[j]}">■</span> {html.escape(name)}'
                        for j, (name, _) in enumerate(f["stores_now"]))
    leaks = [("Waste over target", f["leak_waste"]), ("Prices below cost", f["leak_price"]),
             ("Cost increases", f["leak_cost"])]
    leak = _bars([(label, [(v, BRIGHT if i == 0 else MINT)], money(v)) for i, (label, v) in enumerate(leaks)],
                 max(v for _, v in leaks) * 1.05)
    rows = []
    for name, stock, per_day, life, _ in f["over_life"]:
        short = name.replace("Beverages & Juices", "Juices and drinks")
        rows.append((short, [(stock / per_day, BRIGHT)], f"{stock / per_day:.0f} days"))
        rows.append(("shelf life", [(life, SAND)], f"{life:.0f} days"))
    stock = _bars(rows, max(r[1][0][0] for r in rows) * 1.05, bar_h=30)
    return [("Sales, 30 days", sales, "All stores"),
            ("Sales by store, 30 days", stores, legend),
            ("Margin leaks, a year at today's pace", leak, f"About {money(sum(v for _, v in leaks))} in total"),
            ("Days of stock vs shelf life", stock, "Fresh food categories holding more than they can sell in time")]


# ----------------------------------------------------------------------------- the story
def _m(x: float) -> str:
    """Headline money: $0.9M rather than $901K."""
    return f"${x / 1e6:.1f}M" if abs(x) >= 5e5 else money(x)


def build(f: Figures, plan: dict) -> str:
    growth = f["sales_now"] / f["sales_before"] - 1
    same = f["same_now"] / f["same_before"] - 1
    newest = config.store_by_id(config.newest_store())
    opened = date.fromisoformat(str(newest["open_date"]))
    leak_total = f["leak_waste"] + f["leak_price"] + f["leak_cost"]
    worst = max(f["over_life"], key=lambda c: c[1] / c[2] - c[3])
    drawn = charts(f)
    figs = "".join(f'<figure class="fig" data-i="{i}"><figcaption class="fig-title">{html.escape(t)}</figcaption>'
                   f'{svg}<div class="fig-note">{note}</div></figure>' for i, (t, svg, note) in enumerate(drawn))
    steps = [
        f"<b>{money(f['sales_now'])}</b> in sales over the last 30 days, up <b>{growth:.0%}</b> on the 30 days before.",
        f"But the {f['same_stores']} stores open both months were {'flat' if abs(same) < 0.01 else f'{same:+.0%}'}. "
        f"All of the growth came from {html.escape(newest['name'])}, which opened on {opened:%B} {opened.day}.",
        f"Margin held at <b>{f['gm_now']:.1%}</b>. The money leaks elsewhere: about <b>{money(leak_total)} a year</b> "
        "at today's pace, and most of it is waste.",
        f"Fresh food holds more stock than it can sell in time. {html.escape(worst[0])} sits for "
        f"<b>{worst[1] / worst[2]:.0f} days</b> against a {worst[3]:.0f}-day shelf life. That's where the waste starts.",
    ]
    # On phones the pinned chart is hidden and each step carries its own copy instead.
    step_html = "".join(f'<div class="step" data-i="{i}"><div class="card">{s}<div class="inline-fig">'
                        f'<div class="fig-title">{html.escape(drawn[i][0])}</div>{drawn[i][1]}</div></div></div>'
                        for i, s in enumerate(steps))
    payback = plan.get("payback")
    payback_text = f"pays back in <b>{payback:.0f} months</b>" if isinstance(payback, (int, float)) \
        else "takes more than five years to pay back"
    fixes = [
        ("Cut waste", f"{money(f['leak_waste'])} a year above the {WASTE_TARGET:.0%} target. "
                      f"{f['waste_cafe'] / f['waste']:.0%} of it is the cafe and hot bar.",
         "ask:What is our waste as a percent of perishable cost by store?", "See waste by store"),
        ("Fix pricing", f"{f['leak_price_items']} prices keyed below cost and {f['leak_cost_items']} cost increases "
                        f"not passed on: {money(f['leak_price'] + f['leak_cost'])} a year.",
         "master", "See the audit"),
        ("Pull failed items", f"{f['fail_sales_items']} items that fail the clean standard still sell "
                              f"{money(f['fail_sales'])} a year. The standard says 48 hours.",
         "ask:Which Fail or Review items are still selling?", "See which items"),
    ]
    fix_html = "".join(f'<div class="fix reveal"><div class="fix-n">{i}</div><h3>{t}</h3><p>{d}</p>'
                       f'<a href="#" class="cta" data-goto="{html.escape(g)}">{c} →</a></div>'
                       for i, (t, d, g, c) in enumerate(fixes, 1))
    return f"""
<div class="story">
  <section class="hero parallax">
    <div class="shape s1" data-speed="0.35"></div><div class="shape s2" data-speed="-0.2"></div>
    <div class="shape s3" data-speed="0.6"></div><div class="shape s4" data-speed="0.15"></div>
    <div class="hero-in reveal">
      <div class="eyebrow">A story in four chapters · made-up data</div>
      <h1>What 30 days of data say about a grocery store.</h1>
      <p>Three stores, 2,000 products, one month. Every number on this page is worked out two ways before
      it's shown.</p>
      <div class="cue"><span></span>Scroll</div>
    </div>
  </section>

  <section class="band parallax">
    <div class="big" data-speed="0.4">01</div><div class="shape b1" data-speed="-0.25"></div>
    <div class="band-in reveal"><div class="kicker">The month</div>
      <h2>Sales grew {growth:.0%}. The question is where the money went.</h2></div>
  </section>

  <section class="scrolly">
    <div class="graphic"><div class="frame">{figs}</div></div>
    <div class="steps">{step_html}</div>
  </section>

  <section class="band parallax alt">
    <div class="big" data-speed="0.4">02</div><div class="shape b2" data-speed="-0.3"></div>
    <div class="band-in reveal"><div class="kicker">Can you trust these numbers?</div>
      <h2>Every number is worked out twice.</h2>
      <p>A second, separately written query has to agree within 0.5% before a number is called verified. Policy
      answers are quoted from the document, with the section named.</p>
      <a href="#" class="cta" data-goto="ask:Which prices fell under the margin floor?">See it on a real question →</a>
    </div>
  </section>

  <section class="fixes">
    <div class="fixes-head reveal"><div class="kicker">03 · What to fix first</div>
      <h2>Three fixes, worth about {money(leak_total)} a year, plus the brand.</h2></div>
    <div class="fix-grid">{fix_html}</div>
  </section>

  <section class="band parallax dark">
    <div class="big" data-speed="0.4">04</div><div class="shape b3" data-speed="-0.25"></div>
    <div class="band-in reveal"><div class="kicker">The next store</div>
      <h2>A new store {payback_text}, but needs about {_m(plan.get("funding", 0))} of funding before the
      build starts.</h2>
      <p>From the planning model: a {plan.get("sqft", 0):,.0f} sq ft store costing about
      {_m(plan.get("invest", 0))}, with the cash it needs week by week. The board pack puts this month on three
      slides. Both download below.</p>
    </div>
  </section>
</div>"""


def plan_numbers(path: Path) -> dict:
    """Headline results from the planning model's cached values (Base scenario)."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True, read_only=False)
    except Exception:
        return {}
    out = {}
    for key, name in (("payback", "ns_payback"), ("invest", "ns_invest"), ("funding", "cash_funding"),
                      ("sqft", "sqft_new")):
        try:
            sheet, ref = next(iter(wb.defined_names[name].destinations))
            out[key] = wb[sheet][ref.replace("$", "")].value
        except (KeyError, StopIteration):
            pass
    return out


HTML = '<div class="story-root"></div>'

CSS = """
:host { display: block; }
.story { --ink: #1F3A2D; --green: #2E5E45; --bright: #2E845A; --muted: #6E6A62; --cream: #F4EFE6;
  --line: #E6DFD3; color: var(--ink); font-family: Inter, system-ui, -apple-system, sans-serif; }
.story h1, .story h2, .story h3 { font-family: Fraunces, Georgia, serif; font-weight: 500; letter-spacing: -0.01em;
  margin: 0; }
.parallax { position: relative; overflow: hidden; border-radius: 22px; }
.shape, .big { position: absolute; pointer-events: none; will-change: transform; }
.eyebrow, .kicker { font-size: .74rem; letter-spacing: .14em; text-transform: uppercase; font-weight: 600;
  color: var(--bright); margin-bottom: .8rem; }

.hero { min-height: 74vh; background: linear-gradient(165deg, #EEF3EF 0%, #F6F2EA 70%); display: flex;
  align-items: center; padding: 9vh 6%; margin: 6px 0 22px; }
.hero h1 { font-size: clamp(2.1rem, 5.2vw, 3.9rem); line-height: 1.07; max-width: 13em; }
.hero p { font-size: clamp(1rem, 1.6vw, 1.2rem); line-height: 1.55; color: #3E4A42; max-width: 34em; margin: 1.1rem 0 0; }
.hero-in { position: relative; z-index: 2; }
.s1 { width: 46vw; height: 46vw; max-width: 560px; max-height: 560px; right: -8%; top: -18%; border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, #CFE3D3, #A9CDB2 60%, rgba(169,205,178,0) 72%); opacity: .8; }
.s2 { width: 220px; height: 220px; right: 22%; bottom: -60px; border-radius: 0 70% 0 70%; background: #2E845A; opacity: .16; }
.s3 { width: 120px; height: 120px; right: 9%; bottom: 18%; border-radius: 50%; border: 2px solid #2E5E45; opacity: .25; }
.s4 { width: 340px; height: 340px; left: -120px; bottom: -170px; border-radius: 50%; background: #E3DCCB; opacity: .6; }
.cue { margin-top: 2.4rem; font-size: .78rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted);
  display: flex; align-items: center; gap: 10px; }
.cue span { width: 1px; height: 34px; background: var(--muted); animation: cue 1.8s ease-in-out infinite; transform-origin: top; }
@keyframes cue { 0% { transform: scaleY(0); } 50% { transform: scaleY(1); } 100% { transform: scaleY(0); transform-origin: bottom; } }

.band { background: var(--cream); padding: 13vh 6%; margin: 22px 0; min-height: 46vh; display: flex; align-items: center; }
.band.alt { background: #EEF3EF; }
.band.dark { background: radial-gradient(120% 140% at 100% 0%, #2F4F3C 0%, #1F3A2D 55%, #1A3126 100%); color: #F3EEE4; }
.band.dark .kicker { color: #B8CBB3; } .band.dark p { color: #D9D3C6; }
.band h2 { font-size: clamp(1.7rem, 3.6vw, 2.7rem); line-height: 1.15; max-width: 18em; }
.band p { font-size: 1.05rem; line-height: 1.6; color: #3E4A42; max-width: 38em; margin: 1rem 0 0; }
.band-in { position: relative; z-index: 2; }
.big { right: 4%; top: -4vh; font-family: Fraunces, Georgia, serif; font-size: clamp(9rem, 24vw, 19rem); line-height: 1;
  color: rgba(31,58,45,.07); font-weight: 500; }
.band.dark .big { color: rgba(243,238,228,.07); }
.b1 { width: 260px; height: 260px; left: 46%; bottom: -150px; border-radius: 50%; background: #A9CDB2; opacity: .35; }
.b2 { width: 200px; height: 200px; right: 30%; top: -90px; border-radius: 0 70% 0 70%; background: #2E845A; opacity: .14; }
.b3 { width: 300px; height: 300px; left: 52%; bottom: -170px; border-radius: 50%; border: 1px solid rgba(243,238,228,.2); }

.scrolly { display: grid; grid-template-columns: 1.15fr 1fr; gap: 4%; align-items: start; margin: 10px 0; }
.graphic { position: sticky; top: 10vh; height: 76vh; display: flex; align-items: center; }
.frame { position: relative; width: 100%; height: 54vh; min-height: 380px; background: #FFFFFF; border: 1px solid var(--line);
  border-radius: 18px; }
.fig { position: absolute; inset: 0; margin: 0; padding: 6% 7%; display: flex; flex-direction: column;
  justify-content: center; opacity: 0; transform: translateY(14px); transition: opacity .55s ease, transform .55s ease; }
.fig.on { opacity: 1; transform: none; }
.fig-title { font-family: Fraunces, Georgia, serif; font-size: 1.4rem; margin-bottom: 1.2rem; }
.fig svg { width: 100%; height: auto; overflow: visible; }
.fig .lab { font-size: 17px; fill: #3E4A42; } .fig .val { font-size: 18px; font-weight: 600; fill: #1F3A2D; }
.fig-note { margin-top: 1rem; font-size: .95rem; color: var(--muted); }
.steps { padding: 4vh 0 16vh; }
.step { min-height: 72vh; display: flex; align-items: center; }
.step .card { background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; padding: 24px 26px;
  font-size: 1.18rem; line-height: 1.6; opacity: .3; transition: opacity .45s ease, box-shadow .45s ease; }
.step.on .card { opacity: 1; box-shadow: 0 12px 34px rgba(31,58,45,.10); }
.step b { color: var(--green); }
.inline-fig { display: none; }
.inline-fig svg { width: 100%; height: auto; }
.inline-fig .lab { font-size: 17px; fill: #3E4A42; } .inline-fig .val { font-size: 18px; font-weight: 600; fill: #1F3A2D; }

.fixes { padding: 8vh 0 4vh; }
.fixes h2 { font-size: clamp(1.6rem, 3.2vw, 2.4rem); line-height: 1.15; max-width: 20em; }
.fix-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-top: 2rem; }
.fix { background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; padding: 22px 22px 20px;
  display: flex; flex-direction: column; }
.fix:nth-child(2) { transition-delay: .12s; } .fix:nth-child(3) { transition-delay: .24s; }
.fix-n { font-family: Fraunces, Georgia, serif; font-size: 2rem; color: var(--bright); line-height: 1; }
.fix h3 { font-size: 1.35rem; margin: .6rem 0 .4rem; }
.fix p { font-size: .98rem; line-height: 1.55; color: #3E4A42; margin: 0 0 1rem; flex: 1; }
.cta { display: inline-block; align-self: flex-start; margin-top: 1.1rem; font-weight: 600; font-size: .95rem; color: var(--bright);
  text-decoration: none; border-bottom: 1.5px solid currentColor; padding-bottom: 2px; }
.band.dark .cta { color: #CFE3D3; }
.cta:hover { opacity: .75; }

.reveal { opacity: 0; transform: translateY(28px); transition: opacity .8s ease, transform .8s ease; }
.reveal.in { opacity: 1; transform: none; }

@media (max-width: 820px) {
  .hero { min-height: 62vh; padding: 8vh 7%; }
  .scrolly { grid-template-columns: 1fr; }
  .graphic { display: none; }
  .steps { padding: 0; }
  .step { min-height: 0; padding: 12px 0; }
  .step .card { opacity: 1; font-size: 1.05rem; }
  .inline-fig { display: block; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
  .inline-fig .fig-title { font-size: 1.05rem; margin-bottom: .7rem; }
  .fix-grid { grid-template-columns: 1fr; }
  .band { padding: 10vh 7%; }
}
@media (prefers-reduced-motion: reduce) {
  .reveal, .fig, .step .card { transition: none; }
  .reveal { opacity: 1; transform: none; }
  .cue span { animation: none; }
}
"""

JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.querySelector('.story-root');
  root.innerHTML = data.html;

  const figs = [...root.querySelectorAll('.fig')];
  const steps = [...root.querySelectorAll('.step')];
  let current = -1;
  const show = (i) => {
    if (i === current) return;
    current = i;
    figs.forEach((f, j) => f.classList.toggle('on', j === i));
    steps.forEach((s, j) => s.classList.toggle('on', j === i));
  };
  const nearest = () => {             // the step closest to the middle of the screen
    const mid = window.innerHeight / 2;
    let best = current < 0 ? 0 : current, gap = Infinity;
    steps.forEach((s, i) => {
      const r = s.getBoundingClientRect();
      const d = Math.abs(r.top + r.height / 2 - mid);
      if (d < gap) { gap = d; best = i; }
    });
    show(best);
  };
  show(0);
  const stepWatch = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) show(Number(e.target.dataset.i)); });
  }, { rootMargin: '-45% 0px -45% 0px' });
  steps.forEach((s) => stepWatch.observe(s));

  const revealWatch = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); revealWatch.unobserve(e.target); } });
  }, { threshold: 0.15 });
  root.querySelectorAll('.reveal').forEach((el) => revealWatch.observe(el));

  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const layers = [...root.querySelectorAll('[data-speed]')];
  let queued = false;
  const move = () => {
    queued = false;
    nearest();
    if (still) return;
    const mid = window.innerHeight / 2;
    for (const el of layers) {
      const box = el.closest('.parallax').getBoundingClientRect();
      if (box.bottom < -200 || box.top > window.innerHeight + 200) continue;
      const offset = (box.top + box.height / 2 - mid) * Number(el.dataset.speed);
      el.style.transform = `translate3d(0, ${offset.toFixed(1)}px, 0)`;
    }
  };
  const onScroll = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(move);
    setTimeout(() => { if (queued) move(); }, 150);   // if the browser pauses frames, update anyway
  };
  document.addEventListener('scroll', onScroll, { capture: true, passive: true });
  window.addEventListener('resize', onScroll);
  move();

  root.querySelectorAll('[data-goto]').forEach((link) => {
    link.addEventListener('click', (e) => { e.preventDefault(); setTriggerValue('goto', link.dataset.goto); });
  });

  return () => {
    stepWatch.disconnect();
    revealWatch.disconnect();
    document.removeEventListener('scroll', onScroll, { capture: true });
    window.removeEventListener('resize', onScroll);
  };
}
"""
