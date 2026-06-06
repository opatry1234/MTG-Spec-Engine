"""
Mechanical feature engineering.

Computes card synergy features based on color identity, creature types, and keywords.
"""

import re
from collections import defaultdict


KEYWORD_CATEGORIES = {
    "token": ["create", "token", "populate", "convoke"],
    "graveyard": ["flashback", "delve", "escape", "mill", "graveyard"],
    "copy": ["copy", "copies", "replicate"],
    "sacrifice": ["sacrifice", "dies", "leaves the battlefield"],
    "counter": ["proliferate", "+1/+1 counter", "-1/-1 counter", "wither", "infect", "blight"],
    "artifact": ["artifact enters", "affinity", "improvise"],
}

# Signals that identify each keyword category in a deck's synergy query text.
# Used by get_deck_primary_keyword_category() to select a category-specific
# keyword score instead of the max-across-all-categories score — prevents e.g.
# Food-token Doctor Who cards from scoring high in a graveyard/Spirit deck.
CATEGORY_THEME_SIGNALS: dict[str, list[str]] = {
    "token": ["token", "create a", "creature token", "populate", "convoke"],
    "graveyard": ["graveyard", "flashback", "escape", "delve", "mill", "from your graveyard", "dies"],
    "copy": ["copy", "copies of", "replicate", "clone"],
    "sacrifice": ["sacrifice", "when ~ dies", "leaves the battlefield"],
    "counter": ["counter", "+1/+1", "-1/-1", "proliferate", "blight", "infect"],
    "artifact": ["artifact", "affinity", "improvise", "artifact enters"],
}

# Keywords that indicate a deck's theme involves any counter manipulation.
# Used for thematic adjacency: a blight/-1/-1 deck can spike +1/+1 counter cards.
COUNTER_THEME_SIGNALS = [
    "counter", "-1/-1", "+1/+1", "proliferate", "blight", "charge counter",
    "energy counter", "loyalty counter", "poison counter", "infect",
]

# Keywords that indicate a card interacts with +1/+1 or -1/-1 counters specifically.
# Deliberately narrow — age counters, charge counters, loyalty counters are NOT relevant
# to the thematic adjacency we're modelling (blight/-1/-1 deck spiking +1/+1 counter cards).
COUNTER_INTERACTION_KEYWORDS = [
    "+1/+1 counter", "-1/-1 counter", "proliferate",
    "wither", "infect", "blight", "toxic",
    "put a +1/+1", "put a -1/-1", "remove a +1/+1", "remove a -1/-1",
]

# Patterns indicating a card mass-removes or mass-relocates +1/+1 counters, making it a
# hate card against counter/proliferate strategies. Used to identify off-color "hate specs"
# that spike in price when a prominent counter commander is spoiled.
COUNTER_DISRUPTION_PATTERNS = [
    "move all +1/+1 counters",
    "remove all +1/+1 counters",
    "remove all counters from all",
    "remove all counters from each",
    "counters from other permanents",
    "counters are removed from",
    "lose all counters",
]


def color_identity_match(card_colors: list, deck_colors: list) -> bool:
    """Check if card color identity is subset of deck colors."""
    return set(card_colors).issubset(set(deck_colors))


def creature_type_overlap(card_type_line: str, theme_types: list) -> float:
    """Calculate creature type overlap with deck theme."""
    if not theme_types or not card_type_line:
        return 0.0
    card_types = parse_creature_subtypes(card_type_line)
    matches = len(set(card_types) & set(theme_types))
    return matches / max(len(theme_types), 1)


def parse_creature_subtypes(type_line: str) -> list:
    """Parse creature subtypes from a type line."""
    if not type_line or "—" not in type_line:
        return []
    after_dash = type_line.split("—", 1)[1].strip()
    return [t for t in after_dash.split() if t]


# Common tribal mentions in deck themes / product copy (lowercase keys).
_THEME_TRIBAL_HINTS = (
    "zombie",
    "elf",
    "elves",
    "goblin",
    "dragon",
    "vampire",
    "angel",
    "spirit",
    "wizard",
    "warrior",
    "human",
    "cat",
    "bird",
    "beast",
    "dinosaur",
    "merfolk",
    "faerie",
    "knight",
    "soldier",
    "demon",
    "horror",
    "elemental",
    "treefolk",
    "shaman",
    "cleric",
    "rogue",
    "artifact",
    "equipment",
    "vehicle",
    "tyranid",
    "necron",
)


