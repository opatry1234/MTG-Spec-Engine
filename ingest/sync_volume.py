#!/usr/bin/env python3
"""
Sync Supabase card_prices_history (volume rows) → local volume_history cache.

The engine reads sell-through velocity locally (fast, no per-card network during
scoring). Run this after a volume scrape (ingest/snapshot_volume.py) and before a
scoring/backtest session. Needs SUPABASE_URL and SUPABASE_KEY (anon or service).

    python ingest/sync_volume.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL, SUPABASE_URL, SUPABASE_KEY
from db.schema import Base, VolumeHistory  # noqa: F401  (Base.create_all needs the model imported)

SELECT = "card_name,snapshot_date,quantity_sold,transaction_count,market_price"


def sync_volume_from_supabase() -> int:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Set SUPABASE_URL and SUPABASE_KEY (anon or service) to sync volume."
        )
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    rows: list[dict] = []
    step, start = 1000, 0  # PostgREST caps responses at 1000 rows; page in 1000s
    while True:
        h = dict(headers); h["Range"] = f"{start}-{start + step - 1}"
        r = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/card_prices_history"
            f"?select={SELECT}&quantity_sold=not.is.null",
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

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine, tables=[VolumeHistory.__table__])
    session = sessionmaker(bind=engine)()
    try:
        session.execute(text("DELETE FROM volume_history"))
        payload = [
            {
                "card_name": row["card_name"],
                "snapshot_date": row["snapshot_date"],
                "quantity_sold": row.get("quantity_sold"),
                "transaction_count": row.get("transaction_count"),
                "market_price": row.get("market_price"),
            }
            for row in rows
            if row.get("card_name") and row.get("snapshot_date")
        ]
        for i in range(0, len(payload), 1000):
            session.execute(
                text(
                    "INSERT OR REPLACE INTO volume_history "
                    "(card_name, snapshot_date, quantity_sold, transaction_count, market_price) "
                    "VALUES (:card_name, :snapshot_date, :quantity_sold, "
                    ":transaction_count, :market_price)"
                ),
                payload[i : i + 1000],
            )
        session.commit()
    finally:
        session.close()
    cards = len({r["card_name"] for r in rows})
    print(f"Synced {len(rows)} volume rows across {cards} cards into local volume_history.")
    return len(rows)


if __name__ == "__main__":
    sync_volume_from_supabase()
