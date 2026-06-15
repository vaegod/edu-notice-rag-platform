from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_name: str
    file_url: str
    file_type: str | None


class DocumentTagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str


class DocumentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_page_id: int
    source_id: int | None = None
    source_name: str | None = None
    collection_domain: str = "admissions_notice"
    content_category: str | None = None
    institution_name: str | None = None
    doc_type: str | None
    title: str
    publish_date: date | None
    deadline: date | None
    department: str | None
    summary: str | None
    source_url: str
    status: str
    created_at: datetime


class DocumentRead(DocumentListItem):
    model_output: dict
    model_name: str | None
    tags: list[DocumentTagRead]
    attachments: list[AttachmentRead]


class RawPageEvidenceRead(BaseModel):
    id: int
    source_id: int
    source_name: str | None = None
    url: str
    title: str | None
    raw_html: str
    raw_text: str
    content_hash: str
    http_status: int
    crawled_at: datetime
    created_at: datetime


class LLMLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    biz_type: str
    related_id: int | None
    model_name: str
    prompt_text: str
    response_text: str | None
    parsed_json: dict | None
    success: bool
    error_message: str | None
    created_at: datetime


class DocumentEvidenceRead(BaseModel):
    document: DocumentRead
    raw_page: RawPageEvidenceRead
    llm_logs: list[LLMLogRead]


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int


class DocumentUniversityClassifyRequest(BaseModel):
    document_ids: list[int] = Field(default_factory=list)
    source_id: int | None = None
    collection_domain: str | None = None
    only_missing: bool = True
    limit: int = 100
    dry_run: bool = False


class DocumentUniversityClassifyItem(BaseModel):
    document_id: int
    title: str
    before_institution_name: str | None = None
    after_institution_name: str | None = None
    status: str
    reason: str | None = None
    confidence_score: float | None = None


class DocumentUniversityClassifyResponse(BaseModel):
    matched: int
    processed: int
    updated: int
    unchanged: int
    failed: int
    dry_run: bool
    items: list[DocumentUniversityClassifyItem]


class DocumentUniversityGroupRead(BaseModel):
    institution_name: str
    total: int
    latest_publish_date: date | None = None
