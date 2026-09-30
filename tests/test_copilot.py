"""Store Copilot tests. No API key needed: the LLM path uses FakeListChatModel."""
import pytest

from copilot.db import QueryRejected, StoreDB, guard_sql

# --------------------------------------------------------------------------- safety layer
BLOCKED_SQL = {
    "delete": "DELETE FROM sales_daily",
    "drop": "DROP TABLE stores",
    "update": "UPDATE sku_master SET unit_price = 0.01",
    "two statements": "SELECT 1; SELECT 2",
    "pragma": "PRAGMA table_info(sales_daily)",
    "schema-qualified": "SELECT * FROM main.sales_daily",
    "sqlite_master": "SELECT name, sql FROM sqlite_master",
    "attach": "ATTACH DATABASE 'other.db' AS other",
}


@pytest.mark.parametrize("name", BLOCKED_SQL)
def test_guard_rejects(name):
    with pytest.raises(QueryRejected):
        guard_sql(BLOCKED_SQL[name])


def test_guard_allows_ctes_and_selects():
    sql = "WITH t AS (SELECT store_id, SUM(revenue) r FROM sales_daily GROUP BY 1) SELECT * FROM t"
    assert guard_sql(sql) == sql


def test_store_scope_hides_other_stores():
    db = StoreDB(["S2"])
    asked_for_s1 = db.run("SELECT store_id, COUNT(*) AS n FROM sales_daily WHERE store_id = 'S1' GROUP BY 1")
    assert asked_for_s1.df.empty
    everything = db.run("SELECT DISTINCT store_id FROM sales_daily")
    assert everything.df["store_id"].tolist() == ["S2"]
    # derived views are scoped too
    assert db.run("SELECT DISTINCT store_id FROM sku_velocity_14d").df["store_id"].tolist() == ["S2"]


def test_authorizer_blocks_bypass_even_without_guard():
    """Defense in depth: skip the sqlglot guard and go straight at SQLite."""
    db = StoreDB(["S2"])
    for sql in ("SELECT COUNT(*) FROM main.sales_daily",
                "SELECT store_id, SUM(units_14d) FROM main.sku_velocity_14d GROUP BY 1",
                "SELECT * FROM sqlite_master"):
        with pytest.raises(QueryRejected):
            db._execute(sql)


def test_row_cap_and_time_budget():
    db = StoreDB(["S1"], time_budget_s=0.5)
    result = db.run("SELECT * FROM sales_daily")
    assert len(result.df) == 500 and result.truncated
    with pytest.raises(QueryRejected, match="longer than"):
        db.run("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT MAX(x) FROM c")


# --------------------------------------------------------------------------- templates + verifier
import time  # noqa: E402

from copilot import templates  # noqa: E402
from copilot.verifier import compare_frames, verify_template  # noqa: E402

ALL_STORES = ["S1", "S2", "S3"]


@pytest.mark.parametrize("template", templates.TEMPLATES, ids=lambda t: t.id)
def test_template_example_matches_and_verifies(template):
    matched, _ = templates.match(template.example)
    assert matched is template, f"'{template.example}' matched {matched and matched.id}"
    db = StoreDB(ALL_STORES)
    prepared = templates.prepare(template, template.example, ALL_STORES)
    t0 = time.perf_counter()
    result = db.run(prepared.sql)
    verification = verify_template(db, prepared.check_sql, template.check_of, result.df, result.truncated)
    elapsed = time.perf_counter() - t0
    assert verification.status == "verified", verification.checks
    assert elapsed < 0.5, f"{template.id} took {elapsed:.2f}s"
    assert templates.summarize(prepared, result.df)


def test_silent_error_is_caught_by_independent_check():
    """A query that runs fine but doubles revenue must not be labelled verified."""
    db = StoreDB(ALL_STORES)
    t = templates.BY_ID["revenue_by_store"]
    prepared = templates.prepare(t, t.example, ALL_STORES)
    wrong = db.run(prepared.sql.replace("ROUND(SUM(s.revenue), 2) AS revenue", "ROUND(SUM(s.revenue) * 2, 2) AS revenue"))
    assert verify_template(db, prepared.check_sql, t.check_of, wrong.df).status == "disagree"


def test_compare_frames_tolerance():
    import pandas as pd
    a = pd.DataFrame({"store": ["S1", "S2"], "revenue": [100.0, 200.0]})
    assert compare_frames(a, a.assign(revenue=[100.4, 199.5]))[0]          # within 0.5%
    assert not compare_frames(a, a.assign(revenue=[100.0, 400.0]))[0]      # silent error


# --------------------------------------------------------------------------- documents
from copilot.rag import NO_DOCUMENT, BM25DocRetriever, demo_answer  # noqa: E402

RETRIEVER = BM25DocRetriever.from_directory()


@pytest.mark.parametrize("question,title,section", [
    ("What's the minimum order for Palmetto Ranch?", "Vendor Agreement: Palmetto Ranch Direct", "Minimum order and payment terms"),
    ("How long can hot bar food sit out before we discard it?", "Perishables and Waste SOP", "Hot bar"),
    ("What credit do we get from Biscayne for damaged produce?", "Vendor Agreement: Biscayne Fresh Co.", "Returns and credits"),
    ("What happens to unsold Seagrape juice?", "Vendor Agreement: Seagrape Juice Works", "Returns and credits"),
])
def test_retriever_finds_right_section(question, title, section):
    top = RETRIEVER.invoke(question)[0]
    assert (top.metadata["title"], top.metadata["section"]) == (title, section)
    assert top.metadata["status"] == "current"


