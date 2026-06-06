"""
Commander card image + deck context for backtest / predict headers.
"""

from __future__ import annotations

import re

import streamlit as st
from sqlalchemy.orm import Session

from core.card_images import get_image_src, scryfall_page_url


def _commander_lookup_names(commander: str) -> list[str]:
    if not commander:
        return []
    parts = re.split(r"\s*/\s*|\s+and\s+", commander, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def resolve_commander_image(session: Session, commander_name: str) -> str:
    """First available image for a commander string (handles partners / DFC faces)."""
    for name in _commander_lookup_names(commander_name):
        src = get_image_src(session, name)
        if src:
            return src
    return get_image_src(session, commander_name)


def render_commander_card(session: Session, commander_name: str) -> None:
    """Commander art only — call inside the left column of a layout row."""
    if not commander_name:
        return

    img_src = resolve_commander_image(session, commander_name)
    if img_src:
        st.image(img_src, use_container_width=True)
    else:
        st.caption("No image cached — run `python ingest/card_images.py`")

    page = scryfall_page_url(commander_name.split(" // ")[0].strip())
    st.markdown(f"[View on Scryfall]({page})")


def render_commander_deck_meta(
    *,
    commander_name: str,
    deck_name: str = "",
    product: str = "",
    stage_label: str = "",
) -> None:
    """Deck / commander labels — call inside the right column beside the card."""
    if deck_name:
        st.markdown(f"### {deck_name}")
    if commander_name:
        st.markdown(f"**Commander:** {commander_name}")
    meta_bits = [b for b in (product, stage_label) if b]
    if meta_bits:
        st.caption(" · ".join(meta_bits))


def render_commander_banner(
    session: Session,
    *,
    commander_name: str,
    deck_name: str = "",
    product: str = "",
    stage_label: str = "",
) -> None:
    """Card + metadata row (Results / Predict pages)."""
    if not commander_name:
        return

    col_img, col_meta = st.columns([1, 2.2], gap="medium")
    with col_img:
        render_commander_card(session, commander_name)
    with col_meta:
        render_commander_deck_meta(
            commander_name=commander_name,
            deck_name=deck_name,
            product=product,
            stage_label=stage_label,
        )
