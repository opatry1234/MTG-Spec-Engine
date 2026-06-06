"""
Auto-include statistics from historical precon data.

Identifies tier-1 staples (hard exclude) and tier-2 common inclusions
by color identity.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DATA_DIR, DATABASE_URL
from db.schema import CommanderDeck, DeckCard, StaplesExclusionList

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

OUTPUT_PATH = DATA_DIR / "analytics" / "auto_includes.json"

TIER1_THRESHOLD = 0.95
TIER2_THRESHOLD = 0.70

# --- Layered staple model (color-pooled + archetype, sparsity-safe) ---
# Keying on EXACT color combo breaks down: with ~161 decks across 32 combos, a
# 1-deck combo declares every card a 100% staple. Instead pool by single color
# (decks that CONTAIN the color) and by archetype, and require a minimum deck
# count per cell before trusting any frequency.
STAPLE_UNIVERSAL_THRESHOLD = 0.90   # in >=90% of ALL decks (Sol Ring, Command Tower…)
STAPLE_COLOR_THRESHOLD = 0.60       # in >=60% of decks containing that color
STAPLE_ARCHETYPE_THRESHOLD = 0.60   # in >=60% of decks of that archetype
STAPLE_MIN_DECKS = 5                # skip any cell with fewer decks (no 1-deck artifacts)
STAPLE_LAYERS_PATH = DATA_DIR / "analytics" / "staple_layers.json"


def _color_key(colors: Optional[list]) -> str:
    if not colors:
        return "C"
    order = ["W", "U", "B", "R", "G"]
    return "".join(c for c in order if c in colors)


def compute_auto_includes(session=None) -> dict:
    """Compute tier-1/tier-2 auto-includes globally and by color identity."""
    own_session = session is None
    if own_session:
        session = Session()

    try:
        decks = session.query(CommanderDeck).all()
        total = len(decks)

        global_counts = Counter()
        by_color: Dict[str, Counter] = defaultdict(Counter)
        decks_by_color: Dict[str, int] = defaultdict(int)

        for deck in decks:
            ck = _color_key(deck.colors)
            decks_by_color[ck] += 1
            card_names = {
                row[0]
                for row in session.query(DeckCard.card_name)
                .filter(DeckCard.deck_id == deck.id)
                .all()
            }
            for name in card_names:
                global_counts[name] += 1
                by_color[ck][name] += 1

        def _tier(counts: Counter, deck_total: int, t1: float, t2: float) -> dict:
            tier1, tier2 = [], []
            for name, count in counts.most_common():
                rate = count / deck_total
                entry = {"card_name": name, "rate": round(rate, 3), "count": count}
                if rate >= t1:
                    tier1.append(entry)
                elif rate >= t2:
                    tier2.append(entry)
            return {"tier1": tier1, "tier2": tier2}

        result = {
            "global": _tier(global_counts, total, TIER1_THRESHOLD, TIER2_THRESHOLD),
            "by_color_identity": {
                ck: _tier(by_color[ck], decks_by_color[ck], TIER1_THRESHOLD, TIER2_THRESHOLD)
                for ck in sorted(by_color)
            },
            "deck_count": total,
        }

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(result, f, indent=2)

        return result

    finally:
        if own_session:
            session.close()


def get_tier1_excludes(session=None, colors: Optional[list] = None) -> Set[str]:
    """Return tier-1 auto-include card names (from DB staples + computed stats)."""
    own_session = session is None
    if own_session:
        session = Session()

    try:
        staples = {
            row[0]
            for row in session.query(StaplesExclusionList.card_name).all()
        }

        if OUTPUT_PATH.exists():
            with open(OUTPUT_PATH) as f:
                data = json.load(f)
        else:
            data = compute_auto_includes(session)

        ck = _color_key(colors)
        section = data.get("by_color_identity", {}).get(ck, data.get("global", {}))
        tier1 = {e["card_name"] for e in section.get("tier1", [])}
        return staples | tier1

    finally:
        if own_session:
            session.close()


def get_tier2_includes(colors: Optional[list] = None) -> List[str]:
    """Return tier-2 cards likely included but not hard-excluded."""
    if not OUTPUT_PATH.exists():
        compute_auto_includes()
    with open(OUTPUT_PATH) as f:
        data = json.load(f)

    ck = _color_key(colors)
    section = data.get("by_color_identity", {}).get(ck, data.get("global", {}))
    return [e["card_name"] for e in section.get("tier2", [])]


def compute_staple_layers(session=None) -> dict:
    """Pooled staple layers: universal, per single color, per archetype.

    Sparsity-safe — cells below STAPLE_MIN_DECKS are dropped so a 1-deck combo
    can't declare every card a staple.
    """
    own_session = session is None
    if own_session:
        session = Session()
    try:
        decks = (
            session.query(CommanderDeck)
            .filter(CommanderDeck.decklist_revealed == True)  # noqa: E712
            .all()
        )
        deck_cards: Dict[int, set] = defaultdict(set)
        for deck_id, name in session.query(DeckCard.deck_id, DeckCard.card_name).all():
            deck_cards[deck_id].add(name)

        order = ["W", "U", "B", "R", "G"]
        total = len(decks)
        universal = Counter()
        color_counts: Dict[str, Counter] = defaultdict(Counter)
        color_n: Dict[str, int] = defaultdict(int)
        arch_counts: Dict[str, Counter] = defaultdict(Counter)
        arch_n: Dict[str, int] = defaultdict(int)

        for deck in decks:
            cards = deck_cards.get(deck.id, set())
            cols = [c for c in order if c in (deck.colors or [])]
            arch = (deck.primary_archetype or "").strip().lower()
            for name in cards:
                universal[name] += 1
            for c in cols:
                color_n[c] += 1
                for name in cards:
                    color_counts[c][name] += 1
            if arch:
                arch_n[arch] += 1
                for name in cards:
                    arch_counts[arch][name] += 1

        def _pick(counter: Counter, n: int, thr: float) -> list:
            if not n:
                return []
            return sorted(name for name, cnt in counter.items() if cnt / n >= thr)

        layers = {
            "universal": _pick(universal, total, STAPLE_UNIVERSAL_THRESHOLD),
            "by_color": {
                c: _pick(color_counts[c], color_n[c], STAPLE_COLOR_THRESHOLD)
                for c in order
                if color_n[c] >= STAPLE_MIN_DECKS
            },
            "by_archetype": {
                a: _pick(arch_counts[a], arch_n[a], STAPLE_ARCHETYPE_THRESHOLD)
                for a in arch_counts
                if arch_n[a] >= STAPLE_MIN_DECKS
            },
            "deck_count": total,
            "color_deck_counts": {c: color_n[c] for c in order},
            "archetype_deck_counts": dict(arch_n),
            "thresholds": {
                "universal": STAPLE_UNIVERSAL_THRESHOLD,
                "color": STAPLE_COLOR_THRESHOLD,
                "archetype": STAPLE_ARCHETYPE_THRESHOLD,
                "min_decks": STAPLE_MIN_DECKS,
            },
        }
        STAPLE_LAYERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(STAPLE_LAYERS_PATH, "w") as f:
            json.dump(layers, f, indent=2)
        return layers
    finally:
        if own_session:
            session.close()


def get_staple_excludes(
    session=None,
    colors: Optional[list] = None,
    archetype: Optional[str] = None,
) -> Set[str]:
    """Staples to drop from the spec pool for a deck: universal + its colors +
    its archetype + the manual exclusion list."""
    own_session = session is None
    if own_session:
        session = Session()
    try:
        manual = {row[0] for row in session.query(StaplesExclusionList.card_name).all()}
        if STAPLE_LAYERS_PATH.exists():
            with open(STAPLE_LAYERS_PATH) as f:
                layers = json.load(f)
        else:
            layers = compute_staple_layers(session)

        out: Set[str] = set(layers.get("universal", []))
        for c in colors or []:
            out |= set(layers.get("by_color", {}).get(c, []))
        if archetype:
            out |= set(layers.get("by_archetype", {}).get(archetype.strip().lower(), []))
        return out | manual
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    result = compute_auto_includes()
    print(f"Tier-1 global staples: {len(result['global']['tier1'])}")
    print(f"Tier-2 global includes: {len(result['global']['tier2'])}")
    layers = compute_staple_layers()
    print(f"Universal staples: {len(layers['universal'])}")
    print(f"Color layers: { {c: len(v) for c, v in layers['by_color'].items()} }")
    print(f"Archetype layers: { {a: len(v) for a, v in layers['by_archetype'].items()} }")
    print(f"Written to {OUTPUT_PATH} and {STAPLE_LAYERS_PATH}")
