#!/usr/bin/env python3
"""BPI-M4 Berry GPU 初始化設定回歸測試。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
KERNEL_CONFIG = REPO_DIR / "config/kernel/linux-sunxi64-current.config"
BOARD_DTS = (
    REPO_DIR
    / "patch/kernel/archive/sunxi-6.18/dt_64/sun50i-h618-bananapi-m4-berry.dts"
)


class M4BerryGpuTests(unittest.TestCase):
    def test_gpu_power_domain_provider_is_built_in(self) -> None:
        config = KERNEL_CONFIG.read_text(encoding="utf-8")
        self.assertIn("CONFIG_SUN50I_H6_PRCM_PPU=y", config)
        self.assertNotIn("CONFIG_SUN50I_H6_PRCM_PPU=m", config)

    def test_panfrost_remains_enabled(self) -> None:
        config = KERNEL_CONFIG.read_text(encoding="utf-8")
        self.assertIn("CONFIG_DRM_PANFROST=m", config)

    def test_board_enables_gpu_with_regulator(self) -> None:
        dts = BOARD_DTS.read_text(encoding="utf-8")
        match = re.search(r"&gpu\s*\{(?P<body>.*?)\};", dts, re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        self.assertIn("mali-supply = <&reg_dcdc1>;", body)
        self.assertIn('status = "okay";', body)


if __name__ == "__main__":
    unittest.main()
