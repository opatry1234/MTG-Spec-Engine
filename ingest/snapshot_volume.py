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

Printing selection (which TCGplayer product per card):
  A card has many printings (base, extended art, borderless, showcase, promo …),
  each its own product with its own sales. We capture ONE: the cheapest
  STANDARD-frame, non-promo printing (cheapest overall if a card has only
  variants). Empirically (data/raw spike history) ~80% of spec-range spikes happen
  on standard printings, and extended-art/borderless do NOT spike harder — so the
  standard printing is the right velocity proxy. The choice is PINNED in
  volume_card_source so the weekly series can't drift between printings as prices
  move. (If that table doesn't exist yet, we degrade to per-run selection.)

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
_OFFSET_ENV = os.getenv("VOLUME_WATCHLIST_OFFSET") or None  # "" (unset/empty) → time-based


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


def variant_of(card: dict) -> str:
    """Classify a printing's visual variant from Scryfall fields.

    Only genuine premium treatments count as variants. Ordinary frame_effects
    (legendary, nyxtouched, miracle, devoid, snow, …) are NOT variants — a normal
    legendary creature is a standard printing.
    """
    if card.get("promo"):
        return "promo"
    fx = card.get("frame_effects") or []
    border = card.get("border_color")
    if border == "borderless":
        return "borderless"
    if "extendedart" in fx:
        return "extended art"
    if "showcase" in fx:
        return "showcase"
    if card.get("full_art"):
        return "full art"
    if border in ("gold", "silver"):  # championship / un-set oddities
        return "other-variant"
    return "standard"


def build_pool(bulk_path: str, pinned: dict[str, int]) -> tuple[list, dict]:
    """The full playable universe as a stable (card_name, tcgplayer_id) list.

    For each card we pick the cheapest STANDARD-frame, non-promo printing (cheapest
    overall if it has only variants) — the velocity proxy where spikes actually
    happen. Cards already in ``pinned`` keep their pinned tcgplayer_id (no drift);
    the rest get a freshly-selected one returned as new_pins for persisting.

    Returns (ordered [(name, tid)], new_pins {name: pin-row}). Ordered by EDHREC
    rank then name so the rotation reaches the most-played cards first.
    """
    with open(bulk_path, encoding="utf-8") as f:
        cards = json.load(f)

    agg: dict[str, dict] = {}  # name -> {rank, std:(usd,tid,set,var), any:(usd,tid,set,var)}
    for c in cards:
        if not is_spec_card(c):
            continue
        name = c.get("name")
        tid = c.get("tcgplayer_id")
        usd = c.get("prices", {}).get("usd")
        if not name or not tid or not usd:
            continue
        usd = float(usd)
        cand = (usd, int(tid), (c.get("set") or "").upper(), variant_of(c))
        rank = c.get("edhrec_rank")
        rank = int(rank) if rank is not None else 10**9
        d = agg.get(name)
        if d is None:
            d = {"rank": rank, "std": None, "any": None}
            agg[name] = d
        d["rank"] = min(d["rank"], rank)
        if d["any"] is None or usd < d["any"][0]:
            d["any"] = cand
        if cand[3] == "standard" and (d["std"] is None or usd < d["std"][0]):
            d["std"] = cand

    pool: list[tuple[int, str, int]] = []
    new_pins: dict[str, dict] = {}
    for name, d in agg.items():
        if name in pinned:
            tid = pinned[name]
        else:
            sel = d["std"] or d["any"]  # prefer cheapest standard, else cheapest overall
            tid = sel[1]
            new_pins[name] = {
                "card_name": name, "tcgplayer_id": tid,
                "set_code": sel[2], "variant": sel[3], "usd": sel[0],
            }
        pool.append((d["rank"], name, tid))

    pool.sort(key=lambda x: (x[0], x[1]))
    return [(n, t) for _, n, t in pool], new_pins


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


def _load_pins(url: str, headers: dict) -> dict[str, int]:
    """card_name -> pinned tcgplayer_id from volume_card_source. Empty if the table
    is absent (graceful: we just fall back to per-run selection)."""
    pins: dict[str, int] = {}
    start, step = 0, 1000
    h_base = {"apikey": headers["apikey"], "Authorization": headers["Authorization"]}
    while True:
        h = dict(h_base); h["Range"] = f"{start}-{start + step - 1}"
        req = urllib.request.Request(
            f"{url}/rest/v1/volume_card_source?select=card_name,tcgplayer_id", headers=h)
        try:
            page = json.loads(urllib.request.urlopen(req, timeout=60).read())
        except Exception as exc:  # noqa: BLE001
            log(f"  (volume_card_source unavailable — no pinning this run: {exc})")
            return {}
        if not page:
            break
        for row in page:
            pins[row["card_name"]] = row["tcgplayer_id"]
        if len(page) < step:
            break
        start += step
    return pins


def run(seed: bool = False) -> None:
    rng = "annual" if seed else "month"
    url, headers = _sb()
    pins = _load_pins(url, headers)
    bulk = _download_bulk()
    pool, new_pins = build_pool(bulk, pins)
    watch, off = select_slice(pool, WATCHLIST_MAX)

    # Pin the printings we're about to scrape (≤ slice size), so future runs reuse them.
    to_pin = [new_pins[n] for n, _ in watch if n in new_pins]
    if to_pin:
        try:
            _post(url, headers, "volume_card_source", to_pin,
                  "resolution=merge-duplicates", on_conflict="card_name")
        except Exception as exc:  # noqa: BLE001
            log(f"  (could not persist pins — run volume_card_source DDL: {exc})")

    log(f"Volume pool: {len(pool)} cards ({len(pins)} pinned); scraping slice "
        f"[{off}:{off + len(watch)}] ({len(watch)} cards, range={rng})")

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
