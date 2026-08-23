#!/usr/bin/env python3
"""BPI-M4 Berry A1 全映像矩陣腳本回歸測試。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools/build-bpi-m4berry-a1-792-matrix.sh"
)


class M4BerryA1MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_source_matrix_has_five_releases_and_two_profiles(self) -> None:
        names = re.findall(
            r"[0-9a-f]{64} (Armbian-[^\"]+Bananapim4berry_[^\"]+\.img\.xz)",
            self.script,
        )
        self.assertEqual(len(names), 10)
        releases = {
            re.search(r"Bananapim4berry_([^_]+)_current", name).group(1)
            for name in names
        }
        self.assertEqual(
            releases, {"bookworm", "jammy", "noble", "resolute", "trixie"}
        )
        self.assertEqual(sum("_xfce_desktop.img.xz" in name for name in names), 5)

    def test_script_is_m4berry_specific(self) -> None:
        self.assertIn("linux-u-boot-current-bananapim4berry", self.script)
        self.assertIn("Bananapim4berry_", self.script)
        self.assertNotIn("linux-u-boot-current-bananapim4zero", self.script)
        self.assertNotIn("Bananapim4zero_", self.script)

    def test_locked_bootloader_identity_is_present(self) -> None:
        self.assertIn('expected_build_id="P25cb"', self.script)
        self.assertIn('expected_uboot_version="2025.04"', self.script)
        self.assertIn("93c3dc0766a85974bf8675ac770bf1ebb", self.script)
        self.assertIn("CONFIG_DRAM_CLK=792", self.script)
        self.assertIn("CONFIG_DRAM_SUNXI_TPR11=0x25252523", self.script)
        self.assertIn("CONFIG_DRAM_SUNXI_TPR12=0x110f0f10", self.script)

    def test_output_keeps_raw_and_compressed_images(self) -> None:
        self.assertIn("-type f -name '*.img'", self.script)
        self.assertIn("-type f -name '*.img.xz'", self.script)
        self.assertIn('[[ "${#source_entries[@]}" == 10 ]]', self.script)
        self.assertIn("outside_bootloader_region_unchanged=yes", self.script)


if __name__ == "__main__":
    unittest.main()
