from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., max_length=255)
    organization_name: str | None = None
    source_type: str = "admissions"
    collection_domain: str = "admissions_notice"
    source_origin: str = "manual"
    adapter_id: int | None = None
    template_id: int | None = None
    base_url: str
    start_urls_json: list[str] = Field(default_factory=list)
    site_type: str = "school"
    crawl_mode: str = "static"
    status: str = "active"
    onboarding_status: str = "ready"
    confidence_score: float | None = None
    entrypoint_url: str | None = None
    health_status: str = "healthy"
    last_discovered_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_reason: str | None = None
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
    scope_json: dict[str, Any] = Field(default_factory=dict)
    resolver_meta_json: dict[str, Any] = Field(default_factory=dict)
    config_json: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    organization_name: str | None = None
    source_type: str | None = None
    collection_domain: str | None = None
    source_origin: str | None = None
    adapter_id: int | None = None
    template_id: int | None = None
    base_url: str | None = None
    start_urls_json: list[str] | None = None
    site_type: str | None = None
    crawl_mode: str | None = None
    status: str | None = None
    onboarding_status: str | None = None
    confidence_score: float | None = None
    entrypoint_url: str | None = None
    health_status: str | None = None
    last_discovered_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_reason: str | None = None
    validation_evidence: dict[str, Any] | None = None
    scope_json: dict[str, Any] | None = None
    resolver_meta_json: dict[str, Any] | None = None
    config_json: dict[str, Any] | None = None


class SourceRead(SourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SourceAdapterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None
    is_builtin: bool


class SourceTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    adapter_id: int
    code: str
    name: str
    description: str | None = None
    template_schema_json: dict[str, Any] = Field(default_factory=dict)
    default_config_json: dict[str, Any] = Field(default_factory=dict)


class SourceValidationPreviewItem(BaseModel):
    title: str
    detail_url: str
    publish_date: str | None = None
    detail_title: str | None = None
    content_length: int = 0
    attachment_count: int = 0


class SourceValidationReport(BaseModel):
    sample_count: int
    discovered_count: int
    success_count: int
    success_rate: float
    issues: list[str] = Field(default_factory=list)
    samples: list[SourceValidationPreviewItem] = Field(default_factory=list)


class SourceProbeRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    organization_name: str | None = None
    university_name: str | None = None
    department_name: str | None = None
    url: str | None = None
    collection_domain: str = "admissions_notice"
    admissions_levels: list[str] = Field(default_factory=list)
    admissions_tracks: list[str] = Field(default_factory=list)
    force_refresh_source: bool = False
    site_type: str = "college"
    crawl_mode: str = "dynamic"
    request_data: dict[str, Any] | None = None
    sample_count: int = Field(default=3, ge=1, le=5)


class SourceProbeResponse(BaseModel):
    normalized_source: SourceCreate
    report: SourceValidationReport
    recommended_adapter: str
    recommended_template: str
    probe_mode: str
    selected_url: str
    collection_domain: str = "admissions_notice"
    university_name: str | None = None
    resolved_homepage_url: str | None = None
    resolved_source_url: str | None = None
    used_existing_source: bool = False
    source_id: int | None = None
    selection_confidence: float
    notes: list[str] = Field(default_factory=list)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    candidate_rankings: list[dict[str, Any]] = Field(default_factory=list)
    failure_reason: str | None = None
    used_browser_explorer: bool = False
    bootstrap_strategy: str | None = None
    entrypoint_url: str | None = None
    source_reuse_reason: str | None = None
    health_status: str | None = None
    rediscovery_triggered: bool = False


class SourceCandidateValidateRequest(BaseModel):
    university_name: str
    collection_domain: str = "admissions_notice"
    homepage_url: str
    source_url: str
    source_title: str
    source_kind: str = "list_page"
    admissions_levels: list[str] = Field(default_factory=list)
    admissions_tracks: list[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    reason: str = ""
    request_method: str = "GET"
    list_page_mode: str | None = None
    pagination_hint: str | None = None
    detail_link_hint: str | None = None
    request_data: dict[str, Any] = Field(default_factory=dict)
    request_json: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict)
    crawl_mode: str = "dynamic"


class SourceCandidateValidateResponse(BaseModel):
    report: SourceValidationReport
    source_id: int | None = None
    saved: bool = False
    validation_status: str
    validation_message: str | None = None
    confidence_score: float = 0.0
