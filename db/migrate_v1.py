"""
Schema migration v1: add deck_cards.quantity and commander_decks.deck_composition.

Run with: python db/migrate_v1.py
Safe to re-run — skips columns that already exist.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, inspect, text
from config import DATABASE_URL


def _column_exists(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def migrate_v1():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)

    with engine.begin() as conn:
        if inspector.has_table("deck_cards") and not _column_exists(inspector, "deck_cards", "quantity"):
            conn.execute(text("ALTER TABLE deck_cards ADD COLUMN quantity INTEGER DEFAULT 1"))
            print("Added deck_cards.quantity")

        if inspector.has_table("commander_decks") and not _column_exists(
            inspector, "commander_decks", "deck_composition"
        ):
            conn.execute(text("ALTER TABLE commander_decks ADD COLUMN deck_composition JSON"))
            print("Added commander_decks.deck_composition")

    print("Migration v1 complete.")


if __name__ == "__main__":
    migrate_v1()
