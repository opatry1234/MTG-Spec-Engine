"""
Load the Spike Reasoning sheet from Spike Data.xlsx — the canonical spike bible.

Report dates are resolved from (in order): an explicit Report Date column when
present, MTGStocks article URLs, dates embedded in the Source field, ISO week
numbers in weekly-winners URLs, and finally the 15th of Report Month. When the
workbook also contains an All Spikes sheet, exact TCGPlayer report dates are
matched by card name and price fingerprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

from config import (
    SPIKE_ALL_SPIKES_SHEET,
    SPIKE_DATA_XLSX_PATH,
    SPIKE_REASONING_SHEET,
)

COMBO_DISCOVERED_LABEL = "combo discovered"

_COMBO_SPIKE_TYPES = frozenset(
    {
        "combo discovery",
        "new set combo discovery",
        "commander combo discovery",
        "competitive format (modern combo)",
    }
)

_SOURCE_DATE = re.compile(
    r"\b(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")\b",
    re.I,
)
_MTGSTOCKS_WEEK = re.compile(r"weekly-winners-(\d{4})---(\d+)", re.I)
_DECK_FROM_CAUSE = re.compile(r"(?:Commander\s+)?precon\s*\(([^)]+)\)", re.I)
_MONTH_YEAR = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})$",
    re.I,
)

_REASONING_COLUMN_ALIASES = {
    "rank": ("rank",),
    "card_name": ("card_name", "card name", "product name", "product_name", "name"),
    "set": ("set", "set_name", "set name"),
    "set_code": ("set_code", "set code"),
    "card_number": ("card_number", "card number", "collector number", "collector_number"),
    "report_month": ("report_month", "report month"),
    "report_date": ("report_date", "report date"),
    "initial_price": ("initial_price", "initial price", "initial market price"),
    "final_price": ("final_price", "final price", "final market price"),
    "percent_gain": ("percent_gain", "gain", "gain_pct", "percent gain", "change_pct", "change (%)"),
    "spike_cause": ("spike_cause", "spike cause", "spike_reason", "spike reason", "reason"),
    "spike_type": ("spike_type", "spike type", "type"),
    "confidence": ("confidence",),
    "source": ("source",),
    "source_url": ("source_url", "source url", "url"),
    "deck_name": ("deck_name", "deck name", "deck", "precon"),
    "precon_deck_name": (
        "precon_deck_name",
        "pre-con deck name",
        "precon deck name",
        "precon deck",
        "pre-con deck",
    ),
    "precon_set_code": (
        "precon_set_code",
        "pre-con set code",
        "precon set code",
        "precon product",
        "pre-con product code",
    ),
    "combo_with": ("combo_with", "combo with", "combo partners", "partners"),
    "notes": ("notes", "comment", "comments"),
}


@dataclass
class _PriceDateMatch:
    report_date: date
    initial_price: Optional[float]
    final_price: Optional[float]
    change_pct: float


def _normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _map_columns(columns) -> dict[str, str]:
    normalized = {_normalize_header(c): c for c in columns}
    colmap: dict[str, str] = {}
    for canonical, aliases in _REASONING_COLUMN_ALIASES.items():
        for alias in aliases:
            key = _normalize_header(alias)
            if key in normalized:
                colmap[canonical] = normalized[key]
                break
    return colmap


def _parse_pct(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip().replace("%", "")
    try:
        num = float(text)
    except ValueError:
        return 0.0
    return num / 100.0 if num > 1.5 else num


def _parse_money(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_report_month(value) -> Optional[date]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if not pd.isna(parsed):
        return parsed.date().replace(day=15)
    match = _MONTH_YEAR.match(text)
    if not match:
        return None
    month_name, year = match.group(1), int(match.group(2))
    parsed = pd.to_datetime(f"{month_name} 15, {year}", errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _parse_embedded_dates(text: str) -> list[date]:
    if not text:
        return []
    out: list[date] = []
    for match in _SOURCE_DATE.finditer(text):
        parsed = pd.to_datetime(match.group(0), errors="coerce")
        if not pd.isna(parsed):
            out.append(parsed.date())
    return out


def _parse_mtgstocks_url_date(url: str) -> Optional[date]:
    if not url:
        return None
    match = _MTGSTOCKS_WEEK.search(url)
    if not match:
        return None
    year, week = int(match.group(1)), int(match.group(2))
    try:
        return date.fromisocalendar(year, week, 7)
    except ValueError:
        return None


def _extract_deck_name(spike_cause: str) -> str:
    match = _DECK_FROM_CAUSE.search(spike_cause or "")
    if not match:
        return ""
    name = match.group(1).strip()
    if ":" in name:
        return name.split(":", 1)[1].strip()
    return name


def _split_card_list(value) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[;|,/\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _price_close(a: Optional[float], b: Optional[float], *, tol: float = 0.05) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _normalize_card_key(name: str) -> str:
    from backtester.spike_csv import normalize_oracle_name

    return normalize_oracle_name(name).lower()


@lru_cache(maxsize=1)
def _all_spikes_price_date_index(xlsx_path: str) -> dict[str, list[_PriceDateMatch]]:
    path = Path(xlsx_path)
    if not path.exists() or path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return {}
    try:
        df = pd.read_excel(path, sheet_name=SPIKE_ALL_SPIKES_SHEET)
    except ValueError:
        return {}

    df = df.rename(
        columns={
            "Report Date": "report_date",
            "Product Name": "product_name",
            "Initial Market Price": "initial_price",
            "Final Market Price": "final_price",
            "Change (%)": "change_pct",
        }
    )

    index: dict[str, list[_PriceDateMatch]] = {}
    for row in df.itertuples(index=False):
        product = getattr(row, "product_name", None)
        if not product or pd.isna(product):
            continue
        report_date = pd.to_datetime(getattr(row, "report_date", None), errors="coerce")
        if pd.isna(report_date):
            continue
        initial = _parse_money(getattr(row, "initial_price", None))
        final = _parse_money(getattr(row, "final_price", None))
        change_pct = _parse_pct(getattr(row, "change_pct", None))
        key = _normalize_card_key(str(product))
        index.setdefault(key, []).append(
            _PriceDateMatch(
                report_date=report_date.date(),
                initial_price=initial,
                final_price=final,
                change_pct=change_pct,
            )
        )
    return index


def _refine_report_date(
    *,
    card_name: str,
    report_month: str,
    initial_price: Optional[float],
    final_price: Optional[float],
    change_pct: float,
    source: str,
    source_url: str,
    explicit_report_date: Optional[date],
    xlsx_path: Path,
) -> Optional[date]:
    if explicit_report_date is not None:
        return explicit_report_date

    price_index = _all_spikes_price_date_index(str(xlsx_path))
    from backtester.spike_csv import oracle_name_aliases

    card_keys = {_normalize_card_key(card_name)}
    for alias in oracle_name_aliases(card_name):
        card_keys.add(_normalize_card_key(alias))

    month_anchor = _parse_report_month(report_month)
    best_date: Optional[date] = None
    best_score = -1.0
    for key in card_keys:
        for match in price_index.get(key, []):
            score = 0.0
            if _price_close(initial_price, match.initial_price):
                score += 2.0
            if _price_close(final_price, match.final_price):
                score += 2.0
            if abs(change_pct - match.change_pct) <= 0.05:
                score += 1.0
            if score < 3.0:
                continue
            if month_anchor is not None:
                score -= abs((match.report_date - month_anchor).days) / 100.0
            if score > best_score:
                best_score = score
                best_date = match.report_date
    if best_date is not None:
        return best_date

    embedded = _parse_embedded_dates(f"{source or ''} {source_url or ''}")
    if embedded:
        return max(embedded)

    url_date = _parse_mtgstocks_url_date(source_url or "")
    if url_date is not None:
        return url_date

    return month_anchor


def clear_spike_data_cache() -> None:
    _all_spikes_price_date_index.cache_clear()
    _load_spike_reasoning_rows_cached.cache_clear()


def load_reasoning_dataframe(
    path: Optional[Path] = None,
    *,
    sheet: str = SPIKE_REASONING_SHEET,
) -> pd.DataFrame:
    xlsx_path = path or SPIKE_DATA_XLSX_PATH
    if not xlsx_path.exists():
        return pd.DataFrame()
    if xlsx_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return pd.read_csv(xlsx_path)
    return pd.read_excel(xlsx_path, sheet_name=sheet)


def reasoning_rows_from_dataframe(
    df: pd.DataFrame,
    *,
    xlsx_path: Path = SPIKE_DATA_XLSX_PATH,
) -> list:
    from backtester.spike_reasoning import SpikeReasonRow

    if df.empty:
        return []

    colmap = _map_columns(df.columns)
    if "card_name" not in colmap:
        raise ValueError(
            f"Spike reasoning sheet must include a card name column. Found: {list(df.columns)}"
        )

    bible_path = xlsx_path if xlsx_path.suffix.lower() in {".xlsx", ".xlsm"} else SPIKE_DATA_XLSX_PATH
    rows: list[SpikeReasonRow] = []
    for i, data in enumerate(df.to_dict(orient="records"), start=2):

        def get(field: str, default=""):
            key = colmap.get(field)
            if not key:
                return default
            val = data.get(key, default)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return default
            return str(val).strip()

        card = get("card_name")
        if not card:
            continue

        spike_cause = get("spike_cause") or get("spike_reason")
        spike_type = get("spike_type")
        source = get("source")
        source_url = get("source_url")
        report_month = get("report_month")
        initial_price = _parse_money(data.get(colmap.get("initial_price", ""), None))
        final_price = _parse_money(data.get(colmap.get("final_price", ""), None))
        gain_pct = _parse_pct(data.get(colmap.get("percent_gain", ""), None))

        explicit_date: Optional[date] = None
        if "report_date" in colmap:
            parsed = pd.to_datetime(data.get(colmap["report_date"]), errors="coerce")
            if not pd.isna(parsed):
                explicit_date = parsed.date()

        report_date = _refine_report_date(
            card_name=card,
            report_month=report_month,
            initial_price=initial_price,
            final_price=final_price,
            change_pct=gain_pct,
            source=source,
            source_url=source_url,
            explicit_report_date=explicit_date,
            xlsx_path=bible_path,
        )

        combo_raw = data.get(colmap["combo_with"], "") if colmap.get("combo_with") else ""
        rank_val = data.get(colmap.get("rank", ""), None)
        set_name = get("set")
        set_code = get("set_code")
        card_number = get("card_number")
        try:
            rank = int(rank_val) if rank_val not in (None, "") and not pd.isna(rank_val) else None
        except (TypeError, ValueError):
            rank = None

        precon_deck = get("precon_deck_name") or _extract_deck_name(spike_cause)
        precon_code = get("precon_set_code")
        if not precon_code and precon_deck:
            from backtester.spike_precon_catalog import resolve_precon_set_code

            precon_code = resolve_precon_set_code(
                precon_deck, cause=spike_cause, explicit=""
            )

        rows.append(
            SpikeReasonRow(
                deck_name=precon_deck or get("deck_name"),
                card_name=card,
                spike_reason=spike_cause,
                product_code=precon_code or get("product_code"),
                precon_deck_name=precon_deck,
                precon_set_code=precon_code,
                combo_with=_split_card_list(combo_raw),
                notes=get("notes"),
                source_row=i,
                spike_type=spike_type,
                spike_cause=spike_cause,
                confidence=get("confidence"),
                source=source,
                source_url=source_url,
                report_month=report_month,
                report_date=report_date,
                initial_price=initial_price,
                final_price=final_price,
                gain_pct=gain_pct,
                rank=rank,
                set_name=set_name,
                set_code=set_code,
                card_number=card_number,
            )
        )
    return rows


@lru_cache(maxsize=4)
def _load_spike_reasoning_rows_cached(path_str: str) -> tuple:
    """Cached loader — returns a tuple so lru_cache can hash the result."""
    xlsx_path = Path(path_str) if path_str else SPIKE_DATA_XLSX_PATH
    df = load_reasoning_dataframe(xlsx_path)
    return tuple(reasoning_rows_from_dataframe(df, xlsx_path=xlsx_path))


def load_spike_reasoning_rows(path: Optional[Path] = None) -> list:
    xlsx_path = path or SPIKE_DATA_XLSX_PATH
    return list(_load_spike_reasoning_rows_cached(str(xlsx_path.resolve())))


def load_spike_records_from_bible(path: Optional[Path] = None) -> list:
    from backtester.spike_csv import SpikeRecord, is_valid_spike_row

    rows = load_spike_reasoning_rows(path)
    records = []
    for row in rows:
        if row.report_date is None:
            continue
        change_pct = row.gain_pct or 0.0
        if row.initial_price is not None and row.final_price is not None:
            change_usd = row.final_price - row.initial_price
        else:
            change_usd = 0.0
        if not is_valid_spike_row(change_pct, change_usd):
            continue
        records.append(
            SpikeRecord(
                oracle_name=row.card_name,
                product_name=row.card_name,
                set_name=row.set_name or row.product_code,
                set_code=row.set_code,
                collector_number=row.card_number,
                report_date=row.report_date,
                change_pct=change_pct,
                change_usd=change_usd,
                initial_price=row.initial_price,
                final_price=row.final_price,
            )
        )
    return records


def is_combo_spike_type(spike_type: str) -> bool:
    normalized = (spike_type or "").strip().lower()
    if normalized in _COMBO_SPIKE_TYPES:
        return True
    return "combo" in normalized and "discovery" in normalized
