from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models.source import Source
from app.services.llm.list_selector import ListItemSelector
from app.services.onboarding.providers import Crawl4AIProvider
from app.services.normalize.cleaner import clean_text


@dataclass(slots=True)
class NoticeListItem:
    title: str
    detail_url: str
    publish_date: str | None
    source_site: str


class ListCrawler:
    def __init__(self, provider: Crawl4AIProvider | None = None) -> None:
        self.provider = provider or Crawl4AIProvider()
        self.selector = ListItemSelector()

    def crawl(self, source: Source, page_limit: int = 1) -> list[NoticeListItem]:
        config = source.config_json or {}
        list_config = config.get("list", {})
        list_pages = source.start_urls_json or config.get("list_pages") or config.get("list_url_patterns") or []
        if not list_pages:
            raise ValueError(f"Source {source.name} does not define list_pages.")

        items: list[NoticeListItem] = []
        for page_url in list_pages[:page_limit]:
            request_method = list_config.get("request_method", "GET")
            request_data = list_config.get("request_data")
            request_json = list_config.get("request_json")
            request_headers = list_config.get("request_headers")
            result = self.provider.fetch_page_payload(
                url=page_url,
                crawl_mode=source.crawl_mode,
                method=request_method,
                data=request_data,
                json_payload=request_json,
                headers=request_headers,
            )
            if not result:
                raise RuntimeError(f"Crawl4AI failed to fetch list page: {page_url}")
            html = self._extract_html_payload(result.get("raw_html") or "", list_config.get("response_json_key"))
            selected_items = self.selector.select_items(
                source_name=source.name,
                collection_domain=source.collection_domain,
                page_url=page_url,
                page_title=result.get("title"),
                links=result.get("links") or self._extract_links_from_html(html, page_url),
                page_text=result.get("raw_text") or "",
                max_items=20,
            )
            for item in selected_items:
                if not self._is_supported_detail_url(item.get("url")):
                    continue
                items.append(
                    NoticeListItem(
                        title=item["title"],
                        detail_url=item["url"],
                        publish_date=item.get("publish_date"),
                        source_site=source.name,
                    )
                )
        return items

    def _extract_html_payload(self, text: str, response_json_key: str | None) -> str:
        if not response_json_key:
            return text
        payload = json.loads(text)
        current: object = payload
        for key in response_json_key.split("."):
            if not isinstance(current, dict):
                raise ValueError(f"Response JSON path '{response_json_key}' is invalid.")
            current = current.get(key)
        if not isinstance(current, str):
            raise ValueError(f"Response JSON key '{response_json_key}' did not resolve to HTML text.")
        return current

    def _extract_links_from_html(self, html: str, base_url: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        links: list[dict] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            anchor_clone = BeautifulSoup(str(anchor), "lxml").select_one("a[href]")
            if anchor_clone is not None:
                for noise in anchor_clone.select(".date, .time, time"):
                    noise.decompose()
                title = clean_text(anchor_clone.get_text(" ", strip=True))
            else:
                title = clean_text(anchor.get_text(" ", strip=True))
            if not href or not title:
                continue
            resolved_url = urljoin(base_url, href)
            if not self._is_supported_detail_url(resolved_url):
                continue
            links.append(
                {
                    "title": title,
                    "url": resolved_url,
                    "snippet": title,
                }
            )
        return links

    def _is_supported_detail_url(self, url: str | None) -> bool:
        if not isinstance(url, str) or not url.strip():
            return False
        parsed = urlparse(url.strip())
        if not parsed.scheme:
            return True
        return parsed.scheme.lower() in {"http", "https"}
