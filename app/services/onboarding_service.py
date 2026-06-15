from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source
from app.models.source_adapter import SourceAdapter
from app.models.source_template import SourceTemplate
from app.services.source_config import (
    deep_merge,
    infer_adapter_code,
    infer_template_code,
    normalize_source_config,
    split_source_extraction_rules,
    split_source_metadata,
)


class SourceOnboardingService:
    def normalize_source_dict(self, session: Session, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        config_json = data.get("config_json") or {}
        if data.get("collection_domain") and not config_json.get("collection_domain"):
            config_json = {**config_json, "collection_domain": data["collection_domain"]}
        start_urls = data.get("start_urls_json") or config_json.get("list_pages") or []

        adapter = self._resolve_adapter(
            session,
            adapter_id=data.get("adapter_id"),
            adapter_code=data.get("adapter_code"),
            config_json=config_json,
        )
        template = self._resolve_template(
            session,
            template_id=data.get("template_id"),
            template_code=data.get("template_code"),
            adapter=adapter,
            config_json=config_json,
        )

        base_config = template.default_config_json if template is not None else {}
        merged_config = deep_merge(base_config, config_json)
        normalized_config = normalize_source_config(merged_config, start_urls=start_urls)

        data["adapter_id"] = adapter.id if adapter is not None else None
        data["template_id"] = template.id if template is not None else None
        data["start_urls_json"] = normalized_config.get("list_pages") or []
        data["config_json"] = normalized_config
        data["organization_name"] = data.get("organization_name") or normalized_config.get("institution")
        data["collection_domain"] = (
            data.get("collection_domain")
            or normalized_config.get("collection_domain")
            or "admissions_notice"
        )
        data["source_type"] = data.get("source_type") or data["collection_domain"]
        data["source_origin"] = data.get("source_origin") or "manual"
        data["entrypoint_url"] = data.get("entrypoint_url")
        data["health_status"] = data.get("health_status") or "healthy"
        data["last_discovered_at"] = data.get("last_discovered_at")
        data["last_success_at"] = data.get("last_success_at")
        data["last_failure_reason"] = data.get("last_failure_reason")
        data["validation_evidence"] = data.get("validation_evidence") or {}
        data["scope_json"] = data.get("scope_json") or {}
        data["resolver_meta_json"] = data.get("resolver_meta_json") or {}
        data["onboarding_status"] = data.get("onboarding_status") or "ready"
        data.pop("adapter_code", None)
        data.pop("template_code", None)
        return data

    def summarize_source_shape(self, source: Source) -> dict[str, Any]:
        return {
            "metadata": split_source_metadata(source.config_json),
            "rules": split_source_extraction_rules(source.config_json),
            "status": source.status,
            "onboarding_status": source.onboarding_status,
            "confidence_score": source.confidence_score,
        }

    def _resolve_adapter(
        self,
        session: Session,
        *,
        adapter_id: int | None,
        adapter_code: str | None,
        config_json: dict[str, Any],
    ) -> SourceAdapter | None:
        adapter = None
        if adapter_id is not None:
            adapter = session.get(SourceAdapter, adapter_id)
        elif adapter_code:
            adapter = session.scalar(select(SourceAdapter).where(SourceAdapter.code == adapter_code))
        else:
            inferred_code = infer_adapter_code(config_json)
            adapter = session.scalar(select(SourceAdapter).where(SourceAdapter.code == inferred_code))
        return adapter

    def _resolve_template(
        self,
        session: Session,
        *,
        template_id: int | None,
        template_code: str | None,
        adapter: SourceAdapter | None,
        config_json: dict[str, Any],
    ) -> SourceTemplate | None:
        template = None
        if template_id is not None:
            template = session.get(SourceTemplate, template_id)
        elif template_code:
            template = session.scalar(select(SourceTemplate).where(SourceTemplate.code == template_code))
        else:
            inferred_code = infer_template_code(config_json)
            if inferred_code:
                template = session.scalar(
                    select(SourceTemplate).where(SourceTemplate.code == inferred_code)
                )
        if template is not None and adapter is not None and template.adapter_id != adapter.id:
            raise ValueError("Template does not match the selected adapter.")
        return template
