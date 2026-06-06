"""
Human-readable training run summary for the Settings UI.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from config import DATA_DIR
from features.builder import FEATURE_COLUMNS
from models.inclusion import INCLUSION_FEATURES
from models.reprint import REPRINT_FEATURES

FEATURE_LABELS = {
    "color_identity_match": "Matches deck colors",
    "creature_type_overlap": "Creature type overlap with theme",
    "keyword_overlap_score": "Keyword overlap with commander",
    "token_score": "Token synergy",
    "graveyard_score": "Graveyard synergy",
    "oracle_text_overlap": "Oracle text overlap with commander",
    "mechanical_pool_size": "Mechanical pool size (rarer = higher)",
    "tfidf_similarity": "Announcement text similarity",
    "visible_inventory_score": "Visible inventory (low = scarcer)",
    "seller_count_score": "Seller count (few = scarcer)",
    "num_precon_printings": "Past precon printings",
    "last_reprint_days_ago": "Days since last reprint",
    "is_reserved": "Reserved list",
    "rarity_score": "Rarity",
    "edhrec_rank": "EDHREC popularity rank",
    "edhrec_inclusion_pct": "EDHREC inclusion rate",
    "historical_inclusion_rate": "Past precon inclusion rate",
    "historical_omission_spike_score": "Past omission spike signal",
    "scarcity_score": "Supply scarcity",
    "spec_supply_score": "Spec supply shock (vintage / low print run)",
}


def _top_features(model, feature_names: list[str], limit: int = 8) -> list[dict]:
    if model is None:
        return []
    importances = getattr(model, "feature_importances_", None)
    if importances is None:
        return []

    feature_names_in = getattr(model, "feature_names_in_", None)
    names = list(feature_names_in) if feature_names_in is not None else list(feature_names)
    if len(importances) != len(names):
        return []

    ranked = sorted(zip(names, importances), key=lambda pair: pair[1], reverse=True)[:limit]
    total = sum(importances) or 1.0
    return [
        {
            "feature": name,
            "label": FEATURE_LABELS.get(name, name.replace("_", " ").title()),
            "importance": round(float(weight), 4),
            "share_pct": round(100 * float(weight) / total, 1),
        }
        for name, weight in ranked
        if weight > 0
    ]


def _build_plain_english(
    *,
    unique_decks: int,
    training_rows: int,
    pos_rate: float,
    incl_auc: float | None,
    rep_auc: float | None,
    spec_auc: float | None,
    spec_spike_positives: int,
    decks_with_golden_spikes: int,
    top_inclusion_label: str | None,
    spike_included: float | None,
    spike_omitted: float | None,
    has_spec_model: bool,
) -> str:
    lines = [
        f"We studied **{unique_decks} past precon decklists** ({training_rows:,} card–deck examples) "
        f"to learn what Wizards usually puts in a deck versus what they leave out.",
        "",
        "**Deck-building models (what Wizards includes):**",
    ]

    if incl_auc is not None:
        quality = "very good" if incl_auc >= 0.9 else "decent" if incl_auc >= 0.75 else "still learning"
        lines.append(
            f"- **Inclusion** ({incl_auc:.0%} accurate): {quality} at guessing whether a card "
            f"ends up *in* the precon. Biggest clue: **{top_inclusion_label or 'past precon history'}**."
        )
    if rep_auc is not None:
        lines.append(
            f"- **Reprint** ({rep_auc:.0%} accurate): good at guessing whether an included card "
            f"is a reprint vs new to that product."
        )

    lines.extend(["", "**Spec spike model (cards that spiked because they were left out):**"])

    if has_spec_model:
        if spec_auc is not None:
            lines.append(
                f"- Trained a dedicated **omission-spike model** ({spec_auc:.0%} AUC on training data) "
                f"using **loose** spike labels. Backtest grades still use **strict golden** attribution."
            )
        else:
            lines.append(
                f"- Trained an omission-spike model on **{spec_spike_positives} golden targets** "
                f"across **{decks_with_golden_spikes} decks** (e.g. Varina & Unfulfilled Desires for Eternal Might)."
            )
    else:
        lines.append(
            "- Not enough golden spike examples yet to train a spike model (need at least 2 labeled cards). "
            "Ranking still uses theme fit + scarcity heuristics."
        )

    if spec_spike_positives > 0:
        lines.append(
            f"- Only **{spec_spike_positives}** training rows out of {training_rows:,} are true "
            f"\"buy-ahead profit\" targets — that's intentional. Most cards never spike from a precon omission."
        )
    else:
        lines.append(
            "- No golden spike labels in this training set yet. Re-train after spike CSV + attribution "
            "data covers more decks."
        )

    if spike_included is not None and spike_omitted is not None:
        if spike_omitted > spike_included + 0.05:
            lines.append(
                "- Cards left out of decks show a stronger historical spike signal than included cards — "
                "good for finding spec targets."
            )
        else:
            lines.append(
                "- Historical spike scores are still flat on average — the new spike model (when trained) "
                "is how we learn profit targets directly."
            )

    lines.extend([
        "",
        "**Bottom line:** The model is strong at copying Wizards' deck-building habits. "
        "Profit spec ranking depends on the **golden spike list** per precon — re-train after "
        "adding decks or tightening spike attribution.",
    ])
    return "\n".join(lines)


def ensure_plain_english(summary: dict[str, Any], *, has_spec_model: bool | None = None) -> dict[str, Any]:
    """Backfill plain_english for summaries saved before that field existed."""
    if not summary or summary.get("plain_english"):
        return summary

    metrics = summary.get("metrics") or {}
    top_inclusion = summary.get("inclusion_top_features") or []
    means = summary.get("feature_means") or {}
    included = means.get("included") or {}
    omitted = means.get("omitted") or {}

    if has_spec_model is None:
        version = summary.get("version")
        has_spec_model = bool(
            summary.get("spec_spike_top_features")
            or metrics.get("spec_spike_auc") is not None
            or (
                version
                and (DATA_DIR / "models" / f"spec_spike_v{version}.pkl").exists()
            )
        )

    summary = dict(summary)
    summary["plain_english"] = _build_plain_english(
        unique_decks=int(summary.get("training_decks") or 0),
        training_rows=int(summary.get("training_rows") or 0),
        pos_rate=float(summary.get("positive_rate") or 0),
        incl_auc=metrics.get("inclusion_auc"),
        rep_auc=metrics.get("reprint_auc"),
        spec_auc=metrics.get("spec_spike_auc"),
        spec_spike_positives=int(summary.get("spec_spike_positives") or 0),
        decks_with_golden_spikes=int(summary.get("decks_with_golden_spikes") or 0),
        top_inclusion_label=top_inclusion[0]["label"] if top_inclusion else None,
        spike_included=included.get("historical_omission_spike_score"),
        spike_omitted=omitted.get("historical_omission_spike_score"),
        has_spec_model=has_spec_model,
    )
    return summary


def build_training_summary(
    df: pd.DataFrame,
    models: dict,
    metrics: dict,
    session: Session,
    holdout_metrics: dict | None = None,
) -> dict[str, Any]:
    """Summarize what the models learned from this training run."""
    from db.schema import CommanderDeck
    from engine.historical_spike_prior import get_historical_spike_prior
    from models.trainer import get_training_decks

    decks = get_training_decks(session)
    spike_prior = get_historical_spike_prior(session)
    decks_with_golden = len(spike_prior.deck_golden_spikes)

    deck_count = len(decks)
    deck_ids = df["deck_id"] if "deck_id" in df.columns else pd.Series(dtype=int)
    unique_decks = int(deck_ids.nunique()) if not deck_ids.empty else deck_count

    included = df[df["label_included"] == 1] if "label_included" in df.columns else df.iloc[0:0]
    omitted = df[df["label_included"] == 0] if "label_included" in df.columns else df.iloc[0:0]

    def _mean(col: str, frame: pd.DataFrame) -> float | None:
        if col not in frame.columns or frame.empty:
            return None
        return round(float(frame[col].mean()), 4)

    spike_included = _mean("historical_omission_spike_score", included)
    spike_omitted = _mean("historical_omission_spike_score", omitted)
    spec_spike_positives = (
        int(df["label_spike_loose"].sum())
        if "label_spike_loose" in df.columns
        else int(df["label_spec_spike"].sum()) if "label_spec_spike" in df.columns else 0
    )
    golden_positives = int(df["label_spec_spike"].sum()) if "label_spec_spike" in df.columns else 0
    spec_spike_rate = round(spec_spike_positives / len(df), 4) if len(df) and spec_spike_positives else 0.0

    reprint_rate = None
    if not included.empty and "label_reprinted" in included.columns:
        reprint_rate = round(float(included["label_reprinted"].mean()), 4)

    product_counts: dict[str, int] = {}
    if "deck_id" in df.columns and decks:
        deck_by_id = {d.id: d for d in decks}
        products = [
            deck_by_id[did].product or "?"
            for did in deck_ids.dropna().astype(int).unique()
            if did in deck_by_id
        ]
        product_counts = dict(Counter(products))

    insights: list[str] = []
    pos_rate = float(df["label_included"].mean()) if "label_included" in df.columns else 0.0
    insights.append(
        f"Trained on {unique_decks} revealed decklists ({len(df):,} card×deck rows, "
        f"{pos_rate:.1%} positive in-deck labels)."
    )

    incl_auc = metrics.get("inclusion_auc")
    rep_auc = metrics.get("reprint_auc")
    spec_auc = metrics.get("spec_spike_auc")
    if incl_auc is not None:
        insights.append(
            f"Inclusion model AUC {incl_auc:.3f} — "
            + (
                "strong fit on historical decklists."
                if incl_auc >= 0.85
                else "moderate fit; more deck data or feature tuning may help."
            )
        )
    if rep_auc is not None:
        insights.append(
            f"Reprint model AUC {rep_auc:.3f} on cards Wizards actually included."
        )
    if spec_auc is not None:
        insights.append(
            f"Spec spike model AUC {spec_auc:.3f} on loose omission-spike labels "
            f"({spec_spike_positives} training positives; {golden_positives} strict golden)."
        )
    elif models.get("spec_spike") is not None and spec_spike_positives >= 2:
        insights.append(
            f"Spec spike model trained on {spec_spike_positives} loose targets "
            f"({golden_positives} strict golden across {decks_with_golden} decks)."
        )

    if spike_included is not None and spike_omitted is not None:
        if spike_omitted > spike_included + 0.05:
            insights.append(
                "Omission spike signal is higher for cards left out of decks than for "
                "included cards — the model can use this to separate spec targets from "
                "likely inclusions."
            )
        elif spike_included >= spike_omitted:
            insights.append(
                "Historical omission spike scores are similar for included vs omitted cards "
                "in training — the dedicated spec spike model carries most of the profit signal."
            )

    if spec_spike_positives > 0:
        insights.append(
            f"{spec_spike_rate:.2%} of rows are loose omission-spike training targets "
            f"({spec_spike_positives} card×deck labels; {golden_positives} strict golden for grading)."
        )

    if holdout_metrics and holdout_metrics.get("holdout_rows", 0) > 0:
        h_incl = holdout_metrics.get("inclusion_auc")
        h_spec = holdout_metrics.get("spec_spike_auc")
        cutoff = holdout_metrics.get("holdout_cutoff", "?")
        if h_incl is not None:
            insights.append(
                f"Time holdout (release ≥ {cutoff}): inclusion AUC {h_incl:.3f} "
                f"on {holdout_metrics.get('holdout_decks', 0)} decks."
            )
        if h_spec is not None:
            insights.append(f"Time holdout spec spike AUC {h_spec:.3f}.")

    top_inclusion = _top_features(models.get("inclusion"), INCLUSION_FEATURES)
    top_reprint = _top_features(models.get("reprint"), REPRINT_FEATURES)
    top_spec = _top_features(models.get("spec_spike"), INCLUSION_FEATURES)

    top_inclusion_label = top_inclusion[0]["label"] if top_inclusion else None
    if top_inclusion_label:
        insights.append(f"Strongest inclusion predictor: **{top_inclusion_label}**.")

    plain_english = _build_plain_english(
        unique_decks=unique_decks,
        training_rows=len(df),
        pos_rate=pos_rate,
        incl_auc=incl_auc,
        rep_auc=rep_auc,
        spec_auc=spec_auc,
        spec_spike_positives=spec_spike_positives,
        decks_with_golden_spikes=decks_with_golden,
        top_inclusion_label=top_inclusion_label,
        spike_included=spike_included,
        spike_omitted=spike_omitted,
        has_spec_model=models.get("spec_spike") is not None,
    )

    return {
        "version": models.get("version"),
        "training_rows": int(len(df)),
        "training_decks": unique_decks,
        "deck_count_db": deck_count,
        "positive_rate": round(pos_rate, 4),
        "reprint_rate_included": reprint_rate,
        "spec_spike_label_rate": spec_spike_rate if spec_spike_positives else None,
        "spec_spike_positives": spec_spike_positives,
        "golden_spike_positives": golden_positives,
        "decks_with_golden_spikes": decks_with_golden,
        "metrics": metrics,
        "holdout_metrics": holdout_metrics,
        "inclusion_top_features": top_inclusion,
        "reprint_top_features": top_reprint,
        "spec_spike_top_features": top_spec,
        "feature_means": {
            "included": {
                "historical_omission_spike_score": spike_included,
                "tfidf_similarity": _mean("tfidf_similarity", included),
                "historical_inclusion_rate": _mean("historical_inclusion_rate", included),
                "spec_supply_score": _mean("spec_supply_score", included),
            },
            "omitted": {
                "historical_omission_spike_score": spike_omitted,
                "tfidf_similarity": _mean("tfidf_similarity", omitted),
                "historical_inclusion_rate": _mean("historical_inclusion_rate", omitted),
                "spec_supply_score": _mean("spec_supply_score", omitted),
            },
        },
        "product_counts": product_counts,
        "insights": insights,
        "plain_english": plain_english,
        "feature_columns": list(FEATURE_COLUMNS),
    }
