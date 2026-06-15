from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.database import get_session_factory, init_db
from app.models.source import Source
from app.schemas.task import TaskCreate
from app.services.domains import COLLECTION_DOMAIN_NEWS_CENTER
from app.services.task.orchestrator import TaskOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run news center crawl tasks.")
    parser.add_argument("--source-id", type=int, default=None)
    args = parser.parse_args()

    init_db()
    session = get_session_factory()()
    orchestrator = TaskOrchestrator()
    try:
        stmt = select(Source).where(Source.collection_domain == COLLECTION_DOMAIN_NEWS_CENTER, Source.status == "active")
        if args.source_id is not None:
            stmt = stmt.where(Source.id == args.source_id)
        sources = list(session.scalars(stmt))
        for source in sources:
            task, result = orchestrator.create_task(
                session,
                TaskCreate(
                    task_type="crawl_news_center",
                    source_id=source.id,
                    trigger_mode="manual",
                    task_payload={"page_limit": 1},
                    execute_immediately=True,
                ),
            )
            print(f"[news_center] source={source.name} task_id={task.id} result={result}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
