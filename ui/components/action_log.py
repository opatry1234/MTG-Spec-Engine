"""
Sidebar action log for Predict and Backtest runs.

Persists in st.session_state so entries survive navigation until cleared.
"""

from datetime import datetime
from typing import Callable, List, Optional

import streamlit as st

LOG_KEY = "action_log"
MAX_ENTRIES = 80

LEVEL_ICONS = {
    "info": "ℹ️",
    "step": "→",
    "ok": "✅",
    "warn": "⚠️",
    "error": "❌",
}


def init_log() -> None:
    if LOG_KEY not in st.session_state:
        st.session_state[LOG_KEY] = []


def clear_log() -> None:
    st.session_state[LOG_KEY] = []


def log_action(message: str, level: str = "info") -> None:
    """Append a timestamped line to the session log."""
    init_log()
    st.session_state[LOG_KEY].append(
        {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
    )
    if len(st.session_state[LOG_KEY]) > MAX_ENTRIES:
        st.session_state[LOG_KEY] = st.session_state[LOG_KEY][-MAX_ENTRIES:]


def get_log_entries() -> List[dict]:
    init_log()
    return list(st.session_state[LOG_KEY])


def make_log_fn(source: str = "app") -> Callable[[str, str], None]:
    """Return a (message, level) callback for pipeline/backtest code."""

    def _log(message: str, level: str = "info") -> None:
        log_action(f"[{source}] {message}", level)

    return _log


def _format_entries(entries: List[dict]) -> str:
    if not entries:
        return "_No actions yet. Run **Predict** or **Backtest**._"
    lines = []
    for e in reversed(entries[-40:]):
        icon = LEVEL_ICONS.get(e["level"], "•")
        lines.append(f"`{e['ts']}` {icon} {e['message']}")
    return "\n\n".join(lines)


def render_action_log_sidebar(*, expanded: bool = True) -> None:
    """Draw the action log panel in the sidebar (call once per page)."""
    init_log()
    with st.sidebar:
        with st.expander("Action log", expanded=expanded):
            if st.button("Clear log", key="action_log_clear", use_container_width=True):
                clear_log()
                st.rerun()
            st.markdown(_format_entries(get_log_entries()))
