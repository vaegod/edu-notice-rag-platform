from __future__ import annotations

from datetime import date, datetime, timedelta
import re


_CHINESE_NUMBER_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _normalize_separators(value: str) -> str:
    return (
        value.strip()
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
    )


def parse_date_string(value: str | None) -> date | None:
    if not value:
        return None
    normalized = _normalize_separators(value)
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", normalized)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_datetime_string(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = _normalize_separators(value)
    match = re.search(
        r"(20\d{2})-(\d{1,2})-(\d{1,2})[ T]?(\d{1,2})[:：](\d{2})",
        normalized,
    )
    if not match:
        parsed_date = parse_date_string(value)
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, datetime.min.time())
    year, month, day, hour, minute = map(int, match.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def parse_chinese_number(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if len(token) == 2 and token.startswith("十"):
        return 10 + _CHINESE_NUMBER_MAP.get(token[1], 0)
    if len(token) == 2 and token.endswith("十"):
        return _CHINESE_NUMBER_MAP.get(token[0], 0) * 10
    if len(token) == 3 and token[1] == "十":
        return _CHINESE_NUMBER_MAP.get(token[0], 0) * 10 + _CHINESE_NUMBER_MAP.get(
            token[2], 0
        )
    return _CHINESE_NUMBER_MAP.get(token)


def resolve_relative_time_range(relative: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if relative == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if relative == "this_month":
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if relative == "last_week":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return start, end
    if relative == "last_month":
        first_day_current = today.replace(day=1)
        end = first_day_current - timedelta(days=1)
        start = end.replace(day=1)
        return start, end
    if relative.startswith("last_") and relative.endswith("_weeks"):
        weeks = int(relative.split("_")[1])
        return today - timedelta(days=weeks * 7), today
    if relative.startswith("last_") and relative.endswith("_days"):
        days = int(relative.split("_")[1])
        return today - timedelta(days=days), today
    return today - timedelta(days=14), today


def resolve_time_range(spec: dict | None, today: date | None = None) -> tuple[date | None, date | None]:
    if not spec:
        return None, None
    today = today or date.today()
    if spec.get("relative"):
        return resolve_relative_time_range(spec["relative"], today)
    start = parse_date_string(spec.get("start_date"))
    end = parse_date_string(spec.get("end_date"))
    return start, end
