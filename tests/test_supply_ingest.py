"""Tests for bulk supply ingest helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from features.market_supply import price_at_date_supply_proxy
from ingest.supply import parse_listing_payload


def test_parse_listing_payload_counts():
    payload = {
        "totalListings": 4,
        "listings": [
            {"sellerId": "a", "quantity": 2, "price": 1.5},
            {"sellerId": "b", "quantity": 1, "price": 2.0},
            {"sellerName": "c", "quantity": 3, "price": 1.0},
        ],
    }
    stats = parse_listing_payload(payload)
    assert stats["tcg_listing_count"] == 3
    assert stats["total_listings"] == 6
    assert stats["tcg_price_low"] == 1.0


def test_price_at_date_supply_proxy_expensive_is_scarcer():
    cheap_vis, _ = price_at_date_supply_proxy(0.75)
    pricey_vis, _ = price_at_date_supply_proxy(20.0)
    assert pricey_vis > cheap_vis
