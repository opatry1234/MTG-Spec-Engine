"""
Phase-3 omitted-card scorer for Commander precon speculation.

Given a public decklist, scores color-legal cards NOT in the list using synergy,
supply, demand, and ML signals anchored to decklist reveal date.
"""

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional, Set

import pandas as pd
from sqlalchemy.orm import Session

from analytics.auto_includes import get_staple_excludes
from db.schema import Card, CardPrinting, CommanderDeck, DeckCard
from engine.combo_checker import enrich_predictions_with_combos
from engine.deck_synergy import (
    DeckSynergyContext,
    compute_synergy_fits,
    compute_hate_card_score,
    _HATE_CARD_HISTORICAL_SPIKE_FLOOR,
)
from engine.opportunity_score import compute_spec_opportunity_score, is_alternate_commander
from engine.weighted_spec_score import compute_weighted_spec_score
from engine.volume import VolumeCache
from features.market_supply import MarketSupplyCache
from features.mechanical import deck_has_counter_theme, card_disrupts_counter_strategy, is_mana_fixer_for_colors
from features.popularity import edhrec_demand_score
from features.supply import compute_scarcity_score, compute_spec_supply_score
from filters.candidates import passes_spec_candidate_filters, passes_hate_card_filters
from engine.historical_spike_prior import get_historical_spike_prior, merge_historical_spike_scores
from engine.spec_eligibility import build_earliest_printing_map, was_spec_eligible_at_reveal
from filters.staples import get_staples

from config import MIN_SYNERGY_FOR_SPEC, MIN_SYNERGY_HARD_FLOOR

ML_REPRINT_HEURISTIC_BLEND = 0.0  # reprint likelihood no longer affects ranking
MIN_SYNERGY_FOR_ML_INCLUSION = 0.05


@dataclass
class ScoringCache:
    """Pre-loaded stats for fast candidate scoring."""

    printing_stats: dict = field(default_factory=dict)
    decks_by_color: dict = field(default_factory=dict)
    deck_cards: dict = field(default_factory=dict)

    @classmethod
    def build(cls, session: Session) -> "ScoringCache":
        from collections import defaultdict
        from datetime import date

        cache = cls()
        by_name = defaultdict(list)
        for p in session.query(CardPrinting).all():
            by_name[p.card_name].append(p)

        for name, printings in by_name.items():
            precon = sum(1 for p in printings if p.is_commander_precon)
            sorted_p = sorted(
                [p for p in printings if p.released_at],
                key=lambda x: x.released_at,
                reverse=True,
            )
            last_date = sorted_p[0].released_at if sorted_p else None
            days = (date.today() - last_date).days if last_date else 9999
            num = len(printings)
            precon_ratio = precon / num if num else 0
            recency = max(0.0, 1.0 - days / 1825)
            cache.printing_stats[name] = {
                "num_printings": num,
                "last_reprint_days_ago": days,
                "p_reprint": min(precon_ratio * 0.6 + recency * 0.4, 1.0) if num else 0.1,
            }

        order = ["W", "U", "B", "R", "G"]
        for deck in session.query(CommanderDeck).filter(
            CommanderDeck.decklist_revealed == True
        ).all():
            ck = "".join(c for c in order if c in (deck.colors or [])) or "C"
            cache.decks_by_color.setdefault(ck, []).append(deck.id)

        for deck_id, card_name in session.query(DeckCard.deck_id, DeckCard.card_name).all():
            cache.deck_cards.setdefault(deck_id, set()).add(card_name)

        return cache

    def historical_rate(self, card_name: str, colors: list, exclude_deck_id=None) -> float:
        order = ["W", "U", "B", "R", "G"]
        ck = "".join(c for c in order if c in (colors or [])) or "C"
        deck_ids = self.decks_by_color.get(ck, [])
        if exclude_deck_id:
            deck_ids = [d for d in deck_ids if d != exclude_deck_id]
        if not deck_ids:
            return 0.0
        hits = sum(1 for d in deck_ids if card_name in self.deck_cards.get(d, set()))
        return hits / len(deck_ids)


