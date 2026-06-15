from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("MOCK_LLM_ENABLED", "true")
os.environ.setdefault("AUTO_SYNC_C9_SOURCE_KNOWLEDGE", "false")
os.environ.setdefault("CRAWL4AI_ENABLED", "false")
os.environ.setdefault("CRAWLER_TIMEOUT_SECONDS", "12")

from app.core.config import get_settings
from app.core.database import get_session_factory, init_db
from app.core.time import utc_now
from app.services.c9_source_knowledge import C9SourceKnowledgeService
from app.services.llm.source_resolver import LLMSourceResolverService
from app.services.onboarding.providers import Crawl4AIProvider
from app.services.source_discovery_agent import SourceDiscoveryAgentService
from app.services.source_resolution import LLMResolvedSourceValidator


class C9SourceKnowledgeBuilder:
    def __init__(self) -> None:
        get_settings.cache_clear()
        self.provider = Crawl4AIProvider()
        self.validator = LLMResolvedSourceValidator(provider=self.provider)
        self.resolver = LLMSourceResolverService()
        self.agent = SourceDiscoveryAgentService(
            resolver=self.resolver,
            validator=self.validator,
            provider=self.provider,
            find_existing_source=lambda *args, **kwargs: None,
            upsert_source=lambda *args, **kwargs: None,
        )
        self.knowledge_service = C9SourceKnowledgeService()

    def build(self, *, sync_db: bool = True) -> dict[str, Any]:
        payload = self.knowledge_service.load_payload()
        built_records = [self._build_record(item) for item in payload.get("universities") or [] if isinstance(item, dict)]
        result = {
            "version": payload.get("version") or 1,
            "generated_at": utc_now().isoformat(),
            "scope": payload.get("scope") or "graduate_admissions",
            "universities": built_records,
        }
        self.knowledge_service.save_payload(result)
        if sync_db:
            init_db()
            session = get_session_factory()()
            try:
                self.knowledge_service.sync_from_payload(session, result)
            finally:
                session.close()
        self._write_report(result)
        return result

    def _build_record(self, record: dict[str, Any]) -> dict[str, Any]:
        university_name = record.get("university_name")
        official_homepage_url = record.get("official_homepage_url")
        entrypoint_candidates = self._dedupe_urls(record.get("entrypoint_candidates") or [])
        checked_at = utc_now().isoformat()
        fallback_notes: list[str] = []
        first_accessible_entrypoint: str | None = None
        best_accessible_evidence: dict[str, Any] | None = None

        for entrypoint_url in entrypoint_candidates:
            attempt = self._inspect_entrypoint(university_name=university_name, entrypoint_url=entrypoint_url)
            fallback_notes.extend(attempt["notes"])
            if attempt["accessible"] and first_accessible_entrypoint is None:
                first_accessible_entrypoint = entrypoint_url
                best_accessible_evidence = attempt["validation_evidence"]
            selected_option = self._choose_best_valid_candidate(
                entrypoint_url=entrypoint_url,
                valid_options=attempt.get("valid_options") or [],
            )
            if selected_option is None:
                continue
            selected_candidate = self._json_safe(selected_option["payload"])
            validated = selected_option["validated"]
            selected_candidate = self._normalize_selected_candidate(
                entrypoint_url=entrypoint_url,
                selected_candidate=selected_candidate,
                validated=validated,
            )
            confidence_score = self._confidence_score(entrypoint_url=entrypoint_url, selected_candidate=selected_candidate)
            validation_evidence = dict(attempt["validation_evidence"] or {})
            validation_evidence["normalized_source"] = self._json_safe(validated.normalized_source)
            validation_evidence["selected_candidate"] = selected_candidate
            validation_evidence["confidence_score"] = confidence_score
            return {
                "university_name": university_name,
                "normalized_name": record.get("normalized_name") or university_name,
                "scope": record.get("scope") or "graduate_admissions",
                "official_homepage_url": official_homepage_url,
                "entrypoint_candidates": entrypoint_candidates,
                "selected_entrypoint_url": entrypoint_url,
                "selected_source_url": selected_candidate.get("source_url"),
                "source_title": selected_candidate.get("source_title"),
                "source_kind": selected_candidate.get("source_kind"),
                "candidate_type": selected_candidate.get("candidate_type"),
                "health_status": "healthy",
                "confidence_score": confidence_score,
                "checked_at": checked_at,
                "validation_evidence": validation_evidence,
                "fallback_notes": fallback_notes,
            }

        health_status = "stale" if first_accessible_entrypoint else "invalid"
        confidence_score = 0.35 if first_accessible_entrypoint else 0.0
        return {
            "university_name": university_name,
            "normalized_name": record.get("normalized_name") or university_name,
            "scope": record.get("scope") or "graduate_admissions",
            "official_homepage_url": official_homepage_url,
            "entrypoint_candidates": entrypoint_candidates,
            "selected_entrypoint_url": first_accessible_entrypoint,
            "selected_source_url": None,
            "source_title": None,
            "source_kind": None,
            "candidate_type": None,
            "health_status": health_status,
            "confidence_score": confidence_score,
            "checked_at": checked_at,
            "validation_evidence": best_accessible_evidence or {},
            "fallback_notes": fallback_notes or ["未找到可复用主 source。"],
        }

    def _inspect_entrypoint(self, *, university_name: str, entrypoint_url: str) -> dict[str, Any]:
        notes: list[str] = []
        try:
            payload = self.provider.fetch_page_payload(url=entrypoint_url, crawl_mode="static")
        except Exception as exc:
            payload = None
            notes.append(f"{entrypoint_url} 抓取异常：{exc}")
        if not payload:
            payload = self._crawl4ai_fetch_page_payload(entrypoint_url)
            if payload:
                notes.append(f"{entrypoint_url} 使用 crawl4ai 静态回退抓取成功。")
        if not payload:
            notes.append(f"{entrypoint_url} 不可访问。")
            return {
                "accessible": False,
                "selected_candidate": None,
                "validated": None,
                "notes": notes,
                "validation_evidence": {
                    "entrypoint_url": entrypoint_url,
                    "status": "inaccessible",
                },
            }

        links = []
        discovered = self.provider.discover_candidate_links(homepage_url=entrypoint_url, max_links=60)
        links.extend(discovered.get("links") or [])
        links.extend(payload.get("links") or [])
        links.extend(self.agent._extract_links_from_payload(homepage_url=entrypoint_url, payload=payload))
        if len(links) < 8:
            crawl4ai_links = self._crawl4ai_discover_candidate_links(entrypoint_url)
            if crawl4ai_links:
                notes.append(f"{entrypoint_url} 使用 crawl4ai 候选发现补充 {len(crawl4ai_links)} 条链接。")
                links.extend(crawl4ai_links)
        candidates: list[dict[str, Any]] = []
        for item in links:
            built = self.agent._candidate_from_link(
                homepage_url=entrypoint_url,
                item=item,
                discovery_channel="knowledge_builder",
            )
            if built is not None:
                candidates.append(built)
        root_candidates = [item for item in candidates if item.get("source_kind") == "channel_page"][:6]
        for root_candidate in root_candidates:
            candidates.extend(
                self.agent._expand_candidate(
                    root_candidate,
                    admissions_levels=["graduate"],
                    admissions_tracks=["graduate"],
                    trace=[],
                )
            )
        candidates.extend(self.agent._harvest_sitemap_candidates(homepage_url=entrypoint_url))
        candidates = self.agent._dedupe_candidates(candidates)
        featured = [
            self.agent._featurize_candidate(
                item,
                admissions_levels=["graduate"],
                admissions_tracks=["graduate"],
            )
            for item in candidates
        ]
        featured = sorted(featured, key=lambda item: float(item.get("heuristic_score") or 0.0), reverse=True)

        trace: list[dict[str, Any]] = []
        validated_map: dict[str, Any] = {}
        result_payloads: list[dict[str, Any]] = []
        for item in featured[:20]:
            validated = self.agent._validate_candidate_payload(
                item,
                university_name=university_name,
                collection_domain="admissions_notice",
                homepage_url=entrypoint_url,
                admissions_levels=["graduate"],
                admissions_tracks=["graduate"],
                trace=trace,
            )
            payload_item = self.agent._candidate_result_payload(item, validated, None)
            result_payloads.append(payload_item)
            if validated is not None and payload_item.get("source_url"):
                validated_map[payload_item["source_url"]] = validated

        result_payloads = self.agent._sort_result_payloads(
            self.agent._dedupe_result_payloads(result_payloads),
            admissions_levels=["graduate"],
            admissions_tracks=["graduate"],
        )
        selected_candidate = next((item for item in result_payloads if item.get("validation_status") == "valid"), None)
        valid_options = [
            {
                "payload": item,
                "validated": validated_map.get(item.get("source_url")),
            }
            for item in result_payloads
            if item.get("validation_status") == "valid" and validated_map.get(item.get("source_url")) is not None
        ]
        validation_evidence = {
            "entrypoint_url": entrypoint_url,
            "entrypoint_title": payload.get("title"),
            "entrypoint_status_code": payload.get("status_code"),
            "raw_candidate_count": len(candidates),
            "featured_candidate_count": len(featured),
            "candidate_track_counts": self.agent._build_debug_summary(result_payloads, featured).get("candidate_track_counts", {}),
            "candidate_type_counts": self.agent._build_debug_summary(result_payloads, featured).get("candidate_type_counts", {}),
            "reject_reason_counts": self.agent._build_debug_summary(result_payloads, featured).get("reject_reason_counts", {}),
            "top_results": result_payloads[:5],
        }
        if selected_candidate is None:
            notes.append(f"{entrypoint_url} 可访问，但未找到合格的列表页/栏目页。")
            return {
                "accessible": True,
                "selected_candidate": None,
                "validated": None,
                "valid_options": [],
                "notes": notes,
                "validation_evidence": self._json_safe(validation_evidence),
            }
        return {
            "accessible": True,
            "selected_candidate": self._json_safe(selected_candidate),
            "validated": validated_map.get(selected_candidate.get("source_url")),
            "valid_options": valid_options,
            "notes": notes,
            "validation_evidence": self._json_safe(validation_evidence),
        }

    def _confidence_score(self, *, entrypoint_url: str, selected_candidate: dict[str, Any]) -> float:
        score = 0.25
        candidate_type = selected_candidate.get("candidate_type")
        if candidate_type == "list_page":
            score += 0.20
        elif candidate_type == "channel_page":
            score += 0.14
        official_match = self._registered_domain(entrypoint_url) == self._registered_domain(selected_candidate.get("source_url"))
        if official_match:
            score += 0.20
        if selected_candidate.get("track") == "graduate":
            score += 0.15
        suitability = min(max(float(selected_candidate.get("source_suitability_score") or 0.0), 0.0), 100.0)
        score += suitability / 1000
        if candidate_type in {"detail_page", "file_page", "stats_page"}:
            score -= 0.40
        return round(max(0.0, min(score, 0.99)), 4)

    def _registered_domain(self, value: str | None) -> str:
        host = (urlparse(value or "").hostname or "").lower()
        if host.endswith(".edu.cn"):
            parts = host.split(".")
            return ".".join(parts[-3:]) if len(parts) >= 3 else host
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host

    def _crawl4ai_fetch_page_payload(self, url: str) -> dict[str, Any] | None:
        previous = bool(self.provider.settings.crawl4ai_enabled)
        self.provider.settings.crawl4ai_enabled = True
        try:
            return self.provider.fetch_page_payload(url=url, crawl_mode="static")
        except Exception:
            return None
        finally:
            self.provider.settings.crawl4ai_enabled = previous

    def _crawl4ai_discover_candidate_links(self, homepage_url: str) -> list[dict[str, Any]]:
        previous = bool(self.provider.settings.crawl4ai_enabled)
        self.provider.settings.crawl4ai_enabled = True
        try:
            result = self.provider.discover_candidate_links(homepage_url=homepage_url, max_links=60)
            return result.get("links") or []
        except Exception:
            return []
        finally:
            self.provider.settings.crawl4ai_enabled = previous

    def _choose_best_valid_candidate(self, *, entrypoint_url: str, valid_options: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not valid_options:
            return None
        ranked = sorted(
            valid_options,
            key=lambda item: self._knowledge_priority_score(
                entrypoint_url=entrypoint_url,
                payload=item["payload"],
                validated=item["validated"],
            ),
            reverse=True,
        )
        return ranked[0]

    def _knowledge_priority_score(self, *, entrypoint_url: str, payload: dict[str, Any], validated: Any) -> float:
        source_url = payload.get("source_url") or ""
        source_title = payload.get("source_title") or ""
        page_title = ""
        if validated is not None and getattr(validated.report, "samples", None):
            page_title = validated.report.samples[0].title or ""
        combined = f"{source_title} {page_title} {source_url}"
        score = 0.0
        entry_host = (urlparse(entrypoint_url).hostname or "").lower()
        source_host = (urlparse(source_url).hostname or "").lower()
        if source_host == entry_host:
            score += 40.0
        elif self._registered_domain(source_url) == self._registered_domain(entrypoint_url):
            score += 12.0
        candidate_type = payload.get("candidate_type")
        if candidate_type == "list_page":
            score += 30.0
        elif candidate_type == "channel_page":
            score += 20.0
        elif candidate_type == "site_home":
            score += 8.0
        if payload.get("track") == "graduate":
            score += 10.0
        score += min(float(payload.get("source_suitability_score") or 0.0) / 5.0, 20.0)
        general_tokens = (
            "招生信息",
            "通知公告",
            "招生简章",
            "招生章程",
            "专业目录",
            "硕士招生",
            "博士招生",
            "招生章程、目录",
        )
        if any(token in combined for token in general_tokens):
            score += 16.0
        preferred_titles = (
            "通知公告",
            "招生信息",
            "硕士招生",
            "硕士生招生",
            "硕士最新通知",
            "博士最新通知",
            "招生简章",
            "招生章程",
            "专业目录",
        )
        if any(token in combined for token in preferred_titles):
            score += 18.0
        if any(token in combined for token in ("硕士招生", "硕士生招生", "硕士最新通知", "硕士研究生", "招生信息")):
            score += 14.0
        if any(token in combined for token in ("博士招生", "博士生招生")):
            score -= 6.0
        url_lower = source_url.lower()
        if any(token in url_lower for token in ("list", "notice", "notices", "tzgg", "jzml", "zsxx", "wbtreeid", "column/182", "column/181")):
            score += 10.0
        narrow_tokens = (
            "推免",
            "夏令营",
            "拟录取",
            "名单公示",
            "报名通知",
            "现场咨询",
            "咨询会",
            "招生咨询",
            "站内搜索",
            "国际",
            "港澳台",
            "调剂",
            "复试分数线",
        )
        if any(token in combined for token in narrow_tokens):
            score -= 18.0
        negative_titles = (
            "院系动态",
            "招生咨询",
            "站内搜索",
            "港澳台招生",
            "国际招生",
            "推免招生",
        )
        if any(token in combined for token in negative_titles):
            score -= 20.0
        if any(token in source_title.lower() for token in ("more", "search")):
            score -= 12.0
        if any(token in url_lower for token in ("/post/", "/page.htm")):
            score -= 25.0
        return score

    def _normalize_selected_candidate(
        self,
        *,
        entrypoint_url: str,
        selected_candidate: dict[str, Any],
        validated: Any,
    ) -> dict[str, Any]:
        title = (selected_candidate.get("source_title") or "").strip()
        replacement = self._derive_better_title(
            entrypoint_url=entrypoint_url,
            selected_candidate=selected_candidate,
            validated=validated,
        )
        if replacement and replacement != title:
            selected_candidate["source_title"] = replacement
            if validated is not None:
                validated.candidate.source_title = replacement
                if validated.normalized_source:
                    validated.normalized_source["name"] = (
                        replacement
                        if validated.candidate.university_name in replacement
                        else f"{validated.candidate.university_name} {replacement}"
                    )
                    config_json = dict(validated.normalized_source.get("config_json") or {})
                    config_json["source_title"] = replacement
                    validated.normalized_source["config_json"] = config_json
        return selected_candidate

    def _derive_better_title(
        self,
        *,
        entrypoint_url: str,
        selected_candidate: dict[str, Any],
        validated: Any,
    ) -> str | None:
        current_title = (selected_candidate.get("source_title") or "").strip()
        source_url = selected_candidate.get("source_url") or ""
        raw_text = ""
        payload = None
        try:
            payload = self.provider.fetch_page_payload(url=source_url, crawl_mode="static")
        except Exception:
            payload = None
        raw_text = (payload.get("raw_text") or "") if payload else ""
        combined = f"{current_title} {raw_text} {source_url}"
        url_lower = source_url.lower()
        if "zyml" in url_lower:
            if "硕士" in combined:
                return "硕士研究生招生专业目录"
            if "博士" in combined:
                return "博士研究生招生专业目录"
            return "研究生招生专业目录"
        if any(token in url_lower for token in ("tzgg", "column/182", "column/181")):
            return "通知公告"
        if "/zkxx/sszs" in url_lower:
            return "硕士招生"
        if "/zsjz/sszs" in url_lower:
            return "硕士生招生"
        if "/zkxx/yxdt" in url_lower:
            return "院系动态"
        if "招生章程" in combined:
            return "招生章程"
        if "招生简章" in combined:
            if "硕士" in combined:
                return "硕士招生简章"
            if "博士" in combined:
                return "博士招生简章"
            return "招生简章"
        if "硕士最新通知" in combined:
            return "硕士最新通知"
        if "博士最新通知" in combined:
            return "博士最新通知"
        if "通知公告" in combined:
            return "通知公告"
        if "招生信息" in combined:
            return "招生信息"
        if "Admissions" in current_title or "admissions" in current_title.lower():
            return "招生信息"
        if current_title and current_title not in {"More", "MORE", "招生咨询", "站内搜索"}:
            return current_title
        hints = (
            "通知公告",
            "招生信息",
            "硕士最新通知",
            "博士最新通知",
            "硕士招生",
            "硕士生招生",
            "博士招生",
            "招生简章",
            "招生章程",
            "专业目录",
        )
        for hint in hints:
            if hint in raw_text:
                return hint
        url_lower = source_url.lower()
        if "tzgg" in url_lower:
            return "通知公告"
        if "jzml" in url_lower:
            return "专业目录"
        if "column/182" in url_lower or "column/181" in url_lower:
            return "通知公告"
        if "47863" in url_lower:
            return "硕士最新通知"
        if "47865" in url_lower:
            return "博士最新通知"
        if "47840" in url_lower or "8817" in url_lower:
            return "硕士招生"
        if "zkxx/sszs" in url_lower:
            return "硕士招生"
        if "zsjz/sszs" in url_lower:
            return "硕士生招生"
        if validated is not None and getattr(validated.report, "samples", None):
            sample_title = (validated.report.samples[0].title or "").strip()
            if sample_title and sample_title != current_title:
                return sample_title
        return current_title or None

    def _dedupe_urls(self, urls: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in urls:
            if not isinstance(item, str):
                continue
            clean = item.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            deduped.append(clean)
        return deduped

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

    def _write_report(self, payload: dict[str, Any]) -> None:
        report_path = PROJECT_ROOT / "docs" / "c9_source_knowledge_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# C9 Source Knowledge Report",
            "",
            f"- Generated at: `{payload.get('generated_at')}`",
            "",
            "| 高校 | 入口 | 主 Source | 状态 | 置信度 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in payload.get("universities") or []:
            lines.append(
                "| {university} | {entrypoint} | {source} | {status} | {confidence:.0%} |".format(
                    university=item.get("university_name") or "未知",
                    entrypoint=item.get("selected_entrypoint_url") or "未命中",
                    source=item.get("selected_source_url") or "未命中",
                    status=item.get("health_status") or "unknown",
                    confidence=float(item.get("confidence_score") or 0.0),
                )
            )
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and sync the C9 graduate admissions source knowledge base.")
    parser.add_argument("--skip-db-sync", action="store_true", help="Only write the JSON knowledge base and report.")
    args = parser.parse_args()
    result = C9SourceKnowledgeBuilder().build(sync_db=not args.skip_db_sync)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
