from __future__ import annotations

from dataclasses import dataclass, field

from app.services.crawler.detail_parser import ParsedDetailPage
from app.services.crawler.list_crawler import NoticeListItem


@dataclass(slots=True)
class FetchedNoticeRecord:
    list_item: NoticeListItem
    detail_page: ParsedDetailPage


@dataclass(slots=True)
class CrawlFetchResult:
    engine: str
    discovered: int = 0
    records: list[FetchedNoticeRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    runner_version: str = "v1"