@dataclass
class PredictionInput:
    colors: list
    commander_text: str = ""
    commander_name: str = ""
    theme: str = ""
    product_description: str = ""
    new_cards: int = 15
    total_cards: int = 100
    exclude_deck_id: Optional[int] = None
    known_inclusions: Set[str] = field(default_factory=set)
    product_type: str = "standard_set_commander"
    product_code: str = ""
    release_year: int = 2026
    anchor_date: Optional[date] = None
    primary_archetype: str = ""

    @property
    def release_date(self) -> Optional[date]:
        """Backward-compatible alias for anchor_date."""
        return self.anchor_date


def _is_basic_land(card: Card) -> bool:
    tl = (card.type_line or "").lower()
    return "basic land" in tl or card.name in {
        "Plains", "Island", "Swamp", "Mountain", "Forest",
        "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
        "Snow-Covered Mountain", "Snow-Covered Forest",
    }


def get_candidates(session: Session, pred_input: PredictionInput) -> list:
    """Color-legal omitted cards minus staples (universal + color + archetype) and basics."""
    exclude = get_staple_excludes(
        session, pred_input.colors, pred_input.primary_archetype
    )

    deck_colors = pred_input.colors or []
    known_in_deck = pred_input.known_inclusions or set()
    cards = session.query(Card).all()

    earliest_map = None
    if pred_input.anchor_date:
        earliest_map = build_earliest_printing_map(session)

    theme_query = " ".join(filter(None, [pred_input.commander_text, pred_input.theme]))
    include_hate_cards = deck_has_counter_theme(theme_query)

    # $10 spec gate: drop cards whose KNOWN live price exceeds the cap. Price is
    # only point-in-time for live/recent anchors, so backtests of old decks see
    # None and nothing is dropped (no leakage, never lose a candidate).
    from engine.pricing import PriceCache, over_price_gate
    price_cache = PriceCache.load(session)
    anchor = pred_input.anchor_date

    seen_names: set = set()
    candidates = []
    for card in cards:
        if card.name in exclude or card.name in known_in_deck or _is_basic_land(card):
            continue

        eligible = passes_spec_candidate_filters(card, deck_colors)
        if not eligible and include_hate_cards:
            if passes_hate_card_filters(card) and card_disrupts_counter_strategy(
                card.oracle_text or ""
            ):
                eligible = True
                card._is_hate_card = True

        if not eligible:
            continue

        if earliest_map is not None and not was_spec_eligible_at_reveal(
            card.name, pred_input.anchor_date, earliest_map
        ):
            continue

        if over_price_gate(price_cache.point_in_time_price(card.name, anchor)):
            continue

        if card.name not in seen_names:
            seen_names.add(card.name)
            candidates.append(card)

    return candidates


