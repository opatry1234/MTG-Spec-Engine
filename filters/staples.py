"""
Staples exclusion list management.

Maintains the list of eternal staples that should never be candidates.
"""

from datetime import date

from db.schema import StaplesExclusionList


def get_staples(session) -> set:
    """Get all staple card names that should be excluded from candidates."""
    rows = session.query(StaplesExclusionList.card_name).all()
    return {row[0] for row in rows}


def filter_candidates(candidates: list, session) -> list:
    """Filter out staple cards from candidate list."""
    staples = get_staples(session)
    return [c for c in candidates if c not in staples]


def add_staple(session, card_name: str, reason: str, added_by: str = "manual"):
    """Add a card to the staples exclusion list."""
    session.merge(
        StaplesExclusionList(
            card_name=card_name,
            reason=reason,
            added_date=date.today(),
            added_by=added_by,
        )
    )
    session.commit()


def remove_staple(session, card_name: str):
    """Remove a card from the staples exclusion list."""
    session.query(StaplesExclusionList).filter(
        StaplesExclusionList.card_name == card_name
    ).delete()
    session.commit()
