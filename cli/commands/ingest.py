"""Data ingest pipelines."""

from __future__ import annotations

import argparse
import json


def cmd_scryfall(args) -> None:
    from ingest.scryfall import ingest_oracle_cards, ingest_printings

    if args.mode in ("oracle", "both"):
        print("=== Oracle Cards ===")
        ingest_oracle_cards()
    if args.mode in ("printings", "both"):
        print("=== Card Printings ===")
        ingest_printings()


def cmd_enrich(args) -> None:
    from ingest.enrich_deck_metadata import enrich_decks

    stats = enrich_decks(
        dry_run=args.dry_run,
        commander_only=args.commander_only,
        descriptions_only=args.descriptions_only,
        force_descriptions=args.force_descriptions,
        use_mtgjson_cache=not args.refresh_mtgjson,
    )
    print(f"Decks processed: {stats['decks_total']}")
    print(f"Commander text updated: {stats['commander_updated']}")
    print(f"Product descriptions written: {stats['description_updated']}")


def cmd_prices(args) -> None:
    from datetime import date

    from config import ALL_PRICES_GZ, SPIKE_CSV_PATH, TCGAPIS_KEY
    from db.engine import create_session_factory
    from ingest.prices import (
        backfill_cards,
        decompress_all_prices,
        download_all_prices,
        verify_spikes_csv,
    )

    if args.verify_spikes_csv:
        verify_spikes_csv()
    if args.download_all_prices:
        download_all_prices(force=True)
    if args.decompress:
        decompress_all_prices()

    if args.cards:
        if not args.release_date:
            raise SystemExit("--release-date required with --cards")
        if not SPIKE_CSV_PATH.exists() and not TCGAPIS_KEY and not ALL_PRICES_GZ.exists():
            print("Warning: no spike bible, TCGAPIS_KEY, or AllPrices cache")
        session = create_session_factory()()
        try:
            backfill_cards(session, args.cards, date.fromisoformat(args.release_date))
        finally:
            session.close()

    if not any(
        (
            args.verify_spikes_csv,
            args.download_all_prices,
            args.decompress,
            args.cards,
        )
    ):
        raise SystemExit("Specify at least one of --verify-spikes-csv, --download-all-prices, --decompress, --cards")


def cmd_align_precon(args) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from config import DATABASE_URL
    from ingest.align_precon_releases import align_precon_release_dates

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        stats = align_precon_release_dates(
            session,
            dry_run=args.dry_run,
            min_matches=args.min_matches,
            min_offset_days=args.min_offset_days,
        )
        print(
            f"Done: updated={stats['updated']} cleared={stats.get('cleared', 0)} "
            f"unchanged={stats['unchanged']} skipped={stats['skipped']}"
        )
    finally:
        session.close()


def cmd_refresh_decklists(args) -> None:
    if args.migrate:
        from db.migrate_v4 import migrate_v4

        migrate_v4()
    from ingest.refresh_decklist_metadata import (
        backfill_announced_new_cards,
        refresh_decklist_metadata,
    )

    result = refresh_decklist_metadata()
    announced = backfill_announced_new_cards()
    print(f"Refreshed {result['updated']} decks from Excel")
    print(f"Set announced_new_cards on {announced} decks")
    if result["missing_sheet"]:
        print(f"WARNING: {len(result['missing_sheet'])} decks missing Excel sheets")


def cmd_decklists(args) -> None:
    from ingest.decklists_analyzer import main as analyze_decklists

    analyze_decklists()


