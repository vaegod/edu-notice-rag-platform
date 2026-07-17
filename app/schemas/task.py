from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    task_type: str = "crawl_admissions_notice"
    source_id: int
    trigger_mode: str = "manual"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=5)
    task_payload: dict[str, Any] = Field(default_factory=dict)
    execute_immediately: bool = True


class TaskResult(BaseModel):
    engine: str | None = None
    runner_version: str | None = None
    discovered: int = 0
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: int = 0
    message: str | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    collection_domain: str | None = None
    source_id: int
    source_name: str | None = None
    trigger_mode: str
    trace_id: str | None = None
    idempotency_key: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    task_payload: dict[str, Any]
    status: str
    progress_stage: str | None = None
    progress_percent: int | None = None
    progress_message: str | None = None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    last_result: TaskResult | None = None


class TaskListResponse(BaseModel):
    items: list[TaskRead]
    total: int
    page: int
    page_size: int
