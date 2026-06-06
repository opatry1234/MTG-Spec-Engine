"""Integration test: golden benchmarks exclude shelf batch noise for Eternal Might."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_URL, SPIKE_CSV_PATH
from backtester.spike_csv import clear_spike_cache, find_omission_spike_benchmarks
from db.schema import Card, CommanderDeck, DeckCard
from engine.deck_synergy import DeckSynergyContext
from engine.historical_spike_prior import clear_historical_spike_prior_cache
from engine.spec_eligibility import build_earliest_printing_map


@pytest.fixture
def session():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
    clear_spike_cache()
    clear_historical_spike_prior_cache()


@pytest.mark.skipif(not SPIKE_CSV_PATH.exists(), reason="spike CSV required")
def test_eternal_might_golden_is_varina_and_unfulfilled(session):
    deck = session.query(CommanderDeck).filter_by(deck_name="Eternal Might").one()
    actual = {
        row.card_name
        for row in session.query(DeckCard).filter_by(deck_id=deck.id).all()
    }
    earliest_map = build_earliest_printing_map(session)
    color_map = {
        (c.name or "").lower(): list(c.color_identity or [])
        for c in session.query(Card).all()
        if c.name
    }
    cards_by_name = {
        (c.name or "").lower(): c for c in session.query(Card).all() if c.name
    }

    golden = find_omission_spike_benchmarks(
        actual,
        deck.release_date,
        precon_release_date=deck.precon_release_date,
        product_code=deck.product,
        earliest_printing_map=earliest_map,
        deck_colors=list(deck.colors or []),
        card_color_map=color_map,
        deck_synergy_ctx=DeckSynergyContext.from_deck(deck),
        cards_by_name=cards_by_name,
        limit=15,
    )
    names = [row["card_name"] for row in golden]

    assert names == ["Varina, Lich Queen", "Unfulfilled Desires"]

    varina = golden[0]
    assert varina["attribution"] == "announcement"
    assert varina["is_alternate_commander"] is True

    desires = golden[1]
    assert desires["attribution"] == "announcement"
    assert desires["synergy_fit"] >= 0.15
