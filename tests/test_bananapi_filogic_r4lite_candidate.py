#!/usr/bin/env python3
"""Banana Pi R4 Lite Filogic 候選映像與來源政策回歸測試。"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7987-r4lite-current.json"
BOARD = ROOT / "config/boards/bananapir4lite.wip"
KERNEL_CONFIG = ROOT / "config/kernel/linux-filogic-current.config"
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/filogic-6.17/patches.armbian/mt7987a-bananapi-bpi-r4-lite-sd.patch"
)
KERNEL_SERIES = ROOT / "patch/kernel/archive/filogic-6.17/series.conf"
RUNNER = ROOT / "tools/run-bananapi-filogic-r4lite-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-filogic-r4lite-candidate.sh"
GENERIC_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
FIRMWARE = ROOT / "packages/blobs/filogic/firmware/mediatek/mt7987"


class BananaPiFilogicR4LiteCandidateTests(unittest.TestCase):
    """防止 R4 Lite SD 啟動鏈、網路、I/O 與韌體政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapir4lite"]

    def test_sources_are_exactly_pinned(self) -> None:
        board_text = BOARD.read_text()
        expected = {
            'KERNELBRANCH_BOARD="commit:0529574fee9fcaa75159f9edcedf35e8bc57400d"',
            'BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"',
            'ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'LINUX_FIRMWARE_GIT_REF_BOARD="commit:01205307636157a12c29e6a774bf83b218732050"',
        }
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, board_text)
        self.assertNotIn("branch:6.17-r4lite", board_text)

    def test_uboot_uses_standard_boot_and_protected_environment(self) -> None:
        board_text = BOARD.read_text()
        for value in (
            "post_config_uboot_target__bananapir4lite_standard_boot",
            "scripts/config --disable CONFIG_USE_DEFAULT_ENV_FILE",
            "scripts/config --disable CONFIG_AUTOBOOT_KEYED",
            "CONFIG_ENV_MMC_PARTITION ubootenv",
            "CONFIG_ENV_OFFSET 0x400000",
            "CONFIG_ENV_OFFSET_REDUND 0x440000",
            "mediatek/mt7987a-bananapi-bpi-r4-lite-sd.dtb",
        ):
            with self.subTest(value=value):
                self.assertIn(value, board_text)
        required = self.policy["uboot_required_config_options"]
        self.assertIn("# CONFIG_USE_DEFAULT_ENV_FILE is not set", required)
        self.assertIn("CONFIG_BOOTSTD_BOOTCOMMAND=y", required)
        self.assertIn("CONFIG_ENV_MMC_PARTITION=\"ubootenv\"", required)

    def test_vendor_fit_paths_are_rejected(self) -> None:
        self.assertIn("root=/dev/fit0", self.policy["uboot_forbidden_binary_strings"])
        self.assertIn("/dev/fit0", self.policy["dtb_forbidden_binary_strings"])
        generic_text = GENERIC_VERIFIER.read_text()
        self.assertIn("uboot_forbidden_binary_strings", generic_text)
        self.assertIn("dtb_forbidden_binary_strings", generic_text)
        patch_text = KERNEL_PATCH.read_text()
        self.assertIn("mt7987a-bananapi-bpi-r4-lite-armbian.dtbo", patch_text)
        self.assertIn("/delete-property/ bootargs", patch_text)
        self.assertIn(
            'bootargs = "console=ttyS0,115200n1 loglevel=6 '
            'earlycon=uart8250,mmio32,0x11000000 pci=pcie_bus_perf";',
            patch_text,
        )
        self.assertIn(KERNEL_PATCH.name, KERNEL_SERIES.read_text())

    def test_firmware_matches_fixed_linux_firmware(self) -> None:
        expected = {
            "i2p5ge-phy-DSPBitTb.bin": "1f7b7fd1c243576e04c16b98c649db1e3326f6a715556c2a56094bcd7d300d71",
            "i2p5ge-phy-pmb.bin": "941e3118493d5cb14323968ebc1193b23411d7c330a566014eeeb51c5ea7ed45",
            "LICENCE.mediatek": "a90d3f66704d85889945fec5525ea77622549da83aced1aac99828383f8f1805",
            "SOURCE.md": "c8892a9af291d00b6394180f9c33f8ab5fa75caa4ebaacdf4f0ce02a42b7e9ca",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256((FIRMWARE / filename).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)
        self.assertEqual(
            self.config["linux_firmware_commit"],
            "01205307636157a12c29e6a774bf83b218732050",
        )

    def test_kernel_enables_buttons_expander_network_and_io(self) -> None:
        text = KERNEL_CONFIG.read_text()
        for option, value in self.config["common_kernel_options"].items():
            with self.subTest(option=option):
                self.assertIn(f"{option}={value}", text)
        self.assertNotIn("# CONFIG_INPUT is not set", text)
        self.assertIn("CONFIG_MEDIATEK_2P5GE_PHY=y", text)
        self.assertEqual(
            self.config["common_kernel_options"]["CONFIG_MEDIATEK_2P5GE_PHY"],
            "y",
        )

    def test_sd_gpt_dtb_and_payload_contract_is_complete(self) -> None:
        self.assertEqual(self.policy["root_partition_number"], 5)
        self.assertEqual(self.policy["sd_node"], "/soc/mmc@11230000")
        self.assertEqual(self.policy["sd_bus_width"], 4)
        self.assertIn(
            "/soc/mmc@11230000:max-frequency=52000000",
            self.policy["required_uint_properties"],
        )
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["bl2.img@17408", "u-boot.fip@6815744"],
        )
        self.assertEqual(
            self.policy["required_partitions"],
            [
                "1:bl2:34:8158",
                "2:ubootenv:8192:1024",
                "3:factory:9216:4096",
                "4:fip:13312:8192",
                "5:*:32768:*",
            ],
        )

    def test_dedicated_entrypoints_select_only_r4lite(self) -> None:
        for text in (RUNNER.read_text(), VERIFIER.read_text()):
            self.assertIn("bananapi-filogic-mt7987-r4lite-current.json", text)
            self.assertIn("bananapi-filogic-mt7987-r4lite-trixie-current-cli", text)
            self.assertIn('BOARDS="bananapir4lite"', text)
        self.assertIn("bananapi-filogic-r4lite-cache-overlay", RUNNER.read_text())


if __name__ == "__main__":
    unittest.main()
