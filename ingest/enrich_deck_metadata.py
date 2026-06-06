"""
Enrich commander_decks with TCGPlayer product descriptions and fresh commander oracle text.

Product IDs are resolved via MTGJSON sealed-product metadata (tcgplayerProductId), then
descriptions are fetched from TCGPlayer's public product-details API.

Commander oracle text is refreshed from the local Scryfall cards table (no API calls).

Run:
    python ingest/enrich_deck_metadata.py
    python ingest/enrich_deck_metadata.py --dry-run
    python ingest/enrich_deck_metadata.py --commander-only
    python ingest/enrich_deck_metadata.py --descriptions-only
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR
from db.engine import create_session_factory
from db.schema import Card, CommanderDeck
from engine.product_parser import parse_announced_new_cards

MTGJSON_BASE = "https://mtgjson.com/api/v5"
TCGPLAYER_DETAILS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{product_id}/details"
MTGJSON_CACHE_DIR = DATA_DIR / "cache" / "mtgjson"
USER_AGENT = "MTGSpecEngine/1.0 (deck metadata enrichment)"
TCGPLAYER_DELAY = 0.12

# DB product codes that differ from MTGJSON set codes for the same commander product line.
SET_CODE_ALIASES: Dict[str, List[str]] = {
    "BLB": ["BLC"],
    "PLST": ["SLD"],
    "OVOC": ["VOC"],
    "DFT": ["DRC"],
}

SKIP_SEALED_NAME_FRAGMENTS = (
    "Minimal Packaging",
    "Set of",
    "Display",
    "Fan Bundle",
    "Galaxy Foil",
)


def normalize_deck_name(name: str) -> str:
    """Lowercase, strip punctuation/apostrophes, collapse whitespace."""
    text = name.lower()
    for ch in ("'", "'", "`", "’"):
        text = text.replace(ch, "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_html(raw: str) -> str:
    """Convert TCGPlayer HTML descriptions to plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _lookup_card(session, card_name: str) -> Optional[Card]:
    card = session.query(Card).filter(Card.name == card_name).first()
    if card:
        return card
    front = card_name.split(" // ")[0].strip()
    if front != card_name:
        return session.query(Card).filter(Card.name == front).first()
    return None


def lookup_commander_oracle(session, commander_str: str) -> str:
    """Resolve face commander oracle text from the local cards table."""
    if not commander_str:
        return ""
    for part in re.split(r"\s*/\s*|\s+and\s+", commander_str):
        part = part.strip()
        if not part:
            continue
        card = _lookup_card(session, part)
        if card and card.oracle_text:
            return card.oracle_text
    card = _lookup_card(session, commander_str.strip())
    return (card.oracle_text or "") if card else ""


def _should_skip_sealed_product(name: str) -> bool:
    return any(fragment in name for fragment in SKIP_SEALED_NAME_FRAGMENTS)


def fetch_mtgjson_set(set_code: str, session: requests.Session, use_cache: bool = True) -> dict:
    """Download (or load cached) MTGJSON set JSON."""
    MTGJSON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = MTGJSON_CACHE_DIR / f"{set_code.upper()}.json"

    if use_cache and cache_path.exists():
        import json

        with open(cache_path) as f:
            return json.load(f)

    url = f"{MTGJSON_BASE}/{set_code.upper()}.json"
    resp = session.get(url, timeout=90)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    payload = resp.json()

    import json

    with open(cache_path, "w") as f:
        json.dump(payload, f)

    return payload


def build_tcgplayer_index(
    set_codes: Iterable[str],
    http: requests.Session,
    use_cache: bool = True,
) -> Dict[str, Dict[str, int]]:
    """
    Map MTGJSON set code -> normalized deck name -> tcgplayerProductId.
    """
    index: Dict[str, Dict[str, int]] = {}

    for set_code in sorted(set_codes):
        for code in _set_codes_to_try(set_code):
            if code in index:
                continue

            payload = fetch_mtgjson_set(code, http, use_cache=use_cache)
            data = payload.get("data") or {}
            deck_map: Dict[str, int] = {}

            for sp in data.get("sealedProduct") or []:
                if sp.get("subtype") != "commander" or sp.get("category") != "deck":
                    continue
                sp_name = sp.get("name") or ""
                if _should_skip_sealed_product(sp_name):
                    continue

                deck_contents = (sp.get("contents") or {}).get("deck") or []
                if not deck_contents:
                    continue

                deck_name = deck_contents[0].get("name") or ""
                product_id = (sp.get("identifiers") or {}).get("tcgplayerProductId")
                if not deck_name or not product_id:
                    continue

                deck_map[normalize_deck_name(deck_name)] = int(product_id)

            index[code] = deck_map

    return index


def _set_codes_to_try(product_code: str) -> List[str]:
    codes = [product_code.upper()]
    for alias in SET_CODE_ALIASES.get(product_code.upper(), []):
        if alias.upper() not in codes:
            codes.append(alias.upper())
    return codes


def resolve_product_id(
    deck: CommanderDeck,
    index: Dict[str, Dict[str, int]],
) -> Optional[int]:
    """Find tcgplayerProductId for a deck via MTGJSON index."""
    target = normalize_deck_name(deck.deck_name or "")

    for code in _set_codes_to_try(deck.product or ""):
        deck_map = index.get(code.upper(), {})
        if target in deck_map:
            return deck_map[target]

        for key, product_id in deck_map.items():
            if target in key or key in target:
                return product_id

    return None


