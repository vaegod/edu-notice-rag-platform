from __future__ import annotations

import gc
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient


TEST_DB_PATH = Path(__file__).resolve().parent / "test_app.db"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["MOCK_LLM_ENABLED"] = "true"
os.environ["AUTO_SEED_REAL_SOURCES"] = "false"
os.environ["AUTO_SYNC_C9_SOURCE_KNOWLEDGE"] = "false"
os.environ["SCRAPY_OBEY_ROBOTS_TXT"] = "false"
os.environ["TRAFILATURA_ENABLED"] = "false"
os.environ["SCRAPY_PLAYWRIGHT_ENABLED"] = "false"


def _cleanup_test_db() -> None:
    from app.core.database import reset_database_state

    reset_database_state()
    gc.collect()
    if not TEST_DB_PATH.exists():
        return
    last_error = None
    for _ in range(10):
        try:
            TEST_DB_PATH.unlink(missing_ok=True)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.1)
            gc.collect()
            reset_database_state()
    if last_error is not None:
        raise last_error


@pytest.fixture
def client():
    from app.core.config import get_settings

    _cleanup_test_db()
    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    _cleanup_test_db()


@pytest.fixture
def fixture_texts() -> dict[str, str]:
    fixture_dir = Path(__file__).resolve().parent / "fixtures"
    return {
        "homepage": (fixture_dir / "homepage.html").read_text(encoding="utf-8"),
        "list": (fixture_dir / "list_page.html").read_text(encoding="utf-8"),
        "lecture": (fixture_dir / "detail_lecture.html").read_text(encoding="utf-8"),
        "apply": (fixture_dir / "detail_apply.html").read_text(encoding="utf-8"),
        "undergraduate_list": """
        <html><body><ul class="notice-list">
          <li class="notice-item"><a href="/admissions/undergraduate/charter.html">2026年本科招生章程</a><span class="date">2026-03-20</span></li>
          <li class="notice-item"><a href="/admissions/undergraduate/plan.html">2026年强基计划报名通知</a><span class="date">2026-03-18</span></li>
        </ul></body></html>
        """.strip(),
        "master_list": """
        <html><body><ul class="notice-list">
          <li class="notice-item"><a href="/admissions/master/retest.html">2026年硕士研究生招生复试工作安排</a><span class="date">2026-03-20</span></li>
          <li class="notice-item"><a href="/admissions/master/camp.html">2026年研究生夏令营报名通知</a><span class="date">2026-03-18</span></li>
        </ul></body></html>
        """.strip(),
        "doctor_list": """
        <html><body><ul class="notice-list">
          <li class="notice-item"><a href="/admissions/doctor/brochure.html">2026年博士研究生招生简章</a><span class="date">2026-03-19</span></li>
          <li class="notice-item"><a href="/admissions/doctor/apply.html">2026年博士申请考核工作通知</a><span class="date">2026-03-17</span></li>
        </ul></body></html>
        """.strip(),
        "undergraduate_detail": """
        <html><body><h1 class="notice-title">2026年本科招生章程</h1><div class="publish-date">发布时间：2026-03-20</div><div class="article"><p>示例大学2026年本科招生章程正式发布，欢迎本科考生报考。</p><p>招生办联系电话：021-12345678。</p><a href="/files/undergraduate.pdf">本科招生章程.pdf</a></div></body></html>
        """.strip(),
        "undergraduate_plan_detail": """
        <html><body><h1 class="notice-title">2026年强基计划报名通知</h1><div class="publish-date">发布时间：2026-03-18</div><div class="article"><p>示例大学本科招生办发布强基计划报名通知。</p><p>请考生按要求完成报名。</p></div></body></html>
        """.strip(),
        "master_detail": """
        <html><body><h1 class="notice-title">2026年硕士研究生招生复试工作安排</h1><div class="publish-date">发布时间：2026-03-20</div><div class="article"><p>示例大学研究生院发布硕士研究生招生复试安排。</p><p>请考生按时参加复试。</p></div></body></html>
        """.strip(),
        "master_camp_detail": """
        <html><body><h1 class="notice-title">2026年研究生夏令营报名通知</h1><div class="publish-date">发布时间：2026-03-18</div><div class="article"><p>示例大学研究生招生办公室现启动夏令营报名。</p><p>欢迎优秀本科生报名参加。</p></div></body></html>
        """.strip(),
        "doctor_detail": """
        <html><body><h1 class="notice-title">2026年博士研究生招生简章</h1><div class="publish-date">发布时间：2026-03-19</div><div class="article"><p>示例大学博士研究生招生简章发布。</p><p>欢迎符合条件的考生报考博士。</p></div></body></html>
        """.strip(),
        "doctor_apply_detail": """
        <html><body><h1 class="notice-title">2026年博士申请考核工作通知</h1><div class="publish-date">发布时间：2026-03-17</div><div class="article"><p>示例大学博士招生办公室发布申请考核工作通知。</p><p>请博士考生按要求提交材料。</p></div></body></html>
        """.strip(),
        "school_profile": """
        <html><body><h1>示例大学学校概况</h1><div class="article"><p>示例大学是一所以工科见长的高校，设有多个学院和研究机构。</p><p>学校师资力量雄厚，办学历史悠久。</p></div></body></html>
        """.strip(),
        "news_list": """
        <html><body><ul class="news-list">
          <li class="news-item"><a href="/news/1.html">示例大学举行人工智能论坛</a><span class="date">2026-03-20</span></li>
          <li class="news-item"><a href="/news/2.html">示例大学发布科研创新成果</a><span class="date">2026-03-19</span></li>
        </ul></body></html>
        """.strip(),
        "news_detail_1": """
        <html><body><h1 class="news-title">示例大学举行人工智能论坛</h1><div class="publish-date">发布时间：2026-03-20</div><div class="article"><p>示例大学新闻中心报道人工智能论坛顺利举行。</p></div></body></html>
        """.strip(),
        "news_detail_2": """
        <html><body><h1 class="news-title">示例大学发布科研创新成果</h1><div class="publish-date">发布时间：2026-03-19</div><div class="article"><p>示例大学新闻网报道最新科研创新成果。</p></div></body></html>
        """.strip(),
        "admissions_generic_list": """
        <html><body><ul class="notice-list">
          <li class="notice-item"><a href="/admissions/undergraduate/charter.html">2026年本科招生章程</a><span class="date">2026-03-20</span></li>
          <li class="notice-item"><a href="/graduate/notices/retest.html">2026年硕士研究生招生复试工作安排</a><span class="date">2026-03-18</span></li>
        </ul></body></html>
        """.strip(),
    }


