from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.source import Source
from app.models.task import CrawlTask
from app.schemas.task import TaskCreate
from app.services.task.crawl_task_executor import CrawlTaskExecutor
from app.services.task.orchestrator import TaskOrchestrator


def _source() -> Source:
    return Source(
        name="测试大学研究生招生数据源",
        organization_name="测试大学",
        base_url="https://example.edu.cn/graduate/notices.html",
        start_urls_json=["https://example.edu.cn/graduate/notices.html"],
        collection_domain="admissions_notice",
        status="active",
        onboarding_status="ready",
        health_status="healthy",
    )


def test_task_creation_is_idempotent_per_source_and_key() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    orchestrator = TaskOrchestrator()
    with Session(engine) as session:
        source = _source()
        session.add(source)
        session.commit()
        payload = TaskCreate(
            source_id=source.id,
            idempotency_key="manual-demo-001",
            execute_immediately=False,
        )

        first, _ = orchestrator.create_task(session, payload)
        second, _ = orchestrator.create_task(session, payload)

        assert first.id == second.id
        assert first.trace_id
        assert session.scalar(select(func.count(CrawlTask.id))) == 1
    engine.dispose()


def test_retry_rejects_invalid_state_and_attempt_exhaustion() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    orchestrator = TaskOrchestrator()
    with Session(engine) as session:
        source = _source()
        session.add(source)
        session.commit()
        task, _ = orchestrator.create_task(
            session,
            TaskCreate(source_id=source.id, execute_immediately=False, max_attempts=2),
        )

        try:
            orchestrator.retry_task(session, task.id)
        except ValueError as exc:
            assert "cannot be retried" in str(exc)
        else:
            raise AssertionError("pending task should not be retried")

        task.status = "failed"
        task.attempt_count = 2
        session.commit()
        try:
            orchestrator.retry_task(session, task.id)
        except ValueError as exc:
            assert "maximum" in str(exc)
        else:
            raise AssertionError("exhausted task should not be retried")
    engine.dispose()


def test_executor_does_not_claim_exhausted_pending_task() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = _source()
        session.add(source)
        session.flush()
        task = CrawlTask(
            source_id=source.id,
            status="pending",
            attempt_count=2,
            max_attempts=2,
        )
        session.add(task)
        session.commit()

        try:
            CrawlTaskExecutor().execute(session, task)
        except ValueError as exc:
            assert "cannot be claimed" in str(exc)
        else:
            raise AssertionError("exhausted task should not be claimed")

        session.refresh(task)
        assert task.status == "pending"
        assert task.attempt_count == 2
    engine.dispose()
