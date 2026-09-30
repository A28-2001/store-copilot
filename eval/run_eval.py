"""Score the Copilot on the 40 golden questions.

    python eval/run_eval.py --mode demo      # no key needed
    python eval/run_eval.py --mode llm       # needs a working GROQ_API_KEY

Writes eval/results_<mode>.md (readable) and eval/results_<mode>.json (read by the app).
Numbers are reported as they come out, misses included.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

import yaml
from langchain_core.callbacks import BaseCallbackHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from copilot import config, rag, templates  # noqa: E402
from copilot.db import QueryError, StoreDB  # noqa: E402
from copilot.engine import Copilot  # noqa: E402
from copilot.llm import PRIMARY_MODEL, check_key, make_llm, resolve_key  # noqa: E402
from copilot.verifier import compare_frames  # noqa: E402
from data.generate import ensure_db  # noqa: E402

EXPECTED_ROUTE = {"numbers": "numbers", "documents": "documents", "both": "both"}


class ModelCounter(BaseCallbackHandler):
    """Counts which Groq model actually answered each call (did the fallback kick in?)."""

    def __init__(self):
        self.calls: dict[str, int] = {}

    def on_llm_end(self, response, **kwargs):
        name = (response.llm_output or {}).get("model_name", "unknown")
        self.calls[name] = self.calls.get(name, 0) + 1


MODEL_COUNTER = ModelCounter()


def gold_frame(template_id: str, question: str, scope: list[str]):
    """The template's answer for the same question and scope: the gold result for LLM mode."""
    t = templates.BY_ID[template_id]
    prepared = templates.prepare(t, question, scope)
    if prepared.problem:
        return None
    try:
        return StoreDB(scope).run(prepared.sql).df
    except QueryError:
        return None


ANSWER_KEYS = yaml.safe_load((ROOT / "eval" / "answer_keys.yaml").read_text())


def key_facts(qid: str) -> list[list]:
    """Each acceptable reading of the question, as a list of facts (None = not applicable, dropped)."""
    db = StoreDB(config.store_ids())
    return [[v for v in db.run(sql).df.iloc[0].tolist() if v is not None and v == v] for sql in ANSWER_KEYS[qid]]


def contains_facts(df, facts: list) -> bool:
    """Every fact appears in the table: numbers in any cell, column total or row count (0.5% or 0.1),
    text as a case-insensitive substring of any cell. Column names and extra columns don't matter."""
    num = df.select_dtypes("number")
    numbers = [float(len(df))] + [float(x) for x in num.to_numpy().ravel() if x == x] + [float(num[c].sum()) for c in num]
    texts = [str(x).lower() for x in df.select_dtypes(exclude="number").to_numpy().ravel()]
    for f in facts:
        if isinstance(f, str):
            if not any(f.lower() in t for t in texts):
                return False
        elif not any(abs(n - float(f)) <= max(0.005 * abs(float(f)), 0.1) for n in numbers):
            return False
    return True


def answer_matches(qid: str, df) -> bool:
    return any(contains_facts(df, facts) for facts in key_facts(qid))


def expected_route(item: dict) -> str:
    if item["kind"] == "trap":
        return "refuse" if item["expect"] == "refuse" else "documents"
    return EXPECTED_ROUTE[item["kind"]]


def retrieval_hit(item: dict, sources: list[dict]) -> bool:
    for s in sources[:3]:
        title, _, rest = s["citation"].partition(" · ")
        section = rest.rsplit(" (v", 1)[0]
        if section == item["section"] and (not item.get("title") or title == item["title"]):
            return True
    return False


def score_one(item: dict, answer, mode: str) -> dict:
    row = {"id": item["id"], "kind": item["kind"], "question": item["question"],
           "expected_route": expected_route(item), "route": answer.route,
           "latency_ms": round(answer.latency_ms), "verification": answer.verification.status if answer.verification else None}
    row["route_ok"] = row["route"] == row["expected_route"]

    if item.get("section"):
        row["retrieval_hit"] = retrieval_hit(item, answer.sources)
    if item.get("template"):
        if mode == "demo":
            row["answer_ok"] = answer.template_id == item["template"]
        else:
            gold = gold_frame(item["template"], item["question"], answer.scope)
            has = answer.data is not None and gold is not None and not answer.data.empty and not gold.empty
            row["exact_ok"] = bool(has and compare_frames(answer.data, gold)[0])
            row["answer_ok"] = bool(answer.data is not None and not answer.data.empty and answer_matches(item["id"], answer.data))
            row["sql"] = answer.sql
            row["scope"] = answer.scope
        # a silent error: labelled verified, but not the right answer
        row["silent_error"] = row["verification"] == "verified" and not row["answer_ok"]
    if item["kind"] == "trap":
        row["trap_ok"] = (answer.route == "refuse") if item["expect"] == "refuse" else (answer.text == rag.NO_DOCUMENT)
    return row


