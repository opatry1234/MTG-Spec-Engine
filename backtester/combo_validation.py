"""
Validate Commander Spellbook combo detection against spike-reasoning rows.

Compares infinite-loop detection for cards labeled "Combo Discovered" in the
human spike reasoning spreadsheet, using anchor cards from the precon decklist
and/or the model's expected probable deck.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from backtester.deck_anchors import (
    commander_and_decklist_anchors,
    expected_precon_anchors,
    list_anchor_cards,
    resolve_deck,
)
from backtester.spike_reasoning import SpikeReasonRow, load_combo_discovered_rows
from db.schema import CommanderDeck
from engine.combo_checker import ComboChecker, ComboLoopInfo, get_combo_checker


@dataclass
class ComboValidationRow:
    deck_name: str
    card_name: str
    spike_reason: str
    combo_with_sheet: list[str]
    anchor_mode: str
    detected_loop: bool
    detected_partners: list[str]
    sheet_partners_in_anchors: list[str]
    sheet_partners_detected: list[str]
    match_sheet: bool
    notes: str = ""
    source_row: int = 0

    def to_dict(self) -> dict:
        return {
            "deck_name": self.deck_name,
            "card_name": self.card_name,
            "spike_reason": self.spike_reason,
            "combo_with_sheet": "; ".join(self.combo_with_sheet),
            "anchor_mode": self.anchor_mode,
            "detected_loop": self.detected_loop,
            "detected_partners": "; ".join(self.detected_partners),
            "sheet_partners_in_anchors": "; ".join(self.sheet_partners_in_anchors),
            "sheet_partners_detected": "; ".join(self.sheet_partners_detected),
            "match_sheet": self.match_sheet,
            "notes": self.notes,
            "source_row": self.source_row,
        }


def _norm_key(name: str) -> str:
    """Loose match: ignore case, punctuation, and spacing."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def _norm_set(names) -> set[str]:
    return {_norm_key(n) for n in names if (n or "").strip()}


def _display_names(anchors: set[str], keys: set[str]) -> list[str]:
    by_norm = {_norm_key(a): a for a in anchors}
    return sorted(by_norm[k] for k in keys if k in by_norm)


def _spellbook_name(card_name: str, anchors: set[str]) -> str:
    """Prefer anchor spelling for Spellbook API when the sheet name is a loose match."""
    key = _norm_key(card_name)
    for anchor in anchors:
        if _norm_key(anchor) == key:
            return anchor
    return card_name


def validate_combo_row(
    row: SpikeReasonRow,
    anchors: set[str],
    *,
    anchor_mode: str,
    checker: Optional[ComboChecker] = None,
) -> ComboValidationRow:
    checker = checker or get_combo_checker()
    query_name = _spellbook_name(row.card_name, anchors)
    info: ComboLoopInfo = checker.loop_with_anchors(query_name, anchors)

    anchor_norm = _norm_set(anchors)
    sheet_norm = _norm_set(row.combo_with)
    detected_norm = _norm_set(info.loop_partners)

    in_anchors = sheet_norm & anchor_norm
    detected_from_sheet = in_anchors & detected_norm

    match = True
    notes_parts = []
    if row.combo_with:
        missing_in_deck = sheet_norm - anchor_norm
        if missing_in_deck:
            notes_parts.append(
                "sheet partner not in anchors: "
                + ", ".join(_display_names(set(row.combo_with), missing_in_deck))
            )
            match = False
        if in_anchors and not detected_from_sheet:
            notes_parts.append(
                "in deck but not detected: "
                + ", ".join(_display_names(anchors, in_anchors - detected_norm))
            )
            match = False
    elif info.has_infinite_loop:
        notes_parts.append("detected loop; sheet did not list partners")
    elif not info.has_infinite_loop:
        notes_parts.append("no loop detected with current anchors")

    return ComboValidationRow(
        deck_name=row.deck_name,
        card_name=row.card_name,
        spike_reason=row.spike_reason,
        combo_with_sheet=list(row.combo_with),
        anchor_mode=anchor_mode,
        detected_loop=info.has_infinite_loop,
        detected_partners=list(info.loop_partners),
        sheet_partners_in_anchors=_display_names(anchors, in_anchors),
        sheet_partners_detected=_display_names(anchors, detected_from_sheet),
        match_sheet=match,
        notes="; ".join(notes_parts),
        source_row=row.source_row,
    )


