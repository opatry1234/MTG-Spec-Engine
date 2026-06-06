"""
Streamlit Page: Predict — Phase 3 omitted-card scoring from a public decklist.
"""

import sys
from pathlib import Path

import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import DATABASE_URL, MAX_SPEC_TOP_N, PRODUCT_TYPE_ENUM
from engine.heuristic_scorer import PredictionInput, get_candidates
from engine.pipeline import predict_product
from ui.components.action_log import log_action, make_log_fn, render_action_log_sidebar
from ui.components.card_table import render_card_table
from ui.components.commander_banner import render_commander_banner

st.title("Predict Spec Targets")
st.write("Enter a public decklist and score omitted cards (Phase 3 only).")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

colors = st.multiselect("Color identity", ["W", "U", "B", "R", "G"], default=["U", "B", "R"])
theme = st.text_input("Theme / deck name", value="")
product_description = st.text_area("Product description", height=100)
commander_name = st.text_input("Commander name", value="")
commander_text = st.text_area("Commander oracle text", height=120)
decklist_text = st.text_area(
    "Known decklist (one card per line) *",
    placeholder="Sol Ring\nCommand Tower\n…",
    height=150,
)
known_inclusions = {line.strip() for line in decklist_text.splitlines() if line.strip()}

product_type = st.selectbox("Product type", PRODUCT_TYPE_ENUM, index=0)
product_code = st.text_input("Product set code", value="")
release_year = st.number_input("Release year", min_value=2011, max_value=2030, value=2026)
new_cards = st.number_input("Announced new cards", min_value=0, max_value=50, value=30)
top_n = st.slider("Top N results", 5, MAX_SPEC_TOP_N, MAX_SPEC_TOP_N)
use_ml = st.checkbox("Use ML models (if trained)", value=True)

if st.button("Run Prediction", type="primary"):
    if not product_description.strip():
        st.error("Product description is required.")
        st.stop()
    if not colors:
        st.error("Select at least one color.")
        st.stop()
    if not known_inclusions:
        st.error("Decklist is required.")
        st.stop()

    log = make_log_fn("Predict")
    log_action("Run Prediction clicked", "step")
    session = Session()
    try:
        pred_input = PredictionInput(
            colors=colors,
            commander_text=commander_text,
            commander_name=commander_name,
            theme=theme,
            product_description=product_description,
            new_cards=int(new_cards),
            known_inclusions=known_inclusions,
            product_type=product_type,
            product_code=product_code.upper(),
            release_year=int(release_year),
        )
        kwargs = {"top_n": top_n, "log_fn": log}
        if use_ml:
            try:
                from models.trainer import load_models
                from db.schema import CommanderDeck
                from engine.ml_inference import prepare_ml_inference

                models = load_models()
                dummy = CommanderDeck(
                    colors=colors,
                    commander_text=commander_text,
                    commander_name=commander_name,
                    theme=theme,
                    product_description=product_description,
                    new_cards=int(new_cards),
                    product=product_code.upper() if product_code else "",
                    decklist_reveal_date=pred_input.anchor_date,
                )
                kwargs["scorer"] = "ml"
                kwargs["inclusion_model"] = models["inclusion"]
                kwargs["reprint_model"] = models["reprint"]
                if models.get("spec_spike"):
                    kwargs["spec_spike_model"] = models["spec_spike"]
                cands, features_df, cache = prepare_ml_inference(
                    session, dummy, pred_input, log_fn=log
                )
                kwargs["features_df"] = features_df
                kwargs["feature_cache"] = cache
                kwargs["candidates"] = cands
            except FileNotFoundError:
                st.warning("ML models not found — using heuristic scorer.")

        result = predict_product(session, pred_input, **kwargs)
        if commander_name:
            render_commander_banner(
                session,
                commander_name=commander_name,
                deck_name=theme,
                stage_label="Phase 3 — decklist public",
            )
        render_card_table(result.cards, "Ranked omitted-card targets", session=session)
    finally:
        session.close()

render_action_log_sidebar()
