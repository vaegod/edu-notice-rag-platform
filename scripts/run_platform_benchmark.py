from __future__ import annotations

import sys
from argparse import ArgumentParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.evaluation.platform_benchmark import run_platform_benchmark, write_benchmark_outputs


def main() -> None:
    parser = ArgumentParser(description="Run the API-free platform benchmark.")
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "eval" / "results",
    )
    args = parser.parse_args()
    report = run_platform_benchmark(
        ROOT / "data" / "eval" / "platform_benchmark.json",
        repeats=args.repeats,
    )
    write_benchmark_outputs(report, args.output_dir)
    print(f"Cases: {report['benchmark']['case_count']}")
    print(json_summary(report))
    print(f"Results: {args.output_dir}")


def json_summary(report: dict) -> str:
    intent = report["metrics"]["intent_scope"]
    evidence = report["metrics"]["evidence_qa"]
    return (
        f"Intent={intent['intent_accuracy']:.2%}, "
        f"Validation={intent['validation_accuracy']:.2%}, "
        f"R@3={evidence['recall_at_3']:.2%}, "
        f"Refusal={evidence['refusal_accuracy']:.2%}, "
        f"Citation={evidence['citation_grounded_rate']:.2%}"
    )


if __name__ == "__main__":
    main()