def test_retriever_ignores_superseded_unless_asked():
    current = RETRIEVER.invoke("What's the minimum order for Palmetto Ranch?")
    assert all(d.metadata["status"] == "current" for d in current)
    assert "$750" in current[0].page_content
    old = RETRIEVER.invoke("What was the minimum order in the old Palmetto agreement?")
    assert old[0].metadata["status"] == "superseded" and "$500" in old[0].page_content


def test_off_topic_question_returns_no_document():
    docs = RETRIEVER.invoke("What is our parental leave policy?")
    assert docs == []
    assert demo_answer(docs) == NO_DOCUMENT == "No current document covers that."


# --------------------------------------------------------------------------- router + engine (demo mode, no key)
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402

from copilot.engine import Copilot  # noqa: E402

OWNER = Copilot(ALL_STORES)


@pytest.mark.parametrize("question", [
    "Delete all the Fail items from the database",
    "Change the price of avocados to $1.99",
    "Drop table sales_daily",
    "Update the inventory for store 2",
])
def test_write_requests_are_refused(question):
    answer = OWNER.ask(question)
    assert answer.route == "refuse"
    assert answer.trace == ["screen", "refuse"]  # refused before any router or LLM call


def test_store_3_question_gets_scope_and_low_history_note():
    answer = OWNER.ask("How did store 3 do last week?")
    assert answer.scope == ["S3"] and answer.route == "numbers"
    assert answer.verification.status == "verified"
    assert any("19 days of history" in n for n in answer.notes)


def test_store_manager_cannot_see_other_stores():
    manager = Copilot(["S2"])
    assert manager.ask("How did the flagship do last month?").route == "refuse"
    answer = manager.ask("What was revenue by store over the last 30 days?")
    assert answer.scope == ["S2"] and set(answer.data["store"]) == {"Store 2 (S2)"}


def test_mixed_question_answers_numbers_and_cites_matching_vendor():
    answer = OWNER.ask("Which vendors should we order from, and what are their payment terms?")
    assert answer.route == "both" and answer.verification.status == "verified"
    top_vendor = answer.data["vendor"].iloc[0]
    assert top_vendor in answer.sources[0]["citation"]


def test_demo_mode_no_match_message():
    assert OWNER.ask("What's the weather like?").text.startswith("In demo mode I answer a fixed set of questions")


def test_buttons_use_the_question_library_even_with_a_model():
    """Suggestion buttons stay instant and verified: no LLM call even when a model is configured."""
    copilot = Copilot(ALL_STORES, llm=FakeListChatModel(responses=["this should never be used"]))
    answer = copilot.ask("Which prices fell under the margin floor?", use_llm=False)
    assert answer.mode == "demo" and answer.template_id == "price_exceptions"
    assert answer.verification.status == "verified"


# --------------------------------------------------------------------------- LLM path with a fake model (no key)
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402

REVENUE_SQL = """SELECT st.name || ' (' || st.store_id || ')' AS store, ROUND(SUM(s.revenue), 2) AS revenue
FROM sales_daily s JOIN stores st ON st.store_id = s.store_id
WHERE s.sale_date > date((SELECT MAX(sale_date) FROM sales_daily), '-7 days')
GROUP BY st.store_id ORDER BY revenue DESC"""
REVENUE_CHECK_SQL = """SELECT (SELECT name FROM stores t WHERE t.store_id = s.store_id) || ' (' || s.store_id || ')' AS store,
ROUND(SUM(s.revenue), 2) AS revenue FROM sales_daily s
WHERE julianday(s.sale_date) > julianday((SELECT MAX(sale_date) FROM sales_daily)) - 7
GROUP BY s.store_id ORDER BY revenue DESC"""
QUESTION = "What was revenue by store last week?"


def fake_copilot(*responses):
    return Copilot(ALL_STORES, llm=FakeListChatModel(responses=list(responses)))


def test_llm_path_agreeing_check_is_verified():
    answer = fake_copilot("numbers", REVENUE_SQL, REVENUE_CHECK_SQL, "Flagship (S1) led last week.").ask(QUESTION)
    assert answer.mode == "llm" and answer.route == "numbers"
    assert answer.verification.status == "verified"
    assert answer.text == "Flagship (S1) led last week."


def test_llm_path_silent_error_is_flagged():
    doubled = REVENUE_SQL.replace("ROUND(SUM(s.revenue), 2)", "ROUND(SUM(s.revenue) * 2, 2)")
    answer = fake_copilot("numbers", doubled, REVENUE_CHECK_SQL, "Revenue was high.").ask(QUESTION)
    assert answer.data is not None and not answer.data.empty  # it ran fine...
    assert answer.verification.status == "disagree"           # ...but the checks caught it


def test_llm_path_blocks_delete_then_retries():
    answer = fake_copilot("numbers", "DELETE FROM sales_daily", REVENUE_SQL, REVENUE_CHECK_SQL, "OK.").ask(QUESTION)
    assert answer.trace.count("write_sql") == 2 and answer.trace.count("run_sql") == 2
    assert answer.sql.startswith("SELECT") and answer.verification.status == "verified"


def test_clean_sql_strips_fences_and_chatter():
    from copilot.engine import clean_sql
    assert clean_sql("Here you go:\n```sql\nSELECT 1;\n```\nHope that helps") == "SELECT 1"


# --------------------------------------------------------------------------- dashboard
from copilot import dashboard  # noqa: E402


def test_every_kpi_tile_is_verified():
    tiles = dashboard.kpis(dashboard.overview(ALL_STORES))
    assert len(tiles) == 6 and all(t["status"] == "verified" for t in tiles)


def test_brief_items_link_to_questions_the_copilot_answers():
    items = dashboard.brief(dashboard.overview(ALL_STORES))
    assert items
    for it in items:
        answer = OWNER.ask(it["question"])
        assert answer.route in ("numbers", "both") and answer.verification.status == "verified", it["question"]
