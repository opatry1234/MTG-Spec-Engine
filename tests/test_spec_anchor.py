"""Tests for spec anchor date and reveal eligibility."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.schema import CommanderDeck
from engine.spec_eligibility import (
    spec_anchor_date,
    was_spec_eligible_at_reveal,
)


def test_spec_anchor_prefers_reveal_date():
    deck = CommanderDeck(release_date=date(2025, 5, 13), decklist_reveal_date=date(2025, 2, 14))
    assert spec_anchor_date(deck) == date(2025, 2, 14)


def test_spec_anchor_falls_back_to_release():
    deck = CommanderDeck(release_date=date(2025, 5, 13), decklist_reveal_date=None)
    assert spec_anchor_date(deck) == date(2025, 5, 13)


def test_eligibility_rejects_post_reveal_printings():
    earliest = {"new card": date(2025, 3, 1)}
    assert not was_spec_eligible_at_reveal("New Card", date(2025, 2, 14), earliest)
