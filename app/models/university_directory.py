from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utc_now


class UniversityDirectory(Base):
    __tablename__ = "university_directory"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    university_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    official_homepage_url: Mapped[str] = mapped_column(Text, nullable=False)
    admissions_entry_url: Mapped[str] = mapped_column(Text, nullable=False)
    entrypoint_candidates_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    selected_entrypoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="graduate_admissions")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)
