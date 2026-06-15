from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.source import Source
from app.models.university_directory import UniversityDirectory
from app.services.c9_scope import C9_GRADUATE_ADMISSIONS_HOMEPAGES, C9_UNIVERSITIES
from app.services.onboarding_service import SourceOnboardingService


C9_OFFICIAL_HOMEPAGES = {
    "北京大学": "https://www.pku.edu.cn/",
    "清华大学": "https://www.tsinghua.edu.cn/",
    "复旦大学": "https://www.fudan.edu.cn/",
    "上海交通大学": "https://www.sjtu.edu.cn/",
    "浙江大学": "https://www.zju.edu.cn/",
    "南京大学": "https://www.nju.edu.cn/",
    "中国科学技术大学": "https://www.ustc.edu.cn/",
    "哈尔滨工业大学": "https://www.hit.edu.cn/",
    "西安交通大学": "https://www.xjtu.edu.cn/",
}


def _default_entrypoint_candidates(university_name: str) -> list[str]:
    primary = C9_GRADUATE_ADMISSIONS_HOMEPAGES[university_name]
    if university_name == "浙江大学":
        return [
            "https://www.grs.zju.edu.cn/yjszs/",
            "http://www.grs.zju.edu.cn/yjszs/",
        ]
    return [primary]


DEFAULT_C9_SOURCE_KNOWLEDGE = {
    "version": 1,
    "generated_at": None,
    "scope": "graduate_admissions",
    "universities": [
        {
            "university_name": university_name,
            "normalized_name": university_name,
            "scope": "graduate_admissions",
            "official_homepage_url": C9_OFFICIAL_HOMEPAGES[university_name],
            "entrypoint_candidates": _default_entrypoint_candidates(university_name),
            "selected_entrypoint_url": None,
            "selected_source_url": None,
            "source_title": None,
            "source_kind": None,
            "candidate_type": None,
            "health_status": "stale",
            "confidence_score": 0.0,
            "checked_at": None,
            "validation_evidence": {},
            "fallback_notes": [],
        }
        for university_name in C9_UNIVERSITIES
    ],
}


