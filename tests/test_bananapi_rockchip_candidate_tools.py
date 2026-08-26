#!/usr/bin/env python3
"""Banana Pi Rockchip 候選映像工具回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3308-current.json"
M7_CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3588-m7-current.json"
M5PRO_CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3576-m5pro-edge.json"
BUILD_SCRIPT = ROOT / "tools/build-bananapi-rockchip-candidates.sh"
VERIFY_SCRIPT = ROOT / "tools/verify-bananapi-rockchip-candidates.sh"
ROOTFS_CACHE = ROOT / "lib/functions/rootfs/create-cache.sh"


class BananaPiRockchipCandidateToolTests(unittest.TestCase):
    """驗證 RK3308 來源、映像與無顯示板級守門。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.m7_config = json.loads(M7_CONFIG.read_text())
        cls.m5pro_config = json.loads(M5PRO_CONFIG.read_text())

    def test_rootfs_extract_avoids_cursor_wait_on_dumb_terminal(self) -> None:
        text = ROOTFS_CACHE.read_text()
        self.assertIn('[[ -t 1 && "${TERM:-dumb}" != "dumb" ]]', text)
        self.assertIn('pv_cursor=(-c)', text)
        self.assertIn('pv -p -b -r "${pv_cursor[@]}"', text)

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

    def test_validation_config_has_exact_m7_policy(self) -> None:
        config = self.m7_config
        self.assertEqual(config["kernel_family"], "rockchip64")
        self.assertEqual(
            config["rkbin_commit"],
            "1d3c61008fa823936ae7a59615393f8294b64456",
        )
        self.assertEqual(set(config["boards"]), {"bananapim7"})
        policy = config["boards"]["bananapim7"]
        self.assertEqual(policy["uboot_version"], "2017.09")
        self.assertEqual(
            policy["uboot_git_ref"],
            "commit:39cd993e5d6296635438e84f4576b3a9bf76f86e",
        )
        self.assertEqual(
            policy["uboot_payloads"],
            ["idbloader.img@32768", "u-boot.itb@8388608"],
        )
        self.assertEqual(policy["sd_bus_width"], 4)
        self.assertIn("/mmc@fe2e0000=8", policy["additional_bus_widths"])
        self.assertNotIn("/ethernet@fe1b0000", policy["required_status_nodes"])
        self.assertNotIn("/ethernet@fe1c0000", policy["required_status_nodes"])

    def test_m7_blob_hashes_and_packages_are_complete(self) -> None:
        self.assertEqual(len(self.m7_config["rkbin_blobs"]), 3)
        for path, digest in self.m7_config["rkbin_blobs"].items():
            with self.subTest(path=path):
                self.assertTrue(path.startswith("rk35/rk3588_"))
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
        board_text = (ROOT / "config/boards/bananapim7.conf").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.m7_config["common_packages"]) <= packages)

    def test_generic_verifier_supports_commit_and_multiple_payloads(self) -> None:
        build_text = (ROOT / "tools/build-bananapi-sunxi-candidates.sh").read_text()
        verify_text = (ROOT / "tools/verify-bananapi-sunxi-candidates.sh").read_text()
        for required in (
            "uboot_git_source",
            "uboot_git_ref",
            "uboot_revision",
            "uboot_version",
        ):
            with self.subTest(required=required):
                self.assertIn(required, build_text)
                self.assertIn(required, verify_text)
        self.assertIn("uboot_payloads", verify_text)
        self.assertIn('UBOOT_GIT_BRANCH=\\"${uboot_git_ref}\\"', verify_text)
        self.assertIn('UBOOT_GIT_REVISION=\\"${uboot_revision}\\"', verify_text)

    def test_p2_pro_board_packages_match_policy(self) -> None:
        board_text = (ROOT / "config/boards/bananapip2pro.wip").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)

    def test_validation_config_has_exact_m5_pro_edge_policy(self) -> None:
        config = self.m5pro_config
        self.assertEqual(config["candidate_branch"], "edge")
        self.assertEqual(config["kernel_family"], "rockchip64")
        self.assertEqual(
            config["linux_commit"],
            "458c6079fc1d41d564c37679c8ace02cd83ee817",
        )
        self.assertEqual(
            config["rkbin_commit"],
            "1d3c61008fa823936ae7a59615393f8294b64456",
        )
        self.assertEqual(set(config["boards"]), {"bananapim5pro"})
        policy = config["boards"]["bananapim5pro"]
        self.assertEqual(policy["uboot_version"], "2017.09")
        self.assertEqual(
            policy["uboot_git_ref"],
            "commit:39cd993e5d6296635438e84f4576b3a9bf76f86e",
        )
        self.assertEqual(
            policy["uboot_payloads"],
            ["idbloader.img@32768", "u-boot.itb@8388608"],
        )
        self.assertEqual(policy["sd_node"], "/mmc@2a310000")
        self.assertIn("/mmc@2a320000=4", policy["additional_bus_widths"])
        self.assertIn("/mmc@2a330000=8", policy["additional_bus_widths"])

    def test_m5_pro_rkbin_inputs_are_complete(self) -> None:
        blobs = self.m5pro_config["rkbin_blobs"]
        self.assertEqual(len(blobs), 6)
        self.assertIn("rk35/RK3576MINIALL.ini", blobs)
        self.assertIn("tools/boot_merger", blobs)
        self.assertIn(
            "rk35/rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.08.bin",
            blobs,
        )
        for path, digest in blobs.items():
            with self.subTest(path=path):
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_generic_candidate_tools_use_configured_branch(self) -> None:
        build_text = (ROOT / "tools/build-bananapi-sunxi-candidates.sh").read_text()
        verify_text = (ROOT / "tools/verify-bananapi-sunxi-candidates.sh").read_text()
        self.assertIn('.get("candidate_branch", "current")', build_text)
        self.assertIn('.get("candidate_branch", "current")', verify_text)
        self.assertIn('branch="${BRANCH:-}"', build_text)
        self.assertIn('[[ -n "${branch}" ]] || branch="${candidate_branch}"', build_text)
        self.assertIn("linux-image-${candidate_branch}-${kernel_family}", verify_text)
        self.assertIn("linux-u-boot-${candidate_branch}-${board}", verify_text)
        self.assertIn('"branch ${candidate_branch}"', verify_text)

    def test_existing_configs_default_to_current_branch(self) -> None:
        self.assertEqual(self.config.get("candidate_branch", "current"), "current")
        self.assertEqual(self.m7_config.get("candidate_branch", "current"), "current")

    def test_m5_pro_board_packages_match_policy(self) -> None:
        board_text = (ROOT / "config/boards/bananapim5pro.conf").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.m5pro_config["common_packages"]) <= packages)


if __name__ == "__main__":
    unittest.main()
