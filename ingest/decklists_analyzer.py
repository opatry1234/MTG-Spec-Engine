"""
Analyze Commander precon decklists to identify and populate staples.

Reads all decklists from the Excel file, counts card frequencies,
identifies eternal staples, enriches metadata from Scryfall cards/printings,
and populates commander_decks, deck_cards, and staples_exclusion_list.

Run with: python ingest/decklists_analyzer.py
"""

import sys
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR, DATABASE_URL
from db.schema import (
    Card,
    CardPrinting,
    CommanderDeck,
    DeckCard,
    StaplesExclusionList,
)

DECKLIST_PATH = DATA_DIR / "decklists" / "Commander_Precon_Decklists.xlsx"
STAPLES_CSV_PATH = DATA_DIR / "staples" / "staples_exclusion_list.csv"

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def parse_deck_metadata(header_text: str) -> Dict:
    """Extract set code, release date, and commander(s) from deck header."""
    result = {}

    set_match = re.search(r"Set:\s*(\S+)", str(header_text))
    if set_match:
        result["set_code"] = set_match.group(1)

    date_match = re.search(r"Released:\s*(\d{4}-\d{2}-\d{2})", str(header_text))
    if date_match:
        result["release_date"] = date.fromisoformat(date_match.group(1))

    commander_match = re.search(r"Commander\(s\):\s*([^|]+?)(?:\||$)", str(header_text))
    if commander_match:
        result["commander"] = commander_match.group(1).strip()

    return result


def _parse_land_count(df: pd.DataFrame) -> Optional[int]:
    """Read deck land total from column G on the face commander row."""
    for _, row in df.iterrows():
        if len(row) <= 6:
            continue
        is_commander = str(row.iloc[5]).strip().lower() if pd.notna(row.iloc[5]) else ""
        land_count = row.iloc[6]
        if is_commander == "yes" and pd.notna(land_count):
            try:
                return int(land_count)
            except (TypeError, ValueError):
                continue
    return None


def _parse_card_count(row) -> int:
    """Read quantity from the Count column (E / index 4)."""
    if len(row) <= 4:
        return 1
    val = row.iloc[4]
    if pd.notna(val) and isinstance(val, (int, float)):
        return max(int(val), 0)
    return 1


def _add_card(cards: Counter, card_name: str, count: int) -> None:
    if count <= 0:
        return
    cards[card_name] += count


def read_deck_from_sheet(sheet_name: str, df: pd.DataFrame) -> Dict:
    """Extract deck information from a single sheet."""
    deck_info = {
        "name": sheet_name,
        "cards": Counter(),
        "commander": None,
        "set_code": None,
        "release_date": None,
        "land_count": _parse_land_count(df),
    }

    for _, row in df.iterrows():
        row_str = " | ".join([str(v) for v in row.values if pd.notna(v)])
        if "Set:" in row_str:
            deck_info.update(parse_deck_metadata(row_str))
            break

    commander_started = False
    mainboard_started = False
    for _, row in df.iterrows():
        if isinstance(row.iloc[0], str):
            cell = str(row.iloc[0]).upper()
            if "COMMANDER(S)" in cell:
                commander_started = True
                mainboard_started = False
                continue
            if "MAINBOARD" in cell:
                mainboard_started = True
                commander_started = False
                continue
            if mainboard_started and ("SIDEBOARD" in cell or "MAYBEBOARD" in cell):
                break

        card_name = row.iloc[0]
        if pd.isna(card_name) or not isinstance(card_name, str):
            continue
        if card_name.lower() in ["card name", "mana cost", "type", "colors", "count", "rarity"]:
            continue

        count = _parse_card_count(row)

        if commander_started:
            is_commander = str(row.iloc[5]).strip().lower() if len(row) > 5 and pd.notna(row.iloc[5]) else ""
            if is_commander == "yes":
                deck_info["commander"] = card_name
                _add_card(deck_info["cards"], card_name, count)
            continue

        if mainboard_started:
            _add_card(deck_info["cards"], card_name, count)

    return deck_info


def load_all_decklists(file_path: Path) -> List[Dict]:
    """Read all deck sheets from Excel file."""
    xls = pd.ExcelFile(file_path)
    sheets = [s for s in xls.sheet_names if s != "INDEX"]

    print(f"Found {len(sheets)} deck sheets")

    all_decks = []
    for i, sheet_name in enumerate(sheets, 1):
        if i % 20 == 0:
            print(f"   Processing deck {i}/{len(sheets)}...")
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        all_decks.append(read_deck_from_sheet(sheet_name, df))

    print(f"Loaded {len(all_decks)} decks")
    return all_decks


