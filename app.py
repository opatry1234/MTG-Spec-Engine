"""
MTG Commander Precon Spec Engine - Streamlit Entrypoint

Main application entry point for the Streamlit UI.
Run with: streamlit run app.py
"""

from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).parent


def home():
    st.title("MTG Commander Precon Spec Engine")
    st.write(
        "A local, data-driven system for predicting Commander precon-driven card spikes "
        "before decklists are revealed."
    )

    st.subheader("Get started")
    st.markdown(
        """
        Use the sidebar to navigate:

        - **Backtest** — run predictions against historical decks with hidden decklists
        - **Predict** — enter announcement info for a new product and rank omission targets
        - **Action log** (sidebar on Predict/Backtest) — step-by-step trace of each run
        - **Results** — browse saved backtest runs
        - **Settings** — train ML models (no terminal needed)
        """
    )

    st.subheader("Quick stats")
    st.caption(
        "Card hover previews use Scryfall CDN URLs. "
        "Run `python ingest/card_images.py` to cache images locally."
    )
    try:
        from sqlalchemy import create_engine, func
        from sqlalchemy.orm import sessionmaker

        from config import DATABASE_URL
        from db.schema import Card, CommanderDeck, StaplesExclusionList

        session = sessionmaker(bind=create_engine(DATABASE_URL))()
        decks = session.query(func.count(CommanderDeck.id)).scalar()
        cards = session.query(func.count(Card.id)).scalar()
        staples = session.query(func.count(StaplesExclusionList.card_name)).scalar()
        session.close()

        c1, c2, c3 = st.columns(3)
        c1.metric("Historical decks", decks or 0)
        c2.metric("Cards in DB", cards or 0)
        c3.metric("Staples excluded", staples or 0)
    except Exception as e:
        st.info("Database not loaded yet. Run the ingest pipeline first.")
        st.caption(str(e))


st.set_page_config(page_title="MTG Spec Engine", layout="wide", initial_sidebar_state="expanded")

pages = [
    st.Page(home, title="Home", icon="🏠", default=True),
    st.Page(str(BASE_DIR / "ui/pages/01_Backtest.py"), title="Backtest", icon="📊"),
    st.Page(str(BASE_DIR / "ui/pages/02_Predict.py"), title="Predict", icon="🔮"),
    st.Page(str(BASE_DIR / "ui/pages/03_Results.py"), title="Results", icon="📋"),
    st.Page(str(BASE_DIR / "ui/pages/04_Settings.py"), title="Settings", icon="⚙️"),
]

pg = st.navigation(pages)
pg.run()
