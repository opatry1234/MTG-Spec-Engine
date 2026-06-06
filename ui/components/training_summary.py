"""Render post-training summary in Streamlit."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_training_summary(summary: dict | None) -> None:
    if not summary:
        return

    st.subheader("What the model learned")
    st.caption(f"Training run **{summary.get('version', '?')}**")

    plain = summary.get("plain_english")
    if plain:
        with st.container(border=True):
            st.markdown(plain)

    with st.expander("Technical details", expanded=False):
        for line in summary.get("insights") or []:
            st.markdown(f"- {line}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Training decks", summary.get("training_decks", "—"))
    m2.metric("Training rows", f"{summary.get('training_rows', 0):,}")
    pos = summary.get("positive_rate")
    m3.metric("In-deck rate", f"{pos:.1%}" if pos is not None else "—")
    golden = summary.get("spec_spike_positives")
    m4.metric("Golden spike labels", golden if golden is not None else "—")

    metrics = summary.get("metrics") or {}
    c1, c2, c3 = st.columns(3)
    if metrics.get("inclusion_auc") is not None:
        c1.metric("Inclusion AUC", f"{metrics['inclusion_auc']:.3f}")
    if metrics.get("reprint_auc") is not None:
        c2.metric("Reprint AUC", f"{metrics['reprint_auc']:.3f}")
    if metrics.get("spec_spike_auc") is not None:
        c3.metric("Spec spike AUC", f"{metrics['spec_spike_auc']:.3f}")

    means = summary.get("feature_means") or {}
    inc = (means.get("included") or {}).get("historical_omission_spike_score")
    omit = (means.get("omitted") or {}).get("historical_omission_spike_score")
    if inc is not None and omit is not None:
        st.caption(
            f"Avg historical spike signal — in-deck cards: **{inc:.2f}**, "
            f"left out: **{omit:.2f}**"
        )

    cols = st.columns(3)
    panels = [
        ("Inclusion model", summary.get("inclusion_top_features") or []),
        ("Reprint model", summary.get("reprint_top_features") or []),
        ("Spec spike model", summary.get("spec_spike_top_features") or []),
    ]
    for col, (title, rows) in zip(cols, panels):
        with col:
            st.markdown(f"**{title} — top signals**")
            if rows:
                st.dataframe(
                    pd.DataFrame(rows)[["label", "share_pct"]].rename(
                        columns={"label": "Feature", "share_pct": "Weight %"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("Not trained or no importances.")

    products = summary.get("product_counts") or {}
    if products:
        with st.expander("Training decks by product", expanded=False):
            for product, count in sorted(products.items(), key=lambda x: (-x[1], x[0])):
                st.write(f"**{product}** — {count} deck(s)")

    if metrics:
        with st.expander("Raw evaluation metrics", expanded=False):
            st.json(metrics)
