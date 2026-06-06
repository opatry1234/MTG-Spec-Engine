"""Tests for commander type eligibility."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from filters.candidates import can_be_commander


def test_legendary_creature():
    assert can_be_commander("Legendary Creature — Zombie Wizard")


def test_legendary_planeswalker():
    assert can_be_commander("Legendary Planeswalker — Jace")


def test_enchantment_not_commander():
    assert not can_be_commander("Enchantment")


def test_legendary_artifact_not_commander():
    assert not can_be_commander("Legendary Artifact")


def test_battle_only_not_commander():
    assert not can_be_commander("Battle — Siege")


def test_mdfc_uses_legendary_back_face():
    assert can_be_commander("Battle — Siege // Legendary Creature — Serpent")
