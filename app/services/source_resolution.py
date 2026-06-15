from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse, urlunparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.nl_task import NLTask
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceProbeRequest, SourceProbeResponse, SourceValidationPreviewItem, SourceValidationReport
from app.services.admissions_tracks import broad_levels_from_tracks, normalize_tracks, primary_track
from app.services.c9_scope import (
    C9_UNIVERSITIES,
    c9_scope_rejection_message,
    mentions_c9_group,
    normalize_c9_university_name,
)
from app.services.domains import (
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
    COLLECTION_DOMAIN_NEWS_CENTER,
    COLLECTION_DOMAIN_SCHOOL_PROFILE,
    normalize_collection_domain,
)
from app.services.llm.source_resolver import LLMSourceResolverService, ResolvedSourceCandidate
from app.services.onboarding.providers import Crawl4AIProvider
from app.services.source_discovery_agent import SourceDiscoveryAgentService
from app.services.source_config import infer_adapter_code, infer_template_code, normalize_source_config
from app.services.source_knowledge import SourceKnowledgeHit, SourceKnowledgeRetriever
from app.services.source_state import source_can_be_reused
from app.services.university_directory import UniversityDirectoryService


@dataclass(slots=True)
class ValidatedResolvedSource:
    candidate: ResolvedSourceCandidate
    report: SourceValidationReport
    normalized_source: dict


@dataclass(slots=True)
class SourceResolutionResult:
    source: Source
    report: SourceValidationReport
    used_existing_source: bool
    strategy: str
    source_knowledge_hits: list[dict]