def validate_deck_combo_discovered(
    session: Session,
    deck: CommanderDeck,
    reasoning_rows: Optional[list[SpikeReasonRow]] = None,
    *,
    anchor_mode: str = "decklist",
    models: Optional[dict] = None,
    checker: Optional[ComboChecker] = None,
    fetch_live: bool = True,
) -> dict:
    """
    Validate all combo-discovered reasoning rows for one precon.

    anchor_mode: 'decklist' | 'expected' | 'both' (runs validation twice for 'both')
    """
    if reasoning_rows is None:
        reasoning_rows = load_combo_discovered_rows()

    deck_key = (deck.deck_name or "").strip().lower()
    product_key = (deck.product or "").strip().lower()

    def _row_matches_deck(r: SpikeReasonRow) -> bool:
        r_deck = r.deck_name.strip().lower()
        r_prod = r.product_code.strip().lower()
        if deck_key and r_deck == deck_key:
            return True
        if product_key and r_prod == product_key:
            return True
        return False

    deck_rows = [r for r in reasoning_rows if _row_matches_deck(r)]

    modes = ["decklist", "expected"] if anchor_mode == "both" else [anchor_mode]
    all_results: list[ComboValidationRow] = []

    for mode in modes:
        if mode == "decklist":
            anchors = commander_and_decklist_anchors(session, deck)
        else:
            anchors = expected_precon_anchors(
                session,
                deck,
                models=models,
            )

        chk = checker or ComboChecker(fetch_live=fetch_live)
        for row in deck_rows:
            all_results.append(validate_combo_row(row, anchors, anchor_mode=mode, checker=chk))

    df = pd.DataFrame([r.to_dict() for r in all_results])
    matched = int(df["match_sheet"].sum()) if not df.empty and "match_sheet" in df.columns else 0
    detected = int(df["detected_loop"].sum()) if not df.empty else 0

    return {
        "deck_id": deck.id,
        "deck_name": deck.deck_name,
        "product": deck.product,
        "commander": deck.commander_name,
        "anchor_mode": anchor_mode,
        "combo_discovered_rows": len(deck_rows),
        "validation_rows": len(all_results),
        "detected_loops": detected,
        "sheet_matches": matched,
        "results": all_results,
        "results_df": df,
        "anchor_cards": list_anchor_cards(
            session,
            deck,
            anchor_mode="both" if anchor_mode == "both" else anchor_mode,
            models=models,
        ),
    }


def validate_all_combo_discovered(
    session: Session,
    *,
    anchor_mode: str = "decklist",
    reasoning_path=None,
    models: Optional[dict] = None,
    fetch_live: bool = True,
) -> pd.DataFrame:
    """Run combo validation for every combo-discovered row with a resolvable deck."""
    rows = load_combo_discovered_rows(reasoning_path)
    if not rows:
        return pd.DataFrame()

    seen_decks: dict[str, CommanderDeck] = {}
    all_dfs = []

    def _row_key(r: SpikeReasonRow) -> str:
        return f"{r.deck_name.strip().lower()}|{r.product_code.strip().lower()}"

    for row in rows:
        key = _row_key(row)
        if key == "|":
            continue
        if key not in seen_decks:
            deck = resolve_deck(
                session,
                deck_name=row.deck_name,
                product_code=row.product_code,
            )
            if deck is None:
                continue
            seen_decks[key] = deck

        deck = seen_decks[key]
        report = validate_deck_combo_discovered(
            session,
            deck,
            reasoning_rows=[r for r in rows if _row_key(r) == key],
            anchor_mode=anchor_mode,
            models=models,
            fetch_live=fetch_live,
        )
        if not report["results_df"].empty:
            all_dfs.append(report["results_df"])

    if not all_dfs:
        return pd.DataFrame()
    return pd.concat(all_dfs, ignore_index=True)


def scan_decklist_combo_candidates(
    session: Session,
    deck: CommanderDeck,
    *,
    anchor_mode: str = "decklist",
    models: Optional[dict] = None,
    checker: Optional[ComboChecker] = None,
    card_limit: int = 80,
) -> pd.DataFrame:
    """
    For each anchor card in the decklist/expected pool, report Spellbook infinite
    partners that are also in the anchor pool (in-deck combo lines).
    """
    anchors_list = list_anchor_cards(
        session, deck, anchor_mode=anchor_mode, models=models
    )
    anchor_names = {a["card_name"] for a in anchors_list}
    chk = checker or ComboChecker()

    records = []
    checked = 0
    for entry in anchors_list:
        if checked >= card_limit:
            break
        name = entry["card_name"]
        info = chk.loop_with_anchors(name, anchor_names)
        checked += 1
        if not info.has_infinite_loop:
            continue
        records.append(
            {
                "anchor_card": name,
                "in_actual_decklist": entry["in_actual_decklist"],
                "in_expected_precon": entry["in_expected_precon"],
                "is_commander": entry["is_commander"],
                "combo_with": info.combo_with,
                "partner_count": len(info.loop_partners),
            }
        )

    return pd.DataFrame(records)
