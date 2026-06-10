"""
Sell-through velocity + ignition from local volume_history (weekly quantity_sold,
mirrored from Supabase card_prices_history).

Unlike price (forward-only; no point-in-time for old decks), volume_history is a
real ~12-month weekly series, so velocity IS point-in-time for any anchor inside
the captured window. We measure *acceleration* — recent weekly sales vs the card's
own trailing baseline — rather than absolute volume, which would just track how
liquid (abundant) a card is. The validated ignition logic
(snapshot_schema_validation.xlsx) is:

    velocity_factor    = clamp((recent/baseline - 1) / ACCEL_SPAN, 0, 1)
    effective_scarcity = ½·printing_scarcity + ½·velocity_factor
    price_factor       = 1 - min(price / PRICE_CAP, 1)
    ignition           = price_factor × effective_scarcity × new_home(synergy)

Cards with no volume coverage return ignition 0.0 — a neutral non-contribution,
never a penalty (most of the candidate pool has no scrape yet).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from config import (
    IGNITION_ACCEL_SPAN,
    IGNITION_BASELINE_WEEKS,
    IGNITION_MIN_RECENT_UNITS,
    IGNITION_PRICE_CAP,
    IGNITION_RECENT_WEEKS,
)
from db.schema import VolumeHistory


@dataclass
class VolumeCache:
    """In-memory per-card weekly sales series, built once per scoring session."""

    # card_name -> list of (snapshot_date, quantity_sold, market_price), date-ascending
    series: dict = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session) -> "VolumeCache":
        # Raw SQL, not ORM: this table can be ~500k+ rows and is loaded per scoring
        # pass — ORM object creation makes that painfully slow.
        from datetime import date as _date

        from sqlalchemy import text

        cache = cls()
        rows = session.execute(text(
            "SELECT card_name, snapshot_date, quantity_sold, market_price "
            "FROM volume_history WHERE quantity_sold IS NOT NULL "
            "ORDER BY card_name, snapshot_date"
        ))
        for name, d, qty, price in rows:
            if isinstance(d, str):  # sqlite hands DATE back as an ISO string
                d = _date.fromisoformat(d)
            cache.series.setdefault(name, []).append((d, float(qty), price))
        return cache

    def is_empty(self) -> bool:
        return not self.series

    def _buckets_through(self, card_name: str, anchor: Optional[date]) -> list:
        buckets = self.series.get(card_name)
        if not buckets:
            return []
        if anchor is None:
            return buckets
        return [b for b in buckets if b[0] <= anchor]

    def velocity_factor(self, card_name: str, anchor: Optional[date]) -> float:
        """Sell-through acceleration 0..1 (recent weeks vs trailing baseline).

        Returns 0.0 (neutral) when there isn't enough point-in-time history or the
        card simply isn't liquid enough for the signal to be meaningful.
        """
        buckets = self._buckets_through(card_name, anchor)
        if len(buckets) < IGNITION_RECENT_WEEKS + 2:
            return 0.0

        recent = buckets[-IGNITION_RECENT_WEEKS:]
        baseline = buckets[-(IGNITION_RECENT_WEEKS + IGNITION_BASELINE_WEEKS):-IGNITION_RECENT_WEEKS]
        if not baseline:
            return 0.0

        recent_mean = sum(b[1] for b in recent) / len(recent)
        baseline_mean = sum(b[1] for b in baseline) / len(baseline)
        if recent_mean < IGNITION_MIN_RECENT_UNITS:
            return 0.0  # too thin to be a real demand signal

        accel = recent_mean / max(baseline_mean, 1.0)
        vf = (accel - 1.0) / IGNITION_ACCEL_SPAN
        return round(min(max(vf, 0.0), 1.0), 4)

    def price_at(self, card_name: str, anchor: Optional[date]) -> Optional[float]:
        """Most recent market_price at/before the anchor from the volume series."""
        for d, _qty, price in reversed(self._buckets_through(card_name, anchor)):
            if price is not None:
                return float(price)
        return None

    def ignition_score(
        self,
        card_name: str,
        anchor: Optional[date],
        printing_scarcity: float,
        synergy: float,
    ) -> float:
        """price_factor × effective_scarcity × new_home. 0.0 when no volume coverage."""
        buckets = self._buckets_through(card_name, anchor)
        if not buckets:
            return 0.0

        vf = self.velocity_factor(card_name, anchor)
        price = self.price_at(card_name, anchor)
        # Without a point-in-time price we can't judge cheapness; and without any
        # velocity uplift there's no ignition to add. Either alone → neutral.
        if price is None or vf <= 0.0:
            return 0.0

        effective_scarcity = 0.5 * max(0.0, min(printing_scarcity, 1.0)) + 0.5 * vf
        price_factor = max(0.0, 1.0 - min(price / IGNITION_PRICE_CAP, 1.0))
        ignition = price_factor * effective_scarcity * max(0.0, synergy)
        return round(ignition, 4)
