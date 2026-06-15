from __future__ import annotations

from datetime import datetime
import re
from time import perf_counter

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.nl_task import NLTask
from app.models.source import Source
from app.models.task import CrawlTask
from app.schemas.task import TaskCreate
from app.services.domains import DOMAIN_TO_TASK_TYPE, normalize_collection_domain, normalize_task_type
from app.services.llm.nl_parser import NLTaskParser
from app.services.llm.summarizer import DocumentSummarizer
from app.services.normalize.date_parser import resolve_time_range
from app.services.rag.evidence_rag import EvidenceRAGService
from app.services.search.keyword_search import KeywordSearchService, serialize_document
from app.services.source_resolution import SourceResolutionService
from app.services.source_state import source_can_run, source_readiness_message
from app.services.task.background_runner import background_task_runner
from app.services.task.crawl_task_executor import CrawlTaskExecutor
from app.services.task.validator import TaskValidator


WORKFLOW_STEP_NAMES = [
    "parse_intent",
    "resolve_source",
    "validate_candidate",
    "create_task",
    "crawl",
    "extract",
    "index_for_qa",
    "answer_with_evidence",
]

GENERIC_TOPIC_TOKENS = (
    "最近",
    "最新",
    "相关",
    "信息",
    "动态",
    "情况",
    "怎样",
    "怎么样",
    "如何",
    "哪些",
    "有哪些",
    "一下",
    "介绍",
    "汇总",
    "总结",
    "请问",
    "帮我",
    "查询",
    "查看",
    "看看",
    "了解",
)

ADMISSIONS_TOPIC_MARKERS = (
    "研究生",
    "招生",
    "硕士",
    "博士",
    "复试",
    "推免",
    "夏令营",
    "录取",
    "调剂",
    "报名",
    "考试",
    "简章",
    "公告",
    "通知",
)


