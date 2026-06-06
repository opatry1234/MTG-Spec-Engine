"""Batch run export includes letter grades."""

import pandas as pd

from backtester.export import enrich_batch_summary


def test_enrich_batch_summary_adds_grade_aggregates():
    df = pd.DataFrame(
        [
            {
                "deck_id": 1,
                "deck_name": "Deck A",
                "letter_grade": "A",
                "grade_score": 0.9,
                "golden_specs_found": 2,
                "golden_spec_count": 2,
            },
            {
                "deck_id": 2,
                "deck_name": "Deck B",
                "letter_grade": "C-",
                "grade_score": 0.5,
                "golden_specs_found": 1,
                "golden_spec_count": 3,
            },
        ]
    )
    summary = enrich_batch_summary({"mode": "batch", "deck_count": 2}, df)
    assert summary["graded_deck_count"] == 2
    assert summary["mean_grade_score"] == 0.7
    assert summary["grade_distribution"]["A"] == 1
    assert summary["grade_distribution"]["C-"] == 1
    assert len(summary["deck_grades"]) == 2
    assert summary["deck_grades"][0]["letter_grade"] == "A"
