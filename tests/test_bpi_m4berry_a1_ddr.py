#!/usr/bin/env python3
"""BPI-M4 Berry A1 DDR 設定回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
DDR_PATCH = REPO_DIR / (
    "patch/u-boot/v2025-sunxi/"
    "0010-u-boot-configs-bananapi-m4berry-use-a1-ddr-profile.patch"
)


class M4BerryA1DdrTests(unittest.TestCase):
    def test_a1_profile_matches_validated_values(self) -> None:
        patch = DDR_PATCH.read_text(encoding="utf-8")
        expected = (
            "CONFIG_DRAM_SUNXI_CA_DRI=0x0d0d",
            "CONFIG_DRAM_SUNXI_TPR6=0x3a808080",
            "CONFIG_DRAM_SUNXI_TPR11=0x25252523",
            "CONFIG_DRAM_SUNXI_TPR12=0x110f0f10",
            "CONFIG_DRAM_CLK=792",
        )
        for setting in expected:
            with self.subTest(setting=setting):
                self.assertIn(setting, patch)

    def test_patch_does_not_force_memory_capacity(self) -> None:
        patch = DDR_PATCH.read_text(encoding="utf-8")
        self.assertNotIn("CONFIG_DRAM_SUNXI_2G", patch)
        self.assertNotIn("CONFIG_DRAM_SUNXI_4G", patch)


if __name__ == "__main__":
    unittest.main()
