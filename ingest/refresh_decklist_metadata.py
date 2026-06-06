"""
Refresh deck metadata from Excel without wiping the database.

Fixes land_count (column G), card quantities (duplicate basics), and new_cards totals.

Run with: python ingest/refresh_decklist_metadata.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.engine import create_session_factory
from db.schema import CommanderDeck, DeckCard
from engine.product_parser import parse_announced_new_cards
from ingest.decklists_analyzer import (
    DECKLIST_PATH,
    _is_new_card,
    load_all_decklists,
)


def refresh_decklist_metadata(session=None) -> dict:
    own_session = session is None
    if own_session:
        session = create_session_factory()[0]()

    if not DECKLIST_PATH.exists():
        raise FileNotFoundError(f"Decklist file not found: {DECKLIST_PATH}")

    parsed = {deck["name"]: deck for deck in load_all_decklists(DECKLIST_PATH)}
    decks = session.query(CommanderDeck).all()
    stats = {"updated": 0, "missing_sheet": []}

    try:
        for deck in decks:
            info = parsed.get(deck.deck_name)
            if not info:
                stats["missing_sheet"].append(deck.deck_name)
                continue

            set_code = (info.get("set_code") or deck.product or "").lower()
            deck.land_count = info.get("land_count")
            deck.total_cards = sum(info["cards"].values())

            deck.new_cards = sum(
                qty
                for name, qty in info["cards"].items()
                if _is_new_card(session, name, set_code)
            )

            existing = {
                dc.card_name: dc
                for dc in session.query(DeckCard).filter(DeckCard.deck_id == deck.id).all()
            }
            parsed_names = set(info["cards"].keys())

            for name, qty in info["cards"].items():
                if name in existing:
                    existing[name].quantity = qty
                    existing[name].is_new_card = _is_new_card(session, name, set_code)
                else:
                    session.add(
                        DeckCard(
                            deck_id=deck.id,
                            card_name=name,
                            quantity=qty,
                            is_new_card=_is_new_card(session, name, set_code),
                        )
                    )

            for name, dc in existing.items():
                if name not in parsed_names:
                    session.delete(dc)

            stats["updated"] += 1

        if own_session:
            session.commit()
        return stats
    finally:
        if own_session:
            session.close()


def backfill_announced_new_cards(session=None) -> int:
    own_session = session is None
    if own_session:
        session = create_session_factory()[0]()

    updated = 0
    try:
        for deck in session.query(CommanderDeck).all():
            announced = parse_announced_new_cards(deck.product_description or "")
            if announced is not None and announced != deck.announced_new_cards:
                deck.announced_new_cards = announced
                updated += 1
        if own_session:
            session.commit()
        return updated
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    from db.migrate_v4 import migrate_v4

    migrate_v4()
    result = refresh_decklist_metadata()
    announced = backfill_announced_new_cards()
    print(f"Refreshed {result['updated']} decks from Excel")
    print(f"Set announced_new_cards on {announced} decks")
    if result["missing_sheet"]:
        print(f"WARNING: {len(result['missing_sheet'])} decks missing Excel sheets")
