from __future__ import annotations

import argparse
from pathlib import Path

from tests.shared.runner import format_summary as shared_format_summary
from tests.shared.runner import run_scenario, run_suite


ROOT = Path(__file__).resolve().parent


def format_summary(summary: dict) -> str:
    return shared_format_summary("Hook", summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run hook prompt scenario tests.")
    parser.add_argument(
        "root",
        nargs="?",
        default=str(ROOT / "tests" / "hook" / "scenarios"),
        help="Scenario directory.",
    )
    args = parser.parse_args()
    results, summary = run_suite(Path(args.root))
    print(format_summary(summary))
    for result in [item for item in results if not item.passed]:
        print(f"{result.name}: {result.reason}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
