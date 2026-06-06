#!/usr/bin/env python3
"""
Daily price snapshot → Supabase (run by GitHub Actions, no local machine needed).

Downloads Scryfall's full bulk file (free, no key), keeps only REAL spec-able
cards (drops basics, tokens, emblems, stickers, art/memorabilia, non-paper, and
a few ubiquitous staples), takes the cheapest paper printing's **Near-Mint,
non-foil** USD (Scryfall `prices.usd`) per card, and writes to Supabase.

Note: Scryfall `prices.usd` is the non-foil (NM-equivalent) market price — it is
not condition-tiered. True lowest-NM-listing pricing requires TCGplayer SKU data,
which isn't available free; `prices.usd` is the best free proxy and is what the
$10 spec gate uses. Foil is kept in a separate column only.

  • card_prices_current  — upserted every run (latest price for every card)
  • card_prices_history  — a row only when a value CHANGES (compact point-in-time series)

Supply / seller-concentration columns exist in the schema but stay NULL until a
TCGplayer-style listings feed is wired in (no free source today).

Env (set as GitHub Actions secrets):
  SUPABASE_URL          e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role key (write access; keep secret)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from datetime import date, datetime, timezone

import requests

UA = "mtg-spec-engine/1.0 (daily price snapshot)"
BULK_INDEX = "https://api.scryfall.com/bulk-data"

# ── Junk filters: things that exist in Scryfall but are never real spec targets ──
JUNK_LAYOUTS = {
    "token", "double_faced_token", "emblem", "art_series",
    "vanguard", "scheme", "planar", "augment", "host",
}
JUNK_SET_TYPES = {"token", "memorabilia", "minigame"}
JUNK_TYPE_WORDS = ("basic land", "token", "emblem", "sticker", "attraction", "dungeon")
# Ubiquitous staples: real cards, but supply is effectively infinite — never a spec.
UBIQUITOUS_STAPLES = {
    "Sol Ring", "Command Tower", "Arcane Signet", "Commander's Sphere",
    "Command Sphere", "Mind Stone", "Fellwar Stone", "Exotic Orchard",
    "Reliquary Tower", "Myriad Landscape", "Terramorphic Expanse",
    "Evolving Wilds", "Mortuary Mire", "Bojuka Bog",
}


def log(m: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')}  {m}", flush=True)


def is_spec_card(card: dict) -> bool:
    if "paper" not in (card.get("games") or []):
        return False
    if card.get("layout") in JUNK_LAYOUTS:
        return False
    if card.get("set_type") in JUNK_SET_TYPES:
        return False
    if card.get("oversized"):
        return False
    tl = (card.get("type_line") or "").lower()
    if any(w in tl for w in JUNK_TYPE_WORDS):
        return False
    if card.get("name") in UBIQUITOUS_STAPLES:
        return False
    return True


def cheapest_prices(bulk_path: str) -> dict:
    """card_name -> {oracle, usd, foil}: cheapest paper printing.

    usd  = Scryfall prices.usd      (Near-Mint, NON-foil — the gate price)
    foil = Scryfall prices.usd_foil (foil, kept separately)
    """
    with open(bulk_path, encoding="utf-8") as f:
        cards = json.load(f)
    out: dict = {}
    kept = 0
    for c in cards:
        if not is_spec_card(c):
            continue
        name = c.get("name")
        if not name:
            continue
        pr = c.get("prices") or {}
        usd = float(pr["usd"]) if pr.get("usd") else None
        foil = float(pr["usd_foil"]) if pr.get("usd_foil") else None
        if usd is None and foil is None:
            continue
        kept += 1
        slot = out.setdefault(name, {"oracle": c.get("oracle_id"), "usd": None, "foil": None})
        if usd is not None and (slot["usd"] is None or usd < slot["usd"]):
            slot["usd"] = usd
        if foil is not None and (slot["foil"] is None or foil < slot["foil"]):
            slot["foil"] = foil
    log(f"{kept} priced paper printings → {len(out)} distinct spec-able cards")
    return out


# ── Supabase REST (PostgREST) helpers ───────────────────────────────────────
def _sb():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return url, {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def fetch_current(url: str, headers: dict) -> dict:
    """Pull existing latest prices for change detection (paginated)."""
    out: dict = {}
    step = 10000
    start = 0
    while True:
        h = dict(headers); h["Range"] = f"{start}-{start + step - 1}"
        r = requests.get(
            f"{url}/rest/v1/card_prices_current?select=card_name,price_usd,price_usd_foil",
            headers=h, timeout=120,
        )
        if r.status_code >= 300:
            raise RuntimeError(
                f"Could not read card_prices_current (HTTP {r.status_code}). "
                f"Have you run supabase/schema.sql in the Supabase SQL editor, and are "
                f"SUPABASE_URL / SUPABASE_SERVICE_KEY correct? Response: {r.text[:300]}"
            )
        rows = r.json() if r.content else []
        if isinstance(rows, dict):  # PostgREST error object, not a row list
            raise RuntimeError(
                f"Supabase error reading card_prices_current: {str(rows)[:300]}. "
                f"Most likely the tables don't exist yet — run supabase/schema.sql."
            )
        if not rows:
            break
        for row in rows:
            out[row["card_name"]] = (row.get("price_usd"), row.get("price_usd_foil"))
        if len(rows) < step:
            break
        start += step
    return out


def _post(url: str, headers: dict, table: str, rows: list, prefer: str) -> None:
    h = dict(headers); h["Prefer"] = prefer
    for i in range(0, len(rows), 2000):
        chunk = rows[i:i + 2000]
        r = requests.post(f"{url}/rest/v1/{table}", headers=h, data=json.dumps(chunk), timeout=180)
        if r.status_code >= 300:
            raise RuntimeError(f"{table} write failed {r.status_code}: {r.text[:300]}")


def _eq(a, b) -> bool:
    fa = float(a) if a is not None else None
    fb = float(b) if b is not None else None
    return fa == fb


def run() -> None:
    today = date.today().isoformat()
    url, headers = _sb()

    # 1. download bulk
    idx = requests.get(BULK_INDEX, headers={"User-Agent": UA}, timeout=120).json()
    uri = next(o["download_uri"] for o in idx["data"] if o["type"] == "default_cards")
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    log(f"Downloading bulk → {tmp}")
    req = urllib.request.Request(uri, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=1200) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)

    priced = cheapest_prices(tmp)

    # 2. change detection vs current
    current = fetch_current(url, headers)
    log(f"{len(current)} cards already in card_prices_current")

    now_iso = datetime.now(timezone.utc).isoformat()
    current_rows, history_rows = [], []
    for name, p in priced.items():
        usd, foil = p["usd"], p["foil"]
        current_rows.append({
            "card_name": name, "scryfall_oracle": p["oracle"],
            "price_usd": usd, "price_usd_foil": foil, "updated_at": now_iso,
        })
        prev = current.get(name)
        if prev is None or not (_eq(prev[0], usd) and _eq(prev[1], foil)):
            history_rows.append({
                "card_name": name, "snapshot_date": today,
                "price_usd": usd, "price_usd_foil": foil,
            })

    # 3. write
    _post(url, headers, "card_prices_current", current_rows, "resolution=merge-duplicates")
    _post(url, headers, "card_prices_history", history_rows, "resolution=ignore-duplicates")
    log(f"Upserted {len(current_rows)} current; inserted {len(history_rows)} changed history rows for {today}")
    os.unlink(tmp)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        sys.exit(1)