def extract_creature_types_from_text(*texts: str) -> list[str]:
    """Pull tribal hints from theme / commander / product description."""
    combined = " ".join(t for t in texts if t).lower()
    found = []
    for hint in _THEME_TRIBAL_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", combined):
            found.append(hint.capitalize() if hint != "elves" else "Elf")
    return list(dict.fromkeys(found))


def extract_deck_creature_types(
    *,
    commander_type_line: str = "",
    theme: str = "",
    product_description: str = "",
) -> list[str]:
    """Build deck tribal/type theme list for creature_type_overlap."""
    types = parse_creature_subtypes(commander_type_line)
    types.extend(
        extract_creature_types_from_text(theme, product_description, commander_type_line)
    )
    return list(dict.fromkeys(types))


def keyword_overlap_score(oracle_text: str, theme_keywords: list) -> float:
    """Calculate keyword overlap with deck theme."""
    if not oracle_text or not theme_keywords:
        return 0.0
    text = oracle_text.lower()
    hits = sum(1 for kw in theme_keywords if kw.lower() in text)
    return hits / len(theme_keywords)


def keyword_category_scores(oracle_text: str) -> dict:
    """Score oracle text against each KEYWORD_CATEGORIES bucket."""
    if not oracle_text:
        return {cat: 0.0 for cat in KEYWORD_CATEGORIES}
    text = oracle_text.lower()
    scores = {}
    for category, keywords in KEYWORD_CATEGORIES.items():
        hits = sum(1 for kw in keywords if kw in text)
        scores[category] = min(hits / max(len(keywords), 1), 1.0)
    return scores


def best_keyword_category_score(oracle_text: str) -> float:
    """Return the highest keyword category match score."""
    scores = keyword_category_scores(oracle_text)
    return max(scores.values()) if scores else 0.0


def get_deck_primary_keyword_category(query_text: str) -> str | None:
    """Return the strongest keyword category for this deck's synergy query.

    Counts signal hits per category and returns the winner. Returns None when
    no category has a clear signal (e.g. a generic goodstuff deck).

    Used to make keyword scoring DECK-THEME-AWARE: instead of
    best_keyword_category_score (max across ALL buckets), the synergy scorer
    uses only the deck's primary category score. This prevents e.g. a Food-token
    Doctor Who card from scoring high in a graveyard/Spirit deck just because
    it mentions 'token' or 'graveyard' incidentally.
    """
    if not query_text:
        return None
    text = query_text.lower()
    scores: dict[str, int] = {}
    for cat, signals in CATEGORY_THEME_SIGNALS.items():
        scores[cat] = sum(1 for s in signals if s in text)
    best_cat, best_score = max(scores.items(), key=lambda x: x[1])
    return best_cat if best_score >= 2 else None


def deck_has_counter_theme(query_text: str) -> bool:
    """Return True if the deck's synergy query suggests a counter-manipulation theme."""
    if not query_text:
        return False
    text = query_text.lower()
    return any(sig in text for sig in COUNTER_THEME_SIGNALS)


def card_has_counter_interaction(oracle_text: str) -> bool:
    """Return True if the card interacts with counters in any meaningful way."""
    if not oracle_text:
        return False
    text = oracle_text.lower()
    return any(kw in text for kw in COUNTER_INTERACTION_KEYWORDS)


def is_mana_fixer_for_colors(type_line: str, color_identity: list, deck_colors: list) -> bool:
    """Return True if this card is a dual/fixing land that produces 2+ of the deck's colors.

    Used at Phase 3 (decklist_revealed) to boost confirmed mana-fixing omissions —
    lands that clearly belong in the deck but weren't reprinted (Tainted Isle pattern).
    Only non-basic lands count; basic lands are staples excluded elsewhere.
    """
    if not type_line:
        return False
    tl = type_line.lower()
    if "land" not in tl:
        return False
    if "basic land" in tl:
        return False
    deck_set = set(deck_colors or [])
    card_set = set(color_identity or [])
    # Must produce at least 2 of the deck's colors (dual or tri-land)
    return len(card_set & deck_set) >= 2


