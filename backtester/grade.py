"""
Letter grade (F–A+) for backtest spec-target quality.

Grades measure recall of **actual golden spec targets** — cards that spiked as
attributed omission upgrades for this precon — within the top spec predictions.

Score = golden_specs_found / golden_spec_count (e.g. 2/2 found → 100% → A+).
"""

from __future__ import annotations

from typing import Set

import pandas as pd
from sqlalchemy.orm import Session

from config import GOLDEN_SPIKE_MIN_RELATIVE_PCT, MAX_SPEC_TOP_N
from backtester.spike_check import check_card_spike
from backtester.spike_reasoning import find_reasoning_golden_benchmarks
from engine.spec_eligibility import (
    build_earliest_printing_map,
    earliest_printing_date,
    was_spec_eligible_at_reveal,
)


def _spike_csv_available() -> bool:
    from config import SPIKE_CSV_PATH

    return SPIKE_CSV_PATH.exists()


GRADE_SCALE = [
    (1.00, "A+"),
    (0.93, "A"),
    (0.87, "A-"),
    (0.80, "B+"),
    (0.73, "B"),
    (0.67, "B-"),
    (0.60, "C+"),
    (0.53, "C"),
    (0.47, "C-"),
    (0.40, "D"),
    (0.05, "D-"),  # bottom of top-N window — card appeared but barely
    (0.00, "F"),
]


def score_to_letter(score: float) -> str:
    for threshold, letter in GRADE_SCALE:
        if score >= threshold:
            return letter
    return "F"


def rank_position_score(rank: int, eval_n: int) -> float:
    """Linear rank-position score: rank 1 = 1.0, rank eval_n = 1/eval_n.

    Rewards golden specs found near the top of the prediction list. A card
    found at rank 1 contributes a perfect 1.0; at the bottom of the window
    it contributes 1/eval_n (e.g. 0.10 for top-10 → D-). Not finding the
    card contributes 0.0 (F).

    For decks with multiple golden specs the final score is the mean of all
    per-spec position scores (including 0 for missed specs).
    """
    if eval_n <= 0:
        return 0.0
    return (eval_n - rank + 1) / eval_n


def _build_card_color_map(session: Session) -> dict[str, list]:
    from db.schema import Card

    return {
        (card.name or "").lower(): list(card.color_identity or [])
        for card in session.query(Card).all()
        if card.name
    }


def _build_cards_by_name(session: Session) -> dict:
    from db.schema import Card

    return {(card.name or "").lower(): card for card in session.query(Card).all() if card.name}


def _resolve_golden_benchmarks(
    *,
    session: Session,
    actual_deck: Set[str],
    release_date,
    precon_release_date,
    product_code,
    predicted_top: set[str],
    deck_colors,
    deck_synergy_ctx,
    golden_spikes,
    earliest_map,
    color_map,
    cards_by_name,
    deck_name: str = "",
    commander_name: str = "",
) -> list[dict]:
    if golden_spikes is not None:
        # Pre-computed by historical_spike_prior (already reasoning-first).
        rows = list(golden_spikes)
    else:
        # Direct grading path (e.g. CLI or Results page re-grade).
        # Use ONLY the human-curated Spike Reasoning sheet — no automated fallback.
        # The automated CSV fallback (find_omission_spike_benchmarks) attributes
        # golden specs by temporal proximity alone, producing false benchmarks for
        # decks whose cause text doesn't name them explicitly. All valid golden specs
        # must be explicitly entered in the Spike Reasoning sheet with a Spike Cause
        # that references the deck name so _row_matches_deck() can find them.
        rows = find_reasoning_golden_benchmarks(
            deck_name,
            product_code or "",
            commander_name=commander_name,
        )

    predicted_lower = {n.lower() for n in predicted_top}
    for row in rows:
        row["in_top_predictions"] = row.get("card_name", "").lower() in predicted_lower
    return rows


def _filter_golden_by_reveal(rows: list[dict], reveal_date) -> list[dict]:
    """Drop golden specs whose spike report predates decklist reveal."""
    if not reveal_date:
        return rows
    from datetime import date as date_cls

    kept = []
    for row in rows:
        rd = row.get("report_date")
        if rd is None:
            kept.append(row)
            continue
        if isinstance(rd, str):
            rd = date_cls.fromisoformat(rd[:10])
        if rd >= reveal_date:
            kept.append(row)
    return kept


