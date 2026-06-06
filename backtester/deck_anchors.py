"""
Build anchor card pools for combo validation (commander + decklist).
"""

from __future__ import annotations

from typing import Optional, Set

from sqlalchemy.orm import Session

from db.schema import CommanderDeck
from engine.heuristic_scorer import get_actual_deck_cards


def actual_deck_cards(session: Session, deck_id: int) -> Set[str]:
    return set(get_actual_deck_cards(session, deck_id))


def commander_and_decklist_anchors(session: Session, deck: CommanderDeck) -> Set[str]:
    """Commander plus every card in the revealed decklist."""
    anchors: Set[str] = set()
    if deck.commander_name:
        anchors.add(deck.commander_name.strip())
    if deck.decklist_revealed:
        anchors |= actual_deck_cards(session, deck.id)
    return anchors


def expected_precon_anchors(
    session: Session,
    deck: CommanderDeck,
    *,
    models: Optional[dict] = None,
) -> Set[str]:
    """Phase 3: expected anchors are the actual public decklist."""
    return commander_and_decklist_anchors(session, deck)


def resolve_deck(session: Session, *, deck_name: str = "", product_code: str = ""):
    q = session.query(CommanderDeck)
    if deck_name:
        hit = q.filter(CommanderDeck.deck_name == deck_name).first()
        if hit:
            return hit
    if product_code:
        hits = q.filter(CommanderDeck.product == product_code).all()
        if len(hits) == 1:
            return hits[0]
    return None


def list_anchor_cards(
    session: Session,
    deck: CommanderDeck,
    *,
    anchor_mode: str = "decklist",
    models: Optional[dict] = None,
) -> list[dict]:
    decklist = commander_and_decklist_anchors(session, deck)
    expected = expected_precon_anchors(session, deck, models=models)

    if anchor_mode == "decklist":
        pool = decklist
    elif anchor_mode == "expected":
        pool = expected
    else:
        pool = decklist | expected

    out = []
    for name in sorted(pool, key=str.lower):
        in_deck = name in decklist
        in_expected = name in expected
        out.append(
            {
                "card_name": name,
                "in_actual_decklist": in_deck,
                "in_expected_precon": in_expected,
                "is_commander": name == (deck.commander_name or "").strip(),
            }
        )
    return out
