"""
Database initialization script.

Creates all tables and initializes the database.
Run with: python db/init_db.py
"""

import sys
from pathlib import Path

# Add parent directory to path so we can import config
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from config import DATABASE_URL
from db.schema import Base
from db.migrate_v1 import migrate_v1
from db.migrate_v2 import migrate_v2


def init_db():
    """Create all tables defined in schema.py and apply migrations."""
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    migrate_v1()
    migrate_v2()
    print(f"Database initialized at {DATABASE_URL}")


if __name__ == "__main__":
    init_db()
