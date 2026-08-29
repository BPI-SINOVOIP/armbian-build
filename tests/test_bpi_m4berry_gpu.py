#!/usr/bin/env python3
"""BPI-M4 Berry GPU 裝置樹回歸測試。"""

from pathlib import Path
import re
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_DTS = REPO_DIR / (
    "patch/kernel/archive/sunxi-6.18/dt_64/"
    "sun50i-h618-bananapi-m4-berry.dts"
)


class M4BerryGpuTests(unittest.TestCase):
    def test_board_enables_gpu_with_regulator(self) -> None:
        dts = BOARD_DTS.read_text(encoding="utf-8")
        match = re.search(r"&gpu\s*\{(?P<body>.*?)\};", dts, re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        self.assertIn("mali-supply = <&reg_dcdc1>;", body)
        self.assertIn('status = "okay";', body)


if __name__ == "__main__":
    unittest.main()
