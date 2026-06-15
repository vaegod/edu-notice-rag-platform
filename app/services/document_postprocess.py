from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.document import Document
from app.models.raw_page import RawPage
from app.services.llm.siliconflow_client import SiliconFlowClient, load_prompt_template


class UniversityClassificationResult(BaseModel):
    institution_name: str | None = None
    reason: str | None = None
    confidence_score: float | None = None


class DocumentUniversityPostprocessService:
    def __init__(self, llm_client: SiliconFlowClient | None = None) -> None:
        self.settings = get_settings()
        self.llm_client = llm_client or SiliconFlowClient()
        self.prompt_template = load_prompt_template("university_classify.txt")

    def classify_documents(
        self,
        session: Session,
        *,
        document_ids: list[int] | None = None,
        source_id: int | None = None,
        collection_domain: str | None = None,
        only_missing: bool = True,
        limit: int = 100,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        stmt = (
            select(Document)
            .options(joinedload(Document.raw_page).joinedload(RawPage.source))
            .order_by(Document.id.asc())
        )
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        if source_id is not None:
            stmt = stmt.join(Document.raw_page).where(RawPage.source_id == source_id)
        if collection_domain:
            stmt = stmt.where(Document.collection_domain == collection_domain)
        if only_missing:
            stmt = stmt.where(or_(Document.institution_name.is_(None), Document.institution_name == ""))
        stmt = stmt.limit(max(1, min(limit, 500)))

        documents = list(session.scalars(stmt).unique())
        results: list[dict[str, Any]] = []
        stats = {
            "matched": len(documents),
            "processed": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": 0,
            "dry_run": dry_run,
        }

        for document in documents:
            stats["processed"] += 1
            try:
                item = self._classify_single_document(session, document=document, dry_run=dry_run)
            except Exception as exc:
                session.rollback()
                stats["failed"] += 1
                results.append(
                    {
                        "document_id": document.id,
                        "title": document.title,
                        "before_institution_name": document.institution_name,
                        "after_institution_name": None,
                        "status": "failed",
                        "reason": str(exc),
                        "confidence_score": None,
                    }
                )
                continue
            stats[item["status"]] = stats.get(item["status"], 0) + 1
            results.append(item)

        return {
            **stats,
            "items": results,
        }

    def _classify_single_document(
        self,
        session: Session,
        *,
        document: Document,
        dry_run: bool,
    ) -> dict[str, Any]:
        current_name = (document.institution_name or "").strip() or None
        source = document.raw_page.source if document.raw_page else None
        predicted = self._predict_institution_name(session, document=document)
        predicted_name = self._normalize_institution_name(predicted.institution_name)

        if not predicted_name:
            return {
                "document_id": document.id,
                "title": document.title,
                "before_institution_name": current_name,
                "after_institution_name": None,
                "status": "unchanged",
                "reason": predicted.reason or "未能识别高校名称。",
                "confidence_score": predicted.confidence_score,
            }

        status = "unchanged" if predicted_name == current_name else "updated"
        if status == "updated" and not dry_run:
            document.institution_name = predicted_name
            model_output = dict(document.model_output or {})
            model_output["university_classification"] = {
                "institution_name": predicted_name,
                "reason": predicted.reason,
                "confidence_score": predicted.confidence_score,
                "classified_at": utc_now().isoformat(),
                "source_name": source.name if source else None,
                "source_organization_name": source.organization_name if source else None,
            }
            document.model_output = model_output
            session.commit()

        return {
            "document_id": document.id,
            "title": document.title,
            "before_institution_name": current_name,
            "after_institution_name": predicted_name,
            "status": status,
            "reason": predicted.reason or "分类完成。",
            "confidence_score": predicted.confidence_score,
        }

    def _predict_institution_name(
        self,
        session: Session,
        *,
        document: Document,
    ) -> UniversityClassificationResult:
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._fallback_classification(document)

        raw_page = document.raw_page
        source = raw_page.source if raw_page else None
        user_prompt = (
            f"title: {document.title}\n"
            f"collection_domain: {document.collection_domain}\n"
            f"current_institution_name: {document.institution_name or ''}\n"
            f"source_name: {source.name if source else ''}\n"
            f"source_organization_name: {source.organization_name if source else ''}\n"
            f"url: {document.source_url}\n"
            f"text:\n{(raw_page.raw_text if raw_page else '')[:8000]}"
        )
        try:
            payload = self.llm_client.chat_json(
                system_prompt=self.prompt_template,
                user_prompt=user_prompt,
                biz_type="document_university_classify",
                session=session,
                related_id=document.id,
            )
            return UniversityClassificationResult.model_validate(payload)
        except Exception:
            return self._fallback_classification(document)

    def _fallback_classification(self, document: Document) -> UniversityClassificationResult:
        raw_page = document.raw_page
        source = raw_page.source if raw_page else None
        source_org = self._normalize_institution_name(source.organization_name if source else None)
        if source_org:
            return UniversityClassificationResult(
                institution_name=source_org,
                reason="回退使用数据源所属高校。",
                confidence_score=0.55,
            )

        text_candidates = [
            document.title,
            raw_page.title if raw_page else None,
            raw_page.raw_text if raw_page else None,
        ]
        for text in text_candidates:
            matched = self._extract_university_name(text)
            if matched:
                return UniversityClassificationResult(
                    institution_name=matched,
                    reason="回退从文档标题或正文中识别高校名称。",
                    confidence_score=0.4,
                )

        return UniversityClassificationResult(
            institution_name=self._normalize_institution_name(document.institution_name),
            reason="未识别到更稳定的高校名称。",
            confidence_score=0.2,
        )

    def _extract_university_name(self, text: str | None) -> str | None:
        if not text:
            return None
        match = re.search(r"([^\s，,。；：:（）()]{2,30}?大学)", text)
        if match:
            return self._normalize_institution_name(match.group(1))
        return None

    def _normalize_institution_name(self, value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized or normalized.lower() in {"null", "none", "未知"}:
            return None
        return normalized
