from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.nl_task import NLTask
from app.schemas.nl import NLExecuteResponse, NLParseRequest, NLParseResponse, NLTaskRead
from app.services.task.orchestrator import TaskOrchestrator


router = APIRouter(prefix="/nl", tags=["natural-language"])
orchestrator = TaskOrchestrator()


@router.post("/parse", response_model=NLParseResponse)
def parse_nl(request: NLParseRequest, session: Session = Depends(get_db)) -> NLParseResponse:
    parsed, validation = orchestrator.parse_nl(session, request.query, homepage_url=request.homepage_url)
    return NLParseResponse(
        **parsed,
        validation_status="valid" if validation.is_valid else "invalid",
        validation_message=validation.message,
        matched_sources=[source.name for source in validation.matched_sources],
    )


@router.post("/execute", response_model=NLExecuteResponse)
def execute_nl(request: NLParseRequest, session: Session = Depends(get_db)) -> NLExecuteResponse:
    return NLExecuteResponse(**orchestrator.execute_nl(session, request.query, homepage_url=request.homepage_url))


@router.get("/tasks", response_model=list[NLTaskRead])
def list_nl_tasks(session: Session = Depends(get_db)) -> list[NLTask]:
    return list(session.scalars(select(NLTask).order_by(NLTask.created_at.desc())))
