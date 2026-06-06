"""
Card image lookup — local cache with Scryfall URL fallback.
"""

import base64
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from config import DATA_DIR
from db.schema import Card

IMAGES_DIR = DATA_DIR / "images"
SCRYFALL_SEARCH_URL = "https://scryfall.com/search?q="


def scryfall_page_url(card_name: str) -> str:
    """Link to Scryfall search for this card name (no ! exact-name prefix)."""
    from urllib.parse import quote

    return f"{SCRYFALL_SEARCH_URL}{quote(card_name)}"


@lru_cache(maxsize=8192)
def _local_image_base64(image_path: str) -> Optional[str]:
    path = Path(image_path)
    if not path.is_file():
        return None
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext or "jpeg"
    return f"data:image/{mime};base64,{base64.b64encode(data).decode('ascii')}"


def lookup_card(session: Session, card_name: str) -> Optional[Card]:
    """Resolve a card row by exact name or front face of a split/DFC name."""
    if not card_name:
        return None
    card = session.query(Card).filter(Card.name == card_name).first()
    if card:
        return card
    front = card_name.split(" // ")[0].strip()
    if front != card_name:
        return session.query(Card).filter(Card.name == front).first()
    return None


def get_image_src(session: Session, card_name: str) -> str:
    """
    Return image src for HTML img tags: local base64 if cached, else Scryfall CDN URL.
    """
    card = lookup_card(session, card_name)
    if not card:
        return ""

    if card.image_path:
        b64 = _local_image_base64(card.image_path)
        if b64:
            return b64

    if card.image_url:
        return card.image_url

    return ""


def load_image_map(session: Session, card_names: list) -> dict:
    """Batch lookup card_name -> image src for UI rendering."""
    if not card_names:
        return {}

    result = {}
    for name in card_names:
        result[name] = get_image_src(session, name)
    return result
