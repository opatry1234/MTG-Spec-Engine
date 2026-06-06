"""
Pre-release spec eligibility — cards must exist before the precon release.

At commander pre-release (stage 2), you can only spec on cards that already
had a printing on or before the parent set's release date. A card whose first
printing comes from a later set could never have been a realistic target.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.schema import CardPrinting
from engine.commander_legality import was_commander_legal_at

EarliestPrintingMap = Dict[str, date]


def spec_anchor_date(deck) -> Optional[date]:
    """The spec anchor: the day the full decklist became public.

    Phase-3-only refactor: all point-in-time logic (feature as-of, eligibility
    cutoff, spike-timing floor) keys off the decklist reveal date — the moment a
    spec became actionable. Falls back to release_date when the reveal date is
    unknown (e.g. not yet ingested).
    """
    reveal = getattr(deck, "decklist_reveal_date", None)
    return reveal or getattr(deck, "release_date", None)


def build_earliest_printing_map(session: Session) -> EarliestPrintingMap:
    """Oracle card name (lower) -> earliest known printing date."""
    rows = (
        session.query(
            CardPrinting.card_name,
            func.min(CardPrinting.released_at),
        )
        .filter(CardPrinting.released_at.isnot(None))
        .group_by(CardPrinting.card_name)
        .all()
    )
    return {name.lower(): earliest for name, earliest in rows if name and earliest}


def earliest_printing_date(
    card_name: str,
    earliest_map: EarliestPrintingMap,
) -> Optional[date]:
    if not card_name:
        return None
    return earliest_map.get(card_name.lower())


def was_spec_eligible_at_reveal(
    card_name: str,
    reveal_date: Optional[date],
    earliest_map: EarliestPrintingMap,
    *,
    check_commander_legality: bool = True,
) -> bool:
    """
    True when the card existed on or before decklist reveal and was Commander-legal.
    """
    if not reveal_date or not card_name:
        return True

    earliest = earliest_printing_date(card_name, earliest_map)
    if earliest is None:
        return False
    if earliest > reveal_date:
        return False

    if check_commander_legality and not was_commander_legal_at(card_name, reveal_date):
        return False
    return True


def was_spec_eligible_at_prerelease(
    card_name: str,
    release_date: Optional[date],
    earliest_map: EarliestPrintingMap,
    *,
    check_commander_legality: bool = True,
) -> bool:
    """Backward-compatible alias — cutoff is the spec anchor (reveal) date."""
    return was_spec_eligible_at_reveal(
        card_name,
        release_date,
        earliest_map,
        check_commander_legality=check_commander_legality,
    )
