from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.models.document import Document
from app.services.c9_scope import infer_c9_university_from_text
from app.services.domains import DOMAIN_LABELS, normalize_collection_domain
from app.services.search.keyword_search import serialize_document


QUERY_STOP_TERMS = {
    "什么",
    "哪些",
    "怎样",
    "怎么",
    "如何",
    "最近",
    "最新",
    "一下",
    "相关",
    "信息",
    "情况",
    "请问",
    "帮我",
    "查询",
    "查看",
}

DOMAIN_TERMS = {
    "研究生",
    "招生",
    "硕士",
    "博士",
    "复试",
    "推免",
    "夏令营",
    "调剂",
    "拟录取",
    "录取",
    "简章",
    "报名",
    "截止",
}

MIN_RETRIEVAL_SCORE = 6.0


@dataclass(frozen=True)
class EvidenceAnswer:
    answer: str
    citations: list[dict]
    retrieved_documents: list[dict]
    confidence_notes: list[str]


class EvidenceRAGService:
    """Lightweight evidence-grounded QA over already collected documents."""

    def answer(self, *, query: str, documents: list[Document], intent: str | None = None) -> EvidenceAnswer:
        ranked = self._rank_documents(query=query, documents=documents)
        if not ranked:
            return EvidenceAnswer(
                answer="未找到足够证据回答这个问题。请先采集相关数据，或放宽查询条件后再试。",
                citations=[],
                retrieved_documents=[],
                confidence_notes=["no_retrieved_documents"],
            )

        citations = [self._build_citation(index + 1, document, query) for index, document in enumerate(ranked[:5])]
        answer = self._compose_grounded_answer(
            documents=ranked,
            citations=citations,
            intent=intent or "search_documents",
        )
        retrieved_documents = []
        for score, document in [(self._score_document(query, item), item) for item in ranked[:10]]:
            payload = serialize_document(document)
            payload["retrieval_score"] = score
            retrieved_documents.append(payload)

        return EvidenceAnswer(
            answer=answer,
            citations=citations,
            retrieved_documents=retrieved_documents,
            confidence_notes=[
                "answer_grounded_in_retrieved_documents",
                "lightweight_keyword_retrieval",
            ],
        )

    def _rank_documents(self, *, query: str, documents: list[Document]) -> list[Document]:
        scored = [(self._score_document(query, document), document) for document in documents]
        scored.sort(key=lambda item: (item[0], item[1].publish_date or item[1].created_at.date()), reverse=True)
        return [document for score, document in scored if score >= MIN_RETRIEVAL_SCORE]

    def _score_document(self, query: str, document: Document) -> float:
        terms = self._query_terms(query)
        title = (document.title or "").lower()
        summary = (document.summary or "").lower()
        metadata = " ".join(
            [
                document.doc_type or "",
                document.content_category or "",
                document.institution_name or "",
            ]
        ).lower()
        raw_text = (document.raw_page.raw_text if document.raw_page else "").lower()
        score = 0.0
        for term in terms:
            normalized = term.lower()
            if normalized in title:
                score += 4.0
            elif normalized in metadata:
                score += 3.0
            elif normalized in summary:
                score += 2.0
            elif normalized in raw_text:
                score += 1.0
        return score

    def _query_terms(self, query: str) -> list[str]:
        ascii_terms = re.findall(r"[A-Za-z0-9_]{2,}", query)
        chinese_sequences = re.findall(r"[\u4e00-\u9fff]{2,}", query)
        terms: list[str] = []
        canonical_university = infer_c9_university_from_text(query)
        if canonical_university:
            terms.append(canonical_university)
        for term in ascii_terms:
            clean = term.strip()
            if clean and clean.lower() not in QUERY_STOP_TERMS and clean not in terms:
                terms.append(clean)
        for sequence in chinese_sequences:
            for domain_term in DOMAIN_TERMS:
                if domain_term in sequence and domain_term not in terms:
                    terms.append(domain_term)
            for size in (4, 3, 2):
                for index in range(max(len(sequence) - size + 1, 0)):
                    term = sequence[index : index + size]
                    if term not in QUERY_STOP_TERMS and term not in terms:
                        terms.append(term)
        return terms or [query.strip()]

    def _build_citation(self, index: int, document: Document, query: str) -> dict:
        raw_text = document.raw_page.raw_text if document.raw_page else ""
        if document.summary:
            evidence_field = "summary"
            snippet_source = document.summary
        elif raw_text:
            evidence_field = "raw_text"
            snippet_source = raw_text
        else:
            evidence_field = "title"
            snippet_source = document.title
        normalized_evidence = re.sub(r"\s+", " ", snippet_source or "").strip()
        return {
            "index": index,
            "document_id": document.id,
            "raw_page_id": document.raw_page_id,
            "source_id": document.raw_page.source_id if document.raw_page else None,
            "title": document.title,
            "source_url": document.source_url,
            "snippet": self._snippet(snippet_source, query),
            "evidence_field": evidence_field,
            "evidence_hash": hashlib.sha256(normalized_evidence.encode("utf-8")).hexdigest()[:16],
            "publish_date": document.publish_date.isoformat() if document.publish_date else None,
            "collection_domain": document.collection_domain,
        }

    def _snippet(self, text: str | None, query: str, size: int = 180) -> str:
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip()
        terms = self._query_terms(query)
        lower_text = normalized.lower()
        start = 0
        for term in terms:
            pos = lower_text.find(term.lower())
            if pos >= 0:
                start = max(pos - 40, 0)
                break
        snippet = normalized[start : start + size].strip()
        return snippet

    def _compose_grounded_answer(self, *, documents: list[Document], citations: list[dict], intent: str) -> str:
        domain = normalize_collection_domain(documents[0].collection_domain if documents else None)
        label = DOMAIN_LABELS.get(domain, domain)
        if intent == "summarize_documents":
            lines = [f"根据已采集证据，共找到 {len(documents)} 条{label}相关内容："]
        else:
            lines = [f"找到 {len(documents)} 条可引用的{label}证据："]
        for citation in citations:
            date_text = citation["publish_date"] or "日期未知"
            lines.append(f"[{citation['index']}] {citation['title']}（{date_text}）")
        lines.append("以上回答仅基于当前召回的已采集文档。")
        return "\n".join(lines)
