"""
Streamlit Page: Backtest — Phase 3 omitted-card scoring.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import MAX_SPEC_TOP_N
from db.schema import CommanderDeck
from ui.components.action_log import log_action, render_action_log_sidebar
from ui.components.commander_banner import render_commander_card, render_commander_deck_meta
from ui.db_session import get_session_factory

st.title("Backtest Historical Decks")
st.caption(
    "Phase 3 only: score omitted cards from public decklists, anchored to reveal date. "
    "Runs save automatically to **Results**."
)


@st.cache_data(ttl=120)
def load_deck_rows() -> list[tuple]:
    """Lightweight deck list for the selector (no heavy ML/backtest imports)."""
    session = get_session_factory()()
    try:
        return (
            session.query(
                CommanderDeck.id,
                CommanderDeck.deck_name,
                CommanderDeck.product,
                CommanderDeck.commander_name,
            )
            .filter(CommanderDeck.decklist_revealed == True)
            .order_by(CommanderDeck.release_date.desc())
            .all()
        )
    finally:
        session.close()


def _load_models(use_ml: bool, log):
    if not use_ml:
        return None
    log("Loading ML models", "info")
    try:
        from models.trainer import load_models

        return load_models()
    except FileNotFoundError:
        st.error("No trained models found. Run training first, or disable ML.")
        return None


deck_rows = load_deck_rows()
if not deck_rows:
    st.warning("No decks in database. Run the ingest pipeline first.")
    st.stop()

deck_options = {f"{name} ({product})": deck_id for deck_id, name, product, _ in deck_rows}
selected_label = st.selectbox("Select deck", list(deck_options.keys()))
selected_id = deck_options[selected_label]
selected = next(row for row in deck_rows if row.id == selected_id)

col_card, col_settings = st.columns([1, 2.2], gap="medium")
preview_session = get_session_factory()()
try:
    with col_card:
        if selected.commander_name:
            render_commander_card(preview_session, selected.commander_name)
    with col_settings:
        render_commander_deck_meta(
            commander_name=selected.commander_name or "",
            deck_name=selected.deck_name,
            product=selected.product,
            stage_label="Phase 3 — decklist public",
        )
        st.subheader("Settings")
        top_n = st.slider("Top N predictions", 5, MAX_SPEC_TOP_N, MAX_SPEC_TOP_N)
        use_ml = st.checkbox("Use ML models (if trained)", value=False)
        grade_specs = st.checkbox(
            "Grade spec targets (Spike Reasoning sheet)",
            value=True,
        )
        fetch_spike_prices = st.checkbox(
            "Fetch historical spike prices for grading (slow — uses price APIs/cache)",
            value=False,
        )
        batch_limit = st.slider("Batch backtest deck count", 5, 161, 10)
finally:
    preview_session.close()

if st.button("Run Backtest", type="primary"):
    from backtester.backtest import run_backtest
    from backtester.export import save_deck_run
    from ui.components.card_table import render_card_table
    from ui.components.grade_panel import render_grade_panel
    from ui.components.live_progress import LiveProgressLog, make_live_log_fn

    log_action("Run Backtest clicked", "step")
    with LiveProgressLog("Backtest", total=1, title="Running backtest…") as progress:
        log = make_live_log_fn(progress)
        session = get_session_factory()()
        try:
            models = _load_models(use_ml, log)
            if use_ml and models is None:
                st.stop()

            progress.step(0, f"Scoring {selected_label}")
            result = run_backtest(
                selected_id,
                session,
                models=models,
                top_n=top_n,
                log_fn=log,
                grade_predictions=grade_specs,
                fetch_spike_prices=fetch_spike_prices,
            )

            payload = {
                "deck_id": selected.id,
                "deck_name": selected.deck_name,
                "product": selected.product,
                "commander_name": selected.commander_name,
                "stage": "decklist_revealed",
                "stage_label": "Phase 3 — decklist public",
                "top_n": top_n,
                "use_ml": use_ml,
                "model_version": models.get("version") if models else "heuristic",
                "metrics": result["metrics"],
                "grade": result.get("grade"),
                "visible_fields": result["visible_fields"],
                "data_warning": result.get("data_warning"),
            }
            saved_dir = save_deck_run(payload, result["predictions"])
            st.success(f"Saved to Results → `{saved_dir.name}`")

            if result.get("grade"):
                render_grade_panel(result["grade"])
            if result.get("data_warning"):
                st.warning(result["data_warning"])

            with st.expander("Scoring context", expanded=True):
                for label, value in result["visible_fields"].items():
                    st.write(f"**{label}:** {value}")

            m = result["metrics"]
            c1, c2 = st.columns(2)
            c1.metric("Omitted targets ranked", m.get("spec_targets_ranked", 0))
            c2.metric("Avg opportunity score", m.get("avg_opportunity_score", 0))

            render_card_table(result["predictions"], "Ranked omitted-card targets", session=session)
            progress.complete(f"Backtest complete — {selected.deck_name}")
        except ValueError as e:
            progress.fail(str(e))
            st.error(str(e))
        finally:
            session.close()

st.divider()
st.subheader("Batch backtest")

if st.button(f"Run batch backtest ({batch_limit} decks)"):
    from backtester.backtest import backtest_all_decks
    from backtester.export import enrich_batch_summary, save_batch_run
    from ui.components.live_progress import LiveProgressLog, make_live_log_fn

    with LiveProgressLog("Batch", total=batch_limit, title="Batch backtest…") as progress:
        log = make_live_log_fn(progress)
        session = get_session_factory()()
        try:
            models = _load_models(use_ml, log)
            if use_ml and models is None:
                st.stop()

            summary_df = backtest_all_decks(
                session,
                models=models,
                top_n=top_n,
                limit=batch_limit,
                log_fn=log,
                save_runs=False,
                grade_predictions=grade_specs,
                fetch_spike_prices=fetch_spike_prices,
            )
            batch_summary = enrich_batch_summary(
                {
                    "mode": "batch",
                    "stage": "decklist_revealed",
                    "top_n": top_n,
                    "deck_count": len(summary_df),
                    "use_ml": use_ml,
                    "model_version": models.get("version") if models else "heuristic",
                },
                summary_df,
            )
            saved_dir = save_batch_run(batch_summary, summary_df)
            st.success(f"Saved to Results → `{saved_dir.name}`")
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
            progress.complete(f"Batch complete — {len(summary_df)} decks")
        finally:
            session.close()

render_action_log_sidebar()
