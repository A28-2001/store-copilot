"""The Copilot engine: a LangGraph state graph.

    START -> screen --(write request)--------------------------------> refuse -> END
               |
             route  (LLM one-word classifier, or keywords in demo mode)
               |-- documents --------------------------> retrieve -> answer_docs -> compose -> END
               |-- numbers / both -> write_sql -> run_sql -> verify -> explain --+
               |                        ^            |                          |-- both -> retrieve ...
               |                        +-- retry ---+ (LLM mode, once)         +-- numbers -> compose
               |-- refuse -> refuse -> END

LangChain supplies the parts (prompts, ChatGroq, the BM25 retriever); LangGraph
supplies the control flow: conditional routing, a bounded retry loop, and one
shared state object that every node reads and writes.
"""
from __future__ import annotations

import operator
import re
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

import pandas as pd
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph

from copilot import config, rag, templates
from copilot.db import QueryError, QueryResult, StoreDB
from copilot.schema import schema_notes
from copilot.verifier import Verification, verify_frames, verify_template

ROUTES = ("numbers", "documents", "both", "refuse")
STEP_LABELS = {
    "screen": "Screened the request", "route": "Chose a route", "write_sql": "Wrote the SQL",
    "run_sql": "Ran it read-only, inside your store access", "verify": "Checked it with an independent query",
    "explain": "Summarized the result", "retrieve": "Searched policies and agreements",
    "answer_docs": "Read the matching passages", "compose": "Put the answer together", "refuse": "Declined the request",
}
MAX_SQL_ATTEMPTS = 2
DEMO_NO_MATCH = "In demo mode I answer a fixed set of questions. Add a free Groq key to ask anything."
WRITE_REFUSAL = ("I can only read data, so I can't change prices, inventory or records. Changes go through "
                 "the POS, with the approvals the Pricing and Margin Policy requires.")

WRITE_INTENT = re.compile(r"""
    \b(delete|erase|wipe|purge|truncate)\b
  | \bdrop\s+(the\s+)?(table|database|column|view|index|row|record)s?\b
  | \binsert\s+(into|a|an|new)\b
  | \bupdate\s+(the\s+|our\s+|all\s+)?(price|record|table|inventory|database|cost|sku|status|on[\s_]hand)s?\b
  | \b(change|set|edit|modify|overwrite)\s+(the\s+|our\s+|all\s+|its\s+|their\s+)?(shelf\s+)?
      (price|cost|on[\s_]hand|inventory|stock\s+level|status|margin\s+target)s?\b
  | \bmark\s+.{1,40}\s+as\s+(pass|fail|review|out\s+of\s+stock)\b
  | \b(remove|add)\s+.{0,30}\b(from|to)\s+(the\s+)?(database|table|system|pos|sku\s+master|master\s+list)\b
""", re.X | re.I)

DOC_HINTS = ("policy", "policies", "terms", "minimum order", "return", "credit", "standard", "discard",
             "lead time for", "payment terms", "sop", "agreement", "contract", "checklist", "rule", "allowed",
             "supposed to", "procedure", "markdown", "approval", "approve", "banned", "how long can",
             "what happens", "say about", "according to", "clause", "rebate", "substitution", "shelf life",
             "trial", "who owns", "who approves", "restocking fee", "deliver", "delivery", "exclusive",
             "mark down", "marked down", "who reviews", "must", "when should", "when do we", "what should we do",
             "how long does", "how quickly", "how fast", "goes live", "required fields")
# Procedural phrasing ("what must we do") beats number words unless the question clearly asks for figures.
STRONG_DOC = ("must", "when should", "when do we", "what should we do", "how long does", "how long can",
              "how quickly", "how fast", "who reviews", "who approves", "supposed to", "allowed to")
STRONG_NUM = ("how many", "how much", "which", "show", "list", "top", "trend", "revenue", "sales", "still selling")
NUMBER_HINTS = ("how many", "how much", "revenue", "sales", "sold", "selling", "units", "stock", "cover", "waste",
                "top", "trend", "basket", "margin", "member", "transactions", "reorder", "order from", "running low",
                "out of", "gap", "carried", "carry", "local brand", "duplicate", "unmapped", "sku", "codes", "messy",
                "how did", "doing", "performance", "compare", "best", "worst", "daily", "last week", "this week",
                "last 30 days", "percent", "share", "average", "total", "which vendors", "which products",
                "which items", "show me", "list", "master data", "audit", "needs fixing", "below cost",
                "margin floor", "app", "sync", "reprice")

ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """Classify a grocery store manager's question. Reply with exactly one word:
numbers   - needs figures from the sales, inventory, waste, vendor or transaction database
documents - answered by a policy, SOP, checklist or vendor agreement (including vendor terms such as
            minimum orders, payment terms, returns, credits and delivery)
both      - needs database figures AND a policy or agreement
refuse    - asks to change, delete or update data, or has nothing to do with running the stores

Examples:
Q: Which categories are below their margin target this month? -> numbers
Q: What are the return terms in the Palmetto agreement? -> documents
Q: Which vendors do we need to order from, and what are their payment terms? -> both"""),
    ("human", "Q: {question} ->"),
])

SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You write one SQLite query for a grocery chain's analytics database.

{schema}

Rules:
- Reply with the SQL only: no markdown, no explanation.
- Exactly one SELECT (or WITH ... SELECT) statement.
- You can only see these stores: {scope}. Filter with store_id IN (...) if the question names stores.
- Round money to 2 decimals and percentages to 1 decimal. For lists return every matching row (the app
  shows a table and caps at 500); for totals, aggregate.
- Use business-friendly column names (store, product, category, revenue, gross_margin_pct, ...).
- To show a store, join stores and select name || ' (' || store_id || ')' AS store.
- Answer only the data part of the question. Policy or agreement wording is looked up separately,
  so never invent placeholder text columns for it."""),
    ("human", "{question}{retry}"),
])

CHECK_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You double-check SQL. Write a DIFFERENT SQLite query that answers the same question and returns
the same columns in the same order as the first query. Use a genuinely different formulation: subqueries instead
of joins, julianday() arithmetic instead of date(), a different aggregation path. Reply with the SQL only.

{schema}"""),
    ("human", "Question: {question}\n\nFirst query:\n{sql}"),
])

SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a grocery operations analyst writing for a store manager.
Answer the question in 1 to 3 plain sentences using ONLY numbers that appear in the query result.
Name stores like "Flagship (S1)". Write money like $1,912 and large counts with commas.
Answer only the data part; any policy part is answered separately. No markdown, no invented numbers."""),
    ("human", "Question: {question}\n\nQuery result ({rows} rows, showing up to 25), CSV:\n{csv}"),
])


# --------------------------------------------------------------------------- state + answer
class CopilotState(TypedDict, total=False):
    question: str
    picker: list[str]
    route: str
    route_reason: str
    scope: list[str]
    mentioned: list[str]
    notes: Annotated[list[str], operator.add]
    trace: Annotated[list[str], operator.add]
    numbers_mode: str
    template_id: str | None
    prepared: Any
    sql: str | None
    check_sql: str | None
    attempts: int
    sql_error: str | None
    result: QueryResult | None
    verification: Verification | None
    numbers_text: str | None
    chart: dict | None
    docs: list[Document]
    docs_text: str | None
    answer: str


@dataclass
class Answer:
    question: str
    route: str
    text: str
    mode: str
    scope: list[str]
    notes: list[str] = field(default_factory=list)
    sql: str | None = None
    check_sql: str | None = None
    data: pd.DataFrame | None = None
    truncated: bool = False
    chart: dict | None = None
    verification: Verification | None = None
    sources: list[dict] = field(default_factory=list)
    template_id: str | None = None
    numbers_text: str | None = None
    docs_text: str | None = None
    trace: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


# --------------------------------------------------------------------------- helpers
def _hit(phrase: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?:s|es)?(?![a-z0-9])", text) is not None


def keyword_route(question: str) -> str:
    q = question.lower()
    docs = any(_hit(h, q) for h in DOC_HINTS)
    nums = any(_hit(h, q) for h in NUMBER_HINTS)
    if docs and nums:
        strong_doc = any(_hit(h, q) for h in STRONG_DOC)
        strong_num = any(_hit(h, q) for h in STRONG_NUM)
        return "documents" if strong_doc and not strong_num else "both"
    if docs:
        return "documents"
    if nums:
        return "numbers"
    return "unknown"


def detect_stores(question: str) -> list[str]:
    q = question.lower()
    found = []
    for s in config.stores():
        if any(re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", q) for a in s.get("aliases", [])):
            found.append(s["store_id"])
    return found


def clean_sql(text: str) -> str:
    """Strip markdown fences and chatter; keep one statement starting at SELECT/WITH."""
    text = re.sub(r"```(?:sql)?", "", text, flags=re.I).strip()
    m = re.search(r"\b(with|select)\b", text, flags=re.I)
    if m:
        text = text[m.start():]
    return text.split(";")[0].strip()


def guess_chart(df: pd.DataFrame) -> dict | None:
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    numeric = list(df.select_dtypes("number").columns)
    date_col = next((c for c in cols if "date" in c.lower() or c.lower() in ("day", "week", "month")), None)
    if date_col and numeric:
        return {"type": "line", "x": date_col, "y": numeric[0], "color": "store" if "store" in cols else None}
    label = next((c for c in cols if df[c].dtype == object), None)
    if label and numeric and 2 <= len(df) <= 30:
        return {"type": "bar", "x": label, "y": numeric[0]}
    return None


TIME_WORDS = re.compile(r"\b(trend|week|month|days|daily|growth|growing|doing|do|perform|performance|compare|revenue|sales)\b")


def low_history_notes(scope: list[str]) -> list[str]:
    limit = config.load_config()["low_history_days"]
    return [f"{config.store_label(s)} has only {config.days_of_history(s)} days of history, so treat its trends "
            f"as low confidence until day {limit}." for s in config.low_history_stores(scope)]


def history_matters(state: dict) -> bool:
    """Only warn about a young store when the answer depends on its history."""
    if any(s in config.low_history_stores(state.get("scope", [])) for s in state.get("mentioned", [])):
        return True
    t = templates.BY_ID.get(state.get("template_id") or "")
    if t is not None:
        return t.default_days is not None or t.id == "daily_trend"
    return bool(TIME_WORDS.search(state["question"].lower()))


# --------------------------------------------------------------------------- the copilot
class Copilot:
    """One Copilot per role: `allowed_stores` is what this user may ever see."""

    def __init__(self, allowed_stores: list[str], llm=None, retriever: rag.BM25DocRetriever | None = None,
                 db_path=config.DB_PATH):
        self.allowed = [s for s in config.store_ids() if s in allowed_stores]
        self.llm = llm
        self.retriever = retriever or rag.BM25DocRetriever.from_directory()
        self.db_path = db_path
        self.graph = self._build_graph()

    @property
    def mode(self) -> str:
        return "llm" if self.llm is not None else "demo"

    # -- public ----------------------------------------------------------------
    def ask(self, question: str, stores: list[str] | None = None, on_step=None) -> Answer:
        """Run the graph. `on_step(node_name)` is called as each node finishes (the app shows live progress)."""
        t0 = time.perf_counter()
        inputs = {"question": question.strip(), "picker": stores or [], "notes": [], "trace": [], "attempts": 0,
                  "docs": []}
        state: dict = {}
        for mode, chunk in self.graph.stream(inputs, stream_mode=["updates", "values"]):
            if mode == "values":
                state = chunk
            elif on_step is not None:
                for node in chunk:
                    on_step(node)
        result: QueryResult | None = state.get("result")
        docs = state.get("docs") or []
        return Answer(
            question=question, route=state.get("route", "refuse"), text=state.get("answer", ""),
            mode=self.mode if state.get("numbers_mode", self.mode) == self.mode else "demo",
            scope=state.get("scope", []), notes=list(dict.fromkeys(state.get("notes", []))),
            sql=state.get("sql"), check_sql=state.get("check_sql"),
            data=result.df if result is not None else None, truncated=bool(result and result.truncated),
            chart=state.get("chart"), verification=state.get("verification"),
            sources=[{"citation": d.metadata["citation"], "text": rag.section_text(d), "score": d.metadata.get("score"),
                      "status": d.metadata.get("status")} for d in docs],
            template_id=state.get("template_id"), numbers_text=state.get("numbers_text"),
            docs_text=state.get("docs_text"), trace=state.get("trace", []),
            latency_ms=(time.perf_counter() - t0) * 1000)

    def mermaid(self) -> str:
        return self.graph.get_graph().draw_mermaid()

    # -- graph -----------------------------------------------------------------
    def _build_graph(self):
        g = StateGraph(CopilotState)
        for name in ("screen", "route", "write_sql", "run_sql", "verify", "explain", "retrieve", "answer_docs",
                     "compose", "refuse"):
            g.add_node(name, getattr(self, f"_{name}"))
        g.add_edge(START, "screen")
        g.add_conditional_edges("screen", lambda s: "refuse" if s.get("route") == "refuse" else "route",
                                ["refuse", "route"])
        g.add_conditional_edges("route", self._after_route, ["write_sql", "retrieve", "refuse"])
        g.add_conditional_edges("write_sql", lambda s: "run_sql" if s.get("sql") else "explain",
                                ["run_sql", "explain"])
        g.add_conditional_edges("run_sql", self._after_run, ["verify", "write_sql", "explain"])
        g.add_edge("verify", "explain")
        g.add_conditional_edges("explain", lambda s: "retrieve" if s.get("route") == "both" else "compose",
                                ["retrieve", "compose"])
        g.add_edge("retrieve", "answer_docs")
        g.add_edge("answer_docs", "compose")
        g.add_edge("compose", END)
        g.add_edge("refuse", END)
        return g.compile()

    @staticmethod
    def _after_route(state: CopilotState) -> str:
        return {"numbers": "write_sql", "both": "write_sql", "documents": "retrieve"}.get(state["route"], "refuse")

    def _after_run(self, state: CopilotState) -> str:
        if state.get("result") is not None:
            return "verify"
        if state.get("numbers_mode") == "llm" and state.get("attempts", 0) < MAX_SQL_ATTEMPTS:
            return "write_sql"  # the retry loop: the LLM sees its SQL and the error
        return "explain"

    # -- nodes -----------------------------------------------------------------
    def _screen(self, state: CopilotState) -> dict:
        """Write requests are refused here, before any LLM call."""
        if WRITE_INTENT.search(state["question"]):
            return {"route": "refuse", "route_reason": "write request", "answer": WRITE_REFUSAL, "trace": ["screen"]}
        return {"trace": ["screen"]}

    def _route(self, state: CopilotState) -> dict:
        question = state["question"]
        route, reason = None, "keywords"
        if self.llm is not None:
            try:
                raw = (ROUTER_PROMPT | self.llm | StrOutputParser()).invoke({"question": question})
                word = next((w for w in re.findall(r"[a-z]+", raw.lower()) if w in ROUTES), None)
                route, reason = (word, "llm") if word else (None, "keywords (unclear LLM reply)")
            except Exception:  # rate limit, network, bad key: fall back, never crash
                reason = "keywords (LLM unavailable)"
        route = route or keyword_route(question)
        if route == "unknown":
            route = self._tiebreak(question)

        update: dict = {"route": route, "route_reason": reason, "trace": ["route"]}
        if route in ("numbers", "both"):
            mentioned = detect_stores(question)
            allowed_mentioned = [s for s in mentioned if s in self.allowed]
            if mentioned and not allowed_mentioned:
                visible = ", ".join(config.store_label(s) for s in self.allowed)
                outside = f"That data is outside your store access. You can see: {visible}."
                if route == "numbers":
                    return {**update, "route": "refuse", "route_reason": "store access", "answer": outside}
                update.update(route="documents", notes=[outside])  # still answer the policy half
            picker = [s for s in state.get("picker", []) if s in self.allowed]
            scope = allowed_mentioned or picker or self.allowed
            update.update(scope=scope, mentioned=allowed_mentioned)
        else:
            update["scope"] = [s for s in state.get("picker", []) if s in self.allowed] or self.allowed
        return update

    def _tiebreak(self, question: str) -> str:
        """No hint words at all: let the question library and the retriever vote."""
        _, template_score = templates.match(question)
        if template_score >= 6:
            return "numbers"
        return "documents" if self.retriever.invoke(question) else "numbers"

    def _write_sql(self, state: CopilotState) -> dict:
        if self.llm is not None and state.get("numbers_mode") != "demo":
            try:
                return self._write_sql_llm(state)
            except Exception:
                note = "The LLM was unavailable, so this answer comes from the built-in question library."
                return {**self._write_sql_demo(state), "notes": [note]}
        return self._write_sql_demo(state)

    def _write_sql_demo(self, state: CopilotState) -> dict:
        template, _ = templates.match(state["question"])
        base = {"numbers_mode": "demo", "trace": ["write_sql"]}
        if template is None:
            return {**base, "sql": None, "numbers_text": DEMO_NO_MATCH, "template_id": None}
        prepared = templates.prepare(template, state["question"], state["scope"])
        if prepared.problem:
            return {**base, "sql": None, "numbers_text": prepared.problem, "template_id": template.id}
        return {**base, "sql": prepared.sql, "check_sql": prepared.check_sql, "prepared": prepared,
                "template_id": template.id}

    def _write_sql_llm(self, state: CopilotState) -> dict:
        retry = ""
        if state.get("sql_error"):
            retry = (f"\n\nYour previous query failed.\nQuery:\n{state.get('sql')}\nError: {state['sql_error']}\n"
                     "Write a corrected query.")
        raw = (SQL_PROMPT | self.llm | StrOutputParser()).invoke({
            "schema": schema_notes(state["scope"]), "scope": ", ".join(state["scope"]),
            "question": state["question"], "retry": retry})
        return {"numbers_mode": "llm", "sql": clean_sql(raw), "trace": ["write_sql"]}

    def _run_sql(self, state: CopilotState) -> dict:
        db = StoreDB(state["scope"], db_path=self.db_path)
        try:
            return {"result": db.run(state["sql"]), "sql_error": None, "trace": ["run_sql"]}
        except QueryError as err:
            return {"result": None, "sql_error": str(err), "attempts": state.get("attempts", 0) + 1,
                    "trace": ["run_sql"]}

    def _verify(self, state: CopilotState) -> dict:
        db = StoreDB(state["scope"], db_path=self.db_path)
        result: QueryResult = state["result"]
        if state.get("numbers_mode") == "demo":
            t = templates.BY_ID[state["template_id"]]
            return {"verification": verify_template(db, state["check_sql"], t.check_of, result.df, result.truncated),
                    "trace": ["verify"]}
        other, error, check_sql = None, None, None
        try:
            raw = (CHECK_PROMPT | self.llm | StrOutputParser()).invoke({
                "schema": schema_notes(state["scope"]), "question": state["question"], "sql": state["sql"]})
            check_sql = clean_sql(raw)
            other = db.run(check_sql).df
        except QueryError as err:
            error = str(err)
        except Exception as err:  # LLM unavailable
            error = f"LLM unavailable ({type(err).__name__})"
        return {"verification": verify_frames(result.df, other, error, result.truncated), "check_sql": check_sql,
                "trace": ["verify"]}

    def _explain(self, state: CopilotState) -> dict:
        update: dict = {"trace": ["explain"]}
        if history_matters(state):
            update["notes"] = low_history_notes(state["scope"])
        result: QueryResult | None = state.get("result")
        if result is None:
            if state.get("numbers_text"):
                return update  # demo no-match or a scope problem, already worded
            return {**update, "numbers_text": f"I couldn't answer that from the data: {state.get('sql_error')}"}
        df = result.df
        if state.get("numbers_mode") == "demo":
            prepared = state["prepared"]
            return {**update, "numbers_text": templates.summarize(prepared, df), "chart": prepared.template.chart}
        try:
            text = (SUMMARY_PROMPT | self.llm | StrOutputParser()).invoke({
                "question": state["question"], "rows": len(df), "csv": df.head(25).to_csv(index=False)}).strip()
        except Exception:
            text = f"Here is what the data shows ({len(df)} rows)."
        return {**update, "numbers_text": text, "chart": guess_chart(df)}

    def _retrieve(self, state: CopilotState) -> dict:
        query = state["question"]
        result: QueryResult | None = state.get("result")
        if state.get("route") == "both" and result is not None and "vendor" in result.df.columns and len(result.df):
            query += " " + str(result.df["vendor"].iloc[0])  # look up the agreement for the vendor the numbers point to
        return {"docs": self.retriever.invoke(query), "trace": ["retrieve"]}

    def _answer_docs(self, state: CopilotState) -> dict:
        docs = state.get("docs") or []
        if self.llm is not None and docs:
            try:
                return {"docs_text": rag.llm_answer(state["question"], docs, self.llm), "trace": ["answer_docs"]}
            except Exception:
                pass
        return {"docs_text": rag.demo_answer(docs), "trace": ["answer_docs"]}

    def _compose(self, state: CopilotState) -> dict:
        numbers, docs = state.get("numbers_text"), state.get("docs_text")
        route = state["route"]
        if route == "numbers":
            text = numbers
        elif route == "documents":
            text = docs
        else:  # both: keep whichever halves actually found something, and say so when a half came up empty
            parts = [p for p in (numbers, docs) if p and p not in (DEMO_NO_MATCH, rag.NO_DOCUMENT)]
            if parts and docs == rag.NO_DOCUMENT:
                parts.append("No current document covers the policy part of that question.")
            text = "\n\n".join(parts) if parts else (numbers or docs)
        return {"answer": text or DEMO_NO_MATCH, "trace": ["compose"]}

    def _refuse(self, state: CopilotState) -> dict:
        return {"answer": state.get("answer") or WRITE_REFUSAL, "route": "refuse", "trace": ["refuse"]}
