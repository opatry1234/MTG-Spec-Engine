"""
Align commander precon shelf dates from historical spike CSV clusters.

Set release dates in the decklist Excel reflect the parent set launch
(e.g. Aetherdrift 2025-02-14) while commander-deck-specific cards often
spike when the precon product hits shelves (e.g. Commander: Aetherdrift).

Only updates precon_release_date when the inferred shelf date is near the
deck's release_date — this avoids false positives from unrelated monthly
spike report batches on shared staples.

Run with: python ingest/align_precon_releases.py
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Set

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.precon_products import resolve_commander_spike_set
from backtester.spike_csv import get_spike_index
from config import DATABASE_URL, SPIKE_TCGPLAYER_CSV_PATH
from db.schema import CommanderDeck, DeckCard


def _deck_card_names(session, deck_id: int) -> Set[str]:
    rows = session.query(DeckCard.card_name).filter(DeckCard.deck_id == deck_id).all()
    return {r[0].lower() for r in rows if r[0]}


def _infer_precon_release_date(
    deck_card_names: Set[str],
    spike_records_by_name: dict,
    commander_spike_set: str,
    *,
    min_matches: int = 3,
) -> Optional[date]:
    """Modal report date for spikes on this deck's cards in its commander product set."""
    commander_dates: list[date] = []
    for name in deck_card_names:
        for rec in spike_records_by_name.get(name, []):
            if rec.set_name == commander_spike_set:
                commander_dates.append(rec.report_date)

    if len(commander_dates) < min_matches:
        return None

    counts = Counter(commander_dates)
    modal_date, modal_count = counts.most_common(1)[0]
    if modal_count < min_matches:
        return None
    return modal_date


def _plausible_precon_date(
    release_date: date,
    inferred_date: date,
    *,
    min_offset_days: int = 14,
    max_offset_days: int = 150,
) -> bool:
    """Reject report-batch false positives far from the deck's release."""
    offset = (inferred_date - release_date).days
    return min_offset_days <= offset <= max_offset_days


def align_precon_release_dates(
    session,
    *,
    dry_run: bool = False,
    min_matches: int = 3,
    min_offset_days: int = 14,
    max_offset_days: int = 150,
) -> Dict[str, int]:
    if not SPIKE_TCGPLAYER_CSV_PATH.exists():
        print(f"Spike CSV not found: {SPIKE_TCGPLAYER_CSV_PATH}")
        return {"updated": 0, "skipped": 0}

    get_spike_index()
    index = get_spike_index()
    product_set_cache: dict[str, Optional[str]] = {}

    stats = {"updated": 0, "skipped": 0, "unchanged": 0, "cleared": 0}
    decks = session.query(CommanderDeck).order_by(CommanderDeck.id).all()

    for deck in decks:
        if not deck.release_date:
            if deck.precon_release_date and not dry_run:
                deck.precon_release_date = None
                stats["cleared"] += 1
            stats["skipped"] += 1
            continue

        product = (deck.product or "").upper()
        if product not in product_set_cache:
            product_set_cache[product] = resolve_commander_spike_set(product)
        commander_set = product_set_cache[product]

        if not commander_set:
            if deck.precon_release_date and not dry_run:
                deck.precon_release_date = None
                stats["cleared"] += 1
            stats["skipped"] += 1
            continue

        card_names = _deck_card_names(session, deck.id)
        inferred = _infer_precon_release_date(
            card_names,
            index,
            commander_set,
            min_matches=min_matches,
        )

        if inferred is None or not _plausible_precon_date(
            deck.release_date,
            inferred,
            min_offset_days=min_offset_days,
            max_offset_days=max_offset_days,
        ):
            if deck.precon_release_date and not dry_run:
                deck.precon_release_date = None
                stats["cleared"] += 1
            stats["skipped"] += 1
            continue

        if deck.precon_release_date == inferred:
            stats["unchanged"] += 1
            continue

        old = deck.precon_release_date.isoformat() if deck.precon_release_date else "—"
        print(
            f"Deck {deck.id} {deck.deck_name} ({deck.product} / {commander_set}): "
            f"precon_release_date {old} -> {inferred.isoformat()}"
        )
        if not dry_run:
            deck.precon_release_date = inferred
        stats["updated"] += 1

    if not dry_run and (stats["updated"] or stats["cleared"]):
        session.commit()

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Align precon shelf dates from spike CSV")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without saving")
    parser.add_argument(
        "--min-matches",
        type=int,
        default=3,
        help="Minimum commander-set deck-card spikes to infer a date",
    )
    parser.add_argument(
        "--min-offset-days",
        type=int,
        default=14,
        help="Minimum days after set release for a plausible precon shelf date",
    )
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        stats = align_precon_release_dates(
            session,
            dry_run=args.dry_run,
            min_matches=args.min_matches,
            min_offset_days=args.min_offset_days,
        )
        print(
            f"Done: updated={stats['updated']} cleared={stats.get('cleared', 0)} "
            f"unchanged={stats['unchanged']} skipped={stats['skipped']}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
