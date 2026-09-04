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
        for version in ("6.18", "7.1"):
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

    def test_m3_rejects_the_a83t_mmc_calibration_patch(self) -> None:
        board = (ROOT / "config/boards/bananapim3.csc").read_text()
        self.assertNotIn("BOOTPATCHDIR", board)
        patch_dir = ROOT / "patch/u-boot/u-boot-sunxi/board_bananapim3"
        self.assertEqual(list(patch_dir.glob("*.patch")), [])

    def test_m64_pins_the_complete_current_boot_chain(self) -> None:
        board = (ROOT / "config/boards/bananapim64.csc").read_text()
        for expected in (
            'BOOTPATCHDIR="v2024.01/board_bananapim64"',
            'KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"',
            'BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"',
            'ATFBRANCH_BOARD="commit:c2a0e7080d64d69940be4ad0ff6578501f3cbf9e"',
            'CRUSTBRANCH_BOARD="commit:ffe9f1ac9c675e6e67db9084bd19fbdeffd8e162"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, board)
        self.assertNotIn("CONFIG_DRAM_CLK", board)

        patch = (
            ROOT
            / "patch/u-boot/v2024.01/board_bananapim64/lower-default-dram-frequency.patch"
        ).read_text()
        self.assertIn("default 648 if MACH_SUN50I || MACH_SUN50I_H5", patch)

    def test_6204_pins_the_complete_legacy_source_set(self) -> None:
        board = (ROOT / "config/boards/bananapi6204.wip").read_text()
        for expected in (
            'KERNELBRANCH_BOARD="commit:2538fbeff8a94ee2b54eb09d92209e24a1e650d4"',
            'BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            "post_family_config_branch_legacy__bananapi6204_pin_sources",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, board)
        self.assertNotIn("CONFIG_DRAM_CLK", board)

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
