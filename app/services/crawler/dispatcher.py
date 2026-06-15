from __future__ import annotations

from app.models.source import Source
from app.services.crawler.detail_parser import DetailParser
from app.services.crawler.list_crawler import ListCrawler
from app.services.crawler.types import CrawlFetchResult
from app.services.crawler.types import FetchedNoticeRecord


class CrawlRunnerDispatcher:
    def __init__(self) -> None:
        self.list_crawler = ListCrawler()
        self.detail_parser = DetailParser()

    def fetch_notices(self, source: Source, page_limit: int = 1) -> CrawlFetchResult:
        items = self.list_crawler.crawl(source, page_limit=page_limit)
        result = CrawlFetchResult(
            engine="direct",
            discovered=len(items),
            runner_version="direct-v1",
        )
        for item in items:
            try:
                result.records.append(
                    FetchedNoticeRecord(
                        list_item=item,
                        detail_page=self.detail_parser.parse(source, item.detail_url),
                    )
                )
            except Exception as exc:
                result.errors.append(f"{item.detail_url}：{exc}")
        return result