def cmd_supply(args) -> None:
    from datetime import date

    from db.engine import create_session_factory
    from ingest.supply import (
        build_card_product_index,
        populate_edhrec_proxy_snapshots,
        pull_supply_snapshots,
        supply_snapshot_summary,
    )

    Session, _ = create_session_factory()
    session = Session()
    try:
        if args.summary:
            print(json.dumps(supply_snapshot_summary(session), indent=2))
            return

        if args.build_index:
            index = build_card_product_index(session, refresh_sets=args.refresh_mtgjson)
            print(f"Resolved tcgplayerProductId for {len(index)} card names")
            if args.write_index:
                from pathlib import Path

                out = Path(args.write_index)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(index, indent=2))
                print(f"Wrote {out}")
            return

        snap_date = date.fromisoformat(args.snapshot_date) if args.snapshot_date else None
        if args.scope == "proxy":
            stats = populate_edhrec_proxy_snapshots(
                session,
                snapshot_date=snap_date,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        else:
            stats = pull_supply_snapshots(
                session,
                scope=args.scope,
                limit=args.limit,
                dry_run=args.dry_run,
                snapshot_date=snap_date,
                use_proxy_fallback=not args.no_proxy_fallback,
                edhrec_max=args.edhrec_max,
            )

        print(f"Supply ingest ({stats.get('source', args.scope)}):")
        for key, value in sorted(stats.items()):
            print(f"  {key}: {value}")
        if not args.dry_run:
            summary = supply_snapshot_summary(session)
            print(f"DB now: {summary['unique_cards']} cards, latest {summary.get('latest_date')}")
    finally:
        session.close()


def cmd_enrich_spike_bible(args) -> None:
    from backtester.spike_sheet_maintenance import enrich_spike_workbook

    stats = enrich_spike_workbook(write=not args.dry_run)
    print(f"Spike bible: {stats['path']}")
    print(f"  Rows: {stats['rows_before']} → {stats['rows_after']} (removed {stats['junk_removed']} junk)")
    print(f"  Pre-con deck name filled: {stats['precon_deck_filled']}")
    print(f"  Pre-con set code filled: {stats['precon_set_code_filled']}")
    if args.dry_run:
        print("  (dry run — file not written)")


def register_ingest_commands(sub: argparse._SubParsersAction) -> None:
    ing = sub.add_parser("ingest", help="Data ingest pipelines")
    ing_sub = ing.add_subparsers(dest="ingest_cmd", required=True)

    p = ing_sub.add_parser("scryfall", help="Download Scryfall bulk data into DB")
    p.add_argument("--mode", choices=["oracle", "printings", "both"], default="both")
    p.set_defaults(func=cmd_scryfall)

    p = ing_sub.add_parser("enrich", help="TCGPlayer descriptions + commander oracle text")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--commander-only", action="store_true")
    p.add_argument("--descriptions-only", action="store_true")
    p.add_argument("--force-descriptions", action="store_true")
    p.add_argument("--refresh-mtgjson", action="store_true")
    p.set_defaults(func=cmd_enrich)

    p = ing_sub.add_parser("prices", help="Price history and spike CSV tools")
    p.add_argument("--verify-spikes-csv", action="store_true")
    p.add_argument("--download-all-prices", action="store_true")
    p.add_argument("--decompress", action="store_true")
    p.add_argument("--cards", nargs="+")
    p.add_argument("--release-date", type=str)
    p.set_defaults(func=cmd_prices)

    p = ing_sub.add_parser("align-precon", help="Infer precon shelf dates from spike CSV")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-matches", type=int, default=3)
    p.add_argument("--min-offset-days", type=int, default=14)
    p.set_defaults(func=cmd_align_precon)

    p = ing_sub.add_parser("refresh-decklists", help="Sync decklists from Excel metadata")
    p.add_argument("--migrate", action="store_true", help="Run migrate_v4 first")
    p.set_defaults(func=cmd_refresh_decklists)

    p = ing_sub.add_parser("decklists", help="Analyze decklist CSVs and populate database")
    p.set_defaults(func=cmd_decklists)

    p = ing_sub.add_parser(
        "enrich-spike-bible",
        help="Clean Spike Reasoning sheet, add Pre-con columns, merge pre-con CSV",
    )
    p.add_argument("--dry-run", action="store_true", help="Report only; do not write xlsx")
    p.set_defaults(func=cmd_enrich_spike_bible)

    p = ing_sub.add_parser(
        "supply",
        help="Bulk supply snapshots (live TCGAPIs or instant EDHREC proxy)",
    )
    p.add_argument(
        "--scope",
        choices=["priority", "all", "proxy"],
        default="priority",
        help="priority=EDHREC+spike cards (default), all=catalog, proxy=EDHREC only",
    )
    p.add_argument("--limit", type=int, default=None, help="Cap cards processed")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--snapshot-date", type=str, help="YYYY-MM-DD (default: today)")
    p.add_argument("--no-proxy-fallback", action="store_true", help="Skip EDHREC fallback on API miss")
    p.add_argument("--edhrec-max", type=int, default=25_000, help="Priority scope rank ceiling")
    p.add_argument("--summary", action="store_true", help="Print snapshot table stats only")
    p.add_argument("--build-index", action="store_true", help="Resolve MTGJSON product IDs only")
    p.add_argument("--write-index", type=str, default=None, metavar="PATH")
    p.add_argument("--refresh-mtgjson", action="store_true", help="Re-download MTGJSON sets for index")
    p.set_defaults(func=cmd_supply)
