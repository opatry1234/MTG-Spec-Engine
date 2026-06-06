"""
Per-deck feature extraction for skeleton prediction.

Computes composition, functional roles, and new/reprint breakdowns.
Modal cards may match multiple functional patterns (accepted in v1).
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.engine import create_session_factory
from db.schema import Card, CommanderDeck, DeckCard
from features.mechanical import keyword_category_scores
from ingest.product_metadata import backfill_product_metadata, release_era_for_date

Session, engine = create_session_factory()

COMPOSITION_KEYS = [
    "lands",
    "creatures",
    "legendary_creatures",
    "nonlegendary_creatures",
    "artifact_creatures",
    "artifacts",
    "instants",
    "sorceries",
    "enchantments",
    "planeswalkers",
    "other",
]

FUNCTIONAL_KEYS = [
    "ramp",
    "removal",
    "draw",
    "protection",
    "recursion",
    "tutors",
    "synergy",
    "win_conditions",
    "utility",
]

TYPE_BUCKETS_FOR_NEW = [
    "creatures",
    "artifacts",
    "instants",
    "sorceries",
    "enchantments",
    "lands",
    "planeswalkers",
]

RAMP_PATTERNS = [
    r"add \{",
    r"search your library for a .+ land",
    r"put a land card",
    r"ramp",
]
REMOVAL_PATTERNS = [
    r"destroy target",
    r"exile target",
    r"return target .+ to its owner's hand",
    r"-\d+/-\d+ until end of turn",
]
DRAW_PATTERNS = [
    r"draw a card",
    r"draw cards",
    r"draw two cards",
    r"draw three cards",
]
TUTOR_PATTERNS = [
    r"search your library for a (?!.*land)",
    r"search your library for an?",
]
RECURSION_PATTERNS = [
    r"return .+ from your graveyard",
    r"from your graveyard to",
]
PROTECTION_PATTERNS = [
    r"hexproof",
    r"indestructible",
    r"protection from",
    r"shroud",
]
WIN_PATTERNS = [
    r"you win the game",
    r"each opponent loses",
    r"each opponent's life total becomes 0",
]
SYNERGY_PATTERNS = [
    r"whenever you",
    r"each time you",
    r"whenever another",
]


def _color_key(colors: Optional[list]) -> str:
    if not colors:
        return "C"
    order = ["W", "U", "B", "R", "G"]
    return "".join(c for c in order if c in colors)


def _matches_patterns(text: Optional[str], patterns: List[str]) -> bool:
    import re

    if not text:
        return False
    lower = text.lower()
    return any(re.search(p, lower) for p in patterns)


def _classify_card_bucket(type_line: Optional[str]) -> str:
    if not type_line:
        return "other"
    tl = type_line.lower()
    if "land" in tl:
        return "lands"
    if "planeswalker" in tl:
        return "planeswalkers"
    if "creature" in tl and "artifact" in tl:
        return "artifact_creatures"
    if "creature" in tl:
        return "creatures"
    if "artifact" in tl:
        return "artifacts"
    if "instant" in tl:
        return "instants"
    if "sorcery" in tl:
        return "sorceries"
    if "enchantment" in tl:
        return "enchantments"
    return "other"


def _new_reprint_bucket(bucket: str) -> str:
    if bucket == "artifact_creatures":
        return "creatures"
    if bucket in TYPE_BUCKETS_FOR_NEW:
        return bucket
    return "other"


def _infer_archetypes(commander_text: Optional[str], theme: Optional[str]) -> Tuple[str, str]:
    text = commander_text or ""
    scores = keyword_category_scores(text)
    max_score = max(scores.values()) if scores else 0.0

    if max_score <= 0:
        theme_lower = (theme or "").lower()
        if "graveyard" in theme_lower:
            return "graveyard", ""
        if "token" in theme_lower:
            return "tokens", ""
        return "general", ""

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary = ranked[0][0]
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] > 0 else ""
    return primary, secondary


def compute_deck_features_from_rows(
    deck: CommanderDeck,
    rows: List[Tuple[DeckCard, Optional[Card]]],
) -> dict:
    """Compute full deck feature vector from pre-loaded deck card rows."""
    composition = {k: 0 for k in COMPOSITION_KEYS}
    functional = {k: 0 for k in FUNCTIONAL_KEYS}
    new_by_type = {f"new_{t}": 0 for t in TYPE_BUCKETS_FOR_NEW}
    reprint_by_type = {f"reprint_{t}": 0 for t in TYPE_BUCKETS_FOR_NEW}

    for deck_card, card in rows:
        qty = deck_card.quantity or 1
        type_line = card.type_line if card else ""
        bucket = _classify_card_bucket(type_line)
        oracle = card.oracle_text if card else ""

        if bucket == "artifact_creatures":
            composition["artifact_creatures"] += qty
            composition["creatures"] += qty
        elif bucket in composition and bucket != "lands":
            composition[bucket] += qty
        elif bucket not in ("lands",):
            composition["other"] += qty

        if bucket == "creatures" or bucket == "artifact_creatures":
            if type_line and "legendary" in type_line.lower():
                composition["legendary_creatures"] += qty
            else:
                composition["nonlegendary_creatures"] += qty

        nr_bucket = _new_reprint_bucket(bucket)
        is_new = deck_card.is_new_card
        if is_new:
            key = f"new_{nr_bucket}"
            if key in new_by_type:
                new_by_type[key] += qty
        else:
            key = f"reprint_{nr_bucket}"
            if key in reprint_by_type:
                reprint_by_type[key] += qty

        if bucket == "lands":
            continue

        if _matches_patterns(oracle, RAMP_PATTERNS):
            functional["ramp"] += qty
        if _matches_patterns(oracle, REMOVAL_PATTERNS):
            functional["removal"] += qty
        if _matches_patterns(oracle, DRAW_PATTERNS):
            functional["draw"] += qty
        if _matches_patterns(oracle, TUTOR_PATTERNS):
            functional["tutors"] += qty
        if _matches_patterns(oracle, RECURSION_PATTERNS):
            functional["recursion"] += qty
        if _matches_patterns(oracle, PROTECTION_PATTERNS):
            functional["protection"] += qty
        if _matches_patterns(oracle, WIN_PATTERNS):
            functional["win_conditions"] += qty
        if _matches_patterns(oracle, SYNERGY_PATTERNS):
            functional["synergy"] += qty

    if deck.land_count is not None:
        composition["lands"] = deck.land_count
    else:
        composition["lands"] = sum(
            (deck_card.quantity or 1)
            for deck_card, card in rows
            if card and card.type_line and "land" in card.type_line.lower()
        )

    total_new = sum(new_by_type.values())
    total_reprint = sum(reprint_by_type.values())

    primary_arch, secondary_arch = _infer_archetypes(
        deck.commander_text, deck.theme or deck.deck_name
    )

    return {
        "structural": {
            "color_identity": _color_key(deck.colors),
            "num_colors": len(deck.colors or []),
            "product_type": deck.product_type,
            "release_year": deck.release_date.year if deck.release_date else None,
            "release_era": deck.release_era or release_era_for_date(deck.release_date),
            "primary_archetype": primary_arch,
            "secondary_archetype": secondary_arch,
        },
        "composition": composition,
        "functional": functional,
        "allocation": {
            "total_new": total_new,
            "total_reprint": total_reprint,
            **new_by_type,
            **reprint_by_type,
        },
    }


def compute_deck_features(session, deck: CommanderDeck) -> dict:
    """Compute full deck feature vector for one deck (single-deck query)."""
    rows = (
        session.query(DeckCard, Card)
        .outerjoin(Card, Card.name == DeckCard.card_name)
        .filter(DeckCard.deck_id == deck.id)
        .all()
    )
    return compute_deck_features_from_rows(deck, rows)


def flatten_features(features: dict) -> dict:
    """Flatten nested deck_features for distribution aggregation."""
    flat = {}
    for section in ("composition", "functional", "allocation"):
        flat.update(features.get(section, {}))
    struct = features.get("structural", {})
    flat["num_colors"] = struct.get("num_colors", 0)
    return flat


def rebuild_all_deck_features(session=None, backfill_metadata: bool = True) -> int:
    own_session = session is None
    if own_session:
        session = Session()

    try:
        if backfill_metadata:
            backfill_product_metadata(session=session)

        decks = session.query(CommanderDeck).all()
        all_rows = (
            session.query(DeckCard, Card)
            .outerjoin(Card, Card.name == DeckCard.card_name)
            .all()
        )
        rows_by_deck: Dict[int, list] = defaultdict(list)
        for deck_card, card in all_rows:
            rows_by_deck[deck_card.deck_id].append((deck_card, card))

        with session.no_autoflush:
            for deck in decks:
                feats = compute_deck_features_from_rows(
                    deck, rows_by_deck.get(deck.id, [])
                )
                deck.deck_features = feats
                deck.primary_archetype = feats["structural"]["primary_archetype"]
                deck.secondary_archetype = feats["structural"]["secondary_archetype"]
                deck.deck_composition = {
                    **feats["composition"],
                    **feats["functional"],
                }

        if own_session:
            session.commit()
        return len(decks)
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild deck_features for all decks")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild all deck features")
    parser.add_argument("--no-metadata", action="store_true", help="Skip product_type backfill")
    parser.add_argument("--migrate", action="store_true", help="Run schema migration v3 first")
    args = parser.parse_args()

    if args.migrate:
        from db.migrate_v3 import migrate_v3

        migrate_v3()

    n = rebuild_all_deck_features(backfill_metadata=not args.no_metadata)
    print(f"Rebuilt deck_features for {n} decks")
