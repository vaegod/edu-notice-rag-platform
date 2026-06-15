from __future__ import annotations

from dataclasses import dataclass
import re


C9_UNIVERSITIES = [
    "北京大学",
    "清华大学",
    "复旦大学",
    "上海交通大学",
    "浙江大学",
    "南京大学",
    "中国科学技术大学",
    "哈尔滨工业大学",
    "西安交通大学",
]

C9_ALIASES = {
    "北大": "北京大学",
    "pku": "北京大学",
    "清华": "清华大学",
    "thu": "清华大学",
    "复旦": "复旦大学",
    "fudan": "复旦大学",
    "上海交大": "上海交通大学",
    "上交": "上海交通大学",
    "sjtu": "上海交通大学",
    "浙大": "浙江大学",
    "zju": "浙江大学",
    "南大": "南京大学",
    "nju": "南京大学",
    "中科大": "中国科学技术大学",
    "中国科大": "中国科学技术大学",
    "ustc": "中国科学技术大学",
    "哈工大": "哈尔滨工业大学",
    "hit": "哈尔滨工业大学",
    "西交": "西安交通大学",
    "西安交大": "西安交通大学",
    "xjtu": "西安交通大学",
}

C9_GRADUATE_ADMISSIONS_HOMEPAGES = {
    "北京大学": "https://admission.pku.edu.cn/index.htm",
    "清华大学": "https://yz.tsinghua.edu.cn/",
    "复旦大学": "https://gsao.fudan.edu.cn/",
    "上海交通大学": "https://yzb.sjtu.edu.cn/",
    "浙江大学": "https://www.grs.zju.edu.cn/yjszs/",
    "南京大学": "https://yzb.nju.edu.cn/",
    "中国科学技术大学": "https://yz.ustc.edu.cn/",
    "哈尔滨工业大学": "https://yzb.hit.edu.cn/main.htm",
    "西安交通大学": "https://yz.xjtu.edu.cn/",
}

UNSUPPORTED_ADMISSIONS_TOKENS = (
    "本科",
    "强基计划",
    "高水平运动队",
    "高水平艺术团",
    "留学生",
    "国际生",
    "国际学生",
    "继续教育",
    "成人教育",
    "成教",
    "mba",
    "emba",
    "MBA",
    "EMBA",
    "第二学士",
    "第二学士学位",
    "第二学位",
)

GRADUATE_TOKENS = ("研究生", "硕士", "博士", "推免", "保研", "预推免", "复试", "调剂", "研招")


@dataclass(slots=True)
class C9ScopeResult:
    is_valid: bool
    university_name: str | None
    message: str | None = None


def normalize_c9_university_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _normalize_text(value)
    for university in C9_UNIVERSITIES:
        if _normalize_text(university) == cleaned or _normalize_text(university) in cleaned:
            return university
    for alias, university in C9_ALIASES.items():
        if _normalize_text(alias) == cleaned or _normalize_text(alias) in cleaned:
            return university
    return None


def infer_c9_university_from_text(value: str | None) -> str | None:
    text = value or ""
    lowered = text.lower()
    for university in C9_UNIVERSITIES:
        if university in text:
            return university
    for alias, university in C9_ALIASES.items():
        if alias in text or alias.lower() in lowered:
            return university
    return None


def is_c9_university(value: str | None) -> bool:
    return normalize_c9_university_name(value) is not None


def c9_graduate_admissions_homepage(value: str | None) -> str | None:
    university_name = normalize_c9_university_name(value)
    if not university_name:
        return None
    return C9_GRADUATE_ADMISSIONS_HOMEPAGES.get(university_name)


def mentions_c9_group(value: str | None) -> bool:
    return bool(value and re.search(r"\bC9\b|c9|九校联盟|C9高校|c9高校", value))


def is_graduate_admissions_text(value: str | None) -> bool:
    text = value or ""
    lowered = text.lower()
    if any(token.lower() in lowered for token in ("graduate", "postgraduate", "master", "doctor", "phd", "yz")):
        return True
    return any(token in text for token in GRADUATE_TOKENS)


def has_unsupported_admissions_text(value: str | None) -> bool:
    text = value or ""
    lowered = text.lower()
    return any(token.lower() in lowered for token in UNSUPPORTED_ADMISSIONS_TOKENS)


def c9_scope_rejection_message(
    *,
    university_name: str | None,
    query: str | None = None,
    collection_domain: str | None = None,
    admissions_levels: list[str] | None = None,
    admissions_tracks: list[str] | None = None,
    homepage_url: str | None = None,
) -> str | None:
    if collection_domain and collection_domain != "admissions_notice":
        return "当前收缩版只支持 C9 高校校级研究生招生公告，不支持新闻中心或学校概况采集。"

    normalized_university = normalize_c9_university_name(university_name)
    has_external_entrypoint = bool((homepage_url or "").strip())
    if university_name and not normalized_university and not mentions_c9_group(query) and not has_external_entrypoint:
        return "默认自动找源只覆盖 C9 高校；非 C9 高校请提供官方首页或研究生招生入口 URL 后再进行受控站内发现。"

    tracks = set(admissions_tracks or [])
    unsupported_tracks = tracks.difference({"graduate"})
    levels = set(admissions_levels or [])
    unsupported_levels = levels.difference({"graduate"})
    if unsupported_tracks or unsupported_levels or has_unsupported_admissions_text(query):
        return "当前收缩版只支持校级研究生招生公告，不支持本科、留学生、继续教育、MBA 或第二学士学位招生。"

    return None


def normalize_to_c9_graduate_scope(payload: dict, *, query: str | None = None) -> dict:
    normalized = dict(payload)
    university_name = normalize_c9_university_name(
        normalized.get("university_name") or normalized.get("institution")
    ) or infer_c9_university_from_text(query)
    if university_name:
        normalized["university_name"] = university_name
        normalized["institution"] = university_name
    normalized["collection_domain"] = "admissions_notice"
    normalized["admissions_levels"] = ["graduate"]
    normalized["admissions_tracks"] = ["graduate"]
    return normalized


def _normalize_text(value: str | None) -> str:
    return re.sub(r"[\s,，。；：:（）()【】\-_]+", "", (value or "")).lower()
