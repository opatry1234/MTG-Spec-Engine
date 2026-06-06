"""
Model 3: P(Omission spec spike)

Predicts whether a card was an omission spike for a deck — trained on loose
labels (eligible + spiked + omitted); golden strict labels are for grading only.
"""

import numpy as np
import pandas as pd
import xgboost as xgb

from config import XGB_PARAMS
from models.inclusion import INCLUSION_FEATURES, _model_feature_matrix


def _spike_label_column(training_df: pd.DataFrame) -> str:
    if "label_spike_loose" in training_df.columns:
        return "label_spike_loose"
    return "label_spec_spike"


def train_spec_spike_model(training_df: pd.DataFrame):
    """Train on loose omission-spike labels — heavily imbalanced positive class."""
    label_col = _spike_label_column(training_df)
    if label_col not in training_df.columns:
        raise ValueError(f"{label_col} column required")

    positives = int(training_df[label_col].sum())
    if positives < 2:
        return None

    X = training_df[INCLUSION_FEATURES].fillna(0)
    y = training_df[label_col]

    neg = max(int((y == 0).sum()), 1)
    scale = min(neg / positives, 500)

    params = {**XGB_PARAMS, "scale_pos_weight": scale}
    model = xgb.XGBClassifier(**params)
    model.fit(X, y, verbose=False)
    return model


def predict_spec_spike(model, features_df: pd.DataFrame) -> np.ndarray:
    if model is None:
        return np.zeros(len(features_df))
    X = _model_feature_matrix(model, features_df, INCLUSION_FEATURES)
    return model.predict_proba(X)[:, 1]
