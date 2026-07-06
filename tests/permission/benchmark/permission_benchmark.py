from __future__ import annotations

import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scenario_runner


def run_permission_benchmark() -> dict:
    started = time.perf_counter()
    suites = {
        "scenarios": ROOT / "tests" / "permission" / "scenarios",
        "regression": ROOT / "tests" / "permission" / "regression",
        "replay": ROOT / "tests" / "permission" / "replay",
    }
    suite_results = {}
    totals = {"total": 0, "passed": 0, "failed": 0}
    permission_counts = {"allow": 0, "ask": 0, "deny": 0, "passthrough": 0}
    for name, path in suites.items():
        _, summary = scenario_runner.run_suite(path)
        suite_results[name] = summary
        totals["total"] += summary["total"]
        totals["passed"] += summary["passed"]
        totals["failed"] += summary["failed"]
        for behavior, count in summary["permission_counts"].items():
            permission_counts[behavior] = permission_counts.get(behavior, 0) + count
    elapsed = time.perf_counter() - started
    success_rate = totals["passed"] / totals["total"] if totals["total"] else 0.0
    return {
        "total": totals["total"],
        "passed": totals["passed"],
        "failed": totals["failed"],
        "successRate": round(success_rate, 4),
        "runtimeSeconds": round(elapsed, 4),
        "permissionCounts": permission_counts,
        "suites": suite_results,
    }


def format_benchmark(result: dict) -> str:
    counts = result["permissionCounts"]
    return "\n".join([
        "Permission Benchmark",
        f"PASS {result['passed']}/{result['total']}",
        f"Success Rate {result['successRate']}",
        f"Runtime Seconds {result['runtimeSeconds']}",
        f"ALLOW {counts.get('allow', 0)}",
        f"ASK {counts.get('ask', 0)}",
        f"DENY {counts.get('deny', 0)}",
        f"PASSTHROUGH {counts.get('passthrough', 0)}",
    ])


def main() -> int:
    result = run_permission_benchmark()
    print(format_benchmark(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
