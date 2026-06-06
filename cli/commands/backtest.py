"""Backtest commands (batch, deck)."""

from __future__ import annotations

import argparse
from typing import Optional

from backtester.backtest import backtest_all_decks, run_backtest
from backtester.export import enrich_batch_summary, save_batch_run, save_deck_run
from cli._args import add_grade_arguments, add_ml_arguments, add_run_id_argument
from db.engine import create_session_factory
from db.schema import CommanderDeck


def _load_models(use_ml: bool, model_version: str = "latest"):
    if not use_ml:
        return None
    from models.trainer import load_models

    return load_models(model_version)


def _resolve_deck(session, deck_id: Optional[int], deck_name: Optional[str]) -> CommanderDeck:
    if deck_id is not None:
        deck = session.query(CommanderDeck).get(deck_id)
        if not deck:
            raise SystemExit(f"Deck id {deck_id} not found")
        return deck
    if deck_name:
        deck = session.query(CommanderDeck).filter(CommanderDeck.deck_name == deck_name).first()
        if not deck:
            raise SystemExit(f"Deck '{deck_name}' not found")
        return deck
    raise SystemExit("Provide --id or --name for deck backtest")


def cmd_batch(args) -> None:
    models = _load_models(args.use_ml, args.model_version)
    fetch_spikes = not args.no_spike_prices

    Session, _ = create_session_factory()
    session = Session()
    try:
        df = backtest_all_decks(
            session,
            models=models,
            top_n=args.top_n,
            limit=args.limit,
            grade_predictions=args.grade_predictions,
            fetch_spike_prices=fetch_spikes,
            parallel_workers=args.parallel_workers,
            gradeable_only=getattr(args, "gradeable_only", False),
        )

        summary = {
            "mode": "batch",
            "stage": "decklist_revealed",
            "stage_label": "Phase 3 — decklist public",
            "top_n": args.top_n,
            "deck_count": len(df),
            "use_ml": args.use_ml,
            "model_version": models.get("version") if models else "heuristic",
            "grade_predictions": args.grade_predictions,
        }
        if not df.empty and "omission_hit_rate" in df.columns:
            valid = df["omission_hit_rate"].dropna()
            if not valid.empty:
                summary["mean_omission_hit_rate"] = float(valid.mean())

        summary = enrich_batch_summary(summary, df)
        out_dir = save_batch_run(summary, df, run_id=args.run_id)

        print(f"Batch backtest: {len(df)} decks (Phase 3)")
        if summary.get("mean_omission_hit_rate") is not None:
            print(f"  Mean omission hit rate: {summary['mean_omission_hit_rate']:.0%}")
        if summary.get("mean_grade_score") is not None:
            print(f"  Mean grade score:         {summary['mean_grade_score']:.2f}")
        if not df.empty and "letter_grade" in df.columns:
            graded = df["letter_grade"].dropna()
            if not graded.empty:
                print(f"  Graded decks: {len(graded)}/{len(df)}")
                for letter, count in graded.value_counts().sort_index().items():
                    print(f"    {letter}: {count}")
        print(f"  Scorer: {summary['model_version']}")
        print(f"Saved → {out_dir}")
    finally:
        session.close()


def cmd_deck(args) -> None:
    models = _load_models(args.use_ml, args.model_version)
    fetch_spikes = not args.no_spike_prices

    Session, _ = create_session_factory()
    session = Session()
    try:
        deck = _resolve_deck(session, args.id, args.name)
        result = run_backtest(
            deck.id,
            session,
            models=models,
            top_n=args.top_n,
            grade_predictions=args.grade_predictions,
            fetch_spike_prices=fetch_spikes,
        )

        payload = {
            "deck_id": deck.id,
            "deck_name": deck.deck_name,
            "product": deck.product,
            "commander_name": deck.commander_name,
            "stage": "decklist_revealed",
            "stage_label": "Phase 3 — decklist public",
            "top_n": args.top_n,
            "use_ml": args.use_ml,
            "model_version": models.get("version") if models else "heuristic",
            "metrics": result["metrics"],
            "visible_fields": result["visible_fields"],
            "data_warning": result.get("data_warning"),
            "grade": result.get("grade"),
        }

        m = result["metrics"]
        print(f"Deck backtest: {deck.deck_name} ({deck.product})")
        print(f"  Scorer: {payload['model_version']}")
        if m.get("top_n_omission_hit_rate") is not None:
            print(f"  Omission hit rate: {m['top_n_omission_hit_rate']:.0%}")
        grade = result.get("grade")
        if grade:
            print(
                f"  Spec grade: {grade['letter']} "
                f"({grade.get('golden_specs_found', 0)}/{grade.get('golden_spec_count', 0)} "
                f"golden specs in top {grade.get('evaluation_top_n', args.top_n)})"
            )

        if args.save:
            out_dir = save_deck_run(payload, result["predictions"], run_id=args.run_id)
            print(f"Saved → {out_dir}")
        elif args.json_out:
            import json

            payload["predictions"] = result["predictions"].to_dict(orient="records")
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(payload, indent=2, default=str))
            print(f"Wrote {args.json_out}")
    finally:
        session.close()


def _add_batch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=10, help="Number of decks (default 10)")
    parser.add_argument(
        "--gradeable-only", action="store_true",
        help="Only backtest decks that have golden-spec benchmarks (skip ungradeable decks)",
    )
    parser.add_argument("--top-n", type=int, default=20, help="Top N predictions per deck")
    add_ml_arguments(parser)
    add_run_id_argument(parser)
    add_grade_arguments(parser)
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=0,
        help="Process pool size (0 = sequential, default)",
    )


def _add_deck_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", type=int, default=None, help="Deck database id")
    parser.add_argument("--name", type=str, default=None, help="Deck name (exact match)")
    parser.add_argument("--top-n", type=int, default=20)
    add_ml_arguments(parser)
    add_run_id_argument(parser)
    add_grade_arguments(parser)
    parser.add_argument("--save", action="store_true", help="Save under backtest_runs/")
    parser.add_argument("--json-out", type=argparse.FileType("w"), default=None, help="Write JSON payload")


def register_backtest_commands(sub: argparse._SubParsersAction) -> None:
    p_batch = sub.add_parser("batch", help="Omitted-card backtest across decks")
    _add_batch_args(p_batch)
    p_batch.set_defaults(func=cmd_batch)

    p_deck = sub.add_parser("deck", help="Full backtest for one deck")
    _add_deck_args(p_deck)
    p_deck.set_defaults(func=cmd_deck)


def register_backtest_shortcuts(sub: argparse._SubParsersAction) -> None:
    p_batch = sub.add_parser("batch", help="Shortcut for backtest batch")
    _add_batch_args(p_batch)
    p_batch.set_defaults(func=cmd_batch)

    p_deck = sub.add_parser("deck", help="Shortcut for backtest deck")
    _add_deck_args(p_deck)
    p_deck.set_defaults(func=cmd_deck)


def backtest_only_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run MTG Spec Engine backtests offline")
    sub = parser.add_subparsers(dest="command", required=True)
    register_backtest_commands(sub)
    args = parser.parse_args(argv)
    args.func(args)
