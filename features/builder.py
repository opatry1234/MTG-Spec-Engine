"""
Feature builder: combines all features into a single dataframe.

Orchestrates mechanical, NLP, supply, and popularity features.
"""

import random
from collections import defaultdict
from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from db.schema import Card, CardPrinting, CommanderDeck, DeckCard
from features.mechanical import (
    MechanicalPoolIndex,
    best_keyword_category_score,
    creature_type_overlap,
    extract_deck_creature_types,
    keyword_category_scores,
    oracle_text_overlap,
)
from features.market_supply import MarketSupplyCache
from features.precon_spike_features import compute_precon_spike_features
from filters.candidates import is_golden_spike_training_card, passes_candidate_filters
from features.nlp import tfidf_similarity
from features.popularity import edhrec_demand_score
from features.supply import (
    compute_reprint_likelihood,
    compute_scarcity_score,
    compute_spec_supply_score,
    printing_stats_at,
)
from engine.historical_spike_prior import get_historical_spike_prior
from engine.spec_eligibility import (
    build_earliest_printing_map,
    spec_anchor_date,
    was_spec_eligible_at_reveal,
)
from filters.staples import get_staples


FEATURE_COLUMNS = [
    "creature_type_overlap",
    "keyword_overlap_score",
    "token_score",
    "graveyard_score",
    "oracle_text_overlap",
    "mechanical_pool_size",
    "tfidf_similarity",
    "visible_inventory_score",
    "seller_count_score",
    "num_precon_printings",
    "last_reprint_days_ago",
    "is_reserved",
    "rarity_score",
    "edhrec_rank",
    "edhrec_inclusion_pct",
    "historical_inclusion_rate",
    "historical_omission_spike_score",
    "scarcity_score",
    "spec_supply_score",
    "spike_type_mechanic_score",
    "precon_cause_similarity",
    "mechanic_keyword_density",
    "single_printing_flag",
    "precon_spike_type_prior",
    "surprising_omission_score",
    "deck_synergy_direct",
    "combo_with_deck_card",
    "entry_price_penalty",
    "is_same_product_omission",
    "is_mana_fix_omission",
]

# Deprecated deck-prediction features — kept for backward-compatible model matrices only.
DEPRECATED_FEATURE_COLUMNS = [
    "copy_score",
    "num_printings",
    "p_reprint_heuristic",
]


def _rarity_score(rarity: str) -> float:
    from config import RARITY_SCORE_MAP

    return RARITY_SCORE_MAP.get((rarity or "").lower(), 0.35)


def _color_key(colors) -> str:
    if not colors:
        return "C"
    order = ["W", "U", "B", "R", "G"]
    return "".join(c for c in order if c in colors)


def _resolve_as_of(deck: CommanderDeck) -> date:
    # Spec anchor = decklist reveal date (Phase-3-only); release_date is the fallback.
    from engine.spec_eligibility import spec_anchor_date

    return spec_anchor_date(deck) or date.today()


def _sample_negatives(candidates: list, max_count: int) -> list:
    """Stratified sample across popularity and rarity buckets."""
    if len(candidates) <= max_count:
        return candidates

    buckets: dict[tuple, list] = defaultdict(list)
    for card in candidates:
        rank_bucket = "obscure" if (card.edhrec_rank or 99999) >= 10000 else "popular"
        rarity = (card.rarity or "common").lower()
        buckets[(rank_bucket, rarity)].append(card)

    picked: list = []
    bucket_keys = list(buckets.keys())
    random.shuffle(bucket_keys)
    while len(picked) < max_count and bucket_keys:
        for key in list(bucket_keys):
            if not buckets[key]:
                bucket_keys.remove(key)
                continue
            picked.append(buckets[key].pop())
            if len(picked) >= max_count:
                break
    return picked


