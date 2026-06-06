"""Tests for reveal-anchored spike windows."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.grade import _filter_golden_by_reveal
from backtester.spike_csv import spike_window


def test_spike_window_anchored_to_reveal():
    start, end = spike_window(date(2025, 2, 14))
    assert start == date(2025, 2, 14)
    assert end == date(2025, 6, 14)


def test_golden_filtered_before_reveal():
    rows = [
        {"card_name": "A", "report_date": "2025-02-10"},
        {"card_name": "B", "report_date": "2025-02-20"},
    ]
    kept = _filter_golden_by_reveal(rows, date(2025, 2, 14))
    assert len(kept) == 1
    assert kept[0]["card_name"] == "B"
