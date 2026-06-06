"""
Load product_type from YAML and backfill commander_decks metadata.

Run with: python ingest/product_metadata.py
"""

import sys
from pathlib import Path
from typing import Dict, Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PRODUCT_TYPES_YAML, RELEASE_ERAS
from db.engine import create_session_factory
from db.schema import CommanderDeck

Session, _engine = create_session_factory()


def load_product_type_mapping(path: Optional[Path] = None) -> Dict[str, str]:
    """Load set_code and deck_id → product_type from YAML."""
    path = path or PRODUCT_TYPES_YAML
    if not path.exists():
        return {}

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    mapping = {}
    for key, value in raw.items():
        if key.startswith("#") or value is None:
            continue
        mapping[str(key).lower()] = str(value)
    return mapping


def release_era_for_date(release_date) -> Optional[str]:
    if release_date is None:
        return None
    for era_name, start, end in RELEASE_ERAS:
        if release_date < start:
            continue
        if end is not None and release_date > end:
            continue
        return era_name
    if release_date.year < 2015:
        return "pre-2015"
    return RELEASE_ERAS[-1][0]


def resolve_product_type(deck: CommanderDeck, mapping: Dict[str, str]) -> Optional[str]:
    deck_key = str(deck.id)
    if deck_key in mapping:
        return mapping[deck_key]

    set_code = (deck.product or "").lower()
    if set_code in mapping:
        return mapping[set_code]

    return None


def backfill_product_metadata(session=None, mapping: Optional[Dict[str, str]] = None) -> dict:
    """Apply product_type and release_era to all commander decks."""
    own_session = session is None
    if own_session:
        session = Session()

    mapping = mapping or load_product_type_mapping()
    stats = {"updated": 0, "missing_product_type": []}

    try:
        decks = session.query(CommanderDeck).all()
        for deck in decks:
            pt = resolve_product_type(deck, mapping)
            era = release_era_for_date(deck.release_date)

            if pt:
                deck.product_type = pt
            else:
                stats["missing_product_type"].append(
                    {"id": deck.id, "product": deck.product, "deck_name": deck.deck_name}
                )

            if era:
                deck.release_era = era

            stats["updated"] += 1

        if own_session:
            session.commit()
        return stats
    finally:
        if own_session:
            session.close()


def validate_product_types(session=None) -> list:
    """Return decks missing product_type."""
    own_session = session is None
    if own_session:
        session = Session()
    try:
        rows = session.query(CommanderDeck).filter(
            CommanderDeck.product_type.is_(None)
        ).all()
        return [{"id": d.id, "product": d.product, "deck_name": d.deck_name} for d in rows]
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill product_type from YAML")
    parser.add_argument("--migrate", action="store_true", help="Run schema migration v3 first")
    args = parser.parse_args()

    if args.migrate:
        from db.migrate_v3 import migrate_v3

        migrate_v3()

    result = backfill_product_metadata()
    missing = validate_product_types()
    print(f"Backfilled {result['updated']} decks")
    if missing:
        print(f"WARNING: {len(missing)} decks missing product_type:")
        for m in missing[:10]:
            print(f"  id={m['id']} product={m['product']} name={m['deck_name']}")
    else:
        print("All decks have product_type assigned.")
