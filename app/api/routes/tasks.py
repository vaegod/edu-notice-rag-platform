from __future__ import annotations

from fastapi import Query
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_db
from app.models.task import CrawlTask
from app.schemas.task import TaskCreate, TaskListResponse, TaskRead, TaskResult
from app.services.task.orchestrator import TaskOrchestrator


router = APIRouter(prefix="/tasks", tags=["tasks"])
orchestrator = TaskOrchestrator()


def _task_to_schema(task: CrawlTask) -> TaskRead:
    last_result = task.task_payload.get("execution_result") if task.task_payload else None
    progress = (task.task_payload or {}).get("progress") or {}
    return TaskRead.model_validate(
        {
            **task.__dict__,
            "collection_domain": task.source.collection_domain if task.source else None,
            "source_name": task.source.name if task.source else None,
            "progress_stage": progress.get("stage"),
            "progress_percent": progress.get("percent"),
            "progress_message": progress.get("message"),
            "last_result": TaskResult.model_validate(last_result) if last_result else None,
        }
    )


@router.get("", response_model=TaskListResponse)
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    collection_domain: str | None = None,
    source_id: int | None = None,
    trigger_mode: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_db),
) -> TaskListResponse:
    stmt = select(CrawlTask).order_by(CrawlTask.created_at.desc())
    count_stmt = select(func.count(CrawlTask.id))

    if status_filter:
        stmt = stmt.where(CrawlTask.status == status_filter)
        count_stmt = count_stmt.where(CrawlTask.status == status_filter)
    if source_id is not None:
        stmt = stmt.where(CrawlTask.source_id == source_id)
        count_stmt = count_stmt.where(CrawlTask.source_id == source_id)
    if collection_domain:
        stmt = stmt.where(CrawlTask.source.has(collection_domain=collection_domain))
        count_stmt = count_stmt.where(CrawlTask.source.has(collection_domain=collection_domain))
    if trigger_mode:
        stmt = stmt.where(CrawlTask.trigger_mode == trigger_mode)
        count_stmt = count_stmt.where(CrawlTask.trigger_mode == trigger_mode)

    stmt = stmt.options(joinedload(CrawlTask.source)).offset((page - 1) * page_size).limit(page_size)
    tasks = list(session.scalars(stmt))
    total = session.scalar(count_stmt) or 0
    return TaskListResponse(
        items=[_task_to_schema(task) for task in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, session: Session = Depends(get_db)) -> TaskRead:
    try:
        task, _ = orchestrator.create_task(session, payload)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Source does not exist." else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return _task_to_schema(task)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, session: Session = Depends(get_db)) -> TaskRead:
    task = session.scalar(
        select(CrawlTask).options(joinedload(CrawlTask.source)).where(CrawlTask.id == task_id)
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return _task_to_schema(task)


@router.post("/{task_id}/retry", response_model=TaskRead)
def retry_task(task_id: int, session: Session = Depends(get_db)) -> TaskRead:
    try:
        orchestrator.retry_task(session, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    task = session.scalar(
        select(CrawlTask).options(joinedload(CrawlTask.source)).where(CrawlTask.id == task_id)
    )
    return _task_to_schema(task)
