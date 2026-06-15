from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utc_now


class SourceTemplate(Base):
    __tablename__ = "source_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    adapter_id: Mapped[int] = mapped_column(ForeignKey("source_adapters.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    default_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    adapter = relationship("SourceAdapter", back_populates="templates")
    sources = relationship("Source", back_populates="template")