def fetch_tcgplayer_description(
    product_id: int,
    http: requests.Session,
    description_cache: Dict[int, Optional[str]],
    max_retries: int = 3,
) -> Optional[str]:
    if product_id in description_cache:
        return description_cache[product_id]

    url = TCGPLAYER_DETAILS_URL.format(product_id=product_id)
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            resp = http.get(url, timeout=30)
            if resp.status_code == 404:
                description_cache[product_id] = None
                return None
            resp.raise_for_status()

            attrs = resp.json().get("customAttributes") or {}
            raw = attrs.get("description")
            description = strip_html(raw) if raw else None
            description_cache[product_id] = description
            time.sleep(TCGPLAYER_DELAY)
            return description
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(TCGPLAYER_DELAY * (attempt + 2))

    if last_error:
        raise last_error
    return None


def enrich_decks(
    *,
    dry_run: bool = False,
    commander_only: bool = False,
    descriptions_only: bool = False,
    force_descriptions: bool = False,
    use_mtgjson_cache: bool = True,
) -> dict:
    Session, _engine = create_session_factory()
    session = Session()

    http = requests.Session()
    http.headers.update({"User-Agent": USER_AGENT})

    decks = session.query(CommanderDeck).order_by(CommanderDeck.id).all()
    set_codes = {d.product for d in decks if d.product}

    stats = {
        "decks_total": len(decks),
        "commander_updated": 0,
        "commander_missing": 0,
        "description_updated": 0,
        "description_empty_on_tcgplayer": 0,
        "description_skipped_existing": 0,
        "announced_new_cards_updated": 0,
        "product_id_missing": [],
        "no_description": [],
    }

    tcg_index = {}
    description_cache: Dict[int, Optional[str]] = {}

    if not commander_only:
        print(f"Loading MTGJSON sealed-product index for {len(set_codes)} set codes...")
        tcg_index = build_tcgplayer_index(set_codes, http, use_cache=use_mtgjson_cache)

    for i, deck in enumerate(decks, 1):
        label = f"{deck.product} / {deck.deck_name}"
        changed = False

        if not descriptions_only:
            oracle = lookup_commander_oracle(session, deck.commander_name or "")
            if oracle and oracle != (deck.commander_text or ""):
                if not dry_run:
                    deck.commander_text = oracle
                stats["commander_updated"] += 1
                changed = True
            elif not oracle:
                stats["commander_missing"] += 1

        if not commander_only:
            existing = (deck.product_description or "").strip()
            if existing and not force_descriptions:
                stats["description_skipped_existing"] += 1
            else:
                product_id = resolve_product_id(deck, tcg_index)
                if not product_id:
                    stats["product_id_missing"].append(label)
                else:
                    try:
                        description = fetch_tcgplayer_description(
                            product_id, http, description_cache
                        )
                    except requests.RequestException as exc:
                        stats.setdefault("description_errors", []).append(
                            (label, str(exc))
                        )
                        continue

                    if description:
                        if not dry_run:
                            deck.product_description = description
                            deck.source_url = (
                                f"https://www.tcgplayer.com/product/{product_id}"
                            )
                        stats["description_updated"] += 1
                        changed = True
                    else:
                        stats["description_empty_on_tcgplayer"] += 1
                        stats["no_description"].append(label)

            announced = parse_announced_new_cards(deck.product_description or "")
            if announced is not None and announced != deck.announced_new_cards:
                if not dry_run:
                    deck.announced_new_cards = announced
                stats["announced_new_cards_updated"] += 1
                changed = True

        if changed and i % 25 == 0:
            print(f"  processed {i}/{len(decks)} decks...")

    if not dry_run:
        session.commit()

    session.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich commander decks with TCGPlayer descriptions and commander oracle text"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing to the database",
    )
    parser.add_argument(
        "--commander-only",
        action="store_true",
        help="Only refresh commander_text from the local cards table",
    )
    parser.add_argument(
        "--descriptions-only",
        action="store_true",
        help="Only fetch TCGPlayer product descriptions",
    )
    parser.add_argument(
        "--force-descriptions",
        action="store_true",
        help="Overwrite existing product_description values",
    )
    parser.add_argument(
        "--refresh-mtgjson",
        action="store_true",
        help="Re-download MTGJSON set files instead of using local cache",
    )
    args = parser.parse_args()

    if args.commander_only and args.descriptions_only:
        parser.error("Use at most one of --commander-only and --descriptions-only")

    stats = enrich_decks(
        dry_run=args.dry_run,
        commander_only=args.commander_only,
        descriptions_only=args.descriptions_only,
        force_descriptions=args.force_descriptions,
        use_mtgjson_cache=not args.refresh_mtgjson,
    )

    print()
    print(f"Decks processed: {stats['decks_total']}")
    print(f"Commander text updated: {stats['commander_updated']}")
    print(f"Commander oracle missing in DB: {stats['commander_missing']}")
    print(f"Product descriptions written: {stats['description_updated']}")
    print(f"Skipped (already had description): {stats['description_skipped_existing']}")
    print(f"TCGPlayer product found but no description: {stats['description_empty_on_tcgplayer']}")
    print(f"Announced new cards updated: {stats['announced_new_cards_updated']}")

    if stats["product_id_missing"]:
        print(f"\nCould not resolve TCGPlayer product ID ({len(stats['product_id_missing'])}):")
        for row in stats["product_id_missing"]:
            print(f"  - {row}")

    if stats["no_description"]:
        print(f"\nNo TCGPlayer description available ({len(stats['no_description'])}):")
        for row in stats["no_description"][:15]:
            print(f"  - {row}")
        if len(stats["no_description"]) > 15:
            print(f"  ... and {len(stats['no_description']) - 15} more")

    if stats.get("description_errors"):
        print(f"\nTCGPlayer fetch errors ({len(stats['description_errors'])}):")
        for row, err in stats["description_errors"][:10]:
            print(f"  - {row}: {err}")

    if args.dry_run:
        print("\nDry run — no database changes were saved.")


if __name__ == "__main__":
    main()
