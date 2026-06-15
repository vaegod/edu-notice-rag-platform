from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.source import Source
from app.models.task import CrawlTask
from app.services.crawler.pipelines import CollectionPipelineRegistry


class CrawlTaskExecutor:
    def __init__(self) -> None:
        self.pipeline_registry = CollectionPipelineRegistry()

    def execute(self, session: Session, task: CrawlTask) -> dict:
        source = session.get(Source, task.source_id)
        pipeline = self.pipeline_registry.get_for_task(task, source)
        try:
            result = pipeline.run(session, task)
        except Exception as exc:
            if source is not None:
                source.health_status = "stale"
                source.last_failure_reason = str(exc)
                source.validation_evidence = {
                    **(source.validation_evidence or {}),
                    "last_task_id": task.id,
                    "last_task_status": "failed",
                }
                session.commit()
            raise
        if source is not None:
            source.last_success_at = utc_now()
            source.last_failure_reason = None
            if source.health_status != "invalid":
                source.health_status = "healthy"
            source.validation_evidence = {
                **(source.validation_evidence or {}),
                "last_task_id": task.id,
                "last_task_status": task.status,
            }
            session.commit()
        return result
