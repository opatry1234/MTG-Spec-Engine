"""
Historical price spike CSV index (local spreadsheet, no API).

Loads tcgplayer-style spike reports and matches oracle card names to
report dates near a deck's release_date and optional precon_release_date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence

import pandas as pd

from backtester.precon_products import resolve_commander_spike_set
from backtester.spike_attribution import (
    announcement_window,
    attribution_label,
    is_loose_omission_spike,
    is_precon_attributed_spike,
)
from config import (
    SPIKE_CSV_PATH,
    SPIKE_MIN_ABSOLUTE_USD,
    SPIKE_MIN_RELATIVE_PCT,
    SPIKE_PEAK_END_DAYS,
    SPIKE_PEAK_START_DAYS,
)

if TYPE_CHECKING:
    from engine.deck_synergy import DeckSynergyContext


@dataclass(frozen=True)
class SpikeRecord:
    oracle_name: str
    product_name: str
    set_name: str
    report_date: date
    change_pct: float
    change_usd: float
    initial_price: Optional[float]
    final_price: Optional[float]
    set_code: str = ""
    collector_number: str = ""

    def printing_key(self) -> str:
        code = (self.set_code or "").strip().upper()
        number = str(self.collector_number or "").strip()
        if code and number and number.lower() != "nan":
            return f"{code}/{number}"
        return ""

    def benchmark_key(self) -> str:
        pk = self.printing_key()
        if pk:
            return f"{self.oracle_name.lower()}|{pk}"
        return self.oracle_name.lower()


def normalize_printing_key(set_code: str, collector_number: str) -> str:
    code = (set_code or "").strip().upper()
    number = str(collector_number or "").strip()
    if code and number and number.lower() != "nan":
        return f"{code}/{number}"
    return ""


def record_matches_printing(
    record: SpikeRecord,
    *,
    set_code: Optional[str] = None,
    collector_number: Optional[str] = None,
) -> bool:
    if not set_code or not collector_number:
        return False
    if not record.printing_key():
        return False
    return record.printing_key() == normalize_printing_key(set_code, collector_number)


def spike_lookup_keys(card_name: str) -> set[str]:
    keys = {card_name.lower(), normalize_oracle_name(card_name).lower()}
    for alias in oracle_name_aliases(card_name):
        keys.add(alias.lower())
    return keys


def _record_identity(rec: SpikeRecord) -> tuple:
    return (
        rec.report_date,
        rec.oracle_name.lower(),
        rec.printing_key() or rec.set_name.lower(),
        rec.initial_price,
        rec.final_price,
    )


def collect_spike_candidates(
    index: dict[str, List[SpikeRecord]],
    card_name: str,
    *,
    set_code: Optional[str] = None,
    collector_number: Optional[str] = None,
) -> List[SpikeRecord]:
    seen: set[tuple] = set()
    candidates: List[SpikeRecord] = []
    for key in spike_lookup_keys(card_name):
        for rec in index.get(key, []):
            ident = _record_identity(rec)
            if ident in seen:
                continue
            seen.add(ident)
            candidates.append(rec)

    if set_code and collector_number:
        exact = [
            rec
            for rec in candidates
            if record_matches_printing(
                rec, set_code=set_code, collector_number=collector_number
            )
        ]
        if exact:
            return exact
    return candidates


def _spike_rank_key(rec: SpikeRecord) -> tuple:
    return (bool(rec.printing_key()), rec.change_pct, rec.change_usd)


def normalize_oracle_name(product_name: str) -> str:
    name = (product_name or "").strip().strip('"')
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return name


def oracle_name_aliases(product_name: str) -> List[str]:
    raw = (product_name or "").strip().strip('"')
    aliases: List[str] = []
    seen = set()

    def add(name: str) -> None:
        cleaned = normalize_oracle_name(name)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            aliases.append(cleaned)

    add(raw)
    if " - " in raw:
        for part in raw.split(" - "):
            add(part.strip())
    return aliases


def _parse_pct(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip().replace("%", "")
    try:
        return float(text) / 100.0
    except ValueError:
        return 0.0


def _parse_money(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def meets_spike_threshold(change_pct: float, change_usd: float) -> bool:
    return change_pct >= SPIKE_MIN_RELATIVE_PCT and change_usd >= SPIKE_MIN_ABSOLUTE_USD


def is_valid_spike_row(change_pct: float, change_usd: float) -> bool:
    """Reject zero-change report rows that are not real price movement."""
    return change_pct > 0 and change_usd > 0


def _load_legacy_spike_csv(csv_path: Path) -> List[SpikeRecord]:
    df = pd.read_csv(csv_path)
    df = df.rename(
        columns={
            "Report Month": "report_month",
            "Report Date": "report_date",
            "Product Name": "product_name",
            "Set Name": "set_name",
            "Initial Market Price": "initial_price",
            "Final Market Price": "final_price",
            "Absolute Change ($)": "change_usd",
            "Change (%)": "change_pct",
        }
    )

    records: List[SpikeRecord] = []

    for row in df.itertuples(index=False):
        product = getattr(row, "product_name", None)
        if not product or pd.isna(product):
            continue

        report_date = pd.to_datetime(getattr(row, "report_date", None), errors="coerce")
        if pd.isna(report_date):
            continue

        change_pct = _parse_pct(getattr(row, "change_pct", None))
        change_usd = _parse_money(getattr(row, "change_usd", None)) or 0.0
        if not is_valid_spike_row(change_pct, change_usd):
            continue

        primary = oracle_name_aliases(str(product))[0]

        records.append(
            SpikeRecord(
                oracle_name=primary,
                product_name=str(product).strip().strip('"'),
                set_name=str(getattr(row, "set_name", "") or ""),
                report_date=report_date.date(),
                change_pct=change_pct,
                change_usd=change_usd,
                initial_price=_parse_money(getattr(row, "initial_price", None)),
                final_price=_parse_money(getattr(row, "final_price", None)),
            )
        )

    return records


def load_spike_csv(path: Optional[Path] = None) -> List[SpikeRecord]:
    csv_path = path or SPIKE_CSV_PATH
    if not csv_path.exists():
        return []

    if csv_path.suffix.lower() in {".xlsx", ".xlsm"}:
        from backtester.spike_data import load_spike_records_from_bible

        return load_spike_records_from_bible(csv_path)

    return _load_legacy_spike_csv(csv_path)


def clear_spike_cache() -> None:
    get_spike_index.cache_clear()
    get_spike_printing_index.cache_clear()
    get_spike_records_by_date.cache_clear()
    _spike_counts_by_date.cache_clear()
    from backtester.spike_data import clear_spike_data_cache

    clear_spike_data_cache()


@lru_cache(maxsize=1)
def get_spike_records_by_date(path: Optional[str] = None) -> dict[date, List[SpikeRecord]]:
    by_date: dict[date, List[SpikeRecord]] = {}
    for rec in load_spike_csv(Path(path) if path else None):
        by_date.setdefault(rec.report_date, []).append(rec)
    return by_date


def iter_spikes_in_window(
    window_start: date,
    window_end: date,
    path: Optional[str] = None,
):
    """Yield spike records whose report_date falls in [window_start, window_end]."""
    by_date = get_spike_records_by_date(str(path) if path else None)
    for report_date in sorted(by_date.keys()):
        if report_date < window_start:
            continue
        if report_date > window_end:
            break
        for rec in by_date[report_date]:
            yield rec


@lru_cache(maxsize=1)
def _spike_counts_by_date(path: Optional[str] = None) -> dict[date, int]:
    counts: dict[date, int] = {}
    for rec in load_spike_csv(Path(path) if path else None):
        if meets_spike_threshold(rec.change_pct, rec.change_usd):
            counts[rec.report_date] = counts.get(rec.report_date, 0) + 1
    return counts


def count_qualifying_spikes_on_date(report_date: date, path: Optional[str] = None) -> int:
    """How many qualifying spike rows share a report date (batch noise signal)."""
    return _spike_counts_by_date(str(path) if path else None).get(report_date, 0)


@lru_cache(maxsize=1)
def get_spike_index(path: Optional[str] = None) -> dict[str, List[SpikeRecord]]:
    records = load_spike_csv(Path(path) if path else None)
    index: dict[str, List[SpikeRecord]] = {}
    for rec in records:
        for alias in oracle_name_aliases(rec.product_name):
            key = alias.lower()
            index.setdefault(key, []).append(rec)
    return index


@lru_cache(maxsize=1)
def get_spike_printing_index(path: Optional[str] = None) -> dict[str, List[SpikeRecord]]:
    by_printing: dict[str, List[SpikeRecord]] = {}
    for rec in load_spike_csv(Path(path) if path else None):
        pk = rec.printing_key()
        if not pk:
            continue
        by_printing.setdefault(pk, []).append(rec)
    return by_printing


def release_anchors(
    release_date: date,
    precon_release_date: Optional[date] = None,
) -> List[date]:
    anchors = [release_date]
    if precon_release_date and precon_release_date != release_date:
        anchors.append(precon_release_date)
    return anchors


def spike_window(
    reveal_date: date,
    precon_release_date: Optional[date] = None,
) -> tuple[date, date]:
    """Spike window anchored to decklist reveal date (precon arg ignored)."""
    del precon_release_date
    start = reveal_date + timedelta(days=SPIKE_PEAK_START_DAYS)
    end = reveal_date + timedelta(days=SPIKE_PEAK_END_DAYS)
    return start, end


def _qualifying_spikes(matches: Sequence[SpikeRecord]) -> List[SpikeRecord]:
    return [
        r
        for r in matches
        if meets_spike_threshold(r.change_pct, r.change_usd)
    ]


def _lookup_card_colors(
    card_name: str,
    color_map: Optional[dict[str, list]],
) -> Optional[list]:
    if not color_map or not card_name:
        return None
    keys = {card_name.lower(), normalize_oracle_name(card_name).lower()}
    for alias in oracle_name_aliases(card_name):
        keys.add(alias.lower())
    for key in keys:
        if key in color_map:
            return color_map[key]
    return None


def _lookup_card(
    card_name: str,
    cards_by_name: Optional[dict[str, object]],
):
    if not cards_by_name or not card_name:
        return None
    keys = {card_name.lower(), normalize_oracle_name(card_name).lower()}
    for alias in oracle_name_aliases(card_name):
        keys.add(alias.lower())
    for key in keys:
        card = cards_by_name.get(key)
        if card is not None:
            return card
    return None


def _synergy_context_for_card(
    card_name: str,
    *,
    deck_synergy_ctx: Optional["DeckSynergyContext"],
    cards_by_name: Optional[dict[str, object]],
) -> tuple[Optional[float], bool, Optional[object]]:
    if deck_synergy_ctx is None or cards_by_name is None:
        return None, False, None
    card = _lookup_card(card_name, cards_by_name)
    if card is None:
        return None, False, None
    from engine.deck_synergy import compute_synergy_fit, is_alt_commander_for_deck

    synergy = compute_synergy_fit(card, deck_synergy_ctx)
    is_alt = is_alt_commander_for_deck(card, deck_synergy_ctx, synergy)
    return synergy, is_alt, card


def _filter_attributed(
    records: Sequence[SpikeRecord],
    release_date: date,
    *,
    precon_release_date: Optional[date],
    commander_spike_set: Optional[str],
    require_precon_attributed: bool,
    deck_colors: Optional[list] = None,
    card_color_map: Optional[dict[str, list]] = None,
    deck_synergy_ctx: Optional["DeckSynergyContext"] = None,
    cards_by_name: Optional[dict[str, object]] = None,
) -> List[SpikeRecord]:
    if not require_precon_attributed:
        return list(records)
    out = []
    for r in records:
        synergy, is_alt, card = _synergy_context_for_card(
            r.oracle_name,
            deck_synergy_ctx=deck_synergy_ctx,
            cards_by_name=cards_by_name,
        )
        if is_precon_attributed_spike(
            r.report_date,
            r.set_name,
            release_date,
            precon_release_date=precon_release_date,
            commander_spike_set=commander_spike_set,
            card_name=r.oracle_name,
            deck_colors=deck_colors,
            card_color_identity=_lookup_card_colors(r.oracle_name, card_color_map),
            synergy_fit=synergy,
            is_alt_commander=is_alt,
            card=card,
        ):
            out.append(r)
    return out


def _spike_result(
    best: SpikeRecord,
    *,
    release_date: date,
    precon_release_date: Optional[date],
    commander_spike_set: Optional[str],
    window_start: date,
    window_end: date,
    spike_matches: int,
    deck_colors: Optional[list] = None,
    card_color_map: Optional[dict[str, list]] = None,
    deck_synergy_ctx: Optional["DeckSynergyContext"] = None,
    cards_by_name: Optional[dict[str, object]] = None,
) -> dict:
    card_colors = _lookup_card_colors(best.oracle_name, card_color_map)
    synergy, is_alt, card = _synergy_context_for_card(
        best.oracle_name,
        deck_synergy_ctx=deck_synergy_ctx,
        cards_by_name=cards_by_name,
    )
    attributed = is_precon_attributed_spike(
        best.report_date,
        best.set_name,
        release_date,
        precon_release_date=precon_release_date,
        commander_spike_set=commander_spike_set,
        card_name=best.oracle_name,
        deck_colors=deck_colors,
        card_color_identity=card_colors,
        synergy_fit=synergy,
        is_alt_commander=is_alt,
        card=card,
    )
    ann_start, ann_end = announcement_window(release_date)
    return {
        "had_spike": True,
        "precon_attributed": attributed,
        "attribution": attribution_label(
            best.report_date,
            best.set_name,
            release_date,
            precon_release_date=precon_release_date,
            commander_spike_set=commander_spike_set,
            card_name=best.oracle_name,
            deck_colors=deck_colors,
            card_color_identity=card_colors,
            synergy_fit=synergy,
            is_alt_commander=is_alt,
            card=card,
        ),
        "synergy_fit": synergy,
        "is_alternate_commander": is_alt,
        "price_source": "spike_csv",
        "spike_matches": spike_matches,
        "report_date": best.report_date.isoformat(),
        "product_name": best.product_name,
        "set_name": best.set_name,
        "set_code": best.set_code,
        "collector_number": best.collector_number,
        "printing_key": best.printing_key(),
        "baseline_price": best.initial_price,
        "peak_price": best.final_price,
        "spike_pct": round(best.change_pct, 3),
        "spike_usd": round(best.change_usd, 2),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "announcement_window_start": ann_start.isoformat(),
        "announcement_window_end": ann_end.isoformat(),
    }


def find_spike_near_release(
    card_name: str,
    release_date: date,
    *,
    precon_release_date: Optional[date] = None,
    product_code: Optional[str] = None,
    set_code: Optional[str] = None,
    collector_number: Optional[str] = None,
    require_precon_attributed: bool = True,
    csv_path: Optional[Path] = None,
    deck_colors: Optional[list] = None,
    card_color_map: Optional[dict[str, list]] = None,
    deck_synergy_ctx: Optional["DeckSynergyContext"] = None,
    cards_by_name: Optional[dict[str, object]] = None,
) -> dict:
    if not release_date or not card_name:
        return {"had_spike": False, "price_source": "missing"}

    path_key = str(csv_path) if csv_path else None
    index = get_spike_index(path_key)
    if not index:
        return {"had_spike": False, "price_source": "missing"}

    commander_spike_set = resolve_commander_spike_set(product_code) if product_code else None
    window_start, window_end = spike_window(release_date, precon_release_date)

    if set_code and collector_number:
        pk = normalize_printing_key(set_code, collector_number)
        printing_index = get_spike_printing_index(path_key)
        candidates = list(printing_index.get(pk, []))
        if not candidates:
            candidates = collect_spike_candidates(
                index,
                card_name,
                set_code=set_code,
                collector_number=collector_number,
            )
    else:
        candidates = collect_spike_candidates(index, card_name)

    matches = [r for r in candidates if window_start <= r.report_date <= window_end]
    qualifying = _qualifying_spikes(matches)
    if not qualifying:
        return {
            "had_spike": False,
            "precon_attributed": False,
            "price_source": "spike_csv",
            "spike_matches": len(matches),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    attributed = _filter_attributed(
        qualifying,
        release_date,
        precon_release_date=precon_release_date,
        commander_spike_set=commander_spike_set,
        require_precon_attributed=require_precon_attributed,
        deck_colors=deck_colors,
        card_color_map=card_color_map,
        deck_synergy_ctx=deck_synergy_ctx,
        cards_by_name=cards_by_name,
    )
    pool = attributed if (require_precon_attributed and attributed) else qualifying
    if require_precon_attributed and not attributed:
        return {
            "had_spike": False,
            "precon_attributed": False,
            "price_source": "spike_csv",
            "spike_matches": len(qualifying),
            "note": "Spike found but not attributed to this precon release",
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    best = max(pool, key=_spike_rank_key)
    return _spike_result(
        best,
        release_date=release_date,
        precon_release_date=precon_release_date,
        commander_spike_set=commander_spike_set,
        window_start=window_start,
        window_end=window_end,
        spike_matches=len(qualifying),
        deck_colors=deck_colors,
        card_color_map=card_color_map,
        deck_synergy_ctx=deck_synergy_ctx,
        cards_by_name=cards_by_name,
    )


def find_omission_spike_benchmarks(
    actual_deck: set[str],
    release_date: date,
    *,
    precon_release_date: Optional[date] = None,
    product_code: Optional[str] = None,
    predicted_top: Optional[set[str]] = None,
    earliest_printing_map: Optional[dict[str, date]] = None,
    require_precon_attributed: bool = True,
    attribution_tier: str = "strict",
    limit: int = 15,
    csv_path: Optional[Path] = None,
    deck_colors: Optional[list] = None,
    card_color_map: Optional[dict[str, list]] = None,
    deck_synergy_ctx: Optional["DeckSynergyContext"] = None,
    cards_by_name: Optional[dict[str, object]] = None,
) -> List[dict]:
    if not release_date or not actual_deck:
        return []

    if not get_spike_index(str(csv_path) if csv_path else None):
        return []

    from engine.spec_eligibility import was_spec_eligible_at_prerelease

    commander_spike_set = resolve_commander_spike_set(product_code) if product_code else None
    window_start, window_end = spike_window(release_date, precon_release_date)
    actual_lower = {name.lower() for name in actual_deck}
    predicted_lower = {name.lower() for name in (predicted_top or set())}
    use_loose = attribution_tier == "loose" or (
        not require_precon_attributed and attribution_tier != "strict"
    )

    best_by_card: dict[str, SpikeRecord] = {}
    for rec in iter_spikes_in_window(window_start, window_end, csv_path):
        card_key = rec.oracle_name.lower()
        if card_key in actual_lower:
            continue
        if not meets_spike_threshold(rec.change_pct, rec.change_usd):
            continue
        if earliest_printing_map is not None and not was_spec_eligible_at_prerelease(
            rec.oracle_name, release_date, earliest_printing_map
        ):
            continue

        synergy, is_alt, card = _synergy_context_for_card(
            rec.oracle_name,
            deck_synergy_ctx=deck_synergy_ctx,
            cards_by_name=cards_by_name,
        )
        card_colors = _lookup_card_colors(rec.oracle_name, card_color_map)

        if use_loose:
            if not is_loose_omission_spike(
                rec.report_date,
                rec.set_name,
                release_date,
                precon_release_date=precon_release_date,
                commander_spike_set=commander_spike_set,
                card_name=rec.oracle_name,
                deck_colors=deck_colors,
                card_color_identity=card_colors,
            ):
                continue
        elif require_precon_attributed and not is_precon_attributed_spike(
            rec.report_date,
            rec.set_name,
            release_date,
            precon_release_date=precon_release_date,
            commander_spike_set=commander_spike_set,
            card_name=rec.oracle_name,
            deck_colors=deck_colors,
            card_color_identity=card_colors,
            synergy_fit=synergy,
            is_alt_commander=is_alt,
            card=card,
        ):
            continue

        bench_key = rec.benchmark_key()
        existing = best_by_card.get(bench_key)
        if existing is None or _spike_rank_key(rec) > _spike_rank_key(existing):
            best_by_card[bench_key] = rec

    ranked = sorted(
        best_by_card.values(),
        key=_spike_rank_key,
        reverse=True,
    )[:limit]

    out = []
    for rec in ranked:
        key = rec.oracle_name.lower()
        earliest = (
            earliest_printing_map.get(key) if earliest_printing_map is not None else None
        )
        synergy, is_alt, card = _synergy_context_for_card(
            rec.oracle_name,
            deck_synergy_ctx=deck_synergy_ctx,
            cards_by_name=cards_by_name,
        )
        attributed = is_precon_attributed_spike(
            rec.report_date,
            rec.set_name,
            release_date,
            precon_release_date=precon_release_date,
            commander_spike_set=commander_spike_set,
            card_name=rec.oracle_name,
            deck_colors=deck_colors,
            card_color_identity=_lookup_card_colors(rec.oracle_name, card_color_map),
            synergy_fit=synergy,
            is_alt_commander=is_alt,
            card=card,
        )
        out.append(
            {
                "card_name": rec.oracle_name,
                "report_date": rec.report_date.isoformat(),
                "spike_pct": round(rec.change_pct, 3),
                "spike_usd": round(rec.change_usd, 2),
                "baseline_price": rec.initial_price,
                "peak_price": rec.final_price,
                "set_name": rec.set_name,
                "set_code": rec.set_code,
                "collector_number": rec.collector_number,
                "printing_key": rec.printing_key(),
                "in_top_predictions": key in predicted_lower,
                "spec_eligible": True,
                "precon_attributed": attributed,
                "synergy_fit": synergy,
                "is_alternate_commander": is_alt,
                "attribution": attribution_label(
                    rec.report_date,
                    rec.set_name,
                    release_date,
                    precon_release_date=precon_release_date,
                    commander_spike_set=commander_spike_set,
                    card_name=rec.oracle_name,
                    deck_colors=deck_colors,
                    card_color_identity=_lookup_card_colors(rec.oracle_name, card_color_map),
                    synergy_fit=synergy,
                    is_alt_commander=is_alt,
                    card=card,
                ),
                "earliest_printing": earliest.isoformat() if earliest else None,
            }
        )
    return out


def spike_csv_stats(path: Optional[Path] = None) -> dict:
    records = load_spike_csv(path)
    if not records:
        return {"loaded": False, "rows": 0, "unique_cards": 0}
    return {
        "loaded": True,
        "rows": len(records),
        "unique_cards": len({r.oracle_name.lower() for r in records}),
        "unique_printings": len({r.printing_key() for r in records if r.printing_key()}),
        "path": str(path or SPIKE_CSV_PATH),
        "source": "spike_reasoning" if (path or SPIKE_CSV_PATH).suffix.lower() in {".xlsx", ".xlsm"} else "tcgplayer_csv",
    }
