from __future__ import annotations

import time
import json

from fastapi.testclient import TestClient


def build_source_payload(*, ajax: bool = False, collection_domain: str = "admissions_notice") -> dict:
    list_pages = ["https://example.edu.cn/ajax.html"] if ajax else ["https://example.edu.cn/list.html"]
    list_config = {
        "item_selector": ".notice-item",
        "title_selector": "a",
        "link_selector": "a",
        "date_selector": ".date",
    }
    if ajax:
        list_config = {
            "request_method": "POST",
            "request_data": {"page": 1},
            "response_json_key": "content",
            "item_selector": "li",
            "title_selector": ".tit",
            "link_selector": "a",
            "date_selector": ".time",
        }
    return {
        "name": "上海交通大学计算机学院",
        "base_url": "https://example.edu.cn",
        "site_type": "college",
        "crawl_mode": "dynamic",
        "status": "active",
        "source_type": collection_domain,
        "collection_domain": collection_domain,
        "config_json": {
            "collection_domain": collection_domain,
            "institution": "上海交通大学",
            "department": "计算机学院",
            "aliases": ["上海交大", "上海交通大学"],
            "engine": "scrapy",
            "probe_target": collection_domain,
            "list_pages": list_pages,
            "list": list_config,
            "detail": {
                "title_selector": ".notice-title, h1.notice-title",
                "publish_date_selector": ".publish-date",
                "content_selector": ".article",
                "attachment_selector": ".article a",
            },
        },
    }


def build_news_source_payload() -> dict:
    payload = build_source_payload(collection_domain="news_center")
    payload["name"] = "示例大学新闻中心"
    payload["config_json"]["institution"] = "示例大学"
    payload["config_json"]["department"] = None
    payload["config_json"]["probe_target"] = "news_center"
    payload["config_json"]["list_pages"] = ["https://example.edu.cn/news/list.html"]
    payload["config_json"]["list"] = {
        "item_selector": ".news-item",
        "title_selector": "a",
        "link_selector": "a",
        "date_selector": ".date",
    }
    payload["config_json"]["detail"] = {
        "title_selector": ".news-title, h1.news-title",
        "publish_date_selector": ".publish-date",
        "content_selector": ".article",
        "attachment_selector": ".article a",
    }
    return payload


