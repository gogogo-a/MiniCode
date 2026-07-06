import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BENCHMARKS = ROOT / "tests" / "permission" / "benchmark"
for path in (ROOT, BENCHMARKS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import permission_benchmark


class PermissionBenchmarkTests(unittest.TestCase):
    def test_permission_benchmark_reports_success_rate_and_counts(self):
        result = permission_benchmark.run_permission_benchmark()

        self.assertGreater(result["total"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["successRate"], 1.0)
        self.assertIn("allow", result["permissionCounts"])
        self.assertIn("ask", result["permissionCounts"])
        self.assertIn("deny", result["permissionCounts"])


if __name__ == "__main__":
    unittest.main()
