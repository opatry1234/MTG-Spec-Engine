"""Tests for Commander ban history and format-driven spike detection."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.spike_attribution import is_precon_attributed_spike
from engine.commander_legality import (
    clear_format_events_cache,
    is_format_driven_spike,
    was_commander_legal_at,
)


def setup_function():
    clear_format_events_cache()


def test_gifts_banned_at_dft_spoiler():
    assert not was_commander_legal_at("Gifts Ungiven", date(2025, 2, 14))


def test_gifts_legal_after_unban():
    assert was_commander_legal_at("Gifts Ungiven", date(2025, 4, 22))
    assert was_commander_legal_at("Gifts Ungiven", date(2025, 5, 13))


def test_gifts_may_spike_is_format_driven():
    assert is_format_driven_spike("Gifts Ungiven", date(2025, 5, 13))


def test_gifts_shelf_spike_not_precon_attributed():
    assert not is_precon_attributed_spike(
        date(2025, 5, 13),
        "Signature Spellbook: Jace",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        commander_spike_set="Commander: Aetherdrift",
        card_name="Gifts Ungiven",
    )


def test_braids_may_spike_is_format_driven():
    assert is_format_driven_spike("Braids, Cabal Minion", date(2025, 5, 13))
    assert not is_precon_attributed_spike(
        date(2025, 5, 13),
        "Odyssey",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        commander_spike_set="Commander: Aetherdrift",
        card_name="Braids, Cabal Minion",
    )
