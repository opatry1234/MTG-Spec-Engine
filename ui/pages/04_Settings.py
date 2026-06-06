"""
Streamlit Page: Settings & Model Training

Train ML models from the UI — no terminal required.
Heavy ML imports are deferred until you click Train models.
"""

import pickle
import sys
from pathlib import Path

import streamlit as st
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import DATA_DIR, DATABASE_URL
from db.schema import CommanderDeck

st.title("Settings & Model Training")

MODELS_DIR = DATA_DIR / "models"
TRAINING_PIPELINE_VERSION = "20260602_spec_spike_plain_english"

st.markdown(
    """
    ### ML workflow (simple version)

    1. **Data** — historical decklists in the database (ingest pipeline)
    2. **Train** — learn inclusion + reprint models from those decklists *(this page)*
    3. **Backtest** — test how well predictions match hidden decks
    4. **Predict** — rank cards for a new product announcement

    Backtest results are **evaluation only** — they do not feed back into training automatically.
    To improve the model, refresh your data if needed, then **Train models** again here.
    """
)


@st.cache_resource
def get_session_factory():
    engine = create_engine(DATABASE_URL)
    return sessionmaker(bind=engine)


@st.cache_data(ttl=60)
def get_training_deck_count() -> int:
    session = get_session_factory()()
    try:
        return (
            session.query(func.count(CommanderDeck.id))
            .filter(
                CommanderDeck.include_in_training == True,
                CommanderDeck.decklist_revealed == True,
            )
            .scalar()
            or 0
        )
    finally:
        session.close()


@st.cache_data(ttl=30)
def get_latest_model_info() -> dict | None:
    meta_files = sorted(MODELS_DIR.glob("meta_v*.pkl"))
    if not meta_files:
        return None
    path = meta_files[-1]
    version = path.stem.replace("meta_v", "")
    with open(path, "rb") as f:
        meta = pickle.load(f)
    return {"version": version, "path": str(path), **meta}


@st.cache_data(ttl=30)
def list_model_versions() -> list[dict]:
    rows = []
    for path in sorted(MODELS_DIR.glob("meta_v*.pkl"), reverse=True):
        version = path.stem.replace("meta_v", "")
        with open(path, "rb") as f:
            meta = pickle.load(f)
        rows.append(
            {
                "version": version,
                "training_rows": meta.get("training_rows"),
                "inclusion": (MODELS_DIR / f"inclusion_v{version}.pkl").exists(),
                "reprint": (MODELS_DIR / f"reprint_v{version}.pkl").exists(),
                "spec_spike": (MODELS_DIR / f"spec_spike_v{version}.pkl").exists(),
            }
        )
    return rows


deck_count = get_training_deck_count()
info = get_latest_model_info()

st.subheader("Current state")
c1, c2 = st.columns(2)
c1.metric("Training decks", deck_count)
if info:
    c2.metric("Active model", info["version"])
    st.caption(
        f"Latest save: {info.get('training_rows', '?')} training rows · "
        f"Backtest/Predict auto-load this version when **Use ML** is checked."
    )
    if info.get("training_summary"):
        from models.training_summary import ensure_plain_english
        from ui.components.training_summary import render_training_summary

        saved_summary = ensure_plain_english(info["training_summary"])
        has_spec = (MODELS_DIR / f"spec_spike_v{info['version']}.pkl").exists()
        if not has_spec:
            st.warning(
                f"Model **{info['version']}** was trained before the spec-spike pipeline "
                f"(no `spec_spike` file). Stop Streamlit fully, relaunch, and **Train models** again "
                f"to pick up golden spike labels + plain-English summary."
            )
        with st.expander("Latest training summary", expanded=bool(saved_summary.get("plain_english"))):
            render_training_summary(saved_summary)
else:
    c2.metric("Active model", "None")
    st.info("No trained models yet. Click **Train models** below.")

st.divider()
st.subheader("Train models")

st.caption(
    "Rebuilds the training set from all decks marked for training, fits XGBoost inclusion "
    "and reprint models, and saves a new versioned snapshot to `data/models/`. "
    "First run may take 1–2 minutes while ML libraries load."
)

if deck_count == 0:
    st.warning("No training decks in the database. Run the ingest pipeline first.")
    st.stop()

if st.session_state.get("last_training_summary"):
    from ui.components.training_summary import render_training_summary

    st.subheader("Last training run (this session)")
    render_training_summary(st.session_state["last_training_summary"])
    st.divider()

