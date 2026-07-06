from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PERMISSION_TESTS = ROOT / "tests" / "permission"
LAYERS = ("unit", "integration", "scenarios", "regression", "replay", "benchmark")


def run_layer(layer: str) -> int:
    path = PERMISSION_TESTS / layer
    print(f"Permission {layer}")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(path)],
        cwd=ROOT.parent,
        text=True,
    )
    return result.returncode


def main() -> int:
    failed = []
    for layer in LAYERS:
        code = run_layer(layer)
        if code != 0:
            failed.append(layer)
    if failed:
        print("Failed layers: " + ", ".join(failed))
        return 1
    print("Permission Test Suite PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
