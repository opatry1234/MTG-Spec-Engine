"""
Domain-aware mechanic taxonomy for commander↔card synergy.

Why this exists: bag-of-words synergy (TF-IDF over oracle text) is blind to the
phrases that actually define MTG synergy — "-1/-1 counter" is stripped by word
tokenizers, "+1/+1" and "-1/-1" decks look identical, and a thematically-critical
match is diluted by filler words. Validated failure case: Crumbling Ashes
("destroy each creature with a -1/-1 counter") scored synergy 0.27 against Auntie
Ool ("whenever one or more -1/-1 counters are put on a creature...") despite being
the documented premier upgrade.

Each mechanic = a set of punctuation-aware regex patterns + a distinctiveness
weight (how strongly sharing this mechanic implies real synergy: "-1/-1 counters"
is a tight archetype, "card draw" is everything). Score = weighted overlap of the
card's mechanics against the commander/theme's mechanics.
"""

from __future__ import annotations

import re

# mechanic id -> (distinctiveness weight, [regex patterns])
# Weights: 1.0 = tight archetype glue; 0.6 = strong theme; 0.3 = common engine piece.
MECHANICS: dict[str, tuple[float, list[str]]] = {
    # Counter archetypes — DISTINCT mechanics, never conflated.
    "minus_counters": (1.0, [
        r"-1/-1 counter", r"\bwither\b", r"\binfect\b", r"\bpersist\b",
        r"\bblight(?:s|ed)?\b",
    ]),
    "plus_counters": (0.8, [
        r"\+1/\+1 counter", r"\bmodular\b", r"\bevolve\b", r"\badapt\b",
        r"\bbolster\b", r"\bmentor\b", r"\bsupport \d", r"\btraining\b",
        r"\bbackup \d", r"\breinforce\b",
    ]),
    "proliferate": (0.9, [r"\bproliferate\b"]),
    "poison": (1.0, [r"\bpoison counter", r"\btoxic \d", r"\bcorrupted\b"]),
    "energy": (1.0, [r"\{e\}", r"\benergy counter"]),
    "experience": (0.9, [r"\bexperience counter"]),
    # Theme engines
    "aristocrats": (0.7, [
        r"\bsacrifice (?:a|another|each|any number of)? ?creature",
        r"whenever .{0,40}(?:dies|is put into a graveyard from the battlefield)",
        r"\bdevour \d", r"\bexploit\b",
    ]),
    "tokens": (0.5, [r"\bcreate[sd]? .{0,40}token", r"\bpopulate\b", r"\bamass\b"]),
    "graveyard": (0.5, [
        r"return .{0,40} from (?:your|a) graveyard", r"\bflashback\b", r"\bunearth\b",
        r"\bescape\b", r"\bdredge\b", r"\bdelve\b", r"\bdisturb\b", r"\bembalm\b",
        r"\beternalize\b", r"\bmill[sed]? ",
    ]),
    "discard": (0.6, [r"\bdiscard[s]? ", r"\bmadness\b", r"\bhellbent\b"]),
    "lifegain": (0.5, [r"\bgain[s]? .{0,12}life", r"\blifelink\b"]),
    "lifedrain": (0.6, [r"\blose[s]? .{0,12}life", r"\bextort\b", r"\bdrain"]),
    "spellslinger": (0.5, [
        r"whenever you cast .{0,30}(?:instant|sorcery|noncreature)",
        r"\bmagecraft\b", r"\bprowess\b", r"\bstorm\b",
    ]),
    "artifacts_matter": (0.5, [
        r"\baffinity\b", r"\bmetalcraft\b", r"\bimprovise\b",
        r"whenever .{0,30}artifact .{0,30}enters", r"artifact creature you control",
    ]),
    "enchantments_matter": (0.6, [
        r"\bconstellation\b", r"\benchantress\b",
        r"whenever you cast an enchantment", r"\baura\b.{0,40}attached",
    ]),
    "treasure": (0.6, [r"\btreasure token", r"\bcreate .{0,30}treasure"]),
    "landfall": (0.6, [r"\blandfall\b", r"whenever a land (?:enters|you control enters)"]),
    "vehicles": (0.7, [r"\bcrew \d", r"\bvehicle\b"]),
    "equipment": (0.6, [r"\bequip(?:ped|ment)?\b", r"\battach"]),
    "blink": (0.6, [r"\bexile .{0,50}return .{0,30}(?:to the )?battlefield"]),
    "copy_spells": (0.6, [r"\bcopy (?:target|that|a) (?:spell|instant|sorcery)"]),
    "monarch": (0.8, [r"\bmonarch\b"]),
    "attack_triggers": (0.4, [r"whenever .{0,40}attacks", r"\bmyriad\b", r"\bmelee\b"]),
    "curses": (0.9, [r"\bcurse\b.{0,30}attach", r"enchant player", r"\bcursed?\b"]),
}