class FeatureCache:
    """Pre-loaded data to avoid N+1 queries during feature building."""

    def __init__(self, session: Session):
        self.cards = {c.name: c for c in session.query(Card).all()}
        self.printings_by_name = self._load_printings_by_name(session)
        self.deck_cards = self._load_deck_cards(session)
        self.deck_meta = self._load_deck_meta(session)
        self.decks_by_color = self._index_decks_by_color()
        self.staples = get_staples(session)
        self.printings_by_set = self._load_printings_by_set(session)
        self.earliest_printing_map = build_earliest_printing_map(session)
        self.market_supply = MarketSupplyCache(session)
        from engine.pricing import PriceCache
        self.price_cache = PriceCache.load(session)
        self.mechanical_pool = MechanicalPoolIndex(list(self.cards.values()))

    def _load_printings_by_set(self, session):
        result = defaultdict(list)
        for p in session.query(CardPrinting).filter(CardPrinting.released_at.isnot(None)).all():
            if p.set_code:
                result[p.set_code.lower()].append(p)
        return result

    def _load_printings_by_name(self, session):
        by_name = defaultdict(list)
        for p in session.query(CardPrinting).all():
            by_name[p.card_name].append(p)
        return by_name

    def _load_deck_cards(self, session):
        result = defaultdict(set)
        for deck_id, card_name in session.query(DeckCard.deck_id, DeckCard.card_name).all():
            result[deck_id].add(card_name)
        return result

    def _load_deck_meta(self, session):
        meta = {}
        for deck in session.query(CommanderDeck).filter(
            CommanderDeck.decklist_revealed == True
        ).all():
            meta[deck.id] = {
                "anchor_date": spec_anchor_date(deck) or deck.release_date,
                "colors": deck.colors or [],
            }
        return meta

    def _index_decks_by_color(self):
        by_color = defaultdict(list)
        for deck_id, info in self.deck_meta.items():
            by_color[_color_key(info["colors"])].append(deck_id)
        return by_color

    def printing_stats(self, card_name: str, as_of_date: date) -> dict:
        printings = self.printings_by_name.get(card_name, [])
        return printing_stats_at(printings, as_of_date)

    def card_in_set_at(self, card_name: str, set_code: str, as_of_date: date) -> bool:
        for p in self.printings_by_set.get(set_code.lower(), []):
            if p.card_name == card_name and p.released_at and p.released_at <= as_of_date:
                return True
        return False

    def historical_rate(
        self,
        card_name: str,
        colors,
        as_of_date: date,
        exclude_deck_id=None,
    ) -> float:
        ck = _color_key(colors)
        deck_ids = self.decks_by_color.get(ck, [])
        eligible = []
        for deck_id in deck_ids:
            if exclude_deck_id and deck_id == exclude_deck_id:
                continue
            meta = self.deck_meta.get(deck_id, {})
            anchor = meta.get("anchor_date")
            if anchor is None or anchor >= as_of_date:
                continue
            eligible.append(deck_id)
        if not eligible:
            return 0.0
        hits = sum(1 for d in eligible if card_name in self.deck_cards.get(d, set()))
        return hits / len(eligible)

    def reprint_likelihood(self, card_name: str, as_of_date: date) -> float:
        stats = self.printing_stats(card_name, as_of_date)
        num = stats.get("num_printings", 0)
        precon = stats.get("num_precon_printings", 0)
        days = stats.get("last_reprint_days_ago", 9999)
        if num == 0:
            return 0.1
        precon_ratio = precon / num
        recency = max(0.0, 1.0 - days / 1825)
        return min(precon_ratio * 0.6 + recency * 0.4, 1.0)


def _deck_synergy_direct(card: Card, synergy_ctx) -> float:
    """Synergy vs deck context (caller builds context once per deck)."""
    from engine.deck_synergy import compute_synergy_fit

    return compute_synergy_fit(card, synergy_ctx)


def _combo_with_deck_card(card_name: str, deck_card_names: list[str]) -> float:
    """Lightweight placeholder for training; live scoring enriches via ComboChecker."""
    del card_name, deck_card_names
    return 0.0


def _entry_price_penalty(edhrec_rank: int | None) -> float:
    """Proxy: obscure/cheap cards score better for upside (inverse demand rank)."""
    rank = edhrec_rank or 99999
    return round(min(1.0, rank / 20000.0), 4)


def _resolve_entry_price_penalty(cache, card, as_of, point_in_time: bool) -> float:
    """Cheapness/upside 0..1. Prefer REAL point-in-time price (high = cheap); fall
    back to the edhrec proxy when price is unknown (e.g. backtests before history)."""
    price_cache = getattr(cache, "price_cache", None)
    if price_cache is not None:
        price = price_cache.point_in_time_price(card.name, as_of)
        if price is not None:
            from engine.pricing import price_factor
            return round(price_factor(price), 4)
    return _entry_price_penalty(card.edhrec_rank if not point_in_time else None)


