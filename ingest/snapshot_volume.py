#!/usr/bin/env python3
"""
Daily price+VOLUME capture from the TCGplayer chart endpoint → Supabase.

Scryfall/TCGCSV give price but no sales volume. This scrapes the per-product
chart (price + quantity sold) and writes daily buckets into card_prices_history.

Coverage strategy — the WHOLE playable universe, not a hand-picked subset:
  The pool is every is_spec_card (non-junk, paper, tradeable) card with a
  tcgplayer_id — ~32k cards. The TCGplayer chart endpoint is rate-limited by
  DataDome (~150 cards before a 403), so a single run can't cover the pool. Each
  run instead scrapes ONE rolling slice of WATCHLIST_MAX (~150) cards. The slice
  advances deterministically with wall-clock time (one slice per ROTATE_SECONDS),
  so a cron firing every 90 min sweeps the full pool in ~2 weeks and then keeps
  re-freshing it on the same rotation — no stored offset/state required.

Modes:
  default        last ~month of buckets per card (incremental refresh)
  --seed         last ~12 months per card (history backfill; use during the sweep)

Env:
  SUPABASE_URL, SUPABASE_SERVICE_KEY
  VOLUME_WATCHLIST_MAX       slice size per run (default 150 ≈ one throttle burst)
  VOLUME_WATCHLIST_OFFSET    manual slice start (overrides the time-based rotation)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingest.snapshot_to_supabase import (  # reuse: same bulk + Supabase plumbing
    BULK_INDEX, UA, is_spec_card, _sb, _post, log,
)
from ingest.tcgplayer_chart import fetch_chart, RATE_LIMIT_SEC, Throttled

WATCHLIST_MAX = int(os.getenv("VOLUME_WATCHLIST_MAX", "150"))  # ~one throttle burst
ROTATE_SECONDS = int(os.getenv("VOLUME_ROTATE_SECONDS", "5400"))  # 90 min, matches cron
_OFFSET_ENV = os.getenv("VOLUME_WATCHLIST_OFFSET")  # manual override of the rotation


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


def build_pool(bulk_path: str) -> list[tuple[str, int]]:
    """The full playable universe as a stable (card_name, tcgplayer_id) list.

    Every is_spec_card card with a tcgplayer_id and a price — no cheap/rank cutoff,
    so velocity is available for ANY candidate the scorer might consider. Cheapest
    printing's id is kept (the tradeable floor). Ordered by EDHREC rank then name so
    the rotation covers the most-played cards first, but everything is reached within
    one full cycle.
    """
    with open(bulk_path, encoding="utf-8") as f:
        cards = json.load(f)
    pool: dict[str, tuple[int, int, float]] = {}  # name -> (edhrec_rank, tcgplayer_id, usd)
    for c in cards:
        if not is_spec_card(c):
            continue
        name = c.get("name")
        tid = c.get("tcgplayer_id")
        usd = c.get("prices", {}).get("usd")
        if not name or not tid or not usd:
            continue
        rank = c.get("edhrec_rank")
        rank = int(rank) if rank is not None else 10**9
        cur = pool.get(name)
        if cur is None or float(usd) < cur[2]:
            pool[name] = (rank, int(tid), float(usd))
    ordered = sorted(pool.items(), key=lambda kv: (kv[1][0], kv[0]))
    return [(name, v[1]) for name, v in ordered]


def select_slice(pool: list, max_n: int) -> tuple[list, int]:
    """Rolling slice of the pool. Time-derived offset (one slice per ROTATE_SECONDS)
    unless VOLUME_WATCHLIST_OFFSET is set. Wraps around the end of the pool."""
    n = len(pool)
    if n == 0 or max_n >= n:
        return pool, 0
    if _OFFSET_ENV is not None:
        off = int(_OFFSET_ENV) % n
    else:
        off = (int(time.time() // ROTATE_SECONDS) * max_n) % n
    end = off + max_n
    if end <= n:
        return pool[off:end], off
    return pool[off:] + pool[: end - n], off  # wrap


def run(seed: bool = False) -> None:
    rng = "annual" if seed else "month"
    url, headers = _sb()
    bulk = _download_bulk()
    pool = build_pool(bulk)
    watch, off = select_slice(pool, WATCHLIST_MAX)
    log(f"Volume pool: {len(pool)} cards; scraping slice [{off}:{off + len(watch)}] "
        f"({len(watch)} cards, range={rng})")

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
        if i % 50 == 0:
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