def pct(rows: list[dict], key: str) -> tuple[str, int, int]:
    vals = [r[key] for r in rows if key in r]
    hits = sum(bool(v) for v in vals)
    return (f"{100 * hits / len(vals):.0f}%" if vals else "n/a"), hits, len(vals)


def summarize(rows: list[dict]) -> list[dict]:
    numbers = [r for r in rows if r["verification"] is not None]
    non_traps = [r for r in rows if r["kind"] != "trap"]
    metrics = []

    def add(name, value, detail):
        metrics.append({"metric": name, "value": value, "detail": detail})

    v, h, n = pct(rows, "route_ok")
    add("Routing accuracy", v, f"{h} of {n} questions sent down the expected path")
    v, h, n = pct(rows, "retrieval_hit")
    add("Retrieval hit@3", v, f"{h} of {n} document questions had the right section in the top 3")
    v, h, n = pct(rows, "answer_ok")
    add("Answer accuracy", v, f"{h} of {n} numbers questions matched the gold answer"
        + (" (answer key)" if any("exact_ok" in r for r in rows) else " (expected template)"))
    if any("exact_ok" in r for r in rows):
        v, h, n = pct(rows, "exact_ok")
        add("Exact table match", v, f"{h} of {n} returned the same table as the template (strict)")
    verified = sum(r["verification"] == "verified" for r in numbers)
    add("Verification rate", f"{100 * verified / len(numbers):.0f}%" if numbers else "n/a",
        f"{verified} of {len(numbers)} answers with numbers were labelled Verified")
    silent = sum(bool(r.get("silent_error")) for r in rows)
    graded = sum("silent_error" in r for r in rows)
    add("Silent-error rate", f"{100 * silent / graded:.0f}%" if graded else "n/a",
        f"{silent} of {graded} graded answers were labelled Verified but wrong")
    v, h, n = pct(rows, "trap_ok")
    add("Traps handled", v, f"{h} of {n}: 3 write requests refused, 1 off-topic question answered 'no document'")
    false_refusals = sum(r["route"] == "refuse" for r in non_traps)
    add("False refusals", str(false_refusals), f"real questions refused, out of {len(non_traps)}")
    add("Median latency", f"{statistics.median(r['latency_ms'] for r in rows) / 1000:.2f}s", "per question, end to end")
    return metrics


def to_markdown(mode: str, metrics: list[dict], rows: list[dict], note: str) -> str:
    lines = [f"# Evaluation: {mode} mode", "", note, "",
             "| Metric | Value | Detail |", "|---|---|---|"]
    lines += [f"| {m['metric']} | {m['value']} | {m['detail']} |" for m in metrics]
    lines += ["", "## Every question", "", "| id | kind | question | route (expected) | verified | pass |",
              "|---|---|---|---|---|---|"]
    for r in rows:
        checks = [r.get(k) for k in ("route_ok", "retrieval_hit", "answer_ok", "trap_ok") if k in r]
        ok = "✅" if all(checks) else "❌"
        route = r["route"] if r["route_ok"] else f"**{r['route']}** ({r['expected_route']})"
        lines.append(f"| {r['id']} | {r['kind']} | {r['question']} | {route} | {r['verification'] or ''} | {ok} |")
    misses = [r for r in rows if not all(r.get(k, True) for k in ("route_ok", "retrieval_hit", "answer_ok", "trap_ok"))]
    lines += ["", f"## Misses ({len(misses)})", ""]
    for r in misses:
        why = [k.replace("_ok", "").replace("_", " ") for k in ("route_ok", "retrieval_hit", "answer_ok", "trap_ok")
               if k in r and not r[k]]
        lines.append(f"- **{r['id']}** {r['question']} (failed: {', '.join(why)}; got route `{r['route']}`)")
    return "\n".join(lines) + "\n"


