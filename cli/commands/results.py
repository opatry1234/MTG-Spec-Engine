"""Browse saved backtest runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def cmd_list(args) -> None:
    from backtester.export import BACKTEST_OUTPUT_DIR, list_saved_runs

    runs = list_saved_runs(limit=args.limit)
    if not runs:
        print(f"No runs in {BACKTEST_OUTPUT_DIR}")
        return

    for r in runs:
        mode = r.get("mode", "?")
        line = f"{r['run_id']}  [{mode}]"
        if mode == "batch":
            line += f"  decks={r.get('deck_count', '?')}"
            if r.get("mean_grade_score") is not None:
                line += f"  mean_grade={r['mean_grade_score']:.2f}"
            if r.get("mean_omission_hit_rate") is not None:
                line += f"  omission={r['mean_omission_hit_rate']:.0%}"
        elif mode == "deck":
            line += f"  {r.get('deck_name', '')}"
        print(line)
        if args.verbose:
            print(f"    path: {r.get('path')}")


def cmd_show(args) -> None:
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        from backtester.export import BACKTEST_OUTPUT_DIR

        run_dir = BACKTEST_OUTPUT_DIR / args.run_dir
    if not run_dir.is_dir():
        raise SystemExit(f"Run folder not found: {run_dir}")

    summary = next(run_dir.glob("*_summary.json"), None)
    if summary:
        with open(summary) as f:
            data = json.load(f)
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"No summary JSON in {run_dir}")
        for p in sorted(run_dir.glob("*.csv")):
            print(f"  {p.name}")


def register_results_commands(sub: argparse._SubParsersAction) -> None:
    res = sub.add_parser("results", help="Saved backtest runs")
    res_sub = res.add_subparsers(dest="results_cmd", required=True)

    p = res_sub.add_parser("list", help="List recent runs")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_list)

    p = res_sub.add_parser("show", help="Print batch/deck summary JSON")
    p.add_argument("run_dir", help="Run id or path under backtest_runs/")
    p.set_defaults(func=cmd_show)
