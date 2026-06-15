from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.time import utc_now
from app.models.source import Source
from app.models.source_adapter import SourceAdapter
from app.models.source_template import SourceTemplate
from app.schemas.source import (
    SourceAdapterRead,
    SourceCandidateValidateRequest,
    SourceCandidateValidateResponse,
    SourceCreate,
    SourceProbeRequest,
    SourceProbeResponse,
    SourceRead,
    SourceTemplateRead,
    SourceUpdate,
    SourceValidationReport,
)
from app.services.onboarding_service import SourceOnboardingService
from app.services.rule_validation_service import RuleValidationService
from app.services.llm.source_resolver import ResolvedSourceCandidate
from app.services.c9_scope import c9_scope_rejection_message, normalize_c9_university_name
from app.services.source_resolution import LLMResolvedSourceValidator, SourceResolutionService


router = APIRouter(prefix="/sources", tags=["sources"])
onboarding_service = SourceOnboardingService()
probe_service = SourceResolutionService()
validation_service = RuleValidationService()
llm_validation_service = LLMResolvedSourceValidator()


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, session: Session = Depends(get_db)) -> Source:
    existing = session.scalar(select(Source).where(Source.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Source name already exists.")

    try:
        normalized_payload = onboarding_service.normalize_source_dict(
            session,
            payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = Source(**normalized_payload)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


@router.get("/adapters", response_model=list[SourceAdapterRead])
def list_source_adapters(session: Session = Depends(get_db)) -> list[SourceAdapter]:
    return list(session.scalars(select(SourceAdapter).order_by(SourceAdapter.id.asc())))


@router.get("/templates", response_model=list[SourceTemplateRead])
def list_source_templates(
    adapter_id: int | None = None,
    session: Session = Depends(get_db),
) -> list[SourceTemplate]:
    stmt = select(SourceTemplate).order_by(SourceTemplate.id.asc())
    if adapter_id is not None:
        stmt = stmt.where(SourceTemplate.adapter_id == adapter_id)
    return list(session.scalars(stmt))


@router.post("/probe", response_model=SourceProbeResponse)
def probe_source(
    payload: SourceProbeRequest,
    session: Session = Depends(get_db),
) -> SourceProbeResponse:
    try:
        return probe_service.probe(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM 选源失败：{exc}",
        ) from exc


@router.post("/validate-candidate", response_model=SourceCandidateValidateResponse)
def validate_candidate_source(
    payload: SourceCandidateValidateRequest,
    session: Session = Depends(get_db),
) -> SourceCandidateValidateResponse:
    scope_message = c9_scope_rejection_message(
        university_name=payload.university_name,
        query=f"{payload.source_title} {payload.source_url}",
        collection_domain=payload.collection_domain,
        admissions_levels=payload.admissions_levels,
        admissions_tracks=payload.admissions_tracks,
        homepage_url=payload.homepage_url,
    )
    if scope_message:
        raise HTTPException(status_code=400, detail=scope_message)
    normalized_university = normalize_c9_university_name(payload.university_name) or payload.university_name
    if not normalized_university:
        raise HTTPException(status_code=400, detail="请提供高校名称。")
    candidate_payload = payload.model_dump()
    candidate_payload["university_name"] = normalized_university
    candidate_payload["admissions_levels"] = ["graduate"]
    candidate_payload["admissions_tracks"] = ["graduate"]
    candidate = probe_service.resolver.strict_candidate_from_payload(
        candidate_payload,
        default_university_name=normalized_university,
        default_collection_domain=payload.collection_domain,
        default_homepage_url=payload.homepage_url,
        default_admissions_levels=["graduate"],
        default_admissions_tracks=["graduate"],
    )
    validated = llm_validation_service.validate(candidate)
    source = None
    if validated.report.success_rate > 0 and validated.normalized_source:
        source = probe_service._upsert_source(session, validated)
    return SourceCandidateValidateResponse(
        report=validated.report,
        source_id=source.id if source else None,
        saved=bool(source),
        validation_status="valid" if validated.report.success_rate > 0 else "invalid",
        validation_message=validated.report.issues[0] if validated.report.issues else None,
        confidence_score=validated.report.success_rate,
    )


@router.get("", response_model=list[SourceRead])
def list_sources(session: Session = Depends(get_db)) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.created_at.desc())))


@router.get("/{source_id}", response_model=SourceRead)
def get_source(source_id: int, session: Session = Depends(get_db)) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source


@router.put("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    session: Session = Depends(get_db),
) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")

    merged_payload = {
        "name": source.name,
        "organization_name": source.organization_name,
        "source_type": source.source_type,
        "adapter_id": source.adapter_id,
        "template_id": source.template_id,
        "base_url": source.base_url,
        "start_urls_json": source.start_urls_json,
        "site_type": source.site_type,
        "crawl_mode": source.crawl_mode,
        "status": source.status,
        "onboarding_status": source.onboarding_status,
        "confidence_score": source.confidence_score,
        "entrypoint_url": source.entrypoint_url,
        "health_status": source.health_status,
        "last_discovered_at": source.last_discovered_at,
        "last_success_at": source.last_success_at,
        "last_failure_reason": source.last_failure_reason,
        "validation_evidence": source.validation_evidence,
        "config_json": source.config_json,
    }
    merged_payload.update(payload.model_dump(exclude_unset=True))
    try:
        normalized_payload = onboarding_service.normalize_source_dict(session, merged_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for key, value in normalized_payload.items():
        setattr(source, key, value)
    session.commit()
    session.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_source(
    source_id: int,
    session: Session = Depends(get_db),
) -> None:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    session.delete(source)
    session.commit()


@router.post("/{source_id}/validate", response_model=SourceValidationReport)
def validate_source(
    source_id: int,
    session: Session = Depends(get_db),
) -> SourceValidationReport:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if source.source_origin in {"discovered_by_agent", "source_knowledge", "llm_resolved"} or (source.resolver_meta_json or {}).get("candidate_type"):
        validated = llm_validation_service.validate(
            ResolvedSourceCandidate(
                university_name=source.organization_name or source.name,
                collection_domain=source.collection_domain,
                homepage_url=source.base_url,
                source_url=(source.start_urls_json or [source.base_url])[0],
                source_title=source.name,
                source_kind=(source.config_json or {}).get("source_kind") or "list_page",
                admissions_levels=(source.scope_json or {}).get("admissions_levels"),
            )
        )
        source.confidence_score = validated.report.success_rate
        source.last_validated_at = utc_now()
        source.onboarding_status = "validated" if validated.report.success_rate > 0 else "needs_review"
        source.health_status = "healthy" if validated.report.success_rate > 0 else "stale"
        source.last_failure_reason = None if validated.report.success_rate > 0 else (validated.report.issues[0] if validated.report.issues else "校验失败")
        source.validation_evidence = {
            "issues": list(validated.report.issues or []),
            "success_rate": validated.report.success_rate,
            "validated_source_url": (source.start_urls_json or [source.base_url])[0],
        }
        session.commit()
        return validated.report
    return validation_service.validate_and_record(session, source)
