"""Tests for pre-release spec eligibility."""

import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_URL
from db.schema import CardPrinting
from engine.spec_eligibility import (
    build_earliest_printing_map,
    was_spec_eligible_at_prerelease,
)


@pytest.fixture
def session():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_varina_eligible_for_aetherdrift(session):
    earliest_map = build_earliest_printing_map(session)
    assert was_spec_eligible_at_prerelease(
        "Varina, Lich Queen",
        date(2025, 2, 14),
        earliest_map,
    )


def test_future_printing_ineligible(session):
    earliest_map = build_earliest_printing_map(session)
    # Gogo, Mysterious Mime first printed June 2025 — cannot spec DFT Feb 2025
    assert not was_spec_eligible_at_prerelease(
        "Gogo, Mysterious Mime",
        date(2025, 2, 14),
        earliest_map,
    )


def test_gifts_ineligible_at_dft_spoiler(session):
    earliest_map = build_earliest_printing_map(session)
    assert not was_spec_eligible_at_prerelease(
        "Gifts Ungiven",
        date(2025, 2, 14),
        earliest_map,
    )


def test_same_day_printing_is_eligible(session):
    earliest_map = build_earliest_printing_map(session)
    cutoff = date(2025, 2, 14)
    # Card whose first printing is exactly on release day counts as existing
    session.merge(
        CardPrinting(
            id="test-spec-eligibility-same-day",
            card_name="Spec Eligibility Test Card",
            set_code="tst",
            released_at=cutoff,
            is_commander_precon=False,
        )
    )
    session.commit()
    earliest_map = build_earliest_printing_map(session)
    assert was_spec_eligible_at_prerelease(
        "Spec Eligibility Test Card",
        cutoff,
        earliest_map,
    )
    session.query(CardPrinting).filter_by(id="test-spec-eligibility-same-day").delete()
    session.commit()