# Mechanic signatures for pool-size scoring (smaller pool → stronger spec signal).
MECHANIC_SIGNATURES: dict[str, list[str]] = {
    "myriad": ["myriad"],
    "zombie": ["zombie"],
    "elf": ["elf"],
    "vampire": ["vampire"],
    "spirit": ["spirit"],
    "token": ["create", "token", "populate"],
    "graveyard": ["graveyard", "flashback", "delve", "mill"],
    "sacrifice": ["sacrifice", "dies"],
    "counter": ["+1/+1 counter", "-1/-1 counter", "proliferate"],
    "land_discard": ["discard a land", "discard land", "land card"],
    "copy": ["copy", "copies of", "replicate", "clone"],
    "artifact": ["artifact", "affinity", "improvise"],
    "discard": ["discard"],
    "draw": ["draw a card", "draw cards"],
    "tutor": ["search your library"],
}


def _extract_mechanic_hits(text: str) -> set[str]:
    if not text:
        return set()
    lower = text.lower()
    hits: set[str] = set()
    for sig, probes in MECHANIC_SIGNATURES.items():
        if any(p in lower for p in probes):
            hits.add(sig)
    for hint in _THEME_TRIBAL_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", lower):
            hits.add(hint)
    return hits


def oracle_text_overlap(commander_text: str, card_oracle: str) -> float:
    """
    Overlap between commander oracle phrases and candidate oracle text.

    Example: commander has ``myriad`` → card with ``gains myriad`` scores high.
    """
    if not commander_text or not card_oracle:
        return 0.0

    cmd_lower = commander_text.lower()
    card_lower = card_oracle.lower()

    phrases: list[str] = []
    for sig_probes in MECHANIC_SIGNATURES.values():
        for probe in sig_probes:
            if probe in cmd_lower:
                phrases.append(probe)
    for hint in _THEME_TRIBAL_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", cmd_lower):
            phrases.append(hint)

    words = re.findall(r"[a-z]{4,}", cmd_lower)
    phrases.extend(words)

    unique = list(dict.fromkeys(phrases))
    if not unique:
        return 0.0

    hits = sum(1 for p in unique if p in card_lower)
    return round(min(hits / len(unique), 1.0), 4)


def pool_size_to_score(pool_size: int) -> float:
    """Map mechanic pool size to 0–1 (tiny pools score highest)."""
    if pool_size <= 0:
        return 0.0
    if pool_size <= 20:
        return 1.0
    if pool_size <= 100:
        return 0.85
    if pool_size <= 500:
        return 0.55
    if pool_size <= 2000:
        return 0.30
    return 0.10


class MechanicalPoolIndex:
    """Precomputed oracle-card counts per mechanic signature."""

    def __init__(self, cards: list | None = None):
        self._pools: dict[str, set[str]] = defaultdict(set)
        if cards:
            self._build(cards)

    def _build(self, cards: list) -> None:
        for card in cards:
            name = getattr(card, "name", None) or ""
            oracle = getattr(card, "oracle_text", None) or ""
            if not name:
                continue
            for sig in _extract_mechanic_hits(oracle):
                self._pools[sig].add(name)

    def pool_size(self, signature: str) -> int:
        return len(self._pools.get(signature, set()))

    def mechanical_pool_size_score(self, deck_text: str, card_oracle: str) -> float:
        deck_hits = _extract_mechanic_hits(deck_text)
        card_hits = _extract_mechanic_hits(card_oracle)
        shared = deck_hits & card_hits
        relevant = shared or card_hits or deck_hits
        if not relevant:
            return 0.0
        smallest = min(self.pool_size(sig) for sig in relevant)
        return round(pool_size_to_score(smallest), 4)


def card_disrupts_counter_strategy(oracle_text: str) -> bool:
    """Return True if the card mass-removes or mass-relocates +1/+1 counters.

    These are 'hate cards' — off-color cards that spike when a prominent
    counter/proliferate commander is printed, because players add them to
    their decks to counter that strategy.
    """
    if not oracle_text:
        return False
    text = oracle_text.lower()
    return any(pat in text for pat in COUNTER_DISRUPTION_PATTERNS)
