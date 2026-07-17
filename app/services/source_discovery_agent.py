from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import TYPE_CHECKING, Any
from collections.abc import Callable
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.source_discovery_candidate import SourceDiscoveryCandidate
from app.models.source_discovery_run import SourceDiscoveryRun
from app.models.source_schema_candidate import SourceSchemaCandidate
from app.models.university_resolution_cache import UniversityResolutionCache
from app.schemas.source import SourceValidationReport
from app.services.admissions_tracks import (
    broad_levels_from_tracks,
    infer_tracks_from_text,
    normalize_tracks,
    primary_track,
)
from app.services.domains import COLLECTION_DOMAIN_ADMISSIONS_NOTICE, normalize_collection_domain
from app.services.llm.source_resolver import LLMSourceResolverService, ResolvedSourceCandidate
from app.services.onboarding.providers import Crawl4AIProvider

if TYPE_CHECKING:
    from app.services.source_resolution import ValidatedResolvedSource


@dataclass(slots=True)
class SourceDiscoveryAgentResult:
    success: bool
    resolved_homepage_url: str | None = None
    resolved_source_url: str | None = None
    report: SourceValidationReport | None = None
    source: Any = None
    used_existing_source: bool = False
    resolved_sources: list[dict[str, Any]] = field(default_factory=list)
    candidate_rankings: list[dict[str, Any]] = field(default_factory=list)
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None
    used_browser_explorer: bool = False
    debug: dict[str, Any] = field(default_factory=dict)