def grade_spec_targets(
    session: Session,
    predictions_df: pd.DataFrame,
    actual_deck: Set[str],
    reveal_date=None,
    top_n: int = 20,
    *,
    release_date=None,
    precon_release_date=None,
    product_code: str | None = None,
    deck_colors: list | None = None,
    deck_synergy_ctx=None,
    fetch_prices: bool = True,
    log_fn=None,
    golden_spikes: list | None = None,
    deck_name: str = "",
    commander_name: str = "",
) -> dict:
    """
    Grade spec predictions against golden omission-spike benchmarks for this deck.

    Only the top min(top_n, MAX_SPEC_TOP_N) predictions are considered. The score
    is the fraction of golden specs found in that window (not good_picks / all rows).
    """
    if release_date is not None and reveal_date is None:
        reveal_date = release_date
    eval_n = min(top_n, MAX_SPEC_TOP_N)

    if predictions_df.empty:
        return {
            "letter": "F",
            "score": 0.0,
            "top_n": eval_n,
            "evaluation_top_n": eval_n,
            "good_picks": 0,
            "golden_spec_count": 0,
            "golden_specs_found": 0,
            "golden_recall": 0.0,
            "cards_graded": 0,
            "cards": [],
            "price_data_available": 0,
            "omission_hits": 0,
            "note": "No predictions to grade.",
            "golden_spikes": [],
        }

    top = predictions_df.head(eval_n)
    earliest_map = build_earliest_printing_map(session) if session and reveal_date else {}
    color_map = _build_card_color_map(session) if session else {}
    cards_by_name = _build_cards_by_name(session) if session else {}

    predicted_top = set(predictions_df.head(eval_n)["card_name"].tolist())
    golden_rows = _resolve_golden_benchmarks(
        session=session,
        actual_deck=actual_deck,
        release_date=reveal_date,
        precon_release_date=precon_release_date,
        product_code=product_code,
        predicted_top=predicted_top,
        deck_colors=deck_colors,
        color_map=color_map,
        deck_synergy_ctx=deck_synergy_ctx,
        golden_spikes=golden_spikes,
        earliest_map=earliest_map,
        cards_by_name=cards_by_name,
        deck_name=deck_name,
        commander_name=commander_name,
    )
    golden_rows = _filter_golden_by_reveal(golden_rows, reveal_date)

    golden_names = [
        row["card_name"]
        for row in golden_rows
        if row.get("precon_attributed", True) and row.get("card_name")
    ]
    golden_set = {n.lower(): n for n in golden_names}
    ranked = predictions_df.head(eval_n)["card_name"].tolist()
    rank_by_name = {name.lower(): i + 1 for i, name in enumerate(ranked)}
    # Full-list ranks for recall@k diagnostics (the top-N grade hides progress when
    # goldens move e.g. 250 → 30; recall@25/@50 makes iteration measurable).
    full_ranks = {
        n.lower(): i + 1 for i, n in enumerate(predictions_df["card_name"].tolist())
    }

    hits = []
    position_scores = []
    for key, display in golden_set.items():
        rank = rank_by_name.get(key)
        if rank is not None:
            pos_score = rank_position_score(rank, eval_n)
            hits.append({"card_name": display, "rank": rank, "position_score": round(pos_score, 3)})
            position_scores.append(pos_score)
        else:
            position_scores.append(0.0)  # missed spec = 0

    golden_count = len(golden_set)
    found = len(hits)
    # Rank-weighted score: average position score across all golden specs.
    # A single spec at rank 1 → 1.0 (A+); at rank eval_n → 1/eval_n (D-);
    # not found → 0.0 (F). Multiple specs: mean of all position scores.
    score = sum(position_scores) / golden_count if golden_count else 0.0
    letter = score_to_letter(score)

    cards_out = []
    good = 0
    omission_hits = 0
    price_ok = 0
    ineligible = 0

    for i, row in enumerate(top.itertuples(), start=1):
        name = row.card_name
        not_in_deck = name not in actual_deck
        if not_in_deck:
            omission_hits += 1

        spec_eligible = was_spec_eligible_at_reveal(name, reveal_date, earliest_map)
        if not spec_eligible:
            ineligible += 1

        spike_info = {"had_spike": False, "price_source": "missing"}
        if session is not None and not_in_deck and reveal_date and spec_eligible:
            if log_fn:
                log_fn(f"Checking price spike [{i}/{len(top)}] {name}", "info")
            spike_info = check_card_spike(
                session,
                name,
                reveal_date,
                precon_release_date=precon_release_date,
                product_code=product_code,
                fetch_if_missing=fetch_prices,
                deck_colors=deck_colors,
                card_color_map=color_map,
                deck_synergy_ctx=deck_synergy_ctx,
                cards_by_name=cards_by_name,
            )
            if spike_info.get("price_source") not in (None, "missing"):
                price_ok += 1

        is_golden = name.lower() in golden_set
        is_good = is_golden and rank_by_name.get(name.lower()) is not None
        if is_good:
            good += 1

        earliest = earliest_printing_date(name, earliest_map)
        cards_out.append(
            {
                "rank": i,
                "card_name": name,
                "not_in_deck": not_in_deck,
                "spec_eligible": spec_eligible,
                "earliest_printing": earliest.isoformat() if earliest else None,
                "had_spike": spike_info.get("had_spike", False),
                "precon_attributed": spike_info.get("precon_attributed", False),
                "attribution": spike_info.get("attribution"),
                "baseline_price": spike_info.get("baseline_price"),
                "peak_price": spike_info.get("peak_price"),
                "spike_pct": spike_info.get("spike_pct"),
                "spike_usd": spike_info.get("spike_usd"),
                "price_source": spike_info.get("price_source"),
                "good_pick": is_good,
                "golden_spec": is_golden,
                "opportunity_score": getattr(row, "opportunity_score", None),
                "has_infinite_loop": bool(getattr(row, "has_infinite_loop", False)),
                "combo_with": getattr(row, "combo_with", "") or "",
                "report_date": spike_info.get("report_date"),
                "spike_set": spike_info.get("set_name"),
                "spike_set_code": spike_info.get("set_code"),
                "spike_collector_number": spike_info.get("collector_number"),
                "spike_printing_key": spike_info.get("printing_key"),
            }
        )

    from backtester.spike_csv import spike_window

    window_start, window_end = (
        spike_window(reveal_date, precon_release_date) if reveal_date else (None, None)
    )

    if golden_count == 0:
        note = (
            "No golden omission-spike benchmarks for this deck in the spike CSV "
            "(nothing to grade against)."
        )
    elif found == golden_count:
        ranks_str = ", ".join(f"#{h['rank']}" for h in hits)
        note = (
            f"Found all {found}/{golden_count} actual spec targets in the top {eval_n} "
            f"(ranks: {ranks_str}; rank-weighted score {score:.2f})."
        )
    else:
        missed = [golden_set[k] for k in golden_set if k not in rank_by_name]
        found_str = ", ".join(f"{h['card_name']} #{h['rank']}" for h in hits) if hits else "none"
        note = (
            f"Found {found}/{golden_count} actual spec targets in the top {eval_n} "
            f"(rank-weighted score {score:.2f})."
        )
        if hits:
            note += f" Found: {found_str}."
        if missed:
            note += f" Missed: {', '.join(missed)}."

    if price_ok == 0 and golden_count and not _spike_csv_available():
        note += (
            " No spike bible — place data/raw/Spike Data.xlsx (Spike Reasoning sheet)."
        )

    recall_at = {}
    if golden_count:
        for k in (10, 25, 50, 100):
            n_in = sum(1 for key in golden_set if (full_ranks.get(key) or 10**9) <= k)
            recall_at[f"recall_at_{k}"] = round(n_in / golden_count, 3)

    return {
        "letter": letter,
        "score": round(score, 3),
        **recall_at,
        "golden_recall": round(score, 3),  # kept for backwards compat
        "rank_weighted_score": round(score, 3),
        "top_n": eval_n,
        "evaluation_top_n": eval_n,
        "good_picks": good,
        "golden_spec_count": golden_count,
        "golden_specs_found": found,
        "golden_hits": hits,
        "cards_graded": len(top),
        "omission_hits": omission_hits,
        "omission_rate": round(omission_hits / len(top), 3) if len(top) else 0.0,
        "price_data_available": price_ok,
        "ineligible_picks": ineligible,
        "reveal_date": reveal_date.isoformat() if reveal_date else None,
        "release_date": reveal_date.isoformat() if reveal_date else None,
        "cards": cards_out,
        "note": note,
        "spike_window_start": window_start.isoformat() if window_start else None,
        "spike_window_end": window_end.isoformat() if window_end else None,
        "golden_spikes": golden_rows,
    }
