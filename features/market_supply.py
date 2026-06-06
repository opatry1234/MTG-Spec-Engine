"""
Market supply features: visible inventory and seller count.

Uses a single latest row per card from ``supply_snapshots`` (one catalog refresh,
not per-card time series). When snapshots are missing, falls back to an EDHREC
rank proxy so scoring stays O(1) with no live API calls.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from db.schema import SupplySnapshot

# Listing counts at or above this are treated as "deep supply" (score → 0).
_LISTING_SATURATION = 500
_SELLER_SATURATION = 80


def normalize_visible_inventory(listing_count: int | None) -> float:
    """Low visible inventory → high spec potential (0–1)."""
    if listing_count is None:
        return 0.5
    if listing_count <= 0:
        return 1.0
    return max(0.0, 1.0 - min(listing_count / _LISTING_SATURATION, 1.0))


def normalize_seller_count(seller_count: int | None) -> float:
    """Few sellers → scarcer market (0–1)."""
    if seller_count is None:
        return 0.5
    if seller_count <= 0:
        return 1.0
    return max(0.0, 1.0 - min(seller_count / _SELLER_SATURATION, 1.0))


def price_at_date_supply_proxy(price_usd: float | None) -> tuple[float, float]:
    """
    Reveal-date proxy when historical listings do not exist.

    Uses MTGJSON/TCGAPIS price at anchor: expensive cards imply thinner markets.
    """
    if price_usd is None or price_usd <= 0:
        return 0.5, 0.5
    scarcity = min(price_usd / 25.0, 1.0)
    visible = round(scarcity, 4)
    sellers = round(min(scarcity * 0.85, 1.0), 4)
    return visible, sellers


def reveal_date_supply_proxy(
    session: Session,
    card_name: str,
    anchor_date: date,
) -> tuple[float, float]:
    """
    Point-in-time supply estimate for backtests (no present-day listing leakage).

    Priority: price at reveal from local price_history / AllPrices cache, else
    neutral 0.5 (EDHREC rank is NOT used — it would leak future popularity).
    """
    try:
        from backtester.spike_check import load_price_series

        series, _ = load_price_series(
            session,
            card_name,
            anchor_date,
            anchor_date,
            fetch_if_missing=True,
        )
        price = series.get(anchor_date)
        if price is not None:
            return price_at_date_supply_proxy(price)
    except Exception:
        pass
    return 0.5, 0.5


def edhrec_supply_proxy(edhrec_rank: int | None) -> tuple[float, float]:
    """
    Proxy visible inventory / seller activity from EDHREC rank when market data
    is unavailable. Obscure cards score as scarcer (higher spec upside).
    """
    rank = edhrec_rank or 99_999
    activity = 1.0 - min(rank / 20_000.0, 1.0)
    visible = max(0.0, 1.0 - activity * 0.85)
    sellers = max(0.0, 1.0 - activity * 0.75)
    return round(visible, 4), round(sellers, 4)


class MarketSupplyCache:
    """Latest supply snapshot per card — built once per scoring session."""

    def __init__(self, session: Session | None = None):
        self._by_name: dict[str, dict] = {}
        if session is not None:
            self._load(session)

    def _load(self, session: Session) -> None:
        rows = (
            session.query(SupplySnapshot)
            .order_by(SupplySnapshot.snapshot_date.desc())
            .all()
        )
        seen: set[str] = set()
        for row in rows:
            if row.card_name in seen:
                continue
            seen.add(row.card_name)
            listings = row.total_listings
            if listings is None:
                listings = row.tcg_listing_count
            self._by_name[row.card_name] = {
                "visible_inventory": listings,
                "seller_count": row.tcg_listing_count,
            }

    def scores_for_card(
        self,
        card_name: str,
        *,
        edhrec_rank: int | None = None,
        point_in_time: bool = False,
        session: Session | None = None,
        anchor_date: date | None = None,
    ) -> tuple[float, float]:
        """
        Return (visible_inventory_score, seller_count_score).

        Live path: latest supply_snapshots, else EDHREC proxy.
        Backtest path: reveal-date price proxy (historical listings unavailable).
        """
        if point_in_time and session is not None and anchor_date is not None:
            return reveal_date_supply_proxy(session, card_name, anchor_date)
        if point_in_time:
            return 0.5, 0.5

        row = self._by_name.get(card_name)
        if row:
            visible = normalize_visible_inventory(row.get("visible_inventory"))
            sellers = normalize_seller_count(row.get("seller_count"))
            return round(visible, 4), round(sellers, 4)

        return edhrec_supply_proxy(edhrec_rank)


def load_market_supply_cache(session: Session) -> MarketSupplyCache:
    return MarketSupplyCache(session)
