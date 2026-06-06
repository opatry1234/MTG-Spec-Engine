"""
Precon deck catalog: map spike-bible precon names to product codes (ECC, FIC, …).

Used when enriching Spike Reasoning and when matching golden benchmarks — never
match on short product codes embedded in free-text cause fields.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from config import SPIKE_PRECON_REASONING_CSV_PATH

# Rows that are not real card-level spike records.
JUNK_CARD_NAME_PATTERNS = (
    r"^other interesting winners$",
    r"^march of otherworldly light$",
    r"^brotherhood'?s end$",
)

_PRECON_QUOTED = re.compile(
    r"(?:Commander\s+)?precon\s*['\"]([^'\"]+)['\"]",
    re.I,
)
_PRECON_PAREN = re.compile(r"(?:Commander\s+)?precon\s*\(([^)]+)\)", re.I)
_COMMANDER_DECK = re.compile(
    r"Commander(?:\s+deck)?\s*:\s*([^.;,\n]+)",
    re.I,
)
# "Lorwyn Eclipsed Commander precon 'Blight Curse'"
_PRECON_NAMED = re.compile(
    r"Commander\s+precon\s*['\"]([^'\"]+)['\"]",
    re.I,
)

# Parent set name hints in cause text → commander product code (when deck name unknown).
_PARENT_SET_TO_PRODUCT: dict[str, str] = {
    "lorwyn eclipsed": "ECC",
    "aetherdrift": "DFT",
    "final fantasy": "FIC",
    "duskmourn": "DSC",
    "modern horizons 3": "MH3",
    "outlaws of thunder junction": "OTJ",
    "murders at karlov manor": "MKM",
    "the brothers' war": "BRO",
    "streets of new capenna": "SNC",
    "kamigawa: neon dynasty": "NEO",
    "innistrad: crimson vow": "VOW",
    "innistrad: midnight hunt": "MID",
    "adventures in the forgotten realms": "AFR",
    "kaldheim": "KHM",
    "zendikar rising": "ZNR",
    "march of the machine": "MOM",
    "phyrexia: all will be one": "ONE",
    "wilds of eldraine": "WOE",
    "the lost caverns of ixalan": "LCI",
    "outlaws of thunder junction": "OTJ",
}


def is_junk_card_name(card_name: str) -> bool:
    name = (card_name or "").strip().lower()
    if not name or len(name) < 3:
        return True
    return any(re.match(pat, name, re.I) for pat in JUNK_CARD_NAME_PATTERNS)


def normalize_deck_key(name: str) -> str:
    text = (name or "").strip().lower()
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@lru_cache(maxsize=1)
def build_deck_catalog() -> dict[str, str]:
    """Normalized deck name / alias → product code from commander_decks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from config import DATABASE_URL
    from db.schema import CommanderDeck

    catalog: dict[str, str] = {}
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        for deck in session.query(CommanderDeck).all():
            product = (deck.product or "").strip().upper()
            if not product:
                continue
            for raw in (deck.deck_name, deck.theme):
                if not raw:
                    continue
                catalog[normalize_deck_key(raw)] = product
                base = raw.split("(")[0].strip()
                if base:
                    catalog[normalize_deck_key(base)] = product
    finally:
        session.close()
    return catalog


def parse_precon_deck_from_cause(cause: str) -> str:
    """Extract attributed precon deck title from spike cause prose."""
    text = cause or ""
    for pattern in (_PRECON_QUOTED, _PRECON_NAMED, _PRECON_PAREN):
        match = pattern.search(text)
        if match:
            name = match.group(1).strip()
            if ":" in name and "precon" not in name.lower():
                # e.g. Aetherdrift: Eternal Might → Eternal Might
                name = name.split(":", 1)[-1].strip()
            if name and not name.lower().startswith("june "):
                return name
    match = _COMMANDER_DECK.search(text)
    if match:
        return match.group(1).strip()
    return ""


def infer_parent_set_hint(cause: str) -> str:
    text = (cause or "").lower()
    for hint, _code in sorted(_PARENT_SET_TO_PRODUCT.items(), key=lambda x: -len(x[0])):
        if hint in text:
            return hint
    return ""


def resolve_precon_set_code(
    precon_deck_name: str,
    *,
    cause: str = "",
    explicit: str = "",
) -> str:
    """
    Resolve commander product code (ECC, FIC, …) for a precon deck title.
    """
    if explicit:
        code = explicit.strip().upper()
        if len(code) >= 2:
            return code

    catalog = build_deck_catalog()
    for candidate in (precon_deck_name, parse_precon_deck_from_cause(cause)):
        if not candidate:
            continue
        key = normalize_deck_key(candidate)
        if key in catalog:
            return catalog[key]

    # "Aetherdrift: Eternal Might" in parens — try segment after colon
    if ":" in (precon_deck_name or ""):
        tail = precon_deck_name.split(":", 1)[-1].strip()
        key = normalize_deck_key(tail)
        if key in catalog:
            return catalog[key]

    hint = infer_parent_set_hint(cause)
    if hint:
        return _PARENT_SET_TO_PRODUCT.get(hint, "")

    return ""


def load_precon_reasoning_dataframe():
    """Load the focused pre-con CSV (no header row in file)."""
    import pandas as pd

    path = SPIKE_PRECON_REASONING_CSV_PATH
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path, header=None)
    df.columns = [
        "Rank",
        "Card Name",
        "Set",
        "Set Code",
        "Card Number",
        "Report Month",
        "Initial Price",
        "Final Price",
        "% Gain",
        "Spike Cause",
        "Spike Type",
        "Confidence",
        "Source",
        "Source URL",
    ]
    return df
