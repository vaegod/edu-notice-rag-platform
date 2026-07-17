from __future__ import annotations

import csv
import json
import math
import platform
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.document import Document
from app.models.raw_page import RawPage
from app.models.source import Source
from app.services.llm.nl_parser import NLTaskParser
from app.services.rag.evidence_rag import EvidenceRAGService
from app.services.task.validator import TaskValidator


@dataclass(frozen=True)
class BenchmarkData:
    intent_cases: list[dict[str, Any]]
    evidence_cases: list[dict[str, Any]]
    documents: list[dict[str, Any]]


def load_benchmark_data(path: Path) -> BenchmarkData:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BenchmarkData(
        intent_cases=list(payload["intent_cases"]),
        evidence_cases=list(payload["evidence_cases"]),
        documents=list(payload["documents"]),
    )


def run_platform_benchmark(data_path: Path, *, repeats: int = 30) -> dict[str, Any]:
    data = load_benchmark_data(data_path)
    intent_rows, intent_metrics = _run_intent_benchmark(
        data.intent_cases,
        repeats=max(repeats, 1),
    )
    documents = _build_documents(data.documents)
    evidence_rows, evidence_metrics = _run_evidence_benchmark(
        data.evidence_cases,
        documents,
        repeats=max(repeats, 1),
    )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "benchmark": {
            "name": "edu-notice-platform-offline-benchmark",
            "scope": "API-free intent/scope and evidence-grounding regression",
            "case_count": len(intent_rows) + len(evidence_rows),
            "intent_case_count": len(intent_rows),
            "evidence_case_count": len(evidence_rows),
            "document_count": len(documents),
            "query_repeats": max(repeats, 1),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "metrics": {
            "intent_scope": intent_metrics,
            "evidence_qa": evidence_metrics,
        },
        "cases": [*intent_rows, *evidence_rows],
    }


