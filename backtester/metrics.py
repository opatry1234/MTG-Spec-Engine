"""
Backtesting metrics — Phase-3 omitted-card evaluation.
"""

from typing import Set

import pandas as pd


def top_n_hit_rate(predicted: list, actual: Set[str], n: int = 10) -> float:
    if n == 0 or not predicted:
        return 0.0
    top = predicted[:n]
    hits = sum(1 for c in top if c in actual)
    return hits / n


def omission_hit_rate(predicted: list, actual: Set[str], n: int = 20) -> float:
    if n == 0 or not predicted:
        return 0.0
    top = predicted[:n]
    hits = sum(1 for c in top if c not in actual)
    return hits / n


def evaluate_predictions(
    predictions_df: pd.DataFrame,
    actual_included: Set[str],
    n: int = 20,
    **kwargs,
) -> dict:
    """Metrics for omitted-card spec ranking (decklist is always known)."""
    predicted = predictions_df["card_name"].tolist()
    avg_opp = float(predictions_df["opportunity_score"].mean()) if not predictions_df.empty else 0.0
    return {
        "stage": "decklist_revealed",
        "spec_targets_ranked": len(predicted),
        "avg_opportunity_score": round(avg_opp, 2),
        "top_n_omission_hit_rate": omission_hit_rate(predicted, actual_included, n),
        "top_n_inclusion_hit_rate": None,
        "precision_at_k": None,
        "recall_at_k": None,
        "omission_targets_found": len([c for c in predicted[:n] if c not in actual_included]),
        "n": n,
        "note": "Phase-3 only: all targets are cards not in the decklist.",
    }
