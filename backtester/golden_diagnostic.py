"""
Post-grade diagnostic: score the golden spec cards for a deck regardless of
whether they survived the prefilter or made the top-N. Answers "what would the
golden picks have scored anyway, and where would they have ranked?" — which
reveals prefilter cuts and scoring gaps without affecting the grade.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from db.schema import Card


def score_golden_specs(
    session: Session,
    deck,
    pred_input,
    models: Optional[dict],
    predictions,
    golden_names: list[str],
    feature_cache=None,
) -> list[dict]:
    """For each golden spec: its opportunity_score, estimated rank in the full
    candidate ranking, whether it survived the prefilter, and if it'd be top-10.

    Never raises into the backtest — returns [] on any failure.
    """
    try:
        return _score(session, deck, pred_input, models, predictions, golden_names, feature_cache)
    except Exception:  # noqa: BLE001 — diagnostic must never break a backtest
        return []


def _score(session, deck, pred_input, models, predictions, golden_names, feature_cache) -> list[dict]:
    scored = {}
    all_opps = []
    if predictions is not None and not predictions.empty:
        for row in predictions.itertuples():
            opp = float(getattr(row, "opportunity_score", 0.0) or 0.0)
            scored[row.card_name] = opp
            all_opps.append(opp)

    cards_by_name = {c.name: c for c in session.query(Card).all()}
    golden_names = [n for n in dict.fromkeys(golden_names) if n in cards_by_name]
    missing = [n for n in golden_names if n not in scored]

    # Score golden cards the prefilter dropped, bypassing it entirely.
    golden_scores: dict[str, float] = {}
    use_ml = bool(models and models.get("inclusion") and models.get("reprint"))
    if missing and use_ml:
        from engine.heuristic_scorer import score_candidates
        from features.builder import build_features_for_deck

        cobjs = [cards_by_name[n] for n in missing]
        fdf = build_features_for_deck(session, deck, cobjs, feature_cache)
        gdf = score_candidates(
            session, pred_input,
            candidates=cobjs, scorer="ml",
            inclusion_model=models["inclusion"],
            reprint_model=models["reprint"],
            spec_spike_model=models.get("spec_spike"),
            features_df=fdf, top_n=len(cobjs),
        )
        if gdf is not None and not gdf.empty:
            for row in gdf.itertuples():
                golden_scores[row.card_name] = float(getattr(row, "opportunity_score", 0.0) or 0.0)

    out = []
    for name in golden_names:
        if name in scored:
            opp, survived = scored[name], True
        elif name in golden_scores:
            opp, survived = golden_scores[name], False
        else:
            out.append({
                "card_name": name, "survived_prefilter": False,
                "opportunity_score": None, "est_rank": None, "in_top_10": False,
            })
            continue
        est_rank = sum(1 for o in all_opps if o > opp) + 1
        out.append({
            "card_name": name,
            "survived_prefilter": survived,
            "opportunity_score": round(opp, 2),
            "est_rank": est_rank,
            "in_top_10": est_rank <= 10,
        })
    return out
