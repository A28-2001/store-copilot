"""The Story tab's content: built only from checked numbers and the planning model."""
from pathlib import Path

from copilot import story
from copilot.figures import figures, money

BOOK = Path(__file__).resolve().parents[1] / "planning" / "store_planning_model.xlsx"


def test_story_carries_the_checked_numbers():
    f = figures()
    page = story.build(f, story.plan_numbers(BOOK))
    assert money(f["sales_now"]) in page
    assert page.count('class="step"') == 4 and page.count('class="fig"') == 4
    assert page.count("data-goto=") == 4
    assert "—" not in page


def test_plan_numbers_come_from_the_workbook():
    plan = story.plan_numbers(BOOK)
    assert set(plan) == {"payback", "invest", "funding", "sqft"}
    assert plan["invest"] > plan["funding"] > 0
