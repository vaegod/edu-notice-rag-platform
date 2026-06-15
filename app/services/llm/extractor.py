from __future__ import annotations

from datetime import date
import re

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.source import Source
from app.services.admissions_taxonomy import (
    ADMISSIONS_DOC_TYPE_RULES,
    ADMISSIONS_DOC_TYPES,
    ADMISSIONS_KEYWORDS,
    ADMISSIONS_TARGET_AUDIENCE,
)
from app.services.crawler.detail_parser import ParsedDetailPage
from app.services.domains import (
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
    COLLECTION_DOMAIN_NEWS_CENTER,
    COLLECTION_DOMAIN_SCHOOL_PROFILE,
    normalize_collection_domain,
)
from app.services.llm.siliconflow_client import SiliconFlowClient, load_prompt_template
from app.services.normalize.date_parser import parse_date_string, parse_datetime_string
from app.services.onboarding.providers import Crawl4AIProvider


class ExtractedDocument(BaseModel):
    collection_domain: str = COLLECTION_DOMAIN_ADMISSIONS_NOTICE
    doc_type: str | None = None
    title: str
    publish_date: str | None = None
    deadline: str | None = None
    department: str | None = None
    content_category: str | None = None
    institution_name: str | None = None
    target_audience: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    summary: str | None = None
    attachment_urls: list[str] = Field(default_factory=list)
    event_time: str | None = None
    event_location: str | None = None
    extra: dict = Field(default_factory=dict)

    @property
    def publish_date_value(self) -> date | None:
        return parse_date_string(self.publish_date)

    @property
    def deadline_value(self) -> date | None:
        return parse_date_string(self.deadline)


class BaseDomainExtractor:
    prompt_name = "admissions_extract.txt"
    biz_type = "content_postprocess"

    def __init__(self, llm_client: SiliconFlowClient | None = None) -> None:
        self.settings = get_settings()
        self.llm_client = llm_client or SiliconFlowClient()
        self.prompt_template = load_prompt_template(self.prompt_name)
        self.crawl4ai = Crawl4AIProvider()

    def extract(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
        session: Session | None = None,
        related_id: int | None = None,
    ) -> ExtractedDocument:
        effective_page = self._apply_crawl4ai_page_fallback(detail_page)
        if not self.llm_client.enabled or self.settings.mock_llm_enabled:
            return self._fallback_extract(
                source=source,
                detail_page=effective_page,
                fallback_title=fallback_title,
            )

        base_document = self._build_base_document(
            source=source,
            detail_page=effective_page,
            fallback_title=fallback_title,
        )

        user_prompt = (
            f"collection_domain: {base_document.collection_domain}\n"
            f"title: {base_document.title}\n"
            f"url: {effective_page.url}\n"
            f"text:\n{effective_page.raw_text[:12000]}"
        )
        try:
            parsed = self.llm_client.chat_json(
                system_prompt=self.prompt_template,
                user_prompt=user_prompt,
                biz_type=self.biz_type,
                session=session,
                related_id=related_id,
            )
            return self._merge_llm_result(base_document, parsed, effective_page)
        except Exception:
            return self._fallback_extract(
                source=source,
                detail_page=effective_page,
                fallback_title=fallback_title,
            )

    def _build_base_document(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            title=detail_page.title or fallback_title,
            publish_date=detail_page.publish_date or self._extract_publish_date(detail_page.raw_text),
            institution_name=(source.scope_json or {}).get("university_name") or source.organization_name,
            attachment_urls=[item.file_url for item in detail_page.attachments],
        )

    def _fallback_extract(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        raise NotImplementedError

    def _merge_llm_result(
        self,
        fallback_document: ExtractedDocument,
        llm_result: dict,
        detail_page: ParsedDetailPage,
    ) -> ExtractedDocument:
        merged = fallback_document.model_dump()
        for field in [
            "doc_type",
            "publish_date",
            "deadline",
            "department",
            "content_category",
            "institution_name",
            "event_time",
            "event_location",
        ]:
            preferred = self._normalize_scalar_value(llm_result.get(field))
            if preferred not in (None, "", []):
                merged[field] = preferred

        title = self._normalize_scalar_value(llm_result.get("title"))
        merged["title"] = detail_page.title or title or fallback_document.title

        summary = self._normalize_scalar_value(llm_result.get("summary"))
        if summary not in (None, ""):
            merged["summary"] = summary

        merged["keywords"] = self._merge_string_lists(
            llm_result.get("keywords"),
            merged.get("keywords"),
        )
        merged["target_audience"] = self._merge_string_lists(
            llm_result.get("target_audience"),
            merged.get("target_audience"),
        )
        merged["attachment_urls"] = self._merge_string_lists(
            llm_result.get("attachment_urls"),
            merged.get("attachment_urls"),
        ) or [item.file_url for item in detail_page.attachments]
        merged["extra"] = {
            **(merged.get("extra") or {}),
            **(llm_result.get("extra") or {}),
        }
        return ExtractedDocument.model_validate(merged)

    def _merge_string_lists(self, preferred: list | str | None, fallback: list | str | None) -> list[str]:
        merged: list[str] = []
        for values in (preferred or [], fallback or []):
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, str):
                    normalized = item.strip()
                    if normalized and normalized not in merged:
                        merged.append(normalized)
        return merged

    def _normalize_scalar_value(self, value):
        if isinstance(value, list):
            return self._normalize_scalar_value(value[0]) if value else None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    def _build_summary(self, text: str) -> str:
        normalized = text.replace("\n", " ").strip()
        return normalized[:160] + ("..." if len(normalized) > 160 else "")

    def _extract_publish_date(self, text: str) -> str | None:
        match = re.search(r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)", text)
        if not match:
            return None
        parsed = parse_date_string(match.group(1))
        return parsed.isoformat() if parsed else None

    def _apply_crawl4ai_page_fallback(
        self,
        detail_page: ParsedDetailPage,
    ) -> ParsedDetailPage:
        if not self.crawl4ai.enabled or len(detail_page.raw_text or "") >= 120:
            return detail_page
        payload = self.crawl4ai.extract_detail_payload(url=detail_page.url)
        if not payload:
            return detail_page
        return ParsedDetailPage(
            url=detail_page.url,
            title=payload.get("title") or detail_page.title,
            publish_date=detail_page.publish_date,
            raw_html=payload.get("raw_html") or detail_page.raw_html,
            raw_text=payload.get("raw_text") or detail_page.raw_text,
            status_code=detail_page.status_code,
            attachments=detail_page.attachments,
        )


