"""Tests for precon spike attribution."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.spike_attribution import (
    announcement_window,
    is_basic_land_card,
    is_precon_attributed_spike,
    is_shelf_attributed_spike,
    is_unrelated_commander_set,
)


def test_reveal_spike_window():
    start, end = announcement_window(date(2025, 2, 14))
    assert start == date(2025, 2, 14)
    assert end == date(2025, 6, 14)


def test_pre_reveal_spike_not_attributed():
    assert not is_precon_attributed_spike(
        date(2025, 2, 10),
        "Double Masters 2022",
        date(2025, 2, 14),
        synergy_fit=0.25,
        is_alt_commander=True,
    )


def test_varina_feb19_is_attributed():
    assert is_precon_attributed_spike(
        date(2025, 2, 19),
        "Double Masters 2022",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        commander_spike_set="Commander: Aetherdrift",
        synergy_fit=0.25,
        is_alt_commander=True,
    )


def test_pest_control_feb19_not_attributed_without_synergy():
    assert not is_precon_attributed_spike(
        date(2025, 2, 19),
        "Murders at Karlov Manor",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        commander_spike_set="Commander: Aetherdrift",
        synergy_fit=0.05,
        is_alt_commander=False,
    )


def test_unrelated_commander_set_rejected():
    assert is_unrelated_commander_set(
        "Commander: Tarkir: Dragonstorm",
        "Commander: Aetherdrift",
    )
    assert not is_unrelated_commander_set(
        "Double Masters 2022",
        "Commander: Aetherdrift",
    )


def test_in_deck_commander_product_not_attributed():
    assert not is_precon_attributed_spike(
        date(2025, 5, 13),
        "Commander: Aetherdrift",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        commander_spike_set="Commander: Aetherdrift",
    )


def test_forest_shelf_spike_rejected():
    assert is_basic_land_card("Forest")
    assert not is_shelf_attributed_spike(
        date(2025, 5, 13),
        date(2025, 5, 13),
        card_name="Forest",
        deck_colors=["W", "U", "B"],
        card_color_identity=[],
        synergy_fit=0.2,
    )


def test_mass_batch_shelf_day_rejected_for_precon_attribution():
    """Mass-report days still fail the stricter profile gate in is_precon_attributed_spike."""
    from backtester.spike_attribution import is_mass_spike_report_date

    report = date(2025, 5, 13)
    if not is_mass_spike_report_date(report):
        pytest.skip("mass batch date not loaded in spike index")
    assert not is_precon_attributed_spike(
        report,
        "Foundations Jumpstart",
        date(2025, 2, 14),
        card_name="Bloodflow Connoisseur",
        deck_colors=["W", "U", "B"],
        card_color_identity=["B"],
        synergy_fit=0.25,
    )


def test_green_card_on_wub_shelf_spike_rejected():
    assert not is_precon_attributed_spike(
        date(2025, 5, 13),
        "Foundations Jumpstart",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        commander_spike_set="Commander: Aetherdrift",
        card_name="Enlisted Wurm",
        deck_colors=["W", "U", "B"],
        card_color_identity=["G"],
        synergy_fit=0.08,
    )
