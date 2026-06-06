"""
Model 1: P(Card Included in Deck)

XGBoost classifier predicting probability a card is included.
"""

import numpy as np
import pandas as pd
import xgboost as xgb

from config import XGB_PARAMS
from features.builder import FEATURE_COLUMNS

INCLUSION_FEATURES = [
    c for c in FEATURE_COLUMNS
    if c not in ("p_reprint_heuristic", "scarcity_score")
]


def _model_feature_matrix(model, features_df: pd.DataFrame, default_features: list) -> pd.DataFrame:
    """Align prediction rows to the feature set the model was trained on."""
    feature_names = getattr(model, "feature_names_in_", None)
    if feature_names is None:
        feature_names = default_features
    return features_df.reindex(columns=list(feature_names), fill_value=0).fillna(0)


def train_inclusion_model(training_df: pd.DataFrame):
    """Train inclusion model on labeled training dataframe."""
    X = training_df[INCLUSION_FEATURES].fillna(0)
    y = training_df["label_included"]

    neg = max(int((y == 0).sum()), 1)
    pos = max(int((y == 1).sum()), 1)
    scale = min(neg / pos, 500)

    params = {k: v for k, v in XGB_PARAMS.items() if k != "scale_pos_weight"}
    params["scale_pos_weight"] = scale
    model = xgb.XGBClassifier(**params)
    model.fit(X, y, verbose=False)
    return model


def predict_inclusion(model, features_df: pd.DataFrame) -> np.ndarray:
    """Predict inclusion probabilities."""
    X = _model_feature_matrix(model, features_df, INCLUSION_FEATURES)
    return model.predict_proba(X)[:, 1]
