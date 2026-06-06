"""Shared Streamlit database session factory (WAL + lock timeout)."""

import streamlit as st

from db.engine import create_session_factory


@st.cache_resource
def get_session_factory():
    factory, _engine = create_session_factory()
    return factory
