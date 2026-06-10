"""
Point-in-time EDHREC theme staples via the Wayback Machine — leakage-free Layer 2.

Live EDHREC reflects months of POST-reveal upgrading, so reading it during a
backtest peeks at the answer. The Wayback Machine fixes that: we fetch the latest
archived snapshot STRICTLY BEFORE the deck's anchor date of the EDHREC theme pages
matching the deck's mechanics (e.g. -1/-1 counters), and extract each card's
synergy/inclusion from the embedded __NEXT_DATA__ JSON. That is exactly what a
human speculator knew pre-reveal: "this card is already a staple of the archetype."

Verified empirically: theme/commander pages snapshot ~monthly; __NEXT_DATA__ carries
cardlists[].cardviews[] with name/synergy/inclusion/num_decks.

Fail-soft by design: any network/parse problem returns {} and scoring proceeds
without the feature. Snapshots cache to data/wayback_cache/ so a backtest hits
archive.org at most once per (page, anchor-month).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "data" / "wayback_cache"
UA = "MTGSpecEngine/1.0 (backtest research)"
CDX = "http://web.archive.org/cdx/search/cdx"

# mechanic id (features/mechanic_taxonomy.py) -> EDHREC theme page slugs
MECHANIC_THEME_SLUGS: dict[str, list[str]] = {
    "minus_counters": ["m1-m1-counters"],   # EDHREC encodes -1/-1 as m1-m1
    "plus_counters": ["p1-p1-counters"],
    "proliferate": ["proliferate"],
    "poison": ["infect"],
    "energy": ["energy-counters"],
    "aristocrats": ["aristocrats", "sacrifice"],
    "tokens": ["tokens"],
    "graveyard": ["reanimator", "graveyard"],
    "discard_payoff": ["discard"],
    "lifegain": ["lifegain"],
    "lifedrain": ["lifegain"],
    "spellslinger": ["spellslinger"],
    "artifacts_matter": ["artifacts"],
    "enchantments_matter": ["enchantments"],
    "treasure": ["treasure"],
    "landfall": ["landfall", "lands-matter"],
    "vehicles": ["vehicles"],
    "equipment": ["equipment"],
    "blink": ["blink"],
    "monarch": ["monarch"],
    "curses": ["curses"],
}


def _http(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def latest_snapshot_before(page_url: str, anchor: date) -> str | None:
    """Wayback timestamp (YYYYMMDD...) of the newest capture strictly before anchor."""
    q = urllib.parse.urlencode({
        "url": page_url, "to": anchor.strftime("%Y%m%d"), "output": "json",
        "limit": "-1", "filter": "statuscode:200", "fastLatest": "true",
    })
    rows = json.loads(_http(f"{CDX}?{q}", timeout=30) or b"[]")
    if len(rows) < 2:
        return None
    ts = rows[-1][1]
    return ts if ts[:8] < anchor.strftime("%Y%m%d") else None


def _parse_cardviews(html: str) -> dict[str, dict]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return {}
    d = json.loads(m.group(1))
    out: dict[str, dict] = {}

    def walk(o):
        if isinstance(o, dict):
            for cl in o.get("cardlists") or []:
                for cv in cl.get("cardviews") or []:
                    n = cv.get("name")
                    if n and n not in out:
                        out[n] = {
                            "synergy": cv.get("synergy"),
                            "inclusion": cv.get("inclusion"),
                            "num_decks": cv.get("num_decks"),
                        }
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(d)
    return out


def archived_theme_cards(slug: str, anchor: date) -> dict[str, dict]:
    """name -> {synergy, inclusion, num_decks} from the newest pre-anchor snapshot
    of edhrec.com/themes/<slug>. Cached on disk; {} on any failure."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"theme_{slug}_{anchor.strftime('%Y%m')}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:  # noqa: BLE001
            pass
    page = f"https://edhrec.com/themes/{slug}"
    try:
        ts = latest_snapshot_before(page, anchor)
        if not ts:
            cache.write_text("{}")
            return {}
        html = _http(f"http://web.archive.org/web/{ts}/{page}", timeout=90).decode("utf-8", "replace")
        cards = _parse_cardviews(html)
        cache.write_text(json.dumps(cards))
        return cards
    except Exception:  # noqa: BLE001
        return {}


def theme_staple_scores(mechanic_ids: list[str], anchor: date) -> dict[str, float]:
    """card name -> 0..1 staple score across the deck's theme pages, point-in-time.

    Score per card = max over themes of max(inclusion/100, synergy⁺). A card that
    was already a documented staple of the archetype before the reveal scores ~1.
    """
    scores: dict[str, float] = {}
    seen_slugs: set[str] = set()
    for mid in mechanic_ids:
        for slug in MECHANIC_THEME_SLUGS.get(mid, []):
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            for name, v in archived_theme_cards(slug, anchor).items():
                inc = (v.get("inclusion") or 0) / 100.0
                syn = max(0.0, float(v.get("synergy") or 0.0))
                s = round(min(max(inc, syn), 1.0), 4)
                if s > scores.get(name, 0.0):
                    scores[name] = s
    return scores