def regrade(path: Path) -> None:
    """Re-grade a finished LLM run from the SQL it saved: no new LLM calls."""
    from copilot.engine import detect_stores
    data = json.loads(path.read_text())
    golden = {g["id"]: g for g in yaml.safe_load((ROOT / "eval" / "golden.yaml").read_text())}
    for row in data["rows"]:
        if "exact_ok" not in row:
            continue
        item = golden[row["id"]]
        scope = row.get("scope") or [s for s in detect_stores(item["question"])] or config.store_ids()
        df = None
        if row.get("sql"):
            try:
                df = StoreDB(scope).run(row["sql"]).df
            except QueryError:
                df = None
        gold = gold_frame(item["template"], item["question"], scope)
        row["exact_ok"] = bool(df is not None and gold is not None and not df.empty and not gold.empty
                               and compare_frames(df, gold)[0])
        row["answer_ok"] = bool(df is not None and not df.empty and answer_matches(item["id"], df))
        row["silent_error"] = row["verification"] == "verified" and not row["answer_ok"]
    data["metrics"] = summarize(data["rows"])
    data["note"] = data.get("note", "") + " Re-graded offline with eval/answer_keys.yaml."
    path.write_text(json.dumps(data, indent=2))
    md = path.with_suffix(".md")
    note = md.read_text().split("\n")[2] if md.exists() else ""
    md.write_text(to_markdown("llm", data["metrics"], data["rows"], note + " Re-graded offline with eval/answer_keys.yaml."))
    for m in data["metrics"]:
        print(f"{m['metric']:20s} {m['value']:>7s}  {m['detail']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["demo", "llm"], default="demo")
    ap.add_argument("--regrade", type=Path, help="re-grade a finished LLM results JSON from its saved SQL")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between questions in LLM mode (rate limits)")
    args = ap.parse_args()
    ensure_db()
    if args.regrade:
        return regrade(args.regrade)

    llm = None
    if args.mode == "llm":
        key = resolve_key()
        ok, msg = check_key(key) if key else (False, "No GROQ_API_KEY found.")
        if not ok:
            sys.exit(f"LLM mode needs a working key: {msg}")
        llm = make_llm(key, callbacks=[MODEL_COUNTER])
    copilot = Copilot(config.store_ids(), llm=llm)
    golden = yaml.safe_load((ROOT / "eval" / "golden.yaml").read_text())

    rows = []
    for item in golden:
        answer = copilot.ask(item["question"])
        rows.append(score_one(item, answer, args.mode))
        mark = "ok " if all(rows[-1].get(k, True) for k in ("route_ok", "retrieval_hit", "answer_ok", "trap_ok")) else "MISS"
        print(f"{mark} {item['id']} [{answer.route}] {item['question']}")
        if args.mode == "llm":
            time.sleep(args.pause)

    metrics = summarize(rows)
    run_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    note = (f"Run {run_at}, {len(rows)} golden questions asked as the Owner (all stores). "
            + ("Demo mode: keyword router + 13-question template library, no LLM. "
               "Answer accuracy = the matched template is the expected one." if args.mode == "demo" else
               f"LLM mode: {PRIMARY_MODEL} on Groq (with fallback). Answer accuracy = the LLM's result contains the "
               "facts in eval/answer_keys.yaml for one acceptable reading of the question. Exact table match = "
               "compare_frames against the template's whole table, which penalises different but valid choices."))
    out = ROOT / "eval"
    (out / f"results_{args.mode}.md").write_text(to_markdown(args.mode, metrics, rows, note))
    if args.mode == "llm":
        note += " Model calls: " + ", ".join(f"{m} × {n}" for m, n in sorted(MODEL_COUNTER.calls.items())) + "."
    (out / f"results_{args.mode}.md").write_text(to_markdown(args.mode, metrics, rows, note))
    (out / f"results_{args.mode}.json").write_text(json.dumps(
        {"mode": args.mode, "run_at": run_at, "n_questions": len(rows), "metrics": metrics, "rows": rows,
         "model_calls": MODEL_COUNTER.calls}, indent=2))
    print()
    for m in metrics:
        print(f"{m['metric']:20s} {m['value']:>7s}  {m['detail']}")


if __name__ == "__main__":
    main()
