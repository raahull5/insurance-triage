import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from test_ai_evaluation import (
    TEST_CASES,
    create_engine,
    evaluate_case,
)

OUTPUT_DIR = Path("data/repeatability")


def main():
    parser = argparse.ArgumentParser(
        description="Repeatability test for insurance AI triage"
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="Number of complete evaluation runs (default: 3)"
    )
    parser.add_argument(
        "--cases", nargs="+",
        help="Optional case IDs, e.g. EVAL-012 EVAL-014 EVAL-021"
    )
    args = parser.parse_args()

    if args.runs < 2:
        parser.error("--runs must be at least 2")

    selected = TEST_CASES
    if args.cases:
        requested = set(args.cases)
        known = {case["id"] for case in TEST_CASES}
        unknown = requested - known
        if unknown:
            parser.error("Unknown case IDs: " + ", ".join(sorted(unknown)))
        selected = [case for case in TEST_CASES if case["id"] in requested]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = OUTPUT_DIR / f"repeatability_{timestamp}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("INSURANCE AI - REPEATABILITY TEST")
    print("=" * 70)
    print(f"Runs: {args.runs}")
    print(f"Cases per run: {len(selected)}")
    print(f"Maximum evaluations: {args.runs * len(selected)}")
    print(f"Report: {report_path}")

    engine = create_engine()
    all_runs = []
    stopped = False

    for run_number in range(1, args.runs + 1):
        print(f"\n{'=' * 25} RUN {run_number} {'=' * 25}")
        run_results = []

        for index, case in enumerate(selected, start=1):
            print(f"\n[{index}/{len(selected)}] {case['id']} - {case['name']}")

            result = evaluate_case(engine, case)
            run_results.append(result)

            if result.get("error"):
                print("ERROR:", result["error"])
            else:
                checks = result.get("checks", {})
                passed = sum(value is True for value in checks.values())
                print(f"Checks: {passed}/{len(checks)}")

                failed = [name for name, value in checks.items()
                          if value is not True]
                if failed:
                    print("Failed checks:", ", ".join(failed))

            all_runs.append({
                "run": run_number,
                "result": result,
            })

            # Persist after every case, including interrupted runs.
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "runs_requested": args.runs,
                "cases_selected": [case["id"] for case in selected],
                "completed_evaluations": len(all_runs),
                "stopped_early": stopped,
                "results": all_runs,
            }
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            error = str(result.get("error") or "").lower()
            if any(term in error for term in (
                "429", "rate limit", "rate_limit", "tokens per day"
            )):
                print("\nRate limit detected. Stopping further requests.")
                stopped = True
                break

        if stopped:
            break

    # Aggregate results by case and metric.
    by_case = {}
    for entry in all_runs:
        result = entry["result"]
        case_id = result["id"]
        by_case.setdefault(case_id, []).append(entry)

    print("\n" + "=" * 70)
    print("REPEATABILITY SUMMARY")
    print("=" * 70)

    metric_counts = Counter()
    metric_passes = Counter()
    unstable_cases = []

    for case in selected:
        entries = by_case.get(case["id"], [])
        signatures = []
        case_failures = []

        for entry in entries:
            result = entry["result"]
            if result.get("error"):
                case_failures.append(
                    f"Run {entry['run']}: ERROR"
                )
                continue

            actual = result.get("actual", {})
            signatures.append(tuple(
                actual.get(field)
                for field in (
                    "intent", "category", "priority",
                    "urgency_score", "escalation_needed"
                )
            ))

            for metric, passed in result.get("checks", {}).items():
                metric_counts[metric] += 1
                metric_passes[metric] += passed is True
                if passed is not True:
                    case_failures.append(
                        f"Run {entry['run']}: {metric} failed"
                    )

        distinct = len(set(signatures))
        if distinct > 1 or case_failures or len(entries) < args.runs:
            unstable_cases.append(case["id"])

        print(
            f"{case['id']}: {len(entries)}/{args.runs} runs completed; "
            f"{distinct} distinct output signatures"
        )
        for failure in case_failures:
            print("  -", failure)

    print("\nMETRIC PASS RATES")
    for metric in sorted(metric_counts):
        passed = metric_passes[metric]
        total = metric_counts[metric]
        print(f"{metric:24} {passed}/{total} ({100 * passed / total:.2f}%)")

    print("\nCases requiring review:", 
          ", ".join(unstable_cases) if unstable_cases else "None detected")
    print("Report saved to:", report_path.resolve())


if __name__ == "__main__":
    main()
