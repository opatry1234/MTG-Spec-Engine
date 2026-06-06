"""Shared SQLAlchemy engine with SQLite WAL and lock timeout."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from config import DATABASE_CONNECT_ARGS, DATABASE_URL


def create_db_engine():
    engine = create_engine(DATABASE_URL, connect_args=DATABASE_CONNECT_ARGS)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def create_session_factory():
    engine = create_db_engine()
    return sessionmaker(bind=engine), engine
