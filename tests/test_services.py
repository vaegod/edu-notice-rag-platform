from __future__ import annotations

from app.models.source import Source
from app.services.admissions_tracks import infer_tracks_from_text
from app.services.crawler.dispatcher import CrawlRunnerDispatcher
from app.services.crawler.list_crawler import ListCrawler
from app.services.crawler.detail_parser import ParsedDetailPage
from app.services.document_postprocess import DocumentUniversityPostprocessService
from app.services.llm.extractor import ExtractedAdmission, AdmissionsExtractor
from app.services.llm.nl_parser import NLTaskParser
from app.services.llm.source_resolver import ResolvedSourceCandidate
from app.services.normalize.date_parser import parse_chinese_number, parse_date_string
from app.services.rule_validation_service import RuleValidationService
from app.services.source_knowledge import SourceKnowledgeRetriever
from app.services.source_resolution import LLMResolvedSourceValidator, SourceResolutionService
from app.services.source_discovery_agent import SourceDiscoveryAgentService
from app.services.source_config import split_source_extraction_rules, split_source_metadata
from app.services.source_state import source_can_run, source_readiness_message
from app.services.c9_source_knowledge import DEFAULT_C9_SOURCE_KNOWLEDGE
from app.services.task.validator import TaskValidator


def test_siliconflow_client_routes_models_by_biz_type(monkeypatch):
    from app.core.config import get_settings
    from app.services.llm.siliconflow_client import SiliconFlowClient

    monkeypatch.setenv("SILICONFLOW_TEXT_MODEL", "deepseek-ai/DeepSeek-V3.2")
    monkeypatch.setenv("SILICONFLOW_NL_PARSE_MODEL", "deepseek-ai/DeepSeek-R1")
    monkeypatch.setenv("SILICONFLOW_SOURCE_RESOLVE_MODEL", "deepseek-ai/DeepSeek-R1")
    get_settings.cache_clear()

    client = SiliconFlowClient()
    nl_payload = client._build_payload(
        system_prompt="system",
        user_prompt="user",
        biz_type="nl_parse",
        json_mode=True,
    )
    source_payload = client._build_payload(
        system_prompt="system",
        user_prompt="user",
        biz_type="source_resolve",
        json_mode=True,
    )
    extract_payload = client._build_payload(
        system_prompt="system",
        user_prompt="user",
        biz_type="admissions_extract",
        json_mode=True,
    )

    assert nl_payload["model"] == "deepseek-ai/DeepSeek-R1"
    assert source_payload["model"] == "deepseek-ai/DeepSeek-R1"
    assert extract_payload["model"] == "deepseek-ai/DeepSeek-V3.2"
    assert nl_payload["response_format"] == {"type": "json_object"}
    assert source_payload["response_format"] == {"type": "json_object"}
    assert extract_payload["response_format"] == {"type": "json_object"}

    get_settings.cache_clear()


def test_siliconflow_client_logs_actual_override_model(monkeypatch):
    from app.core.config import get_settings
    from app.services.llm.siliconflow_client import SiliconFlowClient

    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("SILICONFLOW_TEXT_MODEL", "deepseek-ai/DeepSeek-V3.2")
    monkeypatch.setenv("SILICONFLOW_SOURCE_RESOLVE_MODEL", "deepseek-ai/DeepSeek-R1")
    get_settings.cache_clear()

    captured_logs = []

    def fake_request_with_retry(self, *, client, payload):
        assert payload["model"] == "deepseek-ai/DeepSeek-R1"
        assert payload["response_format"] == {"type": "json_object"}
        return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

    def fake_persist_log(self, **kwargs):
        captured_logs.append(kwargs)

    monkeypatch.setattr(SiliconFlowClient, "_request_with_retry", fake_request_with_retry)
    monkeypatch.setattr(SiliconFlowClient, "_persist_log", fake_persist_log)

    result = SiliconFlowClient().chat_json(
        system_prompt="system",
        user_prompt="user",
        biz_type="source_resolve",
    )

    assert result == {"ok": True}
    assert captured_logs[0]["model_name"] == "deepseek-ai/DeepSeek-R1"

    get_settings.cache_clear()


def test_parse_date_string():
    parsed = parse_date_string("发布时间：2026年03月21日")
    assert parsed is not None
    assert parsed.isoformat() == "2026-03-21"


def test_parse_chinese_number():
    assert parse_chinese_number("两") == 2
    assert parse_chinese_number("十二") == 12


