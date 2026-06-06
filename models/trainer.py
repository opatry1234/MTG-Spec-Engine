"""
Unified model training, evaluation, and saving pipeline.
"""

import pickle
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sklearn.metrics import roc_auc_score

from config import DATA_DIR, XGB_PARAMS
from db.schema import CommanderDeck
from features.builder import build_training_set
from models.inclusion import predict_inclusion, train_inclusion_model
from models.reprint import predict_reprint, train_reprint_model
from models.spec_spike import predict_spec_spike, train_spec_spike_model, _spike_label_column

MODELS_DIR = DATA_DIR / "models"
DEFAULT_HOLDOUT_CUTOFF = date(2023, 1, 1)


def get_training_decks(session):
    """Decks included in training."""
    return (
        session.query(CommanderDeck)
        .filter(
            CommanderDeck.include_in_training == True,
            CommanderDeck.decklist_revealed == True,
        )
        .all()
    )


def split_training_by_date(
    df: pd.DataFrame,
    cutoff: date = DEFAULT_HOLDOUT_CUTOFF,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-based split using deck_release_date on each row."""
    if "deck_release_date" not in df.columns:
        return df, df.iloc[0:0]

    train = df[df["deck_release_date"].notna() & (df["deck_release_date"] < cutoff)]
    holdout = df[df["deck_release_date"].notna() & (df["deck_release_date"] >= cutoff)]
    return train, holdout


def train_models(session, max_decks: int = None, training_df: pd.DataFrame = None) -> dict:
    """Train inclusion and reprint models."""
    df = training_df if training_df is not None else build_training_set(session, max_decks=max_decks)
    if df.empty:
        raise ValueError("No training data available")

    inclusion_model = train_inclusion_model(df)
    reprint_model = train_reprint_model(df)
    spec_spike_model = train_spec_spike_model(df)

    label_col = _spike_label_column(df)
    y_inc = df["label_included"]
    scale_inclusion = min(int((y_inc == 0).sum()) / max(int((y_inc == 1).sum()), 1), 500)

    version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    models = {
        "inclusion": inclusion_model,
        "reprint": reprint_model,
        "spec_spike": spec_spike_model,
        "version": version,
        "training_rows": len(df),
        "training_class_balance": {
            "inclusion_pos_rate": round(float(y_inc.mean()), 4),
            "inclusion_scale_pos_weight": scale_inclusion,
            "loose_spike_positives": int(df[label_col].sum()) if label_col in df.columns else 0,
            "golden_spike_positives": int(df["label_spec_spike"].sum())
            if "label_spec_spike" in df.columns
            else 0,
        },
    }
    return models


def evaluate_models(models: dict, test_df: pd.DataFrame) -> dict:
    """Evaluate model performance on holdout dataframe."""
    metrics = {}
    if "label_included" in test_df.columns:
        preds = predict_inclusion(models["inclusion"], test_df)
        try:
            metrics["inclusion_auc"] = roc_auc_score(test_df["label_included"], preds)
        except ValueError:
            metrics["inclusion_auc"] = 0.0

    included = test_df[test_df.get("label_included", 0) == 1]
    if not included.empty and "label_reprinted" in included.columns:
        preds = predict_reprint(models["reprint"], included)
        try:
            metrics["reprint_auc"] = roc_auc_score(included["label_reprinted"], preds)
        except ValueError:
            metrics["reprint_auc"] = 0.0

    label_col = _spike_label_column(test_df)
    if models.get("spec_spike") is not None and label_col in test_df.columns:
        positives = int(test_df[label_col].sum())
        if positives >= 2:
            preds = predict_spec_spike(models["spec_spike"], test_df)
            try:
                metrics["spec_spike_auc"] = roc_auc_score(test_df[label_col], preds)
            except ValueError:
                metrics["spec_spike_auc"] = 0.0

    return metrics


def evaluate_time_holdout(
    session,
    models: dict,
    training_df: pd.DataFrame,
    cutoff: date = DEFAULT_HOLDOUT_CUTOFF,
) -> dict:
    """Evaluate models on decks released on or after cutoff."""
    _, holdout = split_training_by_date(training_df, cutoff)
    if holdout.empty:
        return {"holdout_decks": 0, "holdout_rows": 0}
    metrics = evaluate_models(models, holdout)
    metrics["holdout_decks"] = int(holdout["deck_id"].nunique())
    metrics["holdout_rows"] = int(len(holdout))
    metrics["holdout_cutoff"] = cutoff.isoformat()
    return metrics


def save_models(models: dict, version: str):
    """Save trained models to data/models/."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("inclusion", "reprint", "spec_spike"):
        if models.get(name) is None:
            continue
        path = MODELS_DIR / f"{name}_v{version}.pkl"
        with open(path, "wb") as f:
            pickle.dump(models[name], f)

    meta_path = MODELS_DIR / f"meta_v{version}.pkl"
    meta = {
        "version": version,
        "training_rows": models.get("training_rows", 0),
        "xgb_params": XGB_PARAMS,
        "training_class_balance": models.get("training_class_balance"),
    }
    if models.get("training_summary"):
        meta["training_summary"] = models["training_summary"]
    with open(meta_path, "wb") as f:
        pickle.dump(meta, f)


def load_models(version: str = "latest") -> dict:
    """Load models from data/models/."""
    if version == "latest":
        inclusion_files = sorted(MODELS_DIR.glob("inclusion_v*.pkl"))
        if not inclusion_files:
            raise FileNotFoundError("No trained models found")
        version = inclusion_files[-1].stem.replace("inclusion_v", "")

    result = {"version": version}
    for name in ("inclusion", "reprint", "spec_spike"):
        path = MODELS_DIR / f"{name}_v{version}.pkl"
        if path.exists():
            with open(path, "rb") as f:
                result[name] = pickle.load(f)
    return result
