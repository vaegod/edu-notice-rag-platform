from __future__ import annotations

COLLECTION_DOMAIN_SCHOOL_PROFILE = "school_profile"
COLLECTION_DOMAIN_ADMISSIONS_NOTICE = "admissions_notice"
COLLECTION_DOMAIN_NEWS_CENTER = "news_center"

COLLECTION_DOMAINS = {
    COLLECTION_DOMAIN_SCHOOL_PROFILE,
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
    COLLECTION_DOMAIN_NEWS_CENTER,
}

LEGACY_TASK_TYPE_ALIASES = {
    "crawl_admissions": "crawl_admissions_notice",
    "crawl_notice": "crawl_admissions_notice",
}

TASK_TYPE_TO_DOMAIN = {
    "crawl_school_profile": COLLECTION_DOMAIN_SCHOOL_PROFILE,
    "crawl_admissions_notice": COLLECTION_DOMAIN_ADMISSIONS_NOTICE,
    "crawl_news_center": COLLECTION_DOMAIN_NEWS_CENTER,
    **LEGACY_TASK_TYPE_ALIASES,
}

DOMAIN_TO_TASK_TYPE = {
    COLLECTION_DOMAIN_SCHOOL_PROFILE: "crawl_school_profile",
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE: "crawl_admissions_notice",
    COLLECTION_DOMAIN_NEWS_CENTER: "crawl_news_center",
}

DOMAIN_LABELS = {
    COLLECTION_DOMAIN_SCHOOL_PROFILE: "学校概况",
    COLLECTION_DOMAIN_ADMISSIONS_NOTICE: "招生公告",
    COLLECTION_DOMAIN_NEWS_CENTER: "新闻中心",
}


def normalize_collection_domain(value: str | None) -> str:
    normalized = (value or COLLECTION_DOMAIN_ADMISSIONS_NOTICE).strip().lower()
    if normalized in COLLECTION_DOMAINS:
        return normalized
    if normalized == "admissions":
        return COLLECTION_DOMAIN_ADMISSIONS_NOTICE
    return COLLECTION_DOMAIN_ADMISSIONS_NOTICE


def normalize_task_type(value: str | None, *, collection_domain: str | None = None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in LEGACY_TASK_TYPE_ALIASES:
        return LEGACY_TASK_TYPE_ALIASES[normalized]
    if normalized in TASK_TYPE_TO_DOMAIN:
        return normalized
    if collection_domain:
        return DOMAIN_TO_TASK_TYPE[normalize_collection_domain(collection_domain)]
    return DOMAIN_TO_TASK_TYPE[COLLECTION_DOMAIN_ADMISSIONS_NOTICE]


def task_type_to_domain(task_type: str | None, *, fallback: str | None = None) -> str:
    normalized = normalize_task_type(task_type, collection_domain=fallback)
    return TASK_TYPE_TO_DOMAIN.get(normalized, normalize_collection_domain(fallback))
