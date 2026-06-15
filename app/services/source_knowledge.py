from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source
from app.services.admissions_tracks import normalize_tracks, primary_track
from app.services.c9_scope import (
    C9_UNIVERSITIES,
    infer_c9_university_from_text,
    mentions_c9_group,
    normalize_c9_university_name,
)
from app.services.domains import COLLECTION_DOMAIN_ADMISSIONS_NOTICE, normalize_collection_domain
from app.services.source_state import source_can_be_reused


@dataclass(frozen=True)
class SourceKnowledgeHit:
    university_name: str
    collection_domain: str
    homepage_url: str
    source_url: str
    source_title: str
    source_kind: str
    admissions_levels: list[str]
    admissions_tracks: list[str]
    candidate_type: str
    confidence_score: float
    selection_score: float
    reason: str
    evidence_snippet: str
    source_origin: str
    source_id: int | None = None
    crawl_mode: str = "static"
    entrypoint_url: str | None = None
    health_status: str = "healthy"
    source_reuse_reason: str | None = None

    def to_debug_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "university_name": self.university_name,
            "collection_domain": self.collection_domain,
            "homepage_url": self.homepage_url,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "source_kind": self.source_kind,
            "candidate_type": self.candidate_type,
            "admissions_levels": self.admissions_levels,
            "admissions_tracks": self.admissions_tracks,
            "track": primary_track(self.admissions_tracks),
            "confidence_score": self.confidence_score,
            "selection_score": self.selection_score,
            "reason": self.reason,
            "evidence_snippet": self.evidence_snippet,
            "source_origin": self.source_origin,
            "entrypoint_url": self.entrypoint_url,
            "health_status": self.health_status,
            "source_reuse_reason": self.source_reuse_reason or self.reason,
        }


