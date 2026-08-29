#!/usr/bin/env python3
"""BPI-M4 Berry H618 硬體加速設定回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4berry.conf"
COMMON_CONFIG = REPO_DIR / "config/boards/include/bananapi-h618-common.inc"
KERNEL_CONFIG = REPO_DIR / "config/kernel/linux-sunxi64-current.config"
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

    def test_gpu_power_domain_provider_is_built_in(self) -> None:
        config = KERNEL_CONFIG.read_text(encoding="utf-8")
        self.assertIn("CONFIG_SUN50I_H6_PRCM_PPU=y", config)
        self.assertNotIn("CONFIG_SUN50I_H6_PRCM_PPU=m", config)
        self.assertIn("CONFIG_DRM_PANFROST=m", config)

    def test_board_reserves_enough_cma_for_hevc(self) -> None:
        board = BOARD_CONFIG.read_text(encoding="utf-8")
        common = COMMON_CONFIG.read_text(encoding="utf-8")
        self.assertIn("bananapi-h618-common.inc", board)
        self.assertIn("image_specific_armbian_env_ready__bananapi_h618_cma", common)
        self.assertIn("cma=256M", common)
        self.assertIn("sed -E -i", common)


if __name__ == "__main__":
    unittest.main()
