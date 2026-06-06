"""Per-card functional role tags from oracle text (shared with deck feature extraction)."""

from __future__ import annotations

from typing import List, Optional

from analytics.deck_features import (
    DRAW_PATTERNS,
    PROTECTION_PATTERNS,
    RAMP_PATTERNS,
    RECURSION_PATTERNS,
    REMOVAL_PATTERNS,
    SYNERGY_PATTERNS,
    TUTOR_PATTERNS,
    WIN_PATTERNS,
    _matches_patterns,
)

ROLE_PATTERNS: dict[str, list] = {
    "ramp": RAMP_PATTERNS,
    "removal": REMOVAL_PATTERNS,
    "draw": DRAW_PATTERNS,
    "tutors": TUTOR_PATTERNS,
    "recursion": RECURSION_PATTERNS,
    "protection": PROTECTION_PATTERNS,
    "win_conditions": WIN_PATTERNS,
    "synergy": SYNERGY_PATTERNS,
}

ROLE_LABELS = {
    "ramp": "Ramp",
    "removal": "Removal",
    "draw": "Card draw",
    "tutors": "Tutors",
    "recursion": "Recursion",
    "protection": "Protection",
    "win_conditions": "Win condition",
    "synergy": "Synergy",
    "utility": "Utility",
}

PURPOSE_DISPLAY_ORDER = [
    "Ramp",
    "Removal",
    "Card draw",
    "Tutors",
    "Recursion",
    "Protection",
    "Synergy",
    "Win condition",
    "Utility",
]


def infer_card_purposes(oracle_text: Optional[str]) -> List[str]:
    """Return human-readable roles for a card; may be empty (non-land without text match)."""
    if not oracle_text:
        return []
    found = []
    for key, patterns in ROLE_PATTERNS.items():
        if _matches_patterns(oracle_text, patterns):
            found.append(ROLE_LABELS[key])
    return found


def purposes_label(oracle_text: Optional[str], type_line: Optional[str] = None) -> str:
    roles = infer_card_purposes(oracle_text)
    if roles:
        return ", ".join(roles)
    tl = (type_line or "").lower()
    if "land" in tl:
        return "Mana base"
    if "creature" in tl:
        return "Creature"
    if "artifact" in tl:
        return "Artifact"
    if "enchantment" in tl:
        return "Enchantment"
    if "instant" in tl:
        return "Instant"
    if "sorcery" in tl:
        return "Sorcery"
    if "planeswalker" in tl:
        return "Planeswalker"
    return "General"
