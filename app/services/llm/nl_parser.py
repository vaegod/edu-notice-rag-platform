from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.admissions_tracks import broad_levels_from_tracks, infer_tracks_from_text, normalize_tracks
from app.services.admissions_taxonomy import ADMISSIONS_DOC_TYPES, ADMISSIONS_KEYWORDS
from app.services.c9_scope import (
    has_unsupported_admissions_text,
    infer_c9_university_from_text,
    mentions_c9_group,
    normalize_c9_university_name,
)
from app.services.domains import (
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
    COLLECTION_DOMAIN_NEWS_CENTER,
    COLLECTION_DOMAIN_SCHOOL_PROFILE,
    COLLECTION_DOMAINS,
    normalize_collection_domain,
)
from app.services.llm.siliconflow_client import SiliconFlowClient, load_prompt_template
from app.services.normalize.date_parser import parse_chinese_number

INTENT_RULES = [
    ("discover_sources", ["数据源", "源地址", "源链接"]),
    ("crawl_source", ["采集", "抓取", "爬取"]),
    ("summarize_documents", ["总结", "汇总", "概括"]),
    ("list_deadlines", ["截止", "deadline", "到期"]),
]

DOMAIN_RULES = [
    (COLLECTION_DOMAIN_SCHOOL_PROFILE, ["学校概况", "学校简介", "学校介绍", "基本情况", "概况"]),
    (COLLECTION_DOMAIN_NEWS_CENTER, ["新闻中心", "新闻网", "校园新闻", "综合新闻", "学院新闻", "新闻"]),
    (COLLECTION_DOMAIN_ADMISSIONS_NOTICE, ["招生", "本科招生", "研究生招生", "硕士", "博士", "招生公告"]),
]

NOTICE_TYPE_KEYWORDS = {
    "硕士招生": ["硕士", "统考", "硕士研究生"],
    "博士招生": ["博士", "博士研究生", "申请考核", "直博"],
    "推免": ["推免", "推荐免试", "预推免"],
    "调剂": ["调剂"],
    "夏令营": ["夏令营", "优秀大学生夏令营"],
    "复试": ["复试", "复试名单", "复试安排"],
    "录取/拟录取": ["拟录取", "录取名单", "录取结果"],
    "招生简章": ["招生简章", "招生说明", "报考说明"],
}

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


