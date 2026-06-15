from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class DashboardCounts(BaseModel):
    total_sources: int
    active_sources: int
    total_tasks: int
    running_tasks: int
    failed_tasks: int
    total_documents: int
    total_nl_tasks: int
    total_llm_logs: int


class DashboardRecentTask(BaseModel):
    id: int
    source_id: int
    source_name: str | None = None
    task_type: str
    trigger_mode: str
    status: str
    created_at: datetime


class DashboardRecentDocument(BaseModel):
    id: int
    source_id: int | None = None
    source_name: str | None = None
    title: str
    doc_type: str | None = None
    publish_date: date | None = None
    created_at: datetime


class DashboardRuntimeInfo(BaseModel):
    app_version: str
    process_started_at: datetime
    backend_build_at: datetime | None = None
    knowledge_generated_at: datetime | None = None
    healthy_knowledge_sources: int = 0
    stale_knowledge_sources: int = 0
    invalid_knowledge_sources: int = 0


class DashboardOverviewRead(BaseModel):
    counts: DashboardCounts
    runtime: DashboardRuntimeInfo
    recent_tasks: list[DashboardRecentTask]
    recent_documents: list[DashboardRecentDocument]
