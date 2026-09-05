#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run-bananapi-incremental-queue.sh"


class BananaPiIncrementalQueueTests(unittest.TestCase):
    def test_queue_count_expression_runs_with_system_awk(self) -> None:
        result = subprocess.run(
            ["awk", "END { print (NR > 0 ? NR - 1 : 0) }"],
            input="欄位\n項目一\n項目二\n",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2")

    def test_queue_runner_is_sequential_and_guarded(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("run_audit", script)
        self.assertIn("board_queue_count", script)
        self.assertIn('before="$(queue_count)"', script)
        self.assertIn('after="$(queue_count)"', script)
        self.assertIn("after >= before", script)
        self.assertIn('decision[$1] == "候選只補整板驗證"', script)
        self.assertIn('decision[$1] == "保留部分候選並補缺"', script)
        self.assertIn("--board", script)
        self.assertNotIn("--allow-full-rebuild", script)
        self.assertNotIn("&\n", script)

    def test_queue_runner_pins_build_identity(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        for variable in (
            "SOURCE_COMMIT",
            "BUILD_CONTEXT_SHA256",
            "EXPECTED_BUILD_CONTEXT_SHA256",
            "ARMBIAN_CONTAINER_IMAGE",
            "BUILD_REPO",
        ):
            self.assertIn(variable, script)


if __name__ == "__main__":
    unittest.main()
