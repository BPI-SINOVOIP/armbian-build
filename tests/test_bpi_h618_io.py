#!/usr/bin/env python3
"""Banana Pi H618 共用 I/O 設定回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4berry.conf"
COMMON_CONFIG = REPO_DIR / "config/boards/include/bananapi-h618-common.inc"
OVERLAY_DIR = REPO_DIR / "patch/kernel/archive/sunxi-6.18/overlay_64"
UDEV_RULES = REPO_DIR / "packages/bsp/bananapi-h618/99-bananapi-h618-io.rules"
HARDWARE_INFO = REPO_DIR / "packages/bsp/bananapi-h618/bpi-h618-hw-info"
COMPAT_INSTALLER = REPO_DIR / (
    "packages/bsp/bananapi-h618/bpi-h618-io-compat-install"
)


class BpiH618IoTests(unittest.TestCase):
    def test_all_images_include_standard_io_tools(self) -> None:
        common = COMMON_CONFIG.read_text(encoding="utf-8")
        for package in (
            "gpiod",
            "i2c-tools",
            "python3-libgpiod",
            "python3-spidev",
            "v4l-utils",
        ):
            self.assertIn(package, common)

    def test_desktop_images_include_acceleration_tools(self) -> None:
        common = COMMON_CONFIG.read_text(encoding="utf-8")
        for package in (
            "gstreamer1.0-tools",
            "gstreamer1.0-plugins-bad",
            "libdrm-tests",
        ):
            self.assertIn(package, common)

    def test_mainline_rtl8821cu_driver_is_not_replaced(self) -> None:
        board = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("wifi-rtl8821cu/etc/modprobe.d/8821cu.conf", board)
        self.assertNotIn("bananapi_module_conf", board)

    def test_header_pwm_overlay_is_built_once(self) -> None:
        makefile = (OVERLAY_DIR / "Makefile").read_text(encoding="utf-8")
        overlay = (OVERLAY_DIR / "sun50i-h616-pwm1-pg19.dtso").read_text(
            encoding="utf-8"
        )
        self.assertEqual(makefile.count("sun50i-h616-pwm1-pg19.dtbo"), 1)
        self.assertIn("pwm1_pg_pin", overlay)
        self.assertIn('status = "okay";', overlay)

    def test_udev_rules_do_not_make_io_world_writable(self) -> None:
        rules = UDEV_RULES.read_text(encoding="utf-8")
        self.assertIn('GROUP="users"', rules)
        self.assertIn('MODE="0660"', rules)
        self.assertNotIn('MODE="0666"', rules)
        self.assertIn('KERNEL=="pwmchip*"', rules)
        self.assertIn("/sys%p/export", rules)

    def test_legacy_install_is_reproducibly_pinned(self) -> None:
        installer = COMPAT_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("da58b589a3ca3e44f569850f07ee17de2e294b5f", installer)
        self.assertIn("c04d27c86f65ed824921a457455a09d6820b9e1d", installer)
        self.assertIn("apt-get install --reinstall", installer)
        self.assertIn("--no-deps", installer)
        self.assertIn('"M4-Zero"', installer)

    def test_image_installs_diagnostic_and_compatibility_tools(self) -> None:
        common = COMMON_CONFIG.read_text(encoding="utf-8")
        self.assertIn("bpi-h618-hw-info", common)
        self.assertIn("bpi-h618-io-compat-install", common)
        hardware_info = HARDWARE_INFO.read_text(encoding="utf-8")
        self.assertIn("gpiodetect", hardware_info)
        self.assertIn("v4l2-ctl", hardware_info)


if __name__ == "__main__":
    unittest.main()
