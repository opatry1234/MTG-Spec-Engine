"""
Download Scryfall card images to local storage for fast hover previews.

Run with: python ingest/card_images.py [--limit N] [--force]
Requires image_url populated via: python ingest/scryfall.py --mode oracle
"""

import argparse
import sys
import time
from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR, DATABASE_URL, SCRYFALL_RATE_LIMIT_DELAY
from db.schema import Card

IMAGES_DIR = DATA_DIR / "images"
USER_AGENT = "MTGSpecEngine/1.0 (local research tool)"


def download_images(limit: int = None, force: bool = False):
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DATABASE_URL)
    session = sessionmaker(bind=engine)()

    query = session.query(Card).filter(Card.image_url.isnot(None))
    if limit:
        query = query.limit(limit)
    cards = query.all()

    downloaded = skipped = failed = 0

    for i, card in enumerate(cards, 1):
        dest = IMAGES_DIR / f"{card.id}.jpg"
        if dest.exists() and not force:
            if card.image_path != str(dest):
                card.image_path = str(dest)
            skipped += 1
            continue

        try:
            resp = requests.get(
                card.image_url,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            card.image_path = str(dest)
            downloaded += 1
        except Exception as e:
            print(f"  Failed {card.name}: {e}")
            failed += 1

        if i % 500 == 0:
            session.commit()
            print(f"  {i}/{len(cards)} — downloaded {downloaded}, skipped {skipped}")

        time.sleep(SCRYFALL_RATE_LIMIT_DELAY * 0.1)  # light throttle

    session.commit()
    session.close()
    print(f"Done. Downloaded {downloaded}, skipped {skipped}, failed {failed}")
    print(f"Images stored in {IMAGES_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    download_images(limit=args.limit, force=args.force)
