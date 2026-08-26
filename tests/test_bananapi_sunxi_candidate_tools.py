#!/usr/bin/env python3
"""Banana Pi Sunxi 候選映像工具回歸測試。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-sunxi-a20-current.json"
H3_CONFIG = ROOT / "config/validation/bananapi-sunxi-h3-current.json"
H2PLUS_CONFIG = ROOT / "config/validation/bananapi-sunxi-h2plus-current.json"
BUILD_SCRIPT = ROOT / "tools/build-bananapi-sunxi-candidates.sh"
VERIFY_SCRIPT = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
ISOLATED_RUNNER = ROOT / "tools/run-bananapi-candidates-isolated-cache.sh"
EXPECTED_BOARDS = {"bananapi", "bananapipro"}


class BananaPiSunxiCandidateToolTests(unittest.TestCase):
    """驗證 A20 代表板來源、映像同一性與開機區守門。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())

    def test_validation_config_has_exact_board_set(self) -> None:
        self.assertEqual(self.config["schema_version"], 1)
        self.assertEqual(set(self.config["boards"]), EXPECTED_BOARDS)
        for board, policy in self.config["boards"].items():
            with self.subTest(board=board):
                self.assertEqual(policy["family"], "sun7i")
                self.assertEqual(policy["uboot_tag"], "v2024.01")
                self.assertEqual(policy["uboot_offset"], 8192)
                self.assertEqual(policy["overlay_prefix"], "sun7i-a20")
                self.assertEqual(policy["sd_bus_width"], 4)
                self.assertIn("/display-engine", policy["required_status_nodes"])
                self.assertNotIn("/soc/display-engine", policy["required_status_nodes"])

    def test_board_images_include_standard_io_tools(self) -> None:
        required = set(self.config["common_packages"])
        for board in EXPECTED_BOARDS:
            suffix = "conf" if board == "bananapi" else "csc"
            text = (ROOT / f"config/boards/{board}.{suffix}").read_text()
            package_line = next(
                line for line in text.splitlines()
                if line.startswith('PACKAGE_LIST_BOARD="')
            )
            packages = set(package_line.split('"', 2)[1].split())
            self.assertTrue(required <= packages)

    def test_h3_reference_policy_covers_wireless_and_header_io(self) -> None:
        config = json.loads(H3_CONFIG.read_text())
        self.assertEqual(set(config["boards"]), {"bananapim2plus"})
        policy = config["boards"]["bananapim2plus"]
        self.assertEqual(policy["family"], "sun8i")
        self.assertEqual(policy["sd_node"], "/soc/mmc@1c0f000")
        self.assertEqual(policy["default_overlays"], ["analog-codec"])
        self.assertTrue({"i2c0", "pwm", "spi-spidev", "uart2"} <= set(policy["required_overlays"]))
        board_text = (ROOT / "config/boards/bananapim2plus.conf").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(set(config["common_packages"]) <= set(package_line.split('"', 2)[1].split()))

        overlay_dir = ROOT / "patch/kernel/archive/sunxi-6.18/overlay_32"
        for overlay in policy["required_overlays"]:
            with self.subTest(overlay=overlay):
                self.assertTrue(
                    (overlay_dir / f"{policy['overlay_prefix']}-{overlay}.dtso").is_file()
                )

    def test_h2plus_policy_covers_storage_wireless_and_header_io(self) -> None:
        config = json.loads(H2PLUS_CONFIG.read_text())
        self.assertEqual(set(config["boards"]), {"bananapim2zero", "bananapip2zero"})
        overlay_dir = ROOT / "patch/kernel/archive/sunxi-6.18/overlay_32"
        for board, policy in config["boards"].items():
            with self.subTest(board=board):
                self.assertEqual(policy["family"], "sun8i")
                self.assertEqual(policy["sd_bus_width"], 4)
                self.assertIn("/soc/mmc@1c10000=4", policy["additional_bus_widths"])
                board_text = next((ROOT / "config/boards").glob(f"{board}.*")).read_text()
                package_line = next(
                    line for line in board_text.splitlines()
                    if line.startswith('PACKAGE_LIST_BOARD="')
                )
                self.assertTrue(
                    set(config["common_packages"])
                    <= set(package_line.split('"', 2)[1].split())
                )
                for overlay in policy["required_overlays"]:
                    self.assertTrue(
                        (overlay_dir / f"{policy['overlay_prefix']}-{overlay}.dtso").is_file()
                    )
        self.assertIn(
            "/soc/mmc@1c11000=8",
            config["boards"]["bananapip2zero"]["additional_bus_widths"],
        )

    def test_build_tool_records_reproducibility_evidence(self) -> None:
        text = BUILD_SCRIPT.read_text()
        for required in (
            "status --porcelain --untracked-files=all",
            "validate_default_userpatches",
            "cache 不是 OverlayFS",
            "CANDIDATE_LOCK_FILE",
            "source_commit",
            "source_tree",
            "validation_config_sha256",
            "build_parameters_sha256",
            "decompressed_sha256",
            "ARTIFACT_IGNORE_CACHE",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_verifier_checks_sunxi_boot_layout_read_only(self) -> None:
        text = VERIFY_SCRIPT.read_text()
        for required in (
            "--read-only",
            "mount -o ro,noload",
            "uboot_offset",
            'cmp --silent --ignore-initial="0:${offset}"',
            "u-boot-sunxi-with-spl.bin",
            "fdtfile=",
            "dtb_basename",
            "overlay_prefix=",
            "overlay_directory",
            "required_overlays",
            "additional_bus_widths",
            "sd_bus_width",
            "required_status_nodes",
            "common_kernel_options",
            "candidate_source_commit",
            "verifier_commit",
            "build_validation_config_sha256",
            "verification_config_sha256",
            "kernel_family",
            "xz -dc",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text if required != "u-boot-sunxi-with-spl.bin" else CONFIG.read_text())

    def test_isolated_runner_accepts_a_family_builder(self) -> None:
        text = ISOLATED_RUNNER.read_text()
        self.assertIn("CANDIDATE_BUILDER", text)
        self.assertIn('"${candidate_builder}" "$@"', text)

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
