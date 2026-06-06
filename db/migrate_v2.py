"""
Schema migration v2: card image URLs, commander legality, layout.

Run with: python db/migrate_v2.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, inspect, text
from config import DATABASE_URL


def _column_exists(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def migrate_v2():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)

    additions = [
        ("cards", "image_url", "TEXT"),
        ("cards", "image_path", "TEXT"),
        ("cards", "commander_legal", "BOOLEAN"),
        ("cards", "layout", "TEXT"),
    ]

    with engine.begin() as conn:
        for table, column, col_type in additions:
            if inspector.has_table(table) and not _column_exists(inspector, table, column):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                print(f"Added {table}.{column}")

    print("Migration v2 complete.")


if __name__ == "__main__":
    migrate_v2()
