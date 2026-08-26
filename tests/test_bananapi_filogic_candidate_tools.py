#!/usr/bin/env python3
"""Banana Pi Filogic 候選映像與來源政策回歸測試。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7986-r3-current.json"
BOARD = ROOT / "config/boards/bananapir3.wip"
FAMILY = ROOT / "config/sources/families/filogic.conf"
KERNEL_CONFIG = ROOT / "config/kernel/linux-filogic-current.config"
GENERIC_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"


class BananaPiFilogicCandidateToolTests(unittest.TestCase):
    """防止 R3 啟動鏈、GPT、網路與韌體政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapir3"]

    def test_source_set_is_exactly_pinned(self) -> None:
        self.assertEqual(
            self.config["linux_commit"],
            "4a4506842b77b597f11e7fc53be1dcdbdc97eea9",
        )
        self.assertEqual(
            self.policy["uboot_revision"],
            "34820924edbc4ec7803eb89d9852f4b870fa760a",
        )
        self.assertEqual(
            self.policy["atf_revision"],
            "c34e37802efaea356991a0811c8fc50f8a810f5b",
        )
        self.assertEqual(
            self.config["mt76_firmware_commit"],
            "c5a3bd91aa735b669618610d5f0ebfa5786845a6",
        )

    def test_r3_board_pins_sources_and_standard_boot(self) -> None:
        text = BOARD.read_text()
        for expected in (
            'KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"',
            'BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"',
            'ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"',
            'MT76_FIRMWARE_GIT_REF_BOARD="commit:c5a3bd91aa735b669618610d5f0ebfa5786845a6"',
            "post_config_uboot_target__bananapir3_standard_boot",
            "scripts/config --enable CONFIG_AUTOBOOT",
            "scripts/config --enable CONFIG_BOOTMETH_EXTLINUX",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertTrue(
            set(self.config["common_packages"])
            <= set(
                next(
                    line for line in text.splitlines()
                    if line.startswith('PACKAGE_LIST_BOARD="')
                ).split('"', 2)[1].split()
            )
        )

    def test_filogic_installer_packages_all_boot_material(self) -> None:
        text = FAMILY.read_text()
        self.assertIn('${FILOGIC_FIP_NAME}:u-boot.fip', text)
        self.assertIn('"${destination}/usr/lib/${uboot_name}/gpt"', text)
        function = text.split("write_uboot_platform() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('if="${source_dir}/gpt"', function)
        self.assertNotIn("${SRC}", function)
        self.assertNotIn("v2025.04", text.split("uboot_custom_postprocess() {", 1)[1].split("\n}", 1)[0])

    def test_gpt_and_payload_contract_is_complete(self) -> None:
        self.assertEqual(self.policy["root_partition_number"], 5)
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
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["bl2.img@17408", "u-boot.fip@6815744"],
        )
        self.assertEqual(self.policy["uboot_package_only_payloads"], ["gpt"])
        verifier = GENERIC_VERIFIER.read_text()
        self.assertIn("required_partitions", verifier)
        self.assertIn("root_partition_number", verifier)

    def test_kernel_enables_r3_storage_network_and_wmac(self) -> None:
        text = KERNEL_CONFIG.read_text()
        for option in self.config["common_kernel_options"].items():
            with self.subTest(option=option[0]):
                self.assertIn(f"{option[0]}={option[1]}", text)

    def test_mt7986_firmware_manifest_is_complete(self) -> None:
        blobs = self.config["installed_firmware_blobs"]
        self.assertEqual(len(blobs), 12)
        self.assertTrue(all(path.startswith("/lib/firmware/mediatek/mt7986_") for path in blobs))
        self.assertTrue(all(len(digest) == 64 for digest in blobs.values()))
        board_text = BOARD.read_text()
        for path in blobs:
            self.assertIn(Path(path).name, board_text)
        self.assertIn("mt76-firmware.LICENSE", board_text)


if __name__ == "__main__":
    unittest.main()
