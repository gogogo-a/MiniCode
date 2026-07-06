from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOOK_TESTS = ROOT / "tests" / "hook"
LAYERS = ("unit", "integration", "scenarios", "regression")


def run_layer(layer: str, show_flow: bool = False) -> int:
    path = HOOK_TESTS / layer
    print(f"Hook {layer}")
    env = dict(os.environ)
    if layer == "live" and show_flow:
        env["SHOW_LIVE_FLOW"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(path)],
        cwd=ROOT,
        env=env,
        text=True,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--show-flow", action="store_true")
    args = parser.parse_args()
    failed = []
    layers = LAYERS + (("live",) if args.live else ())
    for layer in layers:
        code = run_layer(layer, show_flow=args.show_flow)
        if code != 0:
            failed.append(layer)
    if failed:
        print("Failed layers: " + ", ".join(failed))
        return 1
    print("Hook Test Suite PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
