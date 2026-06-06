"""
Lightweight deck synergy for spike attribution and golden benchmarks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from db.schema import Card  # noqa: F401 — used in from_deck
from engine.opportunity_score import is_alternate_commander
from features.mechanical import (
    best_keyword_category_score,
    card_has_counter_interaction,
    card_disrupts_counter_strategy,
    creature_type_overlap,
    deck_has_counter_theme,
    extract_deck_creature_types,
    get_deck_primary_keyword_category,
    keyword_category_scores,
)
from features.nlp import tfidf_similarity

_COUNTER_ADJACENCY_BONUS = 0.35  # was 0.15 — TF-IDF misses mechanical synergy (e.g. proliferate
                                 # for a -1/-1 deck scores tfidf=0), so a card that mechanically
                                 # interacts with the deck's counters must get a strong floor.
_HATE_CARD_SYNERGY_SCORE = 0.58
_HATE_CARD_HISTORICAL_SPIKE_FLOOR = 0.45


@dataclass
class DeckSynergyContext:
    colors: list
    commander_name: str = ""
    commander_text: str = ""
    theme: str = ""
    product_description: str = ""
    deck_card_names: list = field(default_factory=list)
    theme_creature_types: list = field(default_factory=list)

    @classmethod
    def from_deck(cls, deck, session=None) -> "DeckSynergyContext":
        commander_type_line = ""
        deck_names: list[str] = []
        if session:
            if deck.commander_name:
                card = session.query(Card).filter_by(name=deck.commander_name).first()
                if card:
                    commander_type_line = card.type_line or ""
            from engine.heuristic_scorer import get_actual_deck_cards

            deck_names = sorted(get_actual_deck_cards(session, deck.id))
        theme_types = extract_deck_creature_types(
            commander_type_line=commander_type_line,
            theme=deck.theme or deck.deck_name or "",
            product_description=deck.product_description or "",
        )
        return cls(
            colors=list(deck.colors or []),
            commander_name=deck.commander_name or "",
            commander_text=deck.commander_text or "",
            theme=deck.theme or deck.deck_name or "",
            product_description=deck.product_description or "",
            deck_card_names=deck_names,
            theme_creature_types=theme_types,
        )


def _synergy_query(ctx: DeckSynergyContext) -> str:
    parts = [ctx.theme]
    if ctx.commander_text:
        parts.insert(0, ctx.commander_text)
    elif ctx.commander_name:
        parts.insert(0, ctx.commander_name)
    return " ".join(filter(None, parts))


def _theme_keyword_score(oracle: str, primary_cat: str | None) -> float:
    if primary_cat:
        return keyword_category_scores(oracle).get(primary_cat, 0.0)
    # Copy/clone only scores when the deck's primary category is copy — never as a default.
    scores = keyword_category_scores(oracle)
    scores.pop("copy", None)
    return max(scores.values()) if scores else 0.0


def compute_synergy_fit(card: Card, ctx: DeckSynergyContext) -> float:
    query = _synergy_query(ctx)
    if not query.strip():
        return 0.0

    counter_deck = deck_has_counter_theme(query)
    primary_cat = "counter" if counter_deck else get_deck_primary_keyword_category(query)
    oracle = card.oracle_text or ""

    tfidf = tfidf_similarity(query, [oracle or card.name or ""])
    tfidf_val = tfidf[0] if tfidf else 0.0
    keyword = _theme_keyword_score(oracle, primary_cat)
    kw_weight = 0.30 if primary_cat == "counter" else 0.20  # was 0.10 — mechanical match matters
    tribal = creature_type_overlap(card.type_line or "", ctx.theme_creature_types or [])
    base = tfidf_val * (1 - kw_weight) + keyword * kw_weight
    if tribal > 0:
        base = min(1.0, base + tribal * 0.10)

    bonus = 0.0
    if counter_deck and card_has_counter_interaction(oracle):
        bonus = _COUNTER_ADJACENCY_BONUS

    return round(min(base + bonus, 1.0), 4)


def compute_synergy_fits(cards: list[Card], ctx: DeckSynergyContext) -> list[float]:
    query = _synergy_query(ctx)
    if not query.strip():
        return [0.0] * len(cards)

    counter_deck = deck_has_counter_theme(query)
    primary_cat = "counter" if counter_deck else get_deck_primary_keyword_category(query)
    texts = [c.oracle_text or c.name or "" for c in cards]
    tfidf_vals = tfidf_similarity(query, texts)
    fits = []
    kw_weight = 0.30 if primary_cat == "counter" else 0.20  # was 0.10 — mechanical match matters
    for card, tfidf_val in zip(cards, tfidf_vals):
        oracle = card.oracle_text or ""
        keyword = _theme_keyword_score(oracle, primary_cat)
        base = tfidf_val * (1 - kw_weight) + keyword * kw_weight
        bonus = 0.0
        if counter_deck and card_has_counter_interaction(oracle):
            bonus = _COUNTER_ADJACENCY_BONUS
        fits.append(round(min(base + bonus, 1.0), 4))
    return fits


def is_alt_commander_for_deck(card: Card, ctx: DeckSynergyContext, synergy_fit: float) -> bool:
    return is_alternate_commander(card, ctx.commander_name, synergy_fit)


def compute_hate_card_score(card: Card, ctx: DeckSynergyContext) -> float:
    query = _synergy_query(ctx)
    if not deck_has_counter_theme(query):
        return 0.0
    if not card_disrupts_counter_strategy(card.oracle_text or ""):
        return 0.0
    return _HATE_CARD_SYNERGY_SCORE
