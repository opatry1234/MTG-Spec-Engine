"""
Schema migration v7: local card_prices cache.

Mirrors Supabase card_prices_current so the engine can read price point-in-time
locally (fast, no per-card network calls during scoring).

Run with: python db/migrate_v7.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, inspect, text
from config import DATABASE_URL


def migrate_v7():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)
    if not inspector.has_table("card_prices"):
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE card_prices ("
                "card_name TEXT PRIMARY KEY, "
                "price_usd REAL, "
                "price_usd_foil REAL, "
                "available_copies INTEGER, "
                "seller_count INTEGER, "
                "copies_per_seller REAL, "
                "as_of_date DATE)"
            ))
        print("Created card_prices")
    print("Migration v7 complete.")


if __name__ == "__main__":
    migrate_v7()
