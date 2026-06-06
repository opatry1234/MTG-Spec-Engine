"""
Streamlit Page: Results History

Browse backtest runs saved from the Backtest page (or CLI).
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backtester.export import BACKTEST_OUTPUT_DIR, list_saved_runs
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from ui.components.commander_banner import render_commander_banner
from ui.components.grade_panel import render_grade_panel

st.title("Results History")
st.write(
    "Backtest runs are saved automatically under "
    f"`{BACKTEST_OUTPUT_DIR.relative_to(Path(__file__).parent.parent.parent)}` "
    "when you run from the Backtest page."
)

runs = list_saved_runs(limit=40)

if not runs:
    st.info("No saved runs yet. Run a backtest on the **Backtest** page — results are saved automatically.")
    st.stop()

run_labels = []
for r in runs:
    mode = r.get("mode", "?")
    if mode == "deck":
        run_labels.append(f"{r['run_id']} — {r.get('deck_name', 'deck')}")
    elif mode == "skeleton":
        run_labels.append(f"{r['run_id']} — skeleton ({r.get('count', '?')} decks)")
    elif mode == "batch":
        run_labels.append(f"{r['run_id']} — batch ({r.get('deck_count', '?')} decks)")
    else:
        run_labels.append(f"{r['run_id']} — {mode}")

selected_idx = st.selectbox("Saved run", range(len(run_labels)), format_func=lambda i: run_labels[i])
run = runs[selected_idx]
run_dir = Path(run["path"])

st.subheader("Run summary")
summary_cols = st.columns(4)
summary_cols[0].metric("Mode", run.get("mode", "—"))
if run.get("model_version"):
    summary_cols[1].metric("Scorer", run.get("model_version", "—"))
if run.get("stage_label"):
    summary_cols[2].metric("Stage", run.get("stage_label", "—"))
if run.get("mean_omission_hit_rate") is not None:
    summary_cols[3].metric("Mean omission hit", f"{run['mean_omission_hit_rate']:.0%}")
elif run.get("mean_grade_score") is not None:
    summary_cols[3].metric("Mean grade score", f"{run['mean_grade_score']:.2f}")
elif run.get("mean_composition_mae") is not None:
    summary_cols[3].metric("Composition MAE", f"{run['mean_composition_mae']:.2f}")

summary_path = next(run_dir.glob("*_summary.json"), None)
summary_data = {}
if summary_path and summary_path.exists():
    with open(summary_path) as f:
        summary_data = json.load(f)

if run.get("mode") == "deck" and summary_data.get("commander_name"):
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        render_commander_banner(
            session,
            commander_name=summary_data["commander_name"],
            deck_name=summary_data.get("deck_name", run.get("deck_name", "")),
            product=summary_data.get("product", ""),
            stage_label=summary_data.get("stage_label", run.get("stage_label", "")),
        )
    finally:
        session.close()

grade = summary_data.get("grade") or (summary_data.get("metrics", {}) or {}).get("grade")
if grade:
    render_grade_panel(grade)

with st.expander("Full summary JSON", expanded=False):
    if summary_data:
        st.json(summary_data)
    else:
        st.caption("No summary file in this run folder.")

csv_files = sorted(run_dir.glob("*.csv"))
if csv_files:
    st.subheader("Data")
    for csv_path in csv_files:
        df = pd.read_csv(csv_path)
        st.markdown(f"**{csv_path.name}** ({len(df)} rows)")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            f"Download {csv_path.name}",
            df.to_csv(index=False),
            file_name=csv_path.name,
            mime="text/csv",
            key=f"dl_{run['run_id']}_{csv_path.name}",
        )

json_files = [p for p in sorted(run_dir.glob("*.json")) if "summary" not in p.name]
if json_files:
    with st.expander("Deck detail JSON"):
        for json_path in json_files[:3]:
            with open(json_path) as f:
                st.json(json.load(f))
