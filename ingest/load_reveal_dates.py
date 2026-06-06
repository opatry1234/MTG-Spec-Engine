"""
Load decklist_reveal_date values into commander_decks from the canonical CSV.

Source of truth: data/metadata/decklist_reveal_dates.csv (researched from official
Wizards "[Set] Commander Decklists" announcements; user-audited). Matches rows to
decks by deck_id. Idempotent — safe to re-run.

Run with: python ingest/load_reveal_dates.py
"""

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL, DATA_DIR
from db.schema import CommanderDeck

CSV_PATH = DATA_DIR / "metadata" / "decklist_reveal_dates.csv"


def _parse(d: str):
    d = (d or "").strip()
    return date.fromisoformat(d) if d else None


def load_reveal_dates(csv_path: Path = CSV_PATH) -> dict:
    engine = create_engine(DATABASE_URL)
    session = sessionmaker(bind=engine)()

    updated = 0
    missing = []
    rel_diffs = []

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        deck_id = int(r["deck_id"])
        reveal = _parse(r["decklist_reveal_date"])
        deck = session.get(CommanderDeck, deck_id)
        if deck is None:
            missing.append(deck_id)
            continue
        deck.decklist_reveal_date = reveal
        # surface release-date discrepancies without overwriting (audited Secret Lairs)
        csv_rel = _parse(r["db_release_date"])
        if csv_rel and deck.release_date and csv_rel != deck.release_date:
            rel_diffs.append((deck_id, r["deck_name"], deck.release_date, csv_rel))
        if reveal:
            updated += 1

    session.commit()

    total = session.query(CommanderDeck).count()
    with_reveal = (
        session.query(CommanderDeck)
        .filter(CommanderDeck.decklist_reveal_date.isnot(None))
        .count()
    )
    session.close()

    print(f"Loaded reveal dates: {updated} rows set.")
    print(f"Coverage: {with_reveal}/{total} decks have decklist_reveal_date "
          f"({with_reveal/total*100:.0f}%).")
    if missing:
        print(f"WARNING: {len(missing)} CSV deck_ids not found in DB: {missing}")
    if rel_diffs:
        print(f"NOTE: {len(rel_diffs)} release-date discrepancies (DB left unchanged):")
        for did, name, db_rel, csv_rel in rel_diffs:
            print(f"   #{did} {name}: DB={db_rel} vs CSV={csv_rel}")
    return {"updated": updated, "coverage": with_reveal, "total": total}


if __name__ == "__main__":
    load_reveal_dates()
