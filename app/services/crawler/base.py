from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.core.config import get_settings


@dataclass(slots=True)
class FetchResult:
    url: str
    status_code: int
    text: str


class PageFetcher:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.settings.crawler_user_agent})

    def fetch(
        self,
        url: str,
        crawl_mode: str = "static",
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> FetchResult:
        if crawl_mode == "dynamic":
            return self._fetch_dynamic(url, timeout_seconds=timeout_seconds)
        return self._fetch_static(
            url,
            method=method,
            data=data,
            json_payload=json_payload,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )

    def _fetch_static(
        self,
        url: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> FetchResult:
        response = self._session.request(
            method=method.upper(),
            url=url,
            data=data,
            json=json_payload,
            headers=headers,
            timeout=timeout_seconds or self.settings.crawler_timeout_seconds,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return FetchResult(url=url, status_code=response.status_code, text=response.text)

    def _fetch_dynamic(self, url: str, *, timeout_seconds: int | float | None = None) -> FetchResult:
        try:
            from playwright.sync_api import sync_playwright
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Dynamic crawl requires Playwright to be installed.") from exc

        try:
            with sync_playwright() as playwright:  # pragma: no cover
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.settings.crawler_user_agent)
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int((timeout_seconds or self.settings.crawler_timeout_seconds) * 1000),
                )
                content = page.content()
                browser.close()
            return FetchResult(url=url, status_code=200, text=content)
        except PlaywrightTimeoutError:
            return self._fetch_static(url, timeout_seconds=timeout_seconds)
