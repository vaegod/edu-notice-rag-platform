from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.document import Document
from app.services.domains import DOMAIN_LABELS, normalize_collection_domain
from app.services.search.keyword_search import serialize_document


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
        return [document for score, document in scored if score > 0] or documents[:5]

    def _score_document(self, query: str, document: Document) -> int:
        terms = self._query_terms(query)
        haystack = " ".join(
            [
                document.title or "",
                document.summary or "",
                document.doc_type or "",
                document.content_category or "",
                document.institution_name or "",
                document.raw_page.raw_text if document.raw_page else "",
            ]
        ).lower()
        score = 0
        for term in terms:
            if term and term.lower() in haystack:
                score += 3 if term.lower() in (document.title or "").lower() else 1
        if document.summary:
            score += 1
        return score

    def _query_terms(self, query: str) -> list[str]:
        ascii_terms = re.findall(r"[A-Za-z0-9_]{2,}", query)
        chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,}", query)
        terms: list[str] = []
        for term in ascii_terms + chinese_terms:
            clean = term.strip()
            if clean and clean not in terms:
                terms.append(clean)
        return terms or [query.strip()]

    def _build_citation(self, index: int, document: Document, query: str) -> dict:
        raw_text = document.raw_page.raw_text if document.raw_page else ""
        snippet_source = document.summary or raw_text or document.title
        return {
            "index": index,
            "document_id": document.id,
            "title": document.title,
            "source_url": document.source_url,
            "snippet": self._snippet(snippet_source, query),
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
