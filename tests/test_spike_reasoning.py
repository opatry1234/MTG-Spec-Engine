"""Spike reasoning loader and combo validation logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.combo_validation import _norm_key, validate_combo_row
from backtester.spike_reasoning import (
    SpikeReasonRow,
    load_combo_discovered_rows,
    load_spike_reasoning,
)
from config import SPIKE_DATA_XLSX_PATH
from engine.combo_checker import ComboChecker, ComboLoopInfo


def test_norm_key_fuzzy_match():
    assert _norm_key("Temmet, Naktamun's Will") == _norm_key("Temmet Naktamuns Will")


def test_load_spike_reasoning_bible():
    if not SPIKE_DATA_XLSX_PATH.exists():
        return
    rows = load_spike_reasoning()
    assert len(rows) >= 100
    assert all(r.card_name for r in rows)
    assert any(r.spike_type for r in rows)


def test_load_combo_discovered_from_bible():
    if not SPIKE_DATA_XLSX_PATH.exists():
        return
    combo = load_combo_discovered_rows()
    assert len(combo) >= 50
    assert all(r.is_combo_discovered for r in combo)
    assert any("combo" in r.spike_type.lower() for r in combo if r.spike_type)


def test_varina_row_in_bible():
    if not SPIKE_DATA_XLSX_PATH.exists():
        return
    rows = load_spike_reasoning()
    varina = [r for r in rows if r.card_name.startswith("Varina")]
    assert varina
    curated = [r for r in varina if r.gain_pct and r.gain_pct >= 40.0]
    assert curated
    assert curated[0].report_date is not None


def test_validate_combo_row_mock():
    row = SpikeReasonRow(
        deck_name="Test",
        card_name="Village Bell-Ringer",
        spike_reason="Combo Discovered",
        combo_with=["Kiki-Jiki, Mirror Breaker"],
    )
    checker = ComboChecker(use_cache=False, fetch_live=False)

    def fake(_name, *, limit=40):
        return [
            {
                "uses": [
                    {"card": {"name": "Kiki-Jiki, Mirror Breaker"}},
                    {"card": {"name": "Village Bell-Ringer"}},
                ],
                "produces": [{"feature": {"name": "Infinite damage"}}],
            }
        ]

    checker.fetch_infinite_variants = fake  # type: ignore
    result = validate_combo_row(
        row,
        {"Kiki-Jiki, Mirror Breaker", "Village Bell-Ringer"},
        anchor_mode="decklist",
        checker=checker,
    )
    assert result.detected_loop is True
    assert "Kiki-Jiki, Mirror Breaker" in result.detected_partners
    assert result.match_sheet is True
