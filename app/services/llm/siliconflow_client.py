from __future__ import annotations

import json
from pathlib import Path
import re
import time

import httpx
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.core.config import get_settings
from app.models.llm_log import LLMLog


def load_prompt_template(name: str) -> str:
    settings = get_settings()
    path = Path(settings.prompts_dir) / name
    return path.read_text(encoding="utf-8")


class SiliconFlowClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.siliconflow_api_key)

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        biz_type: str,
        session: Session | None = None,
        related_id: int | None = None,
    ) -> dict:
        if not self.enabled:
            raise RuntimeError("SILICONFLOW_API_KEY is not configured.")

        payload = self._build_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            biz_type=biz_type,
            json_mode=True,
        )
        model_name = payload["model"]
        prompt_text = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
        with httpx.Client(
            base_url=self.settings.siliconflow_base_url,
            timeout=self._timeout_for_biz_type(biz_type),
        ) as client:
            try:
                response_json = self._request_with_retry(client=client, payload=payload)
                content = self._extract_message_content(response_json)
                parsed = self._parse_json_content(content)
                self._persist_log(
                    session=session,
                    biz_type=biz_type,
                    related_id=related_id,
                    prompt_text=prompt_text,
                    response_text=content,
                    parsed_json=parsed,
                    success=True,
                    model_name=model_name,
                )
                return parsed
            except Exception as exc:
                self._persist_log(
                    session=session,
                    biz_type=biz_type,
                    related_id=related_id,
                    prompt_text=prompt_text,
                    response_text=locals().get("content"),
                    parsed_json=None,
                    success=False,
                    error_message=str(exc),
                    model_name=model_name,
                )
                raise

    def chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        biz_type: str,
        session: Session | None = None,
        related_id: int | None = None,
    ) -> str:
        if not self.enabled:
            raise RuntimeError("SILICONFLOW_API_KEY is not configured.")

        payload = self._build_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            biz_type=biz_type,
            json_mode=False,
        )
        model_name = payload["model"]
        prompt_text = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
        with httpx.Client(
            base_url=self.settings.siliconflow_base_url,
            timeout=self._timeout_for_biz_type(biz_type),
        ) as client:
            try:
                response_json = self._request_with_retry(client=client, payload=payload)
                content = self._extract_message_content(response_json)
                self._persist_log(
                    session=session,
                    biz_type=biz_type,
                    related_id=related_id,
                    prompt_text=prompt_text,
                    response_text=content,
                    parsed_json=None,
                    success=True,
                    model_name=model_name,
                )
                return content.strip()
            except Exception as exc:
                self._persist_log(
                    session=session,
                    biz_type=biz_type,
                    related_id=related_id,
                    prompt_text=prompt_text,
                    response_text=locals().get("content"),
                    parsed_json=None,
                    success=False,
                    error_message=str(exc),
                    model_name=model_name,
                )
                raise

    def _request_with_retry(self, *, client: httpx.Client, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.settings.siliconflow_api_key}"}
        last_exc: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                response = client.post("/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                if status_code not in {429, 500, 502, 503, 504} or attempt >= self.settings.llm_max_retries:
                    raise
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= self.settings.llm_max_retries:
                    raise
            time.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unexpected LLM retry flow.")

    def _build_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        biz_type: str,
        json_mode: bool,
    ) -> dict:
        model_name = self._model_for_biz_type(biz_type)
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "enable_thinking": False,
        }
        if json_mode and self._supports_json_response_format(model_name):
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _model_for_biz_type(self, biz_type: str) -> str:
        if biz_type == "nl_parse" and self.settings.siliconflow_nl_parse_model:
            return self.settings.siliconflow_nl_parse_model
        if biz_type == "source_resolve" and self.settings.siliconflow_source_resolve_model:
            return self.settings.siliconflow_source_resolve_model
        return self.settings.siliconflow_text_model

    def _supports_json_response_format(self, model_name: str) -> bool:
        return bool(model_name.strip())

    def _timeout_for_biz_type(self, biz_type: str) -> float:
        if biz_type == "source_resolve":
            return max(float(self.settings.llm_timeout_seconds), 180.0)
        return float(self.settings.llm_timeout_seconds)

    def _extract_message_content(self, response_json: dict) -> str:
        content = response_json["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        text_parts.append(text)
            return "\n".join(part for part in text_parts if part).strip()
        return json.dumps(content, ensure_ascii=False)

    def _parse_json_content(self, content: str) -> dict:
        normalized = content.strip()
        fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, re.DOTALL)
        if fence_match:
            normalized = fence_match.group(1).strip()
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            json_match = re.search(r"(\{.*\}|\[.*\])", normalized, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            raise

    def _persist_log(
        self,
        *,
        session: Session | None,
        biz_type: str,
        related_id: int | None,
        prompt_text: str,
        response_text: str | None,
        parsed_json: dict | None,
        success: bool,
        model_name: str | None = None,
        error_message: str | None = None,
    ) -> None:
        actual_model_name = model_name or self.settings.siliconflow_text_model
        try:
            log = LLMLog(
                biz_type=biz_type,
                related_id=related_id,
                model_name=actual_model_name,
                prompt_text=prompt_text,
                response_text=response_text,
                parsed_json=parsed_json,
                success=success,
                error_message=error_message,
            )
            if session is not None:
                try:
                    log_session = get_session_factory()()
                    try:
                        log_session.add(log)
                        log_session.commit()
                    finally:
                        log_session.close()
                    return
                except Exception:
                    pass
            fallback_session = get_session_factory()()
            try:
                fallback_session.add(
                    LLMLog(
                        biz_type=biz_type,
                        related_id=related_id,
                        model_name=actual_model_name,
                        prompt_text=prompt_text,
                        response_text=response_text,
                        parsed_json=parsed_json,
                        success=success,
                        error_message=error_message,
                    )
                )
                fallback_session.commit()
            finally:
                fallback_session.close()
        except Exception:
            return
