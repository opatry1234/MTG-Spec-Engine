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
OFFSET = int(os.getenv("VOLUME_WATCHLIST_OFFSET", "0"))  # rotate coverage across runs


def _download_bulk() -> str:
    hdrs = {"User-Agent": UA, "Accept": "application/json"}  # Scryfall 400s without Accept
    idx = json.loads(urllib.request.urlopen(
        urllib.request.Request(BULK_INDEX, headers=hdrs), timeout=120
    ).read())
    uri = next(o["download_uri"] for o in idx["data"] if o["type"] == "default_cards")
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    with urllib.request.urlopen(urllib.request.Request(uri, headers=hdrs), timeout=1200) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return tmp


def _load_priority() -> set[str]:
    p = Path(__file__).parent.parent / "data" / "volume_priority_cards.csv"
    if not p.exists():
        return set()
    import csv
    with open(p, newline="") as f:
        return {row[0].strip() for row in csv.reader(f) if row and row[0].strip()}


def build_watchlist(bulk_path: str, priority_names: set[str]) -> tuple[list, list]:
    """Return (priority, fill) lists of (card_name, tcgplayer_id).

    priority = cards we always want volume for (golden specs etc.), regardless of
    rank/price. fill = EDHREC-ranked, affordable spec cards to round out the run.
    Cheapest printing per oracle name; requires a tcgplayer_id + a price."""
    with open(bulk_path, encoding="utf-8") as f:
        cards = json.load(f)
    pool: dict[str, tuple[float, int, int]] = {}  # name -> (usd, tcgplayer_id, edhrec_rank)
    for c in cards:
        name = c.get("name")
        if not name:
            continue
        is_pri = name in priority_names
        if not is_spec_card(c) and not is_pri:
            continue
        tid = c.get("tcgplayer_id")
        usd = c.get("prices", {}).get("usd")
        if not tid or not usd:
            continue
        usd = float(usd)
        rank = c.get("edhrec_rank")
        cur = pool.get(name)
        if cur is None or usd < cur[0]:
            pool[name] = (usd, int(tid), int(rank) if rank is not None else 10**9)
    priority = [(n, pool[n][1]) for n in sorted(priority_names) if n in pool]
    pset = {n for n, _ in priority}
    fill_ranked = sorted(
        ((n, v) for n, v in pool.items() if n not in pset and v[0] <= 30 and v[2] < 10**9),
        key=lambda kv: kv[1][2],
    )
    fill = [(n, v[1]) for n, v in fill_ranked]
    return priority, fill


def run(seed: bool = False) -> None:
    rng = "annual" if seed else "month"
    import time
    from ingest.tcgplayer_chart import Throttled

    url, headers = _sb()
    bulk = _download_bulk()
    priority, fill = build_watchlist(bulk, _load_priority())
    # priority cards always included; OFFSET rotates the EDHREC fill across runs
    fill_slice = fill[OFFSET:OFFSET + max(0, WATCHLIST_MAX - len(priority))]
    watch = priority + fill_slice
    log(f"Volume watchlist: {len(watch)} cards "
        f"({len(priority)} priority + {len(fill_slice)} fill, range={rng}, offset={OFFSET})")

    rows = []
    scraped = 0
    for i, (name, tid) in enumerate(watch, 1):
        try:
            buckets = fetch_chart(tid, rng=rng)
        except Throttled as t:
            log(f"  TCGplayer throttled us at card {i} ({t}); stopping early with {scraped} cards captured.")
            break
        scraped += 1
        for b in buckets:
            rows.append({
                "card_name": name,
                "snapshot_date": b["date"].isoformat(),
                "market_price": b["market_price"],
                "quantity_sold": b["quantity_sold"],
                "transaction_count": b["transaction_count"],
            })
        if i % 100 == 0:
            log(f"  {i}/{len(watch)} cards scraped, {len(rows)} buckets")
        time.sleep(RATE_LIMIT_SEC)

    # one product can return multiple Normal SKU entries with overlapping dates →
    # dedup on (card_name, snapshot_date) before writing (constraint key)
    seen: set = set()
    deduped = []
    for r in rows:
        k = (r["card_name"], r["snapshot_date"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)

    _post(url, headers, "card_prices_history", deduped, "resolution=ignore-duplicates",
          on_conflict="card_name,snapshot_date")
    log(f"Wrote {len(deduped)} price+volume buckets across {scraped} cards captured "
        f"({len(rows) - len(deduped)} dupes dropped)")
    os.unlink(bulk)


if __name__ == "__main__":
    try:
        run(seed="--seed" in sys.argv)
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        sys.exit(1)