class LLMResolvedSourceValidator:
    def __init__(self, provider: Crawl4AIProvider | None = None) -> None:
        self.provider = provider or Crawl4AIProvider()

    def validate(self, candidate: ResolvedSourceCandidate) -> ValidatedResolvedSource:
        issues: list[str] = []
        working_candidate = candidate
        title = candidate.source_title
        response: dict | None = None
        last_exc: Exception | None = None
        for variant in self._candidate_url_variants(candidate):
            try:
                response = self._fetch_candidate_payload(variant)
                if not response:
                    raise RuntimeError("Crawl4AI 无法获取页面")
                working_candidate = variant
                title = self._extract_title(response.get("raw_html") or "") or variant.source_title
                break
            except Exception as exc:
                last_exc = exc
                response = None
        if response is None:
            return ValidatedResolvedSource(
                candidate=candidate,
                report=SourceValidationReport(
                    sample_count=0,
                    discovered_count=0,
                    success_count=0,
                    success_rate=0.0,
                    issues=[f"数据源不可访问：{last_exc or 'Crawl4AI 无法获取页面'}"],
                    samples=[],
                ),
                normalized_source={},
            )

        if not self._is_official_source(working_candidate):
            issues.append("数据源域名未通过高校官方域名校验。")
        precision_issue = self._precision_issue(working_candidate, response or {}, title)
        if precision_issue:
            issues.append(precision_issue)
        success = 0 if issues else 1
        report = SourceValidationReport(
            sample_count=1,
            discovered_count=1,
            success_count=success,
            success_rate=0.0 if issues else 1.0,
            issues=issues,
            samples=[
                SourceValidationPreviewItem(
                    title=title,
                    detail_url=working_candidate.source_url,
                    publish_date=None,
                    detail_title=title,
                    content_length=0,
                    attachment_count=0,
                )
            ],
        )
        normalized_source = self._build_normalized_source(working_candidate)
        return ValidatedResolvedSource(candidate=working_candidate, report=report, normalized_source=normalized_source)

    def _fetch_candidate_payload(self, candidate: ResolvedSourceCandidate) -> dict | None:
        response = self.provider.fetch_page_payload(url=candidate.source_url, crawl_mode=candidate.crawl_mode)
        if response:
            return response
        previous = bool(self.provider.settings.crawl4ai_enabled)
        self.provider.settings.crawl4ai_enabled = True
        try:
            return self.provider.fetch_page_payload(url=candidate.source_url, crawl_mode="static")
        finally:
            self.provider.settings.crawl4ai_enabled = previous

    def _candidate_url_variants(self, candidate: ResolvedSourceCandidate) -> list[ResolvedSourceCandidate]:
        variants: list[ResolvedSourceCandidate] = [candidate]
        parsed = urlparse(candidate.source_url)
        alternate_scheme = None
        if parsed.scheme == "http":
            alternate_scheme = "https"
        elif parsed.scheme == "https":
            alternate_scheme = "http"
        if not alternate_scheme:
            return variants
        alternate_url = urlunparse(parsed._replace(scheme=alternate_scheme))
        if alternate_url == candidate.source_url:
            return variants
        payload = candidate.model_dump()
        payload["source_url"] = alternate_url
        variants.append(ResolvedSourceCandidate.model_validate(payload).normalized())
        return variants

    def _build_normalized_source(self, candidate: ResolvedSourceCandidate) -> dict:
        cleaned_title = self._clean_source_title(candidate.source_title)
        config_json = {
            "collection_domain": candidate.collection_domain,
            "institution": candidate.university_name,
            "aliases": [candidate.university_name],
            "source_kind": candidate.source_kind,
            "resolver": "deepseek",
            "source_title": cleaned_title,
            "reason": candidate.reason,
            "list_page_mode": candidate.list_page_mode,
            "pagination_hint": candidate.pagination_hint,
            "detail_link_hint": candidate.detail_link_hint,
            "list_pages": [candidate.source_url],
            "list": {
                "request_method": candidate.request_method,
                "request_data": candidate.request_data,
                "request_json": candidate.request_json,
                "request_headers": candidate.request_headers,
                "item_selector": ".notice-item, .news-item, li, tr",
                "title_selector": "a",
                "link_selector": "a",
                "date_selector": ".date, time, .time",
            },
            "detail": {
                "title_selector": "h1, .title, .notice-title, .news-title",
                "publish_date_selector": ".publish-date, .date, time",
                "content_selector": ".article, article, .content, .main-content, .intro",
                "attachment_selector": ".article a, article a, .content a, .main-content a",
            },
        }
        if candidate.collection_domain == COLLECTION_DOMAIN_SCHOOL_PROFILE:
            config_json["detail"]["content_selector"] = ".article, article, .content, .main-content, .intro, .school-intro"
        if candidate.collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            config_json["admissions_levels"] = candidate.admissions_levels
            config_json["admissions_tracks"] = candidate.admissions_tracks
            config_json["selected_track"] = candidate.candidate_track
        config_json["candidate_type"] = candidate.candidate_type

        normalized_config = normalize_source_config(config_json, start_urls=[candidate.source_url])
        source_name = self._build_source_name(candidate)
        return {
            "name": source_name,
            "organization_name": candidate.university_name,
            "source_type": candidate.collection_domain,
            "collection_domain": candidate.collection_domain,
            "source_origin": "discovered_by_agent",
            "base_url": candidate.homepage_url,
            "start_urls_json": [candidate.source_url],
            "site_type": "school",
            "crawl_mode": candidate.crawl_mode,
            "status": "active",
            "onboarding_status": "validated",
            "confidence_score": candidate.confidence_score,
            "entrypoint_url": candidate.homepage_url,
            "health_status": "healthy",
            "last_discovered_at": utc_now(),
            "last_validated_at": utc_now(),
            "last_success_at": None,
            "last_failure_reason": None,
            "validation_evidence": {
                "validation_issue_count": len([]),
                "resolved_source_url": candidate.source_url,
                "entrypoint_url": candidate.homepage_url,
            },
            "scope_json": {
                "university_name": candidate.university_name,
                "admissions_levels": candidate.admissions_levels,
                "admissions_tracks": candidate.admissions_tracks,
                "selected_track": candidate.candidate_track,
            },
            "resolver_meta_json": {
                "reason": candidate.reason,
                "confidence_score": candidate.confidence_score,
                "source_suitability_score": candidate.source_suitability_score,
                "topic_relevance_score": candidate.topic_relevance_score,
                "resolved_at": utc_now().isoformat(),
                "homepage_url": candidate.homepage_url,
                "source_url": candidate.source_url,
                "source_kind": candidate.source_kind,
                "candidate_type": candidate.candidate_type,
                "selected_track": candidate.candidate_track,
                "admissions_tracks": candidate.admissions_tracks,
                "canonical_start_url_pattern": self._canonical_start_url_pattern(candidate.source_url),
            },
            "config_json": normalized_config,
        }

    def _build_source_name(self, candidate: ResolvedSourceCandidate) -> str:
        title = self._clean_source_title(candidate.source_title)
        university_name = (candidate.university_name or "").strip()
        if not title:
            return f"{university_name}{candidate.collection_domain}"
        if university_name and university_name not in title:
            return f"{university_name} {title}"
        return title

    def _clean_source_title(self, title: str | None) -> str:
        cleaned = re.sub(r"[\ue000-\uf8ff]", "", (title or "")).strip()
        return re.sub(r"\s+", " ", cleaned)

    def _registered_domain(self, value: str) -> str:
        host = (urlparse(value).hostname or "").lower()
        if host.endswith(".edu.cn"):
            parts = host.split(".")
            return ".".join(parts[-3:]) if len(parts) >= 3 else host
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host

    def _is_official_source(self, candidate: ResolvedSourceCandidate) -> bool:
        homepage_domain = self._registered_domain(candidate.homepage_url)
        source_domain = self._registered_domain(candidate.source_url)
        if not homepage_domain or not source_domain:
            return False
        if homepage_domain != source_domain:
            return False
        return source_domain.endswith(".edu.cn") or ".edu." in source_domain

    def _extract_title(self, html: str) -> str | None:
        import re

        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _precision_issue(
        self,
        candidate: ResolvedSourceCandidate,
        response: dict,
        title: str | None,
    ) -> str | None:
        collection_domain = normalize_collection_domain(candidate.collection_domain)
        if collection_domain == COLLECTION_DOMAIN_NEWS_CENTER:
            source_url = (candidate.source_url or "").lower()
            if candidate.source_kind == "profile_page":
                return "数据源更像单页介绍，不是新闻中心列表页。"
            if re.search(r"/info/\d+/\d+\.htm$", source_url):
                return "数据源更像单篇新闻详情页，不是新闻中心列表页。"
            return None

        if collection_domain != COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            return None
        scope_message = c9_scope_rejection_message(
            university_name=candidate.university_name,
            query=f"{candidate.source_title} {candidate.source_url}",
            collection_domain=collection_domain,
            admissions_levels=candidate.admissions_levels,
            admissions_tracks=candidate.admissions_tracks,
            homepage_url=candidate.homepage_url,
        )
        if scope_message:
            return scope_message
        if "graduate" not in (candidate.admissions_levels or []):
            return "当前只允许保存校级研究生招生公告数据源。"
        source_url = (candidate.source_url or "").lower()
        source_path = urlparse(source_url).path or "/"
        if (
            re.search(r"/post/\d+/?$", source_path)
            or
            re.search(r"/c\d+a\d+/page\.htm$", source_path)
            or re.search(r"/\d{4}/\d{2,4}/c\d+a\d+/page\.htm$", source_path)
            or re.search(r"/\d+\.htm$", source_path)
            or (source_path.endswith("/page.htm") and re.search(r"/\d{4}/\d{2,4}/", source_path))
        ):
            return "数据源更像单篇详情页，不是可复用的数据源。"
        if candidate.candidate_type == "detail_page":
            return "数据源更像单篇详情页，不是可复用的数据源。"
        if candidate.candidate_type == "file_page":
            return "数据源更像附件下载页，不是可复用的数据源。"
        if candidate.candidate_type == "stats_page":
            return "数据源更像一次性统计页，不是稳定的数据源。"
        if candidate.source_kind == "profile_page":
            return "数据源更像概况页，不是招生公告列表页。"
        strict_list_url_tokens = ("notice", "notices", "list", "tzgg", "gg", "zsxx", "news")
        channel_without_strong_url = candidate.source_kind == "channel_page" and not any(
            token in source_url
            for token in strict_list_url_tokens
        )

        combined = " ".join(
            [
                title or "",
                candidate.source_title or "",
                candidate.source_url or "",
                (response.get("raw_text") or "")[:4000],
            ]
        )
        precise_tokens = ("通知", "公告", "动态", "简章", "复试", "调剂", "夏令营", "推免", "拟录取", "录取")
        if channel_without_strong_url:
            if self._is_non_c9_user_entrypoint(candidate) and self._has_relaxed_admissions_evidence(
                candidate=candidate,
                response=response,
                combined=combined,
                precise_tokens=precise_tokens,
            ):
                return None
            return "数据源更像招生入口页，未精确定位到公告列表页。"

        if any(token in combined for token in precise_tokens):
            return None

        links = response.get("links") or []
        matching_links = 0
        for item in links:
            if not isinstance(item, dict):
                continue
            link_text = f"{item.get('title') or ''} {item.get('url') or ''} {item.get('snippet') or ''}"
            if any(token in link_text for token in precise_tokens):
                matching_links += 1
            if matching_links >= 2:
                return None

        if candidate.source_kind == "channel_page":
            return "数据源更像招生入口页，未精确定位到公告列表页。"
        return None

    def _is_non_c9_user_entrypoint(self, candidate: ResolvedSourceCandidate) -> bool:
        return bool((candidate.homepage_url or "").strip()) and not normalize_c9_university_name(candidate.university_name)

    def _has_relaxed_admissions_evidence(
        self,
        *,
        candidate: ResolvedSourceCandidate,
        response: dict,
        combined: str,
        precise_tokens: tuple[str, ...],
    ) -> bool:
        graduate_tokens = ("研究生", "硕士", "博士", "研招", "yjs", "yz", "sszs", "bszs", "graduate", "postgraduate")
        notice_hits = sum(1 for token in precise_tokens if token in combined)
        graduate_hits = sum(1 for token in graduate_tokens if token.lower() in combined.lower())
        date_hits = len(re.findall(r"20\d{2}[-/.年]\d{1,2}", combined))

        matching_links = 0
        for item in response.get("links") or []:
            if not isinstance(item, dict):
                continue
            link_text = f"{item.get('title') or ''} {item.get('url') or ''} {item.get('snippet') or ''}"
            link_has_notice = any(token in link_text for token in precise_tokens)
            link_has_graduate = any(token.lower() in link_text.lower() for token in graduate_tokens)
            if link_has_notice and link_has_graduate:
                matching_links += 1
            if matching_links >= 2:
                return True

        if graduate_hits <= 0:
            return False
        if notice_hits >= 2:
            return True
        return notice_hits >= 1 and (date_hits >= 1 or matching_links >= 1)

    def _canonical_start_url_pattern(self, source_url: str | None) -> str:
        raw = (source_url or "").strip().lower()
        if not raw:
            return ""
        parsed = urlparse(raw)
        path = re.sub(r"/\d{4}/\d{2}/", "/:date/", parsed.path or "/")
        path = re.sub(r"/\d+/", "/:id/", path)
        path = re.sub(r"[0-9a-f]{24,}", ":hash", path)
        return urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/") or "/", "", parsed.query, ""))


