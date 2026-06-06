"""
Map commander deck product codes to spike CSV set names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from config import MTGJSON_CACHE_DIR

_ANNUAL_COMMANDER = re.compile(r"^C(\d{2})$")

_DIRECT_SPIKE_SET = {
    "CMD": "Commander",
    "CMR": "Commander Legends",
    "CMM": "Commander Masters",
    "CLB": "Commander Legends: Battle for Baldur's Gate",
}


def _load_mtgjson_set(code: str) -> Optional[dict]:
    path = MTGJSON_CACHE_DIR / f"{code.upper()}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("data", {})
    except (json.JSONDecodeError, OSError):
        return None


def resolve_commander_spike_set(product_code: str) -> Optional[str]:
    """Map deck product code to the spike CSV Set Name for that precon line."""
    code = (product_code or "").upper()
    if not code:
        return None

    annual = _ANNUAL_COMMANDER.fullmatch(code)
    if annual:
        return f"Commander {2000 + int(annual.group(1))}"

    if code in _DIRECT_SPIKE_SET:
        return _DIRECT_SPIKE_SET[code]

    parent = _load_mtgjson_set(code)
    parent_name = (parent or {}).get("name")
    if parent_name:
        return f"Commander: {parent_name}"

    for path in MTGJSON_CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8")).get("data", {})
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("type") or "").lower() != "commander":
            continue
        if (data.get("parentCode") or "").upper() != code:
            continue
        cmd_name = data.get("name") or ""
        if cmd_name.endswith(" Commander"):
            return f"Commander: {cmd_name[: -len(' Commander')]}"
        if parent_name:
            return f"Commander: {parent_name}"

    own = _load_mtgjson_set(code)
    if own and "commander" in (own.get("name") or "").lower():
        return own["name"]
    return None
