"""Point-in-time feature construction tests."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from features.builder import FeatureCache
from features.supply import printing_stats_at
from engine.historical_spike_prior import HistoricalSpikePrior, SpikeEvent


class _Printing:
    def __init__(self, released_at, is_commander_precon=False):
        self.released_at = released_at
        self.is_commander_precon = is_commander_precon


def test_printing_stats_respects_as_of_date():
    printings = [
        _Printing(date(2018, 1, 1)),
        _Printing(date(2022, 6, 1)),
    ]
    stats_2019 = printing_stats_at(printings, date(2019, 1, 1))
    assert stats_2019["num_printings"] == 1
    assert stats_2019["last_reprint_days_ago"] == (date(2019, 1, 1) - date(2018, 1, 1)).days

    stats_all = printing_stats_at(printings, date(2025, 1, 1))
    assert stats_all["num_printings"] == 2


def test_historical_rate_excludes_future_decks():
    cache = FeatureCache.__new__(FeatureCache)
    cache.deck_cards = {1: {"Sol Ring"}, 2: {"Sol Ring"}}
    cache.deck_meta = {
        1: {"release_date": date(2018, 1, 1), "colors": ["W"]},
        2: {"release_date": date(2024, 1, 1), "colors": ["W"]},
    }
    cache.decks_by_color = {"W": [1, 2]}

    rate_2019 = cache.historical_rate("Sol Ring", ["W"], date(2019, 1, 1), exclude_deck_id=None)
    assert rate_2019 == 1.0

    rate_2025 = cache.historical_rate("Sol Ring", ["W"], date(2025, 1, 1), exclude_deck_id=2)
    assert rate_2025 == 1.0


def test_spike_prior_score_excludes_future_and_same_deck():
    prior = HistoricalSpikePrior()
    prior.events_by_card["lightning bolt"] = [
        SpikeEvent(
            card_name="Lightning Bolt",
            color_key="R",
            colors=["R"],
            spike_pct=0.5,
            spike_usd=2.0,
            deck_id=10,
            deck_name="Old",
            release_date=date(2019, 1, 1),
            attribution="announcement",
        ),
        SpikeEvent(
            card_name="Lightning Bolt",
            color_key="R",
            colors=["R"],
            spike_pct=0.8,
            spike_usd=5.0,
            deck_id=20,
            deck_name="Future",
            release_date=date(2024, 1, 1),
            attribution="announcement",
        ),
    ]

    score = prior.score(
        "Lightning Bolt",
        ["R"],
        as_of_date=date(2020, 1, 1),
        exclude_deck_id=10,
    )
    assert score == 0.0

    score_past = prior.score(
        "Lightning Bolt",
        ["R"],
        as_of_date=date(2025, 1, 1),
        exclude_deck_id=20,
    )
    assert score_past > 0


def test_loose_spike_excludes_pre_release():
    from backtester.spike_attribution import is_loose_omission_spike

    release = date(2024, 2, 14)
    assert not is_loose_omission_spike(
        date(2024, 2, 1),
        "Some Set",
        release,
        deck_colors=["W", "U", "B"],
        card_color_identity=["W", "U", "B"],
    )
    assert is_loose_omission_spike(
        date(2024, 2, 20),
        "Some Set",
        release,
        deck_colors=["W", "U", "B"],
        card_color_identity=["W", "U", "B"],
    )
