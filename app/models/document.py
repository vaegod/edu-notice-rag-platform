from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utc_now


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id"), nullable=False, unique=True, index=True
    )
    collection_domain: Mapped[str] = mapped_column(String(64), nullable=False, default="admissions_notice", index=True)
    content_category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    institution_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    event_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="structured")
    model_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    raw_page = relationship("RawPage", back_populates="document")
    tags = relationship("DocumentTag", back_populates="document", cascade="all, delete-orphan")
    attachments = relationship(
        "Attachment", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentTag(Base):
    __tablename__ = "document_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )
    tag: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    document = relationship("Document", back_populates="tags")
