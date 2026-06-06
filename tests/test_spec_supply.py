"""Tests for spec supply scoring and ranking."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.schema import Card, CommanderDeck
from engine.historical_spike_prior import HistoricalSpikePrior
from engine.opportunity_score import compute_spec_opportunity_score
from features.builder import FEATURE_COLUMNS, compute_all_features
from features.supply import compute_spec_supply_score


def test_vintage_single_printing_scores_high():
    score = compute_spec_supply_score(
        num_printings=1,
        last_reprint_days_ago=10356,
        is_reserved=True,
        first_printing_date=date(1996, 10, 8),
        as_of_date=date(2025, 2, 14),
    )
    assert score >= 0.9


def test_recent_bulk_rare_scores_lower():
    score = compute_spec_supply_score(
        num_printings=5,
        last_reprint_days_ago=501,
        is_reserved=False,
        first_printing_date=date(2023, 1, 1),
        as_of_date=date(2025, 2, 14),
    )
    assert score < 0.35


def test_vintage_upgrade_outscores_bulk_alt_commander():
    vintage = compute_spec_opportunity_score(
        synergy_fit=0.18,
        surprising_omission_score=0.75,
        p_reprint_adj=0.05,
        scarcity=1.0,
        demand=0.25,
        historical_spike_score=0.55,
        is_alt_commander=False,
        spec_supply=0.95,
        proven_omission_spike=0.55,
        is_reserved=True,
    )
    bulk_alt = compute_spec_opportunity_score(
        synergy_fit=0.25,
        surprising_omission_score=0.92,
        p_reprint_adj=0.7,
        scarcity=0.14,
        demand=0.27,
        historical_spike_score=0.0,
        is_alt_commander=True,
        spec_supply=0.14,
    )
    assert vintage > bulk_alt


def test_spec_supply_score_in_feature_columns():
    assert "spec_supply_score" in FEATURE_COLUMNS


def test_compute_all_features_includes_spec_supply():
    card = Card(
        name="Unfulfilled Desires",
        reserved=True,
        color_identity=["B"],
        oracle_text="Pay 1 life, Sacrifice a creature: Draw a card.",
    )
    deck = CommanderDeck(
        id=1,
        colors=["W", "U", "B"],
        release_date=date(2025, 2, 14),
        decklist_reveal_date=date(2025, 2, 10),
        commander_text="Zombies and discard",
    )

    class _Cache:
        deck_cards = {1: set()}

        def printing_stats(self, _name, _as_of):
            return {
                "num_printings": 1,
                "num_precon_printings": 0,
                "last_reprint_days_ago": 10_000,
                "first_printing_date": date(1996, 10, 8),
            }

        def historical_rate(self, *_args, **_kwargs):
            return 0.0

        def reprint_likelihood(self, *_args, **_kwargs):
            return 0.05

        def card_in_set_at(self, *_args, **_kwargs):
            return False

    import engine.historical_spike_prior as hsp

    hsp._cached_prior = HistoricalSpikePrior()
    try:
        feats = compute_all_features(None, card, deck, cache=_Cache())
    finally:
        hsp._cached_prior = None
    assert feats["spec_supply_score"] >= 0.9
    assert feats["surprising_omission_score"] > 0
