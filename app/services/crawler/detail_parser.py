from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models.source import Source
from app.services.onboarding.providers import Crawl4AIProvider
from app.services.normalize.cleaner import clean_text, html_to_text


@dataclass(slots=True)
class ParsedAttachment:
    file_name: str
    file_url: str
    file_type: str | None = None


@dataclass(slots=True)
class ParsedDetailPage:
    url: str
    title: str | None
    publish_date: str | None
    raw_html: str
    raw_text: str
    status_code: int
    attachments: list[ParsedAttachment] = field(default_factory=list)


class DetailParser:
    def __init__(self, provider: Crawl4AIProvider | None = None) -> None:
        self.provider = provider or Crawl4AIProvider()

    def parse(self, source: Source, url: str) -> ParsedDetailPage:
        result = self.provider.fetch_page_payload(url=url, crawl_mode=source.crawl_mode)
        if not result:
            raise RuntimeError(f"Crawl4AI failed to fetch detail page: {url}")
        html = result.get("raw_html") or ""
        text = result.get("raw_text") or ""
        soup = BeautifulSoup(html, "lxml")
        config = source.config_json or {}
        detail_config = config.get("detail", {})

        title = None
        title_selector = detail_config.get("title_selector")
        if title_selector:
            title_node = soup.select_one(title_selector)
            if title_node is not None:
                title = clean_text(title_node.get_text(" ", strip=True))

        publish_date = None
        publish_date_selector = detail_config.get("publish_date_selector")
        if publish_date_selector:
            publish_date_node = soup.select_one(publish_date_selector)
            if publish_date_node is not None:
                publish_date = clean_text(publish_date_node.get_text(" ", strip=True))

        content_text = ""
        content_selector = detail_config.get("content_selector")
        if content_selector:
            content_node = soup.select_one(content_selector)
            if content_node is not None:
                content_text = clean_text(content_node.get_text("\n", strip=True))

        if not content_text:
            content_text = text or html_to_text(html)

        attachments: list[ParsedAttachment] = []
        attachment_selector = detail_config.get("attachment_selector", "a")
        for node in soup.select(attachment_selector):
            href = node.get("href")
            if not href:
                continue
            file_url = urljoin(url, href)
            if urlparse(file_url).scheme.lower() not in {"http", "https"}:
                continue
            file_name = clean_text(node.get_text(" ", strip=True)) or file_url.rsplit("/", 1)[-1]
            file_type = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None
            attachments.append(
                ParsedAttachment(file_name=file_name, file_url=file_url, file_type=file_type)
            )

        attachments_payload = result.get("attachments") or []
        if attachments_payload:
            attachments = [
                ParsedAttachment(
                    file_name=item.get("file_name") or item.get("file_url", "").rsplit("/", 1)[-1],
                    file_url=item.get("file_url"),
                    file_type=(item.get("file_name") or "").rsplit(".", 1)[-1].lower() if "." in (item.get("file_name") or "") else None,
                )
                for item in attachments_payload
                if item.get("file_url") and urlparse(item.get("file_url")).scheme.lower() in {"http", "https"}
            ] or attachments

        return ParsedDetailPage(
            url=result.get("url") or url,
            title=title,
            publish_date=publish_date,
            raw_html=html,
            raw_text=content_text,
            status_code=int(result.get("status_code") or 200),
            attachments=attachments,
        )
