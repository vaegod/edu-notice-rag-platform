from __future__ import annotations

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover
    BackgroundScheduler = None
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models.source import Source
from app.schemas.task import TaskCreate
from app.services.task.orchestrator import TaskOrchestrator


class SchedulerService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.scheduler = (
            BackgroundScheduler(timezone=self.settings.scheduler_timezone)
            if BackgroundScheduler is not None
            else None
        )
        self._started = False

    def start(self) -> None:
        if self._started or self.scheduler is None:
            return
        self.scheduler.add_job(
            self._sync_source_jobs,
            "interval",
            minutes=5,
            id="sync-source-jobs",
            replace_existing=True,
        )
        self._sync_source_jobs()
        self.scheduler.start()
        self._started = True

    def shutdown(self) -> None:
        if self._started and self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
        self._started = False

    def _sync_source_jobs(self) -> None:
        if self.scheduler is None:
            return
        session = get_session_factory()()
        try:
            sources = list(session.scalars(select(Source).where(Source.status == "active")))
            active_job_ids = {f"source-{source.id}" for source in sources}
            for job in self.scheduler.get_jobs():
                if job.id.startswith("source-") and job.id not in active_job_ids:
                    self.scheduler.remove_job(job.id)
            for source in sources:
                hours = int(
                    source.config_json.get("schedule_hours")
                    or (
                        self.settings.hot_source_crawl_hours
                        if source.config_json.get("hot_source")
                        else self.settings.default_crawl_hours
                    )
                )
                self.scheduler.add_job(
                    self._run_scheduled_source,
                    "interval",
                    hours=hours,
                    id=f"source-{source.id}",
                    replace_existing=True,
                    args=[source.id],
                )
        finally:
            session.close()

    def _run_scheduled_source(self, source_id: int) -> None:
        session = get_session_factory()()
        orchestrator = TaskOrchestrator()
        try:
            orchestrator.create_task(
                session,
                TaskCreate(
                    task_type="crawl_notice",
                    source_id=source_id,
                    trigger_mode="schedule",
                    task_payload={},
                    execute_immediately=True,
                ),
            )
        finally:
            session.close()
