from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utc_now


class SourceDiscoveryRun(Base):
    __tablename__ = "source_discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False, default="homepage_url")
    input_value: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_homepage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    selected_candidate_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    draft_source = relationship("Source")
    candidates = relationship(
        "SourceDiscoveryCandidate",
        back_populates="run",
        cascade="all, delete-orphan",
    )