class SourceKnowledgeRetriever:
    """Lightweight retrieval over reusable graduate admissions source knowledge."""

    MIN_HIGH_CONFIDENCE_SCORE = 70.0

    def search(
        self,
        session: Session,
        *,
        query: str,
        collection_domain: str,
        university_name: str | None = None,
        desired_count: int = 5,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
    ) -> list[SourceKnowledgeHit]:
        collection_domain = normalize_collection_domain(collection_domain)
        if collection_domain != COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            return []

        admissions_tracks = normalize_tracks(admissions_tracks)
        admissions_levels = self._normalize_levels(admissions_levels, admissions_tracks=admissions_tracks)
        if admissions_levels and "graduate" not in admissions_levels:
            return []

        target_university = normalize_c9_university_name(university_name) or infer_c9_university_from_text(query)
        is_c9_batch = mentions_c9_group(query)
        if not target_university and not is_c9_batch:
            return []

        universities = [target_university] if target_university else C9_UNIVERSITIES
        universities = [item for item in universities if item]
        limit = max(1, min(desired_count, len(universities)))
        saved_sources = self._load_saved_sources(session)

        hits: list[SourceKnowledgeHit] = []
        for current_university in universities[:limit]:
            saved_hit = self._hit_from_saved_source(
                current_university,
                saved_sources=saved_sources,
                target_university=target_university,
                is_c9_batch=is_c9_batch,
            )
            if saved_hit is not None:
                hits.append(saved_hit)

        hits.sort(key=lambda item: (item.selection_score, item.source_id or 0), reverse=True)
        return hits[:limit]

    def best_hit(self, session: Session, **kwargs) -> SourceKnowledgeHit | None:
        hits = self.search(session, **kwargs)
        if not hits:
            return None
        hit = hits[0]
        if hit.selection_score < self.MIN_HIGH_CONFIDENCE_SCORE:
            return None
        return hit

    def _load_saved_sources(self, session: Session) -> list[Source]:
        return [
            source
            for source in session.scalars(
                select(Source).where(
                    Source.collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
                    Source.status == "active",
                )
            )
            if source_can_be_reused(source)
        ]

    def _hit_from_saved_source(
        self,
        university_name: str,
        *,
        saved_sources: list[Source],
        target_university: str | None,
        is_c9_batch: bool,
    ) -> SourceKnowledgeHit | None:
        candidates = [
            source
            for source in saved_sources
            if self._source_university(source) == university_name
            and self._source_supports_graduate(source)
        ]
        if not candidates:
            return None
        source = max(candidates, key=self._source_priority)
        source_url = ((source.start_urls_json or [source.base_url]) or [source.base_url])[0]
        confidence = self._normalize_confidence(source.confidence_score, fallback=0.93)
        selection_score = 94.0 if target_university == university_name else 82.0 if is_c9_batch else 0.0
        selection_score += min(confidence * 5, 5.0)
        reason = f"数据源知识库命中：复用已保存的{university_name}研究生招生数据源。"
        evidence = (
            f"高校={university_name}; 数据源={source.name}; URL={source_url}; "
            f"健康状态={source.health_status or 'healthy'}; 来源=已保存数据源"
        )
        return SourceKnowledgeHit(
            source_id=source.id,
            university_name=university_name,
            collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            homepage_url=source.base_url,
            source_url=source_url,
            source_title=source.name,
            source_kind=(source.config_json or {}).get("source_kind") or "list_page",
            admissions_levels=(source.scope_json or {}).get("admissions_levels") or ["graduate"],
            admissions_tracks=(source.scope_json or {}).get("admissions_tracks") or ["graduate"],
            candidate_type=(source.resolver_meta_json or {}).get("candidate_type") or (source.config_json or {}).get("candidate_type") or "list_page",
            confidence_score=confidence,
            selection_score=min(selection_score, 99.0),
            reason=reason,
            evidence_snippet=evidence,
            source_origin="saved_source",
            crawl_mode=source.crawl_mode,
            entrypoint_url=source.entrypoint_url or source.base_url,
            health_status=source.health_status or "healthy",
            source_reuse_reason=f"复用健康且已验证的数据源：{source.name}",
        )

    def _source_university(self, source: Source) -> str | None:
        scope = source.scope_json or {}
        config = source.config_json or {}
        return normalize_c9_university_name(
            source.organization_name
            or scope.get("university_name")
            or config.get("institution")
        )

    def _source_supports_graduate(self, source: Source) -> bool:
        scope = source.scope_json or {}
        config = source.config_json or {}
        tracks = normalize_tracks(scope.get("admissions_tracks") or config.get("admissions_tracks") or [])
        levels = self._normalize_levels(scope.get("admissions_levels") or config.get("admissions_levels") or [], admissions_tracks=tracks)
        if not tracks and not levels:
            return True
        return "graduate" in tracks or "graduate" in levels

    def _source_priority(self, source: Source) -> tuple[float, int]:
        confidence = self._normalize_confidence(source.confidence_score, fallback=0.8)
        source_url = ((source.start_urls_json or [source.base_url]) or [source.base_url])[0]
        precision = 0
        if re.search(r"notice|notices|list|tzgg|gg|zsxx|yjszs|yz", source_url, re.IGNORECASE):
            precision += 2
        candidate_type = (source.resolver_meta_json or {}).get("candidate_type")
        if candidate_type == "list_page":
            precision += 3
        return confidence + precision, source.id or 0

    def _normalize_confidence(self, value: float | None, *, fallback: float) -> float:
        if value is None:
            return fallback
        numeric = float(value)
        return numeric / 100 if numeric > 1 else numeric

    def _normalize_levels(self, admissions_levels: list[str] | None, *, admissions_tracks: list[str] | None = None) -> list[str]:
        levels = [item for item in (admissions_levels or []) if item in {"undergraduate", "graduate"}]
        tracks = normalize_tracks(admissions_tracks)
        if "graduate" in tracks and "graduate" not in levels:
            levels.append("graduate")
        return levels
