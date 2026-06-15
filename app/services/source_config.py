from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.domains import (
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
    COLLECTION_DOMAIN_NEWS_CENTER,
    COLLECTION_DOMAIN_SCHOOL_PROFILE,
    normalize_collection_domain,
)


DEFAULT_PLAYWRIGHT_CONFIG = {
    "enabled": True,
    "wait_until": "networkidle",
    "timeout_ms": 30000,
}

DEFAULT_SCRAPY_CONFIG = {
    "concurrent_requests": 8,
    "download_timeout_seconds": 20,
    "obey_robots_txt": False,
}


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def normalize_source_config(
    config: dict[str, Any] | None,
    *,
    start_urls: list[str] | None = None,
) -> dict[str, Any]:
    normalized = deepcopy(config or {})
    collection_domain = normalize_collection_domain(
        normalized.get("collection_domain") or normalized.get("probe_target")
    )
    effective_start_urls = [url for url in (start_urls or normalized.get("list_pages") or []) if url]
    normalized["list_pages"] = effective_start_urls
    normalized.setdefault("aliases", [])
    normalized.setdefault("list", {})
    normalized.setdefault("detail", {})
    normalized.setdefault("schedule_hours", 6)
    normalized.setdefault("hot_source", True)
    normalized.setdefault("collection_domain", collection_domain)
    normalized.setdefault("probe_target", collection_domain)
    normalized["engine"] = "scrapy"
    normalized.setdefault(
        "render_mode",
        "dynamic" if collection_domain != COLLECTION_DOMAIN_SCHOOL_PROFILE else "static",
    )
    normalized["scrapy"] = deep_merge(
        DEFAULT_SCRAPY_CONFIG,
        normalized.get("scrapy") or {},
    )
    normalized["playwright"] = deep_merge(
        DEFAULT_PLAYWRIGHT_CONFIG,
        normalized.get("playwright") or {},
    )
    return normalized


def infer_adapter_code(config: dict[str, Any] | None) -> str:
    normalized = config or {}
    if normalize_collection_domain(normalized.get("collection_domain")) == COLLECTION_DOMAIN_SCHOOL_PROFILE:
        return "web_profile_page"
    return "web_list_detail"


def infer_template_code(config: dict[str, Any] | None) -> str | None:
    normalized = config or {}
    list_config = normalized.get("list") or {}
    collection_domain = normalize_collection_domain(normalized.get("collection_domain"))
    adapter_code = infer_adapter_code(normalized)
    if adapter_code == "web_profile_page":
        return "school_profile_page"
    if adapter_code != "web_list_detail":
        return None
    is_ajax = list_config.get("request_method", "GET").upper() == "POST" and list_config.get("response_json_key")
    if collection_domain == COLLECTION_DOMAIN_NEWS_CENTER:
        return "news_list_ajax" if is_ajax else "news_list_static"
    return "admissions_list_ajax" if is_ajax else "admissions_list_static"


def get_source_engine(config: dict[str, Any] | None) -> str:
    return "scrapy"


def split_source_metadata(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = deepcopy(config or {})
    return {
        "institution": normalized.get("institution") or "",
        "department": normalized.get("department") or "",
        "aliases": list(normalized.get("aliases") or []),
        "collection_domain": normalize_collection_domain(normalized.get("collection_domain")),
        "schedule_hours": normalized.get("schedule_hours"),
        "hot_source": normalized.get("hot_source"),
    }


def split_source_extraction_rules(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_source_config(config or {})
    return {
        "engine": normalized.get("engine"),
        "render_mode": normalized.get("render_mode"),
        "list_pages": list(normalized.get("list_pages") or []),
        "list": deepcopy(normalized.get("list") or {}),
        "detail": deepcopy(normalized.get("detail") or {}),
        "playwright": deepcopy(normalized.get("playwright") or {}),
        "scrapy": deepcopy(normalized.get("scrapy") or {}),
    }
