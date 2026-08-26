#!/usr/bin/env python3
"""Banana Pi Rockchip 板級來源政策回歸測試。"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RKBIN_COMMIT = "46c4793ea2dcea7c8331fce9f07b5c80561a0395"
IO_PACKAGES = {
    "gpiod",
    "i2c-tools",
    "python3-libgpiod",
    "python3-spidev",
    "v4l-utils",
}
RADIO_PACKAGES = {"rfkill", "bluetooth", "bluez", "bluez-tools"}


class BananaPiRockchipSourcePolicyTests(unittest.TestCase):
    """防止 P2 Pro 的啟動二進位來源與基本工具政策倒退。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = (ROOT / "config/boards/bananapip2pro.wip").read_text()
        cls.extension = (ROOT / "extensions/rkbin-tools.sh").read_text()

    def test_rkbin_extension_accepts_an_immutable_ref(self) -> None:
        self.assertIn("RKBIN_GIT_REF", self.extension)
        self.assertIn(
            'RKBIN_GIT_REF:-branch:${RKBIN_GIT_BRANCH:-master}',
            self.extension,
        )

    def test_p2_pro_pins_rkbin_commit(self) -> None:
        self.assertIn(
            f'RKBIN_GIT_REF="commit:{RKBIN_COMMIT}"',
            self.board,
        )
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.board)

    def test_p2_pro_uses_expected_rk3308_blobs(self) -> None:
        self.assertIn(
            'DDR_BLOB="rk33/rk3308_ddr_589MHz_uart2_m1_v1.30.bin"',
            self.board,
        )
        self.assertIn(
            'BL31_BLOB="rk33/rk3308_bl31_v2.26.elf"',
            self.board,
        )
        self.assertIn(
            'MINILOADER_BLOB="rk33/rk3308_miniloader_sd_nand_v1.13.bin"',
            self.board,
        )

    def test_p2_pro_includes_standard_io_and_radio_packages(self) -> None:
        package_line = next(
            line
            for line in self.board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(IO_PACKAGES <= packages)
        self.assertTrue(RADIO_PACKAGES <= packages)


if __name__ == "__main__":
    unittest.main()
