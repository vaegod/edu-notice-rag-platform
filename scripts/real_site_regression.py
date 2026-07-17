from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import get_session_factory, init_db
from app.schemas.source import SourceProbeRequest
from app.services.source_resolution import SourceResolutionService


CASES_PATH = Path(__file__).resolve().with_name("real_site_regression_cases.json")


def run_regression(*, min_success_rate: float = 0.8) -> tuple[list[dict], bool]:
    init_db()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    session = get_session_factory()()
    service = SourceResolutionService()
    results: list[dict] = []
    passed = True
    try:
        for case in cases:
            case_result: dict = {
                "name": case["name"],
                "url": case["url"],
            }
            try:
                probe = service.probe(
                    session,
                    SourceProbeRequest(
                        name=case["name"],
                        university_name=case.get("organization_name"),
                        organization_name=case.get("organization_name"),
                        department_name=case.get("department_name"),
                        url=case["url"],
                        collection_domain="admissions_notice",
                        admissions_levels=["graduate"],
                        admissions_tracks=["graduate"],
                        force_refresh_source=True,
                        site_type=case.get("site_type", "college"),
                        crawl_mode=case.get("crawl_mode", "static"),
                        request_data=case.get("request_data"),
                        sample_count=3,
                    ),
                )
                case_result.update(
                    {
                        "selected_url": probe.selected_url,
                        "resolved_homepage_url": probe.resolved_homepage_url,
                        "selection_confidence": probe.selection_confidence,
                        "success_rate": probe.report.success_rate,
                        "issues": probe.report.issues,
                        "probe_mode": probe.probe_mode,
                        "bootstrap_strategy": probe.bootstrap_strategy,
                        "candidate_count": len(probe.candidate_rankings),
                        "agent_trace_steps": len(probe.agent_trace),
                    }
                )
                if probe.report.success_rate < min_success_rate:
                    passed = False
            except Exception as exc:
                case_result["error"] = str(exc)
                passed = False
            results.append(case_result)
    finally:
        session.close()

    return results, passed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run regression checks against configured real sites.")
    parser.add_argument("--min-success-rate", type=float, default=0.8)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results, passed = run_regression(min_success_rate=args.min_success_rate)
    payload = {
        "passed": passed,
        "min_success_rate": args.min_success_rate,
        "cases": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