def identify_staples(all_decks: List[Dict], threshold: float = 0.95) -> List[Tuple[str, str]]:
    """Cards appearing in >= threshold fraction of decks."""
    total_decks = len(all_decks)
    deck_count_per_card = Counter()

    for deck in all_decks:
        for card_name in deck["cards"]:
            deck_count_per_card[card_name] += 1

    staples = []
    threshold_count = int(total_decks * threshold)

    for card_name, count in deck_count_per_card.most_common():
        if count >= threshold_count:
            pct = count / total_decks * 100
            staples.append((card_name, f"appears in {count}/{total_decks} decks ({pct:.0f}%)"))

    return staples


def _lookup_card(session, card_name: str) -> Optional[Card]:
    card = session.query(Card).filter(Card.name == card_name).first()
    if card:
        return card
    # Double-faced: try front face only
    front = card_name.split(" // ")[0].strip()
    if front != card_name:
        return session.query(Card).filter(Card.name == front).first()
    return None


def _lookup_commander(session, commander_str: str) -> Optional[Card]:
    if not commander_str:
        return None
    for part in re.split(r"\s*/\s*|\s+and\s+", commander_str):
        part = part.strip()
        if not part:
            continue
        card = _lookup_card(session, part)
        if card:
            return card
    return _lookup_card(session, commander_str.strip())


def _is_new_card(session, card_name: str, set_code: Optional[str]) -> bool:
    if not set_code:
        return False
    return (
        session.query(CardPrinting)
        .filter(CardPrinting.card_name == card_name, CardPrinting.set_code == set_code.lower())
        .first()
        is not None
    )


def clear_deck_data(session):
    """Remove existing deck rows for idempotent re-runs."""
    session.query(DeckCard).delete()
    session.query(CommanderDeck).delete()
    session.commit()


def populate_database(all_decks: List[Dict], staples_list: List[Tuple[str, str]]):
    """Populate commander_decks, deck_cards, and staples_exclusion_list."""
    session = Session()

    try:
        clear_deck_data(session)
        print("\nPopulating database...")

        for i, deck in enumerate(all_decks, 1):
            commander_card = _lookup_commander(session, deck.get("commander") or "")
            colors = commander_card.color_identity if commander_card else None
            commander_text = commander_card.oracle_text if commander_card else ""
            set_code = (deck.get("set_code") or "").lower()

            deck_record = CommanderDeck(
                product=deck.get("set_code", "UNKNOWN"),
                deck_name=deck["name"],
                release_date=deck.get("release_date"),
                theme=deck["name"],
                colors=colors,
                commander_name=deck.get("commander", ""),
                commander_text=commander_text or "",
                total_cards=sum(deck["cards"].values()),
                land_count=deck.get("land_count"),
                new_cards=sum(
                    qty
                    for name, qty in deck["cards"].items()
                    if _is_new_card(session, name, set_code)
                ),
                product_description="",
                include_in_training=True,
                decklist_revealed=True,
            )
            session.add(deck_record)
            session.flush()

            for card_name, count in deck["cards"].items():
                session.add(
                    DeckCard(
                        deck_id=deck_record.id,
                        card_name=card_name,
                        quantity=count,
                        is_new_card=_is_new_card(session, card_name, set_code),
                    )
                )

            if i % 20 == 0:
                print(f"   {i}/{len(all_decks)} decks...")

        session.commit()
        print(f"Populated {len(all_decks)} commander_decks and deck_cards")

        print(f"\nPopulating staples_exclusion_list ({len(staples_list)} cards)...")
        for card_name, reason in staples_list:
            session.merge(
                StaplesExclusionList(
                    card_name=card_name,
                    reason=reason,
                    added_date=date.today(),
                    added_by="analysis",
                )
            )

        session.commit()
        print(f"Populated {len(staples_list)} staples")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def export_staples_csv(staples_list: List[Tuple[str, str]], output_path: Path = STAPLES_CSV_PATH):
    """Export staples list to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        f.write("card_name,reason\n")
        for card_name, reason in staples_list:
            reason_escaped = reason.replace('"', '""')
            f.write(f'"{card_name}","{reason_escaped}"\n')

    print(f"Exported {len(staples_list)} staples to {output_path}")


def main():
    print("\n" + "=" * 70)
    print("COMMANDER PRECON DECKLISTS ANALYZER")
    print("=" * 70)

    if not DECKLIST_PATH.exists():
        raise FileNotFoundError(f"Decklist file not found: {DECKLIST_PATH}")

    print(f"\nReading decklists from {DECKLIST_PATH}...")
    all_decks = load_all_decklists(DECKLIST_PATH)

    print("\nAnalyzing card frequencies...")
    staples_list = identify_staples(all_decks, threshold=0.95)

    print(f"\nAnalysis Results:")
    print(f"   Total decks: {len(all_decks)}")
    print(f"   Staples (95%+): {len(staples_list)}")

    print(f"\nTop 10 Staples:")
    for i, (card_name, reason) in enumerate(staples_list[:10], 1):
        print(f"   {i:2}. {card_name:30} {reason}")

    print("\n" + "=" * 70)
    populate_database(all_decks, staples_list)
    export_staples_csv(staples_list)

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
