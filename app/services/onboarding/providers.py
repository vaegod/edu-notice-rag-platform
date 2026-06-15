from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.services.crawler.base import PageFetcher
from app.services.normalize.cleaner import clean_text


class Crawl4AIProvider:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.page_fetcher = PageFetcher()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.crawl4ai_enabled)

    def extract_detail_payload(self, *, url: str) -> dict[str, Any] | None:
        return self.fetch_page_payload(url=url, crawl_mode="dynamic")

    def fetch_page_payload(
        self,
        *,
        url: str,
        crawl_mode: str = "dynamic",
        method: str = "GET",
        data: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return self._fallback_fetch_page_payload(
                url=url,
                crawl_mode=crawl_mode,
                method=method,
                data=data,
                json_payload=json_payload,
                headers=headers,
            )
        try:
            from bs4 import BeautifulSoup
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        except ImportError:
            return self._fallback_fetch_page_payload(
                url=url,
                crawl_mode=crawl_mode,
                method=method,
                data=data,
                json_payload=json_payload,
                headers=headers,
            )

        async def _run() -> dict[str, Any] | None:
            browser_config = BrowserConfig(
                browser_type=self.settings.playwright_browser_type,
                headless=self.settings.playwright_headless,
                verbose=False,
                headers=headers or None,
            )
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                page_timeout=max(self.settings.playwright_navigation_timeout_ms, self.settings.crawler_timeout_seconds * 1000),
                wait_until="networkidle" if crawl_mode == "dynamic" else "domcontentloaded",
                verbose=False,
                method=(method or "GET").upper(),
                process_in_browser=crawl_mode == "dynamic",
            )
            if data:
                run_config.js_code = [
                    f"window.__crawl4ai_form_data = {data!r};"
                ]
            if json_payload:
                run_config.js_code_before_wait = [
                    f"window.__crawl4ai_json_payload = {json_payload!r};"
                ]
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)
            if not getattr(result, "success", False):
                return None
            cleaned_html = getattr(result, "cleaned_html", None) or getattr(result, "html", "")
            soup = BeautifulSoup(cleaned_html, "lxml")
            text = clean_text(soup.get_text("\n", strip=True))
            attachments = []
            for anchor in soup.select("a[href]"):
                href = anchor.get("href")
                label = clean_text(anchor.get_text(" ", strip=True))
                if not href or not label:
                    continue
                attachments.append({"file_name": label, "file_url": href})
            metadata = getattr(result, "metadata", None) or {}
            links = []
            for field in ("links", "internal_links"):
                field_value = getattr(result, field, None) or {}
                if isinstance(field_value, dict):
                    field_value = list(field_value.values())
                if not isinstance(field_value, list):
                    continue
                for item in field_value:
                    if not isinstance(item, dict):
                        continue
                    href = item.get("href") or item.get("url")
                    if not self._is_supported_link(href):
                        continue
                    links.append(
                        {
                            "title": clean_text(item.get("text") or item.get("title") or href),
                            "url": href,
                            "snippet": clean_text(item.get("text") or item.get("title") or ""),
                            "domain": urlparse(href).hostname or "",
                        }
                    )
            return {
                "title": metadata.get("title"),
                "raw_text": text,
                "raw_html": cleaned_html,
                "attachments": attachments,
                "status_code": getattr(result, "status_code", 200) or 200,
                "url": getattr(result, "url", url) or url,
                "links": self._dedupe_links(links, max_links=100),
            }

        try:
            result = asyncio.run(_run())
            if result:
                return result
        except Exception:
            pass
        return self._fallback_fetch_page_payload(
            url=url,
            crawl_mode=crawl_mode,
            method=method,
            data=data,
            json_payload=json_payload,
            headers=headers,
        )

    def discover_candidate_links(
        self,
        *,
        homepage_url: str,
        max_links: int = 20,
    ) -> dict[str, Any]:
        if self.enabled:
            links = self._discover_candidate_links_crawl4ai(homepage_url=homepage_url, max_links=max_links)
            if links:
                return {
                    "provider": "crawl4ai",
                    "links": links,
                }
        return {"provider": "crawl4ai", "links": []}

    def _discover_candidate_links_crawl4ai(
        self,
        *,
        homepage_url: str,
        max_links: int,
    ) -> list[dict[str, Any]]:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        except ImportError:
            return []

        async def _run() -> list[dict[str, Any]]:
            browser_config = BrowserConfig(
                browser_type=self.settings.playwright_browser_type,
                headless=self.settings.playwright_headless,
                verbose=False,
            )
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                page_timeout=self.settings.playwright_navigation_timeout_ms,
                wait_until="domcontentloaded",
                verbose=False,
            )
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=homepage_url, config=run_config)
            if not getattr(result, "success", False):
                return []
            links = []
            for field in ("links", "internal_links"):
                field_value = getattr(result, field, None) or {}
                if isinstance(field_value, dict):
                    field_value = list(field_value.values())
                if not isinstance(field_value, list):
                    continue
                for item in field_value:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("href") or item.get("url")
                    if not isinstance(url, str) or not url:
                        continue
                    title = clean_text(item.get("text") or item.get("title") or url)
                    links.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": item.get("text") or title,
                            "domain": urlparse(url).hostname or "",
                        }
                    )
            if links:
                return self._dedupe_links(links, max_links=max_links)
            html = getattr(result, "cleaned_html", None) or getattr(result, "html", "")
            return self._extract_links_from_html(html, max_links=max_links)

        try:
            return asyncio.run(_run())
        except Exception:
            return []

    def _extract_links_from_html(self, html: str, *, max_links: int) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        links: list[dict[str, Any]] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            text = clean_text(anchor.get_text(" ", strip=True))
            if not href or not text:
                continue
            parsed = urlparse(href)
            if not self._is_supported_link(href):
                continue
            links.append(
                {
                    "title": text,
                    "url": href,
                    "snippet": text,
                    "domain": parsed.hostname or "",
                }
            )
        return self._dedupe_links(links, max_links=max_links)

    def _is_supported_link(self, href: str | None) -> bool:
        if not isinstance(href, str) or not href.strip():
            return False
        parsed = urlparse(href.strip())
        if not parsed.scheme:
            return True
        return parsed.scheme.lower() in {"http", "https"}

    def _dedupe_links(self, links: list[dict[str, Any]], *, max_links: int) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in links:
            url = item.get("url")
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            deduped.append(item)
            if len(deduped) >= max_links:
                break
        return deduped

    def _fallback_fetch_page_payload(
        self,
        *,
        url: str,
        crawl_mode: str,
        method: str,
        data: dict[str, Any] | None,
        json_payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        try:
            result = self.page_fetcher.fetch(
                url,
                crawl_mode="static" if crawl_mode == "dynamic" else crawl_mode,
                method=method,
                data=data,
                json_payload=json_payload,
                headers=headers,
            )
        except Exception:
            return None
        soup = BeautifulSoup(result.text, "lxml")
        text = clean_text(soup.get_text("\n", strip=True))
        attachments = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            label = clean_text(anchor.get_text(" ", strip=True))
            if not href or not label:
                continue
            attachments.append({"file_name": label, "file_url": href})
        title_node = soup.select_one("title")
        return {
            "title": clean_text(title_node.get_text(" ", strip=True)) if title_node else None,
            "raw_text": text,
            "raw_html": result.text,
            "attachments": attachments,
            "status_code": result.status_code,
            "url": result.url,
            "provider": "requests_fallback",
            "links": self._extract_links_from_html(result.text, max_links=100),
        }
