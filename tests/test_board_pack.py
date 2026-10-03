"""The board pack, planning/board_pack.pptx.

Every number on the slides is worked out two ways while the pack is built.
These tests rerun that and check the committed slides carry today's numbers.
"""
from pathlib import Path

import pytest

pptx = pytest.importorskip("pptx")
PLANNING = Path(__file__).resolve().parents[1] / "planning"
PACK = PLANNING / "board_pack.pptx"


@pytest.fixture(scope="module")
def built():
    import sys
    sys.path.insert(0, str(PLANNING))
    import build_board_pack
    return build_board_pack


@pytest.fixture(scope="module")
def slides():
    return [" ".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)
            for slide in pptx.Presentation(PACK).slides]


def test_every_number_agrees_two_ways(built):
    f = built.figures()            # raises SystemExit if any pair disagrees
    assert f.checks >= 15
    assert f["sales_now"] > f["sales_before"] > 0
    assert 0 < f["leak_price"] < f["leak_waste"]


def test_committed_slides_carry_todays_numbers(built, slides):
    f = built.figures()
    assert len(slides) == 3
    assert built.money(f["sales_now"]) in slides[0]
    assert built.money(f["leak_waste"] + f["leak_price"] + f["leak_cost"]) in slides[1]
    assert built.money(f["slow"]) in slides[2]


def test_no_em_dashes(slides):
    assert all("\u2014" not in s for s in slides)
