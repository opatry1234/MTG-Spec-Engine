"""Precon deck matching uses structured columns, not cause substrings."""

from backtester.spike_precon_catalog import is_junk_card_name, resolve_precon_set_code
from backtester.spike_reasoning import SpikeReasonRow, _row_matches_deck


def test_soc_not_matched_via_cause_substring():
    row = SpikeReasonRow(
        deck_name="",
        card_name="Lightning Bolt",
        spike_reason="Associated with SOC energy mechanics in article prose",
        precon_deck_name="",
        precon_set_code="",
    )
    assert not _row_matches_deck(row, "Lorehold Spirit", "SOC", "")


def test_ecc_matched_via_precon_columns():
    row = SpikeReasonRow(
        deck_name="Blight Curse",
        card_name="Atomize",
        spike_reason="Lorwyn Eclipsed Commander precon 'Blight Curse' …",
        precon_deck_name="Blight Curse",
        precon_set_code="ECC",
    )
    assert _row_matches_deck(row, "Blight Curse", "ECC", "High Perfect Morcant")


def test_junk_card_filtered():
    assert is_junk_card_name("Other interesting winners")


def test_resolve_blight_curse_code():
    code = resolve_precon_set_code("Blight Curse", cause="precon 'Blight Curse'")
    assert code == "ECC"
