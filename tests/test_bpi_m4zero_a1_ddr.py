#!/usr/bin/env python3
"""BPI-M4 Zero A1 DDR 正式建置契約測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
PATCH_DIR = REPO_DIR / "patch/u-boot/v2026.01/board_bananapim4zero"
BASELINE_PATCH = PATCH_DIR / "013-bananapi-m4zero-use-orangepi-zero3-ddr-baseline.patch"
DIAGNOSTICS_PATCH = PATCH_DIR / "014-sunxi-h616-add-structured-dram-diagnostics.patch"
A1_PATCH = PATCH_DIR / "016-bananapi-m4zero-use-0845-validated-ddr-lanes.patch"


class M4ZeroA1DdrTests(unittest.TestCase):
    def test_patch_order_and_production_scope(self) -> None:
        patch_names = sorted(path.name for path in PATCH_DIR.glob("*.patch"))
        self.assertLess(patch_names.index(BASELINE_PATCH.name), patch_names.index(A1_PATCH.name))
        self.assertNotIn(
            "012-mach-sunxi-dram_helpers-add-delay-to-steady-dram-detection.patch",
            patch_names,
        )
        self.assertFalse(any("standalone-ddr-lab" in name for name in patch_names))

    def test_a1_profile_matches_validated_values(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in (BASELINE_PATCH, A1_PATCH)
        )
        expected = (
            "CONFIG_DRAM_SUNXI_DX_ODT=0x07070707",
            "CONFIG_DRAM_SUNXI_DX_DRI=0x0e0e0e0e",
            "CONFIG_DRAM_SUNXI_CA_DRI=0x0d0d",
            "CONFIG_DRAM_SUNXI_ODT_EN=0xaaaaeeee",
            "CONFIG_DRAM_SUNXI_TPR6=0x3a808080",
            "CONFIG_DRAM_SUNXI_TPR10=0x402f6663",
            "CONFIG_DRAM_SUNXI_TPR11=0x25252523",
            "CONFIG_DRAM_SUNXI_TPR12=0x110f0f10",
            "CONFIG_DRAM_CLK=792",
        )
        for setting in expected:
            with self.subTest(setting=setting):
                self.assertIn(setting, combined)

    def test_structured_diagnostics_are_available_but_disabled(self) -> None:
        diagnostics = DIAGNOSTICS_PATCH.read_text(encoding="utf-8")
        self.assertIn("config DRAM_SUNXI_H616_DIAGNOSTICS", diagnostics)
        self.assertIn("M4ZDDR1_PROFILE0", diagnostics)
        self.assertIn("M4ZDDR1_FINAL", diagnostics)
        self.assertNotIn("CONFIG_DRAM_SUNXI_H616_DIAGNOSTICS=y", diagnostics)
        self.assertNotIn("CONFIG_DRAM_SUNXI_H616_LAB=y", diagnostics)


if __name__ == "__main__":
    unittest.main()
