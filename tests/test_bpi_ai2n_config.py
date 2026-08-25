#!/usr/bin/env python3
"""BPI-AI2N 板卡欄位與初始密碼設定回歸測試。"""

from pathlib import Path
import re
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_CONFIG = REPO_DIR / "config/boards/bpi-ai2n.conf"
FAMILY_CONFIG = (
    REPO_DIR / "config/sources/families/renesas-rzv2n-bpi.conf"
)


class BpiAi2nConfigTests(unittest.TestCase):
    def test_supported_board_has_required_ownership_fields(self) -> None:
        config = BOARD_CONFIG.read_text(encoding="utf-8")
        for assignment in (
            'BOARD_VENDOR="sinovoip"',
            'BOARD_MAINTAINER="BPI-SINOVOIP"',
            'INTRODUCED="2026"',
            'KERNEL_TEST_TARGET="legacy"',
        ):
            self.assertIn(assignment, config)

    def test_family_does_not_override_requested_root_password(self) -> None:
        config = FAMILY_CONFIG.read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"^\s*(?:declare\s+-g\s+)?ROOTPWD=", config, re.MULTILINE)
        )


if __name__ == "__main__":
    unittest.main()
