"""
Save backtest outputs to data/analytics/backtest_runs for UI and CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config import DATA_DIR

BACKTEST_OUTPUT_DIR = DATA_DIR / "analytics" / "backtest_runs"


def timestamp_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_output_dir(run_id: Optional[str] = None) -> Path:
    path = BACKTEST_OUTPUT_DIR / (run_id or timestamp_run_id())
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def enrich_batch_summary(summary: dict, deck_df: pd.DataFrame) -> dict:
    """Add letter-grade aggregates and per-deck grade list for batch_summary.json."""
    out = dict(summary)
    if deck_df is None or deck_df.empty or "letter_grade" not in deck_df.columns:
        return out

    graded = deck_df[deck_df["letter_grade"].notna() & (deck_df["letter_grade"] != "")]
    out["graded_deck_count"] = int(len(graded))
    if graded.empty:
        return out

    # A deck with zero golden benchmarks has no ground truth to grade against;
    # scoring it F=0 pollutes the aggregate. Treat such decks as ungradeable and
    # report grade_score / distribution only over decks that actually have specs.
    if "golden_spec_count" in graded.columns:
        gradeable = graded[graded["golden_spec_count"].fillna(0) > 0]
        out["ungradeable_deck_count"] = int(len(graded) - len(gradeable))
    else:
        gradeable = graded
    out["gradeable_deck_count"] = int(len(gradeable))
    if gradeable.empty:
        return out

    if "grade_score" in gradeable.columns:
        scores = gradeable["grade_score"].dropna()
        if not scores.empty:
            out["mean_grade_score"] = float(scores.mean())

    dist = gradeable["letter_grade"].value_counts().sort_index()
    out["grade_distribution"] = {str(k): int(v) for k, v in dist.items()}

    if "golden_spec_count" in graded.columns and "golden_specs_found" in graded.columns:
        denom = graded["golden_spec_count"].replace(0, pd.NA).dropna()
        if not denom.empty:
            numer = graded.loc[denom.index, "golden_specs_found"]
            out["mean_golden_spec_recall"] = float((numer / denom).mean())

    grade_cols = [
        c
        for c in (
            "deck_id",
            "deck_name",
            "product",
            "letter_grade",
            "grade_score",
            "golden_specs_found",
            "golden_spec_count",
            "good_picks",
            "omission_hits",
        )
        if c in graded.columns
    ]
    out["deck_grades"] = graded[grade_cols].to_dict(orient="records")
    return out


def list_saved_runs(limit: int = 50) -> list[dict]:
    """Return saved run folders newest-first."""
    if not BACKTEST_OUTPUT_DIR.exists():
        return []

    runs = []
    for path in sorted(BACKTEST_OUTPUT_DIR.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        summary_files = list(path.glob("*_summary.json"))
        meta = {"run_id": path.name, "path": str(path)}
        if summary_files:
            try:
                with open(summary_files[0]) as f:
                    meta.update(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
        deck_files = list(path.glob("deck_*.json"))
        if deck_files:
            meta.setdefault("mode", "deck")
            meta["deck_files"] = [p.name for p in deck_files]
        runs.append(meta)
        if len(runs) >= limit:
            break
    return runs


def save_skeleton_run(summary: dict, deck_rows: list[dict], run_id: Optional[str] = None) -> Path:
    out_dir = run_output_dir(run_id)
    payload = {"mode": "skeleton", **summary}
    write_json(out_dir / "skeleton_summary.json", payload)
    pd.DataFrame(deck_rows).to_csv(out_dir / "skeleton_decks.csv", index=False)
    return out_dir


def save_batch_run(
    summary: dict,
    deck_df: pd.DataFrame,
    run_id: Optional[str] = None,
) -> Path:
    out_dir = run_output_dir(run_id)
    write_json(out_dir / "batch_summary.json", enrich_batch_summary(summary, deck_df))
    deck_df.to_csv(out_dir / "batch_decks.csv", index=False)
    return out_dir


def save_deck_run(payload: dict, predictions_df: pd.DataFrame, run_id: Optional[str] = None) -> Path:
    out_dir = run_output_dir(run_id)
    deck_id = payload.get("deck_id", "unknown")
    deck_name = (payload.get("deck_name") or "deck").replace(" ", "_")
    write_json(out_dir / f"deck_{deck_id}_{deck_name}.json", payload)
    predictions_df.to_csv(out_dir / f"deck_{deck_id}_predictions.csv", index=False)
    write_json(
        out_dir / "deck_summary.json",
        {
            "mode": "deck",
            "deck_id": payload.get("deck_id"),
            "deck_name": payload.get("deck_name"),
            "product": payload.get("product"),
            "stage": payload.get("stage"),
            "model_version": payload.get("model_version"),
            "metrics": payload.get("metrics"),
            "grade": payload.get("grade"),
            "skeleton_eval": payload.get("skeleton_eval"),
        },
    )
    return out_dir
