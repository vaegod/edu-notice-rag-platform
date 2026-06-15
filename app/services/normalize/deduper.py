from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.raw_page import RawPage


def compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_duplicate_document(
    session: Session,
    *,
    url: str,
    title: str,
    publish_date,
    content_hash: str,
) -> Document | None:
    duplicate = session.scalar(select(Document).where(Document.source_url == url))
    if duplicate is not None:
        return duplicate

    if title and publish_date is not None:
        duplicate = session.scalar(
            select(Document).where(
                Document.title == title,
                Document.publish_date == publish_date,
            )
        )
        if duplicate is not None:
            return duplicate

    raw_page = session.scalar(select(RawPage).where(RawPage.content_hash == content_hash))
    if raw_page is not None and raw_page.document is not None:
        return raw_page.document

    return None
