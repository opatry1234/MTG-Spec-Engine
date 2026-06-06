"""
EDH popularity proxies from Scryfall edhrec_rank and historical precon rates.
"""

import math
from typing import Optional

from db.schema import Card, CommanderDeck, DeckCard


def edhrec_demand_score(edhrec_rank: Optional[int]) -> float:
    """Convert EDHREC rank to 0-1 demand score (lower rank = higher demand)."""
    if not edhrec_rank or edhrec_rank <= 0:
        return 0.3
    return min(1.0 / math.log10(edhrec_rank + 10), 1.0)


def historical_inclusion_rate(
    session, card_name: str, colors: Optional[list], exclude_deck_id: Optional[int] = None
) -> float:
    """Fraction of same-color-identity precons containing this card."""
    query = session.query(CommanderDeck).filter(CommanderDeck.decklist_revealed == True)
    if exclude_deck_id:
        query = query.filter(CommanderDeck.id != exclude_deck_id)

    if colors:
        decks = [d for d in query.all() if set(d.colors or []) == set(colors)]
    else:
        decks = query.all()

    if not decks:
        return 0.0

    hits = 0
    for deck in decks:
        exists = (
            session.query(DeckCard)
            .filter(DeckCard.deck_id == deck.id, DeckCard.card_name == card_name)
            .first()
        )
        if exists:
            hits += 1

    return hits / len(decks)


def compute_popularity_score(
    session,
    card: Card,
    colors: Optional[list],
    exclude_deck_id: Optional[int] = None,
) -> float:
    """Combined popularity: EDHREC + historical precon inclusion."""
    edh = edhrec_demand_score(card.edhrec_rank)
    hist = historical_inclusion_rate(session, card.name, colors, exclude_deck_id)
    return edh * 0.5 + hist * 0.5
