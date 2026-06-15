from __future__ import annotations

from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.document import Document
from app.models.raw_page import RawPage


def serialize_document(document: Document) -> dict:
    raw_page = document.raw_page
    source = raw_page.source if raw_page else None
    return {
        "id": document.id,
        "raw_page_id": document.raw_page_id,
        "source_id": raw_page.source_id if raw_page else None,
        "source_name": source.name if source else None,
        "collection_domain": document.collection_domain,
        "content_category": document.content_category,
        "institution_name": document.institution_name,
        "doc_type": document.doc_type,
        "title": document.title,
        "publish_date": document.publish_date.isoformat() if document.publish_date else None,
        "deadline": document.deadline.isoformat() if document.deadline else None,
        "department": document.department,
        "summary": document.summary,
        "source_url": document.source_url,
    }


class KeywordSearchService:
    def search(
        self,
        session: Session,
        *,
        keyword: str | None = None,
        topics: list[str] | None = None,
        doc_types: list[str] | None = None,
        collection_domain: str | None = None,
        institution_name: str | None = None,
        source_id: int | None = None,
        source_ids: list[int] | None = None,
        publish_date_start: date | None = None,
        publish_date_end: date | None = None,
        deadline_start: date | None = None,
        deadline_end: date | None = None,
        deadline_only: bool = False,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[Document], int]:
        page = max(page, 1)
        page_size = max(page_size, 1)

        stmt = (
            select(Document)
            .join(Document.raw_page)
            .options(
                joinedload(Document.tags),
                joinedload(Document.attachments),
                joinedload(Document.raw_page).joinedload(RawPage.source),
            )
        )
        count_stmt = select(func.count(Document.id)).join(Document.raw_page)

        conditions = []
        search_terms = []
        if keyword:
            search_terms.append(keyword)
        if topics:
            search_terms.extend(topics)
        if search_terms:
            term_conditions = []
            for term in search_terms:
                pattern = f"%{term}%"
                term_conditions.append(Document.title.ilike(pattern))
                term_conditions.append(Document.summary.ilike(pattern))
                term_conditions.append(RawPage.raw_text.ilike(pattern))
            conditions.append(or_(*term_conditions))

        if doc_types:
            conditions.append(Document.doc_type.in_(doc_types))
        if collection_domain:
            conditions.append(Document.collection_domain == collection_domain)
        if institution_name:
            conditions.append(Document.institution_name == institution_name)
        if source_id is not None:
            conditions.append(RawPage.source_id == source_id)
        if source_ids:
            conditions.append(RawPage.source_id.in_(source_ids))
        if publish_date_start is not None:
            conditions.append(Document.publish_date >= publish_date_start)
        if publish_date_end is not None:
            conditions.append(Document.publish_date <= publish_date_end)
        if deadline_only:
            conditions.append(Document.deadline.is_not(None))
        if deadline_start is not None:
            conditions.append(Document.deadline >= deadline_start)
        if deadline_end is not None:
            conditions.append(Document.deadline <= deadline_end)

        if conditions:
            stmt = stmt.where(*conditions)
            count_stmt = count_stmt.where(*conditions)

        stmt = stmt.order_by(Document.publish_date.desc(), Document.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        items = list(session.scalars(stmt).unique())
        total = session.scalar(count_stmt) or 0
        return items, total
