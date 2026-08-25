#!/usr/bin/env python3
"""BPI-M4 Berry 40-pin 與映像工具設定回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4berry.conf"
CURRENT_KERNEL_CONFIG = REPO_DIR / "config/kernel/linux-sunxi64-current.config"
EDGE_KERNEL_CONFIG = REPO_DIR / "config/kernel/linux-sunxi64-edge.config"
OVERLAY_DIR = REPO_DIR / "patch/kernel/archive/sunxi-6.18/overlay_64"
UDEV_RULES = (
    REPO_DIR / "packages/bsp/bananapi-m4berry/99-bananapi-m4berry-io.rules"
)
HARDWARE_INFO = (
    REPO_DIR / "packages/bsp/bananapi-m4berry/bpi-m4berry-hw-info"
)
COMPAT_INSTALLER = (
    REPO_DIR / "packages/bsp/bananapi-m4berry/bpi-m4berry-io-compat-install"
)


class M4BerryIoTests(unittest.TestCase):
    def test_all_images_include_standard_io_tools(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        for package in (
            "gpiod",
            "i2c-tools",
            "python3-libgpiod",
            "python3-spidev",
            "v4l-utils",
        ):
            self.assertIn(package, config)

    def test_desktop_images_include_acceleration_tools(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        for package in (
            "gstreamer1.0-tools",
            "gstreamer1.0-plugins-bad",
            "libdrm-tests",
        ):
            self.assertIn(package, config)

    def test_mainline_rtl8821cu_driver_is_not_blocked(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("wifi-rtl8821cu/etc/modprobe.d/8821cu.conf", config)
        self.assertNotRegex(
            config,
            r"MODULES_BLACKLIST=.*\brtw88_(?:8821c|8821cu)\b",
        )

        for kernel_config_path in (CURRENT_KERNEL_CONFIG, EDGE_KERNEL_CONFIG):
            kernel_config = kernel_config_path.read_text(encoding="utf-8")
            self.assertIn("CONFIG_RTW88_8821CU=m", kernel_config)

    def test_header_pwm_overlay_is_built_and_documented(self) -> None:
        makefile = (OVERLAY_DIR / "Makefile").read_text(encoding="utf-8")
        readme = (OVERLAY_DIR / "README.sun50i-h616-overlays").read_text(
            encoding="utf-8"
        )
        overlay = (OVERLAY_DIR / "sun50i-h616-pwm1-pg19.dtso").read_text(
            encoding="utf-8"
        )
        self.assertIn("sun50i-h616-pwm1-pg19.dtbo", makefile)
        self.assertIn("README.sun50i-h616-overlays", makefile)
        self.assertIn("pwm1_pg_pin", overlay)
        self.assertIn("實體 pin 7", readme)

    def test_udev_rules_do_not_make_io_world_writable(self) -> None:
        rules = UDEV_RULES.read_text(encoding="utf-8")
        self.assertIn('GROUP="users"', rules)
        self.assertIn('MODE="0660"', rules)
        self.assertNotIn('MODE="0666"', rules)

    def test_pwm_sysfs_permissions_are_handled_explicitly(self) -> None:
        rules = UDEV_RULES.read_text(encoding="utf-8")
        self.assertIn('KERNEL=="pwmchip*"', rules)
        self.assertIn("/sys%p/export", rules)
        self.assertIn('KERNEL=="pwm[0-9]*"', rules)

    def test_legacy_install_is_reproducibly_pinned(self) -> None:
        installer = COMPAT_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("da58b589a3ca3e44f569850f07ee17de2e294b5f", installer)
        self.assertIn("c04d27c86f65ed824921a457455a09d6820b9e1d", installer)
        self.assertIn("apt-get install --reinstall", installer)
        self.assertIn("--no-deps", installer)

    def test_image_installs_diagnostic_and_compatibility_tools(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        self.assertIn("bpi-m4berry-hw-info", config)
        self.assertIn("bpi-m4berry-io-compat-install", config)
        hardware_info = HARDWARE_INFO.read_text(encoding="utf-8")
        self.assertIn("gpiodetect", hardware_info)
        self.assertIn("v4l2-ctl", hardware_info)


if __name__ == "__main__":
    unittest.main()
