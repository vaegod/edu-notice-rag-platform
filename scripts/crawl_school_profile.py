from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.database import get_session_factory, init_db
from app.models.source import Source
from app.schemas.task import TaskCreate
from app.services.domains import COLLECTION_DOMAIN_SCHOOL_PROFILE
from app.services.task.orchestrator import TaskOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run school profile crawl tasks.")
    parser.add_argument("--source-id", type=int, default=None)
    args = parser.parse_args()

    init_db()
    session = get_session_factory()()
    orchestrator = TaskOrchestrator()
    try:
        stmt = select(Source).where(Source.collection_domain == COLLECTION_DOMAIN_SCHOOL_PROFILE, Source.status == "active")
        if args.source_id is not None:
            stmt = stmt.where(Source.id == args.source_id)
        sources = list(session.scalars(stmt))
        for source in sources:
            task, result = orchestrator.create_task(
                session,
                TaskCreate(
                    task_type="crawl_school_profile",
                    source_id=source.id,
                    trigger_mode="manual",
                    task_payload={},
                    execute_immediately=True,
                ),
            )
            print(f"[school_profile] source={source.name} task_id={task.id} result={result}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
