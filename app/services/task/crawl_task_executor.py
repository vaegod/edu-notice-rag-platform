from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.source import Source
from app.models.task import CrawlTask
from app.services.crawler.pipelines import CollectionPipelineRegistry


class CrawlTaskExecutor:
    def __init__(self) -> None:
        self.pipeline_registry = CollectionPipelineRegistry()

    def execute(self, session: Session, task: CrawlTask) -> dict:
        claimed = session.execute(
            update(CrawlTask)
            .where(
                CrawlTask.id == task.id,
                CrawlTask.status == "pending",
                CrawlTask.attempt_count < CrawlTask.max_attempts,
            )
            .values(
                status="running",
                started_at=utc_now(),
                attempt_count=CrawlTask.attempt_count + 1,
            )
        )
        if claimed.rowcount != 1:
            session.rollback()
            current = session.get(CrawlTask, task.id)
            current_status = current.status if current is not None else "missing"
            raise ValueError(
                f"Task {task.id} cannot be claimed from status '{current_status}'."
            )
        session.commit()
        session.refresh(task)
        logger.info(
            "crawl_task_started task_id=%s trace_id=%s attempt=%s/%s",
            task.id,
            task.trace_id,
            task.attempt_count,
            task.max_attempts,
        )
        source = None
        try:
            source = session.get(Source, task.source_id)
            pipeline = self.pipeline_registry.get_for_task(task, source)
            result = pipeline.run(session, task)
        except Exception as exc:
            session.rollback()
            session.refresh(task)
            if task.status != "failed":
                task.status = "failed"
                task.error_message = str(exc)
                task.finished_at = utc_now()
                session.commit()
            if source is not None:
                source.health_status = "stale"
                source.last_failure_reason = str(exc)
                source.validation_evidence = {
                    **(source.validation_evidence or {}),
                    "last_task_id": task.id,
                    "last_task_status": "failed",
                }
                session.commit()
            logger.exception(
                "crawl_task_failed task_id=%s trace_id=%s attempt=%s/%s",
                task.id,
                task.trace_id,
                task.attempt_count,
                task.max_attempts,
            )
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
        logger.info(
            "crawl_task_finished task_id=%s trace_id=%s status=%s",
            task.id,
            task.trace_id,
            task.status,
        )
        return result


logger = logging.getLogger(__name__)
