#!/usr/bin/env python3
"""Banana Pi Rockchip 候選映像工具回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3308-current.json"
BUILD_SCRIPT = ROOT / "tools/build-bananapi-rockchip-candidates.sh"
VERIFY_SCRIPT = ROOT / "tools/verify-bananapi-rockchip-candidates.sh"


class BananaPiRockchipCandidateToolTests(unittest.TestCase):
    """驗證 RK3308 來源、映像與無顯示板級守門。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())

    def test_validation_config_has_exact_p2_pro_policy(self) -> None:
        self.assertEqual(self.config["schema_version"], 1)
        self.assertEqual(self.config["kernel_family"], "rockchip64")
        self.assertRegex(self.config["rkbin_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(self.config["boards"]), {"bananapip2pro"})
        policy = self.config["boards"]["bananapip2pro"]
        self.assertEqual(policy["uboot_tag"], "v2025.04")
        self.assertEqual(policy["uboot_payload"], "u-boot-rockchip.bin")
        self.assertEqual(policy["uboot_offset"], 32768)
        self.assertEqual(policy["sd_bus_width"], 4)
        self.assertIn("/mmc@ff490000=8", policy["additional_bus_widths"])
        self.assertIn("/mmc@ff4a0000=4", policy["additional_bus_widths"])

    def test_rkbin_blob_hashes_are_complete(self) -> None:
        self.assertEqual(len(self.config["rkbin_blobs"]), 3)
        for path, digest in self.config["rkbin_blobs"].items():
            with self.subTest(path=path):
                self.assertTrue(path.startswith("rk33/rk3308_"))
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_candidate_tools_preserve_rkbin_evidence(self) -> None:
        build_text = BUILD_SCRIPT.read_text()
        verify_text = VERIFY_SCRIPT.read_text()
        for required in (
            "RKBIN_EVIDENCE.tsv",
            "RKBIN_STATUS.json",
            "git -C \"${rkbin_dir}\" rev-parse HEAD",
            "sha256sum",
            "validation_config_sha256",
        ):
            with self.subTest(required=required):
                self.assertIn(required, build_text)
        for required in (
            "candidate_commit",
            "build_config_sha256",
            "cmp --silent",
            "rkbin_manifest_sha256",
            "GENERIC_CANDIDATE_VERIFIER",
        ):
            with self.subTest(required=required):
                self.assertIn(required, verify_text)

    def test_p2_pro_board_packages_match_policy(self) -> None:
        board_text = (ROOT / "config/boards/bananapip2pro.wip").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)


if __name__ == "__main__":
    unittest.main()
