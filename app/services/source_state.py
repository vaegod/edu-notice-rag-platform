from __future__ import annotations

from datetime import timedelta

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.source import Source


RUNNABLE_ONBOARDING_STATUSES = {"validated", "ready"}
REUSABLE_HEALTH_STATUSES = {"healthy"}


def source_can_run(source: Source) -> bool:
    return (
        source.status == "active"
        and source.onboarding_status in RUNNABLE_ONBOARDING_STATUSES
        and (source.health_status or "healthy") != "invalid"
    )


def source_can_be_reused(source: Source) -> bool:
    if not source_can_run(source):
        return False
    if (source.health_status or "healthy") not in REUSABLE_HEALTH_STATUSES:
        return False
    settings = get_settings()
    freshness_anchor = source.last_validated_at or source.last_discovered_at or source.updated_at
    if freshness_anchor is None:
        return True
    freshness_deadline = freshness_anchor + timedelta(days=max(1, settings.source_knowledge_max_age_days))
    return freshness_deadline >= utc_now()


def source_readiness_message(source: Source) -> str:
    if source.status != "active":
        return "数据源当前未启用，请先启用后再执行任务。"
    if source.onboarding_status not in RUNNABLE_ONBOARDING_STATUSES:
        return "数据源尚未通过验证，请先在数据源管理中完成验证后再执行任务。"
    return "数据源可执行。"
