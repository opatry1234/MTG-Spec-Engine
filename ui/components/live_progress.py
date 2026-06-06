"""
Live progress UI for long-running backtests.

Streamlit only redraws during a run when widgets inside the active script
are updated. This shows an on-page status box, progress bar, and log feed
while also writing to the sidebar action log (visible after the run ends).

Note: st.status.update() collapses the panel when called mid-run — only
update label/state on complete or error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional

import streamlit as st

from ui.components.action_log import LEVEL_ICONS, log_action


class LiveProgressLog:
    """On-page live feed + progress bar. Use as a context manager."""

    def __init__(
        self,
        source: str,
        *,
        total: Optional[int] = None,
        title: str = "Running…",
    ):
        self.source = source
        self.total = total
        self._title = title
        self._lines: List[str] = []
        self._status_ctx = None
        self._status = None
        self._progress = None

    def __enter__(self) -> "LiveProgressLog":
        self._status_ctx = st.status(self._title, expanded=True)
        self._status = self._status_ctx.__enter__()
        if self.total:
            self._progress = st.progress(0.0)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.fail(str(exc))
        if self._status_ctx is not None:
            self._status_ctx.__exit__(exc_type, exc, tb)

    def log(self, message: str, level: str = "info") -> None:
        icon = LEVEL_ICONS.get(level, "•")
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"{ts} {icon} [{self.source}] {message}"
        self._lines.append(line)
        log_action(f"[{self.source}] {message}", level)

        if self._status is not None:
            self._status.write(line)

    def step(self, index: int, message: str, level: str = "step") -> None:
        if self._progress is not None and self.total:
            self._progress.progress(min(max(index, 0) / self.total, 1.0))
        self.log(message, level)

    def complete(self, message: str) -> None:
        if self._progress is not None:
            self._progress.progress(1.0)
        self.log(message, "ok")
        if self._status is not None:
            self._status.update(label=message, state="complete", expanded=True)

    def fail(self, message: str) -> None:
        self.log(message, "error")
        if self._status is not None:
            self._status.update(label=message, state="error", expanded=True)


def make_live_log_fn(progress: LiveProgressLog) -> Callable[[str, str], None]:
    return progress.log
