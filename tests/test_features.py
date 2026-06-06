"""Tests for feature engineering and backtesting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from features.mechanical import (
    color_identity_match,
    keyword_category_scores,
    parse_creature_subtypes,
)
from backtester.metrics import omission_hit_rate, top_n_hit_rate


def test_color_identity_match():
    assert color_identity_match(["W", "U"], ["W", "U", "B"]) is True
    assert color_identity_match(["W", "R"], ["W", "U"]) is False


def test_creature_subtypes():
    assert "Elf" in parse_creature_subtypes("Legendary Creature — Elf Druid")


def test_keyword_categories():
    scores = keyword_category_scores("Create a token that's a copy of target creature.")
    assert scores["token"] > 0
    assert scores["copy"] > 0


def test_omission_hit_rate():
    predicted = ["A", "B", "C", "D", "E"]
    actual = {"A", "X", "Y"}
    assert omission_hit_rate(predicted, actual, 5) == 0.8


def test_inclusion_hit_rate():
    predicted = ["A", "B", "C"]
    actual = {"A", "B", "Z"}
    assert top_n_hit_rate(predicted, actual, 3) == 2 / 3


def test_reserved_list_excluded_from_probable_pool():
    from db.schema import Card
    from filters.candidates import is_commander_candidate, passes_spec_candidate_filters

    led = Card(name="Lion's Eye Diamond", reserved=True, color_identity=[], commander_legal=True)
    assert is_commander_candidate(led) is False
    assert passes_spec_candidate_filters(led, ["R"]) is True


def test_vanguard_excluded():
    from db.schema import Card
    from filters.candidates import is_commander_candidate, is_color_legal

    teysa = Card(
        name="Teysa, Orzhov Scion Avatar",
        type_line="Vanguard",
        color_identity=[],
        reserved=False,
        commander_legal=False,
    )
    assert is_commander_candidate(teysa) is False
    assert is_color_legal(teysa, ["R", "W"]) is True  # empty identity colorless


def test_sticker_type_excluded():
    from db.schema import Card
    from filters.candidates import is_commander_candidate, passes_candidate_filters

    cake = Card(
        name="Giant Mana Cake",
        type_line="Stickers",
        color_identity=["U"],
        reserved=False,
        commander_legal=True,
        layout="normal",
    )
    assert is_commander_candidate(cake) is False
    assert passes_candidate_filters(cake, ["W", "U", "B"]) is False


def test_color_identity_enforced():
    from db.schema import Card
    from filters.candidates import passes_candidate_filters

    teysa = Card(
        name="Teysa, Orzhov Scion",
        color_identity=["W", "B"],
        reserved=False,
        commander_legal=True,
        layout="normal",
        type_line="Legendary Creature — Human Advisor",
    )
    assert passes_candidate_filters(teysa, ["R", "W"]) is False
    assert passes_candidate_filters(teysa, ["W", "B", "R"]) is True


def test_parse_announced_new_cards():
    from engine.product_parser import parse_announced_new_cards

    lorehold = (
        "Every Secrets of Strixhaven Commander Deck includes two traditional foil mythic rare "
        "legendary creature cards featuring borderless art that can be played as your commander, "
        "and each deck introduces 12 never-before-seen Commander cards to Magic: The Gathering.\n"
        "Contents:\n• 1 ready-to-play deck of 100 Magic cards\n"
        "• 12 new-to-Magic cards, including 2 traditional foil cards"
    )
    assert parse_announced_new_cards(lorehold) == 12
    assert parse_announced_new_cards("17 Magic cards make their debut") == 17
    assert parse_announced_new_cards("") is None


def test_excel_land_count_parsing():
    import pandas as pd
    from config import DATA_DIR
    from ingest.decklists_analyzer import read_deck_from_sheet

    path = DATA_DIR / "decklists" / "Commander_Precon_Decklists.xlsx"
    if not path.exists():
        return
    df = pd.read_excel(path, sheet_name="Lorehold Spirit")
    deck = read_deck_from_sheet("Lorehold Spirit", df)
    assert deck["land_count"] == 37
    assert deck["cards"]["Mountain"] == 6
    assert deck["cards"]["Plains"] == 11
    assert sum(deck["cards"].values()) == 100

