"""
Precon-attributed spike detection.

Valid omission spec spikes are price moves tied to a precon release — typically
upgrade demand when players build beyond the announced decklist. We prefer the
announcement window (commander spoiled) and exclude unrelated commander-product
report noise from other precon lines.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import List, Optional

from config import (
    ALT_COMMANDER_SYNERGY_MIN,
    SPIKE_ALT_EDHREC_MIN,
    SPIKE_MIN_DECK_SYNERGY,
    SPIKE_OBSCURE_EDHREC_MIN,
)
from engine.commander_legality import is_format_driven_spike
from features.mechanical import color_identity_match

_COMMANDER_ANNUAL = re.compile(r"^Commander \d{4}$")

BASIC_LAND_NAMES = frozenset(
    {
        "Plains",
        "Island",
        "Swamp",
        "Mountain",
        "Forest",
        "Wastes",
        "Snow-Covered Plains",
        "Snow-Covered Island",
        "Snow-Covered Swamp",
        "Snow-Covered Mountain",
        "Snow-Covered Forest",
    }
)


def is_basic_land_card(card_name: Optional[str]) -> bool:
    if not card_name:
        return False
    name = card_name.strip()
    if name in BASIC_LAND_NAMES:
        return True
    return name.startswith("Basic ") or name.endswith(" Basic Land")


def is_commander_product_set(set_name: str) -> bool:
    name = (set_name or "").strip()
    if not name:
        return False
    if name.startswith("Commander:"):
        return True
    if _COMMANDER_ANNUAL.match(name):
        return True
    if name in {"Commander", "Commander Legends", "Commander Masters"}:
        return True
    if name.startswith("Commander Legends:"):
        return True
    return False


def is_unrelated_commander_set(
    set_name: str,
    commander_spike_set: Optional[str],
) -> bool:
    """
    True when the spike row's set belongs to a different commander product line.
    """
    name = (set_name or "").strip()
    if not name or not is_commander_product_set(name):
        return False
    if commander_spike_set and name == commander_spike_set:
        return False
    return True


def announcement_window(reveal_date: date) -> tuple[date, date]:
    """Reveal-anchored window (legacy name kept for callers)."""
    from backtester.spike_csv import spike_window

    return spike_window(reveal_date)


def shelf_window(reveal_date: date) -> tuple[date, date]:
    return announcement_window(reveal_date)


_SHELF_PEAK_DAYS = 4
_SHELF_MIN_SYNERGY = 0.20


def is_near_shelf_date(report_date: date, precon_release_date: date) -> bool:
    return abs((report_date - precon_release_date).days) <= _SHELF_PEAK_DAYS


def card_fits_deck_colors(
    card_color_identity: Optional[List[str]],
    deck_colors: Optional[List[str]],
) -> bool:
    if not deck_colors:
        return True
    if card_color_identity is None:
        return False
    return color_identity_match(card_color_identity, deck_colors)


_SPIKE_BATCH_MIN_COUNT = 50


def is_mass_spike_report_date(report_date: date) -> bool:
    from backtester.spike_csv import count_qualifying_spikes_on_date

    return count_qualifying_spikes_on_date(report_date) >= _SPIKE_BATCH_MIN_COUNT


def passes_deck_synergy_gate(
    *,
    synergy_fit: Optional[float],
    is_alt_commander: bool = False,
    min_synergy: float = SPIKE_MIN_DECK_SYNERGY,
) -> bool:
    """
    Spike must reflect plausible deck-building demand, not generic market noise.

    Alternate commanders with moderate theme fit pass at a lower bar.
    """
    if synergy_fit is None:
        return False
    if is_alt_commander and synergy_fit >= ALT_COMMANDER_SYNERGY_MIN:
        return True
    return synergy_fit >= min_synergy


def passes_spec_target_profile(
    card,
    *,
    synergy_fit: Optional[float],
    is_alt_commander: bool,
    on_mass_batch_day: bool,
) -> bool:
    """
    On mass-report days (100+ spikes), require cards that look like real spec
    targets — obscure alt commanders or low-supply/theme upgrades — not EDH
    staples that moved with the whole market.
    """
    if not passes_deck_synergy_gate(
        synergy_fit=synergy_fit,
        is_alt_commander=is_alt_commander,
    ):
        return False

    if not on_mass_batch_day:
        return True

    edhrec = card.edhrec_rank if card and card.edhrec_rank else 99999
    if is_alt_commander:
        return edhrec >= SPIKE_ALT_EDHREC_MIN
    if card and card.reserved:
        return True
    return edhrec >= SPIKE_OBSCURE_EDHREC_MIN


def is_announcement_attributed_spike(
    *,
    synergy_fit: Optional[float] = None,
    is_alt_commander: bool = False,
    card=None,
    report_date: Optional[date] = None,
    deck_colors: Optional[List[str]] = None,
    card_color_identity: Optional[List[str]] = None,
) -> bool:
    if deck_colors and not card_fits_deck_colors(card_color_identity, deck_colors):
        return False

    on_batch = bool(report_date and is_mass_spike_report_date(report_date))
    return passes_spec_target_profile(
        card,
        synergy_fit=synergy_fit,
        is_alt_commander=is_alt_commander,
        on_mass_batch_day=on_batch,
    )


def is_shelf_attributed_spike(
    report_date: date,
    precon_release_date: date,
    *,
    card_name: Optional[str] = None,
    deck_colors: Optional[List[str]] = None,
    card_color_identity: Optional[List[str]] = None,
    synergy_fit: Optional[float] = None,
    is_alt_commander: bool = False,
) -> bool:
    """
    Stricter shelf-wave attribution than the broad CSV lookup window.

    Mass-report batch days (e.g. 1000+ rows on precon shelf date) are treated
    as market-wide noise — shelf spikes on those dates are never attributed.
    """
    if not is_near_shelf_date(report_date, precon_release_date):
        return False

    if is_mass_spike_report_date(report_date):
        return False

    if card_name and is_basic_land_card(card_name):
        return False

    if deck_colors and not card_fits_deck_colors(card_color_identity, deck_colors):
        return False

    return passes_deck_synergy_gate(
        synergy_fit=synergy_fit,
        is_alt_commander=is_alt_commander,
        min_synergy=_SHELF_MIN_SYNERGY,
    )


def is_loose_omission_spike(
    report_date: date,
    set_name: str,
    release_date: date,
    *,
    precon_release_date: Optional[date] = None,
    commander_spike_set: Optional[str] = None,
    card_name: Optional[str] = None,
    deck_colors: Optional[List[str]] = None,
    card_color_identity: Optional[List[str]] = None,
) -> bool:
    """Broader training label: post-reveal spike on an omitted, color-legal card."""
    reveal_date = release_date
    if report_date < reveal_date:
        return False

    if card_name and is_format_driven_spike(card_name, report_date):
        return False

    if is_unrelated_commander_set(set_name, commander_spike_set):
        return False

    if commander_spike_set and (set_name or "").strip() == commander_spike_set:
        return False

    if deck_colors and not card_fits_deck_colors(card_color_identity, deck_colors):
        return False

    from backtester.spike_csv import spike_window

    _, window_end = spike_window(reveal_date, precon_release_date)
    return reveal_date <= report_date <= window_end


def is_precon_attributed_spike(
    report_date: date,
    set_name: str,
    release_date: date,
    *,
    precon_release_date: Optional[date] = None,
    commander_spike_set: Optional[str] = None,
    card_name: Optional[str] = None,
    deck_colors: Optional[List[str]] = None,
    card_color_identity: Optional[List[str]] = None,
    synergy_fit: Optional[float] = None,
    is_alt_commander: bool = False,
    card=None,
) -> bool:
    """True when a spike plausibly reflects post-reveal precon upgrade demand."""
    reveal_date = release_date
    if report_date < reveal_date:
        return False

    if card_name and is_format_driven_spike(card_name, report_date):
        return False

    if is_unrelated_commander_set(set_name, commander_spike_set):
        return False

    if commander_spike_set and (set_name or "").strip() == commander_spike_set:
        return False

    if deck_colors and not card_fits_deck_colors(card_color_identity, deck_colors):
        return False

    from backtester.spike_csv import spike_window

    _, window_end = spike_window(reveal_date, precon_release_date)
    if report_date > window_end:
        return False

    on_batch = is_mass_spike_report_date(report_date)
    return passes_spec_target_profile(
        card,
        synergy_fit=synergy_fit,
        is_alt_commander=is_alt_commander,
        on_mass_batch_day=on_batch,
    )


def attribution_label(
    report_date: date,
    set_name: str,
    release_date: date,
    *,
    precon_release_date: Optional[date] = None,
    commander_spike_set: Optional[str] = None,
    card_name: Optional[str] = None,
    deck_colors: Optional[List[str]] = None,
    card_color_identity: Optional[List[str]] = None,
    synergy_fit: Optional[float] = None,
    is_alt_commander: bool = False,
    card=None,
) -> str:
    if card_name and is_format_driven_spike(card_name, report_date):
        return "format_event"
    if not is_precon_attributed_spike(
        report_date,
        set_name,
        release_date,
        precon_release_date=precon_release_date,
        commander_spike_set=commander_spike_set,
        card_name=card_name,
        deck_colors=deck_colors,
        card_color_identity=card_color_identity,
        synergy_fit=synergy_fit,
        is_alt_commander=is_alt_commander,
        card=card,
    ):
        return "unrelated"
    return "post_reveal"
