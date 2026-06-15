from __future__ import annotations

from dataclasses import dataclass, field
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.source import Source
from app.services.c9_scope import (
    c9_scope_rejection_message,
    infer_c9_university_from_text,
    mentions_c9_group,
    normalize_c9_university_name,
    normalize_to_c9_graduate_scope,
)
from app.services.domains import (
    normalize_collection_domain,
)
from app.services.normalize.date_parser import resolve_time_range
from app.services.source_state import source_can_run

SUPPORTED_INTENTS = {
    "discover_sources",
    "crawl_source",
    "search_documents",
    "summarize_documents",
    "list_deadlines",
    "crawl_admissions",
    "search_admissions",
    "summarize_admissions",
}


@dataclass(slots=True)
class ValidationResult:
    is_valid: bool
    message: str | None = None
    matched_sources: list[Source] = field(default_factory=list)
    normalized_payload: dict = field(default_factory=dict)


class TaskValidator:
    def __init__(self) -> None:
        self.settings = get_settings()

    def validate(self, session: Session, parsed_payload: dict) -> ValidationResult:
        payload = {**parsed_payload}
        intent = payload.get("intent")
        if intent not in SUPPORTED_INTENTS:
            return ValidationResult(False, "当前只支持受控的高校官网采集、查询和总结意图。", [])

        collection_domain = normalize_collection_domain(payload.get("collection_domain"))
        payload["collection_domain"] = collection_domain
        payload.setdefault("filters", {})
        payload.setdefault("topic", [])
        payload.setdefault("notice_type", [])
        payload.setdefault("admissions_levels", [])
        payload.setdefault("admissions_tracks", [])
        payload.setdefault("result_limit", 10)
        homepage_url = payload.get("homepage_url") or payload.get("resolved_homepage_url")

        scope_message = c9_scope_rejection_message(
            university_name=payload.get("university_name") or payload.get("institution"),
            query=payload.get("raw_query"),
            collection_domain=collection_domain,
            admissions_levels=payload.get("admissions_levels"),
            admissions_tracks=payload.get("admissions_tracks"),
            homepage_url=homepage_url,
        )
        if scope_message:
            return ValidationResult(False, scope_message, [], payload)

        if collection_domain == "admissions_notice" and not (payload.get("university_name") or payload.get("institution")):
            inferred_university = infer_c9_university_from_text(payload.get("raw_query"))
            if inferred_university:
                payload["university_name"] = inferred_university
                payload["institution"] = inferred_university

        if intent in {"discover_sources", "crawl_source", "crawl_admissions"}:
            has_external_entrypoint = bool(homepage_url) and collection_domain == "admissions_notice"
            if not (
                normalize_c9_university_name(payload.get("university_name") or payload.get("institution"))
                or mentions_c9_group(payload.get("raw_query"))
                or has_external_entrypoint
            ):
                return ValidationResult(
                    False,
                    "默认自动找源只支持 C9 高校校级研究生招生公告；非 C9 高校请同时提供官方首页或研究生招生入口 URL。",
                    [],
                    payload,
                )
            payload = normalize_to_c9_graduate_scope(payload, query=payload.get("raw_query"))

        matched_sources = self.match_sources(
            session,
            institution=payload.get("institution") or payload.get("university_name"),
            department=payload.get("department"),
            collection_domain=collection_domain,
        )

        if intent in {"crawl_source", "crawl_admissions"} and not payload.get("university_name") and not payload.get("institution"):
            return ValidationResult(
                False,
                "采集任务至少需要指定一个高校名称。",
                [],
                payload,
            )
        if intent == "discover_sources":
            payload["result_limit"] = min(
                max(int(payload.get("result_limit", 10)), 1),
                self.settings.max_search_results,
            )
            return ValidationResult(True, None, matched_sources, payload)

        if intent in {"search_documents", "summarize_documents", "list_deadlines", "search_admissions", "summarize_admissions"} and not matched_sources:
            all_sources = self.match_sources(
                session,
                institution=None,
                department=None,
                collection_domain=collection_domain,
            )
            matched_sources = all_sources

        start, end = resolve_time_range(payload.get("time_range"))
        if start is not None and end is not None:
            delta_days = (end - start).days
            if intent in {"crawl_source", "crawl_admissions"} and delta_days > self.settings.max_nl_crawl_days:
                return ValidationResult(
                    False,
                    f"采集时间范围不能超过 {self.settings.max_nl_crawl_days} 天。",
                    matched_sources,
                    payload,
                )

        payload["result_limit"] = min(
            max(int(payload.get("result_limit", 10)), 1),
            self.settings.max_search_results,
        )
        payload["requires_source_discovery"] = bool(
            payload.get("requires_source_discovery")
            or (payload["intent"] == "crawl_source" and not matched_sources)
        )
        return ValidationResult(True, None, matched_sources, payload)

    def match_sources(
        self,
        session: Session,
        *,
        institution: str | None,
        department: str | None,
        collection_domain: str | None,
    ) -> list[Source]:
        sources = [
            source
            for source in session.scalars(select(Source).where(Source.status == "active"))
            if source_can_run(source) and normalize_collection_domain(source.collection_domain) == normalize_collection_domain(collection_domain)
        ]
        if not institution and not department:
            return sources
        matches: list[Source] = []
        normalized_institution = self._normalize_text(institution)
        normalized_department = self._normalize_text(department)
        for source in sources:
            institution_candidates = self._source_institution_candidates(source)
            department_candidates = self._source_department_candidates(source)
            if normalized_institution and normalized_institution not in institution_candidates:
                continue
            if normalized_department and normalized_department not in department_candidates:
                continue
            matches.append(source)
        return matches

    def _normalize_text(self, value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"[\s,，。；：:（）()【】\-\_]+", "", value).lower()

    def _source_institution_candidates(self, source: Source) -> set[str]:
        config = source.config_json or {}
        scope = source.scope_json or {}
        aliases = config.get("aliases") or []
        values = [
            source.organization_name,
            config.get("institution"),
            scope.get("university_name"),
            *aliases,
        ]
        return {
            normalized
            for normalized in (self._normalize_text(item) for item in values)
            if normalized
        }

    def _source_department_candidates(self, source: Source) -> set[str]:
        config = source.config_json or {}
        scope = source.scope_json or {}
        values = [
            config.get("department"),
            scope.get("department"),
        ]
        candidates = {
            normalized
            for normalized in (self._normalize_text(item) for item in values)
            if normalized
        }
        derived = self._derive_department_from_source_name(source)
        if derived:
            candidates.add(derived)
        return candidates

    def _derive_department_from_source_name(self, source: Source) -> str:
        institution_candidates = self._source_institution_candidates(source)
        source_name = self._strip_source_suffix((source.name or "").strip())
        normalized_source_name = self._normalize_text(source_name)
        for institution in institution_candidates:
            if normalized_source_name.startswith(institution):
                remainder = normalized_source_name[len(institution):]
                if remainder:
                    return remainder
        return ""

    def _strip_source_suffix(self, value: str) -> str:
        for suffix in ("学生事务通知", "通知公告", "通知信息", "招生信息", "招生网", "通知", "公告", "列表", "数据源"):
            if value.endswith(suffix) and len(value) > len(suffix):
                return value[: -len(suffix)].strip()
        return value