class NLTaskParser:
    def __init__(self, llm_client: SiliconFlowClient | None = None) -> None:
        self.settings = get_settings()
        self.llm_client = llm_client or SiliconFlowClient()
        self.prompt_template = load_prompt_template("nl_parse.txt")

    def parse(self, session: Session, query: str, homepage_url: str | None = None) -> dict:
        parsed = self._fallback_parse(session, query, homepage_url=homepage_url)
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return parsed
        try:
            llm_result = self.llm_client.chat_json(
                system_prompt=self.prompt_template,
                user_prompt=self._build_llm_user_prompt(query, homepage_url=homepage_url),
                biz_type="nl_parse",
                session=session,
            )
            return self._llm_primary_merge(parsed, llm_result, homepage_url=homepage_url)
        except Exception:
            return parsed

    def _fallback_parse(self, session: Session, query: str, homepage_url: str | None = None) -> dict:
        intent_locked = self._is_explicit_source_discovery_request(query)
        intent = self._detect_intent(query)
        collection_domain = self._detect_collection_domain(query)
        institution = None
        department = None
        university_name = None

        if university_name is None:
            university_name = self._extract_university_name(query)
        if institution is None:
            institution = university_name
        if department is None:
            department_match = re.search(r"([^\s，,。]{2,20}(学院|实验室|研究院|中心|处|办))", query)
            if department_match:
                department = department_match.group(1)

        topics = [keyword for keyword in ADMISSIONS_KEYWORDS if keyword.lower() in query.lower()]
        notice_types = [
            notice_type
            for notice_type, tokens in NOTICE_TYPE_KEYWORDS.items()
            if any(token.lower() in query.lower() for token in tokens)
        ]
        admissions_tracks = self._extract_admissions_tracks(query)
        admissions_levels = self._extract_admissions_levels(query, admissions_tracks=admissions_tracks)

        parsed = {
            "intent": intent,
            "intent_locked": intent_locked,
            "raw_query": query,
            "collection_domain": collection_domain,
            "university_name": university_name,
            "desired_source_count": self._extract_source_count(query) if intent == "discover_sources" else None,
            "institution": institution,
            "department": department,
            "topic": topics,
            "notice_type": notice_types,
            "time_range": self._extract_time_range(query),
            "filters": {"deadline_only": intent == "list_deadlines"},
            "output_mode": "summary" if intent == "summarize_documents" else "default",
            "result_limit": self._extract_result_limit(query),
            "admissions_levels": admissions_levels,
            "admissions_tracks": admissions_tracks,
            "requires_source_discovery": intent in {"crawl_source", "crawl_admissions", "discover_sources"},
            "resolved_homepage_url": homepage_url,
            "homepage_url": homepage_url,
        }
        if collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE and (
            normalize_c9_university_name(university_name)
            or infer_c9_university_from_text(query)
            or mentions_c9_group(query)
        ) and not has_unsupported_admissions_text(query):
            parsed["admissions_levels"] = ["graduate"]
            parsed["admissions_tracks"] = ["graduate"]
        if intent == "crawl_source" and collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            parsed["intent"] = "crawl_admissions"
        elif intent == "search_documents" and collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            parsed["intent"] = "search_admissions"
        elif intent == "summarize_documents" and collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            parsed["intent"] = "summarize_admissions"
        return parsed

    def _detect_intent(self, query: str) -> str:
        lowered = query.lower()
        if self._is_explicit_source_discovery_request(query):
            return "discover_sources"
        for intent, tokens in INTENT_RULES:
            if any(token.lower() in lowered for token in tokens):
                return intent
        return "search_documents"

    def _is_explicit_source_discovery_request(self, query: str) -> bool:
        if "数据源" not in query:
            return False
        discovery_tokens = ("提供", "列出", "给我", "返回", "推荐", "查找", "寻找", "罗列", "采集", "抓取", "爬取")
        return any(token in query for token in discovery_tokens)

    def _detect_collection_domain(self, query: str) -> str:
        lowered = query.lower()
        for collection_domain, tokens in DOMAIN_RULES:
            if any(token.lower() in lowered for token in tokens):
                return collection_domain
        return COLLECTION_DOMAIN_ADMISSIONS_NOTICE

    def _extract_university_name(self, query: str) -> str | None:
        c9_name = infer_c9_university_from_text(query)
        if c9_name:
            return c9_name
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

    def _extract_admissions_tracks(self, query: str) -> list[str]:
        return infer_tracks_from_text(query)

    def _extract_admissions_levels(self, query: str, *, admissions_tracks: list[str] | None = None) -> list[str]:
        levels: list[str] = []
        if any(token in query for token in ("本科", "强基计划", "高水平运动队")):
            levels.append("undergraduate")
        if any(token in query for token in ("研究生", "硕士", "博士", "推免")):
            levels.append("graduate")
        for level in broad_levels_from_tracks(admissions_tracks):
            if level not in levels:
                levels.append(level)
        return levels

    def _extract_time_range(self, query: str) -> dict:
        if "本周" in query:
            return {"relative": "this_week"}
        if "本月" in query:
            return {"relative": "this_month"}
        if "最近" in query or "近" in query:
            week_match = re.search(r"(最近|近)([一二两三四五六七八九十\d]+)周", query)
            if week_match:
                weeks = parse_chinese_number(week_match.group(2)) or 2
                return {"relative": f"last_{weeks}_weeks"}
            day_match = re.search(r"(最近|近)([一二两三四五六七八九十\d]+)天", query)
            if day_match:
                days = parse_chinese_number(day_match.group(2)) or 7
                return {"relative": f"last_{days}_days"}
        return {}

    def _extract_result_limit(self, query: str) -> int:
        match = re.search(r"前\s*(\d+)\s*条", query)
        if match:
            return int(match.group(1))
        return 10

    def _extract_source_count(self, query: str) -> int | None:
        if mentions_c9_group(query):
            return 9
        match = re.search(r"([一二两三四五六七八九十百\d]+)\s*(所|个|条)?\s*高校", query)
        if match:
            value = parse_chinese_number(match.group(1))
            if value:
                return value
            if match.group(1).isdigit():
                return int(match.group(1))
        match = re.search(r"(\d+)\s*(个|条)?\s*数据源", query)
        if match:
            return int(match.group(1))
        if "十所高校" in query:
            return 10
        return None

    def _llm_primary_merge(self, parsed: dict, llm_result: dict, *, homepage_url: str | None = None) -> dict:
        intent_locked = bool(parsed.get("intent_locked"))
        normalized_intent = self._normalize_intent(
            llm_result.get("intent"),
            fallback_intent=parsed.get("intent"),
            intent_locked=intent_locked,
        )
        normalized_domain = self._normalize_llm_collection_domain(
            llm_result.get("collection_domain"),
            fallback_domain=parsed.get("collection_domain"),
        )
        rule_c9_university = (
            normalize_c9_university_name(parsed.get("university_name") or parsed.get("institution"))
            or infer_c9_university_from_text(parsed.get("raw_query"))
        )
        university_name = rule_c9_university or self._preferred_text(llm_result.get("university_name"), parsed.get("university_name"))
        institution = rule_c9_university or self._preferred_text(llm_result.get("institution"), parsed.get("institution"))
        merged = {
            "intent": self._apply_domain_specific_intent(normalized_intent, normalized_domain),
            "intent_locked": intent_locked,
            "raw_query": parsed.get("raw_query"),
            "collection_domain": normalized_domain,
            "university_name": university_name,
            "desired_source_count": self._preferred_positive_int(
                llm_result.get("desired_source_count"),
                parsed.get("desired_source_count"),
            ),
            "institution": institution,
            "department": self._preferred_text(llm_result.get("department"), parsed.get("department")),
            "topic": self._preferred_string_list(llm_result.get("topic"), parsed.get("topic")),
            "notice_type": [
                item
                for item in self._preferred_string_list(llm_result.get("notice_type"), parsed.get("notice_type"))
                if item in ADMISSIONS_DOC_TYPES
            ],
            "time_range": llm_result.get("time_range") if isinstance(llm_result.get("time_range"), dict) else (parsed.get("time_range") or {}),
            "filters": self._merge_filters(parsed.get("filters"), llm_result.get("filters")),
            "output_mode": self._preferred_text(llm_result.get("output_mode"), parsed.get("output_mode")) or "default",
            "result_limit": self._preferred_positive_int(llm_result.get("result_limit"), parsed.get("result_limit") or 10) or 10,
            "admissions_levels": self._normalize_admissions_levels(
                self._preferred_string_list(llm_result.get("admissions_levels"), parsed.get("admissions_levels"))
            ),
            "admissions_tracks": self._normalize_admissions_tracks(
                self._preferred_string_list(llm_result.get("admissions_tracks"), parsed.get("admissions_tracks"))
            ),
            "requires_source_discovery": self._preferred_bool(
                llm_result.get("requires_source_discovery"),
                parsed.get("requires_source_discovery"),
            ),
            "resolved_homepage_url": self._preferred_text(
                homepage_url,
                self._preferred_text(llm_result.get("resolved_homepage_url"), parsed.get("resolved_homepage_url")),
            ),
            "homepage_url": homepage_url if homepage_url is not None else parsed.get("homepage_url"),
        }
        merged["requires_source_discovery"] = bool(
            merged["requires_source_discovery"]
            or merged["intent"] in {"crawl_source", "crawl_admissions", "discover_sources"}
        )
        merged["admissions_levels"] = self._merge_string_lists(
            merged["admissions_levels"],
            broad_levels_from_tracks(merged.get("admissions_tracks")),
        )
        if normalized_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE and (
            normalize_c9_university_name(merged.get("university_name") or merged.get("institution"))
            or infer_c9_university_from_text(parsed.get("raw_query"))
            or mentions_c9_group(parsed.get("raw_query"))
        ) and not has_unsupported_admissions_text(parsed.get("raw_query")):
            merged["admissions_levels"] = ["graduate"]
            merged["admissions_tracks"] = ["graduate"]
        if merged["intent"] == "discover_sources" and merged["desired_source_count"] is None:
            merged["desired_source_count"] = parsed.get("desired_source_count") or 10
        merged.setdefault("collection_domain", COLLECTION_DOMAIN_ADMISSIONS_NOTICE)
        merged.setdefault("university_name", None)
        merged.setdefault("filters", {})
        merged.setdefault("topic", [])
        merged.setdefault("notice_type", [])
        merged.setdefault("admissions_levels", [])
        merged.setdefault("admissions_tracks", [])
        merged.setdefault("time_range", {})
        return merged

    # Backward-compatible alias used by tests and older call sites.
    def _merge_llm_result(self, parsed: dict, llm_result: dict) -> dict:
        return self._llm_primary_merge(parsed, llm_result)

    def _build_llm_user_prompt(self, query: str, *, homepage_url: str | None = None) -> str:
        lines = [f"用户请求: {query}"]
        if homepage_url:
            lines.append(f"官网首页: {homepage_url}")
        return "\n".join(lines)

    def _normalize_intent(self, value, *, fallback_intent: str | None, intent_locked: bool) -> str:
        if intent_locked:
            return "discover_sources"
        aliases = {
            "discover_sources": "discover_sources",
            "data_source_retrieval": "discover_sources",
            "source_discovery": "discover_sources",
            "find_source": "discover_sources",
            "resolve_source": "discover_sources",
            "crawl_source": "crawl_source",
            "crawl_admissions": "crawl_admissions",
            "website_crawl": "crawl_source",
            "crawl_website": "crawl_source",
            "search_documents": "search_documents",
            "search": "search_documents",
            "query": "search_documents",
            "search_notice": "search_documents",
            "search_admissions": "search_admissions",
            "summarize_documents": "summarize_documents",
            "summarize": "summarize_documents",
            "summarize_admissions": "summarize_admissions",
            "list_deadlines": "list_deadlines",
        }
        normalized = aliases.get((value or "").strip()) if isinstance(value, str) else None
        if normalized in SUPPORTED_INTENTS:
            return normalized
        return fallback_intent or "search_documents"

    def _normalize_llm_collection_domain(self, value, *, fallback_domain: str | None) -> str:
        aliases = {
            "admissions": COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            "admissions_notice": COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            "news": COLLECTION_DOMAIN_NEWS_CENTER,
            "news_center": COLLECTION_DOMAIN_NEWS_CENTER,
            "school_profile": COLLECTION_DOMAIN_SCHOOL_PROFILE,
            "university_website": COLLECTION_DOMAIN_SCHOOL_PROFILE,
            "website": COLLECTION_DOMAIN_SCHOOL_PROFILE,
        }
        if isinstance(value, str):
            candidate = aliases.get(value.strip(), value.strip())
            if candidate in COLLECTION_DOMAINS:
                return normalize_collection_domain(candidate)
        return normalize_collection_domain(fallback_domain)

    def _apply_domain_specific_intent(self, intent: str, collection_domain: str) -> str:
        if collection_domain == COLLECTION_DOMAIN_ADMISSIONS_NOTICE:
            if intent == "crawl_source":
                return "crawl_admissions"
            if intent == "search_documents":
                return "search_admissions"
            if intent == "summarize_documents":
                return "summarize_admissions"
        return intent

    def _preferred_text(self, preferred, fallback):
        if self._is_informative_text(preferred):
            return preferred.strip()
        if self._is_informative_text(fallback):
            return fallback.strip()
        return fallback

    def _preferred_positive_int(self, preferred, fallback) -> int | None:
        if isinstance(preferred, int) and preferred > 0:
            return preferred
        if isinstance(fallback, int) and fallback > 0:
            return fallback
        return None

    def _preferred_bool(self, preferred, fallback) -> bool:
        if isinstance(preferred, bool):
            return preferred
        return bool(fallback)

    def _preferred_string_list(self, preferred, fallback) -> list[str]:
        preferred_items = self._normalize_string_list(preferred)
        if preferred_items:
            return preferred_items
        return self._normalize_string_list(fallback)

    def _normalize_string_list(self, value) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            if self._is_informative_text(item):
                clean = item.strip()
                if clean not in normalized:
                    normalized.append(clean)
        return normalized

    def _normalize_admissions_levels(self, values: list[str]) -> list[str]:
        aliases = {
            "undergraduate": "undergraduate",
            "本科": "undergraduate",
            "graduate": "graduate",
            "研究生": "graduate",
            "硕士": "graduate",
            "博士": "graduate",
            "postgraduate": "graduate",
            "master": "graduate",
            "doctor": "graduate",
        }
        normalized: list[str] = []
        for value in values:
            target = aliases.get(value.strip().lower()) or aliases.get(value.strip())
            if target and target not in normalized:
                normalized.append(target)
        return normalized

    def _normalize_admissions_tracks(self, values: list[str]) -> list[str]:
        return normalize_tracks(values)

    def _merge_filters(self, fallback_filters, preferred_filters) -> dict:
        merged = dict(fallback_filters or {})
        if isinstance(preferred_filters, dict):
            merged.update(preferred_filters)
        return merged

    def _merge_string_lists(self, base: list | None, extra: list | None) -> list[str]:
        merged: list[str] = []
        for values in (base or [], extra or []):
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                continue
            for item in values:
                if self._is_informative_text(item) and item not in merged:
                    merged.append(item.strip())
        return merged

    def _is_informative_text(self, value) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.strip()
        if not normalized:
            return False
        if normalized.count("?") >= max(2, len(normalized) // 2):
            return False
        return True
