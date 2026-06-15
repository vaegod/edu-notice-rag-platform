from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.source import Source
from app.services.admissions_taxonomy import ADMISSIONS_KEYWORDS
from app.services.crawler.dispatcher import CrawlRunnerDispatcher
from app.models.source_validation_run import SourceValidationRun
from app.services.normalize.cleaner import clean_text
from app.schemas.source import SourceValidationPreviewItem, SourceValidationReport

NOTICE_TITLE_PATTERN = re.compile(
    r"(关于|通知|公告|公示|讲座|论坛|报告|申报|申请|报名|遴选|选拔|活动|比赛|竞赛|征集|名单|结果|安排|实施|截止)"
)
NEGATIVE_TITLE_HINTS = (
    "机构设置",
    "岗位分工",
    "联系我们",
    "学校简介",
    "学院简介",
    "科研项目管理",
    "科研成果认定管理",
    "二级单位科研秘书",
    "办事流程",
    "下载中心",
    "研究方向",
    "师资队伍",
)
SOURCE_SUFFIXES = ("通知公告", "通知信息", "学生事务通知", "通知", "公告")
ADMISSIONS_TITLE_PATTERN = re.compile(
    r"(招生|报考|报名|硕士|博士|本科|推免|调剂|夏令营|复试|拟录取|录取|招生简章|申请考核|高水平运动队|高水平艺术团|保送生|强基计划|综合评价|专项计划|少年班)"
)
ADMISSIONS_NEGATIVE_TITLE_HINTS = (
    "工作通知",
    "思想动态",
    "学生工作",
    "奖贷助补",
    "就业信息",
    "就业指导",
    "宿舍安全",
    "班委工作",
)


