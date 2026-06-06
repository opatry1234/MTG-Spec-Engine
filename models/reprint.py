"""
Model 2: P(Card Reprinted in Deck)

XGBoost classifier predicting probability a card is reprinted (not new).
"""

import numpy as np
import pandas as pd
import xgboost as xgb

from config import XGB_PARAMS
from features.builder import FEATURE_COLUMNS

REPRINT_FEATURES = FEATURE_COLUMNS


def _model_feature_matrix(model, features_df: pd.DataFrame, default_features: list) -> pd.DataFrame:
    """Align prediction rows to the feature set the model was trained on."""
    feature_names = getattr(model, "feature_names_in_", None)
    if feature_names is None:
        feature_names = default_features
    return features_df.reindex(columns=list(feature_names), fill_value=0).fillna(0)


def train_reprint_model(training_df: pd.DataFrame):
    """Train reprint model on included cards only."""
    included = training_df[training_df["label_included"] == 1].copy()
    if included.empty:
        included = training_df.copy()

    X = included[REPRINT_FEATURES].fillna(0)
    y = included["label_reprinted"]

    params = {**XGB_PARAMS, "scale_pos_weight": 1}
    model = xgb.XGBClassifier(**params)
    model.fit(X, y, verbose=False)
    return model


def predict_reprint(model, features_df: pd.DataFrame) -> np.ndarray:
    """Predict reprint probabilities."""
    X = _model_feature_matrix(model, features_df, REPRINT_FEATURES)
    return model.predict_proba(X)[:, 1]
