"""Tests for deck synergy helper used in spike attribution."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_URL
from db.schema import Card, CommanderDeck
from engine.deck_synergy import DeckSynergyContext, compute_synergy_fit, is_alt_commander_for_deck


@pytest.fixture
def session():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_varina_synergy_and_alt_for_eternal_might(session):
    deck = session.query(CommanderDeck).filter_by(deck_name="Eternal Might").one()
    varina = session.query(Card).filter_by(name="Varina, Lich Queen").one()
    ctx = DeckSynergyContext.from_deck(deck, session)
    syn = compute_synergy_fit(varina, ctx)
    assert syn >= 0.12
    assert is_alt_commander_for_deck(varina, ctx, syn)


def test_unfulfilled_desires_synergy_for_eternal_might(session):
    deck = session.query(CommanderDeck).filter_by(deck_name="Eternal Might").one()
    card = session.query(Card).filter_by(name="Unfulfilled Desires").one()
    ctx = DeckSynergyContext.from_deck(deck, session)
    syn = compute_synergy_fit(card, ctx)
    assert syn >= 0.15


def test_pest_control_low_synergy_for_eternal_might(session):
    deck = session.query(CommanderDeck).filter_by(deck_name="Eternal Might").one()
    card = session.query(Card).filter_by(name="Pest Control").one()
    ctx = DeckSynergyContext.from_deck(deck, session)
    syn = compute_synergy_fit(card, ctx)
    assert syn < 0.30
