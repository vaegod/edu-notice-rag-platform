from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.dashboard import DashboardOverviewRead
from app.services.dashboard import DashboardService


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
dashboard_service = DashboardService()


@router.get("/overview", response_model=DashboardOverviewRead)
def get_overview(session: Session = Depends(get_db)) -> DashboardOverviewRead:
    return dashboard_service.get_overview(session)
