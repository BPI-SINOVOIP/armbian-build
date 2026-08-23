#!/usr/bin/env python3
"""BPI-M4 Berry A1 DDR 候選設定回歸測試。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4berry.conf"
BASE_PATCH = (
    REPO_DIR
    / "patch/u-boot/v2025-sunxi/0008-u-boot-configs-Add-sun50i-h618-bananapi-m4berry-defconfig.patch"
)
A1_PATCH = (
    REPO_DIR
    / "patch/u-boot/v2025-sunxi/0010-u-boot-configs-bananapi-m4berry-use-a1-ddr-profile.patch"
)


def added_config_values(patch_text: str) -> dict[str, str]:
    """讀取補丁新增的 Kconfig 值。"""
    values: dict[str, str] = {}
    for line in patch_text.splitlines():
        match = re.fullmatch(r"\+CONFIG_([A-Z0-9_]+)=(.+)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


class M4BerryA1DdrTests(unittest.TestCase):
    def test_board_uses_expected_uboot_series_and_defconfig(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertIn('BOOTPATCHDIR="v2025-sunxi"', config)
        self.assertIn('BOOTCONFIG="bananapi_m4_berry_defconfig"', config)

    def test_a1_patch_matches_validated_candidate(self) -> None:
        values = added_config_values(A1_PATCH.read_text(encoding="utf-8"))
        self.assertEqual(values["DRAM_SUNXI_CA_DRI"], "0x0d0d")
        self.assertEqual(values["DRAM_SUNXI_TPR6"], "0x3a808080")
        self.assertEqual(values["DRAM_SUNXI_TPR11"], "0x25252523")
        self.assertEqual(values["DRAM_SUNXI_TPR12"], "0x110f0f10")

    def test_frequency_and_capacity_detection_are_not_changed(self) -> None:
        base = BASE_PATCH.read_text(encoding="utf-8")
        candidate = A1_PATCH.read_text(encoding="utf-8")
        self.assertIn("+CONFIG_DRAM_CLK=792", base)
        self.assertNotRegex(
            candidate, re.compile(r"^[+-]CONFIG_DRAM_CLK", re.MULTILINE)
        )
        self.assertNotRegex(
            candidate,
            re.compile(r"^[+-]CONFIG_(?:DRAM|SUNXI_DRAM).*SIZE", re.MULTILINE),
        )
        self.assertIn("geometry 探測", candidate)

    def test_patch_only_changes_four_ddr_fields(self) -> None:
        patch = A1_PATCH.read_text(encoding="utf-8")
        removed = re.findall(r"^-CONFIG_([A-Z0-9_]+)=", patch, re.MULTILINE)
        added = re.findall(r"^\+CONFIG_([A-Z0-9_]+)=", patch, re.MULTILINE)
        expected = {
            "DRAM_SUNXI_CA_DRI",
            "DRAM_SUNXI_TPR6",
            "DRAM_SUNXI_TPR11",
            "DRAM_SUNXI_TPR12",
        }
        self.assertEqual(set(removed), expected)
        self.assertEqual(set(added), expected)
        self.assertEqual(len(removed), 4)
        self.assertEqual(len(added), 4)


if __name__ == "__main__":
    unittest.main()
