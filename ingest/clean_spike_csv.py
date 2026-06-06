"""
Remove invalid zero-change rows from the historical spike CSV.

TCGPlayer report exports include many 0% / $0.00 rows that are not real spikes.

Run with: python ingest/clean_spike_csv.py
          python ingest/clean_spike_csv.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SPIKE_TCGPLAYER_CSV_PATH


def _parse_pct(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_money(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def is_valid_spike_row(row) -> bool:
    pct = _parse_pct(getattr(row, "Change (%)", getattr(row, "change_pct", None)))
    usd = _parse_money(getattr(row, "Absolute Change ($)", getattr(row, "change_usd", None)))
    return pct > 0 and usd > 0


def clean_spike_csv(
    path: Path = SPIKE_TCGPLAYER_CSV_PATH,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Spike CSV not found: {path}")

    df = pd.read_csv(path)
    before = len(df)
    mask = df.apply(is_valid_spike_row, axis=1)
    cleaned = df[mask].copy()
    after = len(cleaned)
    removed = before - after

    if dry_run:
        return {"before": before, "after": after, "removed": removed, "dry_run": True}

    if backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.stem}.backup_{stamp}{path.suffix}")
        shutil.copy2(path, backup_path)
        print(f"Backup: {backup_path}")

    cleaned.to_csv(path, index=False)
    print(f"Cleaned {path.name}: {before} -> {after} rows ({removed} zero-change rows removed)")

    from backtester.spike_csv import clear_spike_cache
    from engine.historical_spike_prior import clear_historical_spike_prior_cache

    clear_spike_cache()
    clear_historical_spike_prior_cache()
    return {"before": before, "after": after, "removed": removed, "dry_run": False}


def main():
    parser = argparse.ArgumentParser(description="Remove zero-change rows from spike CSV")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    stats = clean_spike_csv(dry_run=args.dry_run, backup=not args.no_backup)
    print(stats)


if __name__ == "__main__":
    main()
