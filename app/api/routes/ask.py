from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ask import AskRequest, AskResponse
from app.services.task.orchestrator import TaskOrchestrator


router = APIRouter(prefix="/ask", tags=["ask"])
orchestrator = TaskOrchestrator()


@router.post("", response_model=AskResponse)
def ask(request: AskRequest, session: Session = Depends(get_db)) -> AskResponse:
    return AskResponse(**orchestrator.ask(session, request.query))