class SourceDiscoveryAgentService:
    AUTO_SAVE_THRESHOLD = 70.0
    DISPLAY_THRESHOLD = 40.0
    DEFAULT_TRACK_PRIORITY = {
        "graduate": 5,
    }

    def __init__(
        self,
        *,
        resolver: LLMSourceResolverService,
        validator: Any,
        provider: Crawl4AIProvider,
        find_existing_source: Callable[..., Any],
        upsert_source: Callable[..., Any],
    ) -> None:
        self.resolver = resolver
        self.validator = validator
        self.provider = provider
        self.find_existing_source = find_existing_source
        self.upsert_source = upsert_source

    def ensure_source(
        self,
        session: Session,
        *,
        university_name: str,
        collection_domain: str,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        homepage_url: str | None = None,
        homepage_source: str = "request_homepage",
        force_refresh_source: bool = False,
        rediscovery_triggered: bool = False,
    ) -> SourceDiscoveryAgentResult:
        collection_domain = normalize_collection_domain(collection_domain)
        admissions_tracks = normalize_tracks(admissions_tracks)
        admissions_levels = self._normalize_levels(admissions_levels, admissions_tracks=admissions_tracks)
        trace: list[dict[str, Any]] = []
        debug = {
            "candidate_provider": "agent",
            "raw_candidate_count": 0,
            "raw_candidates": [],
            "llm_selected_candidates": [],
            "llm_fallback_candidates": [],
            "cache_hit": False,
            "admissions_levels": admissions_levels,
            "admissions_tracks": admissions_tracks,
            "bootstrap_strategy": homepage_source,
            "entrypoint_url": homepage_url,
            "source_reuse_reason": None,
            "health_status": None,
            "rediscovery_triggered": bool(rediscovery_triggered),
        }
        run = self._create_run(
            session,
            input_type="homepage_url" if homepage_url else "university_name",
            input_value=homepage_url or university_name,
        )

        if not force_refresh_source:
            existing = self.find_existing_source(
                session,
                university_name=university_name,
                collection_domain=collection_domain,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                require_reusable=True,
            )
            if existing is not None:
                strict_candidate = ResolvedSourceCandidate(
                    university_name=university_name,
                    collection_domain=collection_domain,
                    homepage_url=existing.base_url,
                    source_url=(existing.start_urls_json or [existing.base_url])[0],
                    source_title=existing.name,
                    source_kind=(existing.config_json or {}).get("source_kind") or "list_page",
                    admissions_levels=(existing.scope_json or {}).get("admissions_levels") or admissions_levels,
                    admissions_tracks=(existing.scope_json or {}).get("admissions_tracks") or admissions_tracks,
                    candidate_track=(existing.scope_json or {}).get("selected_track"),
                    candidate_type=(existing.resolver_meta_json or {}).get("candidate_type"),
                    confidence_score=existing.confidence_score or 0.8,
                    source_suitability_score=(existing.resolver_meta_json or {}).get("source_suitability_score") or 80.0,
                    topic_relevance_score=(existing.resolver_meta_json or {}).get("topic_relevance_score") or 80.0,
                    reason=(existing.resolver_meta_json or {}).get("reason") or "复用已保存数据源",
                    crawl_mode=existing.crawl_mode,
                ).normalized()
                validated = self.validator.validate(strict_candidate)
                if validated.report.success_rate > 0:
                    debug["source_reuse_reason"] = f"复用健康且已验证的数据源：{existing.name}"
                    debug["health_status"] = existing.health_status or "healthy"
                    debug["entrypoint_url"] = existing.entrypoint_url or existing.base_url
                    trace.append(self._trace_step("reuse_existing_source", "success", existing.base_url))
                    self._finish_run(
                        session,
                        run,
                        status="completed",
                        selected_candidate_url=strict_candidate.source_url,
                        summary_json={"agent_trace": trace, "resolved_source_url": strict_candidate.source_url},
                    )
                    return SourceDiscoveryAgentResult(
                        success=True,
                        source=existing,
                        report=validated.report,
                        resolved_homepage_url=existing.base_url,
                        resolved_source_url=strict_candidate.source_url,
                        used_existing_source=True,
                        agent_trace=trace,
                        debug=debug,
                    )

        resolved_homepage = self._resolve_homepage(
            session,
            university_name=university_name,
            homepage_url=homepage_url,
            homepage_source=homepage_source,
            debug=debug,
            trace=trace,
        )
        if not resolved_homepage:
            failure_reason = "homepage_resolution_failed"
            self._finish_run(
                session,
                run,
                status="failed",
                selected_candidate_url=None,
                summary_json={"agent_trace": trace, "failure_reason": failure_reason},
            )
            return SourceDiscoveryAgentResult(
                success=False,
                failure_reason="未能解析高校官网首页。",
                agent_trace=trace,
                debug=debug,
            )

        candidates, used_browser_explorer = self._harvest_candidates(
            homepage_url=resolved_homepage,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            trace=trace,
        )
        debug["raw_candidate_count"] = len(candidates)
        debug["raw_candidates"] = candidates[:20]

        featured = [
            self._featurize_candidate(
                item,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )
            for item in candidates
        ]
        debug["llm_selected_candidates"] = featured[:10]
        if not featured:
            trace.append(self._trace_step("candidate_shortfall", "failed", "站内未发现可用候选，已禁用 LLM 直连补候选。"))
            failure_reason = "no_candidates"
            self._finish_run(
                session,
                run,
                status="failed",
                selected_candidate_url=None,
                summary_json={"agent_trace": trace, "failure_reason": failure_reason},
            )
            return SourceDiscoveryAgentResult(
                success=False,
                resolved_homepage_url=resolved_homepage,
                failure_reason="官网内未发现可信的招生候选页。",
                used_browser_explorer=used_browser_explorer,
                agent_trace=trace,
                debug=debug,
            )

        rankings_payload = self.resolver.rank_candidates(
            query=self._build_track_specific_query(university_name, admissions_tracks),
            collection_domain=collection_domain,
            homepage_url=resolved_homepage,
            candidates=featured,
            desired_count=5,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            session=session,
        )
        rankings = self._sort_rankings_for_request(
            featured,
            rankings_payload.get("rankings") or [],
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
        )
        failure_reason = rankings_payload.get("failure_reason")
        candidate_rows = self._persist_candidates(session, run.id, featured, rankings)

        attempts = 0
        candidate_map = {item["source_url"]: item for item in featured if item.get("source_url")}
        for ranking in rankings[:3]:
            source_url = ranking.get("source_url")
            candidate = candidate_map.get(source_url)
            if not candidate:
                continue
            validated = self._validate_candidate_payload(
                candidate,
                university_name=university_name,
                collection_domain=collection_domain,
                homepage_url=resolved_homepage,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                trace=trace,
            )
            attempts += 1
            if validated is not None and validated.report.success_rate > 0 and validated.normalized_source:
                source = self.upsert_source(session, validated)
                self._record_schema_candidate(session, candidate_rows.get(source_url), validated)
                self._update_homepage_cache(
                    session,
                    university_name=university_name,
                    resolved_url=resolved_homepage,
                    provider="agent",
                    confidence_score=float(candidate.get("heuristic_score") or 0.0),
                    raw_candidates=candidates[:20],
                )
                self._finish_run(
                    session,
                    run,
                    status="completed",
                    selected_candidate_url=validated.candidate.source_url,
                    summary_json={
                        "agent_trace": trace,
                        "candidate_rankings": rankings,
                        "resolved_source_url": validated.candidate.source_url,
                    },
                )
                return SourceDiscoveryAgentResult(
                    success=True,
                    source=source,
                    report=validated.report,
                    resolved_homepage_url=resolved_homepage,
                    resolved_source_url=validated.candidate.source_url,
                    candidate_rankings=rankings,
                    used_browser_explorer=used_browser_explorer,
                    agent_trace=trace,
                    debug=debug,
                )
            if attempts >= 3:
                break

            expanded = self._expand_candidate(
                candidate,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                trace=trace,
            )
            if expanded:
                expanded_featured = [
                    self._featurize_candidate(item, admissions_levels=admissions_levels, admissions_tracks=admissions_tracks)
                    for item in expanded
                ]
                expanded_rankings = self.resolver.rank_candidates(
                    query=self._build_track_specific_query(university_name, admissions_tracks),
                    collection_domain=collection_domain,
                    homepage_url=resolved_homepage,
                    candidates=expanded_featured,
                    desired_count=2,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                    session=session,
                ).get("rankings") or []
                for child_ranking in expanded_rankings[:1]:
                    child = next(
                        (item for item in expanded_featured if item.get("source_url") == child_ranking.get("source_url")),
                        None,
                    )
                    if child is None:
                        continue
                    validated_child = self._validate_candidate_payload(
                        child,
                        university_name=university_name,
                        collection_domain=collection_domain,
                        homepage_url=resolved_homepage,
                        admissions_levels=admissions_levels,
                        admissions_tracks=admissions_tracks,
                        trace=trace,
                    )
                    attempts += 1
                    if validated_child is not None and validated_child.report.success_rate > 0 and validated_child.normalized_source:
                        source = self.upsert_source(session, validated_child)
                        self._finish_run(
                            session,
                            run,
                            status="completed",
                            selected_candidate_url=validated_child.candidate.source_url,
                            summary_json={
                                "agent_trace": trace,
                                "candidate_rankings": rankings,
                                "resolved_source_url": validated_child.candidate.source_url,
                            },
                        )
                        return SourceDiscoveryAgentResult(
                            success=True,
                            source=source,
                            report=validated_child.report,
                            resolved_homepage_url=resolved_homepage,
                            resolved_source_url=validated_child.candidate.source_url,
                            candidate_rankings=rankings + expanded_rankings,
                            used_browser_explorer=used_browser_explorer,
                            agent_trace=trace,
                            debug=debug,
                        )
                    if attempts >= 3:
                        break
            if attempts >= 3:
                break

        trace.append(self._trace_step("direct_llm_fallback_disabled", "skipped", "仅允许官方入口目录 + 站内找源，未启用 LLM 直连兜底。"))

        self._finish_run(
            session,
            run,
            status="failed",
            selected_candidate_url=rankings[0]["source_url"] if rankings else None,
            summary_json={
                "agent_trace": trace,
                "candidate_rankings": rankings,
                "failure_reason": failure_reason or "validation_failed",
            },
        )
        return SourceDiscoveryAgentResult(
            success=False,
            resolved_homepage_url=resolved_homepage,
            candidate_rankings=rankings,
            used_browser_explorer=used_browser_explorer,
            failure_reason="候选数据源均未通过校验。",
            agent_trace=trace,
            debug=debug,
        )

    def discover_sources(
        self,
        session: Session,
        *,
        query: str,
        university_name: str,
        homepage_url: str | None = None,
        homepage_source: str = "request_homepage",
        desired_count: int = 10,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
    ) -> SourceDiscoveryAgentResult:
        admissions_tracks = normalize_tracks(admissions_tracks or infer_tracks_from_text(query))
        admissions_levels = self._normalize_levels(admissions_levels, admissions_tracks=admissions_tracks)
        trace: list[dict[str, Any]] = []
        debug = {
            "candidate_provider": "agent",
            "raw_candidate_count": 0,
            "raw_candidates": [],
            "llm_selected_candidates": [],
            "llm_fallback_candidates": [],
            "cache_hit": False,
            "admissions_levels": admissions_levels,
            "admissions_tracks": admissions_tracks,
            "bootstrap_strategy": homepage_source,
            "entrypoint_url": homepage_url,
            "source_reuse_reason": None,
            "health_status": None,
            "rediscovery_triggered": False,
        }
        run = self._create_run(
            session,
            input_type="homepage_url" if homepage_url else "university_name",
            input_value=homepage_url or university_name,
        )
        resolved_homepage = self._resolve_homepage(
            session,
            university_name=university_name,
            homepage_url=homepage_url,
            homepage_source=homepage_source,
            debug=debug,
            trace=trace,
        )
        if not resolved_homepage:
            self._finish_run(
                session,
                run,
                status="failed",
                selected_candidate_url=None,
                summary_json={"agent_trace": trace, "failure_reason": "homepage_resolution_failed"},
            )
            return SourceDiscoveryAgentResult(
                success=False,
                failure_reason="未能解析高校官网首页。",
                agent_trace=trace,
                debug=debug,
            )
        candidates, used_browser_explorer = self._harvest_candidates(
            homepage_url=resolved_homepage,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            trace=trace,
        )
        debug["raw_candidate_count"] = len(candidates)
        debug["raw_candidates"] = candidates[:20]
        featured = [
            self._featurize_candidate(
                item,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )
            for item in candidates
        ]
        debug["llm_selected_candidates"] = featured[:10]
        if not featured:
            trace.append(self._trace_step("candidate_shortfall", "failed", "站内未发现可用候选，已禁用 LLM 直连补候选。"))
            debug.update(self._build_debug_summary([], featured))
            self._finish_run(
                session,
                run,
                status="failed",
                selected_candidate_url=None,
                summary_json={
                    "agent_trace": trace,
                    "candidate_rankings": [],
                    "resolved_sources": [],
                    "failure_reason": "no_candidates",
                },
            )
            return SourceDiscoveryAgentResult(
                success=False,
                resolved_homepage_url=resolved_homepage,
                resolved_sources=[],
                candidate_rankings=[],
                used_browser_explorer=used_browser_explorer,
                agent_trace=trace,
                debug=debug,
                failure_reason="官网内未发现可用候选。",
            )
        rankings_payload = self.resolver.rank_candidates(
            query=query,
            collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            homepage_url=resolved_homepage,
            candidates=featured,
            desired_count=max(3, min(desired_count, 10)),
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            session=session,
        )
        rankings = self._sort_rankings_for_request(
            featured,
            rankings_payload.get("rankings") or [],
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
        )
        candidate_rows = self._persist_candidates(session, run.id, featured, rankings)
        results: list[dict[str, Any]] = []
        featured_map = {item.get("source_url"): item for item in featured if item.get("source_url")}
        for ranking in rankings[:desired_count]:
            source_url = ranking.get("source_url")
            candidate = featured_map.get(source_url)
            if candidate is None:
                continue
            validated = self._validate_candidate_payload(
                candidate,
                university_name=university_name,
                collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
                homepage_url=resolved_homepage,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                trace=trace,
            )
            if validated is None or validated.report.success_rate <= 0:
                expanded_results = self._validate_expanded_candidates(
                    university_name=university_name,
                    homepage_url=resolved_homepage,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                    candidate=candidate,
                    trace=trace,
                    session=session,
                )
                if expanded_results:
                    results.extend(expanded_results[:1])
                    continue
                results.append(self._candidate_result_payload(candidate, validated, None))
                continue
            should_auto_save = self._should_auto_save_candidate(candidate)
            source = self.upsert_source(session, validated) if should_auto_save else None
            if should_auto_save:
                self._record_schema_candidate(session, candidate_rows.get(source_url), validated)
            results.append(self._candidate_result_payload(candidate, validated, source if should_auto_save else None))
        if not results:
            trace.append(self._trace_step("direct_llm_fallback_disabled", "skipped", "仅允许官方入口目录 + 站内找源，未启用 LLM 直连兜底。"))
        results = self._sort_result_payloads(
            self._dedupe_result_payloads(results),
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
        )
        debug.update(self._build_debug_summary(results, featured))
        self._finish_run(
            session,
            run,
            status="completed" if results else "failed",
            selected_candidate_url=results[0]["source_url"] if results else None,
            summary_json={
                "agent_trace": trace,
                "candidate_rankings": rankings,
                "resolved_sources": results,
            },
        )
        return SourceDiscoveryAgentResult(
            success=bool(results),
            resolved_homepage_url=resolved_homepage,
            resolved_sources=results,
            candidate_rankings=rankings,
            used_browser_explorer=used_browser_explorer,
            agent_trace=trace,
            debug=debug,
            failure_reason=None if results else "未发现可用候选。",
        )

    def _validate_expanded_candidates(
        self,
        *,
        university_name: str,
        homepage_url: str,
        admissions_levels: list[str],
        admissions_tracks: list[str],
        candidate: dict[str, Any],
        trace: list[dict[str, Any]],
        session: Session,
    ) -> list[dict[str, Any]]:
        expanded = self._expand_candidate(
            candidate,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            trace=trace,
        )
        if not expanded:
            return []
        expanded_featured = [
            self._featurize_candidate(item, admissions_levels=admissions_levels, admissions_tracks=admissions_tracks)
            for item in expanded
        ]
        results: list[dict[str, Any]] = []
        for item in sorted(expanded_featured, key=lambda value: float(value.get("heuristic_score") or 0.0), reverse=True):
            validated = self._validate_candidate_payload(
                item,
                university_name=university_name,
                collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
                homepage_url=homepage_url,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                trace=trace,
            )
            if validated is None or validated.report.success_rate <= 0:
                continue
            should_auto_save = self._should_auto_save_candidate(item)
            source = self.upsert_source(session, validated) if should_auto_save else None
            results.append(self._candidate_result_payload(item, validated, source if should_auto_save else None))
        return results

    def _fallback_to_direct_candidates(
        self,
        session: Session,
        *,
        query: str,
        homepage_url: str,
        admissions_levels: list[str],
        admissions_tracks: list[str],
        debug: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        debug["llm_fallback_candidates"] = []
        trace.append(self._trace_step("llm_fallback_candidates", "skipped", "已禁用 LLM 直连补候选。"))
        return []

    def _resolve_homepage(
        self,
        session: Session,
        *,
        university_name: str,
        homepage_url: str | None,
        homepage_source: str,
        debug: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> str | None:
        if homepage_url:
            debug["entrypoint_url"] = homepage_url
            if homepage_source == "official_directory":
                trace.append(self._trace_step("resolve_homepage", "success", "使用官方入口目录中的站内入口。"))
            else:
                trace.append(self._trace_step("resolve_homepage", "success", "使用请求提供的官网首页。"))
            return homepage_url

        cache_key = self._normalize_text(university_name)
        cached = session.scalar(
            select(UniversityResolutionCache).where(UniversityResolutionCache.normalized_name == cache_key)
        )
        if cached is not None and cached.resolved_url:
            debug["cache_hit"] = True
            trace.append(self._trace_step("resolve_homepage", "success", "命中高校首页缓存。"))
            return cached.resolved_url

        homepage_result = self.resolver.resolve_homepage(university_name=university_name, session=session)
        resolved_homepage = homepage_result.get("homepage_url")
        if resolved_homepage:
            self._update_homepage_cache(
                session,
                university_name=university_name,
                resolved_url=resolved_homepage,
                provider="llm_homepage",
                confidence_score=float(homepage_result.get("confidence_score") or 0.0),
                raw_candidates=homepage_result.get("candidates") or [],
            )
            trace.append(self._trace_step("resolve_homepage", "success", homepage_result.get("reason") or "LLM 返回官网首页。"))
        else:
            trace.append(self._trace_step("resolve_homepage", "failed", homepage_result.get("reason") or "未解析到官网首页。"))
        return resolved_homepage

    def _harvest_candidates(
        self,
        *,
        homepage_url: str,
        admissions_levels: list[str],
        admissions_tracks: list[str],
        trace: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        candidates: list[dict[str, Any]] = []
        discovery_payload = self.provider.discover_candidate_links(homepage_url=homepage_url, max_links=40)
        links = discovery_payload.get("links") or []
        for item in links:
            built = self._candidate_from_link(
                homepage_url=homepage_url,
                item=item,
                discovery_channel="homepage_nav",
            )
            if built is not None:
                candidates.append(built)

        root_candidates = [item for item in candidates if item.get("source_kind") == "channel_page"][:4]
        for root_candidate in root_candidates:
            candidates.extend(
                self._expand_candidate(
                    root_candidate,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                    trace=trace,
                )
            )

        candidates.extend(self._harvest_sitemap_candidates(homepage_url=homepage_url))
        candidates = self._dedupe_candidates(candidates)
        used_browser_explorer = False
        if len(candidates) < 2:
            browser_candidates = self._browser_explore(homepage_url=homepage_url)
            if browser_candidates:
                used_browser_explorer = True
                trace.append(self._trace_step("optional_browser_explore", "success", f"浏览器兜底补充 {len(browser_candidates)} 个候选。"))
                candidates = self._dedupe_candidates(candidates + browser_candidates)
        trace.append(self._trace_step("harvest_candidates", "success", f"共收集 {len(candidates)} 个候选。"))
        return candidates, used_browser_explorer

    def _harvest_sitemap_candidates(self, *, homepage_url: str) -> list[dict[str, Any]]:
        sitemap_url = urljoin(homepage_url, "/sitemap.xml")
        try:
            payload = self.provider.fetch_page_payload(url=sitemap_url, crawl_mode="static")
        except Exception:
            payload = None
        if not payload:
            return []
        xml_text = payload.get("raw_text") or payload.get("raw_html") or ""
        urls = re.findall(r"https?://[^\s<>\"]+", xml_text)
        candidates = []
        for url in urls[:30]:
            built = self._candidate_from_link(
                homepage_url=homepage_url,
                item={"title": url, "url": url, "snippet": url},
                discovery_channel="sitemap",
            )
            if built is not None:
                candidates.append(built)
        return candidates

    def _browser_explore(self, *, homepage_url: str) -> list[dict[str, Any]]:
        try:
            payload = self.provider.fetch_page_payload(url=homepage_url, crawl_mode="dynamic")
        except Exception:
            payload = None
        if not payload:
            return []
        links = self._extract_links_from_payload(homepage_url=homepage_url, payload=payload)
        return self._dedupe_candidates(
            [
                candidate
                for item in links
                for candidate in [self._candidate_from_link(homepage_url=homepage_url, item=item, discovery_channel="browser_explorer")]
                if candidate is not None
            ]
        )

    def _expand_candidate(
        self,
        candidate: dict[str, Any],
        *,
        admissions_levels: list[str],
        admissions_tracks: list[str],
        trace: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        expanded_links: list[dict[str, Any]] = []
        if candidate.get("candidate_type") in {"site_home", "channel_page"}:
            try:
                discovery_payload = self.provider.discover_candidate_links(
                    homepage_url=candidate["source_url"],
                    max_links=30,
                )
            except Exception:
                discovery_payload = {}
            expanded_links.extend(discovery_payload.get("links") or [])
        try:
            payload = self.provider.fetch_page_payload(
                url=candidate["source_url"],
                crawl_mode=candidate.get("crawl_mode") or "static",
            )
        except Exception:
            payload = None
        if not payload:
            return []
        links = expanded_links + self._extract_links_from_payload(homepage_url=candidate["source_url"], payload=payload)
        expanded = []
        for item in links:
            built = self._candidate_from_link(
                homepage_url=candidate["source_url"],
                item=item,
                discovery_channel="section_nav",
            )
            if built is None:
                continue
            if admissions_tracks and built.get("admissions_tracks"):
                if not set(admissions_tracks).intersection(set(built.get("admissions_tracks") or [])):
                    continue
            elif admissions_levels and built.get("admissions_levels"):
                if not set(admissions_levels).intersection(set(built.get("admissions_levels") or [])):
                    continue
            expanded.append(built)
        if expanded:
            trace.append(self._trace_step("expand_candidate", "success", f"{candidate['source_url']} 下钻得到 {len(expanded)} 个候选。"))
        return self._dedupe_candidates(expanded)

    def _featurize_candidate(
        self,
        candidate: dict[str, Any],
        *,
        admissions_levels: list[str],
        admissions_tracks: list[str],
    ) -> dict[str, Any]:
        source_url = candidate.get("source_url") or ""
        title = candidate.get("source_title") or ""
        reason = candidate.get("reason") or ""
        combined = f"{title} {source_url} {reason}"
        keyword_hits = sum(1 for token in ("招生", "通知", "公告", "动态", "简章", "复试", "夏令营", "推免", "拟录取", "录取") if token in combined)
        inferred_tracks = normalize_tracks(candidate.get("admissions_tracks") or infer_tracks_from_text(combined))
        selected_track = candidate.get("candidate_track") or self._select_track_for_candidate(inferred_tracks, admissions_tracks)
        broad_track_levels = broad_levels_from_tracks(inferred_tracks)
        track_match_score = self._track_match_score(
            requested_tracks=admissions_tracks,
            candidate_tracks=inferred_tracks,
            requested_levels=admissions_levels,
            candidate_levels=broad_track_levels,
        )
        candidate_type = candidate.get("candidate_type") or self._guess_candidate_type(title=title, url=source_url, snippet=reason)
        page_type_guess = candidate_type
        date_hits = len(re.findall(r"20\d{2}[-/.年]\d{1,2}", combined))
        official_domain_match = self._registered_domain(candidate.get("homepage_url")) == self._registered_domain(source_url)
        topic_relevance_score = min(100.0, keyword_hits * 12 + max(track_match_score, 0) * 8 + min(date_hits, 3) * 4)
        source_suitability_score = self._source_suitability_score(
            candidate_type=candidate_type,
            source_kind=candidate.get("source_kind"),
            official_domain_match=official_domain_match,
            date_hits=date_hits,
            track_match_score=track_match_score,
            source_url=source_url,
        )
        return {
            **candidate,
            "candidate_type": candidate_type,
            "candidate_track": selected_track,
            "admissions_tracks": inferred_tracks,
            "source_suitability_score": source_suitability_score,
            "topic_relevance_score": topic_relevance_score,
            "features": {
                "official_domain_match": official_domain_match,
                "keyword_hits": keyword_hits,
                "track_match_score": track_match_score,
                "date_hits": date_hits,
                "page_type_guess": page_type_guess,
                "discovery_channel": candidate.get("discovery_channel"),
            },
            "heuristic_score": source_suitability_score,
        }

    def _validate_candidate_payload(
        self,
        candidate: dict[str, Any],
        *,
        university_name: str,
        collection_domain: str,
        homepage_url: str,
        admissions_levels: list[str],
        admissions_tracks: list[str],
        trace: list[dict[str, Any]],
    ) -> Any:
        try:
            strict_candidate = self.resolver.strict_candidate_from_payload(
                candidate,
                default_university_name=university_name,
                default_collection_domain=collection_domain,
                default_homepage_url=homepage_url,
                default_admissions_levels=admissions_levels,
                default_admissions_tracks=admissions_tracks,
            )
        except Exception as exc:
            trace.append(self._trace_step("validate_candidate", "failed", f"候选结构化失败：{exc}"))
            return None
        validated = self.validator.validate(strict_candidate)
        trace.append(
            self._trace_step(
                "validate_candidate",
                "success" if validated.report.success_rate > 0 else "failed",
                validated.report.issues[0] if validated.report.issues else strict_candidate.source_url,
            )
        )
        return validated

    def _candidate_result_payload(
        self,
        candidate: dict[str, Any],
        validated: ValidatedResolvedSource | None,
        source,
    ) -> dict[str, Any]:
        if validated is None:
            return {
                "source_id": None,
                "saved": False,
                "validation_status": "unchecked",
                "validation_message": "候选结构化失败。",
                "university_name": candidate.get("university_name"),
                "collection_domain": candidate.get("collection_domain"),
                "homepage_url": candidate.get("homepage_url"),
                "source_url": candidate.get("source_url"),
                "source_title": candidate.get("source_title"),
                "source_kind": candidate.get("source_kind"),
                "admissions_levels": candidate.get("admissions_levels") or [],
                "admissions_tracks": candidate.get("admissions_tracks") or [],
                "track": candidate.get("candidate_track") or primary_track(candidate.get("admissions_tracks") or []),
                "candidate_type": candidate.get("candidate_type"),
                "source_suitability_score": candidate.get("source_suitability_score") or 0.0,
                "topic_relevance_score": candidate.get("topic_relevance_score") or 0.0,
                "reject_reason_code": candidate.get("reject_reason_code") or "candidate_parse_failed",
                "confidence_score": candidate.get("heuristic_score") or candidate.get("confidence_score") or 0.0,
                "reason": candidate.get("reason"),
            }
        if validated.report.success_rate <= 0:
            issue = validated.report.issues[0] if validated.report.issues else "候选校验未通过。"
            return {
                "source_id": None,
                "saved": False,
                "validation_status": "invalid",
                "validation_message": issue,
                "university_name": validated.candidate.university_name,
                "collection_domain": validated.candidate.collection_domain,
                "homepage_url": validated.candidate.homepage_url,
                "source_url": validated.candidate.source_url,
                "source_title": validated.candidate.source_title,
                "source_kind": validated.candidate.source_kind,
                "admissions_levels": validated.candidate.admissions_levels or [],
                "admissions_tracks": validated.candidate.admissions_tracks or [],
                "track": validated.candidate.candidate_track,
                "candidate_type": validated.candidate.candidate_type,
                "source_suitability_score": validated.candidate.source_suitability_score,
                "topic_relevance_score": validated.candidate.topic_relevance_score,
                "reject_reason_code": self._reject_reason_code(issue, validated.candidate.candidate_type),
                "confidence_score": validated.candidate.source_suitability_score,
                "reason": validated.candidate.reason or candidate.get("reason"),
            }
        return {
            "source_id": source.id if source else None,
            "saved": bool(source),
            "validation_status": "valid" if validated.report.success_rate > 0 else "invalid",
            "validation_message": validated.report.issues[0] if validated.report.issues else None,
            "university_name": validated.candidate.university_name,
            "collection_domain": validated.candidate.collection_domain,
            "homepage_url": validated.candidate.homepage_url,
            "source_url": validated.candidate.source_url,
            "source_title": validated.candidate.source_title,
            "source_kind": validated.candidate.source_kind,
            "admissions_levels": validated.candidate.admissions_levels or [],
            "admissions_tracks": validated.candidate.admissions_tracks or [],
            "track": validated.candidate.candidate_track,
            "candidate_type": validated.candidate.candidate_type,
            "source_suitability_score": validated.candidate.source_suitability_score,
            "topic_relevance_score": validated.candidate.topic_relevance_score,
            "reject_reason_code": None if source else "manual_confirmation_required",
            "confidence_score": validated.report.success_rate if source else candidate.get("heuristic_score") or 0.0,
            "reason": validated.candidate.reason or candidate.get("reason"),
            "source_origin": source.source_origin if source else None,
            "health_status": source.health_status if source else None,
        }

    def _persist_candidates(
        self,
        session: Session,
        run_id: int,
        candidates: list[dict[str, Any]],
        rankings: list[dict[str, Any]],
    ) -> dict[str, SourceDiscoveryCandidate]:
        ranking_urls = {item.get("source_url") for item in rankings if item.get("source_url")}
        stored: dict[str, SourceDiscoveryCandidate] = {}
        for candidate in candidates:
            source_url = candidate.get("source_url")
            if not source_url:
                continue
            row = SourceDiscoveryCandidate(
                run_id=run_id,
                url=source_url,
                normalized_url=self._normalize_url(source_url),
                page_type=(candidate.get("features") or {}).get("page_type_guess", "invalid"),
                render_mode="dynamic" if candidate.get("crawl_mode") == "dynamic" else "static",
                confidence_score=float(candidate.get("heuristic_score") or 0.0),
                discovery_channel=candidate.get("discovery_channel") or "manual",
                features_json={
                    **(candidate.get("features") or {}),
                    "source_title": candidate.get("source_title"),
                    "admissions_levels": candidate.get("admissions_levels") or [],
                    "admissions_tracks": candidate.get("admissions_tracks") or [],
                    "candidate_track": candidate.get("candidate_track"),
                    "candidate_type": candidate.get("candidate_type"),
                    "source_suitability_score": candidate.get("source_suitability_score") or 0.0,
                    "topic_relevance_score": candidate.get("topic_relevance_score") or 0.0,
                    "reject_reason_code": candidate.get("reject_reason_code"),
                    "reason": candidate.get("reason"),
                },
                is_recommended=source_url in ranking_urls,
            )
            session.add(row)
            stored[source_url] = row
        session.commit()
        return stored

    def _record_schema_candidate(
        self,
        session: Session,
        candidate_row: SourceDiscoveryCandidate | None,
        validated: Any,
    ) -> None:
        if candidate_row is None:
            return
        session.add(
            SourceSchemaCandidate(
                candidate_id=candidate_row.id,
                schema_type="json",
                config_json=self._json_safe(validated.normalized_source),
                confidence_score=validated.report.success_rate,
                generated_by="agent",
                validation_report_json=self._json_safe(validated.report.model_dump()),
            )
        )
        session.commit()

    def _update_homepage_cache(
        self,
        session: Session,
        *,
        university_name: str,
        resolved_url: str,
        provider: str,
        confidence_score: float,
        raw_candidates: list[dict[str, Any]],
    ) -> None:
        normalized_name = self._normalize_text(university_name)
        cache = session.scalar(
            select(UniversityResolutionCache).where(UniversityResolutionCache.normalized_name == normalized_name)
        )
        if cache is None:
            cache = UniversityResolutionCache(
                university_name=university_name,
                normalized_name=normalized_name,
                resolved_url=resolved_url,
                provider=provider,
                confidence_score=confidence_score,
                raw_candidates_json=raw_candidates,
                last_verified_at=utc_now(),
            )
            session.add(cache)
        else:
            cache.university_name = university_name
            cache.resolved_url = resolved_url
            cache.provider = provider
            cache.confidence_score = confidence_score
            cache.raw_candidates_json = raw_candidates
            cache.last_verified_at = utc_now()
        session.commit()

    def _create_run(self, session: Session, *, input_type: str, input_value: str) -> SourceDiscoveryRun:
        run = SourceDiscoveryRun(
            input_type=input_type,
            input_value=input_value,
            status="pending",
            summary_json={},
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run

    def _finish_run(
        self,
        session: Session,
        run: SourceDiscoveryRun,
        *,
        status: str,
        selected_candidate_url: str | None,
        summary_json: dict[str, Any],
    ) -> None:
        run.status = status
        run.selected_candidate_url = selected_candidate_url
        run.summary_json = summary_json
        session.commit()

    def _candidate_from_link(
        self,
        *,
        homepage_url: str,
        item: dict[str, Any],
        discovery_channel: str,
    ) -> dict[str, Any] | None:
        source_url = item.get("url")
        if not isinstance(source_url, str) or not source_url.strip():
            return None
        absolute_url = urljoin(homepage_url, source_url.strip())
        if not self._is_http_url(absolute_url):
            return None
        if self._registered_domain(homepage_url) != self._registered_domain(absolute_url):
            return None
        title = (item.get("title") or item.get("snippet") or absolute_url).strip()
        snippet = (item.get("snippet") or title).strip()
        if not self._looks_like_admissions_candidate(title=title, url=absolute_url, snippet=snippet):
            return None
        source_kind = self._guess_source_kind(title=title, url=absolute_url, snippet=snippet)
        candidate_type = self._guess_candidate_type(title=title, url=absolute_url, snippet=snippet)
        admissions_tracks = self._guess_tracks(f"{title} {snippet} {absolute_url}")
        admissions_levels = broad_levels_from_tracks(admissions_tracks)
        return {
            "university_name": None,
            "collection_domain": COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            "homepage_url": homepage_url,
            "source_url": absolute_url,
            "source_title": title,
            "source_kind": source_kind,
            "admissions_levels": admissions_levels,
            "admissions_tracks": admissions_tracks,
            "candidate_track": primary_track(admissions_tracks),
            "candidate_type": candidate_type,
            "confidence_score": 0.0,
            "reason": snippet,
            "request_method": "GET",
            "request_data": {},
            "request_json": {},
            "request_headers": {},
            "crawl_mode": "static",
            "discovery_channel": discovery_channel,
        }

    def _extract_links_from_payload(self, *, homepage_url: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_html = payload.get("raw_html") or ""
        soup = BeautifulSoup(raw_html, "lxml")
        links: list[dict[str, Any]] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            if not href or not text:
                continue
            absolute_url = urljoin(homepage_url, href)
            links.append(
                {
                    "title": text,
                    "url": absolute_url,
                    "snippet": text,
                }
            )
        return links

    def _looks_like_admissions_candidate(self, *, title: str, url: str, snippet: str) -> bool:
        combined = f"{title} {url} {snippet}".lower()
        required_tokens = ("招生", "graduate", "undergraduate", "notice", "notices", "admission", "研招", "yz")
        return any(token.lower() in combined for token in required_tokens)

    def _guess_source_kind(self, *, title: str, url: str, snippet: str) -> str:
        candidate_type = self._guess_candidate_type(title=title, url=url, snippet=snippet)
        if candidate_type == "list_page":
            return "list_page"
        if candidate_type == "site_home":
            return "channel_page"
        return "channel_page"

    def _guess_candidate_type(self, *, title: str, url: str, snippet: str) -> str:
        lowered = f"{title} {url} {snippet}".lower()
        parsed = urlparse(url)
        path = (parsed.path or "/").lower()
        query = (parsed.query or "").lower()
        if re.search(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|rar)(?:$|\?)", path):
            return "file_page"
        if path in {"", "/"} or path.endswith("/index.html") or path.endswith("/index.htm"):
            return "site_home"
        if (
            re.search(r"/info/\d+", path)
            or re.search(r"/article/", path)
            or re.search(r"/content/", path)
            or re.search(r"/f/[^/]+/article/", path)
            or re.search(r"/post/\d+/?$", path)
            or re.search(r"/c\d+a\d+/page\.htm$", path)
            or re.search(r"/\d{4}/\d{2,4}/c\d+a\d+/page\.htm$", path)
            or re.search(r"/\d+\.htm$", path)
            or (path.endswith("/page.htm") and re.search(r"/\d{4}/\d{2,4}/", path))
            or re.search(r"/:id/", path)
            or "wbnewsid=" in query
            or "newscontenturl" in query
        ):
            return "detail_page"
        if re.search(r"/\d{4}/\d{2}/", path):
            return "detail_page"
        if re.search(r"/column/\d+/?$", path):
            return "list_page"
        if any(token in lowered for token in ("汇总表", "情况汇总", "分组及选考科目", "统计表")):
            return "stats_page"
        list_tokens = (
            "通知",
            "公告",
            "动态",
            "列表",
            "list",
            "notice",
            "notices",
            "wbtreeid",
            "zsxx",
            "tzgg",
            "zxgg",
            "jzml",
            "sszs",
            "bszs",
            "招生信息",
            "硕士招生",
            "博士招生",
            "硕士生招生",
            "博士生招生",
            "招生简章",
            "招生章程",
            "专业目录",
        )
        if any(token.lower() in lowered for token in list_tokens):
            return "list_page"
        return "channel_page"

    def _guess_tracks(self, value: str) -> list[str]:
        return ["graduate"] if "graduate" in infer_tracks_from_text(value) else []

    def _track_match_score(
        self,
        *,
        requested_tracks: list[str],
        candidate_tracks: list[str],
        requested_levels: list[str],
        candidate_levels: list[str],
    ) -> int:
        if requested_tracks and candidate_tracks:
            overlap = len(set(requested_tracks).intersection(candidate_tracks))
            if overlap:
                return 4 + overlap
            return -4
        if requested_levels and candidate_levels:
            overlap = len(set(requested_levels).intersection(candidate_levels))
            if overlap:
                return 2 + overlap
            return -2
        if candidate_tracks:
            primary = primary_track(candidate_tracks)
            return self.DEFAULT_TRACK_PRIORITY.get(primary or "", 0)
        return 0

    def _select_track_for_candidate(self, candidate_tracks: list[str], requested_tracks: list[str]) -> str | None:
        overlap = [track for track in candidate_tracks if track in requested_tracks]
        if overlap:
            return overlap[0]
        return primary_track(candidate_tracks)

    def _source_suitability_score(
        self,
        *,
        candidate_type: str,
        source_kind: str | None,
        official_domain_match: bool,
        date_hits: int,
        track_match_score: int,
        source_url: str,
    ) -> float:
        score = 0.0
        type_weights = {
            "list_page": 55.0,
            "channel_page": 42.0,
            "site_home": 34.0,
            "detail_page": 8.0,
            "file_page": 0.0,
            "stats_page": 5.0,
        }
        score += type_weights.get(candidate_type, 0.0)
        if official_domain_match:
            score += 20.0
        else:
            score -= 20.0
        score += max(min(track_match_score * 6.0, 24.0), -24.0)
        score += min(date_hits, 3) * 2.0
        if source_kind == "list_page":
            score += 8.0
        if any(token in source_url.lower() for token in ("notice", "notices", "list", "wbtreeid", "zsxx", "tzgg")):
            score += 8.0
        if candidate_type in {"detail_page", "file_page", "stats_page"}:
            score -= 18.0
        return max(0.0, min(score, 100.0))

    def _should_auto_save_candidate(self, candidate: dict[str, Any]) -> bool:
        return float(candidate.get("source_suitability_score") or 0.0) >= self.AUTO_SAVE_THRESHOLD

    def _build_debug_summary(self, results: list[dict[str, Any]], featured: list[dict[str, Any]]) -> dict[str, Any]:
        track_counts: dict[str, int] = {}
        candidate_type_counts: dict[str, int] = {}
        reject_reason_counts: dict[str, int] = {}
        for item in featured:
            track = item.get("candidate_track") or primary_track(item.get("admissions_tracks") or [])
            candidate_type = item.get("candidate_type") or "unknown"
            if track:
                track_counts[track] = track_counts.get(track, 0) + 1
            candidate_type_counts[candidate_type] = candidate_type_counts.get(candidate_type, 0) + 1
        for item in results:
            reason_code = item.get("reject_reason_code")
            if reason_code:
                reject_reason_counts[reason_code] = reject_reason_counts.get(reason_code, 0) + 1
        return {
            "candidate_track_counts": track_counts,
            "candidate_type_counts": candidate_type_counts,
            "reject_reason_counts": reject_reason_counts,
        }

    def _build_track_specific_query(self, university_name: str, admissions_tracks: list[str]) -> str:
        return f"{university_name}研究生招生公告真实数据源"

    def _reject_reason_code(self, issue: str, candidate_type: str | None) -> str:
        text = issue or ""
        if "详情页" in text:
            return "detail_page_rejected"
        if "附件" in text or candidate_type == "file_page":
            return "file_page_rejected"
        if "统计页" in text or candidate_type == "stats_page":
            return "stats_page_rejected"
        if "入口页" in text:
            return "entry_page_rejected"
        if "官方域名" in text:
            return "official_domain_failed"
        if "不可访问" in text:
            return "fetch_failed"
        if "结构化失败" in text:
            return "candidate_parse_failed"
        return "validation_failed"

    def _sort_rankings_for_request(
        self,
        featured: list[dict[str, Any]],
        rankings: list[dict[str, Any]],
        *,
        admissions_levels: list[str],
        admissions_tracks: list[str],
    ) -> list[dict[str, Any]]:
        featured_map = {item.get("source_url"): item for item in featured if item.get("source_url")}
        ranked = [item for item in rankings if item.get("source_url") in featured_map]
        ranked.sort(
            key=lambda item: self._ranking_priority_tuple(
                featured_map.get(item.get("source_url"), {}),
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            ),
            reverse=True,
        )
        return ranked

    def _sort_result_payloads(
        self,
        results: list[dict[str, Any]],
        *,
        admissions_levels: list[str],
        admissions_tracks: list[str],
    ) -> list[dict[str, Any]]:
        return sorted(
            results,
            key=lambda item: self._result_priority_tuple(
                item,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            ),
            reverse=True,
        )

    def _ranking_priority_tuple(
        self,
        candidate: dict[str, Any],
        *,
        admissions_levels: list[str],
        admissions_tracks: list[str],
    ) -> tuple[int, int, float, float]:
        candidate_type = candidate.get("candidate_type")
        track = candidate.get("candidate_track") or primary_track(candidate.get("admissions_tracks") or [])
        stable = 1 if candidate_type in {"site_home", "channel_page", "list_page"} else 0
        broad_request = not admissions_tracks and not admissions_levels
        if broad_request:
            if track == "graduate":
                bucket = 4
            else:
                bucket = 1
        else:
            if track and track in admissions_tracks:
                bucket = 5
            elif stable:
                bucket = 2
            else:
                bucket = 0
        type_bonus = {"list_page": 3, "channel_page": 2, "site_home": 1}.get(candidate_type, 0)
        return (
            bucket,
            stable * 10 + type_bonus,
            float(candidate.get("source_suitability_score") or candidate.get("heuristic_score") or 0.0),
            float(candidate.get("topic_relevance_score") or 0.0),
        )

    def _result_priority_tuple(
        self,
        item: dict[str, Any],
        *,
        admissions_levels: list[str],
        admissions_tracks: list[str],
    ) -> tuple[int, int, float, float]:
        validation_status = item.get("validation_status")
        saved = bool(item.get("saved"))
        candidate_type = item.get("candidate_type")
        if validation_status == "valid" and saved:
            validation_bucket = 4
        elif validation_status == "valid":
            validation_bucket = 3
        elif item.get("reject_reason_code") == "entry_page_rejected":
            validation_bucket = 2
        else:
            validation_bucket = 1
        ranking_tuple = self._ranking_priority_tuple(
            {
                "candidate_type": candidate_type,
                "candidate_track": item.get("track"),
                "admissions_tracks": item.get("admissions_tracks") or [],
                "source_suitability_score": item.get("source_suitability_score") or 0.0,
                "topic_relevance_score": item.get("topic_relevance_score") or 0.0,
            },
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
        )
        return (
            validation_bucket,
            ranking_tuple[0] * 10 + ranking_tuple[1],
            float(item.get("source_suitability_score") or 0.0),
            float(item.get("topic_relevance_score") or 0.0),
        )

    def _dedupe_result_payloads(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in results:
            source_url = self._normalize_url(item.get("source_url"))
            if not source_url or source_url in seen:
                continue
            seen.add(source_url)
            deduped.append(item)
        return deduped

    def _dedupe_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            source_url = item.get("source_url")
            normalized_url = self._normalize_url(source_url)
            if not normalized_url or normalized_url in seen:
                continue
            seen.add(normalized_url)
            deduped.append(item)
        return deduped

    def _normalize_url(self, value: str | None) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        parsed = urlparse(value.strip())
        path = parsed.path or "/"
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path.rstrip("/") or "/", "", parsed.query, ""))

    def _registered_domain(self, value: str | None) -> str:
        host = (urlparse(value or "").hostname or "").lower()
        if host.endswith(".edu.cn"):
            parts = host.split(".")
            return ".".join(parts[-3:]) if len(parts) >= 3 else host
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host

    def _is_http_url(self, value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _normalize_levels(self, values: list[str] | None, *, admissions_tracks: list[str] | None = None) -> list[str]:
        return ["graduate"]

    def _normalize_text(self, value: str) -> str:
        return re.sub(r"[\s,，。；：:（）()【】\-_]+", "", (value or "").strip()).lower()

    def _trace_step(self, name: str, status: str, message: str) -> dict[str, Any]:
        return {
            "name": name,
            "status": status,
            "message": message,
            "timestamp": utc_now().isoformat(),
        }

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        return value
