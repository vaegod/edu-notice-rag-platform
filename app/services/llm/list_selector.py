from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
from app.services.llm.siliconflow_client import SiliconFlowClient, load_prompt_template


logger = logging.getLogger(__name__)


class ListItemSelector:
    def __init__(self, llm_client: SiliconFlowClient | None = None) -> None:
        self.settings = get_settings()
        self.llm_client = llm_client or SiliconFlowClient()
        self.prompt_template = load_prompt_template("list_select.txt")

    def select_items(
        self,
        *,
        source_name: str,
        collection_domain: str,
        page_url: str,
        page_title: str | None,
        links: list[dict[str, Any]],
        page_text: str,
        max_items: int = 20,
    ) -> list[dict[str, Any]]:
        fallback_items = self._fallback_select(links, page_url=page_url, max_items=max_items)
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return fallback_items

        candidates = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("snippet"),
            }
            for item in links[:50]
        ]
        try:
            payload = self.llm_client.chat_json(
                system_prompt=self.prompt_template,
                user_prompt=(
                    f"source_name: {source_name}\n"
                    f"collection_domain: {collection_domain}\n"
                    f"page_url: {page_url}\n"
                    f"page_title: {page_title or ''}\n"
                    f"page_text_excerpt:\n{(page_text or '')[:2500]}\n"
                    f"candidate_links: {candidates}"
                ),
                biz_type="list_select",
            )
        except Exception as exc:
            logger.warning("LLM list selection failed for %s, falling back: %s", page_url, exc)
            return fallback_items
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return fallback_items
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title")
            if not isinstance(url, str) or not url or url in seen:
                continue
            if not isinstance(title, str) or not title.strip():
                title = url
            seen.add(url)
            selected.append(
                {
                    "title": title.strip(),
                    "url": url,
                    "publish_date": item.get("publish_date"),
                }
            )
            if len(selected) >= max_items:
                break
        return selected or fallback_items

    def _fallback_select(
        self,
        links: list[dict[str, Any]],
        *,
        page_url: str,
        max_items: int,
    ) -> list[dict[str, Any]]:
        page_host = (urlparse(page_url).hostname or "").lower()
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in links:
            url = item.get("url")
            title = (item.get("title") or "").strip()
            if not isinstance(url, str) or not url or url in seen or not title:
                continue
            host = (urlparse(url).hostname or "").lower()
            if host and page_host and not host.endswith(page_host.split(".", 1)[-1]):
                continue
            seen.add(url)
            selected.append(
                {
                    "title": title,
                    "url": url,
                    "publish_date": None,
                }
            )
            if len(selected) >= max_items:
                break
        return selected