def cheap_prefilter_candidates(
    session: Session,
    pred_input: PredictionInput,
    candidates: list,
    top_k: int,
    *,
    cache=None,
) -> list:
    """Fast heuristic trim before ML feature building (mirrors training negative sampling)."""
    if len(candidates) <= top_k:
        return candidates

    from features.builder import FeatureCache
    from features.popularity import edhrec_demand_score

    pit_cache = cache if cache is not None else FeatureCache(session)
    as_of = pred_input.anchor_date
    deck_colors = pred_input.colors or []
    exclude = pred_input.exclude_deck_id
    point_in_time = as_of is not None

    synergy_ctx = DeckSynergyContext(
        colors=deck_colors,
        commander_name=pred_input.commander_name or "",
        commander_text=pred_input.commander_text or "",
        theme=pred_input.theme or "",
        product_description=pred_input.product_description or "",
        deck_card_names=list(pred_input.known_inclusions or []),
    )
    synergy_fits = compute_synergy_fits(candidates, synergy_ctx)

    # Experiment (config PREFILTER_USE_VELOCITY, default off): let sell-through
    # velocity lift cheap rising-demand cards past the prefilter so the ignition
    # signal can actually score them. No-op until the flag is set + volume coverage
    # is broad. Loaded once per prefilter call.
    from config import PREFILTER_USE_VELOCITY, PREFILTER_VELOCITY_WEIGHT
    volume_cache = None
    if PREFILTER_USE_VELOCITY:
        from engine.volume import VolumeCache
        volume_cache = VolumeCache.load(session)
    # Pre-anchor theme staples must survive the trim too (Atomize was cut while
    # being a documented archetype staple). Same fail-soft source as scoring.
    staples: dict = {}
    if os.getenv("MTG_THEME_STAPLES", "1") == "1":
        from features.mechanic_taxonomy import commander_mechanics as _cm
        from ingest.wayback_edhrec import theme_staple_scores as _tss
        staples = _tss(list(_cm(pred_input.commander_text or "", pred_input.theme or "",
                                pred_input.product_description or "")),
                       as_of or date.today())

    scored: list[tuple[float, object]] = []
    for i, card in enumerate(candidates):
        synergy = synergy_fits[i]
        if point_in_time:
            hist = pit_cache.historical_rate(card.name, deck_colors, as_of, exclude)
            edh = 0.3
        else:
            hist = 0.0
            edh = edhrec_demand_score(card.edhrec_rank)
        rank_score = synergy * 0.55 + hist * 0.25 + edh * 0.20
        if volume_cache is not None:
            rank_score += PREFILTER_VELOCITY_WEIGHT * volume_cache.velocity_factor(card.name, as_of)
        rank_score += 0.35 * staples.get(card.name, 0.0)
        scored.append((rank_score, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [card for _, card in scored[:top_k]]


def score_candidates(
    session: Session,
    pred_input: PredictionInput,
    top_n: int = 50,
    scorer: str = "heuristic",
    inclusion_model=None,
    reprint_model=None,
    spec_spike_model=None,
    features_df: Optional[pd.DataFrame] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
    feature_cache=None,
    candidates: Optional[list] = None,
    **kwargs,
) -> pd.DataFrame:
    """Score omitted candidates and return ranked predictions."""
    if candidates is None:
        candidates = get_candidates(session, pred_input)
    elif scorer == "ml" and features_df is not None and not features_df.empty:
        ml_names = set(features_df["card_name"])
        candidates = [card for card in candidates if card.name in ml_names]
    if log_fn:
        log_fn(f"Omitted-card candidate pool: {len(candidates)} cards", "info")
    if not candidates:
        if log_fn:
            log_fn("No omitted candidates after filters", "warn")
        return pd.DataFrame()

    cache = ScoringCache.build(session)
    market_supply = MarketSupplyCache(session)
    volume_cache = VolumeCache.load(session)
    # Pre-anchor EDHREC theme staples (Wayback; cached on disk; fail-soft {}).
    theme_staples: dict = {}
    if os.getenv("MTG_THEME_STAPLES", "1") == "1":
        from features.mechanic_taxonomy import commander_mechanics as _cmd_mechs
        from ingest.wayback_edhrec import theme_staple_scores
        _anchor = pred_input.anchor_date or date.today()
        _mechs = list(_cmd_mechs(pred_input.commander_text or "",
                                 pred_input.theme or "", pred_input.product_description or ""))
        theme_staples = theme_staple_scores(_mechs, _anchor)
    spike_prior = get_historical_spike_prior(session)
    point_in_time = pred_input.anchor_date is not None

    from features.builder import _rarity_score
    from features.mechanical import MechanicalPoolIndex, oracle_text_overlap

    as_of = pred_input.anchor_date
    pit_cache = feature_cache
    if pit_cache is None and as_of is not None:
        from features.builder import FeatureCache

        pit_cache = FeatureCache(session)

    pool_index = None
    if pit_cache is not None and hasattr(pit_cache, "mechanical_pool"):
        pool_index = pit_cache.mechanical_pool
    else:
        all_cards = session.query(Card).all()
        pool_index = MechanicalPoolIndex(all_cards)

    commander_oracle = pred_input.commander_text or ""
    if pred_input.commander_name:
        cmd_card = session.query(Card).filter_by(name=pred_input.commander_name).first()
        if cmd_card and cmd_card.oracle_text:
            commander_oracle = cmd_card.oracle_text

    theme_query = " ".join(
        filter(None, [pred_input.commander_text, pred_input.theme])
    )

    synergy_ctx = DeckSynergyContext(
        colors=pred_input.colors or [],
        commander_name=pred_input.commander_name or "",
        commander_text=pred_input.commander_text or "",
        theme=pred_input.theme or "",
        product_description=pred_input.product_description or "",
        deck_card_names=list(pred_input.known_inclusions or []),
    )
    synergy_fits = compute_synergy_fits(candidates, synergy_ctx)

    same_product_cards: set[str] = set()
    if pred_input.product_code:
        pcode_lower = pred_input.product_code.lower()
        same_product_cards = {
            p.card_name
            for p in session.query(CardPrinting).filter(
                CardPrinting.set_code == pcode_lower
            ).all()
            if p.card_name
        }

    rows = []
    for i, card in enumerate(candidates):
        is_hate_card = bool(getattr(card, "_is_hate_card", False))
        synergy_fit = (
            compute_hate_card_score(card, synergy_ctx)
            if is_hate_card
            else synergy_fits[i]
        )

        is_same_product = card.name in same_product_cards
        if is_same_product:
            synergy_fit = max(synergy_fit, 0.45)

        is_mana_fix_omission = is_mana_fixer_for_colors(
            card.type_line, list(card.color_identity or []), pred_input.colors
        )
        if is_mana_fix_omission:
            synergy_fit = max(synergy_fit, 0.30)

        hist = cache.historical_rate(card.name, pred_input.colors, pred_input.exclude_deck_id)
        edh = edhrec_demand_score(card.edhrec_rank)
        p_included = min(synergy_fit * 0.6 + (hist * 0.5 + edh * 0.5) * 0.4, 1.0)

        if pit_cache is not None and as_of is not None:
            stats = pit_cache.printing_stats(card.name, as_of)
            p_reprint = pit_cache.reprint_likelihood(card.name, as_of)
        else:
            cached = cache.printing_stats.get(card.name, {})
            stats = {
                "num_printings": cached.get("num_printings", 0),
                "last_reprint_days_ago": cached.get("last_reprint_days_ago", 9999),
                "first_printing_date": None,
            }
            p_reprint = cached.get("p_reprint", 0.1)

        scarcity = compute_scarcity_score(
            stats.get("num_printings", 0),
            stats.get("last_reprint_days_ago", 9999),
            card.reserved or False,
        )
        spec_supply = compute_spec_supply_score(
            stats.get("num_printings", 0),
            stats.get("last_reprint_days_ago", 9999),
            card.reserved or False,
            first_printing_date=stats.get("first_printing_date"),
            as_of_date=pred_input.anchor_date,
        )
        demand = edh
        p_spec_spike_ml = 0.0

        if scorer == "ml" and features_df is not None and inclusion_model and reprint_model:
            feat_row = features_df[features_df["card_name"] == card.name]
            if not feat_row.empty:
                from models.inclusion import predict_inclusion
                from models.reprint import predict_reprint
                from models.spec_spike import predict_spec_spike

                p_included_ml = float(predict_inclusion(inclusion_model, feat_row)[0])
                p_reprint_ml = float(predict_reprint(reprint_model, feat_row)[0])
                synergy_gate = max(synergy_fit, MIN_SYNERGY_FOR_ML_INCLUSION)
                # Floor ML with the heuristic estimate (p_included still holds it):
                # the inclusion model goes stale whenever feature distributions shift
                # and was zeroing surprising_omission for every candidate. ML may
                # raise the estimate, never kill the headline feature.
                p_included = min(max(p_included_ml * synergy_gate, p_included), 1.0)
                p_reprint = (
                    ML_REPRINT_HEURISTIC_BLEND * p_reprint
                    + (1 - ML_REPRINT_HEURISTIC_BLEND) * p_reprint_ml
                )
                if spec_spike_model is not None:
                    p_spec_spike_ml = float(predict_spec_spike(spec_spike_model, feat_row)[0])

        is_alt = is_alternate_commander(card, pred_input.commander_name, synergy_fit)
        prior_spike = spike_prior.score(
            card.name,
            pred_input.colors,
            as_of_date=pred_input.anchor_date,
            exclude_deck_id=pred_input.exclude_deck_id,
        )
        hist_spike = merge_historical_spike_scores(prior_spike, p_spec_spike_ml, synergy_fit)
        if is_hate_card:
            hist_spike = max(hist_spike, _HATE_CARD_HISTORICAL_SPIKE_FLOOR)
        if not is_hate_card:
            if hist_spike >= 0.25:
                synergy_fit = max(synergy_fit, MIN_SYNERGY_FOR_SPEC)
            elif hist_spike >= 0.12:
                synergy_fit = max(synergy_fit, MIN_SYNERGY_HARD_FLOOR + 0.01)

        surprising_omission = round(p_included, 4)
        visible_inv, seller_cnt = market_supply.scores_for_card(
            card.name,
            edhrec_rank=card.edhrec_rank if not point_in_time else None,
            point_in_time=point_in_time,
            session=session,
            anchor_date=pred_input.anchor_date if point_in_time else None,
        )
        oracle_overlap = oracle_text_overlap(commander_oracle, card.oracle_text or "")
        pool_size_score = pool_index.mechanical_pool_size_score(
            theme_query, card.oracle_text or ""
        )
        ignition = volume_cache.ignition_score(
            card.name, pred_input.anchor_date, spec_supply, synergy_fit
        )

        weighted_feats = {
            "surprising_omission_score": surprising_omission,
            "deck_synergy_direct": synergy_fit,
            "oracle_text_overlap": oracle_overlap,
            "mechanical_pool_size": pool_size_score,
            "tfidf_similarity": synergy_fit,
            "visible_inventory_score": visible_inv,
            "seller_count_score": seller_cnt,
            "spec_supply_score": spec_supply,
            "scarcity_score": scarcity,
            "last_reprint_days_ago": stats.get("last_reprint_days_ago", 9999),
            "is_reserved": int(card.reserved or False),
            "historical_omission_spike_score": prior_spike,
            "edhrec_inclusion_pct": demand,
            "historical_inclusion_rate": hist,
            "is_same_product_omission": int(is_same_product),
            "is_mana_fix_omission": int(is_mana_fix_omission),
            "rarity_score": _rarity_score(card.rarity),
            "ignition_score": ignition,
            "theme_staple_score": theme_staples.get(card.name, 0.0),
        }
        weighted_base = compute_weighted_spec_score(weighted_feats)

        opp = compute_spec_opportunity_score(
            synergy_fit=synergy_fit,
            surprising_omission_score=surprising_omission,
            p_reprint_adj=p_reprint,
            scarcity=scarcity,
            demand=demand,
            historical_spike_score=hist_spike,
            is_alt_commander=is_alt,
            spec_supply=spec_supply,
            proven_omission_spike=prior_spike,
            is_reserved=bool(card.reserved),
            weighted_base=weighted_base,
            theme_staple=theme_staples.get(card.name, 0.0),
        )

        rows.append({
            "card_name": card.name,
            "p_included": round(p_included, 4),
            "surprising_omission_score": surprising_omission,
            "p_reprint": round(p_reprint, 4),
            "synergy_fit": round(synergy_fit, 4),
            "scarcity_score": round(scarcity, 4),
            "spec_supply_score": round(spec_supply, 4),
            "demand_score": round(demand, 4),
            "historical_spike_score": round(hist_spike, 4),
            "is_alternate_commander": is_alt,
            "is_hate_card": is_hate_card,
            "is_same_product": is_same_product,
            "is_mana_fix_omission": is_mana_fix_omission,
            "opportunity_score": opp,
            "weighted_spec_score": weighted_base,
            "ignition_score": ignition,
            "theme_staple_score": theme_staples.get(card.name, 0.0),
            "oracle_text_overlap": oracle_overlap,
            "mechanical_pool_size": pool_size_score,
            "visible_inventory_score": visible_inv,
            "seller_count_score": seller_cnt,
            "type_line": card.type_line,
            "oracle_text": card.oracle_text or "",
            "edhrec_rank": card.edhrec_rank,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.sort_values("opportunity_score", ascending=False)
    preview = df.head(min(60, len(df)))
    anchors = list(pred_input.known_inclusions or []) + (
        [pred_input.commander_name] if pred_input.commander_name else []
    )
    if anchors:
        enriched = enrich_predictions_with_combos(preview, anchor_cards=anchors)
        combo_cols = enriched[["card_name", "has_infinite_loop", "combo_with"]]
        df = df.merge(combo_cols, on="card_name", how="left")
        df["has_infinite_loop"] = df["has_infinite_loop"].fillna(False)
        df["combo_with"] = df["combo_with"].fillna("")

    return df.head(top_n).reset_index(drop=True)


def predict_for_deck(
    session: Session,
    deck: CommanderDeck,
    top_n: int = 20,
    pred_input: Optional[PredictionInput] = None,
    **kwargs,
) -> pd.DataFrame:
    if pred_input is None:
        from engine.scoring_context import build_scoring_context, to_prediction_input

        ctx = build_scoring_context(deck, session, is_backtest=True)
        pred_input = to_prediction_input(ctx)
    return score_candidates(session, pred_input, top_n=top_n, **kwargs)


def get_actual_deck_cards(session: Session, deck_id: int) -> set:
    rows = session.query(DeckCard.card_name).filter(DeckCard.deck_id == deck_id).all()
    return {r[0] for r in rows}
