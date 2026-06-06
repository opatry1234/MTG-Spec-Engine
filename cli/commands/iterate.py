"""Train → batch backtest loop for terminal-driven iteration."""

from __future__ import annotations

import argparse

from cli.commands.backtest import cmd_batch
from cli.commands.train import cmd_train
from models.trainer import DEFAULT_HOLDOUT_CUTOFF


def cmd_iterate(args) -> None:
    if args.train:
        print("=== Step 1: Train models ===")
        train_ns = argparse.Namespace(
            max_decks=args.max_decks,
            holdout=args.holdout,
            holdout_cutoff=args.holdout_cutoff,
            summary=args.summary,
            json_out=args.train_json_out,
        )
        cmd_train(train_ns)

    if not args.skip_batch:
        print("\n=== Step 2: Batch backtest ===")
        cmd_batch(args)

    print("\nIterate complete.")


def register_iterate_commands(sub: argparse._SubParsersAction) -> None:
    from cli.commands.backtest import _add_batch_args

    p = sub.add_parser(
        "iterate",
        help="Train models (optional) then run batch backtest — one command loop",
    )
    p.add_argument("--train", action="store_true", help="Train models before batch backtest")
    p.add_argument("--skip-batch", action="store_true", help="Train only, skip batch step")
    p.add_argument("--max-decks", type=int, default=None, help="Training deck limit")
    p.add_argument("--holdout", action="store_true", help="Time holdout eval during train")
    p.add_argument(
        "--holdout-cutoff",
        default=DEFAULT_HOLDOUT_CUTOFF.isoformat(),
    )
    p.add_argument("--no-summary", dest="summary", action="store_false", default=True)
    p.add_argument("--train-json-out", type=argparse.FileType("w"), default=None)
    _add_batch_args(p)
    p.set_defaults(func=cmd_iterate)