class RuleValidationService:
    def __init__(self) -> None:
        self.runner_dispatcher = CrawlRunnerDispatcher()

    def validate_source(self, source: Source, sample_count: int = 3) -> SourceValidationReport:
        issues: list[str] = []
        samples: list[SourceValidationPreviewItem] = []
        success_count = 0

        try:
            fetch_result = self.runner_dispatcher.fetch_notices(
                source,
                page_limit=max(1, len(source.start_urls_json or [])),
            )
        except Exception as exc:
            return SourceValidationReport(
                sample_count=0,
                discovered_count=0,
                success_count=0,
                success_rate=0.0,
                issues=[f"列表页抓取失败：{exc}"],
                samples=[],
            )

        discovered_count = fetch_result.discovered
        issues.extend(fetch_result.errors)
        if discovered_count == 0:
            issues.append("列表页没有解析出任何通知项。")

        notice_like_count = 0
        admissions_like_count = 0
        dated_count = 0
        context_match_count = 0
        records = fetch_result.records[:]
        if self._is_admissions_probe(source):
            records = sorted(records, key=lambda item: self._admissions_record_priority(item), reverse=True)
        for record in records[:sample_count]:
            item = record.list_item
            detail = record.detail_page
            content_length = len((detail.raw_text or "").strip())
            assessment = self._assess_record(
                source=source,
                item_title=item.title,
                item_publish_date=item.publish_date,
                detail_title=detail.title,
                detail_publish_date=detail.publish_date,
                detail_text=detail.raw_text,
                content_length=content_length,
            )
            notice_like_count += 1 if assessment["notice_like"] else 0
            admissions_like_count += 1 if assessment["admissions_like"] else 0
            dated_count += 1 if assessment["has_date"] else 0
            context_match_count += 1 if assessment["context_match"] else 0
            if assessment["is_success"]:
                success_count += 1
            else:
                issues.extend(f"{item.title}：{message}" for message in assessment["issues"])
            samples.append(
                SourceValidationPreviewItem(
                    title=item.title,
                    detail_url=item.detail_url,
                    publish_date=item.publish_date,
                    detail_title=detail.title,
                    content_length=content_length,
                    attachment_count=len(detail.attachments),
                )
            )

        effective_sample_count = min(sample_count, discovered_count)
        if effective_sample_count > 0:
            if not self._is_admissions_probe(source) and notice_like_count / effective_sample_count < 0.5:
                issues.append("当前列表样本缺少明显的通知公告标题特征，疑似接错栏目页。")
            if dated_count / effective_sample_count < 0.4:
                issues.append("当前列表样本日期命中率偏低，疑似不是标准招生列表页。")
            if (not self._is_admissions_probe(source)) and context_match_count / effective_sample_count < 0.3:
                issues.append("当前样本与目标机构/院系上下文匹配度偏低，建议人工复核。")
            if self._is_admissions_probe(source) and admissions_like_count / effective_sample_count < 0.5:
                issues.append("当前列表样本缺少明显招生特征，疑似命中了普通通知栏目而不是招生页。")
        success_rate = (
            round(success_count / effective_sample_count, 4)
            if effective_sample_count > 0
            else 0.0
        )
        return SourceValidationReport(
            sample_count=effective_sample_count,
            discovered_count=discovered_count,
            success_count=success_count,
            success_rate=success_rate,
            issues=issues,
            samples=samples,
        )

    def validate_and_record(
        self,
        session: Session,
        source: Source,
        *,
        validation_type: str = "manual_preview",
        sample_count: int = 3,
    ) -> SourceValidationReport:
        from app.core.time import utc_now

        report = self.validate_source(source, sample_count=sample_count)
        session.add(
            SourceValidationRun(
                source_id=source.id,
                validation_type=validation_type,
                sample_count=report.sample_count,
                success_count=report.success_count,
                success_rate=report.success_rate,
                error_summary="; ".join(report.issues) if report.issues else None,
                report_json=report.model_dump(mode="json"),
            )
        )
        source.confidence_score = report.success_rate
        source.last_validated_at = utc_now()
        source.onboarding_status = "validated" if report.success_rate >= 0.8 else "needs_review"
        session.commit()
        return report

    def _assess_record(
        self,
        *,
        source: Source,
        item_title: str,
        item_publish_date: str | None,
        detail_title: str | None,
        detail_publish_date: str | None,
        detail_text: str,
        content_length: int,
    ) -> dict[str, object]:
        title = (detail_title or item_title or "").strip()
        issues: list[str] = []
        notice_like = self._looks_like_notice_title(item_title) or self._looks_like_notice_title(title)
        admissions_like = self._looks_like_admissions_title(item_title) or self._looks_like_admissions_title(title)
        has_date = bool((item_publish_date or "").strip() or (detail_publish_date or "").strip())
        context_match = self._matches_source_context(source, title=title, detail_text=detail_text)
        if not title:
            issues.append("详情页标题为空")
        if content_length < 20:
            issues.append("详情页正文过短")
        if any(flag in title for flag in ("系统提示", "错误", "404", "403", "登录")):
            issues.append("详情页看起来是错误页或登录页")
        if any(flag in title for flag in NEGATIVE_TITLE_HINTS):
            issues.append("标题更像栏目介绍页，不像招生页")
        if not self._is_admissions_probe(source) and not notice_like and not has_date:
            issues.append("缺少通知标题特征和日期信息")
        if not context_match and not (notice_like or admissions_like):
            issues.append("页面上下文没有明显命中目标院系/机构")
        if self._is_admissions_probe(source):
            if not admissions_like:
                issues.append("页面缺少明显招生特征")
            if any(flag in title for flag in ADMISSIONS_NEGATIVE_TITLE_HINTS):
                issues.append("页面更像普通工作通知，不像招生页")
        return {
            "is_success": len(issues) == 0,
            "issues": issues,
            "notice_like": notice_like,
            "admissions_like": admissions_like,
            "has_date": has_date,
            "context_match": context_match,
        }

    def _looks_like_notice_title(self, title: str | None) -> bool:
        return bool(title and NOTICE_TITLE_PATTERN.search(title))

    def _looks_like_admissions_title(self, title: str | None) -> bool:
        if not title:
            return False
        if ADMISSIONS_TITLE_PATTERN.search(title):
            return True
        lowered = clean_text(title).lower()
        return any(keyword.lower() in lowered for keyword in ADMISSIONS_KEYWORDS)

    def _is_admissions_probe(self, source: Source) -> bool:
        config = source.config_json or {}
        if config.get("probe_target") in {"admissions", "admissions_notice"}:
            return True
        if getattr(source, "collection_domain", None) == "admissions_notice":
            return True
        tracks = config.get("admissions_tracks") or []
        return bool(tracks)

    def _admissions_record_priority(self, record) -> tuple[float, int]:
        title = clean_text(record.list_item.title or "")
        score = 0.0
        if self._looks_like_admissions_title(title):
            score += 2.0
        if any(flag in title for flag in ADMISSIONS_NEGATIVE_TITLE_HINTS):
            score -= 1.5
        if any(token in title for token in ("招生简章", "拟录取", "录取", "复试", "报考", "报名", "保送生", "高水平运动队", "高水平艺术团")):
            score += 1.2
        if any(token in title for token in ("工作管理办法", "管理办法", "制度", "规定", "章程")):
            score -= 0.8
        return score, len(title)

    def _matches_source_context(self, source: Source, *, title: str, detail_text: str) -> bool:
        haystack = clean_text(f"{title}\n{detail_text[:1200]}")
        if not haystack:
            return False
        for alias in self._source_context_aliases(source):
            normalized_alias = clean_text(alias)
            if normalized_alias and normalized_alias in haystack:
                return True
        return False

    def _source_context_aliases(self, source: Source) -> list[str]:
        config = source.config_json or {}
        aliases: list[str] = []
        for value in (
            config.get("department"),
            source.name,
            config.get("institution"),
            source.organization_name,
        ):
            if isinstance(value, str) and value.strip():
                aliases.append(value.strip())
        for suffix in SOURCE_SUFFIXES:
            if source.name.endswith(suffix) and len(source.name) > len(suffix):
                aliases.append(source.name[: -len(suffix)].strip())
        institution = str(config.get("institution") or source.organization_name or "").strip()
        if institution and source.name.startswith(institution):
            remainder = source.name[len(institution) :].strip()
            if remainder:
                aliases.append(remainder)
                for suffix in SOURCE_SUFFIXES:
                    if remainder.endswith(suffix) and len(remainder) > len(suffix):
                        aliases.append(remainder[: -len(suffix)].strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            normalized = clean_text(alias)
            if len(normalized) < 4 or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(alias)
        return deduped
