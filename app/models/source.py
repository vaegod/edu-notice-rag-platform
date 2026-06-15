from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utc_now


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    organization_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="admissions")
    collection_domain: Mapped[str] = mapped_column(String(64), nullable=False, default="admissions_notice", index=True)
    source_origin: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    adapter_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_adapters.id"),
        nullable=True,
        index=True,
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_templates.id"),
        nullable=True,
        index=True,
    )
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    start_urls_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    site_type: Mapped[str] = mapped_column(String(64), nullable=False, default="school")
    crawl_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="dynamic")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    onboarding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    entrypoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="healthy")
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_discovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resolver_meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    adapter = relationship("SourceAdapter", back_populates="sources")
    template = relationship("SourceTemplate", back_populates="sources")
    tasks = relationship("CrawlTask", back_populates="source", cascade="all, delete-orphan")
    raw_pages = relationship("RawPage", back_populates="source", cascade="all, delete-orphan")
    validation_runs = relationship(
        "SourceValidationRun",
        back_populates="source",
        cascade="all, delete-orphan",
    )
