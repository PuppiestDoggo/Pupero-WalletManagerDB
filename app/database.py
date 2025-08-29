from sqlmodel import create_engine, Session
from typing import Generator
import os

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("FALLBACK_SQLITE_URL", "sqlite:///./transactions.db")

# For sqlite we need special connect args; for MariaDB/Postgres/etc, leave empty
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# Create engine (schema creation is centralized in CreateDB)
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
