from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.database import get_db_session


def get_db() -> Generator[Session, None, None]:
    yield from get_db_session()