@pytest.fixture
def mock_fetcher(monkeypatch: pytest.MonkeyPatch, fixture_texts: dict[str, str]) -> None:
    from app.services.crawler.base import FetchResult, PageFetcher
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch(
        self,
        url: str,
        crawl_mode: str = "static",
        *,
        method: str = "GET",
        data=None,
        json_payload=None,
        headers=None,
        timeout_seconds=None,
    ) -> FetchResult:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path or "/"
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://example.edu.cn"
        if host and host != "example.edu.cn":
            if path in {"", "/", "/index.html", "/index.htm", "/main.htm", "/yjszs/"}:
                return FetchResult(
                    url=url,
                    status_code=200,
                    text=f"<html><body><a href='{base}/graduate/notices.html'>研究生招生动态</a><a href='{base}/graduate/intro.html'>研究生院简介</a></body></html>",
                )
            if path.endswith("/graduate/notices.html"):
                return FetchResult(url=url, status_code=200, text=fixture_texts["master_list"])
            if path.endswith("/graduate/intro.html"):
                return FetchResult(url=url, status_code=200, text="<html><body><article>研究生院简介</article></body></html>")
            if path.endswith("retest.html"):
                return FetchResult(url=url, status_code=200, text=fixture_texts["master_detail"])
        if url.endswith("list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["list"])
        if url.endswith("/admissions/list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["admissions_generic_list"])
        if url.endswith("/graduate/notices.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["master_list"])
        if url.endswith("/overview.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["school_profile"])
        if url.endswith("/news/list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["news_list"])
        if url.endswith("/news/1.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["news_detail_1"])
        if url.endswith("/news/2.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["news_detail_2"])
        if url.endswith("lecture.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["lecture"])
        if url.endswith("apply.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["apply"])
        if url.endswith("charter.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["undergraduate_detail"])
        if url.endswith("retest.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["master_detail"])
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(PageFetcher, "fetch", fake_fetch)

    def fake_fetch_page_payload(self, *, url: str, crawl_mode: str = "dynamic", method: str = "GET", data=None, json_payload=None, headers=None):
        result = fake_fetch(self, url, crawl_mode=crawl_mode, method=method, data=data, json_payload=json_payload, headers=headers)
        return {
            "url": result.url,
            "raw_html": result.text,
            "raw_text": result.text,
            "attachments": [],
            "status_code": result.status_code,
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)

    def fake_discover_candidate_links(self, homepage_url, max_links=20):
        parsed = urlparse(homepage_url)
        host = parsed.hostname or ""
        if host and host != "example.edu.cn":
            base = f"{parsed.scheme}://{parsed.netloc}"
            return {
                "provider": "crawl4ai",
                "links": [
                    {"title": "研究生招生动态", "url": f"{base}/graduate/notices.html", "snippet": "研究生招生动态", "domain": host},
                    {"title": "研究生院简介", "url": f"{base}/graduate/intro.html", "snippet": "研究生院简介", "domain": host},
                ],
            }
        return {
            "provider": "crawl4ai",
            "links": [
                {"title": "研究生招生动态", "url": "https://example.edu.cn/graduate/notices.html", "snippet": "研究生招生动态", "domain": "example.edu.cn"},
            ],
        }

    monkeypatch.setattr(Crawl4AIProvider, "discover_candidate_links", fake_discover_candidate_links)


@pytest.fixture
def mock_homepage_fetcher(monkeypatch: pytest.MonkeyPatch, fixture_texts: dict[str, str]) -> None:
    from app.services.crawler.base import FetchResult, PageFetcher
    from app.services.onboarding.providers import Crawl4AIProvider

    def fake_fetch(
        self,
        url: str,
        crawl_mode: str = "static",
        *,
        method: str = "GET",
        data=None,
        json_payload=None,
        headers=None,
        timeout_seconds=None,
    ) -> FetchResult:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path or "/"
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://example.edu.cn"
        if host and host != "example.edu.cn":
            if path in {"", "/", "/index.html", "/index.htm", "/main.htm", "/yjszs/"}:
                return FetchResult(
                    url=url,
                    status_code=200,
                    text=f"<html><body><a href='{base}/graduate/notices.html'>研究生招生动态</a><a href='{base}/graduate/intro.html'>研究生院简介</a></body></html>",
                )
            if path.endswith("/graduate/notices.html"):
                return FetchResult(url=url, status_code=200, text=fixture_texts["master_list"])
            if path.endswith("/graduate/intro.html"):
                return FetchResult(url=url, status_code=200, text="<html><body><article>研究生院简介</article></body></html>")
            if path.endswith("retest.html"):
                return FetchResult(url=url, status_code=200, text=fixture_texts["master_detail"])
        if url.endswith("/admissions/undergraduate/"):
            return FetchResult(
                url=url,
                status_code=200,
                text="""
                <html><body>
                  <a href="/admissions/undergraduate/list.html">本科招生动态</a>
                  <a href="/news.html">新闻动态</a>
                  <a href="/contact.html">联系我们</a>
                </body></html>
                """.strip(),
            )
        if url.endswith("/graduate/"):
            return FetchResult(
                url=url,
                status_code=200,
                text="""
                <html><body>
                  <a href="/graduate/notices.html">研究生招生动态</a>
                  <a href="/graduate/intro.html">研究生院简介</a>
                  <a href="/search.html">搜索</a>
                </body></html>
                """.strip(),
            )
        if url.endswith("/doctor/"):
            return FetchResult(
                url=url,
                status_code=200,
                text="""
                <html><body>
                  <a href="/doctor/notices.html">博士招生动态</a>
                  <a href="/doctor/team.html">博士导师队伍</a>
                </body></html>
                """.strip(),
            )
        if url.endswith("/") or url.endswith("index.html"):
            return FetchResult(
                url=url,
                status_code=200,
                text="""
                <html>
                  <body>
                    <header>
                      <a href="/news.html">校园新闻</a>
                      <a href="/admissions/undergraduate/">本科招生</a>
                      <a href="/graduate/">研究生招生</a>
                      <a href="/doctor/">博士招生</a>
                      <a href="/contact.html">联系我们</a>
                    </header>
                    <main><h2>示例大学官网首页</h2></main>
                  </body>
                </html>
                """.strip(),
            )
        if url.endswith("/admissions/undergraduate/list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["undergraduate_list"])
        if url.endswith("/admissions/list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["admissions_generic_list"])
        if url.endswith("/graduate/notices.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["master_list"])
        if url.endswith("/doctor/notices.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["doctor_list"])
        if url.endswith("/overview.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["school_profile"])
        if url.endswith("/news/list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["news_list"])
        if url.endswith("/news/1.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["news_detail_1"])
        if url.endswith("/news/2.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["news_detail_2"])
        if url.endswith("charter.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["undergraduate_detail"])
        if url.endswith("plan.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["undergraduate_plan_detail"])
        if url.endswith("retest.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["master_detail"])
        if url.endswith("camp.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["master_camp_detail"])
        if url.endswith("brochure.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["doctor_detail"])
        if url.endswith("/admissions/doctor/apply.html") or url.endswith("/doctor/apply.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["doctor_apply_detail"])
        if url.endswith("/notices/list.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["list"])
        if url.endswith("lecture.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["lecture"])
        if url.endswith("apply.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["apply"])
        if url.endswith("news.html"):
            return FetchResult(url=url, status_code=200, text="<html><body><article>新闻页面</article></body></html>")
        if url.endswith("contact.html") or url.endswith("intro.html") or url.endswith("team.html") or url.endswith("search.html"):
            return FetchResult(url=url, status_code=200, text="<html><body><article>普通介绍页面</article></body></html>")
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(PageFetcher, "fetch", fake_fetch)

    def fake_fetch_page_payload(self, *, url: str, crawl_mode: str = "dynamic", method: str = "GET", data=None, json_payload=None, headers=None):
        result = fake_fetch(self, url, crawl_mode=crawl_mode, method=method, data=data, json_payload=json_payload, headers=headers)
        return {
            "url": result.url,
            "raw_html": result.text,
            "raw_text": result.text,
            "attachments": [],
            "status_code": result.status_code,
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
    def fake_discover_candidate_links(self, homepage_url, max_links=20):
        parsed = urlparse(homepage_url)
        host = parsed.hostname or ""
        if host and host != "example.edu.cn":
            base = f"{parsed.scheme}://{parsed.netloc}"
            return {
                "provider": "crawl4ai",
                "links": [
                    {"title": "研究生招生动态", "url": f"{base}/graduate/notices.html", "snippet": "研究生招生动态", "domain": host},
                    {"title": "研究生院简介", "url": f"{base}/graduate/intro.html", "snippet": "研究生院简介", "domain": host},
                ],
            }
        if homepage_url.endswith("/graduate/"):
            return {
                "provider": "crawl4ai",
                "links": [
                    {"title": "研究生招生动态", "url": "https://example.edu.cn/graduate/notices.html", "snippet": "研究生招生动态", "domain": "example.edu.cn"},
                    {"title": "研究生院简介", "url": "https://example.edu.cn/graduate/intro.html", "snippet": "研究生院简介", "domain": "example.edu.cn"},
                ],
            }
        if homepage_url.endswith("/doctor/"):
            return {
                "provider": "crawl4ai",
                "links": [
                    {"title": "博士招生动态", "url": "https://example.edu.cn/doctor/notices.html", "snippet": "博士招生动态", "domain": "example.edu.cn"},
                    {"title": "博士导师队伍", "url": "https://example.edu.cn/doctor/team.html", "snippet": "博士导师队伍", "domain": "example.edu.cn"},
                ],
            }
        if homepage_url.endswith("/admissions/undergraduate/"):
            return {
                "provider": "crawl4ai",
                "links": [
                    {"title": "本科招生动态", "url": "https://example.edu.cn/admissions/undergraduate/list.html", "snippet": "本科招生动态", "domain": "example.edu.cn"},
                    {"title": "联系我们", "url": "https://example.edu.cn/contact.html", "snippet": "联系我们", "domain": "example.edu.cn"},
                ],
            }
        return {
            "provider": "crawl4ai",
            "links": [
                {"title": "本科招生", "url": "https://example.edu.cn/admissions/undergraduate/", "snippet": "本科招生", "domain": "example.edu.cn"},
                {"title": "研究生招生", "url": "https://example.edu.cn/graduate/", "snippet": "研究生招生", "domain": "example.edu.cn"},
                {"title": "博士招生", "url": "https://example.edu.cn/doctor/", "snippet": "博士招生", "domain": "example.edu.cn"},
                {"title": "校园新闻", "url": "https://example.edu.cn/news.html", "snippet": "校园新闻", "domain": "example.edu.cn"},
            ],
        }

    monkeypatch.setattr(Crawl4AIProvider, "discover_candidate_links", fake_discover_candidate_links)


@pytest.fixture
def mock_ajax_fetcher(monkeypatch: pytest.MonkeyPatch, fixture_texts: dict[str, str]) -> None:
    from app.services.crawler.base import FetchResult, PageFetcher
    from app.services.onboarding.providers import Crawl4AIProvider

    ajax_html = """
    <li>
      <a href="https://example.edu.cn/notice/lecture.html">
        <div class="time">2026-03-20</div>
        <div class="tit">2026年硕士研究生招生复试工作安排</div>
      </a>
    </li>
    <li>
      <a href="https://example.edu.cn/notice/apply.html">
        <div class="time">2026-03-18</div>
        <div class="tit">2026年博士研究生招生简章</div>
      </a>
    </li>
    """.strip()

    def fake_fetch(
        self,
        url: str,
        crawl_mode: str = "static",
        *,
        method: str = "GET",
        data=None,
        json_payload=None,
        headers=None,
        timeout_seconds=None,
    ) -> FetchResult:
        if url.endswith("ajax.html"):
            assert method == "POST"
            return FetchResult(
                url=url,
                status_code=200,
                text=json.dumps({"content": ajax_html}, ensure_ascii=False),
            )
        if url.endswith("lecture.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["lecture"])
        if url.endswith("apply.html"):
            return FetchResult(url=url, status_code=200, text=fixture_texts["apply"])
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(PageFetcher, "fetch", fake_fetch)

    def fake_fetch_page_payload(self, *, url: str, crawl_mode: str = "dynamic", method: str = "GET", data=None, json_payload=None, headers=None):
        result = fake_fetch(self, url, crawl_mode=crawl_mode, method=method, data=data, json_payload=json_payload, headers=headers)
        return {
            "url": result.url,
            "raw_html": result.text,
            "raw_text": result.text,
            "attachments": [],
            "status_code": result.status_code,
        }

    monkeypatch.setattr(Crawl4AIProvider, "fetch_page_payload", fake_fetch_page_payload)