def test_infer_tracks_covers_six_admissions_categories():
    assert infer_tracks_from_text("2026年本科招生章程") == ["undergraduate"]
    assert infer_tracks_from_text("2026年硕士研究生招生复试工作安排") == ["graduate"]
    assert infer_tracks_from_text("2026年留学生招生报名通知") == ["international"]
    assert infer_tracks_from_text("2026年继续教育招生简章") == ["continuing_education"]
    assert infer_tracks_from_text("2026年MBA招生简章") == ["mba"]
    assert infer_tracks_from_text("2026年第二学士学位招生简章") == ["second_bachelor"]


def test_university_directory_service_exposes_c9_entrypoints():
    entry_by_name = {item["university_name"]: item for item in DEFAULT_C9_SOURCE_KNOWLEDGE["universities"]}
    assert entry_by_name["清华大学"]["official_homepage_url"] == "https://www.tsinghua.edu.cn/"
    assert entry_by_name["清华大学"]["entrypoint_candidates"][0] == "https://yz.tsinghua.edu.cn/"
    assert entry_by_name["北京大学"]["official_homepage_url"] == "https://www.pku.edu.cn/"
    assert entry_by_name["北京大学"]["entrypoint_candidates"][0] == "https://admission.pku.edu.cn/index.htm"


def test_source_knowledge_retriever_prefers_saved_source():
    saved_source = Source(
        id=7,
        name="清华大学 研究生招生公告列表",
        organization_name="清华大学",
        collection_domain="admissions_notice",
        base_url="https://custom.tsinghua.edu.cn/",
        start_urls_json=["https://custom.tsinghua.edu.cn/graduate/notices.html"],
        site_type="school",
        crawl_mode="dynamic",
        status="active",
        onboarding_status="validated",
        confidence_score=0.97,
        scope_json={"university_name": "清华大学", "admissions_levels": ["graduate"], "admissions_tracks": ["graduate"]},
        resolver_meta_json={"candidate_type": "list_page"},
        config_json={"institution": "清华大学", "source_kind": "list_page"},
    )

    class DummySession:
        def scalars(self, stmt):
            return [saved_source]

    hits = SourceKnowledgeRetriever().search(
        DummySession(),
        query="清华大学研究生招生公告",
        collection_domain="admissions_notice",
        desired_count=1,
    )

    assert hits[0].source_id == 7
    assert hits[0].source_url == "https://custom.tsinghua.edu.cn/graduate/notices.html"
    assert hits[0].source_origin == "saved_source"


def test_nl_parser_keeps_rule_based_values_when_llm_is_bad():
    parser = NLTaskParser()
    parsed = {
        "intent": "crawl_admissions",
        "institution": "上海交通大学",
        "department": "计算机学院",
        "topic": ["硕士", "招生"],
        "notice_type": ["硕士招生"],
        "time_range": {"relative": "last_2_weeks"},
        "filters": {},
        "output_mode": "default",
        "result_limit": 10,
    }
    llm_result = {
        "intent": "get_info",
        "institution": "??????",
        "topic": ["博士", "招生"],
        "notice_type": ["胡乱类型", "博士招生"],
        "result_limit": 3,
    }

    merged = parser._merge_llm_result(parsed, llm_result)
    assert merged["intent"] == "crawl_admissions"
    assert merged["institution"] == "上海交通大学"
    assert merged["topic"] == ["博士", "招生"]
    assert merged["notice_type"] == ["博士招生"]
    assert merged["result_limit"] == 3


def test_nl_parser_prefers_llm_intent_when_available():
    parser = NLTaskParser()
    parsed = {
        "intent": "search_admissions",
        "intent_locked": False,
        "collection_domain": "admissions_notice",
        "university_name": "清华大学",
        "desired_source_count": None,
        "institution": "清华大学",
        "department": None,
        "topic": ["招生"],
        "notice_type": [],
        "time_range": {},
        "filters": {"deadline_only": False},
        "output_mode": "default",
        "result_limit": 10,
        "admissions_levels": ["graduate"],
        "requires_source_discovery": False,
        "resolved_homepage_url": "https://www.tsinghua.edu.cn/",
        "homepage_url": "https://www.tsinghua.edu.cn/",
    }
    llm_result = {
        "intent": "data_source_retrieval",
        "collection_domain": "admissions",
        "university_name": "清华大学",
        "topic": ["研究生招生"],
        "notice_type": [],
        "admissions_levels": ["研究生"],
        "requires_source_discovery": True,
    }

    merged = parser._merge_llm_result(parsed, llm_result)
    assert merged["intent"] == "discover_sources"
    assert merged["collection_domain"] == "admissions_notice"
    assert merged["topic"] == ["研究生招生"]
    assert merged["admissions_levels"] == ["graduate"]
    assert merged["requires_source_discovery"] is True


