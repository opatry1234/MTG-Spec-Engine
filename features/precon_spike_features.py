"""
Mechanical features derived from the precon spike bible (types + cause corpus).

These encode *why* cards tend to spike (synergy, combo, hate, scarcity) without
using same-deck golden labels at inference time — the corpus is global.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from features.nlp import tfidf_similarity

# Spike Type → mechanic keyword probes (from Pre-Con Reasoning CSV taxonomy).
SPIKE_TYPE_MECHANIC_PROBES: dict[str, list[str]] = {
    "new commander precon synergy": [
        "-1/-1 counter",
        "+1/+1 counter",
        "proliferate",
        "sacrifice",
        "enters the battlefield",
        "when you cast",
        "from your graveyard",
        "token",
    ],
    "new commander precon": ["commander", "precon", "deck"],
    "combo discovery": ["copy", "infinite", "untap", "mana", "combo", "loop"],
    "commander precon upgrade": ["reprint", "counter", "attack", "destroy"],
    "tv show hype": ["proliferate", "legendary", "planeswalker"],
    "hate card": ["counter", "remove all", "destroy all", "exile all"],
}

PRECON_CAUSE_MECHANIC_TERMS = [
    "precon upgrade",
    "-1/-1 counter",
    "+1/+1 counter",
    "proliferate",
    "sacrifice",
    "discard",
    "zombie",
    "token",
    "reprint",
    "single printing",
    "combo",
    "infinite",
    "missed reprint",
    "blight",
    "with er",
    "infect",
]


def normalize_spike_type(spike_type: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (spike_type or "").lower()).strip()


def spike_type_mechanic_score(oracle_text: str, spike_type: str = "") -> float:
    """
    Score how well card oracle text matches mechanics implied by a spike type.
    When spike_type is empty, use the best match across precon-related types.
    """
    if not oracle_text:
        return 0.0
    text = oracle_text.lower()
    types_to_check = [normalize_spike_type(spike_type)] if spike_type else []
    if not types_to_check or not types_to_check[0]:
        types_to_check = [
            "new commander precon synergy",
            "combo discovery",
            "commander precon upgrade",
        ]

    best = 0.0
    for st in types_to_check:
        probes = SPIKE_TYPE_MECHANIC_PROBES.get(st, [])
        if not probes:
            for key, vals in SPIKE_TYPE_MECHANIC_PROBES.items():
                if st in key or key in st:
                    probes = vals
                    break
        if not probes:
            continue
        hits = sum(1 for p in probes if p in text)
        best = max(best, hits / max(len(probes), 1))
    return min(best, 1.0)


@lru_cache(maxsize=1)
def _precon_cause_corpus() -> str:
    from backtester.spike_precon_catalog import load_precon_reasoning_dataframe

    df = load_precon_reasoning_dataframe()
    if df.empty:
        from backtester.spike_data import load_reasoning_dataframe

        df = load_reasoning_dataframe()
    if df.empty:
        return ""
    col = "Spike Cause" if "Spike Cause" in df.columns else "spike_cause"
    parts = df[col].dropna().astype(str).tolist()[:400]
    return " ".join(parts)


def precon_cause_similarity(oracle_text: str, theme_text: str = "") -> float:
    """TF-IDF similarity of card+theme text to aggregated precon spike cause corpus."""
    corpus = _precon_cause_corpus()
    if not corpus:
        return 0.0
    query = " ".join(filter(None, [oracle_text or "", theme_text or ""])).strip()
    if not query:
        return 0.0
    sims = tfidf_similarity(query, [corpus])
    return float(sims[0]) if sims else 0.0


def mechanic_keyword_density(oracle_text: str) -> float:
    if not oracle_text:
        return 0.0
    text = oracle_text.lower()
    hits = sum(1 for term in PRECON_CAUSE_MECHANIC_TERMS if term in text)
    return min(hits / 6.0, 1.0)


def single_printing_flag(num_printings: int) -> float:
    return 1.0 if num_printings == 1 else 0.0


def compute_precon_spike_features(
    *,
    oracle_text: str,
    theme_text: str = "",
    num_printings: int = 0,
    spike_type_hint: str = "",
) -> dict[str, float]:
    return {
        "spike_type_mechanic_score": spike_type_mechanic_score(
            oracle_text, spike_type_hint
        ),
        "precon_cause_similarity": precon_cause_similarity(oracle_text, theme_text),
        "mechanic_keyword_density": mechanic_keyword_density(oracle_text),
        "single_printing_flag": single_printing_flag(num_printings),
    }
