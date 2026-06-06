"""
Schema migration v3: deck skeleton metadata and deck_features.

Run with: python db/migrate_v3.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, inspect, text
from config import DATABASE_URL


def _column_exists(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def migrate_v3():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)

    additions = [
        ("commander_decks", "deck_features", "JSON"),
        ("commander_decks", "product_type", "TEXT"),
        ("commander_decks", "release_era", "TEXT"),
        ("commander_decks", "primary_archetype", "TEXT"),
        ("commander_decks", "secondary_archetype", "TEXT"),
    ]

    with engine.begin() as conn:
        for table, column, col_type in additions:
            if inspector.has_table(table) and not _column_exists(inspector, table, column):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                print(f"Added {table}.{column}")

    print("Migration v3 complete.")


if __name__ == "__main__":
    migrate_v3()