if st.button("Train models", type="primary"):
    from features.builder import build_training_set
    from models.trainer import evaluate_models, train_models
    from models.training_summary import build_training_summary
    from ui.components.live_progress import LiveProgressLog, make_live_log_fn
    from ui.components.training_summary import render_training_summary

    with LiveProgressLog("Train", title="Training ML models…") as progress:
        log = make_live_log_fn(progress)
        train_session = get_session_factory()()
        try:
            log(f"Pipeline {TRAINING_PIPELINE_VERSION}", "info")
            log("Loading ML libraries…", "info")
            log(f"Using {deck_count} historical decks", "info")
            log("Building training set (card × deck feature rows)…", "step")
            df = build_training_set(train_session)
            pos_rate = df["label_included"].mean()
            golden = int(df["label_spike_loose"].sum()) if "label_spike_loose" in df.columns else 0
            strict_golden = int(df["label_spec_spike"].sum()) if "label_spec_spike" in df.columns else 0
            log(f"Training set: {len(df):,} rows, {pos_rate:.1%} positive (in-deck)", "ok")
            log(f"Loose omission-spike labels: {golden} (strict golden: {strict_golden})", "ok" if golden >= 2 else "info")

            log("Training inclusion + reprint + spec-spike XGBoost models…", "step")
            models = train_models(train_session, training_df=df)
            has_spike = models.get("spec_spike") is not None
            log(f"Model version {models['version']}", "ok")
            log(
                "Spec spike model: trained"
                if has_spike
                else "Spec spike model: skipped (need ≥2 golden labels)",
                "ok" if has_spike else "info",
            )

            log("Evaluating on training set (sanity check AUC)…", "info")
            metrics = evaluate_models(models, df)
            incl = metrics.get("inclusion_auc", 0)
            rep = metrics.get("reprint_auc", 0)
            spec = metrics.get("spec_spike_auc")
            auc_line = f"AUC — inclusion: {incl:.3f}, reprint: {rep:.3f}"
            if spec is not None:
                auc_line += f", spec spike: {spec:.3f}"
            log(auc_line, "ok")

            from models.trainer import save_models

            save_models(models, models["version"])
            log(f"Saved model weights ({models['version']})", "ok")

            log("Building training summary…", "info")
            from engine.historical_spike_prior import clear_historical_spike_prior_cache

            clear_historical_spike_prior_cache()
            from models.trainer import evaluate_time_holdout

            holdout_metrics = evaluate_time_holdout(train_session, models, df)
            summary = build_training_summary(
                df, models, metrics, train_session, holdout_metrics=holdout_metrics
            )
            models["training_summary"] = summary
            save_models(models, models["version"])

            progress.complete(f"Training complete — {models['version']}")

            get_latest_model_info.clear()
            list_model_versions.clear()

            st.success(f"Models saved as **{models['version']}**")
            m1, m2, m3 = st.columns(3)
            m1.metric("Training rows", f"{models['training_rows']:,}")
            m2.metric("Inclusion AUC", f"{incl:.3f}")
            m3.metric("Reprint AUC", f"{rep:.3f}")
            if metrics.get("spec_spike_auc") is not None:
                st.metric("Spec spike AUC", f"{metrics['spec_spike_auc']:.3f}")

            st.session_state["last_training_summary"] = summary
            render_training_summary(summary)
            st.caption(
                "Inclusion/reprint AUC ~0.97 on training data is normal (the model sees the answers). "
                "Spec spike AUC is the profit-target signal — run a batch backtest to judge real picks."
            )
        except Exception as e:
            progress.fail(str(e))
            st.error(str(e))
        finally:
            train_session.close()

st.divider()
st.subheader("What training does *not* do (yet)")

st.markdown(
    """
    - Does **not** read backtest CSVs or your batch results — labels come from actual decklists only
    - Does **not** automatically fix bad announcement text or skeleton slot counts
    - Does **not** guarantee better announcement-stage backtests (training sees commander text;
      announcement backtests often only have the product blurb)

    **Your loop:** ingest data → **Train models** (here) → **Backtest** → check **Results** → repeat.
    """
)

with st.expander("Advanced: train from terminal"):
    st.code(
        'cd mtg_spec_engine\nsource venv/bin/activate\npython models/trainer_cli.py',
        language="bash",
    )

with st.expander("All saved model versions"):
    versions = list_model_versions()
    if not versions:
        st.caption("No models saved yet.")
    else:
        st.dataframe(versions, use_container_width=True, hide_index=True)
