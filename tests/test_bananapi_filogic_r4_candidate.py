#!/usr/bin/env python3
"""Banana Pi R4 Filogic 候選映像與來源政策回歸測試。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7988-r4-current.json"
BOARD = ROOT / "config/boards/bananapir4.csc"
KERNEL_CONFIG = ROOT / "config/kernel/linux-filogic-current.config"
RUNNER = ROOT / "tools/run-bananapi-filogic-r4-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-filogic-r4-candidate.sh"


class BananaPiFilogicR4CandidateTests(unittest.TestCase):
    """防止 R4 啟動鏈、GPT、網路與韌體政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapir4"]

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

    def test_board_pins_sources_and_repairs_standard_boot(self) -> None:
        text = BOARD.read_text()
        for expected in (
            'KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"',
            'BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"',
            'ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"',
            "post_config_uboot_target__bananapir4_standard_boot",
            "scripts/config --disable CONFIG_AUTOBOOT_KEYED",
            "mediatek/mt7988a-bananapi-bpi-r4-sd.dtb",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn("post_family_tweaks__bpi-r4", text)

    def test_mt7996_and_mt7988_firmware_manifest_is_complete(self) -> None:
        blobs = self.config["installed_firmware_blobs"]
        mt7996 = [path for path in blobs if "/mediatek/mt7996/" in path]
        mt7988 = [path for path in blobs if "/mediatek/mt7988/" in path]
        self.assertEqual(len(mt7996), 11)
        self.assertEqual(len(mt7988), 2)
        self.assertTrue(all(len(digest) == 64 for digest in blobs.values()))
        board_text = BOARD.read_text()
        for path in mt7996 + mt7988:
            self.assertIn(Path(path).name, board_text)
        self.assertIn("mt76-firmware.LICENSE", board_text)

    def test_gpt_dtb_and_payload_contract_is_complete(self) -> None:
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
        self.assertEqual(
            self.policy["dtb_sha256"],
            "61e8b004222395dc8a3ca1dbd2c4f3957bd03e1fa54501d252d1d5eae52d6552",
        )

    def test_kernel_enables_r4_network_storage_and_io(self) -> None:
        text = KERNEL_CONFIG.read_text()
        for option, value in self.config["common_kernel_options"].items():
            with self.subTest(option=option):
                self.assertIn(f"{option}={value}", text)

    def test_dedicated_entrypoints_select_only_r4(self) -> None:
        runner_text = RUNNER.read_text()
        verifier_text = VERIFIER.read_text()
        for text in (runner_text, verifier_text):
            self.assertIn("bananapi-filogic-mt7988-r4-current.json", text)
            self.assertIn("bananapi-filogic-mt7988-r4-trixie-current-cli", text)
            self.assertIn('BOARDS="bananapir4"', text)
        self.assertIn("bananapi-filogic-r4-cache-overlay", runner_text)


if __name__ == "__main__":
    unittest.main()
