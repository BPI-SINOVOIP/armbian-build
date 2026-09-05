#!/usr/bin/env python3
from __future__ import annotations

import csv
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run-bananapi-incremental-queue.sh"
MATRIX = ROOT / "config/bananapi-latest-release-matrix.tsv"
GROUPS = ROOT / "config/bananapi-latest-build-groups.tsv"


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

    def test_queue_runner_separates_build_and_compression(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("run_audit", script)
        self.assertIn("board_queue_count", script)
        self.assertIn('before="$(action_count "建置缺少項目")"', script)
        self.assertIn('after="$(action_count "建置缺少項目")"', script)
        self.assertIn("after >= before", script)
        self.assertIn("bananapi-latest-build-groups.tsv", script)
        self.assertIn("architecture", script)
        self.assertIn("family", script)
        self.assertIn("compress-bananapi-image-queue.sh", script)
        self.assertIn('DEFER_XZ="${1}"', script)
        self.assertIn('MAX_RAW_QUEUE', script)
        self.assertIn('compression-producer-done', script)
        self.assertIn("--board", script)
        self.assertIn("--release", script)
        self.assertIn("--profile", script)
        self.assertNotIn("--allow-full-rebuild", script)

    def test_worklist_order_is_architecture_then_os_then_profile(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "sort -t $'\\t' -k1,1n -k2,2n -k3,3n -k4,4 -k5,5n",
            script,
        )
        self.assertLess(script.index("architecture_order ="), script.index("release_order ="))
        self.assertLess(script.index("release_order ="), script.index("profile_order ="))

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

    def test_queue_runner_supports_explicitly_deferred_boards(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("DEFER_FOLDERS", script)
        self.assertIn("folder_is_deferred", script)
        self.assertIn("受控延後", script)
        self.assertIn("nondeferred", script)

    def test_architecture_groups_cover_matrix_exactly(self) -> None:
        with MATRIX.open(encoding="utf-8", newline="") as stream:
            matrix = list(csv.DictReader(stream, delimiter="\t"))
        with GROUPS.open(encoding="utf-8", newline="") as stream:
            groups = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(groups), 45)
        self.assertEqual(
            {(row["folder"], row["board"], row["branch"]) for row in groups},
            {(row["folder"], row["board"], row["branch"]) for row in matrix},
        )
        counts = {
            architecture: sum(row["architecture"] == architecture for row in groups)
            for architecture in ("arm32", "arm64", "riscv64")
        }
        self.assertEqual(counts, {"arm32": 16, "arm64": 26, "riscv64": 3})
        self.assertTrue(
            all(
                row["armbian_arch"]
                == ("armhf" if row["architecture"] == "arm32" else row["architecture"])
                for row in groups
            )
        )


if __name__ == "__main__":
    unittest.main()
