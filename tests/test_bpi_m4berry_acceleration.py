#!/usr/bin/env python3
"""BPI-M4 Berry H618 硬體加速設定回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4berry.conf"
CEDRUS_PATCH = REPO_DIR / (
    "patch/kernel/archive/sunxi-6.18/patches.armbian/"
    "drv-staging-media-sunxi-cedrus-add-H616-variant.patch"
)


class M4BerryAccelerationTests(unittest.TestCase):
    def test_h618_uses_h616_cedrus_variant(self) -> None:
        patch = CEDRUS_PATCH.read_text(encoding="utf-8")
        h616_match = patch.split(
            'compatible = "allwinner,sun50i-h616-video-engine"', maxsplit=1
        )[1]
        self.assertIn(".data = &sun50i_h616_cedrus_variant", h616_match)
        self.assertNotIn(".data = &sun50i_h6_cedrus_variant", h616_match)

    def test_board_reserves_enough_cma_for_hevc(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertIn("image_specific_armbian_env_ready__bananapi_m4berry_cma", config)
        self.assertIn("cma=256M", config)
        self.assertIn("sed -E -i", config)


if __name__ == "__main__":
    unittest.main()
