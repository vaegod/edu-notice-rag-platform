from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.admissions_tracks import broad_levels_from_tracks, infer_tracks_from_text, normalize_tracks, primary_track
from app.services.c9_scope import C9_UNIVERSITIES
from app.services.domains import normalize_collection_domain
from app.services.llm.siliconflow_client import SiliconFlowClient, load_prompt_template


class ResolvedSourceCandidate(BaseModel):
    university_name: str
    collection_domain: str = "admissions_notice"
    homepage_url: str
    source_url: str
    source_title: str
    source_kind: str = "list_page"
    admissions_levels: list[str] = Field(default_factory=list)
    admissions_tracks: list[str] = Field(default_factory=list)
    candidate_track: str | None = None
    candidate_type: str | None = None
    confidence_score: float = 0.0
    source_suitability_score: float = 0.0
    topic_relevance_score: float = 0.0
    reason: str = ""
    reject_reason_code: str | None = None
    request_method: str = "GET"
    list_page_mode: str | None = None
    pagination_hint: str | None = None
    detail_link_hint: str | None = None
    request_data: dict[str, Any] = Field(default_factory=dict)
    request_json: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict)
    crawl_mode: str = "dynamic"
    source_origin: str = "llm_resolved"

    def normalized(self) -> ResolvedSourceCandidate:
        payload = self.model_dump()
        payload["collection_domain"] = normalize_collection_domain(payload.get("collection_domain"))
        payload["admissions_tracks"] = normalize_tracks(payload.get("admissions_tracks", []))
        payload["candidate_track"] = payload.get("candidate_track") or primary_track(payload.get("admissions_tracks", []))
        payload["admissions_levels"] = [
            item
            for item in payload.get("admissions_levels", [])
            if item in {"undergraduate", "graduate"}
        ]
        for level in broad_levels_from_tracks(payload.get("admissions_tracks", [])):
            if level not in payload["admissions_levels"]:
                payload["admissions_levels"].append(level)
        payload["request_method"] = (payload.get("request_method") or "GET").upper()
        payload["crawl_mode"] = payload.get("crawl_mode") or "dynamic"
        payload["source_origin"] = "llm_resolved"
        return ResolvedSourceCandidate.model_validate(payload)


class LooseResolvedSourceCandidate(BaseModel):
    university_name: str | None = None
    collection_domain: str | None = "admissions_notice"
    homepage_url: str | None = None
    source_url: str | None = None
    source_title: str | None = None
    source_kind: str | None = "list_page"
    admissions_levels: list[str] = Field(default_factory=list)
    admissions_tracks: list[str] = Field(default_factory=list)
    candidate_track: str | None = None
    candidate_type: str | None = None
    confidence_score: float | None = 0.0
    source_suitability_score: float | None = 0.0
    topic_relevance_score: float | None = 0.0
    reason: str | None = None
    reject_reason_code: str | None = None
    request_method: str | None = "GET"
    list_page_mode: str | None = None
    pagination_hint: str | None = None
    detail_link_hint: str | None = None
    request_data: dict[str, Any] = Field(default_factory=dict)
    request_json: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict)
    crawl_mode: str | None = "dynamic"

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "university_name": self.university_name,
            "collection_domain": normalize_collection_domain(self.collection_domain),
            "homepage_url": self.homepage_url,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "source_kind": self.source_kind or "list_page",
            "admissions_levels": [item for item in self.admissions_levels if item in {"undergraduate", "graduate"}],
            "admissions_tracks": normalize_tracks(self.admissions_tracks),
            "candidate_track": self.candidate_track or primary_track(self.admissions_tracks),
            "candidate_type": self.candidate_type,
            "confidence_score": float(self.confidence_score or 0.0),
            "source_suitability_score": float(self.source_suitability_score or 0.0),
            "topic_relevance_score": float(self.topic_relevance_score or 0.0),
            "reason": self.reason,
            "reject_reason_code": self.reject_reason_code,
            "request_method": (self.request_method or "GET").upper(),
            "list_page_mode": self.list_page_mode,
            "pagination_hint": self.pagination_hint,
            "detail_link_hint": self.detail_link_hint,
            "request_data": self.request_data or {},
            "request_json": self.request_json or {},
            "request_headers": self.request_headers or {},
            "crawl_mode": self.crawl_mode or "dynamic",
        }


