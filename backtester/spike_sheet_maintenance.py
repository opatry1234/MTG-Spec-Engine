"""
Enrich and clean Spike Data.xlsx (Spike Reasoning sheet).

Adds:
  - Pre-con Deck Name
  - Pre-con Set Code

Removes junk rows and backfills from Spike Data - Pre-Con Spike Reasoning.csv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from backtester.spike_precon_catalog import (
    is_junk_card_name,
    load_precon_reasoning_dataframe,
    parse_precon_deck_from_cause,
    resolve_precon_set_code,
)
from config import SPIKE_DATA_XLSX_PATH, SPIKE_REASONING_SHEET

PRECON_DECK_COL = "Pre-con Deck Name"
PRECON_SET_COL = "Pre-con Set Code"


def _enrich_row(cause: str, existing_deck: str, existing_code: str) -> tuple[str, str]:
    deck = (existing_deck or "").strip() or parse_precon_deck_from_cause(cause)
    code = resolve_precon_set_code(deck, cause=cause, explicit=existing_code)
    return deck, code


def enrich_reasoning_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    if PRECON_DECK_COL not in out.columns:
        out[PRECON_DECK_COL] = ""
    if PRECON_SET_COL not in out.columns:
        out[PRECON_SET_COL] = ""

    for idx, row in out.iterrows():
        card = str(row.get("Card Name", "") or "").strip()
        if is_junk_card_name(card):
            continue
        cause = str(row.get("Spike Cause", "") or row.get("Spike Reason", "") or "")
        deck, code = _enrich_row(
            cause,
            str(row.get(PRECON_DECK_COL, "") or ""),
            str(row.get(PRECON_SET_COL, "") or ""),
        )
        out.at[idx, PRECON_DECK_COL] = deck
        out.at[idx, PRECON_SET_COL] = code

    return out


def merge_precon_csv_into_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Overlay pre-con CSV rows (authoritative for precon attribution)."""
    pc = load_precon_reasoning_dataframe()
    if pc.empty or df.empty:
        return df

    out = df.copy()
    if PRECON_DECK_COL not in out.columns:
        out[PRECON_DECK_COL] = ""
    if PRECON_SET_COL not in out.columns:
        out[PRECON_SET_COL] = ""

    # Index existing by card + cause prefix for dedupe
    for _, row in pc.iterrows():
        card = str(row.get("Card Name", "")).strip()
        if is_junk_card_name(card):
            continue
        cause = str(row.get("Spike Cause", "")).strip()
        deck, code = _enrich_row(cause, "", "")

        mask = out["Card Name"].astype(str).str.strip() == card
        if mask.any():
            for idx in out.index[mask]:
                if not str(out.at[idx, PRECON_DECK_COL]).strip():
                    out.at[idx, PRECON_DECK_COL] = deck
                if not str(out.at[idx, PRECON_SET_COL]).strip():
                    out.at[idx, PRECON_SET_COL] = code
        else:
            new_row = {col: "" for col in out.columns}
            new_row.update(
                {
                    "Card Name": card,
                    "Set": row.get("Set", ""),
                    "Set Code": row.get("Set Code", ""),
                    "Card Number": row.get("Card Number", ""),
                    "Report Month": row.get("Report Month", ""),
                    "Initial Price": row.get("Initial Price", ""),
                    "Final Price": row.get("Final Price", ""),
                    "% Gain": row.get("% Gain", ""),
                    "Spike Cause": cause,
                    "Spike Type": row.get("Spike Type", ""),
                    "Confidence": row.get("Confidence", ""),
                    "Source": row.get("Source", ""),
                    "Source URL": row.get("Source URL", ""),
                    PRECON_DECK_COL: deck,
                    PRECON_SET_COL: code,
                }
            )
            out = pd.concat([out, pd.DataFrame([new_row])], ignore_index=True)

    return out


def remove_junk_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df.empty or "Card Name" not in df.columns:
        return df, 0
    mask = df["Card Name"].astype(str).apply(lambda n: not is_junk_card_name(n))
    removed = int((~mask).sum())
    return df[mask].reset_index(drop=True), removed


def enrich_spike_workbook(
    path: Optional[Path] = None,
    *,
    write: bool = True,
) -> dict:
    xlsx = path or SPIKE_DATA_XLSX_PATH
    if not xlsx.exists():
        raise FileNotFoundError(xlsx)

    xl = pd.ExcelFile(xlsx)
    sheets = {name: pd.read_excel(xlsx, sheet_name=name) for name in xl.sheet_names}
    df = sheets.get(SPIKE_REASONING_SHEET, pd.DataFrame())

    before = len(df)
    df, junk_removed = remove_junk_rows(df)
    df = merge_precon_csv_into_dataframe(df)
    df = enrich_reasoning_dataframe(df)
    sheets[SPIKE_REASONING_SHEET] = df

    if write:
        with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as writer:
            for name, frame in sheets.items():
                frame.to_excel(writer, sheet_name=name, index=False)

    from backtester.spike_data import clear_spike_data_cache
    from backtester.spike_reasoning import clear_reasoning_cache
    from engine.historical_spike_prior import clear_historical_spike_prior_cache

    clear_spike_data_cache()
    clear_reasoning_cache()
    clear_historical_spike_prior_cache()

    filled_deck = int((df[PRECON_DECK_COL].astype(str).str.strip() != "").sum()) if not df.empty else 0
    filled_code = int((df[PRECON_SET_COL].astype(str).str.strip() != "").sum()) if not df.empty else 0

    return {
        "path": str(xlsx),
        "rows_before": before,
        "rows_after": len(df),
        "junk_removed": junk_removed,
        "precon_deck_filled": filled_deck,
        "precon_set_code_filled": filled_code,
    }
