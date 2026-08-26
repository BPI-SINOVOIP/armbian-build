#!/usr/bin/env python3
"""Banana Pi Sunxi 板級來源政策回歸測試。"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BananaPiSunxiSourcePolicyTests(unittest.TestCase):
    """防止不同 Sunxi SoC 誤用 overlay 相容設定。"""

    def test_m2_magic_uses_a33_overlay_prefix(self) -> None:
        board = (ROOT / "config/boards/bananapim2magic.csc").read_text()
        self.assertIn('OVERLAY_PREFIX="sun8i-a33"', board)
        self.assertNotIn('OVERLAY_PREFIX="sun8i-h3"', board)

    def test_r40_i2c_overlays_target_r40(self) -> None:
        for version in ("6.18", "7.0"):
            overlay_dir = ROOT / f"patch/kernel/archive/sunxi-{version}/overlay_32"
            for bus in ("i2c2", "i2c3"):
                path = overlay_dir / f"sun8i-r40-{bus}.dtso"
                with self.subTest(version=version, bus=bus):
                    text = path.read_text()
                    self.assertIn(
                        'compatible = "allwinner,sun8i-r40";',
                        text,
                    )
                    self.assertNotIn(
                        'compatible = "allwinner,sun8i-h3";',
                        text,
                    )


if __name__ == "__main__":
    unittest.main()
