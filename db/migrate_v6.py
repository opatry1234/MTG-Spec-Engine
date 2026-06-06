"""
Schema migration v6: decklist_reveal_date on commander_decks.

The Phase-3-only refactor anchors all spec logic to the day the full decklist
became public (the "spec anchor"), not the product street date. Eligibility
cutoffs and spike-timing floors key off this column.

Run with: python db/migrate_v6.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, inspect, text
from config import DATABASE_URL


def _column_exists(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def migrate_v6():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)

    additions = [
        ("commander_decks", "decklist_reveal_date", "DATE"),
    ]

    with engine.begin() as conn:
        for table, column, col_type in additions:
            if inspector.has_table(table) and not _column_exists(inspector, table, column):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                print(f"Added {table}.{column}")

    print("Migration v6 complete.")


if __name__ == "__main__":
    migrate_v6()
