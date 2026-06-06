"""
Core backtesting logic — Phase-3 omitted-card scoring anchored to reveal date.
"""

from typing import Callable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from backtester.metrics import evaluate_predictions
from backtester.grade import grade_spec_targets
from db.schema import CommanderDeck, PredictionRun
from engine.heuristic_scorer import get_actual_deck_cards
from engine.pipeline import predict_product
from engine.scoring_context import build_scoring_context, to_prediction_input, visible_fields_summary
from engine.spec_eligibility import spec_anchor_date


def _fmt_pct(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "—"


def _batch_deck_row(deck, result: dict) -> dict:
    m = result["metrics"]
    row = {
        "deck_id": deck.id,
        "deck_name": result.get("deck_name") or deck.deck_name,
        "product": result.get("product") or deck.product,
        "stage": "decklist_revealed",
        "omission_hit_rate": m.get("top_n_omission_hit_rate"),
        "avg_opportunity_score": m.get("avg_opportunity_score"),
    }
    grade = result.get("grade")
    if grade:
        row.update(
            {
                "letter_grade": grade.get("letter"),
                "grade_score": grade.get("score"),
                "golden_recall": grade.get("golden_recall"),
                "golden_specs_found": grade.get("golden_specs_found"),
                "golden_spec_count": grade.get("golden_spec_count"),
                "good_picks": grade.get("good_picks"),
                "omission_hits": grade.get("omission_hits"),
            }
        )
    else:
        row["letter_grade"] = None
    return row


def run_backtest(
    deck_id: int,
    session: Session,
    models: Optional[dict] = None,
    top_n: int = 20,
    log_fn: Optional[Callable[[str, str], None]] = None,
    feature_cache=None,
    save_run: bool = True,
    grade_predictions: bool = True,
    fetch_spike_prices: bool = False,
    **kwargs,
) -> dict:
    def log(msg: str, level: str = "step") -> None:
        if log_fn:
            log_fn(msg, level)

    log(f"Loading deck id={deck_id}")
    deck = session.query(CommanderDeck).get(deck_id)
    if not deck:
        raise ValueError(f"Deck {deck_id} not found")

    log(f"Deck: {deck.deck_name} ({deck.product})", "info")
    ctx = build_scoring_context(deck, session, is_backtest=True)
    anchor = spec_anchor_date(deck)
    log(f"Spec anchor: {anchor}", "info")
    result_note = None
    if not (deck.product_description or "").strip():
        result_note = (
            "This deck has no product_description in the database — "
            "using theme/deck name as a stand-in."
        )
    pred_input = to_prediction_input(ctx)

    predict_kwargs = {"top_n": top_n, "log_fn": log_fn, "feature_cache": feature_cache}
    if models and models.get("inclusion") and models.get("reprint"):
        from engine.ml_inference import prepare_ml_inference

        predict_kwargs["scorer"] = "ml"
        predict_kwargs["inclusion_model"] = models["inclusion"]
        predict_kwargs["reprint_model"] = models["reprint"]
        if models.get("spec_spike") is not None:
            predict_kwargs["spec_spike_model"] = models["spec_spike"]

        candidates, features_df, cache = prepare_ml_inference(
            session,
            deck,
            pred_input,
            feature_cache=feature_cache,
            log_fn=log,
        )
        predict_kwargs["features_df"] = features_df
        predict_kwargs["feature_cache"] = cache
        predict_kwargs["candidates"] = candidates

    product = predict_product(session, pred_input, **predict_kwargs)
    predictions = product.cards

    actual = get_actual_deck_cards(session, deck_id)
    log(f"Evaluating top {top_n} omitted targets vs {len(actual)} deck cards", "info")
    metrics = evaluate_predictions(predictions, actual, n=top_n)
    omit = metrics.get("top_n_omission_hit_rate")
    log(f"Metrics: omission hit={_fmt_pct(omit)}", "ok")

    grade = None
    if grade_predictions and not predictions.empty:
        from config import SPIKE_CSV_PATH
        from engine.deck_synergy import DeckSynergyContext
        from engine.historical_spike_prior import get_historical_spike_prior

        if SPIKE_CSV_PATH.exists():
            log("Grading spec targets using spike reasoning sheet…", "step")
        synergy_ctx = DeckSynergyContext.from_deck(deck, session)
        prior = get_historical_spike_prior(session)
        golden_spikes = prior.deck_golden_benchmarks.get(deck.id)
        grade = grade_spec_targets(
            session,
            predictions,
            actual,
            anchor,
            top_n=top_n,
            product_code=deck.product,
            deck_colors=list(deck.colors or []),
            deck_synergy_ctx=synergy_ctx,
            fetch_prices=fetch_spike_prices,
            log_fn=log_fn,
            golden_spikes=golden_spikes,
            deck_name=deck.deck_name or "",
            commander_name=deck.commander_name or "",
        )
        log(
            f"Spec grade: {grade['letter']} "
            f"({grade['golden_specs_found']}/{grade['golden_spec_count']} golden specs "
            f"in top {grade['evaluation_top_n']})",
            "ok",
        )

    run = PredictionRun(
        deck_id=deck_id,
        model_version=models.get("version", "heuristic") if models else "heuristic",
        reprint_ceiling=deck.new_cards,
        notes=f"phase3, top_n={top_n}, omission_hit={metrics.get('top_n_omission_hit_rate')}",
    )
    if save_run:
        session.add(run)
        session.commit()
    else:
        run.id = None

    return {
        "deck_id": deck_id,
        "deck_name": deck.deck_name,
        "product": deck.product,
        "visible_fields": visible_fields_summary(ctx),
        "predictions": predictions,
        "actual_cards": actual,
        "metrics": metrics,
        "run_id": run.id,
        "data_warning": result_note,
        "grade": grade,
    }


def backtest_all_decks(
    session: Session,
    models: Optional[dict] = None,
    top_n: int = 20,
    limit: Optional[int] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
    on_deck_start: Optional[Callable[[int, int, str], None]] = None,
    on_deck_done: Optional[Callable[[int, int, str, dict], None]] = None,
    save_runs: bool = False,
    parallel_workers: int = 0,
    grade_predictions: bool = True,
    fetch_spike_prices: bool = False,
    **kwargs,
) -> pd.DataFrame:
    def log(msg: str, level: str = "step") -> None:
        if log_fn:
            log_fn(msg, level)

    from features.builder import FeatureCache

    log("Loading shared feature cache for batch…", "info")
    feature_cache = FeatureCache(session)
    log("Feature cache ready", "ok")

    decks = (
        session.query(CommanderDeck)
        .filter(CommanderDeck.decklist_revealed == True)
        .order_by(CommanderDeck.release_date.desc())
        .all()
    )
    if limit:
        decks = decks[:limit]

    total = len(decks)
    log(f"Batch backtest: {total} decks (Phase 3)", "info")

    if parallel_workers > 0 and total > 1:
        return _backtest_all_parallel(
            decks=decks,
            models=models,
            top_n=top_n,
            log_fn=log_fn,
            on_deck_start=on_deck_start,
            on_deck_done=on_deck_done,
            save_runs=save_runs,
            parallel_workers=parallel_workers,
            grade_predictions=grade_predictions,
            fetch_spike_prices=fetch_spike_prices,
        )

    summaries = []
    for i, deck in enumerate(decks, start=1):
        try:
            if on_deck_start:
                on_deck_start(i, total, deck.deck_name)
            log(f"[{i}/{total}] {deck.deck_name} ({deck.product})", "step")
            result = run_backtest(
                deck.id,
                session,
                models=models,
                top_n=top_n,
                log_fn=log_fn,
                feature_cache=feature_cache,
                save_run=save_runs,
                grade_predictions=grade_predictions,
                fetch_spike_prices=fetch_spike_prices,
            )
            row = _batch_deck_row(deck, result)
            summaries.append(row)
            if on_deck_done:
                on_deck_done(i, total, deck.deck_name, row)
            grade_s = f", grade {row.get('letter_grade')}" if row.get("letter_grade") else ""
            log(
                f"[{i}/{total}] Done — omission {_fmt_pct(row.get('omission_hit_rate'))}{grade_s}",
                "ok",
            )
        except ValueError:
            log(f"[{i}/{total}] Skipped {deck.deck_name} (missing data)", "warn")
            continue

    log(f"Batch complete: {len(summaries)}/{total} decks evaluated", "ok")
    return pd.DataFrame(summaries)


def _backtest_worker(payload: dict) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from config import DATABASE_URL

    deck_id = payload["deck_id"]
    engine = create_engine(DATABASE_URL)
    worker_session = sessionmaker(bind=engine)()
    try:
        models = payload.get("models")
        if payload.get("model_version"):
            from models.trainer import load_models

            models = load_models(payload["model_version"])

        result = run_backtest(
            deck_id,
            worker_session,
            models=models,
            top_n=payload["top_n"],
            log_fn=None,
            feature_cache=None,
            save_run=payload.get("save_runs", False),
            grade_predictions=payload.get("grade_predictions", True),
            fetch_spike_prices=payload.get("fetch_spike_prices", False),
        )
        deck = worker_session.query(CommanderDeck).get(deck_id)
        return _batch_deck_row(deck, result)
    finally:
        worker_session.close()


def _backtest_all_parallel(
    *,
    decks,
    models,
    top_n,
    log_fn,
    on_deck_start,
    on_deck_done,
    save_runs,
    parallel_workers,
    grade_predictions: bool = True,
    fetch_spike_prices: bool = False,
) -> pd.DataFrame:
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed

    workers = min(parallel_workers, max(1, (os.cpu_count() or 2) - 1), len(decks))
    model_version = models.get("version") if models else None
    payloads = [
        {
            "deck_id": deck.id,
            "top_n": top_n,
            "save_runs": save_runs,
            "model_version": model_version,
            "models": None,
            "grade_predictions": grade_predictions,
            "fetch_spike_prices": fetch_spike_prices,
        }
        for deck in decks
    ]

    summaries = []
    total = len(decks)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_backtest_worker, p): p for p in payloads}
        for i, future in enumerate(as_completed(futures), start=1):
            payload = futures[future]
            deck_id = payload["deck_id"]
            deck = next(d for d in decks if d.id == deck_id)
            if on_deck_start:
                on_deck_start(i, total, deck.deck_name)
            try:
                row = future.result()
                summaries.append(row)
                if on_deck_done:
                    on_deck_done(i, total, deck.deck_name, row)
                if log_fn:
                    log_fn(f"[{i}/{total}] Done — {deck.deck_name}", "ok")
            except Exception as exc:
                if log_fn:
                    log_fn(f"[{i}/{total}] Failed {deck.deck_name}: {exc}", "warn")

    if log_fn:
        log_fn(f"Batch complete: {len(summaries)}/{total} decks evaluated", "ok")
    return pd.DataFrame(summaries)
