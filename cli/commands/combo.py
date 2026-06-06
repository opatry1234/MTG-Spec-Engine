"""Combo validation against spike reasoning bible."""

from __future__ import annotations

import argparse

from backtester.combo_validation import (
    scan_decklist_combo_candidates,
    validate_all_combo_discovered,
    validate_deck_combo_discovered,
)
from backtester.deck_anchors import list_anchor_cards, resolve_deck
from backtester.spike_reasoning import reasoning_stats
from config import SPIKE_DATA_XLSX_PATH
from db.engine import create_session_factory


def _load_models(use_ml: bool):
    if not use_ml:
        return None
    from models.trainer import load_models

    return load_models()


def _require_spike_file() -> None:
    if not SPIKE_DATA_XLSX_PATH.exists():
        raise SystemExit(f"Missing spike bible: {SPIKE_DATA_XLSX_PATH}")


def cmd_stats(_args) -> None:
    stats = reasoning_stats()
    print(f"Spike reasoning file: {stats['path']}")
    if not stats["loaded"]:
        print("  (file missing)")
        return
    print(f"  Total rows: {stats['total_rows']}")
    print(f"  Combo discovered: {stats['combo_discovered_rows']}")
    if stats["decks"]:
        print(f"  Decks: {', '.join(stats['decks'])}")


def cmd_deck(args) -> None:
    _require_spike_file()
    Session, _ = create_session_factory()
    session = Session()
    try:
        deck = resolve_deck(session, deck_name=args.deck, product_code=args.product)
        if not deck:
            raise SystemExit(f"Deck not found: {args.deck or args.product}")

        models = _load_models(args.use_ml)
        if args.list_anchors:
            anchors = list_anchor_cards(
                session,
                deck,
                anchor_mode=args.anchors if args.anchors != "both" else "both",
                models=models,
            )
            print(f"Anchor pool ({args.anchors}) — {len(anchors)} cards:")
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
        df = report["results_df"]
        if df.empty:
            print("No combo-discovered rows for this deck in Spike Reasoning")
        else:
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
            print(df[cols].to_string(index=False))

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
    finally:
        session.close()


def cmd_all(args) -> None:
    _require_spike_file()
    Session, _ = create_session_factory()
    session = Session()
    try:
        df = validate_all_combo_discovered(
            session,
            fetch_live=not args.offline,
            models=_load_models(args.use_ml),
        )
        if df.empty:
            print("No combo-discovered rows with resolvable decks.")
            return
        print(df.to_string(index=False))
        if args.export:
            df.to_csv(args.export, index=False)
            print(f"Wrote {args.export}")
    finally:
        session.close()


def register_combo_commands(sub: argparse._SubParsersAction) -> None:
    combo = sub.add_parser("combo", help="Validate combo detection vs spike bible")
    combo_sub = combo.add_subparsers(dest="combo_cmd", required=True)

    p = combo_sub.add_parser("stats", help="Spike reasoning file stats")
    p.set_defaults(func=cmd_stats)

    p = combo_sub.add_parser("deck", help="Validate one deck")
    p.add_argument("--deck", type=str, required=True)
    p.add_argument("--product", type=str, default=None)
    p.add_argument("--anchors", choices=["decklist", "expected", "both"], default="decklist")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--use-ml", action="store_true")
    p.add_argument("--scan-decklist", action="store_true")
    p.add_argument("--scan-limit", type=int, default=60)
    p.add_argument("--list-anchors", action="store_true")
    p.set_defaults(func=cmd_deck)

    p = combo_sub.add_parser("all", help="Validate all decks in spike bible")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--use-ml", action="store_true")
    p.add_argument("--export", type=str, default=None)
    p.set_defaults(func=cmd_all)
