#!/usr/bin/env python3
"""Banana Pi SpacemiT K1 來源政策回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapif3.conf"
CONFIG = ROOT / "config/validation/bananapi-spacemit-k1-f3-current.json"


class BananaPiSpacemitSourcePolicyTests(unittest.TestCase):
    """驗證 F3 current 使用固定來源與完整板級政策。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = BOARD.read_text()
        cls.config = json.loads(CONFIG.read_text())

    def test_board_has_one_vendor_and_exact_source_pins(self) -> None:
        self.assertEqual(self.board.count('BOARD_VENDOR="'), 1)
        self.assertIn('BOARD_VENDOR="sinovoip"', self.board)
        revisions = {
            item["revision"] for item in self.config["source_commits"].values()
        }
        for revision in revisions:
            with self.subTest(revision=revision):
                self.assertRegex(revision, r"^[0-9a-f]{40}$")
                self.assertIn(f'commit:{revision}', self.board)

    def test_board_exposes_k1_overlays_and_required_packages(self) -> None:
        self.assertIn('OVERLAY_PREFIX="k1"', self.board)
        package_line = next(
            line for line in self.board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)

    def test_policy_covers_boot_chain_and_storage_widths(self) -> None:
        policy = self.config["boards"]["bananapif3"]
        self.assertEqual(policy["uboot_version"], "2022.10")
        self.assertEqual(
            policy["uboot_payloads"],
            [
                "bootinfo_emmc.bin@0",
                "FSBL.bin@512",
                "fw_dynamic.itb@655360",
                "u-boot.itb@1048576",
            ],
        )
        self.assertEqual(policy["sd_bus_width"], 4)
        self.assertIn(
            "/soc/sdh@d4280800=4", policy["additional_bus_widths"]
        )
        self.assertIn(
            "/soc/sdh@d4281000=8", policy["additional_bus_widths"]
        )
        self.assertRegex(
            self.config["firmware_blobs"][
                "packages/blobs/riscv64/spacemit/esos.elf"
            ],
            r"^[0-9a-f]{64}$",
        )


if __name__ == "__main__":
    unittest.main()
