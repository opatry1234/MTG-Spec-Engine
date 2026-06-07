#!/usr/bin/env python3
"""
Daily price+VOLUME capture from the TCGplayer chart endpoint → Supabase.

Scryfall/TCGCSV give price but no sales volume. This scrapes the per-product
chart (price + quantity sold) for a BOUNDED watchlist of spec-relevant cards and
writes daily buckets into card_prices_history. The chart scrape is ~1 req/sec
(polite + ToS-sensitive), so we cap the watchlist — ~1200 cards ≈ 20 min.

Watchlist = cheapest-printing, non-junk cards that are plausible spec targets
(cheap enough to spike, not staples), ranked toward recent/affordable cards.

Modes:
  default        last ~month of buckets per card (daily incremental)
  --seed         last ~12 months per card (one-time history backfill)

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, plus optional VOLUME_WATCHLIST_MAX.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingest.snapshot_to_supabase import (  # reuse: same bulk + Supabase plumbing
    BULK_INDEX, UA, is_spec_card, _sb, _post, log,
)
from ingest.tcgplayer_chart import fetch_chart, RATE_LIMIT_SEC  # noqa: F401

WATCHLIST_MAX = int(os.getenv("VOLUME_WATCHLIST_MAX", "1200"))


def _download_bulk() -> str:
    idx = json.loads(urllib.request.urlopen(
        urllib.request.Request(BULK_INDEX, headers={"User-Agent": UA}), timeout=120
    ).read())
    uri = next(o["download_uri"] for o in idx["data"] if o["type"] == "default_cards")
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    with urllib.request.urlopen(urllib.request.Request(uri, headers={"User-Agent": UA}), timeout=1200) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return tmp


def build_watchlist(bulk_path: str, cap: int) -> list[tuple[str, int]]:
    """Cheapest-printing (card_name, tcgplayer_id) for spec-relevant cards, capped.

    Prefers affordable cards (more spike upside) and requires a tcgplayer_id."""
    with open(bulk_path, encoding="utf-8") as f:
        cards = json.load(f)
    best: dict[str, tuple[float, int]] = {}  # name -> (usd, tcgplayer_id)
    for c in cards:
        if not is_spec_card(c):
            continue
        tid = c.get("tcgplayer_id")
        usd = c.get("prices", {}).get("usd")
        if not tid or not usd:
            continue
        usd = float(usd)
        if usd > 30:  # very expensive cards aren't the spec profile we want
            continue
        if c["name"] not in best or usd < best[c["name"]][0]:
            best[c["name"]] = (usd, int(tid))
    # rank cheapest-first (most spike-able), cap
    ranked = sorted(best.items(), key=lambda kv: kv[1][0])[:cap]
    return [(name, tid) for name, (usd, tid) in ranked]


def run(seed: bool = False) -> None:
    rng = "annual" if seed else "month"
    url, headers = _sb()
    bulk = _download_bulk()
    watch = build_watchlist(bulk, WATCHLIST_MAX)
    log(f"Volume watchlist: {len(watch)} cards (range={rng})")

    rows = []
    for i, (name, tid) in enumerate(watch, 1):
        for b in fetch_chart(tid, rng=rng):
            rows.append({
                "card_name": name,
                "snapshot_date": b["date"].isoformat(),
                "market_price": b["market_price"],
                "quantity_sold": b["quantity_sold"],
                "transaction_count": b["transaction_count"],
            })
        if i % 100 == 0:
            log(f"  {i}/{len(watch)} cards scraped, {len(rows)} buckets")
        import time; time.sleep(RATE_LIMIT_SEC)

    _post(url, headers, "card_prices_history", rows, "resolution=ignore-duplicates")
    log(f"Wrote {len(rows)} price+volume buckets across {len(watch)} cards")
    os.unlink(bulk)


if __name__ == "__main__":
    try:
        run(seed="--seed" in sys.argv)
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        sys.exit(1)
