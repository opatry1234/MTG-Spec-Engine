"""
Price history ingestion.

Spike grading uses the local CSV at data/raw/ by default (no API).

Examples:
    python ingest/prices.py --verify-spikes-csv
    python ingest/prices.py --download-all-prices
    python ingest/prices.py --cards "Sol Ring" --release-date 2025-04-15
"""

from __future__ import annotations

import argparse
import gzip
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.spike_check import check_card_spike, load_price_series
from backtester.spike_csv import get_spike_index, spike_csv_stats
from config import ALL_PRICES_GZ, ALL_PRICES_PATH, MTGJSON_CACHE_DIR, SPIKE_CSV_PATH, TCGAPIS_KEY
from db.engine import create_session_factory

MTGJSON_ALL_PRICES_URL = "https://mtgjson.com/api/v5/AllPrices.json.gz"


def download_all_prices(force: bool = False) -> Path:
    """Download MTGJSON AllPrices (~90 day history) to local cache."""
    MTGJSON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if ALL_PRICES_GZ.exists() and not force:
        print(f"Already cached: {ALL_PRICES_GZ}")
        return ALL_PRICES_GZ

    print(f"Downloading {MTGJSON_ALL_PRICES_URL} …")
    resp = requests.get(MTGJSON_ALL_PRICES_URL, stream=True, timeout=600)
    resp.raise_for_status()
    with open(ALL_PRICES_GZ, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    print(f"Saved {ALL_PRICES_GZ}")
    return ALL_PRICES_GZ


def decompress_all_prices() -> Path:
    if not ALL_PRICES_GZ.exists():
        download_all_prices()
    print(f"Decompressing to {ALL_PRICES_PATH} …")
    with gzip.open(ALL_PRICES_GZ, "rb") as src, open(ALL_PRICES_PATH, "wb") as dst:
        dst.write(src.read())
    return ALL_PRICES_PATH


def verify_spikes_csv() -> None:
    stats = spike_csv_stats()
    if not stats.get("loaded"):
        print(f"CSV not found: {SPIKE_CSV_PATH}")
        return
    print(f"Loaded {stats['rows']:,} spike rows, {stats['unique_cards']:,} unique oracle names")
    print(f"Path: {stats['path']}")
    get_spike_index(str(SPIKE_CSV_PATH))


def backfill_cards(session, card_names: list[str], release_date: date) -> None:
    start = release_date + timedelta(days=-35)
    end = release_date + timedelta(days=70)
    for name in card_names:
        if SPIKE_CSV_PATH.exists():
            hit = check_card_spike(session, name, release_date, fetch_if_missing=False)
            print(f"  {name}: spike={hit.get('had_spike')} source={hit.get('price_source')}")
            continue
        series, source = load_price_series(session, name, start, end, fetch_if_missing=True)
        print(f"  {name}: {len(series)} points ({source})")


def main():
    parser = argparse.ArgumentParser(description="Price history tools")
    parser.add_argument("--verify-spikes-csv", action="store_true")
    parser.add_argument("--download-all-prices", action="store_true")
    parser.add_argument("--decompress", action="store_true")
    parser.add_argument("--cards", nargs="+", help="Card names to backfill")
    parser.add_argument("--release-date", type=lambda s: date.fromisoformat(s))
    args = parser.parse_args()

    if args.verify_spikes_csv:
        verify_spikes_csv()
    if args.download_all_prices:
        download_all_prices(force=True)
    if args.decompress:
        decompress_all_prices()

    if args.cards:
        if not args.release_date:
            parser.error("--release-date required with --cards")
        if not SPIKE_CSV_PATH.exists() and not TCGAPIS_KEY and not ALL_PRICES_GZ.exists():
            print(
                "Warning: no spikes CSV, TCGAPIS_KEY, or AllPrices cache — "
                "add the spikes CSV or run --download-all-prices"
            )
        session = create_session_factory()()
        try:
            backfill_cards(session, args.cards, args.release_date)
        finally:
            session.close()


if __name__ == "__main__":
    main()
