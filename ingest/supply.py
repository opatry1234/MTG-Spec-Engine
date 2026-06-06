"""
TCGAPIs supply data ingestion.

Fetches live listing counts from TCGPlayer via TCGAPIs and stores one row per card
per refresh date in ``supply_snapshots``. Scoring reads only the latest row per card.

Important:
- **One bulk run populates the full catalog for today** — you do NOT need months of
  snapshots unless you want to study supply *trends* over time.
- **Historical listing/inventory data is not available** from TCGPlayer, MTGJSON, or
  Scryfall. APIs expose current live listings and price history, not past seller counts.
  Backtests at reveal date use EDHREC + reveal-date price proxies instead.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import requests
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import MTGJSON_CACHE_DIR, TCGAPIS_BASE, TCGAPIS_KEY
from db.schema import Card, SupplySnapshot
from features.market_supply import edhrec_supply_proxy, normalize_seller_count, normalize_visible_inventory
from ingest.enrich_deck_metadata import fetch_mtgjson_set

LIVE_LISTINGS_URL = f"{TCGAPIS_BASE}/livelistings/{{product_id}}"
REQUEST_DELAY_SEC = 0.15
DEFAULT_PRIORITY_EDHREC_MAX = 25_000


def build_card_product_index(
    session: Session,
    *,
    set_codes: Iterable[str] | None = None,
    refresh_sets: bool = False,
) -> dict[str, int]:
    """
    Map oracle card name -> tcgplayerProductId using cached MTGJSON set files.

    When a card appears in multiple sets, later set codes in sorted order win.
    """
    if set_codes is None:
        from db.schema import CardPrinting

        set_codes = {
            (row[0] or "").upper()
            for row in session.query(CardPrinting.set_code).distinct().all()
            if row[0]
        }
        cached = {p.stem.upper() for p in MTGJSON_CACHE_DIR.glob("*.json") if p.is_file()}
        set_codes |= cached

    http = requests.Session()
    index: dict[str, int] = {}
    for set_code in sorted(set_codes):
        payload = fetch_mtgjson_set(set_code, http, use_cache=not refresh_sets)
        data = payload.get("data") or {}
        for card in data.get("cards") or []:
            name = card.get("name")
            product_id = (card.get("identifiers") or {}).get("tcgplayerProductId")
            if not name or not product_id:
                continue
            index[name] = int(product_id)
            for face in card.get("faces") or []:
                face_name = face.get("name")
                if face_name:
                    index[face_name] = int(product_id)
    return index


def parse_listing_payload(payload: dict) -> dict:
    """Normalize TCGAPIs live-listings response into supply snapshot fields."""
    listings = payload.get("listings") or []
    if not isinstance(listings, list):
        listings = []

    total_listings = payload.get("totalListings")
    if total_listings is None:
        total_listings = len(listings)

    visible_inventory = 0
    sellers: set[str] = set()
    prices: list[float] = []

    for row in listings:
        if not isinstance(row, dict):
            continue
        qty = row.get("quantity")
        visible_inventory += int(qty) if qty is not None else 1
        seller_key = row.get("sellerId") or row.get("sellerName")
        if seller_key:
            sellers.add(str(seller_key))
        price = row.get("price")
        if price is not None:
            prices.append(float(price))

    if visible_inventory == 0 and total_listings:
        visible_inventory = int(total_listings)

    low = min(prices) if prices else payload.get("lowestPrice")
    high = max(prices) if prices else None
    market = payload.get("marketPrice")
    spread = None
    if low is not None and high is not None and high > 0:
        spread = round((float(high) - float(low)) / float(high), 4)

    seller_count = len(sellers) if sellers else int(total_listings or 0)
    return {
        "tcg_listing_count": seller_count,
        "total_listings": int(visible_inventory or 0),
        "tcg_price_low": float(low) if low is not None else None,
        "tcg_price_market": float(market) if market is not None else None,
        "tcg_price_high": float(high) if high is not None else None,
        "supply_spread": spread,
    }


def fetch_live_listings(
    product_id: int,
    http: requests.Session,
    *,
    api_key: str = "",
) -> dict | None:
    """Pull live TCGPlayer listings for one productId."""
    key = api_key or TCGAPIS_KEY
    if not key:
        return None

    headers = {"x-api-key": key}
    try:
        resp = http.get(
            LIVE_LISTINGS_URL.format(product_id=product_id),
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
    except requests.RequestException:
        return None

    if isinstance(body, dict) and body.get("success") is False:
        return None
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            return data
        return body
    return None


def upsert_supply_snapshot(
    session: Session,
    card_name: str,
    snapshot_date: date,
    stats: dict,
    *,
    source: str,
) -> None:
    session.merge(
        SupplySnapshot(
            card_name=card_name,
            snapshot_date=snapshot_date,
            tcg_listing_count=stats.get("tcg_listing_count"),
            tcg_price_low=stats.get("tcg_price_low"),
            tcg_price_market=stats.get("tcg_price_market"),
            tcg_price_high=stats.get("tcg_price_high"),
            total_listings=stats.get("total_listings"),
            supply_spread=stats.get("supply_spread"),
        )
    )


def _priority_card_names(session: Session, edhrec_max: int) -> set[str]:
    names = {
        row[0]
        for row in session.query(Card.name)
        .filter(Card.edhrec_rank.isnot(None), Card.edhrec_rank <= edhrec_max)
        .all()
    }
    try:
        from config import SPIKE_CSV_PATH
        import pandas as pd

        if SPIKE_CSV_PATH.exists():
            if SPIKE_CSV_PATH.suffix.lower() in {".xlsx", ".xlsm"}:
                df = pd.read_excel(SPIKE_CSV_PATH, sheet_name="All Spikes")
            else:
                df = pd.read_csv(SPIKE_CSV_PATH)
            col = next((c for c in df.columns if "card" in c.lower() and "name" in c.lower()), None)
            if col:
                names |= {str(v).strip() for v in df[col].dropna() if str(v).strip()}
    except Exception:
        pass
    return names


def populate_edhrec_proxy_snapshots(
    session: Session,
    *,
    snapshot_date: date | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Instant catalog-wide supply proxy from EDHREC rank — no API key required.

    Writes normalized scarcity scores as listing proxies so live scoring works
    immediately. Not historical; backtests still use reveal-date proxies.
    """
    snap = snapshot_date or date.today()
    q = session.query(Card.name, Card.edhrec_rank).order_by(Card.edhrec_rank.asc().nullslast())
    if limit:
        q = q.limit(limit)

    stats = {"written": 0, "skipped": 0, "source": "edhrec_proxy"}
    for name, rank in q.all():
        visible_score, seller_score = edhrec_supply_proxy(rank)
        inv_count = int(round((1.0 - visible_score) * 500))
        seller_count = int(round((1.0 - seller_score) * 80))
        if dry_run:
            stats["written"] += 1
            continue
        upsert_supply_snapshot(
            session,
            name,
            snap,
            {
                "tcg_listing_count": seller_count,
                "total_listings": inv_count,
                "seller_count": seller_count,
            },
            source="edhrec_proxy",
        )
        stats["written"] += 1

    if not dry_run:
        session.commit()
    return stats


