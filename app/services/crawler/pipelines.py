from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.attachment import Attachment
from app.models.document import Document, DocumentTag
from app.models.raw_page import RawPage
from app.models.source import Source
from app.models.task import CrawlTask
from app.services.crawler.detail_parser import DetailParser
from app.services.crawler.dispatcher import CrawlRunnerDispatcher
from app.services.domains import (
    COLLECTION_DOMAIN_SCHOOL_PROFILE,
    normalize_collection_domain,
    task_type_to_domain,
)
from app.services.llm.extractor import DomainExtractorRegistry
from app.services.normalize.date_parser import parse_datetime_string, resolve_time_range
from app.services.normalize.deduper import compute_content_hash, find_duplicate_document


class CollectionPipelineBase:
    def __init__(self) -> None:
        self.extractors = DomainExtractorRegistry()

    def run(self, session: Session, task: CrawlTask) -> dict:
        raise NotImplementedError

    def _save_raw_page(
        self,
        *,
        session: Session,
        source: Source,
        url: str,
        title: str,
        raw_html: str,
        raw_text: str,
        status_code: int,
    ) -> RawPage:
        content_hash = compute_content_hash(raw_text)
        raw_page = session.scalar(select(RawPage).where(RawPage.url == url))
        if raw_page is None:
            raw_page = RawPage(
                source_id=source.id,
                url=url,
                title=title,
                raw_html=raw_html,
                raw_text=raw_text,
                content_hash=content_hash,
                http_status=status_code,
                crawled_at=utc_now(),
            )
            session.add(raw_page)
        else:
            raw_page.title = title
            raw_page.raw_html = raw_html
            raw_page.raw_text = raw_text
            raw_page.content_hash = content_hash
            raw_page.http_status = status_code
            raw_page.crawled_at = utc_now()
        session.commit()
        session.refresh(raw_page)
        return raw_page

    def _save_document(
        self,
        *,
        session: Session,
        source: Source,
        raw_page: RawPage,
        extracted,
        model_name: str | None = None,
    ) -> tuple[bool, Document | None]:
        duplicate = find_duplicate_document(
            session,
            url=raw_page.url,
            title=extracted.title,
            publish_date=extracted.publish_date_value,
            content_hash=raw_page.content_hash,
        )
        if duplicate is not None and duplicate.raw_page_id != raw_page.id:
            return False, duplicate

        document = session.scalar(select(Document).where(Document.raw_page_id == raw_page.id))
        if document is None:
            document = Document(raw_page_id=raw_page.id, source_url=raw_page.url, title=extracted.title)
            session.add(document)
            session.flush()

        document.collection_domain = extracted.collection_domain
        document.content_category = extracted.content_category
        document.institution_name = extracted.institution_name or source.organization_name
        document.doc_type = extracted.doc_type
        document.title = extracted.title
        document.publish_date = extracted.publish_date_value
        document.deadline = extracted.deadline_value
        document.department = extracted.department
        document.summary = extracted.summary
        document.event_time = parse_datetime_string(extracted.event_time)
        document.event_location = extracted.event_location
        document.source_url = raw_page.url
        document.status = "structured"
        document.model_output = extracted.model_dump()
        document.model_name = model_name
        session.flush()

        session.query(DocumentTag).filter(DocumentTag.document_id == document.id).delete()
        session.query(Attachment).filter(Attachment.document_id == document.id).delete()
        for tag in extracted.keywords:
            session.add(DocumentTag(document_id=document.id, tag=tag))
        for attachment_url in extracted.attachment_urls:
            file_name = attachment_url.rsplit("/", 1)[-1]
            file_type = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None
            session.add(
                Attachment(
                    document_id=document.id,
                    file_name=file_name,
                    file_url=attachment_url,
                    file_type=file_type,
                )
            )
        session.commit()
        return True, document

    def _update_progress(
        self,
        session: Session,
        task: CrawlTask,
        *,
        stage: str,
        percent: int,
        message: str,
    ) -> None:
        task_payload = dict(task.task_payload or {})
        task_payload["progress"] = {
            "stage": stage,
            "percent": max(0, min(int(percent), 100)),
            "message": message,
        }
        task.task_payload = task_payload
        session.commit()


