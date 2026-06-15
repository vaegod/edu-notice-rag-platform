from __future__ import annotations

from collections import Counter
import json

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document
from app.services.domains import DOMAIN_LABELS, normalize_collection_domain
from app.services.llm.siliconflow_client import SiliconFlowClient, load_prompt_template


class DocumentSummarizer:
    def __init__(self, llm_client: SiliconFlowClient | None = None) -> None:
        self.settings = get_settings()
        self.llm_client = llm_client or SiliconFlowClient()
        self.prompt_template = load_prompt_template("document_summary.txt")

    def summarize(
        self,
        *,
        query: str,
        documents: list[Document],
        session: Session | None = None,
        collection_domain: str | None = None,
    ) -> str:
        if not documents:
            return "当前没有匹配的数据。"

        collection_domain = normalize_collection_domain(
            collection_domain or documents[0].collection_domain
        )
        if self.llm_client.enabled and not self.settings.mock_llm_enabled:
            payload = [
                {
                    "title": item.title,
                    "collection_domain": item.collection_domain,
                    "doc_type": item.doc_type,
                    "content_category": item.content_category,
                    "publish_date": item.publish_date.isoformat() if item.publish_date else None,
                    "deadline": item.deadline.isoformat() if item.deadline else None,
                    "summary": item.summary,
                }
                for item in documents[:10]
            ]
            try:
                return self.llm_client.chat_text(
                    system_prompt=self.prompt_template,
                    user_prompt=(
                        f"主题: {collection_domain}\n"
                        f"用户问题: {query}\n"
                        f"文档列表:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                    biz_type="document_summarize",
                    session=session,
                )
            except Exception:
                pass
        return self._fallback_summary(documents, collection_domain)

    def _fallback_summary(self, documents: list[Document], collection_domain: str) -> str:
        label = DOMAIN_LABELS.get(collection_domain, collection_domain)
        type_counter = Counter(
            item.content_category or item.doc_type or "其他"
            for item in documents
        )
        top_types = "，".join(f"{name}{count}条" for name, count in type_counter.most_common(3))
        lines = [f"共找到 {len(documents)} 条{label}相关数据，主要类型包括：{top_types}。"]
        for document in documents[:5]:
            publish_text = document.publish_date.isoformat() if document.publish_date else "日期未知"
            deadline_text = f"，截止 {document.deadline.isoformat()}" if document.deadline else ""
            lines.append(f"- {document.title}（{publish_text}{deadline_text}）")
        return "\n".join(lines)


AdmissionsSummarizer = DocumentSummarizer
NoticeSummarizer = DocumentSummarizer
