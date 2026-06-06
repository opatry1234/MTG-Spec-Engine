"""Model training commands."""

from __future__ import annotations

import argparse
import json
from datetime import date

from cli._args import add_run_id_argument
from db.engine import create_session_factory
from features.builder import build_training_set
from models.trainer import (
    DEFAULT_HOLDOUT_CUTOFF,
    evaluate_models,
    evaluate_time_holdout,
    save_models,
    train_models,
)


def cmd_train(args) -> None:
    Session, _ = create_session_factory()
    session = Session()
    try:
        print("Building training set…")
        df = build_training_set(session, max_decks=args.max_decks)
        if df.empty:
            raise SystemExit("No training data — ingest decklists and rebuild deck_features first.")

        pos_rate = df["label_included"].mean()
        golden = int(df["label_spike_loose"].sum()) if "label_spike_loose" in df.columns else 0
        strict = int(df["label_spec_spike"].sum()) if "label_spec_spike" in df.columns else 0
        print(f"  Rows: {len(df):,}  inclusion positive rate: {pos_rate:.1%}")
        print(f"  Loose spike labels: {golden}  strict golden: {strict}")

        print("Training inclusion + reprint + spec-spike models…")
        models = train_models(session, training_df=df, max_decks=args.max_decks)

        print("Evaluating on training set…")
        metrics = evaluate_models(models, df)
        incl = metrics.get("inclusion_auc", 0)
        rep = metrics.get("reprint_auc", 0)
        spec = metrics.get("spec_spike_auc")
        line = f"  AUC — inclusion: {incl:.3f}, reprint: {rep:.3f}"
        if spec is not None:
            line += f", spec spike: {spec:.3f}"
        print(line)

        holdout_metrics = {}
        if args.holdout:
            cutoff = date.fromisoformat(args.holdout_cutoff)
            holdout_metrics = evaluate_time_holdout(session, models, df, cutoff=cutoff)
            if holdout_metrics.get("holdout_rows"):
                print(
                    f"  Holdout ({cutoff}+): {holdout_metrics['holdout_decks']} decks, "
                    f"{holdout_metrics['holdout_rows']} rows"
                )
                for key in ("inclusion_auc", "reprint_auc", "spec_spike_auc"):
                    if key in holdout_metrics:
                        print(f"    {key}: {holdout_metrics[key]:.3f}")

        if args.summary:
            from engine.historical_spike_prior import clear_historical_spike_prior_cache
            from models.training_summary import build_training_summary

            clear_historical_spike_prior_cache()
            summary = build_training_summary(
                df, models, metrics, session, holdout_metrics=holdout_metrics or None
            )
            models["training_summary"] = summary
            if args.json_out:
                args.json_out.parent.mkdir(parents=True, exist_ok=True)
                args.json_out.write_text(json.dumps(summary, indent=2, default=str))
                print(f"  Training summary → {args.json_out}")

        save_models(models, models["version"])
        print(f"Saved model version: {models['version']}")
        print("Use with: python -m cli batch --use-ml  (or backtest deck --use-ml)")
    finally:
        session.close()


def cmd_models_list(args) -> None:
    from config import DATA_DIR
    import pickle

    models_dir = DATA_DIR / "models"
    meta_files = sorted(models_dir.glob("meta_v*.pkl"), reverse=True)
    if not meta_files:
        print("No trained models found.")
        return

    limit = args.limit or len(meta_files)
    for path in meta_files[:limit]:
        version = path.stem.replace("meta_v", "")
        with open(path, "rb") as f:
            meta = pickle.load(f)
        rows = meta.get("training_rows", "?")
        spec = (models_dir / f"spec_spike_v{version}.pkl").exists()
        print(f"  {version}  rows={rows}  spec_spike={'yes' if spec else 'no'}")


def register_train_commands(sub: argparse._SubParsersAction) -> None:
    p_train = sub.add_parser("train", help="Train inclusion, reprint, and spec-spike models")
    p_train.add_argument("--max-decks", type=int, default=None, help="Limit training decks")
    p_train.add_argument(
        "--holdout",
        action="store_true",
        help="Evaluate time holdout after training",
    )
    p_train.add_argument(
        "--holdout-cutoff",
        default=DEFAULT_HOLDOUT_CUTOFF.isoformat(),
        help=f"Holdout decks on/after this date (default {DEFAULT_HOLDOUT_CUTOFF})",
    )
    p_train.add_argument(
        "--summary",
        action="store_true",
        default=True,
        help="Build plain-English training summary (default: on)",
    )
    p_train.add_argument("--no-summary", dest="summary", action="store_false")
    p_train.add_argument("--json-out", type=argparse.FileType("w"), default=None)
    p_train.set_defaults(func=cmd_train)

    p_models = sub.add_parser("models", help="List saved model versions")
    m_sub = p_models.add_subparsers(dest="models_cmd", required=True)
    p_list = m_sub.add_parser("list", help="List model versions")
    p_list.add_argument("--limit", type=int, default=10)
    p_list.set_defaults(func=cmd_models_list)
