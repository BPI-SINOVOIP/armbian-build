#!/usr/bin/env python3
"""BPI-M4 Zero RTL8821CU 主線驅動回歸測試。"""

from pathlib import Path
import re
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bananapim4zero.conf"
KERNEL_CONFIGS = (
    REPO_DIR / "config/kernel/linux-sunxi64-current.config",
    REPO_DIR / "config/kernel/linux-sunxi64-edge.config",
)


class M4ZeroWifiTests(unittest.TestCase):
    def test_board_does_not_block_mainline_rtl8821cu(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")

        self.assertNotRegex(
            config,
            r"MODULES_BLACKLIST=.*\brtw88_(?:8821c|8821cu)\b",
        )
        self.assertNotRegex(
            config,
            r"PACKAGE_LIST_BOARD=.*\bwifi-rtl8821cu\b",
        )
        self.assertNotIn("wifi-rtl8821cu/etc/modprobe.d/8821cu.conf", config)

    def test_current_and_edge_enable_mainline_rtl8821cu(self) -> None:
        for kernel_config_path in KERNEL_CONFIGS:
            with self.subTest(kernel_config=kernel_config_path.name):
                kernel_config = kernel_config_path.read_text(encoding="utf-8")
                enabled = re.findall(
                    r"^CONFIG_RTW88_8821CU=m$",
                    kernel_config,
                    flags=re.MULTILINE,
                )
                self.assertEqual(enabled, ["CONFIG_RTW88_8821CU=m"])


if __name__ == "__main__":
    unittest.main()
