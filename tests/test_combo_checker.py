"""Commander Spellbook infinite combo detection tests."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.combo_checker import ComboChecker, ComboLoopInfo


def _variant(uses, produces=("Infinite damage",)):
    return {
        "uses": [{"card": {"name": n}} for n in uses],
        "produces": [{"feature": {"name": p}} for p in produces],
    }


def test_loop_with_commander_anchor():
    checker = ComboChecker(use_cache=False, fetch_live=False)

    def fake_fetch(card_name, *, limit=40):
        if card_name == "Village Bell-Ringer":
            return [_variant(["Kiki-Jiki, Mirror Breaker", "Village Bell-Ringer"])]
        return []

    with patch.object(checker, "fetch_infinite_variants", side_effect=fake_fetch):
        info = checker.loop_with_anchors(
            "Village Bell-Ringer",
            ["Kiki-Jiki, Mirror Breaker"],
        )
    assert info.has_infinite_loop is True
    assert "Kiki-Jiki, Mirror Breaker" in info.loop_partners


def test_no_loop_without_shared_anchor():
    checker = ComboChecker(use_cache=False, fetch_live=False)

    def fake_fetch(card_name, *, limit=40):
        return [_variant(["Unfulfilled Desires", "Feast of Sanity", "Ashnod's Altar"])]

    with patch.object(checker, "fetch_infinite_variants", side_effect=fake_fetch):
        info = checker.loop_with_anchors(
            "Unfulfilled Desires",
            ["Temmet, Naktamun's Will"],
        )
    assert info == ComboLoopInfo(False, ())
