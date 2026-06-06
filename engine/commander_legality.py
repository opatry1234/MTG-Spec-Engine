"""
Commander legality at a point in time and format-driven spike detection.

Current Scryfall legality is a snapshot; ban/unban history is loaded from
data/metadata/commander_format_events.yaml for backtests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from config import BASE_DIR

FORMAT_EVENTS_PATH = BASE_DIR / "data" / "metadata" / "commander_format_events.yaml"


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


@dataclass(frozen=True)
class FormatEvent:
    event_id: str
    event_type: str
    effective_date: date
    cards: frozenset[str]
    spike_window_days: int = 45


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


@lru_cache(maxsize=1)
def load_format_events(path: Optional[Path] = None) -> List[FormatEvent]:
    yaml_path = path or FORMAT_EVENTS_PATH
    if not yaml_path.exists():
        return []

    with open(yaml_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    events: List[FormatEvent] = []
    for row in raw.get("events") or []:
        cards = frozenset(_normalize_name(c) for c in (row.get("cards") or []))
        if not cards or not row.get("effective_date"):
            continue
        events.append(
            FormatEvent(
                event_id=str(row.get("id") or row["effective_date"]),
                event_type=str(row.get("type") or "unban"),
                effective_date=_parse_date(row["effective_date"]),
                cards=cards,
                spike_window_days=int(row.get("spike_window_days") or 45),
            )
        )
    return events


def clear_format_events_cache() -> None:
    load_format_events.cache_clear()


def _unban_dates_by_card(events: List[FormatEvent]) -> Dict[str, date]:
    out: Dict[str, date] = {}
    for ev in events:
        if ev.event_type != "unban":
            continue
        for card in ev.cards:
            prev = out.get(card)
            if prev is None or ev.effective_date < prev:
                out[card] = ev.effective_date
    return out


def was_commander_legal_at(card_name: str, as_of: Optional[date]) -> bool:
    """
    True when the card was a legal Commander inclusion on as_of.

    Cards on a recorded unban list with effective_date after as_of were banned.
    """
    if not card_name or not as_of:
        return True

    key = _normalize_name(card_name)
    unban_dates = _unban_dates_by_card(load_format_events())
    unban_date = unban_dates.get(key)
    if unban_date is not None and as_of < unban_date:
        return False
    return True


def is_format_driven_spike(card_name: str, report_date: Optional[date]) -> bool:
    """
    True when a spike plausibly reflects a format ban/unban, not precon demand.

    Example: Gifts Ungiven spiking in May 2025 after the April 22 Commander unban.
    """
    if not card_name or not report_date:
        return False

    key = _normalize_name(card_name)
    for ev in load_format_events():
        if ev.event_type != "unban" or key not in ev.cards:
            continue
        window_end = ev.effective_date + timedelta(days=ev.spike_window_days)
        if ev.effective_date <= report_date <= window_end:
            return True
    return False


def format_unban_cards() -> Set[str]:
    cards: Set[str] = set()
    for ev in load_format_events():
        if ev.event_type == "unban":
            cards.update(ev.cards)
    return cards
