"""Tests for weighted spec model features."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SPEC_FEATURE_WEIGHTS
from db.schema import Card
from engine.weighted_spec_score import compute_weighted_spec_score
from features.builder import FEATURE_COLUMNS, _rarity_score
from features.market_supply import (
    edhrec_supply_proxy,
    normalize_seller_count,
    normalize_visible_inventory,
)
from features.mechanical import (
    MechanicalPoolIndex,
    oracle_text_overlap,
    pool_size_to_score,
)


def test_copy_score_not_in_feature_columns():
    assert "copy_score" not in FEATURE_COLUMNS
    assert "num_printings" not in FEATURE_COLUMNS
    assert "p_reprint_heuristic" not in FEATURE_COLUMNS


def test_surprising_omission_weight_increased():
    assert SPEC_FEATURE_WEIGHTS["surprising_omission_score"] == 13.0
    assert SPEC_FEATURE_WEIGHTS["p_reprint_heuristic"] == 0.0


def test_oracle_text_overlap_myriad():
    commander = "Whenever ~ attacks, it gains myriad until end of turn."
    card = "Gains myriad until end of turn."
    assert oracle_text_overlap(commander, card) >= 0.5


def test_mechanical_pool_size_small_mechanic_scores_high():
    cards = [
        Card(name="A", oracle_text="Myriad"),
        Card(name="B", oracle_text="Gains myriad"),
        Card(name="C", oracle_text="Zombie"),
    ]
    idx = MechanicalPoolIndex(cards)
    assert idx.pool_size("myriad") == 2
    assert pool_size_to_score(2) == 1.0
    score = idx.mechanical_pool_size_score("myriad commander", "Gains myriad")
    assert score >= 0.85


def test_visible_inventory_low_listings_score_high():
    assert normalize_visible_inventory(5) > normalize_visible_inventory(400)
    assert normalize_seller_count(2) > normalize_seller_count(50)


def test_edhrec_proxy_obscure_card_scarcer():
    obscure_vis, obscure_sell = edhrec_supply_proxy(18_000)
    popular_vis, popular_sell = edhrec_supply_proxy(500)
    assert obscure_vis > popular_vis
    assert obscure_sell > popular_sell


def test_rarity_scores_lowered():
    assert _rarity_score("mythic") < 0.7
    assert _rarity_score("common") < 0.2


def test_weighted_score_uses_surprising_omission():
    low = compute_weighted_spec_score({"surprising_omission_score": 0.1})
    high = compute_weighted_spec_score({"surprising_omission_score": 0.9})
    assert high > low