def compute_all_features(
    session: Session,
    card: Card,
    deck: CommanderDeck,
    cache: FeatureCache = None,
    *,
    as_of_date: date | None = None,
    tfidf_score: float | None = None,
    spike_prior=None,
    synergy_ctx=None,
    theme_types: list | None = None,
    theme_query: str | None = None,
) -> dict:
    """Compute all features for a single (card, deck) pair."""
    as_of = as_of_date or _resolve_as_of(deck)
    deck_colors = deck.colors or []
    identity = card.color_identity or []
    point_in_time = spec_anchor_date(deck) is not None
    deck_cards = list(cache.deck_cards.get(deck.id, set()) if cache and deck.id else [])
    is_omitted = card.name not in set(deck_cards)

    cat_scores = keyword_category_scores(card.oracle_text or "")
    if tfidf_score is None:
        query = deck.commander_text or deck.theme or ""
        card_text = card.oracle_text or card.name
        tfidf = 0.0
        if query.strip():
            sims = tfidf_similarity(query, [card_text])
            tfidf = sims[0] if sims else 0.0
    else:
        tfidf = tfidf_score

    if cache:
        stats = cache.printing_stats(card.name, as_of)
        hist_rate = cache.historical_rate(card.name, deck_colors, as_of, deck.id)
        p_reprint = cache.reprint_likelihood(card.name, as_of)
    else:
        from features.popularity import historical_inclusion_rate
        from features.supply import get_printing_stats

        stats = get_printing_stats(session, card.name, as_of_date=as_of)
        hist_rate = historical_inclusion_rate(session, card.name, deck_colors, deck.id)
        p_reprint = compute_reprint_likelihood(session, card.name, as_of_date=as_of)

    if point_in_time:
        edhrec_rank = 99999
        edhrec_pct = 0.3
    else:
        edhrec_rank = card.edhrec_rank or 99999
        edhrec_pct = edhrec_demand_score(card.edhrec_rank)

    from features.mechanical import is_mana_fixer_for_colors

    set_code = (deck.product or "").lower()
    same_product = bool(
        cache and set_code and cache.card_in_set_at(card.name, set_code, as_of)
    ) if cache else False
    mana_fix = is_mana_fixer_for_colors(card.type_line, list(identity), deck_colors)
    heuristic_include = min(
        (tfidf * 0.6 + (hist_rate * 0.5 + edhrec_pct * 0.5) * 0.4),
        1.0,
    )
    surprising = round(heuristic_include * int(is_omitted), 4)
    deck_direct = _deck_synergy_direct(card, synergy_ctx) if synergy_ctx else tfidf
    combo_flag = _combo_with_deck_card(card.name, deck_cards) if is_omitted else 0.0

    if spike_prior is None:
        spike_prior = get_historical_spike_prior(session)
    if theme_types is None:
        theme_types = extract_deck_creature_types(
            commander_type_line="",
            theme=deck.theme or deck.deck_name or "",
            product_description=deck.product_description or "",
        )
        if deck.commander_name and cache:
            cmd = cache.cards.get(deck.commander_name)
            if cmd and cmd.type_line:
                theme_types = extract_deck_creature_types(
                    commander_type_line=cmd.type_line,
                    theme=deck.theme or deck.deck_name or "",
                    product_description=deck.product_description or "",
                )
    if theme_query is None:
        theme_query = " ".join(
            filter(
                None,
                [deck.commander_text or "", deck.theme or "", deck.deck_name or ""],
            )
        )

    commander_oracle = deck.commander_text or ""
    if deck.commander_name and cache:
        cmd_card = cache.cards.get(deck.commander_name)
        if cmd_card and cmd_card.oracle_text:
            commander_oracle = cmd_card.oracle_text

    oracle_overlap = oracle_text_overlap(commander_oracle, card.oracle_text or "")
    pool_score = 0.0
    if cache and hasattr(cache, "mechanical_pool"):
        pool_score = cache.mechanical_pool.mechanical_pool_size_score(
            theme_query, card.oracle_text or ""
        )

    visible_inv, seller_cnt = (0.5, 0.5)
    if cache and hasattr(cache, "market_supply"):
        visible_inv, seller_cnt = cache.market_supply.scores_for_card(
            card.name,
            edhrec_rank=card.edhrec_rank if not point_in_time else None,
            point_in_time=point_in_time,
            session=session,
            anchor_date=as_of if point_in_time else None,
        )
    elif not point_in_time:
        from features.market_supply import edhrec_supply_proxy

        visible_inv, seller_cnt = edhrec_supply_proxy(card.edhrec_rank)

    precon_feats = compute_precon_spike_features(
        oracle_text=card.oracle_text or card.name,
        theme_text=theme_query,
        num_printings=stats.get("num_printings", 0),
    )
    precon_spike_type_prior = spike_prior.spike_type_prior_score(
        card.name, deck_colors, as_of_date=as_of, exclude_deck_id=deck.id
    )

    return {
        "card_name": card.name,
        "deck_id": deck.id,
        "creature_type_overlap": creature_type_overlap(card.type_line or "", theme_types),
        "keyword_overlap_score": best_keyword_category_score(card.oracle_text or ""),
        "token_score": cat_scores.get("token", 0.0),
        "graveyard_score": cat_scores.get("graveyard", 0.0),
        "oracle_text_overlap": oracle_overlap,
        "mechanical_pool_size": pool_score,
        "tfidf_similarity": tfidf,
        "visible_inventory_score": visible_inv,
        "seller_count_score": seller_cnt,
        "num_precon_printings": stats.get("num_precon_printings", 0),
        "last_reprint_days_ago": stats.get("last_reprint_days_ago", 9999),
        "is_reserved": int(card.reserved or False),
        "rarity_score": _rarity_score(card.rarity),
        "edhrec_rank": edhrec_rank,
        "edhrec_inclusion_pct": edhrec_pct,
        "historical_inclusion_rate": hist_rate,
        "historical_omission_spike_score": spike_prior.score(
            card.name,
            deck_colors,
            as_of_date=as_of,
            exclude_deck_id=deck.id,
        ),
        "scarcity_score": compute_scarcity_score(
            stats.get("num_printings", 0),
            stats.get("last_reprint_days_ago", 9999),
            card.reserved or False,
        ),
        "spec_supply_score": compute_spec_supply_score(
            stats.get("num_printings", 0),
            stats.get("last_reprint_days_ago", 9999),
            card.reserved or False,
            first_printing_date=stats.get("first_printing_date"),
            as_of_date=as_of,
        ),
        **precon_feats,
        "precon_spike_type_prior": precon_spike_type_prior,
        "surprising_omission_score": surprising,
        "deck_synergy_direct": round(deck_direct, 4),
        "combo_with_deck_card": combo_flag,
        "entry_price_penalty": _resolve_entry_price_penalty(
            cache, card, as_of, point_in_time
        ),
        "is_same_product_omission": int(same_product and is_omitted),
        "is_mana_fix_omission": int(mana_fix and is_omitted),
    }