# Cross-mechanic affinities: a card with mechanic K synergizes with a commander
# whose deck is about mechanic V even when texts share no phrase (proliferate works
# in ANY counter archetype; counters_generic = "a counter on it/each" phrasing).
AFFINITIES: dict[str, set[str]] = {
    "proliferate": {"minus_counters", "plus_counters", "poison", "energy", "experience"},
    "counters_generic": {"minus_counters", "plus_counters", "poison", "energy"},
    "tokens": {"aristocrats"},
    "aristocrats": {"tokens", "minus_counters"},  # persist/sac loops live in -1/-1 decks
    "lifegain": {"lifedrain"},
    "lifedrain": {"lifegain"},
}

MECHANICS["counters_generic"] = (0.6, [
    r"counters? (?:on|from) (?:it|each|target|that|all)",
    r"remove [a-z ]*counters?", r"move [a-z ]*counters?", r"with counters on them",
])

_COMPILED: dict[str, tuple[float, list[re.Pattern]]] = {
    mid: (w, [re.compile(p) for p in pats]) for mid, (w, pats) in MECHANICS.items()
}


def detect_mechanics(text: str, *, strip_reminder: bool = True) -> dict[str, float]:
    """Mechanic id -> strength (pattern hit count, capped 3) for one oracle text.

    Reminder text (parentheses) is stripped by default: "Cycling {2} (Discard this
    card...)" is a cycling card, not a discard payoff. Commander detection keeps it
    — a NEW keyword (e.g. Blight) defines its mechanic inside reminder text.
    """
    if not text:
        return {}
    t = text.lower()
    if strip_reminder:
        t = re.sub(r"\([^)]*\)", " ", t)
    found: dict[str, float] = {}
    for mid, (_w, pats) in _COMPILED.items():
        hits = sum(1 for p in pats if p.search(t))
        if hits:
            found[mid] = min(hits, 3)
    return found


def mechanic_synergy(commander_mechs: dict[str, float], card_text: str) -> float:
    """0..1: how strongly the card's mechanics overlap the commander/theme's.

    Weighted by distinctiveness — sharing minus_counters (1.0) is near-proof of
    synergy; sharing tokens (0.5) is suggestive. Normalized by the best weight the
    commander offers, so a -1/-1 commander's premier payoff can hit ~1.0.
    """
    if not commander_mechs or not card_text:
        return 0.0
    card_mechs = detect_mechanics(card_text)
    if not card_mechs:
        return 0.0
    # A card explicitly about the WRONG counter archetype (+1/+1 in a -1/-1 deck)
    # must not ride the generic-counter bridge — its counters aren't ours.
    specific = {"minus_counters", "plus_counters", "poison", "energy", "experience"}
    card_specific = specific & set(card_mechs)
    if card_specific and not (card_specific & set(commander_mechs)):
        card_mechs.pop("counters_generic", None)
    best = max(_COMPILED[m][0] for m in commander_mechs)
    score = 0.0
    for mid, strength in card_mechs.items():
        hit = 0.7 + 0.15 * min(strength, 2)  # 1 hit=0.85, 2+=1.0
        if mid in commander_mechs:
            score += _COMPILED[mid][0] * hit
        elif AFFINITIES.get(mid, set()) & set(commander_mechs):
            # related-mechanic bridge (e.g. proliferate ↔ -1/-1) at a discount
            score += _COMPILED[mid][0] * hit * 0.8
    return round(min(score / max(best, 0.01), 1.0), 4)


def commander_mechanics(commander_text: str, theme: str = "", product_description: str = "") -> dict[str, float]:
    """Mechanics that define the deck: commander oracle text is primary; the theme
    name and product blurb can add (e.g. theme 'Blight Curse' → curses)."""
    mechs = detect_mechanics(commander_text or "", strip_reminder=False)
    for extra in (theme, product_description):
        for mid, s in detect_mechanics(extra or "", strip_reminder=False).items():
            mechs[mid] = max(mechs.get(mid, 0), s * 0.5)  # softer: name/blurb only hints
    return mechs
