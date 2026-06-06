"""
ML inference helpers — prefilter large candidate pools before feature building.
"""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from config import ML_INFERENCE_PREFILTER_TOP_K
from db.schema import CommanderDeck
from engine.heuristic_scorer import PredictionInput, cheap_prefilter_candidates, get_candidates
from features.builder import FeatureCache, build_features_for_deck


def prepare_ml_inference(
    session: Session,
    deck: CommanderDeck,
    pred_input: PredictionInput,
    *,
    feature_cache: Optional[FeatureCache] = None,
    top_k: Optional[int] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> tuple[list, pd.DataFrame, FeatureCache]:
    """
    Trim omitted candidates with a cheap heuristic, then build ML features for top-K only.

    Training samples ~200 negatives per deck; scoring all ~17k candidates at inference
    was the main backtest slowdown after the Phase-3 refactor.
    """
    limit = top_k or ML_INFERENCE_PREFILTER_TOP_K
    cache = feature_cache or FeatureCache(session)
    candidates = get_candidates(session, pred_input)

    if len(candidates) > limit:
        if log_fn:
            log_fn(
                f"Prefiltering {len(candidates)} candidates → top {limit} for ML features",
                "info",
            )
        candidates = cheap_prefilter_candidates(
            session, pred_input, candidates, limit, cache=cache
        )

    if log_fn:
        log_fn(f"Building ML features for {len(candidates)} candidates", "info")

    features_df = build_features_for_deck(session, deck, candidates, cache)
    return candidates, features_df, cache
