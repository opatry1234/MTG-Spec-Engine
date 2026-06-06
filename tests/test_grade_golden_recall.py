"""Golden-spec recall grading tests."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.grade import grade_spec_targets, score_to_letter


def test_score_to_letter():
    assert score_to_letter(1.0) == "A+"
    assert score_to_letter(0.5) == "C-"


def test_perfect_recall_when_both_golden_in_top_two():
    preds = pd.DataFrame(
        {
            "card_name": [
                "Unfulfilled Desires",
                "Varina, Lich Queen",
                "He Who Hungers",
            ],
            "opportunity_score": [27.0, 24.0, 20.0],
        }
    )
    golden = [
        {
            "card_name": "Unfulfilled Desires",
            "precon_attributed": True,
            "spike_pct": 0.5,
        },
        {
            "card_name": "Varina, Lich Queen",
            "precon_attributed": True,
            "spike_pct": 0.4,
        },
    ]
    grade = grade_spec_targets(
        None,
        preds,
        actual_deck={"Temmet, Naktamun's Will", "Sol Ring"},
        release_date=date(2025, 2, 14),
        top_n=20,
        golden_spikes=golden,
        fetch_prices=False,
    )
    assert grade["golden_spec_count"] == 2
    assert grade["golden_specs_found"] == 2
    # Rank-weighted: rank 1 → 1.0, rank 2 → 0.9 → mean 0.95
    assert grade["score"] == 0.95
    assert grade["letter"] == "A"
    assert grade["evaluation_top_n"] == 10


def test_partial_recall_one_of_two():
    preds = pd.DataFrame(
        {
            "card_name": ["Varina, Lich Queen", "Random Bulk", "Another Bulk"],
            "opportunity_score": [20.0, 19.0, 18.0],
        }
    )
    golden = [
        {"card_name": "Unfulfilled Desires", "precon_attributed": True},
        {"card_name": "Varina, Lich Queen", "precon_attributed": True},
    ]
    grade = grade_spec_targets(
        None,
        preds,
        actual_deck=set(),
        release_date=date(2025, 2, 14),
        top_n=10,
        golden_spikes=golden,
        fetch_prices=False,
    )
    assert grade["golden_specs_found"] == 1
    assert grade["score"] == 0.5