def build_features_for_deck(
    session: Session,
    deck: CommanderDeck,
    candidates: list,
    cache: FeatureCache = None,
) -> pd.DataFrame:
    """Build features for all candidates for a given deck."""
    if cache is None:
        cache = FeatureCache(session)
    as_of = _resolve_as_of(deck)
    spike_prior = get_historical_spike_prior(session)

    from engine.deck_synergy import DeckSynergyContext

    if deck.id:
        synergy_ctx = DeckSynergyContext.from_deck(deck, session)
    else:
        synergy_ctx = DeckSynergyContext(
            colors=list(deck.colors or []),
            commander_name=deck.commander_name or "",
            commander_text=deck.commander_text or "",
            theme=deck.theme or deck.deck_name or "",
            product_description=deck.product_description or "",
            deck_card_names=[],
        )

    theme_types = extract_deck_creature_types(
        commander_type_line="",
        theme=deck.theme or deck.deck_name or "",
        product_description=deck.product_description or "",
    )
    if deck.commander_name:
        cmd = cache.cards.get(deck.commander_name)
        if cmd and cmd.type_line:
            theme_types = extract_deck_creature_types(
                commander_type_line=cmd.type_line,
                theme=deck.theme or deck.deck_name or "",
                product_description=deck.product_description or "",
            )

    theme_query = " ".join(
        filter(
            None,
            [deck.commander_text or "", deck.theme or "", deck.deck_name or ""],
        )
    )

    query = deck.commander_text or deck.theme or ""
    oracle_texts = [c.oracle_text or c.name for c in candidates]
    batch_tfidf = []
    if query.strip():
        batch_tfidf = tfidf_similarity(query, oracle_texts)

    rows = []
    for i, card in enumerate(candidates):
        tfidf = batch_tfidf[i] if batch_tfidf else None
        rows.append(
            compute_all_features(
                session,
                card,
                deck,
                cache,
                as_of_date=as_of,
                tfidf_score=tfidf,
                spike_prior=spike_prior,
                synergy_ctx=synergy_ctx,
                theme_types=theme_types,
                theme_query=theme_query,
            )
        )
    return pd.DataFrame(rows)


