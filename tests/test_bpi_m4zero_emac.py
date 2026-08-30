#!/usr/bin/env python3
"""BPI-M4 Zero EMAC 獨立板型回歸測試。"""

from pathlib import Path
import re
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4zeroemac.conf"
STANDARD_BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4zero.conf"
COMMON_CONFIG = REPO_DIR / "config/boards/include/bananapi-h618-common.inc"
KERNEL_CONFIG = REPO_DIR / "config/kernel/linux-sunxi64-current.config"
DT_DIR = REPO_DIR / "patch/kernel/archive/sunxi-6.18/dt_64"
OVERLAY_DIR = REPO_DIR / "patch/kernel/archive/sunxi-6.18/overlay_64"
BOARD_DTS = DT_DIR / "sun50i-h618-bananapi-m4-zero-emac.dts"
STANDARD_DTS = DT_DIR / "sun50i-h618-bananapi-m4-zero.dts"
WIRELESS_OVERLAY = OVERLAY_DIR / (
    "sun50i-h616-bananapi-m4-zero-emac-sdio-wifi-bt.dtso"
)


def node_body(source: str, label: str) -> str:
    """擷取簡單的標籤節點內容。"""
    match = re.search(
        rf"&{re.escape(label)}\s*\{{(?P<body>.*?)\n\}};", source, re.DOTALL
    )
    return match.group("body") if match else ""


class M4ZeroEmacTests(unittest.TestCase):
    def test_board_has_unique_identity_and_current_only_kernel(self) -> None:
        board = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertIn('BOARD_NAME="BananaPi BPI-M4-Zero EMAC"', board)
        self.assertIn(
            'BOOT_FDT_FILE="sun50i-h618-bananapi-m4-zero-emac.dtb"', board
        )
        self.assertIn(
            'DEFAULT_OVERLAYS="bananapi-m4-zero-emac-sdio-wifi-bt"', board
        )
        self.assertIn('KERNEL_TARGET="current"', board)
        self.assertIn('KERNEL_TEST_TARGET="current"', board)
        self.assertNotRegex(board, r'KERNEL_(?:TEST_)?TARGET="[^"]*edge')

    def test_board_reuses_validated_m4zero_uboot_patchset(self) -> None:
        board = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertIn('BOOTCONFIG="bananapi_m4zero_defconfig"', board)
        self.assertIn(
            'BOOTPATCHDIR="v2026.01 v2026.01/board_bananapim4zero"', board
        )
        self.assertIn('BOOTBRANCH_BOARD="tag:v2026.01"', board)

    def test_board_inherits_h618_acceleration_and_io_baseline(self) -> None:
        board = BOARD_CONFIG.read_text(encoding="utf-8")
        common = COMMON_CONFIG.read_text(encoding="utf-8")
        self.assertIn("bananapi-h618-common.inc", board)
        self.assertIn("cma=256M", common)
        for package in ("gpiod", "i2c-tools", "python3-spidev", "v4l-utils"):
            with self.subTest(package=package):
                self.assertIn(package, common)

    def test_board_installs_derivative_broadcom_firmware_aliases(self) -> None:
        board = BOARD_CONFIG.read_text(encoding="utf-8")
        aliases = (
            "brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.bin",
            "brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.txt",
            "brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.clm_blob",
            "BCM4345C0.sinovoip,bpi-m4-zero-emac.hcd",
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                self.assertIn(alias, board)
        self.assertIn("cyfmac43455-sdio.bin", board)
        self.assertIn("cyfmac43455-sdio.1LC.txt", board)
        self.assertIn("cyfmac43455-sdio.1LC.clm_blob", board)
        self.assertIn("BCM4345C0_003.001.025.0187.0366.1MW.hcd", board)

    def test_derivative_dts_enables_gpu_and_internal_emac(self) -> None:
        dts = BOARD_DTS.read_text(encoding="utf-8")
        self.assertIn('#include "sun50i-h618-bananapi-m4-zero.dts"', dts)
        self.assertIn('model = "BananaPi BPI-M4-Zero EMAC";', dts)
        self.assertIn('"sinovoip,bpi-m4-zero-emac"', dts)
        self.assertIn('"sinovoip,bpi-m4-zero"', dts)
        for label in ("gpu", "pwm", "pwm5", "emac1"):
            with self.subTest(label=label):
                self.assertIn('status = "okay";', node_body(dts, label))
        self.assertIn("clk_bypass_output = <0x1>;", node_body(dts, "pwm5"))

    def test_derivative_dts_binds_cpu_thermal_cooling(self) -> None:
        dts = BOARD_DTS.read_text(encoding="utf-8")
        self.assertIn("&{/thermal-zones/cpu-thermal}", dts)
        self.assertIn("cooling-maps", dts)
        self.assertIn("trip = <&cpu_threshold>;", dts)
        self.assertIn("cooling-device = <&cpu0 1 3>;", dts)
        self.assertIn("trip = <&cpu_target>;", dts)
        self.assertIn(
            "cooling-device = <&cpu0 4 THERMAL_NO_LIMIT>;",
            dts,
        )

    def test_standard_board_keeps_fpc_ethernet_opt_in(self) -> None:
        standard_board = STANDARD_BOARD_CONFIG.read_text(encoding="utf-8")
        standard_dts = STANDARD_DTS.read_text(encoding="utf-8")
        disabled_count = node_body(standard_dts, "emac1").count(
            'status = "disabled";'
        )
        self.assertEqual(disabled_count, 1)
        self.assertNotIn("bananapi-m4-zero-emac", standard_board)
        self.assertNotIn('model = "BananaPi BPI-M4-Zero EMAC";', standard_dts)

    def test_base_dts_describes_ac300_clock_and_calibration(self) -> None:
        dts = STANDARD_DTS.read_text(encoding="utf-8")
        expected = (
            'compatible = "allwinner,sun50i-h616-internal-emac";',
            '"allwinner,sun50i-h618-ac300-ephy"',
            'clock-names = "ephy", "pwm";',
            "clock-frequency = <2000000>;",
            "nvmem-cells = <&ephy_calibration>;",
            "ephy-calibration@2c",
        )
        for setting in expected:
            with self.subTest(setting=setting):
                self.assertIn(setting, dts)

    def test_wireless_overlay_preserves_derivative_model(self) -> None:
        overlay = WIRELESS_OVERLAY.read_text(encoding="utf-8")
        makefile = (OVERLAY_DIR / "Makefile").read_text(encoding="utf-8")
        self.assertIn('"sinovoip,bpi-m4-zero-emac"', overlay)
        self.assertIn("target = <&mmc1>;", overlay)
        self.assertIn("target = <&uart1>;", overlay)
        self.assertIn('compatible = "brcm,bcm43540-bt";', overlay)
        self.assertNotRegex(overlay, r"\bmodel\s*=")
        self.assertEqual(
            makefile.count(
                "sun50i-h616-bananapi-m4-zero-emac-sdio-wifi-bt.dtbo"
            ),
            1,
        )

    def test_ac300_probe_dependencies_are_built_in(self) -> None:
        config = KERNEL_CONFIG.read_text(encoding="utf-8")
        for setting in (
            "CONFIG_AC300_PHY=y",
            "CONFIG_COMMON_CLK_PWM=y",
            "CONFIG_PWM_SUNXI_ENHANCE=y",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, config)


if __name__ == "__main__":
    unittest.main()
