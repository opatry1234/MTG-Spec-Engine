"""
Validate combo detection against spike-reasoning spreadsheet rows.

Examples:
    python backtester/combo_validation_cli.py --stats
    python backtester/combo_validation_cli.py --deck "Eternal Might"
    python backtester/combo_validation_cli.py --deck "Eternal Might" --anchors decklist
    python backtester/combo_validation_cli.py --all --offline
    python backtester/combo_validation_cli.py --deck "Eternal Might" --scan-decklist
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.combo_validation import (
    scan_decklist_combo_candidates,
    validate_all_combo_discovered,
    validate_deck_combo_discovered,
)
from backtester.deck_anchors import resolve_deck
from backtester.spike_reasoning import reasoning_stats
from config import SPIKE_DATA_XLSX_PATH
from db.engine import create_session_factory


def _load_models(use_ml: bool):
    if not use_ml:
        return None
    from models.trainer import load_models

    return load_models()


def cmd_stats(_args) -> None:
    stats = reasoning_stats()
    print(f"Spike reasoning file: {stats['path']}")
    if not stats["loaded"]:
        print("  (file missing — add data/raw/Spike Data.xlsx with Spike Reasoning sheet)")
        return
    print(f"  Total rows: {stats['total_rows']}")
    print(f"  Combo discovered: {stats['combo_discovered_rows']}")
    if stats["decks"]:
        print(f"  Decks: {', '.join(stats['decks'])}")


def cmd_deck(args) -> None:
    Session, _ = create_session_factory()
    session = Session()
    try:
        deck = resolve_deck(session, deck_name=args.deck, product_code=args.product)
        if not deck:
            raise SystemExit(f"Deck not found: {args.deck or args.product}")

        models = _load_models(args.use_ml)

        if args.list_anchors:
            from backtester.deck_anchors import list_anchor_cards

            anchors = list_anchor_cards(
                session,
                deck,
                anchor_mode=args.anchors if args.anchors != "both" else "both",
                models=models,
            )
            print(f"\nAnchor pool ({args.anchors}) — {len(anchors)} cards:")
            for a in anchors:
                flags = []
                if a["is_commander"]:
                    flags.append("commander")
                if a["in_actual_decklist"]:
                    flags.append("decklist")
                if a["in_expected_precon"]:
                    flags.append("expected")
                print(f"  {a['card_name']} [{', '.join(flags)}]")
            return

        report = validate_deck_combo_discovered(
            session,
            deck,
            anchor_mode=args.anchors,
            models=models,
            fetch_live=not args.offline,
        )

        print(f"\n{deck.deck_name} ({deck.product}) — commander: {deck.commander_name}")
        print(
            f"Combo-discovered rows: {report['combo_discovered_rows']} | "
            f"detected loops: {report['detected_loops']} | "
            f"sheet matches: {report['sheet_matches']}"
        )

        if report["results_df"].empty:
            print("No combo-discovered rows for this deck in Spike Reasoning")
        else:
            print("\nValidation results:")
            cols = [
                "card_name",
                "anchor_mode",
                "detected_loop",
                "detected_partners",
                "combo_with_sheet",
                "sheet_partners_in_anchors",
                "match_sheet",
                "notes",
            ]
            print(report["results_df"][cols].to_string(index=False))

        if args.scan_decklist:
            scan = scan_decklist_combo_candidates(
                session,
                deck,
                anchor_mode=args.anchors if args.anchors != "both" else "decklist",
                models=models,
                fetch_live=not args.offline,
                card_limit=args.scan_limit,
            )
            print(f"\nIn-deck infinite lines (top {args.scan_limit} anchors checked):")
            if scan.empty:
                print("  (none found)")
            else:
                print(scan.to_string(index=False))

        if args.export:
            out = Path(args.export)
            report["results_df"].to_csv(out, index=False)
            print(f"\nWrote {out}")
    finally:
        session.close()


def cmd_all(args) -> None:
    Session, _ = create_session_factory()
    session = Session()
    try:
        models = _load_models(args.use_ml)
        df = validate_all_combo_discovered(
            session,
            anchor_mode=args.anchors,
            models=models,
            fetch_live=not args.offline,
        )
        if df.empty:
            print("No combo-discovered rows with resolvable decks.")
            return
        print(df.to_string(index=False))
        if args.export:
            df.to_csv(args.export, index=False)
            print(f"\nWrote {args.export}")
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate combo detection vs spike reasoning CSV")
    parser.add_argument("--stats", action="store_true", help="Show reasoning file stats")
    parser.add_argument("--deck", type=str, help="Deck name to validate")
    parser.add_argument("--product", type=str, help="Product code fallback")
    parser.add_argument(
        "--anchors",
        choices=["decklist", "expected", "both"],
        default="decklist",
        help="Anchor pool: actual decklist, expected probable deck, or both",
    )
    parser.add_argument("--all", action="store_true", help="Validate all decks in reasoning CSV")
    parser.add_argument("--offline", action="store_true", help="Use Spellbook cache only")
    parser.add_argument("--use-ml", action="store_true", help="Use ML probable deck for expected anchors")
    parser.add_argument(
        "--scan-decklist",
        action="store_true",
        help="Also list infinite combos between cards in the anchor pool",
    )
    parser.add_argument("--scan-limit", type=int, default=60, help="Max anchor cards to scan")
    parser.add_argument("--export", type=str, help="Write results CSV path")
    parser.add_argument(
        "--list-anchors",
        action="store_true",
        help="Print anchor card pool (decklist / expected) and exit",
    )
    args = parser.parse_args()

    if args.stats:
        cmd_stats(args)
        return

    if not SPIKE_DATA_XLSX_PATH.exists():
        print(f"Missing {SPIKE_DATA_XLSX_PATH}")
        print("Copy your spike reasoning spreadsheet there (see template headers in file).")
        raise SystemExit(1)

    if args.all:
        cmd_all(args)
    elif args.deck or args.product:
        cmd_deck(args)
    else:
        cmd_stats(args)
        parser.print_help()


if __name__ == "__main__":
    main()
