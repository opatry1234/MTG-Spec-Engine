#!/usr/bin/env python3
"""
TCGplayer price+volume chart scraper.

Pulls the per-product price-history chart that the TCGplayer product page renders,
which is the ONLY source of historical *sales volume* (quantity sold). Endpoint:

    https://infinite-api.tcgplayer.com/price/history/{productId}/detailed?range={range}

range = "month" | "quarter" | "annual" (annual ≈ last 12 months — the deepest
history available anywhere; older volume was never archived).

Returns per (productId, variant=Normal/Foil) a list of daily buckets:
  {date, market_price, quantity_sold, transaction_count, low_sale, high_sale}

NOTE: this hits a TCGplayer internal endpoint, which is against their ToS for
automated use. It's here for personal/research use at a low request rate. Keep
RATE_LIMIT polite and the watchlist small; prefer TCGCSV for bulk price.
"""

from __future__ import annotations

import time
import urllib.request
import json
from datetime import datetime
from typing import Optional

CHART_URL = "https://infinite-api.tcgplayer.com/price/history/{pid}/detailed?range={rng}"
UA = "Mozilla/5.0 (research price/volume capture)"
RATE_LIMIT_SEC = 1.0  # be polite — one request/sec


def _parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_chart(payload: dict, variant: str = "Normal") -> list[dict]:
    """Flatten the chart payload into daily rows for the chosen variant."""
    rows: list[dict] = []
    for entry in (payload or {}).get("result", []) or []:
        if (entry.get("variant") or "").lower() != variant.lower():
            continue
        for b in entry.get("buckets", []) or []:
            d = _parse_date(b.get("bucketStartDate"))
            if not d:
                continue
            rows.append({
                "date": d,
                "variant": entry.get("variant"),
                "market_price": _num(b.get("marketPrice")),
                "quantity_sold": int(_num(b.get("quantitySold")) or 0),
                "transaction_count": int(_num(b.get("transactionCount")) or 0),
                "low_sale": _num(b.get("lowSalePrice")),
                "high_sale": _num(b.get("highSalePrice")),
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_chart(product_id: int, rng: str = "annual") -> list[dict]:
    """Fetch + parse the Normal-variant price/volume history for a product."""
    url = CHART_URL.format(pid=product_id, rng=rng)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 — caller treats failures as "no data"
        return []
    return parse_chart(payload, variant="Normal")


def fetch_many(product_ids: list[int], rng: str = "annual", log=print) -> dict[int, list[dict]]:
    """Sequential, rate-limited fetch for a watchlist of product ids."""
    out: dict[int, list[dict]] = {}
    for i, pid in enumerate(product_ids, 1):
        out[pid] = fetch_chart(pid, rng=rng)
        if i % 100 == 0:
            log(f"  chart-scraped {i}/{len(product_ids)}")
        time.sleep(RATE_LIMIT_SEC)
    return out


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 558355
    rng = sys.argv[2] if len(sys.argv) > 2 else "month"
    rows = fetch_chart(pid, rng=rng)
    print(f"{len(rows)} daily buckets for product {pid} ({rng})")
    for r in rows[-5:]:
        print(f"  {r['date']}  price={r['market_price']}  sold={r['quantity_sold']}  txns={r['transaction_count']}")