class AdmissionsExtractor(BaseDomainExtractor):
    prompt_name = "admissions_extract.txt"
    biz_type = "admissions_extract"

    def _build_base_document(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            title=detail_page.title or fallback_title,
            publish_date=detail_page.publish_date or self._extract_publish_date(detail_page.raw_text),
            institution_name=(source.scope_json or {}).get("university_name") or source.organization_name,
            target_audience=[],
            keywords=[],
            attachment_urls=[item.file_url for item in detail_page.attachments],
            extra={"admissions_levels": (source.scope_json or {}).get("admissions_levels") or []},
        )

    def _fallback_extract(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        text = detail_page.raw_text
        title = detail_page.title or fallback_title
        keywords = [
            keyword
            for keyword in ADMISSIONS_KEYWORDS
            if keyword.lower() in text.lower() or keyword.lower() in title.lower()
        ]
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
            doc_type=self._detect_doc_type(title, text),
            title=title,
            publish_date=detail_page.publish_date or self._extract_publish_date(text),
            deadline=self._extract_deadline(text),
            department=self._extract_department(text, title),
            content_category="招生公告",
            institution_name=(source.scope_json or {}).get("university_name") or source.organization_name,
            target_audience=self._extract_target_audience(text),
            keywords=keywords,
            summary=self._build_summary(text),
            attachment_urls=[item.file_url for item in detail_page.attachments],
            event_time=self._extract_event_time(text),
            event_location=self._extract_event_location(text),
            extra={"admissions_levels": (source.scope_json or {}).get("admissions_levels") or []},
        )

    def _detect_doc_type(self, title: str, text: str) -> str:
        combined = f"{title}\n{text}".lower()
        for doc_type, tokens in ADMISSIONS_DOC_TYPE_RULES.items():
            if any(token.lower() in combined for token in tokens):
                return doc_type
        return "其他招生"

    def _extract_deadline(self, text: str) -> str | None:
        match = re.search(r"(截止(?:时间|日期)?[：: ]*)(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)", text)
        if not match:
            return None
        deadline = parse_date_string(match.group(2))
        return deadline.isoformat() if deadline else None

    def _extract_department(self, text: str, title: str) -> str | None:
        combined = f"{title}\n{text}"
        match = re.search(r"([^\n，,。]{2,20}(学院|实验室|研究院|中心|处|办))", combined)
        return match.group(1) if match else None

    def _extract_event_time(self, text: str) -> str | None:
        match = re.search(r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?[ ]*\d{1,2}[:：]\d{2})", text)
        if not match:
            return None
        parsed = parse_datetime_string(match.group(1))
        return parsed.isoformat(timespec="minutes") if parsed else None

    def _extract_event_location(self, text: str) -> str | None:
        match = re.search(r"(地点|会议地点|活动地点)[：: ]*([^\n。；]{2,40})", text)
        return match.group(2).strip() if match else None

    def _extract_target_audience(self, text: str) -> list[str]:
        return [label for label in ADMISSIONS_TARGET_AUDIENCE if label in text]

    def _merge_llm_result(
        self,
        fallback_document: ExtractedDocument,
        llm_result: dict,
        detail_page: ParsedDetailPage,
    ) -> ExtractedDocument:
        merged = super()._merge_llm_result(fallback_document, llm_result, detail_page)
        if merged.doc_type not in ADMISSIONS_DOC_TYPES:
            merged.doc_type = fallback_document.doc_type
        merged.title = detail_page.title or fallback_document.title
        return merged


