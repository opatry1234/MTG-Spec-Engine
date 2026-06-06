"""
Historical omission spike prior for ranking and ML features.

Cards that spiked as precon-attributed omission upgrades on similar color
identities in the past (e.g. Varina on WUB zombie precons) get a ranking boost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backtester.spike_precon_catalog import is_junk_card_name, normalize_deck_key
from backtester.spike_reasoning import find_reasoning_golden_benchmarks, load_spike_reasoning
from config import (
    GOLDEN_SPIKE_MIN_RELATIVE_PCT,
    HISTORICAL_SPIKE_EXCLUDE_THRESHOLD,
    ML_SPIKE_NO_PRIOR_DAMPEN,
    MIN_SYNERGY_FOR_SPEC,
    SPIKE_CSV_PATH,
)
from db.schema import Card, CommanderDeck, DeckCard
from engine.deck_synergy import DeckSynergyContext
from engine.spec_eligibility import build_earliest_printing_map, spec_anchor_date


def _golden_on_or_after_reveal(row: dict, reveal_date: date) -> bool:
    rd = row.get("report_date")
    if not rd:
        return True
    if isinstance(rd, str):
        rd = date.fromisoformat(rd[:10])
    return rd >= reveal_date


def _color_key(colors) -> str:
    if not colors:
        return "C"
    order = ["W", "U", "B", "R", "G"]
    return "".join(c for c in order if c in colors)


def _color_overlap(colors_a, colors_b) -> float:
    sa, sb = set(colors_a or []), set(colors_b or [])
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@dataclass
class SpikeEvent:
    card_name: str
    color_key: str
    colors: list
    spike_pct: float
    spike_usd: float
    deck_id: int
    deck_name: str
    release_date: date
    attribution: str
    spike_type: str = ""
    precon_set_code: str = ""


@dataclass
class HistoricalSpikePrior:
    events_by_card: Dict[str, List[SpikeEvent]] = field(default_factory=dict)
    deck_spike_cards: Dict[int, set[str]] = field(default_factory=dict)
    deck_loose_spikes: Dict[int, set[str]] = field(default_factory=dict)
    deck_golden_spikes: Dict[int, set[str]] = field(default_factory=dict)
    deck_golden_benchmarks: Dict[int, List[dict]] = field(default_factory=dict)

    @classmethod
    def build(cls, session: Session) -> "HistoricalSpikePrior":
        if not SPIKE_CSV_PATH.exists():
            return cls()

        prior = cls()
        earliest_map = build_earliest_printing_map(session)
        color_map = {
            (c.name or "").lower(): list(c.color_identity or [])
            for c in session.query(Card).all()
            if c.name
        }
        cards_by_name = {
            (c.name or "").lower(): c for c in session.query(Card).all() if c.name
        }
        deck_cards: dict[int, set[str]] = defaultdict(set)
        for deck_id, card_name in session.query(DeckCard.deck_id, DeckCard.card_name).all():
            deck_cards[deck_id].add(card_name)

        decks = (
            session.query(CommanderDeck)
            .filter(
                CommanderDeck.decklist_revealed == True,
                CommanderDeck.release_date.isnot(None),
            )
            .all()
        )

        for deck in decks:
            anchor = spec_anchor_date(deck)
            if not anchor:
                continue
            actual = deck_cards.get(deck.id, set())
            if not actual:
                continue

            synergy_ctx = DeckSynergyContext.from_deck(deck, session)
            common_kwargs = dict(
                actual_deck=actual,
                release_date=anchor,
                precon_release_date=getattr(deck, "precon_release_date", None),
                product_code=deck.product,
                earliest_printing_map=earliest_map,
                limit=50,
                deck_colors=list(deck.colors or []),
                card_color_map=color_map,
                deck_synergy_ctx=synergy_ctx,
                cards_by_name=cards_by_name,
            )

            # --- Golden benchmarks: Spike Reasoning sheet ONLY ---
            # The human-curated Spike Reasoning sheet is the single source of truth.
            # The old automated fallback (find_omission_spike_benchmarks) attributed
            # spikes by temporal proximity — any card that spiked near the release date
            # regardless of whether it was actually caused by this deck. This produced
            # false benchmarks for decks whose Spike Cause text doesn't name them.
            # All valid golden specs must be explicitly entered in the Spike Reasoning
            # sheet with a Spike Cause that includes the deck name.
            golden = [
                row
                for row in find_reasoning_golden_benchmarks(
                    deck.deck_name or "",
                    deck.product or "",
                    commander_name=deck.commander_name or "",
                )
                if _golden_on_or_after_reveal(row, anchor)
            ]

            golden_names = {
                row["card_name"]
                for row in golden
                if row.get("precon_attributed") and not is_junk_card_name(row["card_name"])
            }

            if golden_names:
                prior.deck_golden_spikes[deck.id] = golden_names
                prior.deck_spike_cards[deck.id] = golden_names
                prior.deck_loose_spikes[deck.id] = golden_names
            if golden:
                prior.deck_golden_benchmarks[deck.id] = golden

            ck = _color_key(deck.colors)
            for row in golden:
                name = row["card_name"]
                if is_junk_card_name(name):
                    continue
                key = name.lower()
                event = SpikeEvent(
                    card_name=name,
                    color_key=ck,
                    colors=list(deck.colors or []),
                    spike_pct=row.get("spike_pct") or 0.0,
                    spike_usd=row.get("spike_usd") or 0.0,
                    deck_id=deck.id,
                    deck_name=deck.deck_name or "",
                    release_date=anchor,
                    attribution=row.get("attribution", "spike_reasoning_sheet"),
                    spike_type=row.get("spike_type") or "",
                    precon_set_code=(deck.product or "").upper(),
                )
                prior.events_by_card.setdefault(key, []).append(event)

        # Pre-con bible rows → train labels on all matching historical decks (by set code).
        product_by_code = {
            (d.product or "").upper(): d
            for d in decks
            if d.product and d.release_date
        }
        for row in load_spike_reasoning():
            if is_junk_card_name(row.card_name):
                continue
            code = (row.precon_set_code or "").strip().upper()
            if len(code) < 3:
                continue
            deck = product_by_code.get(code)
            if not deck:
                continue
            if row.card_name in deck_cards.get(deck.id, set()):
                continue
            prior.deck_golden_spikes.setdefault(deck.id, set()).add(row.card_name)
            prior.deck_loose_spikes.setdefault(deck.id, set()).add(row.card_name)
            ck = _color_key(deck.colors)
            key = row.card_name.lower()
            prior.events_by_card.setdefault(key, []).append(
                SpikeEvent(
                    card_name=row.card_name,
                    color_key=ck,
                    colors=list(deck.colors or []),
                    spike_pct=row.gain_pct or 0.0,
                    spike_usd=(
                        (row.final_price or 0) - (row.initial_price or 0)
                        if row.final_price and row.initial_price
                        else 0.0
                    ),
                    deck_id=deck.id,
                    deck_name=deck.deck_name or "",
                    release_date=anchor,
                    attribution="spike_reasoning_precon",
                    spike_type=row.spike_type or "",
                    precon_set_code=code,
                )
            )

        return prior

    def spike_type_prior_score(
        self,
        card_name: str,
        colors: list,
        *,
        as_of_date: Optional[date] = None,
        exclude_deck_id: Optional[int] = None,
        preferred_types: Optional[tuple[str, ...]] = None,
    ) -> float:
        """How often this card spiked as a precon-synergy type on similar-color decks."""
        preferred = preferred_types or (
            "new commander precon synergy",
            "new commander precon",
            "commander precon upgrade",
        )
        events = self.events_by_card.get((card_name or "").lower(), [])
        if not events:
            return 0.0
        best = 0.0
        target_ck = _color_key(colors)
        for ev in events:
            if as_of_date and ev.release_date >= as_of_date:
                continue
            if exclude_deck_id is not None and ev.deck_id == exclude_deck_id:
                continue
            st = (ev.spike_type or "").lower()
            if not any(p in st for p in preferred):
                continue
            overlap = 1.0 if ev.color_key == target_ck else _color_overlap(colors, ev.colors)
            if overlap <= 0:
                continue
            magnitude = min(ev.spike_pct / 5.0, 1.0) if ev.spike_pct else 0.3
            best = max(best, min(overlap * magnitude, 1.0))
        return round(best, 4)

    def score(
        self,
        card_name: str,
        colors: list,
        as_of_date: Optional[date] = None,
        exclude_deck_id: Optional[int] = None,
    ) -> float:
        """
        Return 0–1 score: how strongly this card historically spiked as an omission
        upgrade on decks with similar color identity, using only past events.
        """
        events = self.events_by_card.get((card_name or "").lower(), [])
        if not events:
            return 0.0

        best = 0.0
        target_ck = _color_key(colors)
        for ev in events:
            if as_of_date is not None and ev.release_date >= as_of_date:
                continue
            if exclude_deck_id is not None and ev.deck_id == exclude_deck_id:
                continue
            overlap = 1.0 if ev.color_key == target_ck else _color_overlap(colors, ev.colors)
            if overlap <= 0:
                continue
            magnitude = min(ev.spike_pct / 10.0, 1.0)
            announcement_bonus = 1.15 if ev.attribution == "announcement" else 1.0
            candidate = overlap * magnitude * announcement_bonus
            best = max(best, min(candidate, 1.0))
        return round(best, 4)

    def was_deck_spike_target(self, deck_id: int, card_name: str) -> bool:
        return card_name in self.deck_loose_spikes.get(deck_id, set())

    def was_deck_golden_target(self, deck_id: int, card_name: str) -> bool:
        return card_name in self.deck_golden_spikes.get(deck_id, set())


_cached_prior: Optional[HistoricalSpikePrior] = None


def get_historical_spike_prior(session: Session) -> HistoricalSpikePrior:
    global _cached_prior
    if _cached_prior is None:
        _cached_prior = HistoricalSpikePrior.build(session)
    return _cached_prior


def clear_historical_spike_prior_cache() -> None:
    global _cached_prior
    _cached_prior = None


def merge_historical_spike_scores(
    prior_spike: float,
    ml_spike: float,
    synergy_fit: float,
) -> float:
    """
    Combine CSV-backed omission spike history with ML spec-spike predictions.

    Proven omission spikes on similar decks dominate; ML only fills gaps and is
    dampened when there is no historical prior (reduces bulk false positives).
    """
    if ml_spike <= 0:
        return prior_spike
    if ml_spike <= prior_spike:
        return prior_spike
    if prior_spike >= HISTORICAL_SPIKE_EXCLUDE_THRESHOLD:
        return round(min(1.0, prior_spike + (ml_spike - prior_spike) * 0.25), 4)
    dampened = ml_spike * max(synergy_fit, MIN_SYNERGY_FOR_SPEC) * ML_SPIKE_NO_PRIOR_DAMPEN
    return round(max(prior_spike, dampened), 4)
