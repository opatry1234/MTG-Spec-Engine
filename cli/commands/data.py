"""Analytics / feature rebuild commands."""

from __future__ import annotations

import argparse


def cmd_deck_features(args) -> None:
    if args.migrate:
        from db.migrate_v3 import migrate_v3

        migrate_v3()
    from analytics.deck_features import rebuild_all_deck_features

    n = rebuild_all_deck_features(backfill_metadata=not args.no_metadata)
    print(f"Rebuilt deck_features for {n} decks")


def cmd_historical_distributions(args) -> None:
    raise SystemExit("Skeleton distributions removed in Phase-3-only refactor.")


def cmd_auto_includes(_args) -> None:
    from analytics.auto_includes import OUTPUT_PATH, compute_auto_includes

    result = compute_auto_includes()
    print(f"Tier-1 global staples: {len(result['global']['tier1'])}")
    print(f"Tier-2 global includes: {len(result['global']['tier2'])}")
    print(f"Written to {OUTPUT_PATH}")


def register_data_commands(sub: argparse._SubParsersAction) -> None:
    data = sub.add_parser("data", help="Rebuild analytics features and priors")
    data_sub = data.add_subparsers(dest="data_cmd", required=True)

    p = data_sub.add_parser("deck-features", help="Rebuild deck_features for all decks")
    p.add_argument("--rebuild", action="store_true", help="Accepted for compatibility")
    p.add_argument("--no-metadata", action="store_true")
    p.add_argument("--migrate", action="store_true", help="Run migrate_v3 first")
    p.set_defaults(func=cmd_deck_features)

    p = data_sub.add_parser("distributions", help="Build skeleton historical distributions")
    p.set_defaults(func=cmd_historical_distributions)

    p = data_sub.add_parser("auto-includes", help="Rebuild auto-include priors")
    p.set_defaults(func=cmd_auto_includes)
