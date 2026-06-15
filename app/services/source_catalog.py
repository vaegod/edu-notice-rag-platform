from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source_adapter import SourceAdapter
from app.models.source_template import SourceTemplate


BUILTIN_ADAPTERS: list[dict[str, Any]] = [
    {
        "code": "web_list_detail",
        "name": "网页列表-详情",
        "description": "用于新闻中心、招生公告等列表页采集。",
        "is_builtin": True,
    },
    {
        "code": "web_profile_page",
        "name": "单页详情",
        "description": "用于学校概况等单页型采集。",
        "is_builtin": True,
    },
]


BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "adapter_code": "web_profile_page",
        "code": "school_profile_page",
        "name": "学校概况单页",
        "description": "适用于高校概况、学校简介、学校介绍等单页型页面。",
        "template_schema_json": {
            "required_fields": ["name", "base_url", "start_urls_json"],
            "editable_sections": ["detail", "scrapy", "playwright"],
        },
        "default_config_json": {
            "collection_domain": "school_profile",
            "list_pages": [],
            "aliases": [],
            "engine": "scrapy",
            "render_mode": "static",
            "playwright": {
                "enabled": True,
                "wait_until": "networkidle",
                "timeout_ms": 30000,
            },
            "scrapy": {
                "concurrent_requests": 4,
                "download_timeout_seconds": 20,
                "obey_robots_txt": False,
            },
            "detail": {
                "title_selector": "h1, .title, .article-title",
                "publish_date_selector": ".publish-date, .date, time",
                "content_selector": ".article, article, .content, .main-content, .intro",
                "attachment_selector": ".article a, article a, .content a, .main-content a",
            },
        },
    },
    {
        "adapter_code": "web_list_detail",
        "code": "admissions_list_static",
        "name": "招生列表（静态）",
        "description": "适用于公开静态 HTML 招生列表页。",
        "template_schema_json": {
            "required_fields": ["name", "base_url", "start_urls_json"],
            "editable_sections": ["list", "detail", "scrapy", "playwright"],
        },
        "default_config_json": {
            "collection_domain": "admissions_notice",
            "probe_target": "admissions_notice",
            "list_pages": [],
            "aliases": [],
            "engine": "scrapy",
            "render_mode": "dynamic",
            "playwright": {
                "enabled": True,
                "wait_until": "networkidle",
                "timeout_ms": 30000,
            },
            "scrapy": {
                "concurrent_requests": 8,
                "download_timeout_seconds": 20,
                "obey_robots_txt": False,
            },
            "list": {
                "item_selector": ".notice-item",
                "title_selector": "a",
                "link_selector": "a",
                "date_selector": ".date",
            },
            "detail": {
                "title_selector": "h1, .notice-title",
                "publish_date_selector": ".publish-date, .date, time",
                "content_selector": ".article, article, .content",
                "attachment_selector": ".article a, article a, .content a",
            },
        },
    },
    {
        "adapter_code": "web_list_detail",
        "code": "news_list_static",
        "name": "新闻列表（静态）",
        "description": "适用于公开静态 HTML 新闻栏目页。",
        "template_schema_json": {
            "required_fields": ["name", "base_url", "start_urls_json"],
            "editable_sections": ["list", "detail", "scrapy", "playwright"],
        },
        "default_config_json": {
            "collection_domain": "news_center",
            "probe_target": "news_center",
            "list_pages": [],
            "aliases": [],
            "engine": "scrapy",
            "render_mode": "dynamic",
            "playwright": {
                "enabled": True,
                "wait_until": "networkidle",
                "timeout_ms": 30000,
            },
            "scrapy": {
                "concurrent_requests": 8,
                "download_timeout_seconds": 20,
                "obey_robots_txt": False,
            },
            "list": {
                "item_selector": ".news-item, .notice-item, li",
                "title_selector": "a",
                "link_selector": "a",
                "date_selector": ".date, time, .time",
            },
            "detail": {
                "title_selector": "h1, .news-title, .notice-title",
                "publish_date_selector": ".publish-date, .date, time",
                "content_selector": ".article, article, .content, .main-content",
                "attachment_selector": ".article a, article a, .content a, .main-content a",
            },
        },
    },
    {
        "adapter_code": "web_list_detail",
        "code": "admissions_list_ajax",
        "name": "招生列表（接口）",
        "description": "适用于 POST/JSON 包裹 HTML 的招生列表接口。",
        "template_schema_json": {
            "required_fields": ["name", "base_url", "start_urls_json"],
            "editable_sections": ["list", "detail", "scrapy", "playwright"],
        },
        "default_config_json": {
            "collection_domain": "admissions_notice",
            "probe_target": "admissions_notice",
            "list_pages": [],
            "aliases": [],
            "engine": "scrapy",
            "render_mode": "dynamic",
            "playwright": {
                "enabled": True,
                "wait_until": "networkidle",
                "timeout_ms": 30000,
            },
            "scrapy": {
                "concurrent_requests": 8,
                "download_timeout_seconds": 20,
                "obey_robots_txt": False,
            },
            "list": {
                "request_method": "POST",
                "request_data": {},
                "response_json_key": "content",
                "item_selector": "li",
                "title_selector": ".tit",
                "link_selector": "a",
                "date_selector": ".time",
            },
            "detail": {
                "title_selector": "h1, .notice-title, .xw-cont .tit",
                "publish_date_selector": ".publish-date, .date, .xw-cont .jj p",
                "content_selector": ".article, article, .content, .xw-cont .txt",
                "attachment_selector": ".article a, article a, .content a, .xw-cont .txt a",
            },
        },
    },
    {
        "adapter_code": "web_list_detail",
        "code": "news_list_ajax",
        "name": "新闻列表（接口）",
        "description": "适用于 POST/JSON 包裹 HTML 的新闻列表接口。",
        "template_schema_json": {
            "required_fields": ["name", "base_url", "start_urls_json"],
            "editable_sections": ["list", "detail", "scrapy", "playwright"],
        },
        "default_config_json": {
            "collection_domain": "news_center",
            "probe_target": "news_center",
            "list_pages": [],
            "aliases": [],
            "engine": "scrapy",
            "render_mode": "dynamic",
            "playwright": {
                "enabled": True,
                "wait_until": "networkidle",
                "timeout_ms": 30000,
            },
            "scrapy": {
                "concurrent_requests": 8,
                "download_timeout_seconds": 20,
                "obey_robots_txt": False,
            },
            "list": {
                "request_method": "POST",
                "request_data": {},
                "response_json_key": "content",
                "item_selector": "li",
                "title_selector": ".tit, a",
                "link_selector": "a",
                "date_selector": ".time, .date",
            },
            "detail": {
                "title_selector": "h1, .news-title, .notice-title, .xw-cont .tit",
                "publish_date_selector": ".publish-date, .date, .xw-cont .jj p",
                "content_selector": ".article, article, .content, .xw-cont .txt",
                "attachment_selector": ".article a, article a, .content a, .xw-cont .txt a",
            },
        },
    },
]