def write_benchmark_outputs(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_cases_csv(report["cases"], output_dir / "benchmark_cases.csv")
    (output_dir / "README.md").write_text(_render_summary(report), encoding="utf-8")
    (output_dir / "bad_cases.md").write_text(_render_bad_cases(report), encoding="utf-8")


def _run_intent_benchmark(
    cases: list[dict[str, Any]],
    *,
    repeats: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    parser = NLTaskParser()
    validator = TaskValidator()
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []

    with Session(engine) as session:
        for case in cases:
            parsed: dict[str, Any] = {}
            validation = None
            parser.parse(
                session,
                case["query"],
                homepage_url=case.get("homepage_url"),
            )
            started = time.perf_counter_ns()
            for _ in range(repeats):
                parsed = parser.parse(
                    session,
                    case["query"],
                    homepage_url=case.get("homepage_url"),
                )
                validation = validator.validate(session, parsed)
            latency_ms = (time.perf_counter_ns() - started) / 1_000_000 / repeats
            latencies.append(latency_ms)
            assert validation is not None
            observed_university = (
                validation.normalized_payload.get("university_name")
                or parsed.get("university_name")
            )
            intent_ok = parsed.get("intent") == case["expected_intent"]
            validation_ok = validation.is_valid is bool(case["expected_valid"])
            expected_university = case.get("expected_university")
            university_ok = (
                expected_university is None or observed_university == expected_university
            )
            rows.append(
                {
                    "suite": "intent_scope",
                    "case_id": case["id"],
                    "query": case["query"],
                    "expected": {
                        "intent": case["expected_intent"],
                        "valid": case["expected_valid"],
                        "university": expected_university,
                    },
                    "observed": {
                        "intent": parsed.get("intent"),
                        "valid": validation.is_valid,
                        "university": observed_university,
                        "message": validation.message,
                    },
                    "checks": {
                        "intent_ok": intent_ok,
                        "validation_ok": validation_ok,
                        "university_ok": university_ok,
                    },
                    "passed": intent_ok and validation_ok and university_ok,
                    "latency_ms": round(latency_ms, 4),
                }
            )
    engine.dispose()

    expected_refusals = [not bool(case["expected_valid"]) for case in cases]
    observed_refusals = [not bool(row["observed"]["valid"]) for row in rows]
    refusal_precision, refusal_recall = _binary_precision_recall(
        expected_refusals,
        observed_refusals,
    )
    metrics = {
        "intent_accuracy": _mean_bool(row["checks"]["intent_ok"] for row in rows),
        "validation_accuracy": _mean_bool(
            row["checks"]["validation_ok"] for row in rows
        ),
        "university_accuracy": _mean_bool(
            row["checks"]["university_ok"] for row in rows
        ),
        "end_to_end_case_accuracy": _mean_bool(row["passed"] for row in rows),
        "refusal_precision": refusal_precision,
        "refusal_recall": refusal_recall,
        "avg_latency_ms": round(statistics.fmean(latencies), 4),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 4),
    }
    return rows, metrics


def _run_evidence_benchmark(
    cases: list[dict[str, Any]],
    documents: list[Document],
    *,
    repeats: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    service = EvidenceRAGService()
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    citation_checks: list[bool] = []
    recall_at_1: list[float] = []
    recall_at_3: list[float] = []
    recall_at_5: list[float] = []
    reciprocal_ranks: list[float] = []

    for case in cases:
        answer = service.answer(query=case["query"], documents=documents)
        started = time.perf_counter_ns()
        for _ in range(repeats):
            answer = service.answer(query=case["query"], documents=documents)
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000 / repeats
        latencies.append(latency_ms)

        ranking = [item["id"] for item in answer.retrieved_documents]
        expected_ids = [int(item) for item in case.get("expected_document_ids", [])]
        observed_refusal = not answer.citations
        refusal_ok = observed_refusal is bool(case.get("expect_refusal", False))
        r1 = _recall_at_k(expected_ids, ranking, 1)
        r3 = _recall_at_k(expected_ids, ranking, 3)
        r5 = _recall_at_k(expected_ids, ranking, 5)
        rr = _reciprocal_rank(expected_ids, ranking)
        if expected_ids:
            recall_at_1.append(r1)
            recall_at_3.append(r3)
            recall_at_5.append(r5)
            reciprocal_ranks.append(rr)

        grounded = [
            _citation_is_grounded(citation, documents) for citation in answer.citations
        ]
        citation_checks.extend(grounded)
        citation_ok = bool(grounded) and all(grounded) if not observed_refusal else True
        passed = refusal_ok and citation_ok and (not expected_ids or r5 == 1.0)
        rows.append(
            {
                "suite": "evidence_qa",
                "case_id": case["id"],
                "query": case["query"],
                "expected": {
                    "document_ids": expected_ids,
                    "refusal": bool(case.get("expect_refusal", False)),
                },
                "observed": {
                    "document_ids": ranking,
                    "refusal": observed_refusal,
                    "citation_count": len(answer.citations),
                    "confidence_notes": answer.confidence_notes,
                },
                "checks": {
                    "refusal_ok": refusal_ok,
                    "citations_grounded": citation_ok,
                    "recall_at_1": r1,
                    "recall_at_3": r3,
                    "recall_at_5": r5,
                    "reciprocal_rank": rr,
                },
                "passed": passed,
                "latency_ms": round(latency_ms, 4),
            }
        )

    metrics = {
        "recall_at_1": round(statistics.fmean(recall_at_1), 4),
        "recall_at_3": round(statistics.fmean(recall_at_3), 4),
        "recall_at_5": round(statistics.fmean(recall_at_5), 4),
        "mrr": round(statistics.fmean(reciprocal_ranks), 4),
        "refusal_accuracy": _mean_bool(
            row["checks"]["refusal_ok"] for row in rows
        ),
        "citation_grounded_rate": _mean_bool(citation_checks),
        "end_to_end_case_accuracy": _mean_bool(row["passed"] for row in rows),
        "avg_latency_ms": round(statistics.fmean(latencies), 4),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 4),
    }
    return rows, metrics


def _build_documents(items: list[dict[str, Any]]) -> list[Document]:
    sources: dict[str, Source] = {}
    documents: list[Document] = []
    for item in items:
        institution = item["institution_name"]
        source = sources.get(institution)
        if source is None:
            source = Source(
                id=len(sources) + 1,
                name=f"{institution}研究生招生数据源",
                organization_name=institution,
                base_url=item["source_url"],
                start_urls_json=[item["source_url"]],
            )
            sources[institution] = source
        raw_page = RawPage(
            id=int(item["id"]),
            source_id=source.id,
            url=item["source_url"],
            title=item["title"],
            raw_html=f"<article>{item['raw_text']}</article>",
            raw_text=item["raw_text"],
            content_hash=f"benchmark-{item['id']}",
            http_status=200,
        )
        raw_page.source = source
        document = Document(
            id=int(item["id"]),
            raw_page_id=raw_page.id,
            collection_domain="admissions_notice",
            institution_name=institution,
            doc_type=item["doc_type"],
            title=item["title"],
            publish_date=date.fromisoformat(item["publish_date"]),
            deadline=(
                date.fromisoformat(item["deadline"]) if item.get("deadline") else None
            ),
            summary=item["summary"],
            source_url=item["source_url"],
        )
        document.raw_page = raw_page
        documents.append(document)
    return documents


def _citation_is_grounded(citation: dict[str, Any], documents: list[Document]) -> bool:
    document = next(
        (item for item in documents if item.id == citation.get("document_id")),
        None,
    )
    if document is None or citation.get("source_url") != document.source_url:
        return False
    field = citation.get("evidence_field")
    if field == "summary":
        evidence = document.summary or ""
    elif field == "raw_text":
        evidence = document.raw_page.raw_text if document.raw_page else ""
    elif field == "title":
        evidence = document.title
    else:
        return False
    snippet = " ".join(str(citation.get("snippet", "")).split())
    normalized_evidence = " ".join(evidence.split())
    return bool(snippet) and snippet in normalized_evidence


def _recall_at_k(expected: list[int], ranking: list[int], cutoff: int) -> float:
    if not expected:
        return 0.0
    return len(set(expected) & set(ranking[:cutoff])) / len(set(expected))


def _reciprocal_rank(expected: list[int], ranking: list[int]) -> float:
    expected_set = set(expected)
    for rank, document_id in enumerate(ranking, start=1):
        if document_id in expected_set:
            return 1 / rank
    return 0.0


def _mean_bool(values: Any) -> float:
    materialized = [float(bool(value)) for value in values]
    return round(statistics.fmean(materialized), 4) if materialized else 0.0


def _binary_precision_recall(
    expected: list[bool], observed: list[bool]
) -> tuple[float, float]:
    true_positive = sum(1 for gold, prediction in zip(expected, observed, strict=True) if gold and prediction)
    false_positive = sum(1 for gold, prediction in zip(expected, observed, strict=True) if not gold and prediction)
    false_negative = sum(1 for gold, prediction in zip(expected, observed, strict=True) if gold and not prediction)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    return round(precision, 4), round(recall, 4)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _write_cases_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "suite",
        "case_id",
        "query",
        "expected",
        "observed",
        "checks",
        "passed",
        "latency_ms",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            for field in ("expected", "observed", "checks"):
                serialized[field] = json.dumps(serialized[field], ensure_ascii=False)
            writer.writerow(serialized)


def _render_summary(report: dict[str, Any]) -> str:
    intent = report["metrics"]["intent_scope"]
    evidence = report["metrics"]["evidence_qa"]
    return "\n".join(
        [
            "# 离线平台评测结果",
            "",
            (
                f"> 共 {report['benchmark']['case_count']} 条固定样例："
                f"{report['benchmark']['intent_case_count']} 条意图/边界样例，"
                f"{report['benchmark']['evidence_case_count']} 条证据问答样例。"
            ),
            "",
            "| 评测项 | 结果 |",
            "| --- | ---: |",
            f"| 意图识别准确率 | {intent['intent_accuracy']:.2%} |",
            f"| 任务边界判定准确率 | {intent['validation_accuracy']:.2%} |",
            f"| 越界拒绝 Precision / Recall | {intent['refusal_precision']:.2%} / {intent['refusal_recall']:.2%} |",
            f"| 证据检索 Recall@1 / Recall@3 / Recall@5 | {evidence['recall_at_1']:.2%} / {evidence['recall_at_3']:.2%} / {evidence['recall_at_5']:.2%} |",
            f"| 证据检索 MRR | {evidence['mrr']:.4f} |",
            f"| 无证据拒答准确率 | {evidence['refusal_accuracy']:.2%} |",
            f"| Citation 可追溯率 | {evidence['citation_grounded_rate']:.2%} |",
            "",
            "## 口径说明",
            "",
            "- 全部样例在 `MOCK_LLM_ENABLED=true` / 无 API Key 的规则回退链路上运行，用于稳定回归，不代表在线 DeepSeek 效果。",
            "- Citation 可追溯要求 document、source URL、证据字段和 snippet 均能回到同一条已采集文档。",
            "- 无证据拒答专门检查不相关问题不会被强行绑定到任意招生公告。",
            "- 本评测不访问真实高校网站；真实站点波动、反爬和页面改版需要单独在线回归。",
            "",
            "逐题结果见 `benchmark_cases.csv`，失败样例见 `bad_cases.md`，机器可读结果见 `benchmark_summary.json`。",
            "",
        ]
    )


def _render_bad_cases(report: dict[str, Any]) -> str:
    failures = [row for row in report["cases"] if not row["passed"]]
    lines = [
        "# 失败样例",
        "",
        "该文件只记录固定离线评测中未通过的样例，便于后续回归。",
        "",
    ]
    if not failures:
        lines.extend(
            [
                "当前版本在这组小规模固定样例上没有失败项。扩展语料和真实站点后仍需重新评测。",
                "",
            ]
        )
        return "\n".join(lines)
    for row in failures:
        failed_checks = [
            name for name, value in row["checks"].items() if value is False or value == 0
        ]
        lines.append(
            f"- **{row['case_id']}** `{row['suite']}` {row['query']}："
            f"未通过 {', '.join(failed_checks) or '综合检查'}。"
        )
    lines.append("")
    return "\n".join(lines)