def test_nl_parser_keeps_c9_university_from_raw_query_when_llm_disagrees():
    parser = NLTaskParser()
    parsed = {
        "intent": "search_admissions",
        "intent_locked": False,
        "raw_query": "最近哈尔滨工业大学的研究生招生情况怎样？",
        "collection_domain": "admissions_notice",
        "university_name": "哈尔滨工业大学",
        "institution": "哈尔滨工业大学",
        "department": None,
        "topic": ["招生"],
        "notice_type": [],
        "time_range": {},
        "filters": {"deadline_only": False},
        "output_mode": "default",
        "result_limit": 10,
        "admissions_levels": ["graduate"],
        "admissions_tracks": ["graduate"],
        "requires_source_discovery": False,
        "resolved_homepage_url": None,
        "homepage_url": None,
    }
    llm_result = {
        "intent": "search_documents",
        "collection_domain": "admissions_notice",
        "university_name": "中国科学技术大学",
        "institution": "中国科学技术大学",
        "topic": ["研究生招生情况"],
    }

    merged = parser._merge_llm_result(parsed, llm_result)
    assert merged["university_name"] == "哈尔滨工业大学"
    assert merged["institution"] == "哈尔滨工业大学"


def test_notice_extractor_prefers_llm_output_but_keeps_page_title():
    extractor = AdmissionsExtractor()
    fallback_document = ExtractedAdmission(
        doc_type="硕士招生",
        title="北京大学计算机学院2026年硕士研究生复试工作安排",
        publish_date="2026-03-20",
        deadline="2026-03-25",
        department="计算机学院",
        target_audience=["考生"],
        keywords=["硕士", "招生"],
        summary="规则摘要",
        attachment_urls=["https://example.edu.cn/files/admission.pdf"],
        event_time=None,
        event_location=None,
    )
    detail_page = ParsedDetailPage(
        url="https://example.edu.cn/notice/admission.html",
        title=fallback_document.title,
        publish_date="2026-03-20",
        raw_html="<html></html>",
        raw_text="硕士招生复试安排",
        status_code=200,
        attachments=[],
    )
    llm_result = {
        "doc_type": "复试",
        "title": "模型改写标题",
        "summary": "模型摘要",
        "keywords": ["复试"],
        "target_audience": ["本科生"],
    }

    merged = extractor._merge_llm_result(fallback_document, llm_result, detail_page)
    assert merged.doc_type == "复试"
    assert merged.title == "北京大学计算机学院2026年硕士研究生复试工作安排"
    assert merged.summary == "模型摘要"
    assert merged.keywords == ["复试", "硕士", "招生"]
    assert merged.target_audience == ["本科生", "考生"]


def test_notice_extractor_does_not_call_fallback_when_llm_succeeds(monkeypatch):
    class FakeLLMClient:
        enabled = True

        def chat_json(self, **kwargs):
            return {
                "doc_type": "复试",
                "title": "模型标题",
                "publish_date": "2026-03-20",
                "department": "计算机学院",
                "content_category": "招生公告",
                "institution_name": "北京大学",
                "keywords": ["复试"],
                "summary": "模型摘要",
                "target_audience": ["考生"],
                "attachment_urls": [],
                "extra": {},
            }

    extractor = AdmissionsExtractor(llm_client=FakeLLMClient())
    extractor.crawl4ai = type("FakeCrawl4AI", (), {"enabled": False})()
    extractor.settings.mock_llm_enabled = False

    def fail_fallback(*args, **kwargs):
        raise AssertionError("fallback extractor should not run on the primary LLM path")

    monkeypatch.setattr(extractor, "_fallback_extract", fail_fallback)

    source = Source(
        name="北京大学 研究生招生",
        organization_name="北京大学",
        collection_domain="admissions_notice",
        base_url="https://example.edu.cn",
        site_type="school",
        crawl_mode="static",
        status="active",
        config_json={},
        scope_json={"university_name": "北京大学", "admissions_levels": ["graduate"]},
    )
    detail_page = ParsedDetailPage(
        url="https://example.edu.cn/notice/admission.html",
        title="北京大学计算机学院2026年硕士研究生复试工作安排",
        publish_date="2026-03-20",
        raw_html="<html></html>",
        raw_text="北京大学计算机学院发布2026年硕士研究生复试安排。",
        status_code=200,
        attachments=[],
    )

    extracted = extractor.extract(source=source, detail_page=detail_page, fallback_title="后备标题")
    assert extracted.doc_type == "复试"
    assert extracted.title == "北京大学计算机学院2026年硕士研究生复试工作安排"
    assert extracted.summary == "模型摘要"