def build_training_set(
    session: Session, max_decks: int = None, max_negatives_per_deck: int = 200
) -> pd.DataFrame:
    """Build training dataframe from historical decks."""
    cache = FeatureCache(session)
    spike_prior = get_historical_spike_prior(session)

    decks = (
        session.query(CommanderDeck)
        .filter(
            CommanderDeck.include_in_training == True,
            CommanderDeck.decklist_revealed == True,
        )
        .order_by(CommanderDeck.release_date)
        .all()
    )
    if max_decks:
        decks = decks[:max_decks]

    rows = []
    for deck in decks:
        as_of = _resolve_as_of(deck)
        included = cache.deck_cards.get(deck.id, set())
        deck_colors = deck.colors or []
        set_code = (deck.product or "").lower()

        candidates = [
            c
            for c in cache.cards.values()
            if c.name not in cache.staples
            and passes_candidate_filters(c, deck_colors)
            and was_spec_eligible_at_reveal(
                c.name, spec_anchor_date(deck), cache.earliest_printing_map
            )
        ]

        negatives = [c for c in candidates if c.name not in included]
        negatives = _sample_negatives(negatives, max_negatives_per_deck)

        deck_candidates = [cache.cards[n] for n in included if n in cache.cards] + negatives

        loose_names = spike_prior.deck_loose_spikes.get(deck.id, set())
        golden_names = spike_prior.deck_golden_spikes.get(deck.id, set())
        existing_names = {c.name for c in deck_candidates}
        for name in loose_names | golden_names:
            if name in included or name in existing_names:
                continue
            card = cache.cards.get(name)
            if card is None or not is_golden_spike_training_card(card, deck_colors):
                continue
            deck_candidates.append(card)
            existing_names.add(name)

        query = deck.commander_text or deck.theme or ""
        oracle_texts = [c.oracle_text or c.name for c in deck_candidates]
        batch_tfidf = []
        if query.strip():
            batch_tfidf = tfidf_similarity(query, oracle_texts)

        for i, card in enumerate(deck_candidates):
            tfidf = batch_tfidf[i] if batch_tfidf else None
            feats = compute_all_features(
                session,
                card,
                deck,
                cache,
                as_of_date=as_of,
                tfidf_score=tfidf,
            )
            feats["deck_release_date"] = spec_anchor_date(deck)
            feats["label_included"] = int(card.name in included)
            new_in_set = cache.card_in_set_at(card.name, set_code, as_of)
            feats["label_reprinted"] = int(card.name in included and not new_in_set)
            feats["label_spike_loose"] = int(spike_prior.was_deck_spike_target(deck.id, card.name))
            feats["label_spec_spike"] = int(
                spike_prior.was_deck_golden_target(deck.id, card.name)
            )
            rows.append(feats)

    return pd.DataFrame(rows)


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from config import DATABASE_URL

    parser = argparse.ArgumentParser()
    parser.add_argument("--deck-id", type=int)
    parser.add_argument("--max-decks", type=int, default=None)
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    sess = sessionmaker(bind=engine)()

    if args.deck_id:
        deck = sess.query(CommanderDeck).get(args.deck_id)
        cache = FeatureCache(sess)
        cands = [c for c in cache.cards.values() if c.name not in cache.staples]
        df = build_features_for_deck(sess, deck, cands, cache)
    else:
        df = build_training_set(sess, max_decks=args.max_decks)

    print(df.shape)
    print(df.head())