class ListDetailPipeline(CollectionPipelineBase):
    def __init__(self) -> None:
        super().__init__()
        self.runner_dispatcher = CrawlRunnerDispatcher()

    def run(self, session: Session, task: CrawlTask) -> dict:
        source = session.get(Source, task.source_id)
        if source is None:
            raise ValueError("Task source does not exist.")
        collection_domain = normalize_collection_domain(source.collection_domain)
        extractor = self.extractors.get(collection_domain)

        task.status = "running"
        task.started_at = utc_now()
        session.commit()
        self._update_progress(session, task, stage="starting", percent=5, message="任务开始执行。")

        stats = {
            "engine": "direct",
            "runner_version": "direct-v2",
            "discovered": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicates": 0,
            "skipped": 0,
            "errors": 0,
            "message": None,
        }
        payload = task.task_payload or {}
        page_limit = int(payload.get("page_limit", 1))
        time_start, time_end = resolve_time_range(payload.get("time_range"))

        try:
            self._update_progress(session, task, stage="fetching_list", percent=15, message="正在抓取列表页。")
            fetch_result = self.runner_dispatcher.fetch_notices(source, page_limit=page_limit)
            stats["engine"] = fetch_result.engine
            stats["runner_version"] = fetch_result.runner_version
            stats["discovered"] = fetch_result.discovered
            stats["fetched"] = len(fetch_result.records)
            total_records = max(len(fetch_result.records), 1)
            for record in fetch_result.records:
                try:
                    current_index = stats["inserted"] + stats["duplicates"] + stats["skipped"] + stats["errors"] + 1
                    progress = 30 + int((current_index / total_records) * 55)
                    self._update_progress(
                        session,
                        task,
                        stage="processing_detail",
                        percent=progress,
                        message=f"正在处理第 {current_index}/{total_records} 条详情页。",
                    )
                    raw_page = self._save_raw_page(
                        session=session,
                        source=source,
                        url=record.detail_page.url,
                        title=record.detail_page.title or record.list_item.title,
                        raw_html=record.detail_page.raw_html,
                        raw_text=record.detail_page.raw_text,
                        status_code=record.detail_page.status_code,
                    )
                    extracted = extractor.extract(
                        source=source,
                        detail_page=record.detail_page,
                        fallback_title=record.list_item.title,
                        session=session,
                        related_id=raw_page.id,
                    )
                    if not self._within_time_range(extracted.publish_date_value, time_start, time_end):
                        stats["skipped"] += 1
                        continue
                    if not self._matches_payload(extracted.model_dump(), payload):
                        stats["skipped"] += 1
                        continue
                    inserted, _ = self._save_document(
                        session=session,
                        source=source,
                        raw_page=raw_page,
                        extracted=extracted,
                        model_name=None if extractor.llm_client.enabled else "rule-based",
                    )
                    if inserted:
                        stats["inserted"] += 1
                    else:
                        stats["duplicates"] += 1
                except Exception as item_exc:
                    session.rollback()
                    stats["errors"] += 1
                    stats["message"] = str(item_exc)
            task.status = "success" if stats["errors"] == 0 else "partial_success"
            task.finished_at = utc_now()
            task_payload = dict(task.task_payload or {})
            task_payload["execution_result"] = stats
            task_payload["progress"] = {
                "stage": "completed",
                "percent": 100,
                "message": "任务执行完成。",
            }
            task.task_payload = task_payload
            session.commit()
            return stats
        except Exception as exc:
            session.rollback()
            task.status = "failed"
            task.error_message = str(exc)
            task.finished_at = utc_now()
            task_payload = dict(task.task_payload or {})
            task_payload["progress"] = {
                "stage": "failed",
                "percent": 100,
                "message": f"任务失败：{exc}",
            }
            task.task_payload = task_payload
            session.commit()
            raise

    def _matches_payload(self, extracted: dict, payload: dict) -> bool:
        topics = payload.get("topic") or []
        notice_types = payload.get("notice_type") or []
        if topics:
            combined = " ".join(
                [
                    extracted.get("title") or "",
                    extracted.get("summary") or "",
                    " ".join(extracted.get("keywords") or []),
                ]
            ).lower()
            if not any(topic.lower() in combined for topic in topics):
                return False
        if notice_types and extracted.get("doc_type") not in notice_types:
            return False
        return True

    def _within_time_range(self, publish_date, start, end) -> bool:
        if publish_date is None or start is None or end is None:
            return True
        return start <= publish_date <= end


class SchoolProfilePipeline(CollectionPipelineBase):
    def __init__(self) -> None:
        super().__init__()
        self.detail_parser = DetailParser()

    def run(self, session: Session, task: CrawlTask) -> dict:
        source = session.get(Source, task.source_id)
        if source is None:
            raise ValueError("Task source does not exist.")
        task.status = "running"
        task.started_at = utc_now()
        session.commit()
        self._update_progress(session, task, stage="fetching_page", percent=20, message="正在抓取学校概况页面。")

        page_url = (source.start_urls_json or [source.base_url])[0]
        parsed = self.detail_parser.parse(source, page_url)
        self._update_progress(session, task, stage="extracting", percent=60, message="正在提取结构化内容。")
        raw_page = self._save_raw_page(
            session=session,
            source=source,
            url=parsed.url,
            title=parsed.title or source.name,
            raw_html=parsed.raw_html,
            raw_text=parsed.raw_text,
            status_code=parsed.status_code,
        )
        extracted = self.extractors.get(COLLECTION_DOMAIN_SCHOOL_PROFILE).extract(
            source=source,
            detail_page=parsed,
            fallback_title=source.name,
            session=session,
            related_id=raw_page.id,
        )
        inserted, _ = self._save_document(
            session=session,
            source=source,
            raw_page=raw_page,
            extracted=extracted,
            model_name=None if self.extractors.get(COLLECTION_DOMAIN_SCHOOL_PROFILE).llm_client.enabled else "rule-based",
        )
        stats = {
            "engine": "detail_parser",
            "runner_version": "profile-v1",
            "discovered": 1,
            "fetched": 1,
            "inserted": 1 if inserted else 0,
            "duplicates": 0 if inserted else 1,
            "skipped": 0,
            "errors": 0,
            "message": None,
        }
        task.status = "success"
        task.finished_at = utc_now()
        task_payload = dict(task.task_payload or {})
        task_payload["execution_result"] = stats
        task_payload["progress"] = {
            "stage": "completed",
            "percent": 100,
            "message": "任务执行完成。",
        }
        task.task_payload = task_payload
        session.commit()
        return stats


class CollectionPipelineRegistry:
    def __init__(self) -> None:
        shared_list_pipeline = ListDetailPipeline()
        self._pipelines = {
            COLLECTION_DOMAIN_SCHOOL_PROFILE: SchoolProfilePipeline(),
            "admissions_notice": shared_list_pipeline,
            "news_center": shared_list_pipeline,
        }

    def get_for_task(self, task: CrawlTask, source: Source | None) -> CollectionPipelineBase:
        collection_domain = normalize_collection_domain(
            (source.collection_domain if source else None) or task_type_to_domain(task.task_type)
        )
        return self._pipelines[collection_domain]