def wait_for_task_completion(client, task_id: int, timeout_seconds: float = 5.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        payload = resp.json()
        if payload["status"] in {"success", "partial_success", "failed"}:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Task {task_id} did not finish in time")


def test_source_crud(client):
    create_resp = client.post(
        "/api/v1/sources",
        json=build_source_payload(collection_domain="news_center"),
    )
    assert create_resp.status_code == 201
    source = create_resp.json()
    assert source["collection_domain"] == "news_center"
    assert source["start_urls_json"] == ["https://example.edu.cn/list.html"]

    list_resp = client.get("/api/v1/sources")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    detail_resp = client.get(f"/api/v1/sources/{source['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == source["id"]

    delete_resp = client.delete(f"/api/v1/sources/{source['id']}")
    assert delete_resp.status_code == 204
    assert client.get(f"/api/v1/sources/{source['id']}").status_code == 404


def test_source_catalog_and_validation(client, mock_fetcher):
    adapters_resp = client.get("/api/v1/sources/adapters")
    assert adapters_resp.status_code == 200
    adapters = adapters_resp.json()
    assert any(item["code"] == "web_list_detail" for item in adapters)
    assert any(item["code"] == "web_profile_page" for item in adapters)

    templates_resp = client.get("/api/v1/sources/templates")
    assert templates_resp.status_code == 200
    templates = templates_resp.json()
    assert any(item["code"] == "admissions_list_static" for item in templates)
    assert any(item["code"] == "school_profile_page" for item in templates)
    assert any(item["code"] == "news_list_static" for item in templates)

    source = client.post("/api/v1/sources", json=build_source_payload()).json()
    validate_saved_resp = client.post(f"/api/v1/sources/{source['id']}/validate")
    assert validate_saved_resp.status_code == 200
    saved_report = validate_saved_resp.json()
    assert saved_report["discovered_count"] == 2
    refreshed = client.get(f"/api/v1/sources/{source['id']}").json()
    assert refreshed["confidence_score"] is not None
    assert refreshed["onboarding_status"] in {"validated", "needs_review"}


def test_llm_probe_resolves_and_reuses_source(client, mock_homepage_fetcher):
    probe_payload = {
        "organization_name": "清华大学",
        "university_name": "清华大学",
        "url": "https://example.edu.cn/",
        "collection_domain": "admissions_notice",
    }
    first_resp = client.post("/api/v1/sources/probe", json=probe_payload)
    assert first_resp.status_code == 200
    first = first_resp.json()
    assert first["collection_domain"] == "admissions_notice"
    assert first["resolved_source_url"] == "https://example.edu.cn/graduate/notices.html"
    assert first["used_existing_source"] is False
    assert first["source_id"] >= 1

    second_resp = client.post("/api/v1/sources/probe", json=probe_payload)
    assert second_resp.status_code == 200
    second = second_resp.json()
    assert second["used_existing_source"] is True
    assert second["source_id"] == first["source_id"]


def test_nl_execute_rejects_school_profile_scope(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "请采集示例大学学校概况"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["validation_status"] == "invalid"
    assert payload["task_ids"] == []
    assert "只支持 C9 高校校级研究生招生公告" in payload["answer"]


def test_nl_execute_rejects_news_center_scope(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "请采集示例大学新闻中心最近信息"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["validation_status"] == "invalid"
    assert payload["task_ids"] == []
    assert "只支持 C9 高校校级研究生招生公告" in payload["answer"]


def test_nl_execute_can_discover_multiple_sources_without_crawling(client, mock_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供C9高校研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["desired_source_count"] == 9
    assert payload["task_ids"] == []
    assert len(payload["resolved_sources"]) == 9
    assert payload["debug"]["source_resolution_strategy"] in {"live_discovery", "mixed"}
    assert payload["debug"]["bootstrap_strategy"] == "official_directory"
    assert payload["resolved_sources"][0]["source_url"].startswith("https://")
    assert payload["resolved_sources"][0]["source_origin"] == "discovered_by_agent"
    assert payload["resolved_sources"][0]["health_status"] == "healthy"


def test_nl_execute_can_discover_sources_from_provided_homepage(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "提供清华大学研究生招生公告的真实数据源",
            "homepage_url": "https://example.edu.cn/",
        },
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["homepage_url"] == "https://example.edu.cn/"
    assert payload["debug"]["candidate_provider"] in {"agent", "crawl4ai"}
    assert payload["debug"]["raw_candidate_count"] >= 0
    assert len(payload["resolved_sources"]) >= 1
    assert any(
        item["collection_domain"] == "admissions_notice"
        for item in payload["resolved_sources"]
    )


def test_discover_sources_returns_refined_list_candidate(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "提供清华大学研究生招生公告的真实数据源",
            "homepage_url": "https://example.edu.cn/",
        },
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["resolved_sources"]
    assert payload["resolved_sources"][0]["source_url"] == "https://example.edu.cn/graduate/notices.html"
    assert payload["resolved_sources"][0]["saved"] is True


def test_discover_sources_uses_official_directory_without_homepage(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供清华大学研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["debug"]["source_resolution_strategy"] == "live_discovery"
    assert payload["debug"]["bootstrap_strategy"] == "official_directory"
    assert payload["debug"]["entrypoint_url"] == "https://yz.tsinghua.edu.cn/"
    assert payload["resolved_sources"][0]["source_url"].startswith("https://yz.tsinghua.edu.cn/")
    assert payload["resolved_sources"][0]["source_origin"] == "discovered_by_agent"
    assert payload["resolved_sources"][0]["saved"] is True


def test_non_c9_discovery_requires_provided_homepage(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供安徽工业大学研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["validation_status"] == "invalid"
    assert payload["resolved_sources"] == []
    assert "非 C9 高校" in payload["answer"]
    assert "URL" in payload["answer"]


def test_non_c9_discovery_with_provided_homepage_uses_controlled_site_discovery(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "提供安徽工业大学研究生招生公告的真实数据源",
            "homepage_url": "https://example.edu.cn/",
        },
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["validation_status"] == "valid"
    assert payload["intent"] == "discover_sources"
    assert payload["debug"]["bootstrap_strategy"] == "request_homepage"
    assert payload["resolved_sources"]
    assert payload["resolved_sources"][0]["university_name"] == "安徽工业大学"
    assert payload["resolved_sources"][0]["source_url"] == "https://example.edu.cn/graduate/notices.html"
    assert payload["resolved_sources"][0]["saved"] is True


def test_non_c9_discovery_accepts_user_provided_channel_page_with_notice_evidence(client, monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    qhu_url = "https://www.qhu.edu.cn/zsjy/zs/yjszs/index.htm"

    def fake_discover_candidate_links(self, homepage_url, max_links=20):
        return {
            "provider": "crawl4ai",
            "links": [
                {"title": "研究生招生", "url": qhu_url, "snippet": "青海大学研究生招生", "domain": "www.qhu.edu.cn"},
                {
                    "title": "2026年硕士研究生招生复试工作安排",
                    "url": "https://www.qhu.edu.cn/info/1001/2001.htm",
                    "snippet": "复试工作安排",
                    "domain": "www.qhu.edu.cn",
                },
                {
                    "title": "青海大学2026年硕士研究生招生调剂公告",
                    "url": "https://www.qhu.edu.cn/info/1001/2002.htm",
                    "snippet": "调剂公告",
                    "domain": "www.qhu.edu.cn",
                },
            ],
        }

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        if url.endswith("/sitemap.xml"):
            return None
        if url == qhu_url:
            return {
                "url": url,
                "raw_html": """
                <html><head><title>青海大学研究生招生</title></head><body>
                  <a href="/info/1001/2001.htm">2026年硕士研究生招生复试工作安排</a>
                  <a href="/info/1001/2002.htm">青海大学2026年硕士研究生招生调剂公告</a>
                </body></html>
                """,
                "raw_text": "青海大学研究生招生 2026年硕士研究生招生复试工作安排 2026-03-20 青海大学2026年硕士研究生招生调剂公告 2026-04-02",
                "attachments": [],
                "status_code": 200,
                "links": [
                    {
                        "title": "2026年硕士研究生招生复试工作安排",
                        "url": "https://www.qhu.edu.cn/info/1001/2001.htm",
                        "snippet": "复试工作安排",
                    },
                    {
                        "title": "青海大学2026年硕士研究生招生调剂公告",
                        "url": "https://www.qhu.edu.cn/info/1001/2002.htm",
                        "snippet": "调剂公告",
                    },
                ],
            }
        return {
            "url": url,
            "raw_html": "<html><body><article>单篇详情</article></body></html>",
            "raw_text": "单篇详情",
            "attachments": [],
            "status_code": 200,
            "links": [],
        }

    monkeypatch.setattr(Crawl4AIProvider, "discover_candidate_links", fake_discover_candidate_links)
    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)

    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "提供青海大学研究生招生公告的真实数据源",
            "homepage_url": qhu_url,
        },
    )

    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["validation_status"] == "valid"
    assert payload["intent"] == "discover_sources"
    assert payload["resolved_sources"][0]["university_name"] == "青海大学"
    assert payload["resolved_sources"][0]["source_url"] == qhu_url
    assert payload["resolved_sources"][0]["saved"] is True


def test_nl_crawl_returns_rag_answer_from_collected_documents(client, mock_homepage_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "采集清华大学研究生招生公告",
            "homepage_url": "https://example.edu.cn/",
        },
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "crawl_source"
    assert payload["task_ids"]
    assert payload["documents"]
    assert payload["citations"]
    assert payload["retrieved_documents"]
    assert "基于本次采集文档的证据回答" in payload["answer"]
    assert "以上回答仅基于当前召回的已采集文档" in payload["answer"]
    assert any(item["name"] == "answer_with_evidence" and item["status"] == "success" for item in payload["workflow_steps"])


def test_nl_crawl_uses_saved_source_knowledge_and_returns_confidence_note(client, mock_homepage_fetcher):
    source_payload = build_source_payload()
    source_payload["name"] = "清华大学研究生招生动态"
    source_payload["organization_name"] = "清华大学"
    source_payload["base_url"] = "https://example.edu.cn/"
    source_payload["config_json"]["institution"] = "清华大学"
    source_payload["config_json"]["aliases"] = ["清华", "清华大学"]
    source_payload["config_json"]["list_pages"] = ["https://example.edu.cn/graduate/notices.html"]
    source_payload["scope_json"] = {
        "university_name": "清华大学",
        "admissions_levels": ["graduate"],
        "admissions_tracks": ["graduate"],
    }
    source_payload["resolver_meta_json"] = {"candidate_type": "list_page"}
    create_resp = client.post("/api/v1/sources", json=source_payload)
    assert create_resp.status_code == 201

    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "采集清华大学研究生招生公告"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["debug"]["source_resolution_strategy"] == "source_knowledge"
    assert payload["debug"]["source_knowledge_hits"][0]["source_origin"] == "saved_source"
    assert "source_selected_from_knowledge_base" in payload["confidence_notes"]
    assert payload["documents"]
    assert payload["citations"]


def test_nl_crawl_hits_json_synced_c9_source_knowledge(tmp_path, monkeypatch, mock_homepage_fetcher):
    from app.core.config import get_settings
    from app.main import create_app
    from app.core.database import reset_database_state

    database_path = tmp_path / "knowledge_test.db"
    knowledge_path = tmp_path / "c9_source_knowledge.json"
    knowledge_path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-05-05T00:00:00+00:00",
                "scope": "graduate_admissions",
                "universities": [
                    {
                        "university_name": "浙江大学",
                        "normalized_name": "浙江大学",
                        "scope": "graduate_admissions",
                        "official_homepage_url": "https://www.zju.edu.cn/",
                        "entrypoint_candidates": ["https://example.edu.cn/graduate/"],
                        "selected_entrypoint_url": "https://example.edu.cn/graduate/",
                        "selected_source_url": "https://example.edu.cn/graduate/notices.html",
                        "source_title": "研究生招生动态",
                        "source_kind": "list_page",
                        "candidate_type": "list_page",
                        "health_status": "healthy",
                        "confidence_score": 0.92,
                        "checked_at": "2026-05-05T00:00:00+00:00",
                        "validation_evidence": {
                            "normalized_source": {
                                "name": "浙江大学 研究生招生动态",
                                "organization_name": "浙江大学",
                                "source_type": "admissions_notice",
                                "collection_domain": "admissions_notice",
                                "source_origin": "seed",
                                "base_url": "https://example.edu.cn/graduate/",
                                "start_urls_json": ["https://example.edu.cn/graduate/notices.html"],
                                "site_type": "school",
                                "crawl_mode": "static",
                                "status": "active",
                                "onboarding_status": "validated",
                                "confidence_score": 0.92,
                                "entrypoint_url": "https://example.edu.cn/graduate/",
                                "health_status": "healthy",
                                "scope_json": {
                                    "university_name": "浙江大学",
                                    "admissions_levels": ["graduate"],
                                    "admissions_tracks": ["graduate"],
                                    "selected_track": "graduate"
                                },
                                "resolver_meta_json": {
                                    "candidate_type": "list_page",
                                    "source_kind": "list_page"
                                },
                                "config_json": {
                                    "collection_domain": "admissions_notice",
                                    "institution": "浙江大学",
                                    "aliases": ["浙江大学"],
                                    "source_kind": "list_page",
                                    "candidate_type": "list_page",
                                    "admissions_levels": ["graduate"],
                                    "admissions_tracks": ["graduate"],
                                    "selected_track": "graduate",
                                    "list_pages": ["https://example.edu.cn/graduate/notices.html"],
                                    "list": {
                                        "item_selector": ".notice-item",
                                        "title_selector": "a",
                                        "link_selector": "a",
                                        "date_selector": ".date"
                                    },
                                    "detail": {
                                        "title_selector": "h1, .notice-title",
                                        "publish_date_selector": ".publish-date, .date, time",
                                        "content_selector": ".article, article, .content",
                                        "attachment_selector": ".article a, article a, .content a"
                                    }
                                }
                            }
                        },
                        "fallback_notes": []
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("AUTO_SEED_REAL_SOURCES", "false")
    monkeypatch.setenv("AUTO_SYNC_C9_SOURCE_KNOWLEDGE", "true")
    monkeypatch.setenv("C9_SOURCE_KNOWLEDGE_PATH", str(knowledge_path))
    monkeypatch.setenv("SOURCE_KNOWLEDGE_MAX_AGE_DAYS", "3650")
    reset_database_state()
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as test_client:
        execute_resp = test_client.post(
            "/api/v1/nl/execute",
            json={"query": "采集浙江大学的研究生招生信息"},
        )
    reset_database_state()

    payload = execute_resp.json()
    assert execute_resp.status_code == 200
    assert payload["intent"] == "crawl_source"
    assert payload["debug"]["source_resolution_strategy"] == "source_knowledge"
    assert payload["debug"]["source_knowledge_hits"][0]["source_id"] is not None
    assert "source_selected_from_knowledge_base" in payload["confidence_notes"]


def test_nl_discovery_rediscover_stale_source_instead_of_reusing_it(client, mock_homepage_fetcher):
    source_payload = build_source_payload()
    source_payload["name"] = "清华大学旧研究生招生入口"
    source_payload["organization_name"] = "清华大学"
    source_payload["base_url"] = "https://yz.tsinghua.edu.cn/"
    source_payload["start_urls_json"] = ["https://yz.tsinghua.edu.cn/graduate/legacy.html"]
    source_payload["config_json"]["institution"] = "清华大学"
    source_payload["config_json"]["aliases"] = ["清华", "清华大学"]
    source_payload["config_json"]["list_pages"] = ["https://yz.tsinghua.edu.cn/graduate/legacy.html"]
    source_payload["scope_json"] = {
        "university_name": "清华大学",
        "admissions_levels": ["graduate"],
        "admissions_tracks": ["graduate"],
    }
    source_payload["resolver_meta_json"] = {"candidate_type": "list_page"}
    source_payload["health_status"] = "stale"
    create_resp = client.post("/api/v1/sources", json=source_payload)
    assert create_resp.status_code == 201

    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供清华大学研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["debug"]["source_resolution_strategy"] == "live_discovery"
    assert payload["debug"]["bootstrap_strategy"] == "official_directory"
    assert payload["debug"]["rediscovery_triggered"] is True
    assert payload["resolved_sources"][0]["source_origin"] == "discovered_by_agent"
    assert payload["resolved_sources"][0]["source_url"].startswith("https://yz.tsinghua.edu.cn/")


def test_discover_sources_always_researches(client, mock_homepage_fetcher):
    first = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供清华大学研究生招生公告的真实数据源", "homepage_url": "https://example.edu.cn/"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供清华大学研究生招生公告的真实数据源", "homepage_url": "https://example.edu.cn/"},
    )
    assert second.status_code == 200
    assert first.json()["debug"]["cache_hit"] is False
    assert second.json()["debug"]["cache_hit"] is False
    assert first.json()["resolved_sources"][0]["source_url"] == "https://example.edu.cn/graduate/notices.html"


def test_c9_scope_rejects_undergraduate_discovery(client, mock_homepage_fetcher):
    undergraduate_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供清华大学本科招生公告的真实数据源", "homepage_url": "https://example.edu.cn/"},
    )
    assert undergraduate_resp.status_code == 200
    undergraduate_payload = undergraduate_resp.json()
    assert undergraduate_payload["validation_status"] == "invalid"
    assert undergraduate_payload["resolved_sources"] == []
    assert "只支持校级研究生招生公告" in undergraduate_payload["answer"]


def test_discovered_sources_are_returned_even_if_only_partially_valid(client, monkeypatch):
    from app.services.source_resolution import SourceResolutionService

    def fake_discover(
        self,
        session,
        *,
        query,
        collection_domain,
        desired_count=10,
        homepage_url=None,
        admissions_levels=None,
        admissions_tracks=None,
    ):
        return ([
            {
                "source_id": None,
                "saved": False,
                "validation_status": "unchecked",
                "validation_message": "结构化校验未通过，但已保留模型原始结果",
                "university_name": "北京大学",
                "collection_domain": collection_domain,
                "homepage_url": "https://www.pku.edu.cn",
                "source_url": "https://admission.pku.edu.cn",
                "source_title": "北京大学招生网",
                "source_kind": "list_page",
                "admissions_levels": ["graduate"],
                "confidence_score": 0.88,
                "reason": "模型返回的官方招生网。",
            }
        ], {
            "candidate_provider": "mock",
            "raw_candidate_count": 1,
            "raw_candidates": [],
            "llm_selected_candidates": [],
        })

    monkeypatch.setattr(SourceResolutionService, "discover_sources_from_query", fake_discover)
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供北京大学研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert len(payload["resolved_sources"]) == 1
    assert payload["resolved_sources"][0]["source_url"] == "https://admission.pku.edu.cn"


def test_discover_sources_handles_duplicate_generic_source_names(client, mock_homepage_fetcher, monkeypatch):
    existing_payload = build_source_payload()
    existing_payload["name"] = "研究生招生"
    existing_payload["organization_name"] = "安徽工业大学"
    existing_payload["base_url"] = "https://www.ahut.edu.cn/"
    existing_payload["start_urls_json"] = ["https://www.ahut.edu.cn/graduate/notices.html"]
    existing_payload["config_json"]["institution"] = "安徽工业大学"
    existing_payload["config_json"]["list_pages"] = ["https://www.ahut.edu.cn/graduate/notices.html"]
    create_resp = client.post("/api/v1/sources", json=existing_payload)
    assert create_resp.status_code == 201

    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "提供清华大学研究生招生公告的真实数据源",
            "homepage_url": "https://example.edu.cn/",
        },
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["resolved_sources"][0]["saved"] is True

    sources_resp = client.get("/api/v1/sources")
    assert sources_resp.status_code == 200
    tsinghua_sources = [
        item for item in sources_resp.json()
        if item.get("organization_name") == "清华大学"
    ]
    assert len(tsinghua_sources) >= 1
    assert len({item["name"] for item in tsinghua_sources}) == len(tsinghua_sources)
    assert all(item["name"].startswith("清华大学 ") for item in tsinghua_sources)
    assert all("研究生招生" != item["name"] for item in tsinghua_sources)


def test_discover_sources_timeout_returns_explained_error(client, monkeypatch):
    from app.services.source_resolution import SourceResolutionService

    def fake_discover(self, session, *, query, collection_domain, desired_count=10, admissions_levels=None, admissions_tracks=None):
        raise TimeoutError("source_resolve timeout")

    monkeypatch.setattr(SourceResolutionService, "discover_sources_from_query", fake_discover)
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供C9高校研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["validation_status"] == "invalid"
    assert "timeout" in payload["answer"]


def test_validate_candidate_can_save_resolved_source(client, mock_homepage_fetcher):
    response = client.post(
        "/api/v1/sources/validate-candidate",
        json={
            "university_name": "清华大学",
            "collection_domain": "admissions_notice",
            "homepage_url": "https://example.edu.cn/",
            "source_url": "https://example.edu.cn/graduate/notices.html",
            "source_title": "研究生招生动态",
            "source_kind": "list_page",
            "admissions_levels": ["graduate"],
            "admissions_tracks": ["graduate"],
            "confidence_score": 0.95,
            "reason": "测试候选校验并保存。",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["validation_status"] == "valid"
    assert payload["saved"] is True
    assert payload["source_id"] is not None

    source_resp = client.get(f"/api/v1/sources/{payload['source_id']}")
    assert source_resp.status_code == 200
    source_payload = source_resp.json()
    assert source_payload["organization_name"] == "清华大学"
    assert source_payload["start_urls_json"][0] == "https://example.edu.cn/graduate/notices.html"


def test_validate_candidate_can_save_non_c9_source_when_homepage_is_provided(client, mock_homepage_fetcher):
    response = client.post(
        "/api/v1/sources/validate-candidate",
        json={
            "university_name": "安徽工业大学",
            "collection_domain": "admissions_notice",
            "homepage_url": "https://example.edu.cn/",
            "source_url": "https://example.edu.cn/graduate/notices.html",
            "source_title": "研究生招生动态",
            "source_kind": "list_page",
            "admissions_levels": ["graduate"],
            "admissions_tracks": ["graduate"],
            "confidence_score": 0.88,
            "reason": "测试非 C9 高校在用户提供官方入口时可校验保存。",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["validation_status"] == "valid"
    assert payload["saved"] is True
    assert payload["source_id"] is not None

    source_resp = client.get(f"/api/v1/sources/{payload['source_id']}")
    assert source_resp.status_code == 200
    source_payload = source_resp.json()
    assert source_payload["organization_name"] == "安徽工业大学"
    assert source_payload["entrypoint_url"] == "https://example.edu.cn/"


def test_discover_sources_returns_graceful_error_when_site_has_no_candidates(client, mock_homepage_fetcher, monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider
    monkeypatch.setattr(
        Crawl4AIProvider,
        "discover_candidate_links",
        lambda self, homepage_url, max_links=20: {"provider": "crawl4ai", "links": []},
    )

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        if url == "https://example.edu.cn/":
            return {
                "url": url,
                "raw_html": "<html><body></body></html>",
                "raw_text": "",
                "attachments": [],
                "status_code": 200,
            }
        if url == "https://example.edu.cn/graduate/notices.html":
            return {
                "url": url,
                "raw_html": """
                <html>
                  <head><title>研究生招生动态</title></head>
                  <body><a href="/graduate/notices/retest.html">2026年硕士研究生招生复试工作安排</a></body>
                </html>
                """,
                "raw_text": "研究生招生动态 2026年硕士研究生招生复试工作安排",
                "attachments": [],
                "status_code": 200,
            }
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)

    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={
            "query": "提供清华大学研究生招生公告的真实数据源",
            "homepage_url": "https://example.edu.cn/",
        },
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["resolved_sources"] == []
    assert payload["failure_reason"] == "官网内未发现可用候选。"
    assert payload["debug"]["llm_fallback_candidates"] == []
    assert any(step["name"] == "candidate_shortfall" and step["status"] == "failed" for step in payload["agent_trace"])


def test_nl_parse_locks_explicit_source_discovery_intent(client):
    parse_resp = client.post(
        "/api/v1/nl/parse",
        json={"query": "提供C9高校的研究生招生公告的真实数据源"},
    )
    assert parse_resp.status_code == 200
    payload = parse_resp.json()
    assert payload["intent"] == "discover_sources"
    assert payload["collection_domain"] == "admissions_notice"
    assert payload["desired_source_count"] == 9


def test_manual_task_crawl_and_query_admissions(client, mock_fetcher):
    source_id = client.post("/api/v1/sources", json=build_source_payload()).json()["id"]
    client.post(f"/api/v1/sources/{source_id}/validate")

    task_resp = client.post(
        "/api/v1/tasks",
        json={"source_id": source_id, "task_payload": {}, "execute_immediately": True},
    )
    assert task_resp.status_code == 201
    task = wait_for_task_completion(client, task_resp.json()["id"])
    assert task["status"] in {"success", "partial_success"}
    assert task["last_result"]["inserted"] == 2
    assert task["collection_domain"] == "admissions_notice"

    ask_resp = client.post("/api/v1/ask", json={"query": "最近有哪些上海交大招生信息"})
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert len(ask_data["documents"]) >= 1
    assert ask_data["citations"]
    assert ask_data["citations"][0]["document_id"] is not None
    assert ask_data["citations"][0]["source_url"].startswith("https://")
    assert "以上回答仅基于当前召回的已采集文档" in ask_data["answer"]


def test_ask_relaxes_over_specific_admissions_topic(client, mock_fetcher, monkeypatch):
    from app.api.routes import ask as ask_route

    source_id = client.post("/api/v1/sources", json=build_source_payload()).json()["id"]
    client.post(f"/api/v1/sources/{source_id}/validate")

    task_resp = client.post(
        "/api/v1/tasks",
        json={"source_id": source_id, "task_payload": {}, "execute_immediately": True},
    )
    assert task_resp.status_code == 201
    wait_for_task_completion(client, task_resp.json()["id"])

    original_parse_nl = ask_route.orchestrator.parse_nl

    def fake_parse_nl(session, query, homepage_url=None):
        parsed, validation = original_parse_nl(session, query, homepage_url=homepage_url)
        payload = dict(validation.normalized_payload or parsed)
        payload["topic"] = ["完全不存在的细分词"]
        parsed["topic"] = payload["topic"]
        validation.normalized_payload = payload
        return parsed, validation

    monkeypatch.setattr(ask_route.orchestrator, "parse_nl", fake_parse_nl)
    ask_resp = client.post("/api/v1/ask", json={"query": "最近上海交通大学的研究生招生情况怎样？"})
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert ask_data["documents"]
    assert ask_data["citations"]
    assert "relaxed_topic_filter" in ask_data["confidence_notes"]


def test_ask_coerces_non_crawl_question_when_llm_marks_crawl(client, mock_fetcher, monkeypatch):
    from app.api.routes import ask as ask_route

    source_id = client.post("/api/v1/sources", json=build_source_payload()).json()["id"]
    client.post(f"/api/v1/sources/{source_id}/validate")

    task_resp = client.post(
        "/api/v1/tasks",
        json={"source_id": source_id, "task_payload": {}, "execute_immediately": True},
    )
    assert task_resp.status_code == 201
    wait_for_task_completion(client, task_resp.json()["id"])

    original_parse_nl = ask_route.orchestrator.parse_nl

    def fake_parse_nl(session, query, homepage_url=None):
        parsed, validation = original_parse_nl(session, query, homepage_url=homepage_url)
        payload = dict(validation.normalized_payload or parsed)
        payload["intent"] = "crawl_admissions"
        parsed["intent"] = "crawl_admissions"
        validation.normalized_payload = payload
        return parsed, validation

    monkeypatch.setattr(ask_route.orchestrator, "parse_nl", fake_parse_nl)
    ask_resp = client.post("/api/v1/ask", json={"query": "最近上海交通大学的研究生招生情况怎样？"})
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert ask_data["intent"] == "search_documents"
    assert ask_data["documents"]
    assert ask_data["citations"]
    assert "ask_intent_coerced_to_query" in ask_data["confidence_notes"]


def test_ask_coerces_non_discovery_question_and_recovers_school_scope(client, mock_fetcher, monkeypatch):
    from app.api.routes import ask as ask_route

    source_id = client.post("/api/v1/sources", json=build_source_payload()).json()["id"]
    client.post(f"/api/v1/sources/{source_id}/validate")

    task_resp = client.post(
        "/api/v1/tasks",
        json={"source_id": source_id, "task_payload": {}, "execute_immediately": True},
    )
    assert task_resp.status_code == 201
    wait_for_task_completion(client, task_resp.json()["id"])

    original_parse_nl = ask_route.orchestrator.parse_nl

    def fake_parse_nl(session, query, homepage_url=None):
        parsed, validation = original_parse_nl(session, query, homepage_url=homepage_url)
        payload = dict(validation.normalized_payload or parsed)
        payload["intent"] = "discover_sources"
        payload["university_name"] = None
        payload["institution"] = None
        payload["topic"] = []
        parsed.update({
            "intent": "discover_sources",
            "university_name": None,
            "institution": None,
            "topic": [],
        })
        validation.normalized_payload = payload
        return parsed, validation

    monkeypatch.setattr(ask_route.orchestrator, "parse_nl", fake_parse_nl)
    ask_resp = client.post("/api/v1/ask", json={"query": "最近上海交通大学的研究生招生情况怎样？"})
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert ask_data["intent"] == "search_documents"
    assert ask_data["documents"]
    assert ask_data["citations"]
    assert {item["institution_name"] for item in ask_data["documents"]} == {"上海交通大学"}
    assert "ask_intent_coerced_to_query" in ask_data["confidence_notes"]


def test_ask_still_rejects_explicit_crawl_request(client):
    ask_resp = client.post("/api/v1/ask", json={"query": "采集清华大学研究生招生公告"})
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert ask_data["documents"] == []
    assert "ask_endpoint_rejects_crawl_intents" in ask_data["confidence_notes"]


def test_ask_refuses_when_no_evidence(client):
    ask_resp = client.post("/api/v1/ask", json={"query": "这所学校明年的奖学金金额是多少？"})
    assert ask_resp.status_code == 200
    payload = ask_resp.json()
    assert "未找到足够证据" in payload["answer"]
    assert payload["citations"] == []
    assert payload["retrieved_documents"] == []


def test_nl_execute_returns_workflow_steps(client, mock_fetcher):
    execute_resp = client.post(
        "/api/v1/nl/execute",
        json={"query": "提供C9高校研究生招生公告的真实数据源"},
    )
    assert execute_resp.status_code == 200
    payload = execute_resp.json()
    step_names = [item["name"] for item in payload["workflow_steps"]]
    assert step_names == [
        "parse_intent",
        "resolve_source",
        "validate_candidate",
        "create_task",
        "crawl",
        "extract",
        "index_for_qa",
        "answer_with_evidence",
    ]
    assert payload["workflow_steps"][0]["status"] == "success"
    assert any(item["name"] == "resolve_source" and item["status"] == "success" for item in payload["workflow_steps"])


def test_documents_can_be_postprocessed_by_university_with_deepseek(client, mock_homepage_fetcher, monkeypatch):
    from app.api.routes import documents as documents_route
    from app.core.database import get_session_factory
    from app.models.document import Document

    class FakeLLMClient:
        enabled = True

        def chat_json(self, **kwargs):
            return {
                "institution_name": "示例大学",
                "reason": "标题和正文都明确指向示例大学。",
                "confidence_score": 0.93,
            }

    source_id = client.post(
        "/api/v1/sources",
        json=build_news_source_payload(),
    ).json()["id"]
    task_resp = client.post(
        "/api/v1/tasks",
        json={"source_id": source_id, "task_payload": {}, "execute_immediately": True},
    )
    assert task_resp.status_code == 201
    wait_for_task_completion(client, task_resp.json()["id"])

    monkeypatch.setattr(documents_route.document_postprocess_service, "llm_client", FakeLLMClient())
    monkeypatch.setattr(documents_route.document_postprocess_service.settings, "mock_llm_enabled", False)

    session = get_session_factory()()
    try:
        for document in session.query(Document).all():
            document.institution_name = None
        session.commit()
    finally:
        session.close()

    postprocess_resp = client.post(
        "/api/v1/documents/postprocess/university-classify",
        json={"collection_domain": "news_center", "only_missing": True, "limit": 10},
    )
    assert postprocess_resp.status_code == 200
    payload = postprocess_resp.json()
    assert payload["matched"] == 2
    assert payload["updated"] == 2
    assert all(item["after_institution_name"] == "示例大学" for item in payload["items"])

    docs_resp = client.get("/api/v1/documents", params={"collection_domain": "news_center"})
    assert docs_resp.status_code == 200
    docs = docs_resp.json()["items"]
    assert all(item["institution_name"] == "示例大学" for item in docs)

    grouped_resp = client.get("/api/v1/documents/by-university", params={"collection_domain": "news_center"})
    assert grouped_resp.status_code == 200
    grouped = grouped_resp.json()
    assert grouped[0]["institution_name"] == "示例大学"
    assert grouped[0]["total"] == 2

    filtered_resp = client.get("/api/v1/documents", params={"institution_name": "示例大学", "collection_domain": "news_center"})
    assert filtered_resp.status_code == 200
    assert filtered_resp.json()["total"] == 2


def test_documents_and_tasks_support_collection_domain_filters(client, mock_homepage_fetcher):
    admissions_payload = build_source_payload()
    admissions_payload["config_json"]["list_pages"] = ["https://example.edu.cn/graduate/notices.html"]
    admissions_source_id = client.post("/api/v1/sources", json=admissions_payload).json()["id"]
    news_source_id = client.post("/api/v1/sources", json=build_news_source_payload()).json()["id"]
    admissions_task = client.post(
        "/api/v1/tasks",
        json={"source_id": admissions_source_id, "task_payload": {}, "execute_immediately": True},
    ).json()
    news_task = client.post(
        "/api/v1/tasks",
        json={"source_id": news_source_id, "task_payload": {}, "execute_immediately": True},
    ).json()
    wait_for_task_completion(client, admissions_task["id"])
    wait_for_task_completion(client, news_task["id"])

    task_resp = client.get("/api/v1/tasks", params={"collection_domain": "news_center"})
    assert task_resp.status_code == 200
    task_payload = task_resp.json()
    assert task_payload["total"] >= 1
    assert all(item["collection_domain"] == "news_center" for item in task_payload["items"])

    docs_resp = client.get("/api/v1/documents", params={"collection_domain": "admissions_notice"})
    assert docs_resp.status_code == 200
    docs_payload = docs_resp.json()
    assert docs_payload["total"] >= 1
    assert all(item["collection_domain"] == "admissions_notice" for item in docs_payload["items"])


def test_root_page_and_dashboard(client, mock_fetcher):
    source_id = client.post("/api/v1/sources", json=build_source_payload()).json()["id"]
    client.post(f"/api/v1/sources/{source_id}/validate")
    task_resp = client.post(
        "/api/v1/tasks",
        json={"source_id": source_id, "task_payload": {}, "execute_immediately": True},
    )
    wait_for_task_completion(client, task_resp.json()["id"])

    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert "C9 研招公告采集工作台" in root_resp.text
    assert "证据问答平台" in root_resp.text
    assert "/static/admin.js" in root_resp.text

    overview_resp = client.get("/api/v1/dashboard/overview")
    assert overview_resp.status_code == 200
    overview = overview_resp.json()
    assert overview["counts"]["total_sources"] == 1
    assert overview["counts"]["total_documents"] == 2
    assert overview["runtime"]["app_version"]
    assert overview["runtime"]["process_started_at"]
    assert overview["runtime"]["backend_build_at"]
    assert "healthy_knowledge_sources" in overview["runtime"]
    assert "knowledge_generated_at" in overview["runtime"]
    assert len(overview["recent_tasks"]) >= 1
    assert len(overview["recent_documents"]) >= 1


def test_agents_md_documents_project_rules():
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    content = (project_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "university public information discovery" in content
    assert "Evidence-grounded RAG answers" in content
    assert "Run tests" in content or "pytest" in content
    assert "Do not reintroduce Redis" in content
