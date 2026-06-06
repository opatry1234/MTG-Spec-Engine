"""Database init and migrations."""

from __future__ import annotations

import argparse


def cmd_init(_args) -> None:
    from db.init_db import init_db

    init_db()


def cmd_migrate(args) -> None:
    migrations = []
    if args.all or args.v1:
        from db.migrate_v1 import migrate_v1

        migrations.append(("v1", migrate_v1))
    if args.all or args.v2:
        from db.migrate_v2 import migrate_v2

        migrations.append(("v2", migrate_v2))
    if args.all or args.v3:
        from db.migrate_v3 import migrate_v3

        migrations.append(("v3", migrate_v3))
    if args.all or args.v4:
        from db.migrate_v4 import migrate_v4

        migrations.append(("v4", migrate_v4))
    if args.all or args.v5:
        from db.migrate_v5 import migrate_v5

        migrations.append(("v5", migrate_v5))

    if not migrations:
        raise SystemExit("Specify --all or one of --v1 … --v5")

    for label, fn in migrations:
        print(f"Running migrate_{label}…")
        fn()
    print("Migrations complete.")


def register_db_commands(sub: argparse._SubParsersAction) -> None:
    db = sub.add_parser("db", help="Database setup")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)

    p = db_sub.add_parser("init", help="Create tables and run base migrations")
    p.set_defaults(func=cmd_init)

    p = db_sub.add_parser("migrate", help="Run schema migrations")
    p.add_argument("--all", action="store_true", help="Run v1–v5 in order")
    for v in ("v1", "v2", "v3", "v4", "v5"):
        p.add_argument(f"--{v}", action="store_true", help=f"Run migrate_{v} only")
    p.set_defaults(func=cmd_migrate)