def test_resolved_source_name_includes_university_when_title_is_generic():
    validator = LLMResolvedSourceValidator()
    normalized = validator._build_normalized_source(
        ResolvedSourceCandidate(
            university_name="清华大学",
            collection_domain="admissions_notice",
            homepage_url="https://www.tsinghua.edu.cn/",
            source_url="https://yz.tsinghua.edu.cn/",
            source_title="研究生招生 \ue888",
            source_kind="channel_page",
            admissions_levels=["graduate"],
            confidence_score=0.95,
            reason="测试数据源命名",
        )
    )

    assert normalized["name"] == "清华大学 研究生招生"


def test_llm_resolved_source_validator_retries_alternate_scheme(monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        if url == "http://example.edu.cn/graduate/":
            return None
        if url == "https://example.edu.cn/graduate/":
            return {
                "url": url,
                "status_code": 200,
                "raw_html": "<html><head><title>清华大学研究生招生</title></head><body><a href='/notice/1.html'>研究生招生动态</a><a href='/notice/2.html'>招生公告</a></body></html>",
                "raw_text": "清华大学研究生招生 研究生招生动态 招生公告",
                "attachments": [],
                "links": [
                    {"title": "研究生招生动态", "url": "https://example.edu.cn/notice/1.html", "snippet": "研究生招生动态"},
                    {"title": "招生公告", "url": "https://example.edu.cn/notice/2.html", "snippet": "招生公告"},
                ],
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    validator = LLMResolvedSourceValidator()
    validated = validator.validate(
        ResolvedSourceCandidate(
            university_name="清华大学",
            collection_domain="admissions_notice",
            homepage_url="https://example.edu.cn/",
            source_url="http://example.edu.cn/graduate/",
            source_title="研究生招生",
            source_kind="list_page",
            admissions_levels=["graduate"],
            admissions_tracks=["graduate"],
            confidence_score=0.9,
            reason="测试协议兜底。",
        )
    )

    assert validated.report.success_rate == 1.0
    assert validated.candidate.source_url == "https://example.edu.cn/graduate/"


def test_non_c9_user_entrypoint_channel_page_passes_with_notice_evidence(monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        return {
            "url": url,
            "status_code": 200,
            "raw_html": """
            <html><head><title>青海大学研究生招生</title></head><body>
              <ul>
                <li><a href="/info/1001/2001.htm">2026年硕士研究生招生复试工作安排</a><span>2026-03-20</span></li>
                <li><a href="/info/1001/2002.htm">青海大学2026年硕士研究生招生调剂公告</a><span>2026-04-02</span></li>
              </ul>
            </body></html>
            """,
            "raw_text": "青海大学研究生招生 2026年硕士研究生招生复试工作安排 2026-03-20 青海大学2026年硕士研究生招生调剂公告 2026-04-02",
            "attachments": [],
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

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    validator = LLMResolvedSourceValidator()
    validated = validator.validate(
        ResolvedSourceCandidate(
            university_name="青海大学",
            collection_domain="admissions_notice",
            homepage_url="https://www.qhu.edu.cn/zsjy/zs/yjszs/index.htm",
            source_url="https://www.qhu.edu.cn/zsjy/zs/yjszs/index.htm",
            source_title="研究生招生",
            source_kind="channel_page",
            admissions_levels=["graduate"],
            admissions_tracks=["graduate"],
            candidate_type="site_home",
            confidence_score=0.88,
            reason="用户提供的研究生招生入口，页面包含复试与调剂公告列表。",
        )
    )

    assert validated.report.success_rate == 1.0
    assert validated.report.issues == []


def test_non_c9_user_entrypoint_channel_page_still_rejects_without_notice_evidence(monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        return {
            "url": url,
            "status_code": 200,
            "raw_html": "<html><head><title>青海大学研究生院</title></head><body>研究生院简介 学位管理 导师队伍</body></html>",
            "raw_text": "青海大学研究生院 研究生院简介 学位管理 导师队伍",
            "attachments": [],
            "links": [],
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    validator = LLMResolvedSourceValidator()
    validated = validator.validate(
        ResolvedSourceCandidate(
            university_name="青海大学",
            collection_domain="admissions_notice",
            homepage_url="https://www.qhu.edu.cn/yjs/index.htm",
            source_url="https://www.qhu.edu.cn/yjs/index.htm",
            source_title="研究生院",
            source_kind="channel_page",
            admissions_levels=["graduate"],
            admissions_tracks=["graduate"],
            candidate_type="site_home",
            confidence_score=0.7,
            reason="用户提供入口但页面缺少公告证据。",
        )
    )

    assert validated.report.success_rate == 0.0
    assert any("招生入口页" in issue for issue in validated.report.issues)


def test_llm_resolved_source_validator_rejects_news_detail_page(monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        return {
            "url": url,
            "status_code": 200,
            "raw_html": "<html><head><title>喜报！示例大学获奖</title></head><body><article>新闻正文</article></body></html>",
            "raw_text": "新闻正文",
            "attachments": [],
            "links": [],
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    validator = LLMResolvedSourceValidator()
    validated = validator.validate(
        ResolvedSourceCandidate(
            university_name="示例大学",
            collection_domain="news_center",
            homepage_url="https://example.edu.cn/",
            source_url="https://example.edu.cn/info/1003/73554.htm",
            source_title="喜报！示例大学获奖",
            source_kind="list_page",
            confidence_score=0.8,
            reason="测试新闻详情页误判。",
        )
    )

    assert validated.report.success_rate == 0.0
    assert any("单篇新闻详情页" in issue for issue in validated.report.issues)


def test_source_discovery_agent_classifies_detail_and_file_pages():
    agent = SourceDiscoveryAgentService(
        resolver=None,
        validator=None,
        provider=None,
        find_existing_source=lambda *args, **kwargs: None,
        upsert_source=lambda *args, **kwargs: None,
    )
    assert agent._guess_candidate_type(
        title="2026年研究生招生复试工作安排",
        url="https://example.edu.cn/info/1003/73554.htm",
        snippet="招生复试安排",
    ) == "detail_page"
    assert agent._guess_candidate_type(
        title="招生简章 PDF",
        url="https://example.edu.cn/files/brochure.pdf",
        snippet="招生简章附件",
    ) == "file_page"
    assert agent._guess_candidate_type(
        title="硕士招生",
        url="https://yzb.sjtu.edu.cn/zkxx/sszs",
        snippet="硕士招生",
    ) == "list_page"
    assert agent._guess_candidate_type(
        title="2026-04-15 浙江大学研究生招生政策现场咨询会（西安专场）报名通知",
        url="http://www.grs.zju.edu.cn/yjszs/2026/0415/c28498a3152180/page.htm",
        snippet="研究生招生通知",
    ) == "detail_page"


def test_source_discovery_agent_prefers_primary_tracks_for_broad_admissions():
    agent = SourceDiscoveryAgentService(
        resolver=None,
        validator=None,
        provider=None,
        find_existing_source=lambda *args, **kwargs: None,
        upsert_source=lambda *args, **kwargs: None,
    )
    graduate = agent._track_match_score(
        requested_tracks=[],
        candidate_tracks=["graduate"],
        requested_levels=[],
        candidate_levels=["graduate"],
    )
    undergraduate = agent._track_match_score(
        requested_tracks=[],
        candidate_tracks=["undergraduate"],
        requested_levels=[],
        candidate_levels=["undergraduate"],
    )
    mba = agent._track_match_score(
        requested_tracks=[],
        candidate_tracks=["mba"],
        requested_levels=[],
        candidate_levels=["graduate"],
    )
    international = agent._track_match_score(
        requested_tracks=[],
        candidate_tracks=["international"],
        requested_levels=[],
        candidate_levels=[],
    )

    assert graduate > mba
    assert graduate > undergraduate
    assert undergraduate == international


def test_source_discovery_agent_expands_site_home_into_list_candidates():
    class FakeProvider:
        def discover_candidate_links(self, homepage_url, max_links=30):
            assert homepage_url == "https://example.edu.cn/graduate/"
            return {
                "links": [
                    {
                        "title": "研究生招生动态",
                        "url": "https://example.edu.cn/graduate/notices.html",
                        "snippet": "研究生招生动态",
                    }
                ]
            }

        def fetch_page_payload(self, *, url: str, crawl_mode="static", method="GET", data=None, json_payload=None, headers=None):
            return {
                "url": url,
                "raw_html": "<html><body><a href='/graduate/notices.html'>研究生招生动态</a></body></html>",
                "raw_text": "研究生招生动态",
                "attachments": [],
                "status_code": 200,
            }

    agent = SourceDiscoveryAgentService(
        resolver=None,
        validator=None,
        provider=FakeProvider(),
        find_existing_source=lambda *args, **kwargs: None,
        upsert_source=lambda *args, **kwargs: None,
    )
    expanded = agent._expand_candidate(
        {
            "homepage_url": "https://example.edu.cn/",
            "source_url": "https://example.edu.cn/graduate/",
            "source_title": "研究生招生",
            "source_kind": "channel_page",
            "candidate_type": "site_home",
            "admissions_levels": ["graduate"],
            "admissions_tracks": ["graduate"],
        },
        admissions_levels=["graduate"],
        admissions_tracks=["graduate"],
        trace=[],
    )

    assert expanded
    assert expanded[0]["source_url"] == "https://example.edu.cn/graduate/notices.html"


def test_source_discovery_agent_sorts_valid_list_results_before_rejected_entry_pages():
    agent = SourceDiscoveryAgentService(
        resolver=None,
        validator=None,
        provider=None,
        find_existing_source=lambda *args, **kwargs: None,
        upsert_source=lambda *args, **kwargs: None,
    )
    results = [
        {
            "source_url": "https://example.edu.cn/graduate/",
            "validation_status": "invalid",
            "saved": False,
            "reject_reason_code": "entry_page_rejected",
            "candidate_type": "site_home",
            "track": "graduate",
            "admissions_tracks": ["graduate"],
            "source_suitability_score": 78.0,
            "topic_relevance_score": 52.0,
        },
        {
            "source_url": "https://example.edu.cn/graduate/notices.html",
            "validation_status": "valid",
            "saved": True,
            "reject_reason_code": None,
            "candidate_type": "list_page",
            "track": "graduate",
            "admissions_tracks": ["graduate"],
            "source_suitability_score": 88.0,
            "topic_relevance_score": 60.0,
        },
    ]

    sorted_results = agent._sort_result_payloads(
        results,
        admissions_levels=[],
        admissions_tracks=[],
    )

    assert sorted_results[0]["source_url"] == "https://example.edu.cn/graduate/notices.html"


def test_dispatcher_skips_invalid_detail_links(monkeypatch):
    dispatcher = CrawlRunnerDispatcher()

    source = Source(
        name="示例大学新闻中心",
        organization_name="示例大学",
        collection_domain="news_center",
        base_url="https://example.edu.cn",
        site_type="school",
        crawl_mode="static",
        status="active",
        config_json={},
    )

    dispatcher.list_crawler.crawl = lambda source, page_limit=1: [
        type("ListItem", (), {"title": "无效链接", "detail_url": "mailto:test@example.edu.cn", "publish_date": None, "source_site": source.name})(),
        type("ListItem", (), {"title": "有效新闻", "detail_url": "https://example.edu.cn/news/1.html", "publish_date": None, "source_site": source.name})(),
    ]

    def fake_parse(source, url):
        if url.startswith("mailto:"):
            raise RuntimeError("Crawl4AI failed to fetch detail page: mailto:test@example.edu.cn")
        return ParsedDetailPage(
            url=url,
            title="有效新闻",
            publish_date="2026-03-25",
            raw_html="<html></html>",
            raw_text="正文",
            status_code=200,
            attachments=[],
        )

    dispatcher.detail_parser.parse = fake_parse
    result = dispatcher.fetch_notices(source)
    assert result.discovered == 2
    assert len(result.records) == 1
    assert result.errors


def test_list_crawler_supports_json_wrapped_html(monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        assert method == "POST"
        return {
            "url": url,
            "status_code": 200,
            "raw_html": '{"content":"<li><a href=\\"https://example.edu.cn/notice/1.html\\"><div class=\\"time\\">17 2026-03</div><div class=\\"tit\\">测试通知</div></a></li>"}',
            "raw_text": "",
            "attachments": [],
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    crawler = ListCrawler()
    source = Source(
        name="测试源",
        base_url="https://example.edu.cn",
        site_type="college",
        crawl_mode="static",
        status="active",
        config_json={
            "list_pages": ["https://example.edu.cn/ajax"],
            "list": {
                "request_method": "POST",
                "request_data": {"page": 1},
                "response_json_key": "content",
                "item_selector": "li",
                "title_selector": ".tit",
                "link_selector": "a",
                "date_selector": ".time",
            },
        },
    )

    items = crawler.crawl(source)
    assert len(items) == 1
    assert items[0].title == "测试通知"
    assert items[0].detail_url == "https://example.edu.cn/notice/1.html"


def test_list_crawler_filters_mailto_links(monkeypatch):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        return {
            "url": url,
            "status_code": 200,
            "raw_html": """
            <html><body>
              <a href="mailto:test@example.edu.cn">邮箱</a>
              <a href="https://example.edu.cn/news/1.html">新闻一</a>
            </body></html>
            """,
            "raw_text": "邮箱 新闻一",
            "attachments": [],
            "links": [
                {"title": "邮箱", "url": "mailto:test@example.edu.cn", "snippet": "邮箱"},
                {"title": "新闻一", "url": "https://example.edu.cn/news/1.html", "snippet": "新闻一"},
            ],
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    crawler = ListCrawler()
    source = Source(
        name="示例大学新闻中心",
        organization_name="示例大学",
        collection_domain="news_center",
        base_url="https://example.edu.cn",
        site_type="school",
        crawl_mode="static",
        status="active",
        start_urls_json=["https://example.edu.cn/news/list.html"],
        config_json={},
    )

    items = crawler.crawl(source)
    assert len(items) == 1
    assert items[0].detail_url == "https://example.edu.cn/news/1.html"


def test_document_university_postprocess_falls_back_to_source_organization():
    service = DocumentUniversityPostprocessService()
    source = Source(
        name="示例大学新闻中心",
        organization_name="示例大学",
        collection_domain="news_center",
        base_url="https://example.edu.cn",
        site_type="school",
        crawl_mode="static",
        status="active",
        config_json={},
        scope_json={"university_name": "示例大学"},
    )
    raw_page = type(
        "RawPageStub",
        (),
        {
            "title": "示例大学举行人工智能论坛",
            "raw_text": "示例大学新闻中心报道人工智能论坛顺利举行。",
            "source": source,
        },
    )()
    document = type(
        "DocumentStub",
        (),
        {
            "institution_name": None,
            "title": "示例大学举行人工智能论坛",
            "raw_page": raw_page,
            "source_url": "https://example.edu.cn/news/1.html",
        },
    )()

    result = service._fallback_classification(document)
    assert result.institution_name == "示例大学"

def test_task_validator_matches_sources_by_exact_structured_fields():
    validator = TaskValidator()
    source = Source(
        name="北京物资学院计算机与人工智能学院招生信息",
        organization_name="北京物资学院",
        collection_domain="admissions_notice",
        base_url="https://example.edu.cn",
        site_type="college",
        crawl_mode="static",
        status="active",
        onboarding_status="validated",
        config_json={
            "institution": "北京物资学院",
            "department": "计算机与人工智能学院",
            "aliases": ["北京物资学院"],
        },
    )

    matches = validator.match_sources(
        session=type("DummySession", (), {"scalars": lambda self, stmt: [source]})(),
        institution="北京物资学院",
        department="计算机与人工智能学院",
        collection_domain="admissions_notice",
    )

    assert [item.name for item in matches] == ["北京物资学院计算机与人工智能学院招生信息"]


def test_task_validator_does_not_fuzzy_match_partial_institution():
    validator = TaskValidator()
    source = Source(
        name="北京大学计算机学院招生信息",
        organization_name="北京大学",
        collection_domain="admissions_notice",
        base_url="https://example.edu.cn",
        site_type="college",
        crawl_mode="static",
        status="active",
        onboarding_status="validated",
        config_json={
            "institution": "北京大学",
            "department": "计算机学院",
            "aliases": ["北大计算机学院"],
        },
    )

    matches = validator.match_sources(
        session=type("DummySession", (), {"scalars": lambda self, stmt: [source]})(),
        institution="北京",
        department=None,
        collection_domain="admissions_notice",
    )

    assert matches == []


def test_find_existing_source_does_not_reuse_different_school_profile_url():
    service = SourceResolutionService()
    source = Source(
        id=1,
        name="示例大学 学校简介",
        organization_name="示例大学",
        collection_domain="school_profile",
        base_url="https://example.edu.cn",
        start_urls_json=["https://example.edu.cn/overview.html"],
        site_type="school",
        crawl_mode="static",
        status="active",
        onboarding_status="validated",
        config_json={},
        scope_json={"university_name": "示例大学"},
    )

    class DummySession:
        def scalars(self, stmt):
            return [source]

    exact = service.find_existing_source(
        DummySession(),
        university_name="示例大学",
        collection_domain="school_profile",
        source_url="https://example.edu.cn/overview.html",
    )
    different = service.find_existing_source(
        DummySession(),
        university_name="示例大学",
        collection_domain="school_profile",
        source_url="https://example.edu.cn/intro.html",
    )

    assert exact is source
    assert different is None


def test_rule_validation_rejects_section_pages(monkeypatch):
    from app.services.crawler.base import FetchResult, PageFetcher

    list_html = """
    <html>
      <body>
        <table class="tb1_1_">
          <tr><td><a href="https://example.edu.cn/roles.html">科研处岗位分工</a></td></tr>
          <tr><td><a href="https://example.edu.cn/project.html">科研项目管理</a></td></tr>
        </table>
      </body>
    </html>
    """.strip()
    detail_html = """
    <html>
      <head><title>科研处岗位分工-北京物资学院科研处</title></head>
      <body>
        <table><tr><td>这里是科研处岗位分工介绍页面，不是招生页。</td></tr></table>
      </body>
    </html>
    """.strip()

    def fake_fetch(self, url, crawl_mode="static", *, method="GET", data=None, json_payload=None, headers=None, timeout_seconds=None):
        if url.endswith("list.html"):
            return FetchResult(url=url, status_code=200, text=list_html)
        return FetchResult(url=url, status_code=200, text=detail_html)

    monkeypatch.setattr(PageFetcher, "fetch", fake_fetch)
    source = Source(
        name="北京物资学院计算机与人工智能学院招生信息",
        organization_name="北京物资学院",
        base_url="https://example.edu.cn",
        site_type="college",
        crawl_mode="static",
        status="active",
        start_urls_json=["https://example.edu.cn/list.html"],
        config_json={
            "institution": "北京物资学院",
            "department": "",
            "probe_target": "admissions",
            "admissions_tracks": ["master"],
            "list": {
                "item_selector": ".tb1_1_ > tr",
                "title_selector": "a",
                "link_selector": "a",
                "date_selector": None,
            },
            "detail": {
                "title_selector": "title",
                "publish_date_selector": ".publish-date",
                "content_selector": "td",
                "attachment_selector": "td a",
            },
        },
    )

    report = RuleValidationService().validate_source(source, sample_count=2)
    assert report.success_rate == 0.0
    assert report.issues


def test_source_config_can_be_split_into_metadata_and_rules():
    config = {
        "institution": "北京物资学院",
        "department": "计算机与人工智能学院",
        "aliases": ["信息学院"],
        "engine": "scrapy",
        "list_pages": ["https://example.edu.cn/list.html"],
        "list": {"item_selector": ".news"},
        "detail": {"content_selector": "#vsb_content"},
    }
    metadata = split_source_metadata(config)
    rules = split_source_extraction_rules(config)
    assert metadata == {
        "institution": "北京物资学院",
        "department": "计算机与人工智能学院",
        "aliases": ["信息学院"],
        "collection_domain": "admissions_notice",
        "schedule_hours": None,
        "hot_source": None,
    }
    assert rules["list_pages"] == ["https://example.edu.cn/list.html"]
    assert rules["list"]["item_selector"] == ".news"
    assert rules["detail"]["content_selector"] == "#vsb_content"


def test_source_must_be_validated_before_running():
    source = Source(
        name="待验证源",
        base_url="https://example.edu.cn",
        site_type="college",
        crawl_mode="static",
        status="active",
        onboarding_status="needs_review",
        config_json={},
    )
    assert source_can_run(source) is False
    assert "尚未通过验证" in source_readiness_message(source)


def test_crawl_runner_dispatcher_uses_direct_fetch(monkeypatch, fixture_texts):
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch_page_payload(self, *, url: str, crawl_mode="dynamic", method="GET", data=None, json_payload=None, headers=None):
        if url.endswith("list.html"):
            return {"url": url, "status_code": 200, "raw_html": fixture_texts["list"], "raw_text": "", "attachments": []}
        if url.endswith("lecture.html"):
            return {"url": url, "status_code": 200, "raw_html": fixture_texts["lecture"], "raw_text": "", "attachments": []}
        if url.endswith("apply.html"):
            return {"url": url, "status_code": 200, "raw_html": fixture_texts["apply"], "raw_text": "", "attachments": []}
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    source = Source(
        name="测试源",
        base_url="https://example.edu.cn",
        site_type="college",
        crawl_mode="static",
        status="active",
        start_urls_json=["https://example.edu.cn/list.html"],
        config_json={
            "list": {
                "item_selector": ".notice-item",
                "title_selector": "a",
                "link_selector": "a",
                "date_selector": ".date",
            },
            "detail": {
                "title_selector": "h1.notice-title",
                "publish_date_selector": ".publish-date",
                "content_selector": ".article",
                "attachment_selector": ".article a",
            },
        },
    )

    result = CrawlRunnerDispatcher().fetch_notices(source)
    assert result.engine == "direct"
    assert result.discovered == 2
    assert len(result.records) == 2