def pull_supply_snapshots(
    session: Session,
    *,
    scope: str = "priority",
    limit: int | None = None,
    dry_run: bool = False,
    snapshot_date: date | None = None,
    use_proxy_fallback: bool = True,
    edhrec_max: int = DEFAULT_PRIORITY_EDHREC_MAX,
) -> dict:
    """
    Bulk refresh supply snapshots.

    scope:
      - ``priority``: EDHREC top N + spike-bible cards (default, ~few thousand)
      - ``all``: every card with a resolved tcgplayerProductId
      - ``proxy``: EDHREC proxy only (instant, no API)
    """
    snap = snapshot_date or date.today()

    if scope == "proxy":
        return populate_edhrec_proxy_snapshots(
            session, snapshot_date=snap, limit=limit, dry_run=dry_run
        )

    product_index = build_card_product_index(session)
    if not product_index:
        raise RuntimeError(
            "No tcgplayerProductId mappings found. Run ingest scryfall printings "
            "and ensure MTGJSON set cache exists under data/cache/mtgjson/."
        )

    if scope == "priority":
        target_names = _priority_card_names(session, edhrec_max)
    else:
        target_names = {row[0] for row in session.query(Card.name).all()}

    work: list[tuple[str, int]] = []
    for name in sorted(target_names):
        product_id = product_index.get(name)
        if not product_id and " // " in name:
            product_id = product_index.get(name.split(" // ")[0].strip())
        if product_id:
            work.append((name, product_id))

    if limit:
        work = work[:limit]

    stats = {
        "scope": scope,
        "snapshot_date": snap.isoformat(),
        "candidates": len(target_names),
        "with_product_id": len(work),
        "written": 0,
        "api_hits": 0,
        "api_miss": 0,
        "proxy_fallback": 0,
        "skipped_no_product_id": len(target_names) - len(work),
        "source": "tcgapis_live",
    }

    if not TCGAPIS_KEY and scope != "proxy":
        if use_proxy_fallback:
            print("TCGAPIS_KEY not set — falling back to EDHREC proxy ingest.")
            proxy_stats = populate_edhrec_proxy_snapshots(
                session,
                snapshot_date=snap,
                limit=limit,
                dry_run=dry_run,
            )
            stats.update(proxy_stats)
            stats["source"] = "edhrec_proxy_fallback"
            return stats
        raise RuntimeError("TCGAPIS_KEY required for live listing ingest. Use --scope proxy otherwise.")

    http = requests.Session()
    for name, product_id in work:
        payload = fetch_live_listings(product_id, http)
        stats["api_hits"] += 1
        time.sleep(REQUEST_DELAY_SEC)

        if payload:
            parsed = parse_listing_payload(payload)
            if dry_run:
                stats["written"] += 1
                continue
            upsert_supply_snapshot(session, name, snap, parsed, source="tcgapis_live")
            stats["written"] += 1
        elif use_proxy_fallback:
            card = session.query(Card).filter(Card.name == name).first()
            visible_score, seller_score = edhrec_supply_proxy(card.edhrec_rank if card else None)
            inv_count = int(round((1.0 - visible_score) * 500))
            seller_count = int(round((1.0 - seller_score) * 80))
            if not dry_run:
                upsert_supply_snapshot(
                    session,
                    name,
                    snap,
                    {"tcg_listing_count": seller_count, "total_listings": inv_count},
                    source="edhrec_proxy_fallback",
                )
            stats["proxy_fallback"] += 1
            stats["written"] += 1
        else:
            stats["api_miss"] += 1

    if not dry_run:
        session.commit()
    return stats


def supply_snapshot_summary(session: Session) -> dict:
    """Quick stats on stored supply snapshots."""
    rows = session.query(SupplySnapshot).all()
    if not rows:
        return {"rows": 0, "unique_cards": 0, "latest_date": None}
    dates = [r.snapshot_date for r in rows if r.snapshot_date]
    names = {r.card_name for r in rows}
    return {
        "rows": len(rows),
        "unique_cards": len(names),
        "latest_date": max(dates).isoformat() if dates else None,
        "earliest_date": min(dates).isoformat() if dates else None,
    }
