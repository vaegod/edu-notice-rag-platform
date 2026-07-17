from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.core.database import get_session_factory, init_db
from app.models.document import Document
from app.models.raw_page import RawPage
from app.models.source import Source
from app.services.admissions_taxonomy import ADMISSIONS_DOC_TYPES, ADMISSIONS_KEYWORDS
from app.services.rule_validation_service import RuleValidationService


CASES_PATH = PROJECT_ROOT / "scripts" / "real_site_regression_cases.json"


def _backup_sqlite() -> str | None:
    settings = get_settings()
    if not settings.database_url.startswith("sqlite:///"):
        return None
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    if not db_path.exists():
        return None
    backup_path = db_path.with_suffix(db_path.suffix + ".bak")
    shutil.copy2(db_path, backup_path)
    return str(backup_path)


def _load_keep_urls() -> set[str]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return {case["url"] for case in cases}


def _is_admissions_document(document: Document, raw_page: RawPage | None) -> bool:
    if document.doc_type in ADMISSIONS_DOC_TYPES:
        return True
    combined = " ".join(
        [
            document.title or "",
            document.summary or "",
            raw_page.raw_text if raw_page else "",
        ]
    ).lower()
    return any(keyword.lower() in combined for keyword in ADMISSIONS_KEYWORDS)


def main() -> None:
    init_db()
    backup_path = _backup_sqlite()
    keep_urls = _load_keep_urls()
    session = get_session_factory()()
    summary = {
        "backup_path": backup_path,
        "deleted_sources": [],
        "deleted_documents": 0,
        "deleted_tasks": 0,
        "deleted_raw_pages": 0,
        "revalidated_sources": [],
    }

    try:
        sources = list(session.scalars(select(Source)))
        keep_source_ids: set[int] = set()
        for source in sources:
            source_urls = set(source.start_urls_json or [])
            if source_urls.intersection(keep_urls):
                keep_source_ids.add(source.id)
                continue
            summary["deleted_sources"].append({"id": source.id, "name": source.name})
            session.delete(source)
        session.commit()

        documents = list(
            session.scalars(
                select(Document).join(Document.raw_page).where(RawPage.source_id.in_(keep_source_ids))
            )
        )
        for document in documents:
            raw_page = document.raw_page
            if _is_admissions_document(document, raw_page):
                continue
            session.delete(document)
            summary["deleted_documents"] += 1
        session.commit()

        orphan_pages = list(
            session.scalars(
                select(RawPage)
                .outerjoin(Document, Document.raw_page_id == RawPage.id)
                .where(RawPage.source_id.in_(keep_source_ids), Document.id.is_(None))
            )
        )
        for raw_page in orphan_pages:
            session.delete(raw_page)
            summary["deleted_raw_pages"] += 1
        session.commit()

        remaining_sources = list(session.scalars(select(Source).where(Source.id.in_(keep_source_ids))))
        validator = RuleValidationService()
        for source in remaining_sources:
            report = validator.validate_and_record(session, source)
            summary["revalidated_sources"].append(
                {
                    "id": source.id,
                    "name": source.name,
                    "success_rate": report.success_rate,
                }
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
