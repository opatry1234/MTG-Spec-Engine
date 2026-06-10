"""
Human-curated spike reasoning from Spike Data.xlsx (Spike Reasoning sheet).

Used to validate combo detection against classified spike types and to record
which partner cards were in the precon decklist (or expected in the product).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# Trailing printing-treatment annotations the spike sheet appends to card names,
# e.g. "Crumbling Ashes (Extended Art)", "Life Finds a Way (Borderless)". These
# are printing variants, not distinct oracle cards. Inclusion/omission grading is
# printing-agnostic, and predictions only ever carry the oracle name, so a golden
# benchmark must be normalized to its oracle name to be eligible and matchable.
_VARIANT_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def normalize_oracle_name(name: str) -> str:
    """Strip a trailing parenthetical printing-treatment suffix to the oracle name.

    Oracle card names never end in a parenthetical (double-faced cards use ' // '),
    so a trailing '(...)' is always a variant annotation and is safe to remove.
    """
    if not name:
        return ""
    return _VARIANT_SUFFIX_RE.sub("", name).strip()

from backtester.spike_data import (
    COMBO_DISCOVERED_LABEL,
    clear_spike_data_cache,
    is_combo_spike_type,
    load_spike_reasoning_rows,
)
from config import SPIKE_DATA_XLSX_PATH


@dataclass
class SpikeReasonRow:
    deck_name: str
    card_name: str
    spike_reason: str
    product_code: str = ""
    combo_with: list[str] = field(default_factory=list)
    notes: str = ""
    source_row: int = 0
    spike_type: str = ""
    spike_cause: str = ""
    confidence: str = ""
    source: str = ""
    source_url: str = ""
    report_month: str = ""
    report_date: Optional[date] = None
    initial_price: Optional[float] = None
    final_price: Optional[float] = None
    gain_pct: Optional[float] = None
    rank: Optional[int] = None
    set_name: str = ""
    set_code: str = ""
    card_number: str = ""
    precon_deck_name: str = ""
    precon_set_code: str = ""

    @property
    def printing_key(self) -> str:
        """Unique printing identifier: SET/code/number."""
        if self.set_code and self.card_number:
            return f"{self.set_code.upper()}/{self.card_number}"
        return self.card_name

    @property
    def is_combo_discovered(self) -> bool:
        if COMBO_DISCOVERED_LABEL in (self.spike_reason or "").lower():
            return True
        return is_combo_spike_type(self.spike_type)


def load_spike_reasoning(path: Optional[Path] = None) -> list[SpikeReasonRow]:
    """Load all rows from the Spike Reasoning sheet."""
    return load_spike_reasoning_rows(path or SPIKE_DATA_XLSX_PATH)


def load_combo_discovered_rows(path: Optional[Path] = None) -> list[SpikeReasonRow]:
    return [r for r in load_spike_reasoning(path) if r.is_combo_discovered]


def reasoning_stats(path: Optional[Path] = None) -> dict:
    target = path or SPIKE_DATA_XLSX_PATH
    rows = load_spike_reasoning(target)
    combo = [r for r in rows if r.is_combo_discovered]
    return {
        "path": str(target),
        "loaded": bool(rows),
        "total_rows": len(rows),
        "combo_discovered_rows": len(combo),
        "decks": sorted({r.deck_name for r in combo if r.deck_name}),
        "spike_types": sorted({r.spike_type for r in rows if r.spike_type}),
    }


def clear_reasoning_cache() -> None:
    clear_spike_data_cache()


def _row_matches_deck(
    row: SpikeReasonRow,
    deck_name: str,
    product_code: str,
    commander_name: str,
) -> bool:
    """Return True if this reasoning row is attributed to the given deck.

    Uses structured Pre-con Deck Name / Pre-con Set Code columns only.
    Does not match on short product codes or substrings inside Spike Cause text
    (avoids false positives like "SOC" inside unrelated words).
    """
    from backtester.spike_precon_catalog import normalize_deck_key

    product_lower = (product_code or "").strip().upper()
    deck_key = normalize_deck_key(deck_name)
    commander_lower = (commander_name or "").strip().lower()

    row_precon_code = (row.precon_set_code or row.product_code or "").strip().upper()
    row_has_deck_name = bool((row.precon_deck_name or "").strip())
    row_precon_deck = normalize_deck_key(row.precon_deck_name or row.deck_name)

    # 1. Pre-con deck title match (most specific). When the sheet names a deck,
    #    attribute the spike ONLY to that deck — never let a deck-specific row
    #    bleed into sibling decks of the same product via the code rule below.
    if deck_key and row_precon_deck:
        if deck_key == row_precon_deck or deck_key in row_precon_deck or row_precon_deck in deck_key:
            return True

    # 2. Pre-con set code (ECC, FIC, DFT, …) — must be ≥3 chars. Product-level
    #    attribution applies only to rows WITHOUT a specific deck name; otherwise
    #    a row tagged to one deck would match every deck in the product.
    if (
        not row_has_deck_name
        and product_lower
        and row_precon_code
        and len(row_precon_code) >= 3
        and row_precon_code == product_lower
    ):
        return True

    # 3. Commander name only when also tied to same product code in sheet
    if (
        commander_lower
        and len(commander_lower) > 10
        and commander_lower in (row.spike_cause or row.spike_reason or "").lower()
        and product_lower
        and row_precon_code == product_lower
    ):
        return True

    return False


def find_reasoning_golden_benchmarks(
    deck_name: str,
    product_code: str,
    commander_name: str = "",
    *,
    path: Optional[Path] = None,
) -> list[dict]:
    """Return golden benchmark dicts from the human-curated Spike Reasoning sheet.

    Entries with a pre-spike price above PRICE_GATE_USD are excluded: the spike was
    real, but it isn't a BUYABLE spec under the user's price discipline (no buying
    $26 cards hoping for $50), and the engine's $10 prediction gate matches that.
    Benchmarks must measure what we'd actually buy.

    Returns dicts in the same format expected by grade_spec_targets / golden_rows.
    """
    from backtester.spike_precon_catalog import is_junk_card_name
    from config import PRICE_GATE_USD

    rows = load_spike_reasoning(path)
    results = []
    seen: set[str] = set()
    for row in rows:
        if not row.card_name or is_junk_card_name(row.card_name):
            continue
        if not _row_matches_deck(row, deck_name, product_code, commander_name):
            continue
        if row.initial_price is not None and row.initial_price > PRICE_GATE_USD:
            continue  # not buyable pre-spike → not a graded target
        # Printing-agnostic: normalize "Card (Extended Art)" -> "Card" so the
        # benchmark resolves to a real oracle card (eligibility) and can match an
        # oracle-named prediction. Dedup repeated/variant rows for the same card.
        oracle_name = normalize_oracle_name(row.card_name)
        if not oracle_name or oracle_name.lower() in seen:
            continue
        seen.add(oracle_name.lower())
        results.append({
            "card_name": oracle_name,
            "precon_attributed": True,
            "attribution": "spike_reasoning_sheet",
            "spike_pct": round(row.gain_pct, 3) if row.gain_pct is not None else None,
            "spike_usd": (
                round(row.final_price - row.initial_price, 2)
                if row.final_price is not None and row.initial_price is not None
                else None
            ),
            "baseline_price": row.initial_price,
            "peak_price": row.final_price,
            "report_date": row.report_date.isoformat() if row.report_date else None,
            "set_name": row.set_name or "",
            "set_code": row.set_code or "",
            "collector_number": row.card_number or "",
            "printing_key": row.printing_key,
            "spike_reason": row.spike_reason or "",
            "spike_type": row.spike_type or "",
            "synergy_fit": None,
            "is_alternate_commander": False,
            "earliest_printing": None,
            "spec_eligible": True,
            "in_top_predictions": False,
        })
    return results
