from pathlib import Path

from app.evaluation.platform_benchmark import run_platform_benchmark


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_platform_benchmark_runs_without_model_api() -> None:
    report = run_platform_benchmark(
        PROJECT_ROOT / "data" / "eval" / "platform_benchmark.json",
        repeats=1,
    )

    assert report["benchmark"]["case_count"] == 30
    assert report["benchmark"]["intent_case_count"] == 20
    assert report["benchmark"]["evidence_case_count"] == 10
    assert report["metrics"]["intent_scope"]["validation_accuracy"] >= 0.9
    assert report["metrics"]["evidence_qa"]["refusal_accuracy"] == 1
    assert report["metrics"]["evidence_qa"]["citation_grounded_rate"] == 1
