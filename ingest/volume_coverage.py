#!/usr/bin/env python3
"""
Volume-coverage sanity check: how many cards in the pool have volume data yet.

Prints a one-line summary against Supabase — distinct cards with volume, % of the
price pool covered, row count, and date range. Handy to watch the full-pool sweep
fill in (~150 cards per 90-min run). Needs SUPABASE_URL + SUPABASE_KEY/SERVICE_KEY.

    python ingest/volume_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from config import SUPABASE_URL, SUPABASE_KEY


def _headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_KEY (anon or service).")
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def _count(path: str) -> int:
    h = dict(_headers()); h["Prefer"] = "count=exact"; h["Range"] = "0-0"
    r = requests.get(f"{SUPABASE_URL.rstrip('/')}/rest/v1/{path}", headers=h, timeout=60)
    r.raise_for_status()
    return int(r.headers.get("content-range", "0-0/0").split("/")[-1])


def _distinct_cards_with_volume() -> int:
    seen: set[str] = set()
    start, step = 0, 1000
    base = _headers()
    while True:
        h = dict(base); h["Range"] = f"{start}-{start + step - 1}"
        r = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/card_prices_history"
            f"?select=card_name&quantity_sold=not.is.null",
            headers=h, timeout=60,
        )
        r.raise_for_status()
        page = r.json() if r.content else []
        if not page:
            break
        seen.update(row["card_name"] for row in page)
        if len(page) < step:
            break
        start += step
    return len(seen)


def _date(order: str) -> str | None:
    r = requests.get(
        f"{SUPABASE_URL.rstrip('/')}/rest/v1/card_prices_history"
        f"?select=snapshot_date&quantity_sold=not.is.null&order=snapshot_date.{order}&limit=1",
        headers=_headers(), timeout=60,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0]["snapshot_date"] if rows else None


def main() -> None:
    pool = _count("card_prices_current?select=card_name")
    cards = _distinct_cards_with_volume()
    rows = _count("card_prices_history?select=card_name&quantity_sold=not.is.null")
    pct = round(100.0 * cards / pool, 2) if pool else 0.0
    print("=== Volume coverage ===")
    print(f"  cards with volume : {cards:,} / {pool:,} price pool  ({pct}%)")
    print(f"  volume rows       : {rows:,}")
    print(f"  date range        : {_date('asc')} → {_date('desc')}")


if __name__ == "__main__":
    main()
