from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TimeRangePayload(BaseModel):
    relative: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class NLParseRequest(BaseModel):
    query: str = Field(..., min_length=1)
    homepage_url: str | None = None


class NLParseResponse(BaseModel):
    intent: str
    collection_domain: str = "admissions_notice"
    university_name: str | None = None
    desired_source_count: int | None = None
    institution: str | None = None
    department: str | None = None
    topic: list[str] = Field(default_factory=list)
    notice_type: list[str] = Field(default_factory=list)
    admissions_levels: list[str] = Field(default_factory=list)
    admissions_tracks: list[str] = Field(default_factory=list)
    time_range: TimeRangePayload = Field(default_factory=TimeRangePayload)
    filters: dict[str, Any] = Field(default_factory=dict)
    output_mode: str = "default"
    result_limit: int = 10
    requires_source_discovery: bool = False
    resolved_homepage_url: str | None = None
    homepage_url: str | None = None
    validation_status: str | None = None
    validation_message: str | None = None
    matched_sources: list[str] = Field(default_factory=list)


class NLExecuteResponse(BaseModel):
    intent: str
    collection_domain: str = "admissions_notice"
    university_name: str | None = None
    desired_source_count: int | None = None
    homepage_url: str | None = None
    resolved_source_url: str | None = None
    used_existing_source: bool = False
    admissions_levels: list[str] = Field(default_factory=list)
    admissions_tracks: list[str] = Field(default_factory=list)
    validation_status: str
    validation_message: str | None = None
    task_ids: list[int] = Field(default_factory=list)
    matched_sources: list[str] = Field(default_factory=list)
    resolved_sources: list[dict[str, Any]] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    answer: str
    documents: list[dict[str, Any]] = Field(default_factory=list)
    workflow_steps: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_documents: list[dict[str, Any]] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    candidate_rankings: list[dict[str, Any]] = Field(default_factory=list)
    failure_reason: str | None = None
    used_browser_explorer: bool = False


class NLTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_input: str
    intent: str
    parsed_payload: dict[str, Any]
    validation_status: str
    validation_message: str | None
    created_task_id: int | None
    created_at: datetime