class LLMSourceResolverService:
    def __init__(self, llm_client: SiliconFlowClient | None = None) -> None:
        self.settings = get_settings()
        self.llm_client = llm_client or SiliconFlowClient()
        self.prompt_template = load_prompt_template("source_resolve.txt")
        self.rank_prompt_template = load_prompt_template("source_rank.txt")

    def resolve(
        self,
        *,
        university_name: str,
        collection_domain: str,
        homepage_url: str | None = None,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        session: Session | None = None,
    ) -> ResolvedSourceCandidate:
        payload = self.resolve_loose(
            university_name=university_name,
            collection_domain=collection_domain,
            homepage_url=homepage_url,
            admissions_levels=admissions_levels,
            admissions_tracks=admissions_tracks,
            session=session,
        )
        return self.strict_candidate_from_payload(
            payload,
            default_university_name=university_name,
            default_collection_domain=collection_domain,
            default_homepage_url=homepage_url,
            default_admissions_levels=admissions_levels,
            default_admissions_tracks=admissions_tracks,
        )

    def resolve_loose(
        self,
        *,
        university_name: str,
        collection_domain: str,
        homepage_url: str | None = None,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        collection_domain = normalize_collection_domain(collection_domain)
        admissions_levels = list(admissions_levels or [])
        admissions_tracks = normalize_tracks(admissions_tracks)
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._mock_resolve(
                university_name=university_name,
                collection_domain=collection_domain,
                homepage_url=homepage_url,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            ).model_dump()

        user_prompt = (
            f"高校名称: {university_name}\n"
            f"采集主题: {collection_domain}\n"
            f"招生层级: {admissions_levels}\n"
            f"已知官网首页: {homepage_url or ''}\n"
            "请只返回一个最可信的官方公开数据源 JSON。"
        )
        payload = self.llm_client.chat_json(
            system_prompt=self.prompt_template,
            user_prompt=user_prompt,
            biz_type="source_resolve",
            session=session,
        )
        return self.coerce_candidate_payload(
            payload if isinstance(payload, dict) else {},
            default_university_name=university_name,
            default_collection_domain=collection_domain,
            default_homepage_url=homepage_url,
            default_admissions_levels=admissions_levels,
            default_admissions_tracks=admissions_tracks,
        )

    def resolve_homepage(
        self,
        *,
        university_name: str,
        session: Session | None = None,
    ) -> dict[str, Any]:
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._mock_resolve_homepage(university_name=university_name)

        system_prompt = (
            "你是一个高校官网首页解析助手。\n"
            "请根据高校名称返回最可信的官方首页 JSON。\n"
            "只输出 JSON，不要解释。\n"
            "字段至少包含：university_name、homepage_url、confidence_score、reason、candidates。"
        )
        user_prompt = f"高校名称: {university_name}\n请返回官方首页。"
        try:
            payload = self.llm_client.chat_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                biz_type="source_resolve",
                session=session,
            )
        except Exception as exc:
            return {
                "university_name": university_name,
                "homepage_url": None,
                "confidence_score": 0.0,
                "reason": f"LLM 首页解析失败：{exc}",
                "candidates": [],
            }
        homepage_url = None
        if isinstance(payload, dict):
            homepage_url = payload.get("homepage_url") or payload.get("resolved_homepage_url")
            if not homepage_url and isinstance(payload.get("source_url"), str):
                homepage_url = self._homepage_from_url(payload["source_url"])
        return {
            "university_name": university_name,
            "homepage_url": homepage_url,
            "confidence_score": self._coerce_numeric_score((payload or {}).get("confidence_score")),
            "reason": (payload or {}).get("reason") or "",
            "candidates": (payload or {}).get("candidates") if isinstance((payload or {}).get("candidates"), list) else [],
        }

    def rank_candidates(
        self,
        *,
        query: str,
        collection_domain: str,
        homepage_url: str,
        candidates: list[dict[str, Any]],
        desired_count: int = 5,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        collection_domain = normalize_collection_domain(collection_domain)
        admissions_levels = [item for item in (admissions_levels or []) if item in {"undergraduate", "graduate"}]
        admissions_tracks = normalize_tracks(admissions_tracks)
        if not candidates:
            return {"rankings": [], "failure_reason": "no_candidates"}
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._mock_rank_candidates(
                candidates=candidates,
                desired_count=desired_count,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )

        compact_candidates = [
            {
                "source_url": item.get("source_url"),
                "source_title": item.get("source_title"),
                "source_kind": item.get("source_kind"),
                "crawl_mode": item.get("crawl_mode"),
                "admissions_levels": item.get("admissions_levels") or [],
                "heuristic_score": item.get("heuristic_score", 0),
                "features": item.get("features") or {},
                "reason": item.get("reason") or "",
            }
            for item in candidates[:20]
        ]
        user_prompt = (
            f"用户请求: {query}\n"
            f"采集主题: {collection_domain}\n"
            f"官网首页: {homepage_url}\n"
            f"期望数量: {desired_count}\n"
            f"招生层级: {admissions_levels}\n"
            f"招生轨道: {admissions_tracks}\n"
            f"候选 JSON:\n{compact_candidates}\n"
            '请返回 JSON：{"rankings":[{"source_url":"","score":0.0,"recommended_action":"validate","reason":""}],"failure_reason":null}'
        )
        try:
            payload = self.llm_client.chat_json(
                system_prompt=self.rank_prompt_template,
                user_prompt=user_prompt,
                biz_type="source_resolve",
                session=session,
            )
        except Exception:
            return self._mock_rank_candidates(
                candidates=candidates,
                desired_count=desired_count,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )
        rankings = payload.get("rankings") if isinstance(payload, dict) else None
        if not isinstance(rankings, list):
            return self._mock_rank_candidates(
                candidates=candidates,
                desired_count=desired_count,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )
        candidate_map = {
            str(item.get("source_url")): item
            for item in candidates
            if item.get("source_url")
        }
        normalized_rankings: list[dict[str, Any]] = []
        for item in rankings:
            if not isinstance(item, dict):
                continue
            source_url = item.get("source_url")
            if not isinstance(source_url, str) or source_url not in candidate_map:
                continue
            candidate = candidate_map[source_url]
            normalized_rankings.append(
                {
                    "source_url": source_url,
                    "source_title": candidate.get("source_title"),
                    "source_kind": candidate.get("source_kind"),
                    "score": self._coerce_numeric_score(item.get("score"), fallback=float(candidate.get("heuristic_score") or 0.0)),
                    "recommended_action": self._normalize_rank_action(item.get("recommended_action")),
                    "reason": item.get("reason") or candidate.get("reason") or "",
                }
            )
            if len(normalized_rankings) >= desired_count:
                break
        if not normalized_rankings:
            return self._mock_rank_candidates(
                candidates=candidates,
                desired_count=desired_count,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )
        return {
            "rankings": normalized_rankings,
            "failure_reason": payload.get("failure_reason") if isinstance(payload, dict) else None,
        }

    def coerce_candidate_payload(
        self,
        raw: dict[str, Any],
        *,
        default_university_name: str | None = None,
        default_collection_domain: str | None = None,
        default_homepage_url: str | None = None,
        default_admissions_levels: list[str] | None = None,
        default_admissions_tracks: list[str] | None = None,
    ) -> dict[str, Any]:
        candidate = LooseResolvedSourceCandidate.model_validate(raw or {})
        payload = candidate.to_display_dict()
        payload["university_name"] = payload.get("university_name") or default_university_name
        payload["collection_domain"] = normalize_collection_domain(
            payload.get("collection_domain") or default_collection_domain
        )
        payload["homepage_url"] = payload.get("homepage_url") or default_homepage_url or self._homepage_from_url(payload.get("source_url"))
        payload["source_title"] = payload.get("source_title") or payload.get("source_url")
        payload["source_kind"] = payload.get("source_kind") or "list_page"
        payload["request_method"] = (payload.get("request_method") or "GET").upper()
        payload["admissions_tracks"] = normalize_tracks(
            payload.get("admissions_tracks") or default_admissions_tracks or []
        )
        payload["candidate_track"] = payload.get("candidate_track") or primary_track(payload.get("admissions_tracks"))
        payload["candidate_type"] = payload.get("candidate_type") or payload.get("source_kind") or "list_page"
        payload["admissions_levels"] = [
            item
            for item in (payload.get("admissions_levels") or default_admissions_levels or [])
            if item in {"undergraduate", "graduate"}
        ]
        for level in broad_levels_from_tracks(payload.get("admissions_tracks")):
            if level not in payload["admissions_levels"]:
                payload["admissions_levels"].append(level)
        return payload

    def strict_candidate_from_payload(
        self,
        raw: dict[str, Any],
        *,
        default_university_name: str | None = None,
        default_collection_domain: str | None = None,
        default_homepage_url: str | None = None,
        default_admissions_levels: list[str] | None = None,
        default_admissions_tracks: list[str] | None = None,
    ) -> ResolvedSourceCandidate:
        payload = self.coerce_candidate_payload(
            raw,
            default_university_name=default_university_name,
            default_collection_domain=default_collection_domain,
            default_homepage_url=default_homepage_url,
            default_admissions_levels=default_admissions_levels,
            default_admissions_tracks=default_admissions_tracks,
        )
        return ResolvedSourceCandidate.model_validate(payload).normalized()

    def choose_from_candidates(
        self,
        *,
        query: str,
        collection_domain: str,
        homepage_url: str,
        candidates: list[dict[str, Any]],
        desired_count: int = 10,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        session: Session | None = None,
    ) -> list[dict[str, Any]]:
        collection_domain = normalize_collection_domain(collection_domain)
        desired_count = max(1, desired_count)
        admissions_levels = [item for item in (admissions_levels or []) if item in {"undergraduate", "graduate"}]
        admissions_tracks = normalize_tracks(admissions_tracks)
        if not candidates:
            return []
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._mock_choose_from_candidates(
                collection_domain=collection_domain,
                homepage_url=homepage_url,
                candidates=candidates,
                desired_count=desired_count,
                admissions_levels=admissions_levels,
                admissions_tracks=admissions_tracks,
            )

        candidate_text = "\n".join(
            f"{idx+1}. title={item.get('title')} url={item.get('url')} snippet={item.get('snippet')}"
            for idx, item in enumerate(candidates[:30])
        )
        user_prompt = (
            f"用户请求: {query}\n"
            f"采集主题: {collection_domain}\n"
            f"官网首页: {homepage_url}\n"
            f"期望数量: {desired_count}\n"
            f"招生层级: {admissions_levels}\n"
            f"招生轨道: {admissions_tracks}\n"
            "候选链接如下，请只从候选中选择，不要虚构新链接：\n"
            f"{candidate_text}\n"
            '请返回 JSON：{"sources":[...]}'
        )
        try:
            try:
                payload = self.llm_client.chat_json(
                    system_prompt=self.prompt_template,
                    user_prompt=user_prompt,
                    biz_type="source_resolve",
                    session=session,
                )
            except Exception:
                return []
        except Exception:
            return []
        sources = payload.get("sources") if isinstance(payload, dict) else None
        if not isinstance(sources, list):
            return []
        results: list[dict[str, Any]] = []
        for item in sources[:desired_count]:
            if not isinstance(item, dict):
                continue
            try:
                results.append(LooseResolvedSourceCandidate.model_validate(item).to_display_dict())
            except Exception:
                continue
        return results

    def resolve_many(
        self,
        *,
        query: str,
        collection_domain: str,
        desired_count: int = 10,
        session: Session | None = None,
    ) -> list[ResolvedSourceCandidate]:
        collection_domain = normalize_collection_domain(collection_domain)
        desired_count = max(1, desired_count)
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._mock_resolve_many(
                collection_domain=collection_domain,
                desired_count=desired_count,
            )

        user_prompt = (
            f"用户请求: {query}\n"
            f"采集主题: {collection_domain}\n"
            f"期望数量: {desired_count}\n"
            "请返回一个 JSON，包含 sources 数组；每个元素都是一个最可信的官方公开数据源。"
        )
        payload = self.llm_client.chat_json(
            system_prompt=self.prompt_template,
            user_prompt=user_prompt,
            biz_type="source_resolve",
            session=session,
        )
        sources = payload.get("sources") if isinstance(payload, dict) else None
        if not isinstance(sources, list):
            return []
        normalized_items: list[ResolvedSourceCandidate] = []
        for item in sources[: max(desired_count * 2, desired_count)]:
            if not isinstance(item, dict):
                continue
            try:
                normalized_items.append(
                    ResolvedSourceCandidate.model_validate(item).normalized()
                )
            except Exception:
                continue
        return self._dedupe_candidates(normalized_items, desired_count=desired_count)

    def resolve_many_loose(
        self,
        *,
        query: str,
        collection_domain: str,
        desired_count: int = 10,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
        session: Session | None = None,
    ) -> list[dict[str, Any]]:
        collection_domain = normalize_collection_domain(collection_domain)
        desired_count = max(1, desired_count)
        admissions_levels = [item for item in (admissions_levels or []) if item in {"undergraduate", "graduate"}]
        admissions_tracks = normalize_tracks(admissions_tracks)
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return [item.model_dump() for item in self._mock_resolve_many(
                collection_domain=collection_domain,
                desired_count=desired_count,
            )]

        items: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        excluded_universities: list[str] = []
        batch_size = 5
        rounds = max(1, (desired_count + batch_size - 1) // batch_size)
        for _ in range(rounds):
            remaining = desired_count - len(items)
            if remaining <= 0:
                break
            user_prompt = (
                f"用户请求: {query}\n"
                f"采集主题: {collection_domain}\n"
                f"本轮期望数量: {min(batch_size, remaining)}\n"
                f"招生层级: {admissions_levels}\n"
                f"招生轨道: {admissions_tracks}\n"
                f"已返回高校: {excluded_universities}\n"
                "请返回一个 JSON，包含 sources 数组；每个元素都是一个最可信的官方公开数据源。"
            )
            payload = self.llm_client.chat_json(
                system_prompt=self.prompt_template,
                user_prompt=user_prompt,
                biz_type="source_resolve",
                session=session,
            )
            sources = payload.get("sources") if isinstance(payload, dict) else None
            if not isinstance(sources, list):
                continue
            for raw in sources[: max(batch_size * 3, batch_size)]:
                if not isinstance(raw, dict):
                    continue
                try:
                    item = LooseResolvedSourceCandidate.model_validate(raw).to_display_dict()
                except Exception:
                    continue
                if not item.get("source_url"):
                    continue
                key = ((item.get("university_name") or "").strip(), item["source_url"].strip())
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                if item.get("university_name"):
                    excluded_universities.append(item["university_name"])
                items.append(item)
                if len(items) >= desired_count:
                    break
        return items

    def _mock_resolve(
        self,
        *,
        university_name: str,
        collection_domain: str,
        homepage_url: str | None,
        admissions_levels: list[str],
        admissions_tracks: list[str] | None = None,
    ) -> ResolvedSourceCandidate:
        homepage = homepage_url or "https://example.edu.cn/"
        base = homepage.rstrip("/")
        if not urlparse(homepage).path or urlparse(homepage).path == "/":
            homepage = f"{base}/"
        if collection_domain == "school_profile":
            source_url = f"{base}/overview.html"
            title = f"{university_name}学校概况"
            source_kind = "profile_page"
            reason = "Mock 模式下返回学校概况单页。"
        elif collection_domain == "news_center":
            source_url = f"{base}/news/list.html"
            title = f"{university_name}新闻中心"
            source_kind = "channel_page"
            reason = "Mock 模式下返回新闻中心栏目页。"
        else:
            source_url = f"{base}/graduate/notices.html"
            title = f"{university_name}研究生招生公告"
            source_kind = "list_page"
            reason = "Mock 模式下返回 C9 研究生招生公告列表页。"
        return ResolvedSourceCandidate(
            university_name=university_name,
            collection_domain=collection_domain,
            homepage_url=homepage,
            source_url=source_url,
            source_title=title,
            source_kind=source_kind,
            admissions_levels=["graduate"] if collection_domain == "admissions_notice" else admissions_levels,
            admissions_tracks=["graduate"] if collection_domain == "admissions_notice" else normalize_tracks(admissions_tracks),
            candidate_track="graduate" if collection_domain == "admissions_notice" else primary_track(admissions_tracks),
            candidate_type=source_kind,
            confidence_score=0.91,
            source_suitability_score=0.91,
            topic_relevance_score=0.91,
            reason=reason,
        ).normalized()

    def _mock_resolve_many(
        self,
        *,
        collection_domain: str,
        desired_count: int,
    ) -> list[ResolvedSourceCandidate]:
        universities = C9_UNIVERSITIES
        results: list[ResolvedSourceCandidate] = []
        for name in universities[:desired_count]:
            if collection_domain == "school_profile":
                source_url = "https://example.edu.cn/overview.html"
                title = f"{name}学校概况"
                source_kind = "profile_page"
            elif collection_domain == "news_center":
                source_url = "https://example.edu.cn/news/list.html"
                title = f"{name}新闻中心"
                source_kind = "channel_page"
            else:
                source_url = "https://example.edu.cn/graduate/notices.html"
                title = f"{name}研究生招生公告"
                source_kind = "list_page"
            results.append(
                ResolvedSourceCandidate(
                    university_name=name,
                    collection_domain=collection_domain,
                    homepage_url="https://example.edu.cn/",
                    source_url=source_url,
                    source_title=title,
                    source_kind=source_kind,
                    admissions_levels=["graduate"] if collection_domain == "admissions_notice" else [],
                    admissions_tracks=["graduate"] if collection_domain == "admissions_notice" else [],
                    candidate_track="graduate" if collection_domain == "admissions_notice" else None,
                    confidence_score=0.9,
                    reason="Mock 模式下返回批量官方数据源。",
                ).normalized()
            )
        return results

    def _mock_resolve_homepage(self, *, university_name: str) -> dict[str, Any]:
        return {
            "university_name": university_name,
            "homepage_url": "https://example.edu.cn/",
            "confidence_score": 0.9,
            "reason": "Mock 模式下返回官网首页。",
            "candidates": [
                {
                    "url": "https://example.edu.cn/",
                    "title": f"{university_name}官网首页",
                }
            ],
        }

    def _mock_rank_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        desired_count: int,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
    ) -> dict[str, Any]:
        rankings = []
        wanted_levels = set(admissions_levels or [])
        for item in sorted(candidates, key=lambda value: float(value.get("heuristic_score") or 0.0), reverse=True):
            item_levels = set(item.get("admissions_levels") or [])
            recommended_action = "validate"
            if item.get("source_kind") == "channel_page":
                recommended_action = "expand"
            elif wanted_levels and not wanted_levels.intersection(item_levels) and item.get("source_kind") != "list_page":
                recommended_action = "narrow_to_graduate"
            rankings.append(
                {
                    "source_url": item.get("source_url"),
                    "source_title": item.get("source_title"),
                    "source_kind": item.get("source_kind"),
                    "score": float(item.get("heuristic_score") or 0.0),
                    "recommended_action": recommended_action,
                    "reason": item.get("reason") or "规则打分结果。",
                }
            )
            if len(rankings) >= desired_count:
                break
        return {"rankings": rankings, "failure_reason": None if rankings else "no_rankings"}

    def _homepage_from_url(self, value: str | None) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = urlparse(value.strip())
        if not parsed.scheme or not parsed.netloc:
            return None
        return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))

    def _normalize_rank_action(self, value: Any) -> str:
        if not isinstance(value, str):
            return "validate"
        normalized = value.strip().lower()
        if normalized in {"validate", "expand", "switch_to_dynamic", "next_candidate", "narrow_to_graduate"}:
            return normalized
        return "validate"

    def _coerce_numeric_score(self, value: Any, *, fallback: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _mock_choose_from_candidates(
        self,
        *,
        collection_domain: str,
        homepage_url: str,
        candidates: list[dict[str, Any]],
        desired_count: int,
        admissions_levels: list[str] | None = None,
        admissions_tracks: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        keywords_map = {
            "school_profile": ("概况", "简介", "介绍", "overview"),
            "news_center": ("新闻", "新闻网", "news"),
            "admissions_notice": ("招生", "研究生", "本科", "硕士", "博士", "admission", "graduate", "undergraduate"),
        }
        matched: list[dict[str, Any]] = []
        keywords = keywords_map[collection_domain]
        requested_levels = set(admissions_levels or [])
        mock_university_name = self._mock_university_name_from_homepage(homepage_url)
        for item in candidates:
            title = item.get("title", "") or ""
            url = item.get("url", "") or ""
            snippet = item.get("snippet", "") or ""
            haystack = f"{title} {url} {snippet}".lower()
            if any(token.lower() in haystack for token in keywords):
                candidate_levels: list[str] = []
                text_haystack = f"{title} {snippet}".lower()
                url_haystack = url.lower()
                if "本科" in text_haystack or re.search(r"(?:^|[/._-])undergraduate(?:[/._?-]|$)", url_haystack):
                    candidate_levels.append("undergraduate")
                if (
                    any(token in text_haystack for token in ("研究生", "硕士", "博士"))
                    or re.search(r"(?:^|[/._-])(graduate|master|doctor|yz)(?:[/._?-]|$)", url_haystack)
                ):
                    candidate_levels.append("graduate")
                if requested_levels and set(candidate_levels) and not requested_levels.intersection(candidate_levels):
                    continue
                matched.append(
                    {
                        "university_name": mock_university_name,
                        "collection_domain": collection_domain,
                        "homepage_url": homepage_url,
                        "source_url": item.get("url"),
                        "source_title": item.get("title") or item.get("url"),
                        "source_kind": self._mock_source_kind_for_candidate(
                            collection_domain=collection_domain,
                            url=url,
                            title=title,
                        ),
                        "admissions_levels": candidate_levels,
                        "admissions_tracks": normalize_tracks(infer_tracks_from_text(f"{title} {snippet} {url}")),
                        "confidence_score": 0.75,
                        "reason": "根据官网首页候选链接匹配得到。",
                        "request_method": "GET",
                    }
                )
        return matched[:desired_count]

    def _mock_university_name_from_homepage(self, homepage_url: str) -> str | None:
        lowered = homepage_url.lower()
        if "example.edu.cn" in lowered:
            return "清华大学"
        return None

    def _mock_source_kind_for_candidate(self, *, collection_domain: str, url: str, title: str) -> str:
        if collection_domain == "school_profile":
            return "profile_page"
        lowered = f"{title} {url}".lower()
        if url.endswith("/") and not any(token in lowered for token in ("notice", "list", "公告", "通知", "动态")):
            return "channel_page"
        return "list_page"

    def _dedupe_candidates(
        self,
        candidates: list[ResolvedSourceCandidate],
        *,
        desired_count: int,
    ) -> list[ResolvedSourceCandidate]:
        deduped: list[ResolvedSourceCandidate] = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in candidates:
            key = (item.university_name.strip(), item.source_url.strip())
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            deduped.append(item)
            if len(deduped) >= desired_count:
                break
        return deduped
