#!/usr/bin/env python3
"""
Sync Supabase card_prices_current → local card_prices cache.

The engine reads price locally (fast, no per-card network during scoring). Run
this before a scoring/backtest session, or on a schedule. Needs SUPABASE_URL and
SUPABASE_KEY (anon or service) in the environment / config.

    python ingest/sync_prices.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL, SUPABASE_URL, SUPABASE_KEY

SELECT = "card_name,price_usd,price_usd_foil,available_copies,seller_count,copies_per_seller"


def sync_prices_from_supabase() -> int:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Set SUPABASE_URL and SUPABASE_KEY (anon or service) to sync prices."
        )
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    rows: list[dict] = []
    step, start = 10000, 0
    while True:
        h = dict(headers); h["Range"] = f"{start}-{start + step - 1}"
        r = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/card_prices_current?select={SELECT}",
            headers=h, timeout=120,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase read failed {r.status_code}: {r.text[:200]}")
        page = r.json() if r.content else []
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        if len(page) < step:
            break
        start += step

    today = date.today()
    engine = create_engine(DATABASE_URL)
    session = sessionmaker(bind=engine)()
    try:
        session.execute(text("DELETE FROM card_prices"))
        payload = [
            {
                "card_name": row["card_name"],
                "price_usd": row.get("price_usd"),
                "price_usd_foil": row.get("price_usd_foil"),
                "available_copies": row.get("available_copies"),
                "seller_count": row.get("seller_count"),
                "copies_per_seller": row.get("copies_per_seller"),
                "as_of_date": today,
            }
            for row in rows if row.get("card_name")
        ]
        for i in range(0, len(payload), 1000):
            session.execute(
                text(
                    "INSERT INTO card_prices "
                    "(card_name, price_usd, price_usd_foil, available_copies, "
                    " seller_count, copies_per_seller, as_of_date) VALUES "
                    "(:card_name, :price_usd, :price_usd_foil, :available_copies, "
                    " :seller_count, :copies_per_seller, :as_of_date)"
                ),
                payload[i : i + 1000],
            )
        session.commit()
    finally:
        session.close()
    print(f"Synced {len(rows)} prices into local card_prices (as_of {today}).")
    return len(rows)


if __name__ == "__main__":
    sync_prices_from_supabase()
