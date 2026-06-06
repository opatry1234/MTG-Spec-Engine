"""
Supply and scarcity feature engineering from card printings history.
"""

from datetime import date
from typing import Optional

from config import SPEC_SUPPLY_EXPONENT
from db.schema import Card, CardPrinting


def printing_stats_at(
    printings: list,
    as_of_date: Optional[date] = None,
) -> dict:
    """Aggregate printing statistics visible on or before as_of_date."""
    ref = as_of_date or date.today()
    visible = [p for p in printings if p.released_at and p.released_at <= ref]
    if not visible and printings:
        visible = [p for p in printings if p.released_at]

    if not visible:
        return {
            "num_printings": 0,
            "num_precon_printings": 0,
            "last_reprint_days_ago": 9999,
            "last_reprint_date": None,
            "first_printing_date": None,
        }

    precon = sum(1 for p in visible if p.is_commander_precon)
    sorted_p = sorted(visible, key=lambda x: x.released_at, reverse=True)
    sorted_asc = sorted(visible, key=lambda x: x.released_at)
    last_date = sorted_p[0].released_at
    first_date = sorted_asc[0].released_at
    days_ago = (ref - last_date).days if last_date else 9999

    return {
        "num_printings": len(visible),
        "num_precon_printings": precon,
        "last_reprint_days_ago": days_ago,
        "last_reprint_date": last_date,
        "first_printing_date": first_date,
    }


def get_printing_stats(session, card_name: str, as_of_date: Optional[date] = None) -> dict:
    """Aggregate printing statistics for a card."""
    printings = (
        session.query(CardPrinting)
        .filter(CardPrinting.card_name == card_name)
        .order_by(CardPrinting.released_at.desc())
        .all()
    )
    return printing_stats_at(printings, as_of_date)


def compute_scarcity_score(
    num_printings: int,
    last_reprint_days_ago: int,
    is_reserved: bool,
    current_price: Optional[float] = None,
) -> float:
    """
    Scarcity score 0-1: fewer printings, older, reserved, higher price = scarcer.
    """
    if is_reserved:
        return 1.0

    printing_factor = 1.0 / max(num_printings, 1)
    age_factor = min(last_reprint_days_ago / 3650, 1.0)  # cap at ~10 years
    price_factor = 0.0
    if current_price:
        price_factor = min(current_price / 50.0, 1.0)

    return min(printing_factor * 0.4 + age_factor * 0.4 + price_factor * 0.2, 1.0)


def compute_spec_supply_score(
    num_printings: int,
    last_reprint_days_ago: int,
    is_reserved: bool,
    *,
    first_printing_date: Optional[date] = None,
    as_of_date: Optional[date] = None,
    current_price: Optional[float] = None,
) -> float:
    """
    Supply score for spec targets — emphasizes cards that can actually move.

    Old, low-print-run, never-reprinted cards (e.g. 1996 Unfulfilled Desires)
    score much higher than recent bulk rares with many printings.
    """
    base = compute_scarcity_score(
        num_printings, last_reprint_days_ago, is_reserved, current_price=current_price
    )

    ref = as_of_date or date.today()
    years_since_first = 0.0
    if first_printing_date:
        years_since_first = max(0.0, (ref - first_printing_date).days / 365.25)

    # Single printing / vintage: the supply shock profile that drives omission spikes
    if num_printings <= 1:
        base = max(base, 0.82)
    elif num_printings == 2 and last_reprint_days_ago >= 3650:
        base = max(base, 0.68)

    if years_since_first >= 20 and num_printings <= 3:
        base = max(base, 0.88)
    elif years_since_first >= 10 and num_printings <= 2:
        base = max(base, 0.75)

    if last_reprint_days_ago >= 7300 and num_printings <= 3:
        base = max(base, 0.9)

    return round(min(base, 1.0), 4)


def compute_reprint_likelihood(session, card_name: str, as_of_date: Optional[date] = None) -> float:
    """
    Heuristic P(reprinted) 0-1 based on precon printing history and recency.
    """
    stats = get_printing_stats(session, card_name, as_of_date=as_of_date)
    num = stats["num_printings"]
    precon = stats["num_precon_printings"]
    days = stats["last_reprint_days_ago"]

    if num == 0:
        return 0.1

    precon_ratio = precon / num
    recency = max(0.0, 1.0 - days / 1825)  # recent reprint within ~5 years

    return min(precon_ratio * 0.6 + recency * 0.4, 1.0)


def get_card_price_proxy(card: Card) -> Optional[float]:
    """Placeholder for price — uses None until price ingest exists."""
    return None
