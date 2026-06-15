from __future__ import annotations

import threading

from app.core.database import get_session_factory
from app.services.task.crawl_task_executor import CrawlTaskExecutor


class BackgroundTaskRunner:
    def __init__(self) -> None:
        self.executor = CrawlTaskExecutor()

    def start(self, task_id: int) -> None:
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            daemon=True,
            name=f"crawl-task-{task_id}",
        )
        thread.start()

    def _run_task(self, task_id: int) -> None:
        session = get_session_factory()()
        try:
            task = session.get(__import__("app.models.task", fromlist=["CrawlTask"]).CrawlTask, task_id)
            if task is None:
                return
            self.executor.execute(session, task)
        except Exception:
            # executor already writes task failure state when possible
            pass
        finally:
            session.close()


background_task_runner = BackgroundTaskRunner()
