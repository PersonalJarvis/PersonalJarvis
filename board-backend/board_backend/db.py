"""SQLAlchemy-Engine + Session-Factory.

Synchron, weil das Backend mit ein paar Hundert Pushes pro Tag laeuft —
async-SQLAlchemy waere Overkill, und die Hot-Path-Queries sind alle
indexed. WAL-Modus fuer concurrent Reader.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings


def make_engine(settings: Settings):
    """Baut die SQLAlchemy-Engine. Eigene Funktion, damit Tests sie ohne
    File-Side-Effect ueberschreiben koennen (in-memory ``sqlite:///:memory:``).
    """
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{settings.db_path}"
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()

    return engine


def make_session_factory(engine) -> sessionmaker[Session]:  # noqa: ANN001
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_dep(session_factory: sessionmaker[Session]):
    """FastAPI-Dependency-Factory. Liefert eine kurzlebige Session pro Request."""
    def _dep() -> Iterator[Session]:
        with session_factory() as session:
            yield session
    return _dep


def init_schema(engine) -> None:  # noqa: ANN001
    """Schema additiv aufbauen. Kein Alembic — additive ``create_all`` reicht."""
    from . import models  # noqa: F401  (registers tables on Base)
    models.Base.metadata.create_all(bind=engine)