class C9SourceKnowledgeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.onboarding_service = SourceOnboardingService()

    @property
    def knowledge_path(self) -> Path:
        return Path(self.settings.c9_source_knowledge_path)

    def load_payload(self) -> dict[str, Any]:
        if not self.knowledge_path.exists():
            payload = json.loads(json.dumps(DEFAULT_C9_SOURCE_KNOWLEDGE, ensure_ascii=False))
            self.save_payload(payload)
            return payload
        raw = json.loads(self.knowledge_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("C9 source knowledge payload must be a JSON object.")
        payload = {
            "version": raw.get("version") or 1,
            "generated_at": raw.get("generated_at"),
            "scope": raw.get("scope") or "graduate_admissions",
            "universities": [],
        }
        for item in raw.get("universities") or []:
            if not isinstance(item, dict):
                continue
            normalized = {
                "university_name": item.get("university_name"),
                "normalized_name": item.get("normalized_name") or item.get("university_name"),
                "scope": item.get("scope") or "graduate_admissions",
                "official_homepage_url": item.get("official_homepage_url"),
                "entrypoint_candidates": list(item.get("entrypoint_candidates") or []),
                "selected_entrypoint_url": item.get("selected_entrypoint_url"),
                "selected_source_url": item.get("selected_source_url"),
                "source_title": item.get("source_title"),
                "source_kind": item.get("source_kind"),
                "candidate_type": item.get("candidate_type"),
                "health_status": item.get("health_status") or "stale",
                "confidence_score": float(item.get("confidence_score") or 0.0),
                "checked_at": item.get("checked_at"),
                "validation_evidence": item.get("validation_evidence") or {},
                "fallback_notes": list(item.get("fallback_notes") or []),
            }
            if normalized["university_name"]:
                payload["universities"].append(normalized)
        return payload

    def save_payload(self, payload: dict[str, Any]) -> None:
        self.knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def sync_from_payload(self, session: Session, payload: dict[str, Any], *, sync_sources: bool = True) -> dict[str, int]:
        universities = payload.get("universities") or []
        directory_created = 0
        directory_updated = 0
        source_created = 0
        source_updated = 0
        active_universities = set()
        for item in universities:
            if not isinstance(item, dict):
                continue
            university_name = item.get("university_name")
            if not university_name:
                continue
            active_universities.add(university_name)
            entrypoint_candidates = list(item.get("entrypoint_candidates") or [])
            selected_entrypoint_url = item.get("selected_entrypoint_url") or (entrypoint_candidates[0] if entrypoint_candidates else None)
            directory = session.scalar(
                select(UniversityDirectory).where(UniversityDirectory.normalized_name == item.get("normalized_name"))
            )
            values = {
                "university_name": university_name,
                "normalized_name": item.get("normalized_name") or university_name,
                "official_homepage_url": item.get("official_homepage_url") or selected_entrypoint_url or "",
                "admissions_entry_url": selected_entrypoint_url or (entrypoint_candidates[0] if entrypoint_candidates else (item.get("official_homepage_url") or "")),
                "entrypoint_candidates_json": entrypoint_candidates,
                "selected_entrypoint_url": selected_entrypoint_url,
                "scope": item.get("scope") or "graduate_admissions",
                "status": "active",
                "last_checked_at": self._parse_iso_datetime(item.get("checked_at")) or utc_now(),
            }
            if directory is None:
                directory = UniversityDirectory(**values)
                session.add(directory)
                directory_created += 1
            else:
                for key, value in values.items():
                    setattr(directory, key, value)
                directory_updated += 1

            if sync_sources:
                preexisting_source = None
                if item.get("selected_source_url"):
                    preexisting_source = self._get_source_by_url(
                        session,
                        university_name=university_name,
                        source_url=item["selected_source_url"],
                    )
                self._sync_university_source(
                    session,
                    item=item,
                    selected_entrypoint_url=selected_entrypoint_url,
                )
                if item.get("selected_source_url") and item.get("health_status") == "healthy":
                    if preexisting_source is None:
                        source_created += 1
                    else:
                        source_updated += 1

        if sync_sources:
            self._mark_stale_sources(session, active_universities, universities)
        session.commit()
        return {
            "directory_created": directory_created,
            "directory_updated": directory_updated,
            "source_created": source_created,
            "source_updated": source_updated,
        }

    def sync_from_file(self, session: Session, *, sync_sources: bool = True) -> dict[str, int]:
        return self.sync_from_payload(session, self.load_payload(), sync_sources=sync_sources)

    def _sync_university_source(
        self,
        session: Session,
        *,
        item: dict[str, Any],
        selected_entrypoint_url: str | None,
    ) -> None:
        university_name = item["university_name"]
        selected_source_url = item.get("selected_source_url")
        health_status = item.get("health_status") or "stale"
        existing_sources = list(
            session.scalars(
                select(Source).where(
                    Source.organization_name == university_name,
                    Source.collection_domain == "admissions_notice",
                )
            )
        )
        if not selected_source_url:
            for source in existing_sources:
                source.health_status = "stale"
                source.last_failure_reason = "知识库当前未提供 healthy 主 source。"
            return

        snapshot = ((item.get("validation_evidence") or {}).get("normalized_source") or {})
        payload = dict(snapshot) if isinstance(snapshot, dict) else {}
        if not payload:
            payload = self._build_fallback_source_payload(
                item=item,
                selected_entrypoint_url=selected_entrypoint_url,
            )
        payload["source_origin"] = "seed"
        payload["confidence_score"] = float(item.get("confidence_score") or payload.get("confidence_score") or 0.0)
        payload["entrypoint_url"] = selected_entrypoint_url
        payload["health_status"] = health_status
        payload["validation_evidence"] = item.get("validation_evidence") or {}
        checked_at = self._parse_iso_datetime(item.get("checked_at")) or utc_now()
        payload["last_discovered_at"] = checked_at
        payload["last_validated_at"] = checked_at
        payload["last_failure_reason"] = None if health_status == "healthy" else "知识库标记为非 healthy。"
        payload["scope_json"] = payload.get("scope_json") or {
            "university_name": university_name,
            "admissions_levels": ["graduate"],
            "admissions_tracks": ["graduate"],
            "selected_track": "graduate",
        }
        payload["resolver_meta_json"] = {
            **(payload.get("resolver_meta_json") or {}),
            "candidate_type": item.get("candidate_type") or ((payload.get("resolver_meta_json") or {}).get("candidate_type")),
            "source_resolution_strategy": "knowledge_sync",
            "selected_entrypoint_url": selected_entrypoint_url,
        }
        payload = self.onboarding_service.normalize_source_dict(session, payload)

        target = self._get_source_by_url(session, university_name=university_name, source_url=selected_source_url)
        if target is None:
            target = Source(**payload)
            session.add(target)
            session.flush()
        else:
            for key, value in payload.items():
                setattr(target, key, value)
            session.flush()

        for source in existing_sources:
            if target.id is not None and source.id == target.id:
                source.health_status = health_status
                source.last_failure_reason = None if health_status == "healthy" else "知识库主 source 非 healthy。"
                continue
            source.health_status = "stale"
            source.last_failure_reason = "已被知识库主 source 替换。"

    def _mark_stale_sources(
        self,
        session: Session,
        active_universities: set[str],
        universities: list[dict[str, Any]],
    ) -> None:
        selected_urls = {
            (item.get("university_name"), item.get("selected_source_url"))
            for item in universities
            if isinstance(item, dict) and item.get("university_name") and item.get("selected_source_url")
        }
        for source in session.scalars(select(Source).where(Source.collection_domain == "admissions_notice")):
            if source.organization_name not in active_universities:
                continue
            current_url = ((source.start_urls_json or [source.base_url]) or [source.base_url])[0]
            if (source.organization_name, current_url) not in selected_urls and source.source_origin == "seed":
                source.health_status = "stale"
                source.last_failure_reason = "不再是知识库主 source。"

    def _get_source_by_url(self, session: Session, *, university_name: str, source_url: str) -> Source | None:
        candidates = list(
            session.scalars(
                select(Source).where(
                    Source.organization_name == university_name,
                    Source.collection_domain == "admissions_notice",
                )
            )
        )
        for source in candidates:
            current_url = ((source.start_urls_json or [source.base_url]) or [source.base_url])[0]
            if current_url == source_url:
                return source
        return None

    def _build_fallback_source_payload(
        self,
        *,
        item: dict[str, Any],
        selected_entrypoint_url: str | None,
    ) -> dict[str, Any]:
        university_name = item["university_name"]
        source_title = item.get("source_title") or "研究生招生公告"
        selected_source_url = item["selected_source_url"]
        source_name = source_title if university_name in source_title else f"{university_name} {source_title}"
        return {
            "name": source_name,
            "organization_name": university_name,
            "source_type": "admissions_notice",
            "collection_domain": "admissions_notice",
            "source_origin": "seed",
            "base_url": selected_entrypoint_url or item.get("official_homepage_url") or selected_source_url,
            "start_urls_json": [selected_source_url],
            "site_type": "school",
            "crawl_mode": "static",
            "status": "active",
            "onboarding_status": "validated",
            "entrypoint_url": selected_entrypoint_url,
            "health_status": item.get("health_status") or "healthy",
            "scope_json": {
                "university_name": university_name,
                "admissions_levels": ["graduate"],
                "admissions_tracks": ["graduate"],
                "selected_track": "graduate",
            },
            "resolver_meta_json": {
                "candidate_type": item.get("candidate_type") or "list_page",
                "source_kind": item.get("source_kind") or "list_page",
                "source_resolution_strategy": "knowledge_sync",
            },
            "config_json": {
                "collection_domain": "admissions_notice",
                "institution": university_name,
                "aliases": [university_name],
                "source_kind": item.get("source_kind") or "list_page",
                "candidate_type": item.get("candidate_type") or "list_page",
                "admissions_levels": ["graduate"],
                "admissions_tracks": ["graduate"],
                "selected_track": "graduate",
                "list_pages": [selected_source_url],
                "list": {
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
            },
        }

    def _parse_iso_datetime(self, value: Any):
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
