"""
Phase-3 prediction pipeline — score omitted cards from a public decklist.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from engine.heuristic_scorer import PredictionInput, score_candidates


@dataclass
class ProductPrediction:
    cards: pd.DataFrame = field(default_factory=pd.DataFrame)


def predict_product(
    session: Session,
    pred_input: PredictionInput,
    *,
    top_n: int = 50,
    scorer: str = "heuristic",
    inclusion_model=None,
    reprint_model=None,
    spec_spike_model=None,
    features_df: Optional[pd.DataFrame] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
    feature_cache=None,
    **kwargs,
) -> ProductPrediction:
    def log(msg: str, level: str = "step") -> None:
        if log_fn:
            log_fn(msg, level)

    log("Scoring omitted-card spec targets (Phase 3)")
    cards = score_candidates(
        session,
        pred_input,
        top_n=top_n,
        scorer=scorer,
        inclusion_model=inclusion_model,
        reprint_model=reprint_model,
        spec_spike_model=spec_spike_model,
        features_df=features_df,
        log_fn=log_fn,
        feature_cache=feature_cache,
        candidates=kwargs.get("candidates"),
    )
    log(f"Ranked {len(cards)} omitted-card targets", "ok")
    return ProductPrediction(cards=cards)
