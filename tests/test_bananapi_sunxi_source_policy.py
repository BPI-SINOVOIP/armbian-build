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
            for overlay in (
                "i2c2",
                "i2c3",
                "spi-spidev0",
                "spi-spidev1",
                "uart2",
                "uart4",
                "uart5",
                "uart7",
            ):
                path = overlay_dir / f"sun8i-r40-{overlay}.dtso"
                with self.subTest(version=version, overlay=overlay):
                    text = path.read_text()
                    self.assertIn(
                        'compatible = "allwinner,sun8i-r40";',
                        text,
                    )
                    self.assertNotIn(
                        'compatible = "allwinner,sun8i-h3";',
                        text,
                    )

    def test_firmware_artifact_accepts_an_exact_git_ref(self) -> None:
        compile_text = (
            ROOT / "lib/functions/compilation/packages/firmware-deb.sh"
        ).read_text()
        self.assertIn(
            'ARMBIAN_FIRMWARE_GIT_REF:-"branch:${ARMBIAN_FIRMWARE_GIT_BRANCH}"',
            compile_text,
        )
        self.assertIn(
            '"armbian-firmware-git" "${ARMBIAN_FIRMWARE_GIT_REF}"',
            compile_text,
        )
        for name in ("artifact-firmware.sh", "artifact-full_firmware.sh"):
            text = (ROOT / f"lib/functions/artifacts/{name}").read_text()
            with self.subTest(name=name):
                self.assertIn("ARMBIAN_FIRMWARE_GIT_REF", text)
                self.assertIn('[GIT_REF]="${ARMBIAN_FIRMWARE_REF}"', text)


if __name__ == "__main__":
    unittest.main()
