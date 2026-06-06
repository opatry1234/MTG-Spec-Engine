"""Product prediction — Phase 3 omitted-card scoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cli._args import add_ml_arguments
from config import MAX_SPEC_TOP_N, PRODUCT_TYPE_ENUM
from db.engine import create_session_factory
from db.schema import CommanderDeck
from engine.heuristic_scorer import PredictionInput
from engine.ml_inference import prepare_ml_inference
from engine.pipeline import predict_product


def cmd_predict(args) -> None:
    colors = [c.upper() for c in args.colors]
    if not colors:
        raise SystemExit("Provide at least one color with --colors W U B …")

    description = args.description
    if args.description_file:
        description = args.description_file.read_text(encoding="utf-8")
    if not (description or "").strip():
        raise SystemExit("Product description required (--description or --description-file)")

    known_inclusions = set()
    if args.decklist_file:
        known_inclusions = {
            line.strip()
            for line in args.decklist_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    if not known_inclusions:
        raise SystemExit("Phase 3 requires --decklist-file with known decklist cards")

    pred_input = PredictionInput(
        colors=colors,
        commander_text=args.commander_text or "",
        commander_name=args.commander_name or "",
        theme=args.theme or "",
        product_description=description,
        new_cards=args.new_cards,
        known_inclusions=known_inclusions,
        product_type=args.product_type,
        release_year=args.release_year,
        product_code=(args.product_code or "").upper(),
    )

    kwargs = {"top_n": args.top_n}

    Session, _ = create_session_factory()
    session = Session()
    try:
        if args.use_ml:
            from models.trainer import load_models

            models = load_models(args.model_version)
            dummy = CommanderDeck(
                colors=colors,
                commander_text=pred_input.commander_text,
                commander_name=pred_input.commander_name,
                theme=pred_input.theme,
                product_description=description,
                new_cards=args.new_cards,
                product=(args.product_code or "").upper(),
                decklist_reveal_date=pred_input.anchor_date,
            )
            cands, features_df, cache = prepare_ml_inference(session, dummy, pred_input)
            kwargs["scorer"] = "ml"
            kwargs["inclusion_model"] = models["inclusion"]
            kwargs["reprint_model"] = models["reprint"]
            if models.get("spec_spike") is not None:
                kwargs["spec_spike_model"] = models["spec_spike"]
            kwargs["features_df"] = features_df
            kwargs["feature_cache"] = cache
            kwargs["candidates"] = cands
            scorer_label = models["version"]
        else:
            scorer_label = "heuristic"

        result = predict_product(session, pred_input, **kwargs)
        preds = result.cards

        print(f"Predict (Phase 3)  scorer={scorer_label}  top_n={args.top_n}")
        for i, row in enumerate(preds.itertuples(), start=1):
            score = getattr(row, "opportunity_score", None)
            score_s = f"{score:.3f}" if score is not None else "—"
            print(f"  {i:2}. {row.card_name}  ({score_s})")

        if args.json_out:
            payload = {
                "stage": "decklist_revealed",
                "scorer": scorer_label,
                "top_n": args.top_n,
                "predictions": preds.to_dict(orient="records"),
            }
            out = Path(args.json_out.name)
            out.write_text(json.dumps(payload, indent=2, default=str))
            print(f"Wrote {out}")

        if args.csv_out:
            preds.to_csv(args.csv_out.name, index=False)
            print(f"Wrote {args.csv_out.name}")
    finally:
        session.close()


def register_predict_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("predict", help="Rank omitted-card spec targets from a public decklist")
    add_ml_arguments(p)
    p.add_argument(
        "--colors",
        nargs="+",
        default=["U", "B", "R"],
        help="Color identity letters (default: U B R)",
    )
    p.add_argument("--theme", default="", help="Deck / product theme label")
    p.add_argument("--description", default=None, help="Product announcement text")
    p.add_argument("--description-file", type=argparse.FileType("r"), default=None)
    p.add_argument("--commander-name", default="")
    p.add_argument("--commander-text", default="")
    p.add_argument("--new-cards", type=int, default=30)
    p.add_argument("--product-code", default="", help="Set code for same-product omission detection")
    p.add_argument(
        "--product-type",
        choices=PRODUCT_TYPE_ENUM,
        default="universes_beyond",
    )
    p.add_argument("--release-year", type=int, default=2026)
    p.add_argument("--decklist-file", type=argparse.FileType("r"), required=True)
    p.add_argument("--top-n", type=int, default=MAX_SPEC_TOP_N)
    p.add_argument("--json-out", type=argparse.FileType("w"), default=None)
    p.add_argument("--csv-out", type=argparse.FileType("w"), default=None)
    p.set_defaults(func=cmd_predict)