class SourceResolutionService:
    def __init__(
        self,
        resolver: LLMSourceResolverService | None = None,
        validator: LLMResolvedSourceValidator | None = None,
    ) -> None:
        self.resolver = resolver or LLMSourceResolverService()
        self.validator = validator or LLMResolvedSourceValidator()
        self.crawl4ai = Crawl4AIProvider()
        self.source_knowledge_retriever = SourceKnowledgeRetriever()
        self.directory_service = UniversityDirectoryService()
        self.discovery_agent = SourceDiscoveryAgentService(
            resolver=self.resolver,
            validator=self.validator,
            provider=self.crawl4ai,
            find_existing_source=self.find_existing_source,
            upsert_source=self._upsert_source,
        )

    def ensure_source(
        self,
        session: Session,
        *,
        university_name: str,
        collection_domain: str,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        homepage_url: str | None = None,
        force_refresh_source: bool = False,
    ) -> tuple[Source, SourceValidationReport, bool]:
        result = self.ensure_source_result(
            session,
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            homepage_url=homepage_url,
            force_refresh_source=force_refresh_source,
        )
        return result.source, result.report, result.used_existing_source

    def ensure_source_result(
        self,
        session: Session,
        *,
        university_name: str,
        collection_domain: str,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        homepage_url: str | None = None,
        force_refresh_source: bool = False,
        query: str | None = None,
    ) -> SourceResolutionResult:
        collection_domain = normalize_collection_domain(collection_domain)
        admissions_tracks = normalize_tracks(admissions_tracks)
        admissions_levels = self._normalize_admissions_levels(admissions_levels, admissions_tracks=admissions_tracks)
        self._assert_c9_graduate_scope(
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            homepage_url=homepage_url,
        )
        if collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            bootstrap_strategy = "request_homepage" if homepage_url else None
            entrypoint_url = homepage_url
            rediscovery_triggered = bool(force_refresh_source)
            if not homepage_url and not force_refresh_source:
                rediscovery_triggered = self._needs_rediscovery(
                    session,
                    university_name=university_name,
                    collection_domain=collection_domain,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                )
                knowledge_hit = self.source_knowledge_retriever.best_hit(
                    session,
                    query=query or self._build_resolution_query(
                        university_name=university_name,
                        collection_domain=collection_domain,
                        admissions_levels=admissions_levels,
                    ),
                    collection_domain=collection_domain,
                    university_name=university_name,
                    desired_count=1,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                )
                if knowledge_hit is not None:
                    return self._ensure_source_from_knowledge_hit(session, knowledge_hit)
            if not entrypoint_url:
                directory_entry = self.directory_service.get_entry(session, university_name)
                if directory_entry is None:
                    raise ValueError("官方入口目录中未找到该高校研究生招生入口。")
                entrypoint_url = self.directory_service.selected_entrypoint_url(directory_entry) or directory_entry.official_homepage_url
                bootstrap_strategy = "official_directory"
            agent_result = self.discovery_agent.ensure_source(
                session,
                university_name=university_name,
                collection_domain=collection_domain,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                homepage_url=entrypoint_url,
                homepage_source=bootstrap_strategy or "request_homepage",
                force_refresh_source=force_refresh_source,
                rediscovery_triggered=rediscovery_triggered,
            )
            if not agent_result.success or agent_result.source is None or agent_result.report is None:
                raise ValueError(agent_result.failure_reason or "受控选源智能体未能找到可用数据源。")
            return SourceResolutionResult(
                source=agent_result.source,
                report=agent_result.report,
                used_existing_source=agent_result.used_existing_source,
                strategy="existing_source" if agent_result.used_existing_source else "live_discovery",
                source_knowledge_hits=[],
            )
        existing = None if force_refresh_source else self.find_existing_source(
            session,
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=admissions_levels,
        )
        if existing is not None:
            validated = self.validator.validate(
                ResolvedSourceCandidate(
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
                    reason=(existing.resolver_meta_json or {}).get("reason") or "复用已保存数据源",
                    crawl_mode=existing.crawl_mode,
                ).normalized()
            )
            if validated.report.success_rate > 0:
                return SourceResolutionResult(
                    source=existing,
                    report=validated.report,
                    used_existing_source=True,
                    strategy="existing_source",
                    source_knowledge_hits=[],
                )

        try:
            candidate = self.resolver.resolve(
                university_name=university_name,
                collection_domain=collection_domain,
                homepage_url=homepage_url,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                session=session,
            )
        except Exception as exc:
            raise ValueError(f"LLM 选源返回了无效候选：{exc}") from exc
        validated = self.validator.validate(candidate)
        if validated.report.success_rate <= 0:
            raise ValueError(validated.report.issues[0] if validated.report.issues else "LLM 选源校验失败。")
        source = self._upsert_source(session, validated)
        return SourceResolutionResult(
            source=source,
            report=validated.report,
            used_existing_source=False,
            strategy="fallback",
            source_knowledge_hits=[],
        )

    def _ensure_source_from_knowledge_hit(self, session: Session, hit: SourceKnowledgeHit) -> SourceResolutionResult:
        if hit.source_id is None:
            raise ValueError("数据源知识库命中缺少已保存 source_id。")
        source = session.get(Source, hit.source_id)
        if source is None:
            raise ValueError("数据源知识库命中对应的 source 已不存在。")
        return SourceResolutionResult(
            source=source,
            report=self._knowledge_hit_report(hit),
            used_existing_source=True,
            strategy="source_knowledge",
            source_knowledge_hits=[hit.to_debug_dict()],
        )

    def _knowledge_hit_report(self, hit: SourceKnowledgeHit) -> SourceValidationReport:
        return SourceValidationReport(
            sample_count=0,
            discovered_count=1,
            success_count=1,
            success_rate=hit.confidence_score,
            issues=[hit.evidence_snippet],
            samples=[],
        )

    def probe(self, session: Session, payload: SourceProbeRequest) -> SourceProbeResponse:
        university_name = payload.university_name or payload.organization_name or ""
        if not university_name:
            raise ValueError("请提供高校名称。")
        admissions_tracks = normalize_tracks(payload.admissions_tracks)
        collection_domain = normalize_collection_domain(payload.collection_domain)
        self._assert_c9_graduate_scope(
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=payload.admissions_levels,
            admissions_tracks=admissions_tracks,
            homepage_url=payload.url,
        )
        university_name = normalize_c9_university_name(university_name) or university_name
        if normalize_collection_domain(payload.collection_domain) == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            bootstrap_strategy = "request_homepage" if payload.url else None
            entrypoint_url = payload.url
            source_reuse_reason = None
            health_status = None
            rediscovery_triggered = bool(payload.force_refresh_source)
            if not payload.url and not payload.force_refresh_source:
                rediscovery_triggered = self._needs_rediscovery(
                    session,
                    university_name=university_name,
                    collection_domain=collection_domain,
                    admissions_levels=["graduate"],
                    admissions_tracks=["graduate"],
                )
                knowledge_hit = self.source_knowledge_retriever.best_hit(
                    session,
                    query=f"提供{university_name}研究生招生公告的真实数据源",
                    collection_domain=collection_domain,
                    university_name=university_name,
                    desired_count=1,
                    admissions_levels=["graduate"],
                    admissions_tracks=["graduate"],
                )
                if knowledge_hit is not None:
                    resolution = self._ensure_source_from_knowledge_hit(session, knowledge_hit)
                    source = resolution.source
                    report = resolution.report
                    template_code = infer_template_code(source.config_json) or "admissions_list_static"
                    adapter_code = infer_adapter_code(source.config_json)
                    resolved_source_url = (source.start_urls_json or [source.base_url])[0]
                    return SourceProbeResponse(
                        normalized_source=SourceCreate.model_validate(source),
                        report=report,
                        recommended_adapter=adapter_code,
                        recommended_template=template_code,
                        probe_mode="source_knowledge",
                        selected_url=resolved_source_url,
                        collection_domain=source.collection_domain,
                        university_name=university_name,
                        resolved_homepage_url=source.base_url,
                        resolved_source_url=resolved_source_url,
                        used_existing_source=True,
                        source_id=source.id,
                        selection_confidence=source.confidence_score or report.success_rate,
                        notes=["命中健康且已验证的数据源知识库，直接复用已有 source。"],
                        agent_trace=[],
                        candidate_rankings=[],
                        failure_reason=None,
                        used_browser_explorer=False,
                        bootstrap_strategy="source_knowledge",
                        entrypoint_url=source.entrypoint_url or source.base_url,
                        source_reuse_reason=knowledge_hit.source_reuse_reason,
                        health_status=source.health_status,
                        rediscovery_triggered=False,
                    )
            if not entrypoint_url:
                directory_entry = self.directory_service.get_entry(session, university_name)
                if directory_entry is None:
                    raise ValueError("官方入口目录中未找到该高校研究生招生入口。")
                entrypoint_url = self.directory_service.selected_entrypoint_url(directory_entry) or directory_entry.official_homepage_url
                bootstrap_strategy = "official_directory"
            agent_result = self.discovery_agent.ensure_source(
                session,
                university_name=university_name,
                collection_domain=collection_domain,
                admissions_levels=["graduate"],
                admissions_tracks=["graduate"],
                homepage_url=entrypoint_url,
                homepage_source=bootstrap_strategy or "request_homepage",
                force_refresh_source=payload.force_refresh_source,
                rediscovery_triggered=rediscovery_triggered,
            )
            if not agent_result.success or agent_result.source is None or agent_result.report is None:
                raise ValueError(agent_result.failure_reason or "受控选源智能体未能找到可用数据源。")
            source = agent_result.source
            report = agent_result.report
            used_existing = agent_result.used_existing_source
            source_reuse_reason = (agent_result.debug or {}).get("source_reuse_reason")
            health_status = (agent_result.debug or {}).get("health_status") or source.health_status
            template_code = infer_template_code(source.config_json) or "admissions_list_static"
            adapter_code = infer_adapter_code(source.config_json)
            resolved_source_url = (source.start_urls_json or [source.base_url])[0]
            return SourceProbeResponse(
                normalized_source=SourceCreate.model_validate(source),
                report=report,
                recommended_adapter=adapter_code,
                recommended_template=template_code,
                probe_mode="admissions_source_discovery_agent",
                selected_url=resolved_source_url,
                collection_domain=source.collection_domain,
                university_name=university_name,
                resolved_homepage_url=agent_result.resolved_homepage_url or source.base_url,
                resolved_source_url=resolved_source_url,
                used_existing_source=used_existing,
                source_id=source.id,
                selection_confidence=source.confidence_score or report.success_rate,
                notes=[
                    "招生公告数据源由受控选源智能体发现并校验。",
                    "失败时会保留候选排序和执行轨迹用于排障。",
                ],
                agent_trace=agent_result.agent_trace,
                candidate_rankings=agent_result.candidate_rankings,
                failure_reason=agent_result.failure_reason,
                used_browser_explorer=agent_result.used_browser_explorer,
                bootstrap_strategy=bootstrap_strategy,
                entrypoint_url=entrypoint_url,
                source_reuse_reason=source_reuse_reason,
                health_status=health_status,
                rediscovery_triggered=rediscovery_triggered,
            )
        source, report, used_existing = self.ensure_source(
            session,
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=["graduate"],
            admissions_tracks=["graduate"],
            homepage_url=payload.url,
            force_refresh_source=payload.force_refresh_source,
        )
        template_code = infer_template_code(source.config_json) or "admissions_list_static"
        adapter_code = infer_adapter_code(source.config_json)
        resolved_source_url = (source.start_urls_json or [source.base_url])[0]
        return SourceProbeResponse(
            normalized_source=SourceCreate.model_validate({
                "name": source.name,
                "organization_name": source.organization_name,
                "source_type": source.source_type,
                "collection_domain": source.collection_domain,
                "source_origin": source.source_origin,
                "adapter_id": source.adapter_id,
                "template_id": source.template_id,
                "base_url": source.base_url,
                "start_urls_json": source.start_urls_json,
                "site_type": source.site_type,
                "crawl_mode": source.crawl_mode,
                "status": source.status,
                "onboarding_status": source.onboarding_status,
                "confidence_score": source.confidence_score,
                "scope_json": source.scope_json,
                "resolver_meta_json": source.resolver_meta_json,
                "config_json": source.config_json,
            }),
            report=report,
            recommended_adapter=adapter_code,
            recommended_template=template_code,
            probe_mode="llm_source_resolve",
            selected_url=resolved_source_url,
            collection_domain=source.collection_domain,
            university_name=university_name,
            resolved_homepage_url=source.base_url,
            resolved_source_url=resolved_source_url,
            used_existing_source=used_existing,
            source_id=source.id,
            selection_confidence=source.confidence_score or 0.0,
            notes=[
                "数据源由 DeepSeek 直选后落库。",
                "系统已执行轻量校验并保存可复用数据源。",
            ],
            agent_trace=[],
            candidate_rankings=[],
            failure_reason=None,
            used_browser_explorer=False,
        )

    def discover_sources_from_query(
        self,
        session: Session,
        *,
        query: str,
        collection_domain: str,
        desired_count: int = 10,
        homepage_url: str | None = None,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
    ) -> tuple[list[dict], dict]:
        admissions_tracks = normalize_tracks(admissions_tracks)
        admissions_levels = self._normalize_admissions_levels(admissions_levels, admissions_tracks=admissions_tracks)
        collection_domain = normalize_collection_domain(collection_domain)
        university_name = self._extract_university_name_from_query(query)
        if university_name:
            university_name = normalize_c9_university_name(university_name) or university_name
        if collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            if not university_name and mentions_c9_group(query):
                return self._discover_c9_batch_sources(
                    session,
                    query=query,
                    desired_count=desired_count,
                    homepage_url=homepage_url,
                )
            self._assert_c9_graduate_scope(
                university_name=university_name,
                collection_domain=collection_domain,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                query=query,
                homepage_url=homepage_url,
            )
        if collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE and university_name:
            entrypoint_url = homepage_url
            bootstrap_strategy = "request_homepage" if homepage_url else None
            rediscovery_triggered = False
            if not homepage_url:
                rediscovery_triggered = self._needs_rediscovery(
                    session,
                    university_name=university_name,
                    collection_domain=collection_domain,
                    admissions_levels=["graduate"],
                    admissions_tracks=["graduate"],
                )
                knowledge_hits = self.source_knowledge_retriever.search(
                    session,
                    query=query,
                    collection_domain=collection_domain,
                    university_name=university_name,
                    desired_count=desired_count,
                    admissions_levels=["graduate"],
                    admissions_tracks=["graduate"],
                )
                if knowledge_hits:
                    results = [self._result_from_knowledge_hit(session, hit) for hit in knowledge_hits[:desired_count]]
                    return (
                        results,
                        self._source_knowledge_debug(
                            query=query,
                            hits=knowledge_hits,
                            admissions_levels=["graduate"],
                            admissions_tracks=["graduate"],
                        ),
                    )
                directory_entry = self.directory_service.get_entry(session, university_name)
                if directory_entry is None:
                    raise ValueError("官方入口目录中未找到该高校研究生招生入口。")
                entrypoint_url = self.directory_service.selected_entrypoint_url(directory_entry) or directory_entry.official_homepage_url
                bootstrap_strategy = "official_directory"
            agent_result = self.discovery_agent.discover_sources(
                session,
                query=query,
                university_name=university_name,
                homepage_url=entrypoint_url,
                homepage_source=bootstrap_strategy or "request_homepage",
                desired_count=desired_count,
                admissions_levels=["graduate"],
                admissions_tracks=["graduate"],
            )
            return (
                agent_result.resolved_sources,
                {
                    **agent_result.debug,
                    "source_resolution_strategy": "live_discovery",
                    "source_knowledge_hits": [],
                    "bootstrap_strategy": bootstrap_strategy or "request_homepage",
                    "entrypoint_url": entrypoint_url,
                    "rediscovery_triggered": rediscovery_triggered,
                    "agent_trace": agent_result.agent_trace,
                    "candidate_rankings": agent_result.candidate_rankings,
                    "failure_reason": agent_result.failure_reason,
                    "used_browser_explorer": agent_result.used_browser_explorer,
                },
            )
        debug: dict = {
            "homepage_url": homepage_url,
            "candidate_provider": None,
            "source_resolution_strategy": "live_discovery" if homepage_url else "fallback",
            "source_knowledge_hits": [],
            "raw_candidate_count": 0,
            "raw_candidates": [],
            "llm_selected_candidates": [],
            "llm_fallback_candidates": [],
            "cache_hit": False,
            "admissions_levels": admissions_levels,
            "admissions_tracks": admissions_tracks,
        }
        if homepage_url:
            discovery_payload = self.crawl4ai.discover_candidate_links(
                homepage_url=homepage_url,
                max_links=max(20, desired_count * 4),
            )
            homepage_candidates = discovery_payload.get("links") or []
            debug["candidate_provider"] = discovery_payload.get("provider")
            debug["raw_candidate_count"] = len(homepage_candidates)
            debug["raw_candidates"] = homepage_candidates[:20]
            selection_count = max(desired_count, 3)
            if homepage_candidates:
                loose_candidates = self.resolver.choose_from_candidates(
                    query=query,
                    collection_domain=collection_domain,
                    homepage_url=homepage_url,
                    candidates=homepage_candidates,
                    desired_count=selection_count,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                    session=session,
                )
                debug["llm_selected_candidates"] = loose_candidates[:20]
                if len(loose_candidates) < min(3, selection_count):
                    fallback_candidates = self.resolver.resolve_many_loose(
                        query=query,
                        collection_domain=collection_domain,
                        desired_count=min(3, selection_count),
                        admissions_levels=admissions_levels,
                        admissions_tracks=admissions_tracks,
                        session=session,
                    )
                    debug["llm_fallback_candidates"] = fallback_candidates[:20]
                    loose_candidates = self._dedupe_candidate_dicts(loose_candidates + fallback_candidates)
            else:
                loose_candidates = self.resolver.resolve_many_loose(
                    query=query,
                    collection_domain=collection_domain,
                    desired_count=desired_count,
                    admissions_levels=admissions_levels,
                    admissions_tracks=admissions_tracks,
                    session=session,
                )
                debug["llm_selected_candidates"] = loose_candidates[:20]
        else:
            loose_candidates = self.resolver.resolve_many_loose(
                query=query,
                collection_domain=collection_domain,
                desired_count=desired_count,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
                session=session,
            )
            debug["candidate_provider"] = "llm_direct"
            debug["llm_selected_candidates"] = loose_candidates[:20]
        loose_candidates = self._dedupe_candidate_dicts(loose_candidates)
        results: list[dict] = []
        for raw in loose_candidates:
            saved_source_id = None
            saved = False
            validation_status = "unchecked"
            validation_message = None
            try:
                candidate = self.resolver.strict_candidate_from_payload(
                    raw,
                    default_collection_domain=collection_domain,
                    default_admissions_levels=admissions_levels,
                    default_admissions_tracks=admissions_tracks,
                )
                validation = self.validator.validate(candidate)
                validation_status = "invalid" if validation.report.issues else "valid"
                validation_message = validation.report.issues[0] if validation.report.issues else None
                if validation.report.success_rate > 0 and validation.normalized_source:
                    source = self._upsert_source(session, validation)
                    saved_source_id = source.id
                    saved = True
                item = validation.candidate.model_dump()
            except Exception as exc:
                session.rollback()
                validation_status = "unchecked"
                validation_message = f"结构化校验未通过，但已保留模型原始结果：{exc}"
                item = dict(raw)
            item.setdefault("collection_domain", collection_domain)
            item.setdefault("confidence_score", 0.0)
            item.setdefault("source_kind", "list_page")
            item.setdefault("admissions_levels", [])
            item.setdefault("admissions_tracks", [])
            item.setdefault("reason", validation_message or item.get("reason"))
            results.append(
                {
                    "source_id": saved_source_id,
                    "saved": saved,
                    "validation_status": validation_status,
                    "validation_message": validation_message,
                    "university_name": item.get("university_name"),
                    "collection_domain": item.get("collection_domain"),
                    "homepage_url": item.get("homepage_url"),
                    "source_url": item.get("source_url"),
                    "source_title": item.get("source_title"),
                    "source_kind": item.get("source_kind"),
                    "admissions_levels": item.get("admissions_levels") or [],
                    "admissions_tracks": item.get("admissions_tracks") or [],
                    "track": item.get("candidate_track") or primary_track(item.get("admissions_tracks") or []),
                    "candidate_type": item.get("candidate_type"),
                    "source_suitability_score": item.get("source_suitability_score") or 0.0,
                    "topic_relevance_score": item.get("topic_relevance_score") or 0.0,
                    "reject_reason_code": item.get("reject_reason_code"),
                    "confidence_score": item.get("confidence_score") or 0.0,
                    "reason": item.get("reason"),
                }
            )
        return results, debug

    def _result_from_knowledge_hit(self, session: Session, hit: SourceKnowledgeHit) -> dict:
        resolution = self._ensure_source_from_knowledge_hit(session, hit)
        source_id = resolution.source.id
        payload = {**hit.to_debug_dict(), "source_id": source_id}
        return {
            "source_id": source_id,
            "saved": True,
            "validation_status": "valid",
            "validation_message": None,
            "university_name": payload.get("university_name"),
            "collection_domain": payload.get("collection_domain"),
            "homepage_url": payload.get("homepage_url"),
            "source_url": payload.get("source_url"),
            "source_title": payload.get("source_title"),
            "source_kind": payload.get("source_kind"),
            "admissions_levels": payload.get("admissions_levels") or [],
            "admissions_tracks": payload.get("admissions_tracks") or [],
            "track": payload.get("track"),
            "candidate_type": payload.get("candidate_type"),
            "source_suitability_score": payload.get("selection_score") or 0.0,
            "topic_relevance_score": payload.get("selection_score") or 0.0,
            "reject_reason_code": None,
            "confidence_score": payload.get("confidence_score") or 0.0,
            "reason": payload.get("reason"),
            "source_origin": payload.get("source_origin"),
            "evidence_snippet": payload.get("evidence_snippet"),
            "source_resolution_strategy": "source_knowledge",
            "health_status": payload.get("health_status"),
        }

    def _source_knowledge_debug(
        self,
        *,
        query: str,
        hits: list[SourceKnowledgeHit],
        admissions_levels: list[str],
        admissions_tracks: list[str],
    ) -> dict:
        return {
            "homepage_url": None,
            "candidate_provider": "source_knowledge",
            "source_resolution_strategy": "source_knowledge",
            "source_knowledge_hits": [hit.to_debug_dict() for hit in hits],
            "raw_candidate_count": len(hits),
            "raw_candidates": [hit.to_debug_dict() for hit in hits],
            "llm_selected_candidates": [],
            "llm_fallback_candidates": [],
            "cache_hit": False,
            "admissions_levels": admissions_levels,
            "admissions_tracks": admissions_tracks,
            "bootstrap_strategy": "source_knowledge",
            "entrypoint_url": None,
            "source_reuse_reason": hits[0].source_reuse_reason if hits else None,
            "health_status": hits[0].health_status if hits else None,
            "rediscovery_triggered": False,
            "agent_trace": [
                {
                    "step": "source_knowledge_retrieval",
                    "status": "success",
                    "message": f"从数据源知识库检索到 {len(hits)} 个研招源候选。",
                    "query": query,
                }
            ],
            "candidate_rankings": [
                {
                    "source_url": hit.source_url,
                    "source_title": hit.source_title,
                    "score": hit.selection_score,
                    "recommended_action": "reuse",
                    "reason": hit.reason,
                }
                for hit in hits
            ],
            "failure_reason": None,
            "used_browser_explorer": False,
        }

    def find_existing_source(
        self,
        session: Session,
        *,
        university_name: str,
        collection_domain: str,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        candidate_type: str | None = None,
        source_url: str | None = None,
        require_reusable: bool = False,
    ) -> Source | None:
        admissions_tracks = normalize_tracks(admissions_tracks)
        admissions_levels = self._normalize_admissions_levels(admissions_levels, admissions_tracks=admissions_tracks)
        candidates = list(
            session.scalars(
                select(Source).where(
                    Source.organization_name == university_name,
                    Source.collection_domain == collection_domain,
                    Source.status == "active",
                )
            )
        )
        if require_reusable:
            candidates = [source for source in candidates if source_can_be_reused(source)]
        normalized_source_url = (source_url or "").strip()
        if normalized_source_url:
            exact_match = next(
                (
                    source
                    for source in candidates
                    if normalized_source_url in ((source.start_urls_json or [source.base_url]) or [])
                ),
                None,
            )
            if exact_match is not None:
                return exact_match
            if normalize_collection_domain(collection_domain) != COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
                return None
        if not admissions_levels:
            return self._pick_best_existing_source(
                candidates,
                collection_domain=collection_domain,
                admissions_tracks=admissions_tracks,
                candidate_type=candidate_type,
            )
        wanted = set(admissions_levels)
        matching_candidates: list[Source] = []
        for source in candidates:
            current_levels = set((source.scope_json or {}).get("admissions_levels") or [])
            current_tracks = set((source.scope_json or {}).get("admissions_tracks") or [])
            track_match = not admissions_tracks or bool(set(admissions_tracks).intersection(current_tracks))
            type_match = not candidate_type or (source.resolver_meta_json or {}).get("candidate_type") == candidate_type
            if (wanted.issubset(current_levels) or current_levels.issubset(wanted)) and track_match and type_match:
                matching_candidates.append(source)
        return self._pick_best_existing_source(
            matching_candidates or candidates,
            collection_domain=collection_domain,
            admissions_tracks=admissions_tracks,
            candidate_type=candidate_type,
        )

    def _upsert_source(self, session: Session, validated: ValidatedResolvedSource) -> Source:
        payload = dict(validated.normalized_source)
        requested_source_url = (payload.get("start_urls_json") or [payload.get("base_url")])[0]
        source = self.find_existing_source(
            session,
            university_name=payload["organization_name"],
            collection_domain=payload["collection_domain"],
            admissions_levels=payload["scope_json"].get("admissions_levels") or [],
            admissions_tracks=payload["scope_json"].get("admissions_tracks") or [],
            candidate_type=(payload.get("resolver_meta_json") or {}).get("candidate_type"),
            source_url=requested_source_url,
            require_reusable=False,
        )
        if source is not None:
            existing_urls = (source.start_urls_json or [source.base_url]) or []
            if requested_source_url not in existing_urls:
                source = None
        payload["entrypoint_url"] = payload.get("entrypoint_url") or payload.get("base_url")
        payload["health_status"] = "healthy"
        payload["last_discovered_at"] = utc_now()
        payload["last_validated_at"] = utc_now()
        payload["last_failure_reason"] = None
        payload["validation_evidence"] = {
            "issues": list(validated.report.issues or []),
            "success_rate": validated.report.success_rate,
            "source_url": requested_source_url,
            "entrypoint_url": payload.get("entrypoint_url"),
        }
        if source is None:
            payload["name"] = self._allocate_source_name(
                session,
                payload["name"],
                organization_name=payload["organization_name"],
            )
            source = Source(**payload)
            session.add(source)
        else:
            for key, value in payload.items():
                setattr(source, key, value)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if source is not None and source.id is not None:
                raise
            payload["name"] = self._allocate_source_name(
                session,
                payload["name"],
                organization_name=payload["organization_name"],
            )
            source = Source(**payload)
            session.add(source)
            session.commit()
        session.refresh(source)
        return source

    def _allocate_source_name(
        self,
        session: Session,
        name: str,
        *,
        organization_name: str | None,
    ) -> str:
        base_name = (name or "").strip() or "未命名数据源"
        if session.scalar(select(Source.id).where(Source.name == base_name)) is None:
            return base_name

        org_name = (organization_name or "").strip()
        if org_name:
            preferred = f"{base_name} ({org_name})"
            if session.scalar(select(Source.id).where(Source.name == preferred)) is None:
                return preferred

        suffix = 2
        while True:
            candidate = f"{base_name} ({suffix})"
            if session.scalar(select(Source.id).where(Source.name == candidate)) is None:
                return candidate
            suffix += 1

    def _sanitize_cached_resolved_sources(self, resolved_sources: list[dict]) -> list[dict]:
        sanitized: list[dict] = []
        for item in resolved_sources:
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            normalized_item["source_title"] = self.validator._clean_source_title(item.get("source_title"))
            sanitized.append(normalized_item)
        return sanitized

    def _build_resolution_query(
        self,
        *,
        university_name: str,
        collection_domain: str,
        admissions_levels: list[str] | None = None,
    ) -> str:
        if normalize_collection_domain(collection_domain) == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            level_text = "研究生" if "graduate" in (admissions_levels or []) else "招生"
            return f"{university_name}{level_text}招生公告真实数据源"
        if normalize_collection_domain(collection_domain) == COLLECTION_DOMAIN_SCHOOL_PROFILE:
            return f"{university_name}学校概况真实数据源"
        return f"{university_name}新闻中心真实数据源"

    def _candidate_precision_score(self, candidate: dict, *, collection_domain: str, query: str | None = None) -> int:
        score = 0
        source_kind = candidate.get("source_kind") or ""
        title = self.validator._clean_source_title(candidate.get("source_title"))
        source_url = candidate.get("source_url") or ""
        combined = f"{title} {source_url}"
        query_text = query or ""
        if normalize_collection_domain(collection_domain) == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            for token in ("通知", "公告", "动态", "简章", "复试", "调剂", "夏令营", "推免", "拟录取", "录取"):
                if token in combined:
                    score += 4
            if "notices" in source_url or "notice" in source_url:
                score += 5
            if "list" in source_url:
                score += 2
            if source_kind == "list_page":
                score += 4
            elif source_kind == "channel_page":
                score += 1
            if any(token in query_text for token in ("研究生", "硕士", "博士", "推免")):
                if "graduate" in (candidate.get("admissions_levels") or []) or any(
                    token in combined for token in ("研究生", "硕士", "博士", "graduate", "yz")
                ):
                    score += 8
                if any(token in combined for token in ("本科", "undergraduate")):
                    score -= 6
            if "本科" in query_text and any(token in combined for token in ("本科", "undergraduate")):
                score += 8
        score += int(float(candidate.get("confidence_score") or 0))
        return score

    def _sort_candidates_by_precision(self, candidates: list[dict], *, collection_domain: str, query: str | None = None) -> list[dict]:
        deduped = self._dedupe_candidate_dicts(candidates)
        return sorted(
            deduped,
            key=lambda item: self._candidate_precision_score(
                item,
                collection_domain=collection_domain,
                query=query,
            ),
            reverse=True,
        )

    def _dedupe_candidate_dicts(self, candidates: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            key = (
                (item.get("university_name") or "").strip(),
                (item.get("source_url") or "").strip(),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _pick_best_existing_source(
        self,
        candidates: list[Source],
        *,
        collection_domain: str,
        admissions_tracks: list[str] | None = None,
        candidate_type: str | None = None,
    ) -> Source | None:
        if not candidates:
            return None
        admissions_tracks = normalize_tracks(admissions_tracks)

        def score_source(source: Source) -> tuple[int, int]:
            source_track = (source.scope_json or {}).get("selected_track")
            source_type = (source.resolver_meta_json or {}).get("candidate_type")
            track_bonus = 5 if admissions_tracks and source_track in admissions_tracks else 0
            type_bonus = 3 if candidate_type and source_type == candidate_type else 0
            precision = self._candidate_precision_score(
                {
                    "source_kind": (source.config_json or {}).get("source_kind"),
                    "source_title": source.name,
                    "source_url": (source.start_urls_json or [source.base_url])[0],
                    "confidence_score": source.confidence_score or 0,
                    "admissions_levels": (source.scope_json or {}).get("admissions_levels") or [],
                },
                collection_domain=collection_domain,
            )
            return precision + track_bonus + type_bonus, source.id or 0

        ranked = sorted(
            candidates,
            key=score_source,
            reverse=True,
        )
        return ranked[0]

    def _needs_rediscovery(
        self,
        session: Session,
        *,
        university_name: str,
        collection_domain: str,
        admissions_levels: list[str] | None,
        admissions_tracks: list[str] | None,
    ) -> bool:
        any_existing = self.find_existing_source(
            session,
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            require_reusable=False,
        )
        reusable = self.find_existing_source(
            session,
            university_name=university_name,
            collection_domain=collection_domain,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            require_reusable=True,
        )
        return any_existing is not None and reusable is None

    def _normalize_admissions_levels(
        self,
        admissions_levels: list[str] | None,
        *,
        admissions_tracks: list[str] | None = None,
    ) -> list[str]:
        normalized = [item for item in (admissions_levels or []) if item in {"undergraduate", "graduate"}]
        for level in broad_levels_from_tracks(admissions_tracks):
            if level not in normalized:
                normalized.append(level)
        return normalized

    def _assert_c9_graduate_scope(
        self,
        *,
        university_name: str | None,
        collection_domain: str,
        admissions_levels: list[str] | None,
        admissions_tracks: list[str] | None,
        query: str | None = None,
        homepage_url: str | None = None,
    ) -> None:
        message = c9_scope_rejection_message(
            university_name=university_name,
            query=query,
            collection_domain=collection_domain,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            homepage_url=homepage_url,
        )
        if message:
            raise ValueError(message)
        if collection_domain != COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            raise ValueError("当前收缩版只支持 C9 高校校级研究生招生公告。")
        has_external_entrypoint = bool((homepage_url or "").strip())
        if not university_name and not mentions_c9_group(query):
            raise ValueError("请指定高校名称；非 C9 高校还需要提供官方首页或研究生招生入口 URL。")
        if university_name and not normalize_c9_university_name(university_name) and not has_external_entrypoint:
            raise ValueError("默认自动找源只支持 C9 高校校级研究生招生公告；非 C9 高校请提供官方首页或研究生招生入口 URL。")

    def _discover_c9_batch_sources(
        self,
        session: Session,
        *,
        query: str,
        desired_count: int,
        homepage_url: str | None,
    ) -> tuple[list[dict], dict]:
        results: list[dict] = []
        knowledge_hits = self.source_knowledge_retriever.search(
            session,
            query=query,
            collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            desired_count=desired_count,
            admissions_levels=["graduate"],
            admissions_tracks=["graduate"],
        ) if not homepage_url else []
        hit_by_university = {hit.university_name: hit for hit in knowledge_hits}
        debug = {
            "homepage_url": homepage_url,
            "candidate_provider": "c9_batch",
            "source_resolution_strategy": "live_discovery" if not knowledge_hits else "mixed",
            "source_knowledge_hits": [hit.to_debug_dict() for hit in knowledge_hits],
            "raw_candidate_count": len(C9_UNIVERSITIES),
            "raw_candidates": [{"university_name": name} for name in C9_UNIVERSITIES],
            "llm_selected_candidates": [],
            "llm_fallback_candidates": [],
            "cache_hit": False,
            "admissions_levels": ["graduate"],
            "admissions_tracks": ["graduate"],
            "bootstrap_strategy": "official_directory" if not homepage_url else "request_homepage",
            "entrypoint_url": homepage_url,
            "source_reuse_reason": None,
            "health_status": None,
            "rediscovery_triggered": False,
            "agent_trace": [
                {
                    "step": "c9_scope",
                    "status": "success",
                    "message": "识别为 C9 高校批量研究生招生公告数据源发现。",
                }
            ],
            "candidate_rankings": [],
            "failure_reason": None,
            "used_browser_explorer": False,
        }
        for university_name in C9_UNIVERSITIES[: max(1, min(desired_count, len(C9_UNIVERSITIES)))]:
            if university_name in hit_by_university:
                results.append(self._result_from_knowledge_hit(session, hit_by_university[university_name]))
                continue
            directory_entry = self.directory_service.get_entry(session, university_name)
            entrypoint_url = homepage_url or (
                self.directory_service.selected_entrypoint_url(directory_entry) if directory_entry is not None else None
            ) or (
                directory_entry.official_homepage_url if directory_entry is not None else None
            )
            if not entrypoint_url:
                results.append(
                    {
                        "source_id": None,
                        "saved": False,
                        "validation_status": "invalid",
                        "validation_message": "官方入口目录缺少站内入口。",
                        "university_name": university_name,
                        "collection_domain": COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
                        "admissions_levels": ["graduate"],
                        "admissions_tracks": ["graduate"],
                        "track": "graduate",
                        "candidate_type": None,
                        "source_suitability_score": 0.0,
                        "topic_relevance_score": 0.0,
                        "reject_reason_code": "entrypoint_missing",
                        "confidence_score": 0.0,
                        "reason": "官方入口目录缺少站内入口。",
                    }
                )
                continue
            try:
                agent_result = self.discovery_agent.discover_sources(
                    session,
                    query=f"提供{university_name}研究生招生公告的真实数据源",
                    university_name=university_name,
                    homepage_url=entrypoint_url,
                    homepage_source="official_directory" if not homepage_url else "request_homepage",
                    desired_count=1,
                    admissions_levels=["graduate"],
                    admissions_tracks=["graduate"],
                )
            except Exception as exc:
                results.append(
                    {
                        "source_id": None,
                        "saved": False,
                        "validation_status": "invalid",
                        "validation_message": str(exc),
                        "university_name": university_name,
                        "collection_domain": COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
                        "admissions_levels": ["graduate"],
                        "admissions_tracks": ["graduate"],
                        "track": "graduate",
                        "candidate_type": None,
                        "source_suitability_score": 0.0,
                        "topic_relevance_score": 0.0,
                        "reject_reason_code": "c9_batch_discovery_failed",
                        "confidence_score": 0.0,
                        "reason": str(exc),
                    }
                )
                continue
            results.extend(agent_result.resolved_sources[:1])
            debug["agent_trace"].extend(agent_result.agent_trace)
            debug["candidate_rankings"].extend(agent_result.candidate_rankings[:3])
        return results, debug

    def _extract_university_name_from_query(self, query: str) -> str | None:
        candidates = re.findall(r"([^\s，,。]{2,24}?(?:大学|学院))", query)
        if not candidates:
            return None
        cleaned: list[str] = []
        for item in candidates:
            value = item
            for prefix in ("请采集", "采集", "提供", "查询", "帮我采集", "帮我", "给我", "提供一下"):
                if value.startswith(prefix):
                    value = value[len(prefix):]
            cleaned.append(value)
        cleaned = [item for item in cleaned if item]
        return cleaned[-1] if cleaned else None