def sync_builtin_source_catalog(session: Session) -> dict[str, int]:
    created_adapters = 0
    updated_adapters = 0
    adapter_by_code: dict[str, SourceAdapter] = {}

    for payload in BUILTIN_ADAPTERS:
        existing = session.scalar(select(SourceAdapter).where(SourceAdapter.code == payload["code"]))
        if existing is None:
            existing = SourceAdapter(**payload)
            session.add(existing)
            created_adapters += 1
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
            updated_adapters += 1
        adapter_by_code[payload["code"]] = existing

    session.flush()

    created_templates = 0
    updated_templates = 0
    valid_adapter_codes = {item["code"] for item in BUILTIN_ADAPTERS}
    valid_codes = {item["code"] for item in BUILTIN_TEMPLATES}
    for payload in BUILTIN_TEMPLATES:
        adapter = adapter_by_code[payload["adapter_code"]]
        existing = session.scalar(select(SourceTemplate).where(SourceTemplate.code == payload["code"]))
        values = {
            "adapter_id": adapter.id,
            "code": payload["code"],
            "name": payload["name"],
            "description": payload.get("description"),
            "template_schema_json": payload.get("template_schema_json") or {},
            "default_config_json": payload.get("default_config_json") or {},
        }
        if existing is None:
            session.add(SourceTemplate(**values))
            created_templates += 1
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            updated_templates += 1

    for stale in session.scalars(select(SourceTemplate)):
        if stale.code not in valid_codes:
            session.delete(stale)
    for stale in session.scalars(select(SourceAdapter)):
        if stale.code not in valid_adapter_codes:
            session.delete(stale)

    session.commit()
    return {
        "created_adapters": created_adapters,
        "updated_adapters": updated_adapters,
        "created_templates": created_templates,
        "updated_templates": updated_templates,
    }
