from __future__ import annotations

import re


TRACK_UNDERGRADUATE = "undergraduate"
TRACK_GRADUATE = "graduate"
TRACK_INTERNATIONAL = "international"
TRACK_CONTINUING_EDUCATION = "continuing_education"
TRACK_MBA = "mba"
TRACK_SECOND_BACHELOR = "second_bachelor"

ADMISSIONS_TRACKS = [
    TRACK_UNDERGRADUATE,
    TRACK_GRADUATE,
    TRACK_INTERNATIONAL,
    TRACK_CONTINUING_EDUCATION,
    TRACK_MBA,
    TRACK_SECOND_BACHELOR,
]

TRACK_LABELS = {
    TRACK_UNDERGRADUATE: "本科招生",
    TRACK_GRADUATE: "研究生招生",
    TRACK_INTERNATIONAL: "留学生招生",
    TRACK_CONTINUING_EDUCATION: "继续教育招生",
    TRACK_MBA: "MBA招生",
    TRACK_SECOND_BACHELOR: "第二学士学位招生",
}

TRACK_ALIASES = {
    TRACK_UNDERGRADUATE: TRACK_UNDERGRADUATE,
    "本科": TRACK_UNDERGRADUATE,
    "本科招生": TRACK_UNDERGRADUATE,
    "强基计划": TRACK_UNDERGRADUATE,
    "高水平运动队": TRACK_UNDERGRADUATE,
    "高水平艺术团": TRACK_UNDERGRADUATE,
    TRACK_GRADUATE: TRACK_GRADUATE,
    "研究生": TRACK_GRADUATE,
    "研究生招生": TRACK_GRADUATE,
    "硕士": TRACK_GRADUATE,
    "博士": TRACK_GRADUATE,
    "推免": TRACK_GRADUATE,
    "保研": TRACK_GRADUATE,
    "预推免": TRACK_GRADUATE,
    TRACK_INTERNATIONAL: TRACK_INTERNATIONAL,
    "国际生": TRACK_INTERNATIONAL,
    "留学生": TRACK_INTERNATIONAL,
    "留学生招生": TRACK_INTERNATIONAL,
    "国际班": TRACK_INTERNATIONAL,
    TRACK_CONTINUING_EDUCATION: TRACK_CONTINUING_EDUCATION,
    "继续教育": TRACK_CONTINUING_EDUCATION,
    "继续教育招生": TRACK_CONTINUING_EDUCATION,
    "成人教育": TRACK_CONTINUING_EDUCATION,
    "成教": TRACK_CONTINUING_EDUCATION,
    TRACK_MBA: TRACK_MBA,
    "emba": TRACK_MBA,
    "mba": TRACK_MBA,
    "MBA": TRACK_MBA,
    "EMBA": TRACK_MBA,
    TRACK_SECOND_BACHELOR: TRACK_SECOND_BACHELOR,
    "第二学士": TRACK_SECOND_BACHELOR,
    "第二学士学位": TRACK_SECOND_BACHELOR,
    "第二学位": TRACK_SECOND_BACHELOR,
}

TRACK_BROAD_LEVELS = {
    TRACK_UNDERGRADUATE: ["undergraduate"],
    TRACK_GRADUATE: ["graduate"],
    TRACK_INTERNATIONAL: [],
    TRACK_CONTINUING_EDUCATION: [],
    TRACK_MBA: ["graduate"],
    TRACK_SECOND_BACHELOR: ["undergraduate"],
}

TRACK_HINTS = {
    TRACK_UNDERGRADUATE: (
        "本科",
        "本科招生",
        "强基计划",
        "高水平运动队",
        "高水平艺术团",
        "综合评价",
        "专项计划",
        "保送生",
        "少年班",
        "undergraduate",
    ),
    TRACK_GRADUATE: (
        "研究生",
        "研究生招生",
        "硕士",
        "博士",
        "推免",
        "预推免",
        "统考",
        "graduate",
        "master",
        "doctor",
        "yz",
        "yjs",
    ),
    TRACK_INTERNATIONAL: (
        "留学生",
        "国际生",
        "国际班",
        "国际学生",
        "international",
        "overseas",
        "foreign",
        "is.",
    ),
    TRACK_CONTINUING_EDUCATION: (
        "继续教育",
        "继续教育招生",
        "成人教育",
        "成教",
        "夜大",
        "函授",
        "jxjy",
    ),
    TRACK_MBA: (
        "mba",
        "emba",
        "工商管理硕士",
    ),
    TRACK_SECOND_BACHELOR: (
        "第二学士",
        "第二学士学位",
        "第二学位",
    ),
}


def normalize_tracks(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        alias = TRACK_ALIASES.get(value.strip())
        if alias is None:
            alias = TRACK_ALIASES.get(value.strip().lower())
        if alias and alias not in normalized:
            normalized.append(alias)
    return normalized


def broad_levels_from_tracks(tracks: list[str] | None) -> list[str]:
    levels: list[str] = []
    for track in normalize_tracks(tracks):
        for level in TRACK_BROAD_LEVELS.get(track, []):
            if level not in levels:
                levels.append(level)
    return levels


def infer_tracks_from_text(value: str | None) -> list[str]:
    text = value or ""
    lowered = text.lower()
    found: list[str] = []
    for track, hints in TRACK_HINTS.items():
        for hint in hints:
            hint_lower = hint.lower()
            if hint_lower in {"undergraduate", "graduate", "master", "doctor", "international", "overseas", "foreign"}:
                if re.search(rf"(?:^|[/._-]){re.escape(hint_lower)}(?:[/._?-]|$)", lowered):
                    if track not in found:
                        found.append(track)
                    break
                continue
            if hint in text or hint_lower in lowered:
                if track not in found:
                    found.append(track)
                break
    return found


def primary_track(tracks: list[str] | None) -> str | None:
    normalized = normalize_tracks(tracks)
    return normalized[0] if normalized else None
