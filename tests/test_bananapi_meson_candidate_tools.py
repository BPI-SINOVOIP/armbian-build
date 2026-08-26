#!/usr/bin/env python3
"""Banana Pi Meson 候選映像工具回歸測試。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-meson-current.json"
BUILD_SCRIPT = ROOT / "tools/build-bananapi-meson-candidates.sh"
VERIFY_SCRIPT = ROOT / "tools/verify-bananapi-meson-candidates.sh"
ISOLATED_RUNNER = ROOT / "tools/run-bananapi-candidates-isolated-cache.sh"
MESON_RUNNER = ROOT / "tools/run-bananapi-meson-candidates-isolated-cache.sh"
EXPECTED_BOARDS = {
    "bananapim5",
    "bananapim2pro",
    "bananapicm4io",
    "bananapim2s",
}


class BananaPiMesonCandidateToolTests(unittest.TestCase):
    """驗證板級預期、來源同一性與唯讀守門不會退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())

    def test_validation_config_has_exact_board_set(self) -> None:
        self.assertEqual(set(self.config["boards"]), EXPECTED_BOARDS)
        self.assertEqual(self.config["schema_version"], 1)
        self.assertEqual(
            self.config["fip_commit"],
            "e11ae32f65219e9cba903e9744f216239b41386a",
        )
        for board, policy in self.config["boards"].items():
            with self.subTest(board=board):
                self.assertRegex(
                    policy["fip_manifest_sha256"],
                    re.compile(r"^[0-9a-f]{64}$"),
                )

    def test_emmc_policy_is_board_specific(self) -> None:
        boards = self.config["boards"]
        for board in ("bananapim5", "bananapicm4io"):
            with self.subTest(board=board):
                self.assertEqual(
                    boards[board]["emmc_max_frequency"],
                    100_000_000,
                )
                self.assertTrue(boards[board]["emmc_no_hs400"])
        for board in ("bananapim2pro", "bananapim2s"):
            with self.subTest(board=board):
                self.assertEqual(
                    boards[board]["emmc_max_frequency"],
                    200_000_000,
                )
                self.assertFalse(boards[board]["emmc_no_hs400"])

    def test_required_overlays_are_built(self) -> None:
        makefile = (
            ROOT
            / "patch/kernel/archive/meson64-6.18/overlay/Makefile"
        ).read_text()
        for board, policy in self.config["boards"].items():
            for overlay in policy["required_overlays"]:
                with self.subTest(board=board, overlay=overlay):
                    self.assertIn(overlay, makefile)

    def test_build_tool_records_reproducibility_evidence(self) -> None:
        text = BUILD_SCRIPT.read_text()
        for required in (
            "status --porcelain --untracked-files=all",
            "userpatches",
            "cache 不是 OverlayFS",
            ".tmp/.bananapi-meson-build.lock",
            '-iname "Armbian-*_${board}_${release}_${branch}_*.img"',
            "source_commit",
            "source_tree",
            "validation_config_sha256",
            "build_parameters_sha256",
            "fip-blobs.sha256",
            "fip_manifest_sha256",
            "decompressed_sha256",
            "ARTIFACT_IGNORE_CACHE",
            "FIP 工作樹不是乾淨狀態",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_verifier_uses_read_only_mounts_and_identity_checks(self) -> None:
        text = VERIFY_SCRIPT.read_text()
        for required in (
            "--read-only",
            "mount -o ro,noload",
            "CANDIDATES.tsv",
            "source_commit",
            "source_tree",
            "validation_config_sha256",
            "xz -dc",
            "fdtfile=",
            "/aliases mmc1",
            "emmc_max_frequency",
            "no-mmc-hs400",
            "cmp --silent --bytes=442",
            "cmp --silent --ignore-initial=512:512",
            'linux-u-boot-${board}-current.md5sums',
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_isolated_runner_protects_the_cache_lower_layer(self) -> None:
        text = ISOLATED_RUNNER.read_text()
        for required in (
            "mount -t overlay overlay",
            "lowerdir=",
            "upperdir=",
            "workdir=",
            "sudo umount",
            "[c]ompile.sh.*build",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_meson_runner_keeps_the_compatible_entrypoint(self) -> None:
        text = MESON_RUNNER.read_text()
        self.assertIn("build-bananapi-meson-candidates.sh", text)
        self.assertIn("run-bananapi-candidates-isolated-cache.sh", text)

    def test_build_tool_rejects_non_reference_release(self) -> None:
        environment = os.environ.copy()
        environment["RELEASE"] = "jammy"
        with tempfile.TemporaryDirectory() as output_dir:
            environment["OUTPUT_DIR"] = output_dir
            result = subprocess.run(
                ["bash", str(BUILD_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("只接受 RELEASE=trixie", result.stderr)


if __name__ == "__main__":
    unittest.main()
