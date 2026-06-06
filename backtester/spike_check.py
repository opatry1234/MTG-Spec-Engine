"""
Price history helpers and spike detection inputs.

Loads daily prices from the local price_history table, with optional
fetch from TCGAPIs (trendprices) or MTGJSON AllPrices cache.
"""

from __future__ import annotations

import gzip
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from sqlalchemy.orm import Session

from config import (
    ALL_PRICES_GZ,
    ALL_PRICES_PATH,
    MTGJSON_CACHE_DIR,
    SCRYFALL_RATE_LIMIT_DELAY,
    SPIKE_BASELINE_END_DAYS,
    SPIKE_BASELINE_START_DAYS,
    SPIKE_MIN_ABSOLUTE_USD,
    SPIKE_MIN_RELATIVE_PCT,
    SPIKE_PEAK_END_DAYS,
    SPIKE_PEAK_START_DAYS,
    TCGAPIS_KEY,
)
from db.schema import CardPrinting, PriceHistory

MTGJSON_BASE = "https://mtgjson.com/api/v5"
TCGAPIS_TREND_HISTORY = "https://tcgapis.com/api/v2/trendprices/uuid/{uuid}/history"


def _parse_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve_mtgjson_uuid(session: Session, card_name: str) -> Optional[str]:
    """Best-effort MTGJSON uuid for a card (prefer oldest non-precon printing)."""
    printing = (
        session.query(CardPrinting)
        .filter(CardPrinting.card_name == card_name)
        .order_by(CardPrinting.released_at.asc())
        .first()
    )
    if not printing or not printing.set_code:
        return None

    set_path = MTGJSON_CACHE_DIR / f"{printing.set_code.upper()}.json"
    if not set_path.exists():
        return None

    try:
        data = json.loads(set_path.read_text(encoding="utf-8")).get("data", {})
        for card in data.get("cards", []):
            if (card.get("name") or "").lower() == card_name.lower():
                return card.get("uuid")
    except (json.JSONDecodeError, OSError):
        return None
    return None


def _load_all_prices_index() -> Optional[dict]:
    """Load MTGJSON AllPrices (90-day window) if cached locally."""
    path = ALL_PRICES_PATH if ALL_PRICES_PATH.exists() else None
    if path is None and ALL_PRICES_GZ.exists():
        try:
            with gzip.open(ALL_PRICES_GZ, "rt", encoding="utf-8") as f:
                return json.load(f).get("data", {})
        except (OSError, json.JSONDecodeError):
            return None
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("data", {})
    except (json.JSONDecodeError, OSError):
        return None


def _series_from_mtgjson(uuid: str, start: date, end: date) -> Dict[date, float]:
    index = _load_all_prices_index()
    if not index or uuid not in index:
        return {}

    paper = index.get(uuid, {}).get("paper", {})
    tcg = paper.get("tcgplayer", {}) or paper.get("tcgplayerRetail", {})
    retail = tcg.get("retail", {}) if isinstance(tcg, dict) else {}
    normal = retail.get("normal", {}) if isinstance(retail, dict) else {}

    series: Dict[date, float] = {}
    for day_str, price in normal.items():
        d = _parse_date(day_str)
        if d is None or price is None:
            continue
        if start <= d <= end:
            series[d] = float(price)
    return series


