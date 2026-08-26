#!/usr/bin/env python3
"""Banana Pi R64 Filogic 候選映像與來源政策回歸測試。"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7622-r64-current.json"
BOARD = ROOT / "config/boards/bananapir64.csc"
OLD_BOARD = ROOT / "config/boards/bananapir64.wip"
KERNEL_CONFIG = ROOT / "config/kernel/linux-filogic-current.config"
RUNNER = ROOT / "tools/run-bananapi-filogic-r64-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-filogic-r64-candidate.sh"
MT7622_FIRMWARE = ROOT / "packages/blobs/filogic/firmware/mediatek/mt7622"


class BananaPiFilogicR64CandidateTests(unittest.TestCase):
    """防止 R64 啟動鏈、GPT、網路、儲存與韌體政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapir64"]

    def test_board_is_promoted_and_sources_are_pinned(self) -> None:
        self.assertTrue(BOARD.is_file())
        self.assertFalse(OLD_BOARD.exists())
        text = BOARD.read_text()
        for expected in (
            'KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"',
            'BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"',
            'ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'LINUX_FIRMWARE_GIT_REF_BOARD="commit:01205307636157a12c29e6a774bf83b218732050"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_uboot_uses_standard_boot_and_protected_redundant_environment(self) -> None:
        text = BOARD.read_text()
        for expected in (
            "post_config_uboot_target__bananapir64_standard_boot",
            "mediatek/mt7622-bananapi-bpi-r64.dtb",
            "CONFIG_ENV_IS_IN_MMC",
            "CONFIG_SYS_REDUNDAND_ENVIRONMENT",
            "CONFIG_ENV_MMC_PARTITION ubootenv",
            "CONFIG_ENV_SIZE 0x40000",
            "CONFIG_ENV_OFFSET 0x400000",
            "CONFIG_ENV_OFFSET_REDUND 0x440000",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        required = self.policy["uboot_required_config_options"]
        self.assertIn('CONFIG_DEFAULT_FDT_FILE="mediatek/mt7622-bananapi-bpi-r64.dtb"', required)
        self.assertIn('CONFIG_ENV_MMC_PARTITION="ubootenv"', required)
        self.assertIn("CONFIG_SYS_REDUNDAND_ENVIRONMENT=y", required)

    def test_mt7622_firmware_matches_fixed_linux_firmware(self) -> None:
        expected = {
            "mt7622pr2h.bin": "48c919e6ea243485f5092e63fd5558d03a5b9075e79c14447e3705ca42c14b53",
            "mt7622_n9.bin": "f1b21fced7344006e029b291ed1edacddd41eaf2571c7a31e2207903ddd111a3",
            "mt7622_rom_patch.bin": "b7ad5bab333b2dffe31dcb4cc911a15060ee16f661de38139e66f0804a74ba26",
            "LICENCE.mediatek": "a90d3f66704d85889945fec5525ea77622549da83aced1aac99828383f8f1805",
            "SOURCE.md": "96887e12198b03b03957abdad8e8ed8df6a88b72bd049d60c66b5283798c19bc",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256((MT7622_FIRMWARE / filename).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)
        self.assertEqual(
            self.config["linux_firmware_commit"],
            "01205307636157a12c29e6a774bf83b218732050",
        )

    def test_kernel_enables_mt7622_network_storage_and_io(self) -> None:
        text = KERNEL_CONFIG.read_text()
        for option, value in self.config["common_kernel_options"].items():
            with self.subTest(option=option):
                self.assertIn(f"{option}={value}", text)

    def test_gpt_dtb_and_dual_mmc_contract_is_complete(self) -> None:
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
        self.assertEqual(self.policy["sd_node"], "/mmc@11240000")
        self.assertIn("/mmc@11230000=8", self.policy["additional_bus_widths"])
        self.assertIn(
            "/mmc@11230000:non-removable",
            self.policy["required_boolean_properties"],
        )
        self.assertEqual(
            self.policy["dtb_sha256"],
            "4ec43868bd7ff3965b60410631ba6b8fd8c87a23647110fd7b647201953353ab",
        )

    def test_switch_ports_and_sata_pcie_limit_are_explicit(self) -> None:
        present = self.policy["required_present_nodes"]
        self.assertEqual(len([node for node in present if "/ports/port@" in node]), 7)
        self.assertIn("/sata@1a200000", self.policy["required_status_nodes"])
        self.assertIn("/pcie@1a145000", self.policy["required_status_nodes"])
        self.assertEqual(
            self.policy["hardware_mux_limitations"],
            ["GPIO90 在第二組 PCIe 與 SATA 間切換，兩者不得宣稱可同時使用"],
        )

    def test_dedicated_entrypoints_select_only_r64(self) -> None:
        runner_text = RUNNER.read_text()
        verifier_text = VERIFIER.read_text()
        for text in (runner_text, verifier_text):
            self.assertIn("bananapi-filogic-mt7622-r64-current.json", text)
            self.assertIn("bananapi-filogic-mt7622-r64-trixie-current-cli", text)
            self.assertIn('BOARDS="bananapir64"', text)
        self.assertIn("bananapi-filogic-r64-cache-overlay", runner_text)


if __name__ == "__main__":
    unittest.main()
