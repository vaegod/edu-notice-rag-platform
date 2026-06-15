from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_db
from app.models.attachment import Attachment
from app.models.document import Document
from app.models.llm_log import LLMLog
from app.models.raw_page import RawPage
from app.schemas.document import DocumentEvidenceRead, DocumentListResponse, DocumentRead, LLMLogRead
from app.schemas.document import (
    DocumentUniversityGroupRead,
    DocumentUniversityClassifyRequest,
    DocumentUniversityClassifyResponse,
)
from app.services.document_postprocess import DocumentUniversityPostprocessService
from app.services.normalize.date_parser import parse_date_string
from app.services.search.keyword_search import KeywordSearchService, serialize_document


router = APIRouter(prefix="/documents", tags=["documents"])
search_service = KeywordSearchService()
document_postprocess_service = DocumentUniversityPostprocessService()


def _document_to_schema(document: Document) -> DocumentRead:
    raw_page = document.raw_page
    source = raw_page.source if raw_page else None
    payload = {
        **serialize_document(document),
        "model_output": document.model_output,
        "model_name": document.model_name,
        "status": document.status,
        "created_at": document.created_at,
        "tags": document.tags,
        "attachments": document.attachments,
    }
    if raw_page and source:
        payload["source_id"] = raw_page.source_id
        payload["source_name"] = source.name
    return DocumentRead.model_validate(payload)


@router.get("", response_model=DocumentListResponse)
def list_documents(
    keyword: str | None = None,
    doc_type: str | None = None,
    collection_domain: str | None = None,
    institution_name: str | None = None,
    source_id: int | None = None,
    publish_date_start: str | None = None,
    publish_date_end: str | None = None,
    deadline_start: str | None = None,
    deadline_end: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_db),
) -> DocumentListResponse:
    items, total = search_service.search(
        session,
        keyword=keyword,
        doc_types=[doc_type] if doc_type else None,
        collection_domain=collection_domain,
        institution_name=institution_name,
        source_id=source_id,
        publish_date_start=parse_date_string(publish_date_start),
        publish_date_end=parse_date_string(publish_date_end),
        deadline_start=parse_date_string(deadline_start),
        deadline_end=parse_date_string(deadline_end),
        page=page,
        page_size=page_size,
    )
    return DocumentListResponse(
        items=[_document_to_schema(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/by-university", response_model=list[DocumentUniversityGroupRead])
def list_documents_grouped_by_university(
    collection_domain: str | None = None,
    session: Session = Depends(get_db),
) -> list[DocumentUniversityGroupRead]:
    stmt = (
        select(
            Document.institution_name,
            func.count(Document.id),
            func.max(Document.publish_date),
        )
        .where(Document.institution_name.is_not(None), Document.institution_name != "")
        .group_by(Document.institution_name)
        .order_by(func.count(Document.id).desc(), Document.institution_name.asc())
    )
    if collection_domain:
        stmt = stmt.where(Document.collection_domain == collection_domain)
    rows = session.execute(stmt).all()
    return [
        DocumentUniversityGroupRead(
            institution_name=institution_name,
            total=total,
            latest_publish_date=latest_publish_date,
        )
        for institution_name, total, latest_publish_date in rows
        if institution_name
    ]


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: int, session: Session = Depends(get_db)) -> Document:
    document = (
        session.query(Document)
        .options(
            joinedload(Document.tags),
            joinedload(Document.attachments),
            joinedload(Document.raw_page).joinedload(RawPage.source),
        )
        .filter(Document.id == document_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _document_to_schema(document)


@router.get("/{document_id}/evidence", response_model=DocumentEvidenceRead)
def get_document_evidence(document_id: int, session: Session = Depends(get_db)) -> DocumentEvidenceRead:
    document = (
        session.query(Document)
        .options(
            joinedload(Document.tags),
            joinedload(Document.attachments),
            joinedload(Document.raw_page).joinedload(RawPage.source),
        )
        .filter(Document.id == document_id)
        .first()
    )
    if document is None or document.raw_page is None:
        raise HTTPException(status_code=404, detail="Document evidence not found.")

    llm_logs = list(
        session.scalars(
            select(LLMLog)
            .where(LLMLog.biz_type == "extract", LLMLog.related_id == document.raw_page.id)
            .order_by(LLMLog.created_at.desc())
        )
    )

    return DocumentEvidenceRead(
        document=_document_to_schema(document),
        raw_page={
            "id": document.raw_page.id,
            "source_id": document.raw_page.source_id,
            "source_name": document.raw_page.source.name if document.raw_page.source else None,
            "url": document.raw_page.url,
            "title": document.raw_page.title,
            "raw_html": document.raw_page.raw_html,
            "raw_text": document.raw_page.raw_text,
            "content_hash": document.raw_page.content_hash,
            "http_status": document.raw_page.http_status,
            "crawled_at": document.raw_page.crawled_at,
            "created_at": document.raw_page.created_at,
        },
        llm_logs=[LLMLogRead.model_validate(item, from_attributes=True) for item in llm_logs],
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_document(document_id: int, session: Session = Depends(get_db)) -> None:
    document = (
        session.query(Document)
        .options(joinedload(Document.raw_page))
        .filter(Document.id == document_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    raw_page = document.raw_page
    session.query(Attachment).filter(Attachment.document_id == document.id).delete()
    session.delete(document)
    if raw_page is not None:
        refreshed = session.get(RawPage, raw_page.id)
        if refreshed is not None and refreshed.document is None:
            session.delete(refreshed)
    session.commit()


@router.post("/postprocess/university-classify", response_model=DocumentUniversityClassifyResponse)
def classify_document_universities(
    payload: DocumentUniversityClassifyRequest,
    session: Session = Depends(get_db),
) -> DocumentUniversityClassifyResponse:
    result = document_postprocess_service.classify_documents(
        session,
        document_ids=payload.document_ids or None,
        source_id=payload.source_id,
        collection_domain=payload.collection_domain,
        only_missing=payload.only_missing,
        limit=payload.limit,
        dry_run=payload.dry_run,
    )
    return DocumentUniversityClassifyResponse.model_validate(result)
