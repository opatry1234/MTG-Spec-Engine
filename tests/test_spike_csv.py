"""Tests for historical spike CSV matching."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.spike_csv import (
    clear_spike_cache,
    collect_spike_candidates,
    find_omission_spike_benchmarks,
    find_spike_near_release,
    get_spike_index,
    is_valid_spike_row,
    load_spike_csv,
    meets_spike_threshold,
    normalize_oracle_name,
    normalize_printing_key,
    oracle_name_aliases,
    record_matches_printing,
    spike_csv_stats,
    spike_window,
    SpikeRecord,
)
from config import SPIKE_CSV_PATH


def test_normalize_oracle_name():
    assert normalize_oracle_name("Faithless Looting (Retro Frame)") == "Faithless Looting"
    assert normalize_oracle_name('"Ob Nixilis, Captive Kingpin"') == "Ob Nixilis, Captive Kingpin"


def test_oracle_name_aliases_secret_lair():
    aliases = oracle_name_aliases("Linda, Kandarian Queen - Varina, Lich Queen")
    assert "Varina, Lich Queen" in aliases
    assert "Linda, Kandarian Queen" in aliases


def test_is_valid_spike_row():
    assert not is_valid_spike_row(0.0, 0.0)
    assert is_valid_spike_row(0.26, 1.0)


def test_varina_spike_eternal_might_window():
    if not SPIKE_CSV_PATH.exists():
        return
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from config import DATABASE_URL
    from db.schema import Card, CommanderDeck
    from engine.deck_synergy import DeckSynergyContext

    clear_spike_cache()
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        deck = session.query(CommanderDeck).filter_by(deck_name="Eternal Might").one()
        cards_by_name = {
            (c.name or "").lower(): c for c in session.query(Card).all() if c.name
        }
        result = find_spike_near_release(
            "Varina, Lich Queen",
            date(2025, 2, 14),
            precon_release_date=date(2025, 5, 13),
            product_code="DFT",
            deck_colors=list(deck.colors or []),
            card_color_map={
                (c.name or "").lower(): list(c.color_identity or [])
                for c in session.query(Card).all()
                if c.name
            },
            deck_synergy_ctx=DeckSynergyContext.from_deck(deck, session),
            cards_by_name=cards_by_name,
        )
    finally:
        session.close()

    assert result["had_spike"] is True
    assert result["precon_attributed"] is True
    assert result.get("attribution") == "post_reveal"
    assert result.get("report_date") == "2025-02-19"
    assert result.get("spike_pct", 0) >= 40.0
    if result.get("printing_key"):
        assert result["printing_key"] in {"2X2/549", "6ED/278"}


def test_printing_exact_match_prefers_identified_printing():
    window = date(2024, 6, 1)
    records = [
        SpikeRecord(
            oracle_name="Lightning Bolt",
            product_name="Lightning Bolt",
            set_name="Modern Horizons 2",
            report_date=window,
            change_pct=0.50,
            change_usd=1.0,
            initial_price=1.0,
            final_price=2.0,
            set_code="MH2",
            collector_number="187",
        ),
        SpikeRecord(
            oracle_name="Lightning Bolt",
            product_name="Lightning Bolt",
            set_name="Limited Edition Alpha",
            report_date=window,
            change_pct=0.90,
            change_usd=900.0,
            initial_price=1000.0,
            final_price=1900.0,
            set_code="LEA",
            collector_number="161",
        ),
    ]
    index = {"lightning bolt": records}

    mh2_only = collect_spike_candidates(
        index, "Lightning Bolt", set_code="MH2", collector_number="187"
    )
    assert len(mh2_only) == 1
    assert mh2_only[0].printing_key() == "MH2/187"

    all_bolts = collect_spike_candidates(index, "Lightning Bolt")
    assert len(all_bolts) == 2
    assert record_matches_printing(all_bolts[0], set_code="MH2", collector_number="187")
    assert normalize_printing_key("mh2", "187") == "MH2/187"


def test_get_spike_printing_index_from_bible():
    if not SPIKE_CSV_PATH.exists():
        return
    clear_spike_cache()
    stats = spike_csv_stats()
    assert stats.get("unique_printings", 0) > 500


def test_find_spike_exact_printing():
    if not SPIKE_CSV_PATH.exists():
        return
    clear_spike_cache()
    result = find_spike_near_release(
        "Varina, Lich Queen",
        date(2024, 2, 2),
        set_code="2X2",
        collector_number="549",
    )
    if result.get("had_spike"):
        assert result.get("printing_key") == "2X2/549"


def test_zero_change_spike_not_counted():
    if not SPIKE_CSV_PATH.exists():
        return
    clear_spike_cache()
    result = find_spike_near_release(
        "Necromancer's Covenant",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        product_code="DFT",
    )
    assert result["had_spike"] is False


def test_unattributed_may_batch_not_good_spike():
    if not SPIKE_CSV_PATH.exists():
        return
    clear_spike_cache()
    result = find_spike_near_release(
        "Battle at the Helvault",
        date(2025, 2, 14),
        precon_release_date=date(2025, 5, 13),
        product_code="DFT",
    )
    assert result.get("had_spike") is False or result.get("precon_attributed") is False


def test_spike_window_anchored_to_reveal():
    start, end = spike_window(date(2025, 2, 14), date(2025, 5, 13))
    assert start == date(2025, 2, 14)
    assert end == date(2025, 6, 14)


def test_spike_csv_loads():
    clear_spike_cache()
    stats = spike_csv_stats()
    assert stats["loaded"] is True
    assert stats["rows"] > 1000
    assert stats["unique_cards"] > 500


def test_find_spike_outside_window():
    if not SPIKE_CSV_PATH.exists():
        return
    result = find_spike_near_release("Faithless Looting", date(2020, 1, 1))
    assert result["had_spike"] is False
    assert result["price_source"] == "spike_csv"
