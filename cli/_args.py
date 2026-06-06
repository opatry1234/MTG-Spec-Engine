"""Shared argparse helpers."""

from __future__ import annotations

import argparse


def add_ml_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--use-ml", action="store_true", help="Use trained XGBoost models")
    parser.add_argument(
        "--model-version",
        default="latest",
        help="Model version tag or 'latest' (default: latest)",
    )


def add_run_id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-id",
        default=None,
        help="Output folder name under data/analytics/backtest_runs/",
    )


def add_grade_arguments(parser: argparse.ArgumentParser, *, default_grade: bool = True) -> None:
    grade = parser.add_mutually_exclusive_group()
    grade.add_argument(
        "--grade",
        dest="grade_predictions",
        action="store_true",
        default=default_grade,
        help="Grade spec targets against spike bible (default: on)",
    )
    grade.add_argument(
        "--no-grade",
        dest="grade_predictions",
        action="store_false",
        help="Skip letter-grade evaluation",
    )
    parser.add_argument(
        "--no-spike-prices",
        action="store_true",
        help="Skip spike price lookups during grading (faster)",
    )
