from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import BASE_DIR, get_settings
from app.core.time import utc_now
from app.models.document import Document
from app.models.llm_log import LLMLog
from app.models.nl_task import NLTask
from app.models.raw_page import RawPage
from app.models.source import Source
from app.models.task import CrawlTask
from app.schemas.dashboard import (
    DashboardCounts,
    DashboardOverviewRead,
    DashboardRecentDocument,
    DashboardRecentTask,
    DashboardRuntimeInfo,
)
from app.services.c9_source_knowledge import C9SourceKnowledgeService


PROCESS_STARTED_AT = utc_now()


class DashboardService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.knowledge_service = C9SourceKnowledgeService()

    def get_overview(self, session: Session) -> DashboardOverviewRead:
        runtime = self._runtime_info()
        counts = DashboardCounts(
            total_sources=session.scalar(select(func.count(Source.id))) or 0,
            active_sources=session.scalar(
                select(func.count(Source.id)).where(Source.status == "active")
            )
            or 0,
            total_tasks=session.scalar(select(func.count(CrawlTask.id))) or 0,
            running_tasks=session.scalar(
                select(func.count(CrawlTask.id)).where(CrawlTask.status == "running")
            )
            or 0,
            failed_tasks=session.scalar(
                select(func.count(CrawlTask.id)).where(CrawlTask.status == "failed")
            )
            or 0,
            total_documents=session.scalar(select(func.count(Document.id))) or 0,
            total_nl_tasks=session.scalar(select(func.count(NLTask.id))) or 0,
            total_llm_logs=session.scalar(select(func.count(LLMLog.id))) or 0,
        )

        recent_tasks = [
            DashboardRecentTask(
                id=item.id,
                source_id=item.source_id,
                source_name=item.source.name if item.source else None,
                task_type=item.task_type,
                trigger_mode=item.trigger_mode,
                status=item.status,
                created_at=item.created_at,
            )
            for item in session.scalars(
                select(CrawlTask)
                .options(joinedload(CrawlTask.source))
                .order_by(CrawlTask.created_at.desc())
                .limit(5)
            )
        ]

        recent_documents = [
            DashboardRecentDocument(
                id=item.id,
                source_id=item.raw_page.source_id if item.raw_page else None,
                source_name=item.raw_page.source.name if item.raw_page and item.raw_page.source else None,
                title=item.title,
                doc_type=item.doc_type,
                publish_date=item.publish_date,
                created_at=item.created_at,
            )
            for item in session.scalars(
                select(Document)
                .options(joinedload(Document.raw_page).joinedload(RawPage.source))
                .order_by(Document.created_at.desc())
                .limit(5)
            ).unique()
        ]

        return DashboardOverviewRead(
            counts=counts,
            runtime=runtime,
            recent_tasks=recent_tasks,
            recent_documents=recent_documents,
        )

    def _runtime_info(self) -> DashboardRuntimeInfo:
        knowledge_payload = self.knowledge_service.load_payload()
        healthy = 0
        stale = 0
        invalid = 0
        for item in knowledge_payload.get("universities") or []:
            status = item.get("health_status")
            if status == "healthy":
                healthy += 1
            elif status == "stale":
                stale += 1
            elif status == "invalid":
                invalid += 1
        return DashboardRuntimeInfo(
            app_version=self.settings.app_version,
            process_started_at=PROCESS_STARTED_AT,
            backend_build_at=self._backend_build_at(),
            knowledge_generated_at=self._parse_datetime(knowledge_payload.get("generated_at")),
            healthy_knowledge_sources=healthy,
            stale_knowledge_sources=stale,
            invalid_knowledge_sources=invalid,
        )

    def _backend_build_at(self) -> datetime | None:
        app_dir = BASE_DIR / "app"
        latest_mtime = 0.0
        for path in app_dir.rglob("*.py"):
            try:
                latest_mtime = max(latest_mtime, path.stat().st_mtime)
            except OSError:
                continue
        if latest_mtime <= 0:
            return None
        return datetime.fromtimestamp(latest_mtime)

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
