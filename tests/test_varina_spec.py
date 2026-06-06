"""Integration test: Varina surfaces as spec target for Eternal Might."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_URL, SPIKE_CSV_PATH
from db.schema import CommanderDeck
from engine.pipeline import predict_product
from engine.scoring_context import build_scoring_context, to_prediction_input
from backtester.spike_csv import clear_spike_cache
from engine.historical_spike_prior import clear_historical_spike_prior_cache


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
def test_varina_in_eternal_might_spec_targets(session):
    deck = session.query(CommanderDeck).filter_by(deck_name="Eternal Might").one()
    ctx = build_scoring_context(deck, session)
    pred = to_prediction_input(ctx)

    result = predict_product(session, pred, top_n=20, scorer="heuristic")
    names = set(result.cards["card_name"].tolist())

    assert "Varina, Lich Queen" in names
    varina_row = result.cards[result.cards["card_name"] == "Varina, Lich Queen"].iloc[0]
    assert varina_row["is_alternate_commander"] == True
    assert varina_row["historical_spike_score"] >= 0.5

    rank = list(result.cards["card_name"]).index("Varina, Lich Queen") + 1
    assert rank <= 10, f"Varina ranked #{rank}, expected top 10"
