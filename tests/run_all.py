from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_command(label: str, command: list[str], env: dict[str, str] | None = None) -> int:
    print(label)
    result = subprocess.run(command, cwd=ROOT, env=env or os.environ.copy(), text=True)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run s20 test suites.")
    parser.add_argument("--live", action="store_true", help="Also run real-model live smoke tests.")
    parser.add_argument("--show-flow", action="store_true", help="Show live prompt/tool/event/final flow.")
    args = parser.parse_args()

    checks: list[tuple[str, list[str], dict[str, str] | None]] = [
        (
            "Shared tests",
            [sys.executable, "-m", "unittest", "tests.shared.test_domain_layout", "tests.shared.test_runner", "tests.shared.test_live_runner", "-v"],
            None,
        ),
        ("Hook tests", [sys.executable, "tests/hook/run_tests.py"], None),
        ("Permission tests", [sys.executable, "tests/permission/run_tests.py"], None),
    ]

    if args.live:
        env = os.environ.copy()
        if args.show_flow:
            env["SHOW_LIVE_FLOW"] = "1"
        hook_live_command = [sys.executable, "tests/hook/run_tests.py", "--live"]
        permission_live_command = [sys.executable, "tests/permission/run_tests.py", "--live"]
        if args.show_flow:
            hook_live_command.append("--show-flow")
            permission_live_command.append("--show-flow")
        checks.extend([
            ("Hook live tests", hook_live_command, env),
            ("Permission live tests", permission_live_command, env),
        ])

    failed = []
    for label, command, env in checks:
        if run_command(label, command, env) != 0:
            failed.append(label)
    if failed:
        print("Failed suites: " + ", ".join(failed))
        return 1
    print("All Test Suites PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