def _fetch_tcgapis_history(uuid: str, start: date, end: date) -> Dict[date, float]:
    if not TCGAPIS_KEY:
        return {}

    headers = {"x-api-key": TCGAPIS_KEY}
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "provider": "tcgplayer",
        "finish": "normal",
    }
    try:
        resp = requests.get(
            TCGAPIS_TREND_HISTORY.format(uuid=uuid),
            headers=headers,
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            return {}
        body = resp.json()
    except requests.RequestException:
        return {}

    series: Dict[date, float] = {}
    rows = body if isinstance(body, list) else body.get("history") or body.get("data") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _parse_date(row.get("date") or row.get("snapshotDate"))
        price = row.get("marketPrice") or row.get("price") or row.get("retail")
        if d and price is not None:
            series[d] = float(price)
    time.sleep(SCRYFALL_RATE_LIMIT_DELAY)
    return series


def _cache_series(session: Session, card_name: str, series: Dict[date, float], source: str) -> None:
    for d, price in series.items():
        session.merge(
            PriceHistory(
                card_name=card_name,
                date=d,
                price_usd=price,
                source=source,
            )
        )
    if series:
        session.commit()


def load_price_series(
    session: Session,
    card_name: str,
    start: date,
    end: date,
    *,
    fetch_if_missing: bool = True,
) -> Tuple[Dict[date, float], str]:
    """
    Return {date: usd_price} and a source label.
    """
    rows = (
        session.query(PriceHistory)
        .filter(
            PriceHistory.card_name == card_name,
            PriceHistory.date >= start,
            PriceHistory.date <= end,
        )
        .all()
    )
    if rows:
        return {r.date: float(r.price_usd) for r in rows if r.price_usd is not None}, "database"

    if not fetch_if_missing:
        return {}, "missing"

    uuid = resolve_mtgjson_uuid(session, card_name)
    if not uuid:
        return {}, "missing"

    series = _fetch_tcgapis_history(uuid, start, end)
    source = "tcgapis"
    if not series:
        series = _series_from_mtgjson(uuid, start, end)
        source = "mtgjson" if series else "missing"

    if series:
        _cache_series(session, card_name, series, source)
    return series, source


def detect_price_spike(
    series: Dict[date, float],
    release_date: date,
) -> dict:
    """
    Compare baseline window before release to peak window after announcement.
    """
    if not series or not release_date:
        return {
            "had_spike": False,
            "baseline_price": None,
            "peak_price": None,
            "spike_pct": None,
            "spike_usd": None,
            "data_points": len(series),
        }

    baseline_start = release_date + timedelta(days=SPIKE_BASELINE_START_DAYS)
    baseline_end = release_date + timedelta(days=SPIKE_BASELINE_END_DAYS)
    peak_start = release_date + timedelta(days=SPIKE_PEAK_START_DAYS)
    peak_end = release_date + timedelta(days=SPIKE_PEAK_END_DAYS)

    baseline_vals = [p for d, p in series.items() if baseline_start <= d <= baseline_end]
    peak_vals = [p for d, p in series.items() if peak_start <= d <= peak_end]

    if not baseline_vals or not peak_vals:
        return {
            "had_spike": False,
            "baseline_price": None,
            "peak_price": None,
            "spike_pct": None,
            "spike_usd": None,
            "data_points": len(series),
        }

    baseline = sorted(baseline_vals)[len(baseline_vals) // 2]
    peak = max(peak_vals)
    spike_usd = peak - baseline
    spike_pct = spike_usd / baseline if baseline > 0 else 0.0
    had_spike = spike_pct >= SPIKE_MIN_RELATIVE_PCT and spike_usd >= SPIKE_MIN_ABSOLUTE_USD

    return {
        "had_spike": had_spike,
        "baseline_price": round(baseline, 2),
        "peak_price": round(peak, 2),
        "spike_pct": round(spike_pct, 3),
        "spike_usd": round(spike_usd, 2),
        "data_points": len(series),
    }


def check_card_spike(
    session: Session,
    card_name: str,
    release_date: date,
    *,
    precon_release_date: Optional[date] = None,
    product_code: Optional[str] = None,
    fetch_if_missing: bool = True,
    deck_colors: Optional[list] = None,
    card_color_map: Optional[dict] = None,
    deck_synergy_ctx=None,
    cards_by_name: Optional[dict] = None,
) -> dict:
    if not release_date:
        return {"card_name": card_name, "had_spike": False, "price_source": "missing"}

    from backtester.spike_csv import find_spike_near_release, spike_window
    from config import SPIKE_CSV_PATH

    if SPIKE_CSV_PATH.exists():
        result = find_spike_near_release(
            card_name,
            release_date,
            precon_release_date=precon_release_date,
            product_code=product_code,
            deck_colors=deck_colors,
            card_color_map=card_color_map,
            deck_synergy_ctx=deck_synergy_ctx,
            cards_by_name=cards_by_name,
        )
        result["card_name"] = card_name
        return result

    window_start, window_end = spike_window(release_date, precon_release_date)
    series, source = load_price_series(
        session, card_name, window_start, window_end, fetch_if_missing=fetch_if_missing
    )
    if series:
        spike = detect_price_spike(series, release_date)
        spike["card_name"] = card_name
        spike["price_source"] = source
        return spike

    return {
        "card_name": card_name,
        "had_spike": False,
        "price_source": "missing",
    }
