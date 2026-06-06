"""Tests for spec target grading and price spike detection."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.grade import score_to_letter
from backtester.spike_check import detect_price_spike


def test_score_to_letter():
    assert score_to_letter(1.0) == "A+"
    assert score_to_letter(0.95) == "A"
    assert score_to_letter(0.5) == "C-"
    assert score_to_letter(0.0) == "F"


def test_detect_price_spike_positive():
    release = date(2025, 4, 1)
    series = {}
    for day in range(-20, -8):
        d = date.fromordinal(release.toordinal() + day)
        series[d] = 2.0
    for day in range(-5, 30):
        d = date.fromordinal(release.toordinal() + day)
        series[d] = 4.0 if day > 10 else 2.5

    result = detect_price_spike(series, release)
    assert result["had_spike"] is True
    assert result["peak_price"] >= 4.0


def test_detect_price_spike_flat():
    release = date(2025, 4, 1)
    series = {date.fromordinal(release.toordinal() + i): 1.0 for i in range(-25, 40)}
    result = detect_price_spike(series, release)
    assert result["had_spike"] is False
