"""
Spec opportunity ranking for omitted cards (Phase-3-only).

Scores thematic fit × demand × supply for cards known to be absent from the decklist.
"""

from __future__ import annotations

from config import (
    ALT_COMMANDER_SPEC_BOOST,
    ALT_COMMANDER_SYNERGY_MIN,
    HISTORICAL_SPIKE_EXCLUDE_THRESHOLD,
    HISTORICAL_SPIKE_MODE,
    HISTORICAL_SPIKE_PENALTY,
    HISTORICAL_SPIKE_PRIOR_WEIGHT,
    PROVEN_OMISSION_SPEC_BOOST,
    RESERVED_SPEC_BOOST,
    SPEC_DEMAND_BLEND,
    SPEC_SUPPLY_EXPONENT,
    VINTAGE_DEMAND_GATE,
    VINTAGE_MIN_SUPPLY,
    VINTAGE_MIN_SYNERGY,
    VINTAGE_SPEC_BOOST,
)
from db.schema import Card
from filters.candidates import can_be_commander


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def is_alternate_commander(
    card: Card,
    commander_name: str,
    synergy_fit: float,
    *,
    min_synergy: float = ALT_COMMANDER_SYNERGY_MIN,
) -> bool:
    """Legendary commander/planeswalker that fits the theme but is not the face commander."""
    if not commander_name or not card:
        return False
    if _normalize_name(card.name) == _normalize_name(commander_name):
        return False
    if card.commander_legal is False:
        return False
    if not can_be_commander(card.type_line):
        return False
    return synergy_fit >= min_synergy


def compute_spec_opportunity_score(
    *,
    synergy_fit: float,
    surprising_omission_score: float,
    p_reprint_adj: float,
    scarcity: float,
    demand: float,
    historical_spike_score: float,
    is_alt_commander: bool,
    spec_supply: float | None = None,
    proven_omission_spike: float = 0.0,
    is_reserved: bool = False,
    weighted_base: float | None = None,
    theme_staple: float = 0.0,
) -> float:
    """
    Spec ranking: weighted feature sum (primary) with demand/supply/vintage boosts.

    When ``weighted_base`` is provided it replaces the legacy omission × supply ×
    demand formula. Reprint likelihood is intentionally excluded (weight 0).
    """
    supply = spec_supply if spec_supply is not None else scarcity

    if weighted_base is not None:
        score = weighted_base
    else:
        p_spec_omission = max(
            surprising_omission_score, synergy_fit * surprising_omission_score
        )
        demand_qualifies_for_supply = demand >= VINTAGE_DEMAND_GATE
        if demand_qualifies_for_supply:
            supply_term = 0.25 + 0.75 * (supply ** SPEC_SUPPLY_EXPONENT)
        else:
            supply_term = 0.25
        if (
            historical_spike_score >= HISTORICAL_SPIKE_EXCLUDE_THRESHOLD
            and demand_qualifies_for_supply
        ):
            supply_term = max(supply_term, 0.25 + 0.75 * (0.55 ** SPEC_SUPPLY_EXPONENT))
        demand_term = SPEC_DEMAND_BLEND + (1 - SPEC_DEMAND_BLEND) * demand
        score = p_spec_omission * supply_term * demand_term * 100

    # Prior-spike directive (config HISTORICAL_SPIKE_MODE): in neutral/negative modes
    # a past spike no longer boosts the score; negative mode applies a mild penalty
    # at the end. Zero the spike-driven inputs so every downstream boost vanishes.
    _prior_spike = historical_spike_score
    if HISTORICAL_SPIKE_MODE != "positive":
        historical_spike_score = 0.0
        proven_omission_spike = 0.0

    demand_scale = min(1.0, demand / max(VINTAGE_DEMAND_GATE, 0.01))
    score *= 1 + HISTORICAL_SPIKE_PRIOR_WEIGHT * historical_spike_score * demand_scale

    vintage_demand_qualifies = demand >= VINTAGE_DEMAND_GATE
    if (
        not is_alt_commander
        and supply >= VINTAGE_MIN_SUPPLY
        and synergy_fit >= VINTAGE_MIN_SYNERGY
        and vintage_demand_qualifies
    ):
        fit_gate = min(1.0, synergy_fit / 0.18)
        if proven_omission_spike >= HISTORICAL_SPIKE_EXCLUDE_THRESHOLD or is_reserved:
            score *= 1 + VINTAGE_SPEC_BOOST * supply * fit_gate
        else:
            score *= 1 + (VINTAGE_SPEC_BOOST * 0.5) * supply * fit_gate

    if (
        not is_alt_commander
        and proven_omission_spike >= HISTORICAL_SPIKE_EXCLUDE_THRESHOLD
        and supply >= VINTAGE_MIN_SUPPLY
        and synergy_fit >= VINTAGE_MIN_SYNERGY
        and vintage_demand_qualifies
    ):
        fit_gate = min(1.0, synergy_fit / 0.18)
        score *= 1 + PROVEN_OMISSION_SPEC_BOOST * proven_omission_spike * supply * fit_gate

    if (
        is_reserved
        and not is_alt_commander
        and synergy_fit >= VINTAGE_MIN_SYNERGY
        and vintage_demand_qualifies
    ):
        score *= 1 + RESERVED_SPEC_BOOST * supply

    if is_alt_commander and synergy_fit >= ALT_COMMANDER_SYNERGY_MIN:
        scarcity_gate = min(1.0, supply / 0.35)
        spike_gate = min(1.0, historical_spike_score / HISTORICAL_SPIKE_EXCLUDE_THRESHOLD)
        alt_weight = max(scarcity_gate, spike_gate)
        # Alt-commander upside is real mainly when the legend is already a known
        # staple of the archetype; non-staple legends were outranking documented
        # golden staples on this multiplier alone.
        staple_gate = 0.4 + 0.6 * min(max(theme_staple, 0.0), 1.0)
        score *= 1 + ALT_COMMANDER_SPEC_BOOST * alt_weight * staple_gate
        if proven_omission_spike >= HISTORICAL_SPIKE_EXCLUDE_THRESHOLD:
            fit_gate = min(1.0, synergy_fit / ALT_COMMANDER_SYNERGY_MIN)
            score *= 1 + 0.35 * proven_omission_spike * fit_gate
        if historical_spike_score >= HISTORICAL_SPIKE_EXCLUDE_THRESHOLD and alt_weight >= 0.5:
            score += synergy_fit * historical_spike_score * 25

    if HISTORICAL_SPIKE_MODE == "negative" and _prior_spike > 0:
        score *= max(0.0, 1.0 - HISTORICAL_SPIKE_PENALTY * _prior_spike)

    return round(score, 2)
