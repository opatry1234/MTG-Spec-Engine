"""Display backtest letter grade at top of results."""

import streamlit as st


_GRADE_COLORS = {
    "A+": "#16a34a",
    "A": "#16a34a",
    "A-": "#22c55e",
    "B+": "#84cc16",
    "B": "#eab308",
    "B-": "#f59e0b",
    "C+": "#f97316",
    "C": "#fb923c",
    "C-": "#f87171",
    "D": "#ef4444",
    "F": "#dc2626",
}


def render_grade_panel(grade: dict | None) -> None:
    if not grade:
        return

    letter = grade.get("letter", "—")
    color = _GRADE_COLORS.get(letter, "#64748b")
    golden_found = grade.get("golden_specs_found", grade.get("good_picks", 0))
    golden_total = grade.get("golden_spec_count", 0)
    eval_n = grade.get("evaluation_top_n", grade.get("top_n", 10))
    recall = grade.get("golden_recall", grade.get("score", 0))
    omission = grade.get("omission_hits", 0)
    cards_graded = grade.get("cards_graded", eval_n)

    st.markdown(
        f"""
        <div style="
            border: 2px solid {color};
            border-radius: 12px;
            padding: 1rem 1.25rem;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, {color}18, transparent);
        ">
            <div style="display:flex;align-items:center;gap:1.25rem;flex-wrap:wrap;">
                <div style="font-size:3rem;font-weight:800;color:{color};line-height:1;">
                    {letter}
                </div>
                <div>
                    <div style="font-size:1.1rem;font-weight:600;">Spec Target Grade</div>
                    <div style="color:#64748b;margin-top:0.25rem;">
                        {golden_found}/{golden_total} actual specs found in top {eval_n}
                        ({recall:.0%} recall)
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Golden specs found", f"{golden_found}/{golden_total}")
    c2.metric("Omission hits", f"{omission}/{cards_graded}")
    c3.metric("Recall", f"{recall:.0%}")

    if grade.get("note"):
        st.caption(grade["note"])

    window_start = grade.get("spike_window_start")
    window_end = grade.get("spike_window_end")
    if window_start and window_end:
        st.caption(f"Spike window: {window_start} → {window_end}")

    golden = grade.get("golden_spikes") or []
    if golden:
        found = sum(1 for row in golden if row.get("in_top_predictions"))
        with st.expander(
            f"Golden spike benchmarks ({found}/{len(golden)} found in top picks)",
            expanded=found < len(golden),
        ):
            st.caption(
                "Cards that spiked near release, were omitted from the actual precon, "
                "and fit this deck's theme (announcement-window demand). "
                "Shelf-day batch reports and low-synergy spikes are excluded."
            )
            for row in golden:
                icon = "✅" if row.get("in_top_predictions") else "⚠️"
                pct = row.get("spike_pct") or 0
                attr = row.get("attribution") or "spike"
                spike_reason = row.get("spike_reason") or ""
                spike_type = row.get("spike_type") or ""
                label = (
                    f"{icon} **{row['card_name']}** — "
                    f"+{pct:.0%} on {row.get('report_date')} ({attr}) "
                    f"(${row.get('baseline_price')}→${row.get('peak_price')}, "
                    f"{row.get('set_name')})"
                )
                if spike_type:
                    label += f" *[{spike_type}]*"
                st.write(label)
                if spike_reason:
                    st.caption(f"↳ {spike_reason}")

    cards = grade.get("cards") or []
    if cards:
        with st.expander("Grade breakdown by card", expanded=False):
            for row in cards:
                icon = "✅" if row.get("good_pick") else "❌"
                if row.get("golden_spec") and not row.get("good_pick"):
                    icon = "⚠️"
                parts = []
                if row.get("not_in_deck"):
                    parts.append("not in deck")
                else:
                    parts.append("in deck")
                if row.get("golden_spec"):
                    parts.append("actual spec target")
                if row.get("has_infinite_loop"):
                    parts.append(f"infinite loop with {row.get('combo_with') or 'anchor'}")
                if not row.get("spec_eligible", True):
                    earliest = row.get("earliest_printing") or "unknown"
                    release = grade.get("release_date") or "release"
                    parts.append(
                        f"not spec-eligible (first printing {earliest}, after {release})"
                    )
                elif row.get("had_spike") and row.get("precon_attributed"):
                    pct = row.get("spike_pct") or 0
                    attr = row.get("attribution") or "spike"
                    if row.get("report_date"):
                        parts.append(
                            f"{attr} spike on {row['report_date']} (+{pct:.0%}, "
                            f"${row.get('baseline_price')}→${row.get('peak_price')})"
                        )
                    else:
                        parts.append(f"{attr} spike +{pct:.0%}")
                st.write(f"{icon} **{row['rank']}. {row['card_name']}** — {', '.join(parts)}")
