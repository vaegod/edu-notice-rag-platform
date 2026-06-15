from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "高校官网采集工作台"
    app_version: str = "0.1.0"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default=f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}",
        alias="DATABASE_URL",
    )

    crawler_timeout_seconds: int = 20
    probe_timeout_seconds: int = 4
    probe_max_candidate_urls: int = 2
    discovery_max_urls: int = 50
    discovery_classify_top_k: int = 20
    discovery_schema_top_k: int = 3
    discovery_max_depth: int = 3
    trafilatura_timeout_seconds: int = 8
    trafilatura_enabled: bool = True
    crawl4ai_enabled: bool = True
    crawler_user_agent: str = (
        "Mozilla/5.0 (compatible; EduAdmissionsDetector/0.1; +https://example.local)"
    )
    scrapy_crawl_timeout_seconds: int = 60
    scrapy_concurrent_requests: int = 8
    scrapy_obey_robots_txt: bool = False
    scrapy_playwright_enabled: bool = True
    playwright_browser_type: str = "chromium"
    playwright_headless: bool = True
    playwright_navigation_timeout_ms: int = 30000
    admissions_discovery_max_pages: int = 20
    admissions_discovery_max_depth: int = 3
    admissions_discovery_timeout_seconds: int = 45
    max_task_items: int = 50
    default_crawl_hours: int = 24
    hot_source_crawl_hours: int = 6

    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Shanghai"
    auto_seed_real_sources: bool = False
    auto_sync_c9_source_knowledge: bool = True
    source_knowledge_max_age_days: int = 30
    c9_source_knowledge_path: str = Field(
        default=str((BASE_DIR / "config_templates" / "c9_source_knowledge.json").resolve()),
        alias="C9_SOURCE_KNOWLEDGE_PATH",
    )

    siliconflow_api_key: str | None = Field(default=None, alias="SILICONFLOW_API_KEY")
    siliconflow_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        alias="SILICONFLOW_BASE_URL",
    )
    siliconflow_text_model: str = Field(
        default="deepseek-ai/DeepSeek-V3.2",
        alias="SILICONFLOW_TEXT_MODEL",
    )
    siliconflow_nl_parse_model: str | None = Field(
        default=None,
        alias="SILICONFLOW_NL_PARSE_MODEL",
    )
    siliconflow_source_resolve_model: str | None = Field(
        default=None,
        alias="SILICONFLOW_SOURCE_RESOLVE_MODEL",
    )
    llm_timeout_seconds: int = 40
    llm_max_retries: int = 2
    llm_retry_backoff_seconds: float = 1.0
    mock_llm_enabled: bool = False

    max_search_results: int = 20
    max_nl_crawl_days: int = 62

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def prompts_dir(self) -> Path:
        return BASE_DIR / "app" / "prompts"


@lru_cache
def get_settings() -> Settings:
    return Settings()