class NewsExtractor(BaseDomainExtractor):
    prompt_name = "news_extract.txt"
    biz_type = "news_extract"

    def _build_base_document(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_NEWS_CENTER,
            title=detail_page.title or fallback_title,
            publish_date=detail_page.publish_date or self._extract_publish_date(detail_page.raw_text),
            institution_name=(source.scope_json or {}).get("university_name") or source.organization_name,
            attachment_urls=[item.file_url for item in detail_page.attachments],
        )

    def _fallback_extract(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        text = detail_page.raw_text
        title = detail_page.title or fallback_title
        category = self._detect_news_category(title, text)
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_NEWS_CENTER,
            doc_type=category,
            title=title,
            publish_date=detail_page.publish_date or self._extract_publish_date(text),
            department=self._extract_department(text, title),
            content_category=category,
            institution_name=(source.scope_json or {}).get("university_name") or source.organization_name,
            keywords=self._extract_news_keywords(title, text),
            summary=self._build_summary(text),
            attachment_urls=[item.file_url for item in detail_page.attachments],
        )

    def _detect_news_category(self, title: str, text: str) -> str:
        combined = f"{title}\n{text}"
        if any(token in combined for token in ("学院", "院系")):
            return "学院新闻"
        if any(token in combined for token in ("科研", "实验室", "学术")):
            return "科研新闻"
        return "校园新闻"

    def _extract_department(self, text: str, title: str) -> str | None:
        combined = f"{title}\n{text}"
        match = re.search(r"([^\n，,。]{2,20}(学院|实验室|研究院|中心|处|办))", combined)
        return match.group(1) if match else None

    def _extract_news_keywords(self, title: str, text: str) -> list[str]:
        candidates = ["新闻", "校园", "学术", "科研", "学院", "活动", "论坛", "讲座"]
        combined = f"{title}\n{text}".lower()
        return [item for item in candidates if item.lower() in combined]


class SchoolProfileExtractor(BaseDomainExtractor):
    prompt_name = "profile_extract.txt"
    biz_type = "profile_extract"

    def _build_base_document(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        institution_name = (source.scope_json or {}).get("university_name") or source.organization_name or fallback_title
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_SCHOOL_PROFILE,
            title=detail_page.title or fallback_title or f"{institution_name}学校概况",
            publish_date=detail_page.publish_date or self._extract_publish_date(detail_page.raw_text),
            institution_name=institution_name,
            attachment_urls=[item.file_url for item in detail_page.attachments],
            extra={},
        )

    def _fallback_extract(
        self,
        *,
        source: Source,
        detail_page: ParsedDetailPage,
        fallback_title: str,
    ) -> ExtractedDocument:
        text = detail_page.raw_text
        institution_name = (source.scope_json or {}).get("university_name") or source.organization_name or fallback_title
        title = detail_page.title or fallback_title or f"{institution_name}学校概况"
        return ExtractedDocument(
            collection_domain=COLLECTION_DOMAIN_SCHOOL_PROFILE,
            doc_type="学校概况",
            title=title,
            publish_date=detail_page.publish_date or self._extract_publish_date(text),
            content_category="学校概况",
            institution_name=institution_name,
            keywords=self._extract_profile_keywords(title, text),
            summary=self._build_summary(text),
            attachment_urls=[item.file_url for item in detail_page.attachments],
            extra={
                "overview": text[:4000],
            },
        )

    def _extract_profile_keywords(self, title: str, text: str) -> list[str]:
        candidates = ["学校概况", "学校简介", "办学", "学科", "师资", "历史沿革", "校园"]
        combined = f"{title}\n{text}".lower()
        return [item for item in candidates if item.lower() in combined]


class DomainExtractorRegistry:
    def __init__(self) -> None:
        self._extractors = {
            COLLECTION_DOMAIN_ADMISSIONS_NOTICE: AdmissionsExtractor(),
            COLLECTION_DOMAIN_NEWS_CENTER: NewsExtractor(),
            COLLECTION_DOMAIN_SCHOOL_PROFILE: SchoolProfileExtractor(),
        }

    def get(self, collection_domain: str) -> BaseDomainExtractor:
        return self._extractors[normalize_collection_domain(collection_domain)]


ExtractedAdmission = ExtractedDocument
ExtractedNotice = ExtractedDocument
NoticeExtractor = AdmissionsExtractor
