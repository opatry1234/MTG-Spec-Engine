"""
Candidate pool filtering for Commander precon predictions.
"""

from db.schema import Card
from features.mechanical import color_identity_match

# Scryfall layouts that are never real deck inclusions
BLOCKED_LAYOUTS = {
    "token",
    "emblem",
    "vanguard",
    "scheme",
    "plane",
    "planar",
    "art_series",
    "double_faced_token",
    "reversible_card",
    "mystery",
}

BLOCKED_TYPE_KEYWORDS = (
    "vanguard",
    "plane —",
    "scheme",
    "conspiracy",
    "token creature",
    "token artifact",
    "token enchantment",
    "stickers",
)


def can_be_commander(type_line: str | None) -> bool:
    """
    True when a card's type line includes a face that can be a Commander.

    Commanders must be legendary creatures or planeswalkers (not artifacts,
    enchantments, battles alone, etc.). Double-faced cards are checked per face.
    """
    tl = (type_line or "").strip()
    if not tl:
        return False
    for segment in tl.split("//"):
        seg = segment.strip().lower()
        if "legendary" not in seg:
            continue
        if "creature" in seg or "planeswalker" in seg:
            return True
    return False


def _passes_base_card_filters(card: Card) -> bool:
    """Layout/type/commander legality — no reserved-list or color checks."""
    if card.commander_legal is False:
        return False

    layout = (card.layout or "").lower()
    if layout in BLOCKED_LAYOUTS:
        return False

    type_line = (card.type_line or "").lower()
    if any(kw in type_line for kw in BLOCKED_TYPE_KEYWORDS):
        return False

    if card.commander_legal is None and type_line == "vanguard":
        return False

    return True


def is_commander_candidate(card: Card) -> bool:
    """True if this card could plausibly appear as a precon deck inclusion."""
    if card.reserved:
        return False
    return _passes_base_card_filters(card)


def is_color_legal(card: Card, deck_colors: list) -> bool:
    """
    Commander color identity check.

    Cards with empty identity are legal only if they are truly colorless
    (no colored mana symbols). Vanguard/plane cards with empty identity
    are handled by is_commander_candidate().
    """
    deck_colors = deck_colors or []
    identity = card.color_identity or []

    if identity:
        return color_identity_match(identity, deck_colors)

    # Colorless card — verify no colored mana pips in cost
    mana = card.mana_cost or ""
    colored = {"W", "U", "B", "R", "G"}
    if any(f"{{{c}}}" in mana for c in colored):
        # Has colored mana but empty identity (data issue) — derive from colors field
        card_colors = card.colors or []
        if card_colors:
            return color_identity_match(card_colors, deck_colors)
        return False

    return True  # genuinely colorless


def passes_candidate_filters(card: Card, deck_colors: list) -> bool:
    """Probable-deck / inclusion pool — excludes reserved list."""
    return is_commander_candidate(card) and is_color_legal(card, deck_colors)


def passes_spec_candidate_filters(card: Card, deck_colors: list) -> bool:
    """
    Spec-target pool — includes reserved vintage cards (e.g. Unfulfilled Desires)
    that can spike on supply shock when omitted from a precon.
    """
    return _passes_base_card_filters(card) and is_color_legal(card, deck_colors)


def is_golden_spike_training_card(card: Card, deck_colors: list) -> bool:
    """Cards eligible as labeled omission-spike training rows."""
    return passes_spec_candidate_filters(card, deck_colors)


def passes_hate_card_filters(card: Card) -> bool:
    """Off-color hate-card candidate pool — bypasses color identity check.

    These cards spike because they counter a newly-printed popular strategy
    (e.g. Spike Cannibal vs a major counter/proliferate commander). They are
    never expected to be in the deck itself, so color identity is irrelevant.
    """
    return _passes_base_card_filters(card) and not card.reserved
