"""
Weighted linear spec score from normalized features.

Phase-3-only: weights emphasize surprising omission, oracle overlap, and market
supply — not deck-prediction signals (skeleton, slots, reprint likelihood).
"""

from __future__ import annotations

from config import SPEC_FEATURE_WEIGHTS


def compute_weighted_spec_score(features: dict) -> float:
    """
    Sum(weight × normalized_feature) for all configured weights.

    Features are expected in 0–1 (or 0/1 flags). ``last_reprint_days_ago`` is
    inverted so older = higher score.
    """
    total = 0.0
    for name, weight in SPEC_FEATURE_WEIGHTS.items():
        if weight <= 0:
            continue
        value = _normalize_feature(name, features.get(name))
        total += weight * value
    return round(total, 2)


def _normalize_feature(name: str, raw) -> float:
    if raw is None:
        return 0.0
    if name == "last_reprint_days_ago":
        days = float(raw)
        return min(days / 7300.0, 1.0)
    if name == "edhrec_rank":
        rank = float(raw)
        return 1.0 - min(rank / 25_000.0, 1.0)
    if name == "entry_price_penalty":
        return float(raw)
    if name in ("is_reserved", "single_printing_flag", "is_same_product_omission", "is_mana_fix_omission"):
        return float(raw)
    try:
        return min(max(float(raw), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0
