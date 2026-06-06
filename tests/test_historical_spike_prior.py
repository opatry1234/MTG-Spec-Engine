"""Tests for historical spike prior."""

import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_URL, SPIKE_CSV_PATH
from engine.historical_spike_prior import HistoricalSpikePrior


@pytest.fixture
def session():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_varina_scores_on_wub(session):
    if not SPIKE_CSV_PATH.exists():
        return
    prior = HistoricalSpikePrior.build(session)
    score = prior.score("Varina, Lich Queen", ["W", "U", "B"])
    assert score > 0.5


def test_varina_low_score_on_unrelated_colors(session):
    if not SPIKE_CSV_PATH.exists():
        return
    prior = HistoricalSpikePrior.build(session)
    score = prior.score("Varina, Lich Queen", ["R", "G"])
    assert score < prior.score("Varina, Lich Queen", ["W", "U", "B"])
