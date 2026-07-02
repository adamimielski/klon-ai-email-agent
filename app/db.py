"""SQLAlchemy engine + sesja + utworzenie tabel."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings, db_url


class Base(DeclarativeBase):
    pass


engine = create_engine(
    db_url(),
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _):
    """Włącza klucze obce w SQLite + WAL dla concurrency."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Tworzy tabele jeśli nie istnieją. Idempotentne."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    from . import models  # noqa: F401  rejestruje modele w Base.metadata
    Base.metadata.create_all(engine)


def get_session():
    """Generator sesji dla FastAPI Depends()."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
