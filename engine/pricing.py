"""
Point-in-time price for the spec engine, backed by the local card_prices cache
(mirrored from Supabase). Price history only accumulates forward, so we treat the
cached price as point-in-time ONLY when the anchor is within PRICE_LIVE_GRACE_DAYS
of when it was captured — i.e. for live/recent evaluations. For backtests of older
decks there is no point-in-time price, so we return None and callers degrade
gracefully (never drop a candidate or apply a leaky present-day price).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from config import PRICE_GATE_USD, PRICE_LIVE_GRACE_DAYS
from db.schema import CardPrice


@dataclass
class PriceCache:
    """In-memory snapshot of the local card_prices table."""

    prices: dict = field(default_factory=dict)        # name -> price_usd
    copies_per_seller: dict = field(default_factory=dict)
    as_of: Optional[date] = None

    @classmethod
    def load(cls, session: Session) -> "PriceCache":
        cache = cls()
        for row in session.query(CardPrice).all():
            if row.price_usd is not None:
                cache.prices[row.card_name] = float(row.price_usd)
            if row.copies_per_seller is not None:
                cache.copies_per_seller[row.card_name] = float(row.copies_per_seller)
            if row.as_of_date and (cache.as_of is None or row.as_of_date > cache.as_of):
                cache.as_of = row.as_of_date
        return cache

    def is_empty(self) -> bool:
        return not self.prices

    def _anchor_is_live(self, anchor: Optional[date]) -> bool:
        """True when the cached price can stand in for the anchor's point-in-time price."""
        if self.as_of is None:
            return False
        if anchor is None:
            return True
        # cached price is "today-ish"; only trust it when the anchor is near now.
        return anchor >= self.as_of - timedelta(days=PRICE_LIVE_GRACE_DAYS)

    def point_in_time_price(self, card_name: str, anchor: Optional[date]) -> Optional[float]:
        if not self._anchor_is_live(anchor):
            return None
        return self.prices.get(card_name)


def price_factor(price: Optional[float], cap: float = PRICE_GATE_USD) -> float:
    """Cheapness 0..1: cheap = ~1, at/above the cap = 0. Unknown price = 0 (neutral)."""
    if price is None:
        return 0.0
    return max(0.0, 1.0 - min(price / cap, 1.0))


def over_price_gate(price: Optional[float], cap: float = PRICE_GATE_USD) -> bool:
    """True only when a KNOWN price exceeds the cap. Unknown never gates."""
    return price is not None and price > cap
