"""
Phase-3 scoring context — decklist is public, score omitted cards only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Set

from db.schema import CommanderDeck
from engine.product_parser import parse_announced_new_cards
from engine.spec_eligibility import spec_anchor_date


@dataclass
class ScoringContext:
    deck: CommanderDeck
    colors: list
    theme: str
    product_description: str
    commander_name: str
    commander_text: str
    known_inclusions: Set[str]
    new_cards: int
    total_cards: int
    exclude_deck_id: Optional[int]
    product_type: str
    product_code: str
    release_year: int
    anchor_date: Optional[date]
    primary_archetype: str = ""


def _product_description(deck: CommanderDeck) -> str:
    text = (deck.product_description or "").strip()
    if text:
        return text
    return (deck.theme or deck.deck_name or "").strip()


def _announced_new_cards(deck: CommanderDeck, product_description: str) -> int:
    if deck.announced_new_cards is not None:
        return deck.announced_new_cards
    parsed = parse_announced_new_cards(product_description)
    if parsed is not None:
        return parsed
    return deck.new_cards or 15


def build_scoring_context(
    deck: CommanderDeck,
    session,
    *,
    is_backtest: bool = True,
) -> ScoringContext:
    from engine.heuristic_scorer import get_actual_deck_cards

    product_description = _product_description(deck)
    theme = (deck.theme or deck.deck_name or "").strip()
    anchor = spec_anchor_date(deck)
    known = get_actual_deck_cards(session, deck.id)

    ctx = ScoringContext(
        deck=deck,
        colors=deck.colors or [],
        theme=theme,
        product_description=product_description,
        commander_name=(deck.commander_name or "").strip(),
        commander_text=(deck.commander_text or "").strip(),
        known_inclusions=known,
        new_cards=_announced_new_cards(deck, product_description),
        total_cards=deck.total_cards or 100,
        exclude_deck_id=deck.id if is_backtest else None,
        product_type=deck.product_type or "standard_set_commander",
        product_code=(deck.product or "").upper(),
        release_year=anchor.year if anchor else 2026,
        anchor_date=anchor,
        primary_archetype=(deck.primary_archetype or "").strip(),
    )
    if not ctx.colors:
        raise ValueError("Color identity is required to run predictions.")
    return ctx


def to_prediction_input(ctx: ScoringContext):
    from engine.heuristic_scorer import PredictionInput

    return PredictionInput(
        colors=ctx.colors,
        commander_text=ctx.commander_text,
        commander_name=ctx.commander_name,
        theme=ctx.theme,
        product_description=ctx.product_description,
        new_cards=ctx.new_cards,
        total_cards=ctx.total_cards,
        exclude_deck_id=ctx.exclude_deck_id,
        known_inclusions=ctx.known_inclusions,
        product_type=ctx.product_type,
        product_code=ctx.product_code,
        release_year=ctx.release_year,
        anchor_date=ctx.anchor_date,
        primary_archetype=ctx.primary_archetype,
    )


def visible_fields_summary(ctx: ScoringContext) -> dict:
    return {
        "Color identity": ", ".join(ctx.colors) if ctx.colors else "—",
        "Theme": ctx.theme or "—",
        "Product description": (
            (ctx.product_description[:120] + "…")
            if len(ctx.product_description) > 120
            else ctx.product_description or "—"
        ),
        "Commander": ctx.commander_name or "—",
        "Commander text": "available" if ctx.commander_text else "—",
        "Decklist": f"{len(ctx.known_inclusions)} cards known",
        "Spec anchor": ctx.anchor_date.isoformat() if ctx.anchor_date else "—",
    }
