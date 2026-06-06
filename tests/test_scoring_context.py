"""Tests for Phase-3 scoring context."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_URL
from engine.scoring_context import build_scoring_context, to_prediction_input
from engine.heuristic_scorer import get_candidates


@pytest.fixture
def session():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_scoring_context_loads_decklist(session):
    from db.schema import CommanderDeck

    deck = session.query(CommanderDeck).filter(CommanderDeck.decklist_revealed == True).first()
    if deck is None:
        pytest.skip("no decks in database")
    ctx = build_scoring_context(deck, session)
    assert len(ctx.known_inclusions) > 0
    assert ctx.anchor_date is not None


def test_candidates_exclude_decklist_cards(session):
    from db.schema import CommanderDeck

    deck = session.query(CommanderDeck).filter(CommanderDeck.decklist_revealed == True).first()
    if deck is None:
        pytest.skip("no decks in database")
    ctx = build_scoring_context(deck, session)
    pred = to_prediction_input(ctx)
    candidates = get_candidates(session, pred)
    names = {c.name for c in candidates}
    assert not names & ctx.known_inclusions
