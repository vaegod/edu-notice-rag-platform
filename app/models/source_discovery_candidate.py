from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utc_now


class SourceDiscoveryCandidate(Base):
    __tablename__ = "source_discovery_candidates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("source_discovery_runs.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), nullable=False, default="invalid")
    render_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="static")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discovery_channel: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    run = relationship("SourceDiscoveryRun", back_populates="candidates")
    schema_candidates = relationship(
        "SourceSchemaCandidate",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
