"""
Scryfall API data ingestion.

Downloads bulk card data and printing history from Scryfall.
Run with: python ingest/scryfall.py --mode oracle | --mode printings | --mode both
"""

import sys
import json
import time
import argparse
from datetime import datetime, date
from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SCRYFALL_API_BASE, SCRYFALL_RATE_LIMIT_DELAY, DATABASE_URL
from db.schema import Card, CardPrinting

BULK_URL = f"{SCRYFALL_API_BASE}/bulk-data"
USER_AGENT = "MTGSpecEngine/1.0 (local research tool)"
BATCH_SIZE = 1000


def _get_session():
    engine = create_engine(DATABASE_URL)
    return sessionmaker(bind=engine)()


def _get_bulk_download_uri(bulk_type: str) -> str:
    print(f"Fetching bulk-data index from Scryfall...")
    resp = requests.get(BULK_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    meta = resp.json()

    try:
        entry = next(d for d in meta["data"] if d["type"] == bulk_type)
    except StopIteration:
        raise ValueError(f"Bulk type '{bulk_type}' not found in Scryfall response")

    size_mb = entry.get("size", 0) / 1024 / 1024
    print(f"  type={bulk_type}  size={size_mb:.0f} MB  updated={entry.get('updated_at', 'unknown')[:10]}")
    time.sleep(SCRYFALL_RATE_LIMIT_DELAY)
    return entry["download_uri"]


def _download_bulk_json(uri: str, label: str) -> list:
    print(f"Downloading {label}...")
    resp = requests.get(uri, headers={"User-Agent": USER_AGENT}, stream=True, timeout=300)
    resp.raise_for_status()

    content_length = int(resp.headers.get("Content-Length", 0))
    chunks = []
    downloaded = 0

    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        chunks.append(chunk)
        downloaded += len(chunk)
        if content_length:
            pct = downloaded / content_length * 100
            print(f"\r  {downloaded / 1_048_576:.1f} / {content_length / 1_048_576:.1f} MB  ({pct:.0f}%)", end="", flush=True)
        else:
            print(f"\r  {downloaded / 1_048_576:.1f} MB", end="", flush=True)

    print()
    print("  Parsing JSON...", flush=True)
    data = json.loads(b"".join(chunks))
    print(f"  Loaded {len(data):,} records")
    return data


def _parse_creature_types(type_line: str) -> list:
    if not type_line or "—" not in type_line:
        return []
    after_dash = type_line.split("—", 1)[1].strip()
    return [t for t in after_dash.split() if t]


def _oracle_text_for(card: dict) -> str | None:
    text = card.get("oracle_text")
    if text is None and "card_faces" in card:
        parts = [f.get("oracle_text", "") for f in card["card_faces"] if f.get("oracle_text")]
        text = " // ".join(parts) if parts else None
    return text


def _mana_cost_for(card: dict) -> str | None:
    cost = card.get("mana_cost")
    if not cost and "card_faces" in card:
        cost = card["card_faces"][0].get("mana_cost")
    return cost


def _image_url_for(card: dict) -> str | None:
    uris = card.get("image_uris")
    if uris:
        return uris.get("normal") or uris.get("large")
    if "card_faces" in card and card["card_faces"]:
        face_uris = card["card_faces"][0].get("image_uris") or {}
        return face_uris.get("normal") or face_uris.get("large")
    return None


def _commander_legal_for(card: dict) -> bool:
    legalities = card.get("legalities") or {}
    return legalities.get("commander") == "legal"


def download_oracle_cards() -> list:
    """Download oracle_cards bulk data (one entry per unique card)."""
    uri = _get_bulk_download_uri("oracle_cards")
    return _download_bulk_json(uri, "oracle_cards")


def download_all_printings() -> list:
    """Download default_cards bulk data (every printing of every card)."""
    uri = _get_bulk_download_uri("default_cards")
    return _download_bulk_json(uri, "default_cards")


def ingest_oracle_cards(session=None):
    """Download oracle cards and upsert into the cards table."""
    own_session = session is None
    if own_session:
        session = _get_session()

    try:
        cards_data = download_oracle_cards()
        total = len(cards_data)
        print(f"\nUpserting {total:,} cards into `cards` table...")
        now = datetime.utcnow()

        for i, card in enumerate(cards_data, start=1):
            type_line = card.get("type_line", "")
            creature_types = _parse_creature_types(type_line) if "Creature" in type_line else []

            released_at_str = card.get("released_at")
            released_at = date.fromisoformat(released_at_str) if released_at_str else None

            session.merge(Card(
                id=card.get("oracle_id") or card["id"],
                name=card["name"],
                mana_cost=_mana_cost_for(card),
                cmc=card.get("cmc"),
                colors=card.get("colors", []),
                color_identity=card.get("color_identity", []),
                type_line=type_line,
                oracle_text=_oracle_text_for(card),
                keywords=card.get("keywords", []),
                creature_types=creature_types,
                rarity=card.get("rarity"),
                set_code=card.get("set"),
                released_at=released_at,
                reserved=card.get("reserved", False),
                edhrec_rank=card.get("edhrec_rank"),
                image_url=_image_url_for(card),
                commander_legal=_commander_legal_for(card),
                layout=card.get("layout"),
                updated_at=now,
            ))

            if i % BATCH_SIZE == 0:
                session.flush()
                print(f"  {i:,} / {total:,} ({i / total * 100:.0f}%)", flush=True)

        session.commit()
        print(f"Done. Upserted {total:,} oracle cards.")

    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()


def ingest_printings(session=None):
    """Download default_cards and upsert all printings into card_printings table."""
    own_session = session is None
    if own_session:
        session = _get_session()

    try:
        printings_data = download_all_printings()
        total = len(printings_data)
        print(f"\nUpserting {total:,} printings into `card_printings` table...")

        precon_count = 0

        for i, card in enumerate(printings_data, start=1):
            released_at_str = card.get("released_at")
            released_at = date.fromisoformat(released_at_str) if released_at_str else None

            is_commander_precon = card.get("set_type") == "commander"
            if is_commander_precon:
                precon_count += 1

            session.merge(CardPrinting(
                id=card["id"],
                card_name=card["name"],
                set_code=card.get("set"),
                released_at=released_at,
                is_commander_precon=is_commander_precon,
            ))

            if i % BATCH_SIZE == 0:
                session.flush()
                print(f"  {i:,} / {total:,} ({i / total * 100:.0f}%)", flush=True)

        session.commit()
        print(f"Done. Upserted {total:,} printings ({precon_count:,} flagged as commander precon).")

    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest Scryfall bulk data into the MTG Spec Engine database."
    )
    parser.add_argument(
        "--mode",
        choices=["oracle", "printings", "both"],
        default="both",
        help="oracle = cards table, printings = card_printings table, both = run both (default)",
    )
    args = parser.parse_args()

    if args.mode in ("oracle", "both"):
        print("=== Oracle Cards ===")
        ingest_oracle_cards()
        print()

    if args.mode in ("printings", "both"):
        print("=== Card Printings ===")
        ingest_printings()
