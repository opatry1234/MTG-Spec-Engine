"""
Unified CLI for MTG Spec Engine.

Run from the project root (mtg_spec_engine/):

    python -m cli --help
    python -m cli batch --limit 30 --use-ml --grade
    python -m cli train --holdout
    python -m cli iterate --train --limit 20 --use-ml
    python -m cli predict --description-file announce.txt --colors U B R --use-ml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on path when invoked as python -m cli
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cli import __version__
from cli.commands.backtest import register_backtest_commands, register_backtest_shortcuts
from cli.commands.combo import register_combo_commands
from cli.commands.data import register_data_commands
from cli.commands.db import register_db_commands
from cli.commands.ingest import register_ingest_commands
from cli.commands.iterate import register_iterate_commands
from cli.commands.predict import register_predict_commands
from cli.commands.results import register_results_commands
from cli.commands.train import register_train_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtg-spec",
        description="MTG Spec Engine — full terminal control (backtest, train, ingest, predict)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # Common workflows (shortcuts)
    register_backtest_shortcuts(sub)

    # Namespaced commands
    bt = sub.add_parser("backtest", help="Backtest commands (same as top-level batch/deck/skeleton)")
    bt_sub = bt.add_subparsers(dest="backtest_cmd", required=True)
    register_backtest_commands(bt_sub)

    register_train_commands(sub)
    register_predict_commands(sub)
    register_ingest_commands(sub)
    register_data_commands(sub)
    register_db_commands(sub)
    register_combo_commands(sub)
    register_results_commands(sub)
    register_iterate_commands(sub)

    # UI launcher
    p_ui = sub.add_parser("ui", help="Launch Streamlit app")
    p_ui.add_argument("--port", type=int, default=8501)
    p_ui.set_defaults(func=_cmd_ui)

    return parser


def _cmd_ui(args) -> None:
    import subprocess

    app = _ROOT / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    print(" ".join(cmd))
    raise SystemExit(subprocess.call(cmd))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
