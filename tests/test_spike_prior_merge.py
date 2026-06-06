"""Historical spike prior merge logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.historical_spike_prior import merge_historical_spike_scores


def test_ml_dampened_without_prior():
    merged = merge_historical_spike_scores(0.0, 0.99, 0.28)
    assert merged < 0.2


def test_prior_dominates_when_proven():
    merged = merge_historical_spike_scores(0.56, 0.99, 0.18)
    assert 0.56 <= merged <= 0.75
