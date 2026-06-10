"""Tests for volume capture (printing selection) and velocity/ignition scoring."""

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.opportunity_score as opp
from engine.opportunity_score import compute_spec_opportunity_score
from engine.volume import VolumeCache
from ingest.snapshot_volume import build_pool, variant_of


# ── variant_of: only genuine premium treatments are variants ──────────────────

def test_variant_of_plain_standard():
    assert variant_of({"border_color": "black"}) == "standard"


def test_variant_of_legendary_frame_is_standard():
    # ordinary frame_effects (legendary/nyx/miracle) must NOT count as variants
    assert variant_of({"frame_effects": ["legendary"], "border_color": "black"}) == "standard"
    assert variant_of({"frame_effects": ["nyxtouched", "legendary"]}) == "standard"


def test_variant_of_premium_treatments():
    assert variant_of({"border_color": "borderless"}) == "borderless"
    assert variant_of({"frame_effects": ["extendedart"]}) == "extended art"
    assert variant_of({"frame_effects": ["showcase"]}) == "showcase"
    assert variant_of({"promo": True}) == "promo"
    assert variant_of({"full_art": True}) == "full art"


# ── build_pool: cheapest STANDARD printing, fallback to cheapest overall ───────

def _card(name, tid, usd, *, fx=None, border="black", promo=False, set_="tst"):
    return {
        "name": name, "tcgplayer_id": tid, "prices": {"usd": str(usd)},
        "games": ["paper"], "layout": "normal", "type_line": "Creature — Elf",
        "frame_effects": fx or [], "border_color": border, "promo": promo,
        "set": set_, "edhrec_rank": 100,
    }


def _bulk(cards):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(cards, f)
    f.close()
    return f.name


def test_build_pool_prefers_cheapest_standard_over_cheaper_variant():
    # A cheap showcase ($1) and a pricier standard ($2) → we must pick the standard.
    cards = [
        _card("Bloomy", 111, 1.00, fx=["showcase"]),
        _card("Bloomy", 222, 2.00),               # standard
    ]
    pool, pins = build_pool(_bulk(cards), pinned={})
    assert pins["Bloomy"]["tcgplayer_id"] == 222
    assert pins["Bloomy"]["variant"] == "standard"


def test_build_pool_falls_back_to_cheapest_when_no_standard():
    cards = [
        _card("Onlyfoil", 333, 5.00, border="borderless"),
        _card("Onlyfoil", 444, 9.00, fx=["extendedart"]),
    ]
    pool, pins = build_pool(_bulk(cards), pinned={})
    assert pins["Onlyfoil"]["tcgplayer_id"] == 333  # cheapest of the variants


def test_build_pool_respects_existing_pin():
    cards = [_card("Pinned", 555, 1.00), _card("Pinned", 666, 2.00)]
    pool, pins = build_pool(_bulk(cards), pinned={"Pinned": 999})
    assert ("Pinned", 999) in pool          # uses the pinned id, not a fresh pick
    assert "Pinned" not in pins             # already pinned → not re-pinned


# ── VolumeCache: sell-through velocity + ignition ─────────────────────────────

def _series(weekly_qty, price=2.0, start=date(2026, 1, 1)):
    return [(start + timedelta(weeks=i), float(q), price) for i, q in enumerate(weekly_qty)]


def test_velocity_rising_demand_high():
    # 12 baseline weeks @10, then 4 recent weeks @40 → strong acceleration
    vc = VolumeCache(series={"Hot": _series([10] * 12 + [40] * 4)})
    assert vc.velocity_factor("Hot", date(2027, 1, 1)) >= 0.9


def test_velocity_flat_is_zero():
    vc = VolumeCache(series={"Flat": _series([20] * 16)})
    assert vc.velocity_factor("Flat", date(2027, 1, 1)) == 0.0


def test_velocity_thin_volume_is_zero():
    # recent mean below the min-units floor → not a real signal
    vc = VolumeCache(series={"Thin": _series([1] * 12 + [3] * 4)})
    assert vc.velocity_factor("Thin", date(2027, 1, 1)) == 0.0


def test_velocity_point_in_time_excludes_future():
    vc = VolumeCache(series={"Hot": _series([10] * 12 + [40] * 4)})
    # anchor before the data starts → no buckets → neutral 0
    assert vc.velocity_factor("Hot", date(2025, 1, 1)) == 0.0


def test_ignition_cheap_rising_positive():
    vc = VolumeCache(series={"Hot": _series([10] * 12 + [40] * 4, price=2.0)})
    ign = vc.ignition_score("Hot", date(2027, 1, 1), printing_scarcity=0.8, synergy=0.5)
    assert ign > 0.3


def test_ignition_price_gated_to_zero():
    vc = VolumeCache(series={"Pricey": _series([10] * 12 + [40] * 4, price=25.0)})
    assert vc.ignition_score("Pricey", date(2027, 1, 1), 0.8, 0.5) == 0.0


def test_ignition_missing_card_is_neutral():
    vc = VolumeCache(series={})
    assert vc.ignition_score("Ghost", date(2027, 1, 1), 0.8, 0.5) == 0.0


# ── prior-spike modes: positive boosts, neutral removes, negative penalizes ───

def _score():
    return compute_spec_opportunity_score(
        synergy_fit=0.30, surprising_omission_score=0.5, p_reprint_adj=0.1,
        scarcity=0.6, demand=0.4, historical_spike_score=0.6, is_alt_commander=False,
        spec_supply=0.9, proven_omission_spike=0.6, is_reserved=False, weighted_base=100.0,
    )


def test_spike_modes_ordering(monkeypatch):
    monkeypatch.setattr(opp, "HISTORICAL_SPIKE_MODE", "positive")
    pos = _score()
    monkeypatch.setattr(opp, "HISTORICAL_SPIKE_MODE", "neutral")
    neu = _score()
    monkeypatch.setattr(opp, "HISTORICAL_SPIKE_MODE", "negative")
    neg = _score()
    # a card with a strong prior spike should rank highest under positive,
    # lower under neutral (boost removed), lowest under negative (penalized).
    assert pos > neu > neg
    assert neg < 100.0  # negative drops it below the weighted base