class TaskOrchestrator:
    def __init__(self) -> None:
        self.task_executor = CrawlTaskExecutor()
        self.nl_parser = NLTaskParser()
        self.validator = TaskValidator()
        self.search_service = KeywordSearchService()
        self.summarizer = DocumentSummarizer()
        self.rag_service = EvidenceRAGService()
        self.source_resolution_service = SourceResolutionService()

    def create_task(self, session: Session, payload: TaskCreate) -> tuple[CrawlTask, dict | None]:
        source = session.get(Source, payload.source_id)
        if source is None:
            raise ValueError("Source does not exist.")
        if not source_can_run(source):
            raise ValueError(source_readiness_message(source))

        task_type = normalize_task_type(payload.task_type, collection_domain=source.collection_domain)
        task = CrawlTask(
            task_type=task_type,
            source_id=payload.source_id,
            trigger_mode=payload.trigger_mode,
            task_payload={
                **(payload.task_payload or {}),
                "progress": {
                    "stage": "queued",
                    "percent": 0,
                    "message": "任务已创建，等待执行。",
                },
            },
            status="pending",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        result = None
        if payload.execute_immediately:
            background_task_runner.start(task.id)
            task = session.get(CrawlTask, task.id)
        return task, result

    def execute_task(self, session: Session, task_id: int) -> dict:
        task = session.get(CrawlTask, task_id)
        if task is None:
            raise ValueError("Task does not exist.")
        return self.task_executor.execute(session, task)

    def retry_task(self, session: Session, task_id: int) -> dict:
        task = session.get(CrawlTask, task_id)
        if task is None:
            raise ValueError("Task does not exist.")
        task.status = "pending"
        task.error_message = None
        task.started_at = None
        task.finished_at = None
        task_payload = dict(task.task_payload or {})
        task_payload["progress"] = {
            "stage": "queued",
            "percent": 0,
            "message": "任务已重新排队。",
        }
        task.task_payload = task_payload
        session.commit()
        background_task_runner.start(task_id)
        return {"queued": True, "task_id": task_id}

    def parse_nl(self, session: Session, query: str, homepage_url: str | None = None):
        parsed = self.nl_parser.parse(session, query, homepage_url=homepage_url)
        if homepage_url:
            parsed["homepage_url"] = homepage_url
            parsed["resolved_homepage_url"] = homepage_url
        validation = self.validator.validate(session, parsed)
        return parsed, validation

    def execute_nl(self, session: Session, query: str, homepage_url: str | None = None) -> dict:
        workflow_steps = self._new_workflow_steps()
        parse_started_at = utc_now()
        parse_timer = perf_counter()
        parsed, validation = self.parse_nl(session, query, homepage_url=homepage_url)
        self._finish_workflow_step(
            workflow_steps,
            "parse_intent",
            "success" if validation.is_valid else "failed",
            validation.message or f"识别为 {parsed.get('intent', 'unknown')}。",
            parse_started_at,
            parse_timer,
        )
        payload = validation.normalized_payload or parsed
        nl_task = NLTask(
            user_input=query,
            intent=payload.get("intent", "unknown"),
            parsed_payload=payload,
            validation_status="valid" if validation.is_valid else "invalid",
            validation_message=validation.message,
        )
        session.add(nl_task)
        session.commit()
        session.refresh(nl_task)

        if not validation.is_valid:
            return {
                "intent": payload.get("intent", "unknown"),
                "collection_domain": payload.get("collection_domain", "admissions_notice"),
                "university_name": payload.get("university_name"),
                "desired_source_count": payload.get("desired_source_count"),
                "homepage_url": payload.get("homepage_url"),
                "resolved_source_url": None,
                "used_existing_source": False,
                "admissions_levels": payload.get("admissions_levels", []),
                "admissions_tracks": payload.get("admissions_tracks", []),
                "validation_status": "invalid",
                "validation_message": validation.message,
                "task_ids": [],
                "matched_sources": [],
                "resolved_sources": [],
                "debug": {},
                "answer": validation.message or "任务校验失败。",
                "documents": [],
                "workflow_steps": workflow_steps,
                "agent_trace": [],
                "candidate_rankings": [],
                "failure_reason": None,
                "used_browser_explorer": False,
            }

        if payload["intent"] == "discover_sources":
            desired_count = payload.get("desired_source_count") or 10
            resolve_started_at = utc_now()
            resolve_timer = perf_counter()
            try:
                resolved_sources, debug = self.source_resolution_service.discover_sources_from_query(
                    session,
                    query=query,
                    collection_domain=payload["collection_domain"],
                    desired_count=desired_count,
                    homepage_url=payload.get("homepage_url") or payload.get("resolved_homepage_url"),
                    admissions_levels=payload.get("admissions_levels"),
                    admissions_tracks=payload.get("admissions_tracks"),
                )
                resolve_message = f"发现 {len(resolved_sources)} 个候选数据源。"
                if debug.get("source_resolution_strategy") == "source_knowledge":
                    resolve_message = f"数据源知识库命中 {len(resolved_sources)} 个候选。"
                self._finish_workflow_step(
                    workflow_steps,
                    "resolve_source",
                    "success",
                    resolve_message,
                    resolve_started_at,
                    resolve_timer,
                )
                self._finish_workflow_step(
                    workflow_steps,
                    "validate_candidate",
                    "success" if any(item.get("saved") or item.get("validation_status") == "valid" for item in resolved_sources) else "skipped",
                    "候选数据源已完成基础校验。",
                )
            except Exception as exc:
                self._finish_workflow_step(
                    workflow_steps,
                    "resolve_source",
                    "failed",
                    str(exc),
                    resolve_started_at,
                    resolve_timer,
                )
                return {
                    "intent": "discover_sources",
                    "collection_domain": payload["collection_domain"],
                    "university_name": None,
                    "desired_source_count": desired_count,
                    "homepage_url": payload.get("homepage_url"),
                    "resolved_source_url": None,
                    "used_existing_source": False,
                    "admissions_levels": payload.get("admissions_levels", []),
                    "admissions_tracks": payload.get("admissions_tracks", []),
                    "validation_status": "invalid",
                    "validation_message": str(exc),
                    "task_ids": [],
                    "matched_sources": [],
                    "resolved_sources": [],
                    "documents": [],
                    "debug": {},
                    "answer": f"DeepSeek 查找数据源时超时或失败：{exc}",
                    "workflow_steps": workflow_steps,
                    "agent_trace": [],
                    "candidate_rankings": [],
                    "failure_reason": str(exc),
                    "used_browser_explorer": False,
                }
            result = {
                "intent": "discover_sources",
                "collection_domain": payload["collection_domain"],
                "university_name": None,
                "desired_source_count": desired_count,
                "homepage_url": payload.get("homepage_url"),
                "resolved_source_url": None,
                "used_existing_source": False,
                "admissions_levels": payload.get("admissions_levels", []),
                "admissions_tracks": payload.get("admissions_tracks", []),
                "validation_status": "valid",
                "validation_message": None,
                "task_ids": [],
                "matched_sources": [],
                "resolved_sources": resolved_sources,
                "documents": [],
                "debug": debug,
                "answer": self._compose_source_discovery_answer(resolved_sources, payload["collection_domain"]),
                "workflow_steps": workflow_steps,
                "agent_trace": debug.get("agent_trace") or [],
                "candidate_rankings": debug.get("candidate_rankings") or [],
                "failure_reason": debug.get("failure_reason"),
                "used_browser_explorer": bool(debug.get("used_browser_explorer")),
            }
            self._persist_nl_task_output(session, nl_task, result)
            return result

        if payload["intent"] in {"crawl_source", "crawl_admissions"}:
            resolve_started_at = utc_now()
            resolve_timer = perf_counter()
            try:
                resolution = self.source_resolution_service.ensure_source_result(
                    session,
                    university_name=payload.get("university_name") or payload.get("institution"),
                    collection_domain=payload["collection_domain"],
                    admissions_levels=payload.get("admissions_levels"),
                    admissions_tracks=payload.get("admissions_tracks"),
                    homepage_url=payload.get("resolved_homepage_url"),
                    force_refresh_source=bool(payload.get("force_refresh_source")),
                    query=query,
                )
                source = resolution.source
                report = resolution.report
                used_existing = resolution.used_existing_source
                resolve_message = f"使用数据源：{source.name}"
                if resolution.strategy == "source_knowledge":
                    resolve_message = f"数据源知识库命中：{source.name}"
                elif resolution.strategy == "live_discovery":
                    resolve_message = f"实时发现并使用数据源：{source.name}"
                elif resolution.strategy == "existing_source":
                    resolve_message = f"复用已保存数据源：{source.name}"
                self._finish_workflow_step(
                    workflow_steps,
                    "resolve_source",
                    "success",
                    resolve_message,
                    resolve_started_at,
                    resolve_timer,
                )
                self._finish_workflow_step(
                    workflow_steps,
                    "validate_candidate",
                    "success",
                    f"数据源置信度 {report.success_rate:.2f}。",
                )
            except ValueError as exc:
                self._finish_workflow_step(
                    workflow_steps,
                    "resolve_source",
                    "failed",
                    str(exc),
                    resolve_started_at,
                    resolve_timer,
                )
                return {
                    "intent": "crawl_source",
                    "collection_domain": payload["collection_domain"],
                    "university_name": payload.get("university_name"),
                    "desired_source_count": payload.get("desired_source_count"),
                    "homepage_url": payload.get("homepage_url"),
                    "resolved_source_url": None,
                    "used_existing_source": False,
                    "admissions_levels": payload.get("admissions_levels", []),
                    "admissions_tracks": payload.get("admissions_tracks", []),
                    "validation_status": "invalid",
                    "validation_message": str(exc),
                    "task_ids": [],
                    "matched_sources": [],
                    "resolved_sources": [],
                    "debug": {},
                    "answer": str(exc),
                    "documents": [],
                    "workflow_steps": workflow_steps,
                    "agent_trace": [],
                    "candidate_rankings": [],
                    "failure_reason": str(exc),
                    "used_browser_explorer": False,
                }
            create_started_at = utc_now()
            create_timer = perf_counter()
            task, result = self.create_task(
                session,
                TaskCreate(
                    task_type=DOMAIN_TO_TASK_TYPE[normalize_collection_domain(source.collection_domain)],
                    source_id=source.id,
                    trigger_mode="nl_task",
                    task_payload={
                        "time_range": payload.get("time_range", {}),
                        "topic": payload.get("topic", []),
                        "notice_type": payload.get("notice_type", []),
                        "page_limit": 1,
                    },
                    execute_immediately=False,
                ),
            )
            self._finish_workflow_step(
                workflow_steps,
                "create_task",
                "success",
                f"已创建采集任务 #{task.id}。",
                create_started_at,
                create_timer,
            )
            result = {"discovered": 0, "inserted": 0, "duplicates": 0, "errors": 0}
            run_inline = bool(self.nl_parser.settings.mock_llm_enabled)
            if run_inline:
                crawl_started_at = utc_now()
                crawl_timer = perf_counter()
                try:
                    result = self.execute_task(session, task.id)
                    task = session.get(CrawlTask, task.id) or task
                    crawl_status = "success" if task.status in {"success", "partial_success"} else "failed"
                    self._finish_workflow_step(
                        workflow_steps,
                        "crawl",
                        crawl_status,
                        f"采集完成：发现 {result.get('discovered', 0)} 条，入库 {result.get('inserted', 0)} 条。",
                        crawl_started_at,
                        crawl_timer,
                    )
                    self._finish_workflow_step(
                        workflow_steps,
                        "extract",
                        "success" if result.get("inserted", 0) else "skipped",
                        "已完成结构化抽取。" if result.get("inserted", 0) else "没有新增文档可抽取。",
                    )
                except Exception as exc:
                    self._finish_workflow_step(
                        workflow_steps,
                        "crawl",
                        "failed",
                        str(exc),
                        crawl_started_at,
                        crawl_timer,
                    )
                    result = {"discovered": 0, "inserted": 0, "duplicates": 0, "errors": 1}
                    self._finish_workflow_step(workflow_steps, "extract", "skipped", "采集失败，跳过结构化抽取。")
            else:
                background_task_runner.start(task.id)
                self._finish_workflow_step(workflow_steps, "crawl", "pending", "真实站点采集已进入后台执行，避免阻塞接口。")
                self._finish_workflow_step(workflow_steps, "extract", "pending", "采集完成后将进入结构化抽取。")

            documents = self._documents_for_completed_crawl(session, payload, source) if run_inline else []
            self._finish_workflow_step(
                workflow_steps,
                "index_for_qa",
                "success" if documents else "skipped",
                f"已读取 {len(documents)} 条文档用于证据问答。" if documents else "任务完成后可读取新文档用于证据问答。",
            )
            rag_answer = self.rag_service.answer(query=query, documents=documents, intent="search_documents")
            self._finish_workflow_step(
                workflow_steps,
                "answer_with_evidence",
                "success" if rag_answer.citations else "skipped",
                "已基于本次采集文档生成证据型回答。" if rag_answer.citations else "暂无新文档证据，任务完成后可查询已采集数据。",
            )
            nl_task.created_task_id = task.id
            session.commit()
            crawl_answer = self._compose_crawl_answer([{
                "source": source.name,
                **(result or {"discovered": 0, "inserted": 0, "duplicates": 0}),
                "selection_confidence": report.success_rate,
                "used_existing_source": used_existing,
            }])
            answer = f"{crawl_answer}\n\n基于本次采集文档的证据回答：\n{rag_answer.answer}"
            if not run_inline:
                answer = (
                    f"采集任务已创建：\n- {source.name}：任务 #{task.id} 已进入后台执行。\n\n"
                    "任务完成后，点击“查询已采集数据”即可读取新文档并返回带引用答案。"
                )
            confidence_notes = list(rag_answer.confidence_notes)
            if resolution.strategy == "source_knowledge":
                confidence_notes.append("source_selected_from_knowledge_base")
            return {
                "intent": "crawl_source",
                "collection_domain": source.collection_domain,
                "university_name": source.organization_name,
                "desired_source_count": None,
                "homepage_url": payload.get("homepage_url"),
                "resolved_source_url": (source.start_urls_json or [source.base_url])[0],
                "used_existing_source": used_existing,
                "admissions_levels": (source.scope_json or {}).get("admissions_levels") or [],
                "admissions_tracks": (source.scope_json or {}).get("admissions_tracks") or payload.get("admissions_tracks", []),
                "validation_status": "valid",
                "validation_message": None,
                "task_ids": [task.id],
                "matched_sources": [source.name],
                "resolved_sources": [],
                "debug": {
                    "source_resolution_strategy": resolution.strategy,
                    "source_knowledge_hits": resolution.source_knowledge_hits,
                },
                "answer": answer,
                "documents": [serialize_document(document) for document in documents],
                "citations": rag_answer.citations,
                "retrieved_documents": rag_answer.retrieved_documents,
                "confidence_notes": confidence_notes,
                "workflow_steps": workflow_steps,
                "agent_trace": [],
                "candidate_rankings": [],
                "failure_reason": None if not result.get("errors") else "crawl_completed_with_errors",
                "used_browser_explorer": False,
            }

        if payload["intent"] == "list_deadlines":
            payload = {
                **payload,
                "filters": {
                    **(payload.get("filters") or {}),
                    "deadline_only": True,
                },
            }

        effective_intent = payload["intent"]
        if effective_intent in {"search_admissions", "list_deadlines"}:
            effective_intent = "search_documents"
        elif effective_intent == "summarize_admissions":
            effective_intent = "summarize_documents"

        documents, retrieval_notes = self._search_documents_with_trace(session, payload, validation.matched_sources)
        rag_answer = self.rag_service.answer(query=query, documents=documents, intent=effective_intent)
        self._finish_workflow_step(workflow_steps, "answer_with_evidence", "success", "已基于召回文档生成证据型回答。")
        confidence_notes = self._merge_confidence_notes(rag_answer.confidence_notes, retrieval_notes)
        return {
            "intent": effective_intent,
            "collection_domain": payload["collection_domain"],
            "university_name": payload.get("university_name"),
            "desired_source_count": payload.get("desired_source_count"),
            "homepage_url": payload.get("homepage_url"),
            "resolved_source_url": None,
            "used_existing_source": False,
            "admissions_levels": payload.get("admissions_levels", []),
            "admissions_tracks": payload.get("admissions_tracks", []),
            "validation_status": "valid",
            "validation_message": None,
            "task_ids": [],
            "matched_sources": [source.name for source in validation.matched_sources],
            "resolved_sources": [],
            "debug": {},
            "answer": rag_answer.answer,
            "documents": [serialize_document(document) for document in documents],
            "citations": rag_answer.citations,
            "retrieved_documents": rag_answer.retrieved_documents,
            "confidence_notes": confidence_notes,
            "workflow_steps": workflow_steps,
            "agent_trace": [],
            "candidate_rankings": [],
            "failure_reason": None,
            "used_browser_explorer": False,
        }

    def _persist_nl_task_output(self, session: Session, nl_task: NLTask, result: dict) -> None:
        payload = dict(nl_task.parsed_payload or {})
        payload["resolved_sources"] = result.get("resolved_sources") or []
        payload["debug"] = result.get("debug") or {}
        payload["agent_trace"] = result.get("agent_trace") or []
        payload["candidate_rankings"] = result.get("candidate_rankings") or []
        payload["failure_reason"] = result.get("failure_reason")
        payload["used_browser_explorer"] = bool(result.get("used_browser_explorer"))
        if result.get("homepage_url"):
            payload["homepage_url"] = result["homepage_url"]
        nl_task.parsed_payload = payload
        session.commit()

    def ask(self, session: Session, query: str, homepage_url: str | None = None) -> dict:
        parsed, validation = self.parse_nl(session, query, homepage_url=homepage_url)
        payload = validation.normalized_payload or parsed
        ask_notes: list[str] = []
        if payload.get("intent") in {"crawl_source", "crawl_admissions"} and not self._is_explicit_crawl_request(query):
            payload = {
                **payload,
                "intent": "search_admissions" if payload.get("collection_domain") == "admissions_notice" else "search_documents",
                "requires_source_discovery": False,
            }
            validation = self.validator.validate(session, payload)
            payload = validation.normalized_payload or payload
            ask_notes.append("ask_intent_coerced_to_query")
        if payload.get("intent") == "discover_sources" and not self._is_explicit_source_discovery_request(query):
            payload = {
                **payload,
                "intent": "search_admissions" if payload.get("collection_domain") == "admissions_notice" else "search_documents",
                "requires_source_discovery": False,
            }
            validation = self.validator.validate(session, payload)
            payload = validation.normalized_payload or payload
            ask_notes.append("ask_intent_coerced_to_query")
        if payload.get("intent") in {"crawl_source", "crawl_admissions"}:
            return {
                "intent": payload.get("intent"),
                "collection_domain": payload.get("collection_domain", "admissions_notice"),
                "university_name": payload.get("university_name"),
                "desired_source_count": payload.get("desired_source_count"),
                "resolved_source_url": None,
                "used_existing_source": False,
                "admissions_levels": payload.get("admissions_levels", []),
                "validation_status": "invalid",
                "validation_message": "该接口只查询已采集数据。若需要让 DeepSeek 查找数据源或发起采集，请调用 /api/v1/nl/execute。",
                "answer": "该接口只查询已采集数据。若需要发起采集，请调用 /api/v1/nl/execute。",
                "resolved_sources": [],
                "debug": {},
                "documents": [],
                "citations": [],
                "retrieved_documents": [],
                "confidence_notes": self._merge_confidence_notes(ask_notes, ["ask_endpoint_rejects_crawl_intents"]),
            }
        if payload.get("intent") == "discover_sources":
            return {
                "intent": "discover_sources",
                "collection_domain": payload.get("collection_domain", "admissions_notice"),
                "university_name": None,
                "desired_source_count": payload.get("desired_source_count"),
                "resolved_source_url": None,
                "used_existing_source": False,
                "admissions_levels": payload.get("admissions_levels", []),
                "validation_status": "invalid",
                "validation_message": "该接口只查询已采集数据。若需要让 DeepSeek 查找数据源，请调用 /api/v1/nl/execute。",
                "answer": "该接口只查询已采集数据。若需要让 DeepSeek 查找数据源，请调用 /api/v1/nl/execute。",
                "resolved_sources": [],
                "debug": {},
                "documents": [],
                "citations": [],
                "retrieved_documents": [],
                "confidence_notes": ["ask_endpoint_rejects_source_discovery"],
            }
        if not validation.is_valid:
            return {
                "intent": payload.get("intent", "unknown"),
                "answer": validation.message or "查询参数不合法。",
                "documents": [],
                "citations": [],
                "retrieved_documents": [],
                "confidence_notes": self._merge_confidence_notes(ask_notes, ["invalid_query"]),
            }
        documents, retrieval_notes = self._search_documents_with_trace(
            session,
            payload,
            validation.matched_sources,
        )
        effective_intent = payload["intent"]
        if effective_intent in {"search_admissions", "list_deadlines"}:
            effective_intent = "search_documents"
        elif effective_intent == "summarize_admissions":
            effective_intent = "summarize_documents"
        rag_answer = self.rag_service.answer(query=query, documents=documents, intent=effective_intent)
        confidence_notes = self._merge_confidence_notes(rag_answer.confidence_notes, retrieval_notes, ask_notes)
        return {
            "intent": effective_intent,
            "answer": rag_answer.answer,
            "documents": [serialize_document(document) for document in documents],
            "citations": rag_answer.citations,
            "retrieved_documents": rag_answer.retrieved_documents,
            "confidence_notes": confidence_notes,
        }

    def _search_documents_for_payload(
        self,
        session: Session,
        payload: dict,
        matched_sources: list[Source],
    ):
        documents, _ = self._search_documents_with_trace(session, payload, matched_sources)
        return documents

    def _search_documents_with_trace(
        self,
        session: Session,
        payload: dict,
        matched_sources: list[Source],
    ) -> tuple[list, list[str]]:
        start, end = resolve_time_range(payload.get("time_range"))
        deadline_only = (payload.get("filters") or {}).get("deadline_only", False)
        raw_topics = self._topic_list(payload.get("topic"))
        normalized_topics = self._normalize_search_topics(raw_topics)
        retrieval_notes: list[str] = []
        if raw_topics and normalized_topics != raw_topics:
            retrieval_notes.append("normalized_topic_filter")

        institution_name = payload.get("university_name") or payload.get("institution")
        source_ids = [source.id for source in matched_sources] or None
        kwargs = {
            "topics": normalized_topics,
            "doc_types": payload.get("notice_type"),
            "collection_domain": payload.get("collection_domain"),
            "institution_name": None if matched_sources else institution_name,
            "source_ids": source_ids,
            "deadline_only": deadline_only,
            "page": 1,
            "page_size": payload.get("result_limit", 10),
        }
        if deadline_only:
            kwargs["deadline_start"] = start
            kwargs["deadline_end"] = end
        else:
            kwargs["publish_date_start"] = start
            kwargs["publish_date_end"] = end
        documents, _ = self.search_service.search(session, **kwargs)
        if documents:
            return documents, retrieval_notes

        can_relax_topics = bool(raw_topics) and self._should_relax_topic_filter(payload, raw_topics, normalized_topics)
        if can_relax_topics:
            relaxed_kwargs = {**kwargs, "topics": []}
            documents, _ = self.search_service.search(session, **relaxed_kwargs)
            if documents:
                return documents, self._merge_confidence_notes(retrieval_notes, ["relaxed_topic_filter"])

        if source_ids and institution_name:
            relaxed_source_kwargs = {
                **kwargs,
                "topics": [] if can_relax_topics else kwargs["topics"],
                "source_ids": None,
                "institution_name": institution_name,
            }
            documents, _ = self.search_service.search(session, **relaxed_source_kwargs)
            if documents:
                notes = ["relaxed_source_filter"]
                if can_relax_topics:
                    notes.insert(0, "relaxed_topic_filter")
                return documents, self._merge_confidence_notes(retrieval_notes, notes)

        return documents, retrieval_notes

    def _topic_list(self, value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            candidates = [value]
        else:
            candidates = [str(item) for item in value if item is not None]
        return [item.strip() for item in candidates if item.strip()]

    def _normalize_search_topics(self, topics: list[str]) -> list[str]:
        normalized: list[str] = []
        for topic in topics:
            value = re.sub(r"[\s,，。；：:？?！!（）()【】\[\]<>《》\"'“”‘’、/\\|]+", "", topic)
            for token in GENERIC_TOPIC_TOKENS:
                value = value.replace(token, "")
            if not value:
                continue
            expanded: list[str] = []
            if "研究生招生" in value:
                expanded.extend(["研究生招生", "研究生", "招生"])
            elif "研究生" in value:
                expanded.append("研究生")
            if "硕士" in value:
                expanded.append("硕士")
            if "博士" in value:
                expanded.append("博士")
            if "招生" in value:
                expanded.append("招生")
            if "复试" in value:
                expanded.append("复试")
            if "推免" in value:
                expanded.append("推免")
            if "夏令营" in value:
                expanded.append("夏令营")
            if "调剂" in value:
                expanded.append("调剂")
            if "录取" in value:
                expanded.append("录取")
            if not expanded:
                expanded.append(value)
            for item in expanded:
                if item and item not in normalized:
                    normalized.append(item)
        return normalized

    def _should_relax_topic_filter(self, payload: dict, raw_topics: list[str], normalized_topics: list[str]) -> bool:
        text = "".join([
            payload.get("raw_query") or "",
            payload.get("university_name") or "",
            payload.get("institution") or "",
            "".join(raw_topics),
            "".join(normalized_topics),
        ])
        return any(marker in text for marker in ADMISSIONS_TOPIC_MARKERS)

    def _is_explicit_crawl_request(self, query: str) -> bool:
        crawl_tokens = ("采集", "抓取", "爬取", "同步", "更新数据", "拉取", "收集")
        return any(token in query for token in crawl_tokens)

    def _is_explicit_source_discovery_request(self, query: str) -> bool:
        discovery_tokens = ("数据源", "源地址", "源链接", "入口地址", "真实来源", "官方入口")
        return any(token in query for token in discovery_tokens)

    def _merge_confidence_notes(self, *note_groups: list[str]) -> list[str]:
        notes: list[str] = []
        for group in note_groups:
            for note in group or []:
                if note not in notes:
                    notes.append(note)
        return notes

    def _documents_for_completed_crawl(self, session: Session, payload: dict, source: Source):
        documents = self._search_documents_for_payload(session, payload, [source])
        if documents:
            return documents
        documents, _ = self.search_service.search(
            session,
            collection_domain=source.collection_domain,
            source_id=source.id,
            page=1,
            page_size=payload.get("result_limit", 10),
        )
        return documents

    def _compose_query_answer(self, session: Session, query: str, payload: dict, documents):
        if payload["intent"] == "summarize_documents":
            return self.summarizer.summarize(
                query=query,
                documents=documents,
                session=session,
                collection_domain=payload.get("collection_domain"),
            )
        if not documents:
            return "没有找到匹配的数据。"
        lines = [f"找到 {len(documents)} 条相关数据："]
        for document in documents[:5]:
            date_text = document.publish_date.isoformat() if document.publish_date else "日期未知"
            lines.append(f"- {document.title}（{date_text}）")
        return "\n".join(lines)

    def _compose_crawl_answer(self, results: list[dict]) -> str:
        if not results:
            return "采集任务已创建，但当前没有匹配结果。"
        lines = ["采集任务执行完成："]
        for item in results:
            extra = "（复用已有数据源）" if item.get("used_existing_source") else ""
            lines.append(
                f"- {item['source']}{extra}：发现 {item.get('discovered', 0)} 条，入库 {item.get('inserted', 0)} 条，重复 {item.get('duplicates', 0)} 条"
            )
        return "\n".join(lines)

    def _compose_source_discovery_answer(self, sources: list[dict], collection_domain: str) -> str:
        if not sources:
            return "当前没有找到可用的数据源。"
        valid_count = sum(1 for item in sources if item.get("validation_status") == "valid")
        lines = [f"已找到 {len(sources)} 个候选数据源，其中 {valid_count} 个已通过基础校验。"]
        for item in sources[:10]:
            status = "已入库" if item.get("saved") else (item.get("validation_message") or "待确认")
            university_name = item.get("university_name") or item.get("organization_name") or "未知高校"
            source_title = (
                item.get("source_title")
                or item.get("title")
                or item.get("source_url")
                or item.get("failure_reason")
                or "未生成候选标题"
            )
            lines.append(f"- {university_name}：{source_title}（{status}）")
        return "\n".join(lines)

    def _new_workflow_steps(self) -> list[dict]:
        return [
            {
                "name": name,
                "status": "pending",
                "message": "",
                "started_at": None,
                "finished_at": None,
                "latency_ms": None,
            }
            for name in WORKFLOW_STEP_NAMES
        ]

    def _finish_workflow_step(
        self,
        steps: list[dict],
        name: str,
        status: str,
        message: str,
        started_at: datetime | None = None,
        timer_start: float | None = None,
    ) -> None:
        finished_at = utc_now()
        for step in steps:
            if step["name"] != name:
                continue
            step["status"] = status
            step["message"] = message
            step["started_at"] = (started_at or finished_at).isoformat()
            step["finished_at"] = finished_at.isoformat()
            step["latency_ms"] = int((perf_counter() - timer_start) * 1000) if timer_start is not None else 0
            return
