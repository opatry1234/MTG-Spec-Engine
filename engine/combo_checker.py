"""
Infinite combo detection via Commander Spellbook API.

EDHREC's combo pages are powered by this database (see https://edhrec.com/combos).
We query infinite-result variants for a card and check whether the combo closes with
anchor cards (spoiled commander and/or known decklist inclusions).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from config import (
    COMMANDER_SPELLBOOK_API,
    SPELLBOOK_COMBO_CACHE_DIR,
    SPELLBOOK_COMBO_RATE_LIMIT_SEC,
)


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _card_names_in_variant(variant: dict) -> set[str]:
    names: set[str] = set()
    for use in variant.get("uses") or []:
        card = use.get("card") or {}
        if card.get("name"):
            names.add(card["name"])
    return names


def _is_infinite_variant(variant: dict) -> bool:
    for prod in variant.get("produces") or []:
        feature = (prod.get("feature") or {}).get("name") or ""
        if "infinite" in feature.lower():
            return True
    return False


def _cache_path(card_name: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", _norm(card_name))[:120]
    return SPELLBOOK_COMBO_CACHE_DIR / f"{safe}.json"


@dataclass(frozen=True)
class ComboLoopInfo:
    has_infinite_loop: bool
    loop_partners: tuple[str, ...]

    @property
    def combo_with(self) -> str:
        return ", ".join(self.loop_partners) if self.loop_partners else ""


class ComboChecker:
    """Find infinite loops between a candidate card and deck anchor cards."""

    def __init__(self, *, use_cache: bool = True, fetch_live: bool = True):
        self.use_cache = use_cache
        self.fetch_live = fetch_live
        if use_cache:
            SPELLBOOK_COMBO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < SPELLBOOK_COMBO_RATE_LIMIT_SEC:
            time.sleep(SPELLBOOK_COMBO_RATE_LIMIT_SEC - elapsed)
        self._last_request = time.monotonic()

    def _load_cache(self, card_name: str) -> Optional[list]:
        path = _cache_path(card_name)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            return payload.get("variants") or []
        except (json.JSONDecodeError, OSError):
            return None

    def _save_cache(self, card_name: str, variants: list) -> None:
        path = _cache_path(card_name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"card_name": card_name, "variants": variants}, f)

    def fetch_infinite_variants(self, card_name: str, *, limit: int = 40) -> list:
        cached = self._load_cache(card_name) if self.use_cache else None
        if cached is not None:
            return cached
        if not self.fetch_live:
            return []

        query = f'card="{card_name}" all-results:infinite'
        url = f"{COMMANDER_SPELLBOOK_API}/variants/?{urllib.parse.urlencode({'q': query, 'limit': limit})}"
        self._throttle()
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []

        variants = data.get("results") or []
        if self.use_cache:
            self._save_cache(card_name, variants)
        return variants

    def loop_with_anchors(
        self,
        card_name: str,
        anchor_cards: Iterable[str],
    ) -> ComboLoopInfo:
        """
        True when an infinite combo variant includes card_name and at least one anchor.

        Partners are anchor cards that appear on the same infinite variant (not templates).
        """
        anchors = {_norm(a): a for a in anchor_cards if (a or "").strip()}
        if not card_name or not anchors:
            return ComboLoopInfo(False, ())

        variants = self.fetch_infinite_variants(card_name)
        partners: set[str] = set()
        card_key = _norm(card_name)

        for variant in variants:
            if not _is_infinite_variant(variant):
                continue
            names = _card_names_in_variant(variant)
            name_keys = {_norm(n): n for n in names}
            if card_key not in name_keys:
                continue
            overlap = set(name_keys.keys()) & set(anchors.keys())
            overlap.discard(card_key)
            if not overlap:
                continue
            for key in overlap:
                partners.add(anchors[key])

        ordered = tuple(sorted(partners, key=str.lower))
        return ComboLoopInfo(bool(ordered), ordered)


@lru_cache(maxsize=1)
def get_combo_checker() -> ComboChecker:
    from config import SPELLBOOK_FETCH_LIVE

    return ComboChecker(fetch_live=SPELLBOOK_FETCH_LIVE)


def enrich_predictions_with_combos(
    predictions_df,
    *,
    anchor_cards: list[str],
    combo_checker: Optional[ComboChecker] = None,
):
    """Add has_infinite_loop and combo_with columns for ranked spec rows."""
    import pandas as pd

    if predictions_df is None or predictions_df.empty:
        return predictions_df

    checker = combo_checker or get_combo_checker()
    df = predictions_df.copy()
    has_loop = []
    combo_with = []

    for name in df["card_name"].tolist():
        info = checker.loop_with_anchors(name, anchor_cards)
        has_loop.append(info.has_infinite_loop)
        combo_with.append(info.combo_with)

    df["has_infinite_loop"] = has_loop
    df["combo_with"] = combo_with
    return df


def anchor_cards_for_stage(
    *,
    commander_name: str = "",
    known_inclusions: Optional[set[str]] = None,
) -> list[str]:
    """Cards that can complete an infinite loop for the current release stage."""
    anchors = []
    if commander_name:
        anchors.append(commander_name)
    if known_inclusions:
        anchors.extend(sorted(known_inclusions))
    return anchors
