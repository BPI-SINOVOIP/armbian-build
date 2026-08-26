#!/usr/bin/env python3
"""Banana Pi BPI-CM6 固定來源與候選守門回歸測試。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapicm6.wip"
CONFIG = ROOT / "config/validation/bananapi-spacemit-k1-cm6-legacy.json"
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/bananapicm6-legacy"
    / "001-identify-bananapi-cm6-and-defer-bootargs.patch"
)
SOURCE_NOTE = ROOT / "packages/blobs/riscv64/spacemit/SOURCE.zh-TW.md"
GENERIC_VERIFY = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
RUNNER = ROOT / "tools/run-bananapi-spacemit-cm6-candidate-isolated-cache.sh"
VERIFY = ROOT / "tools/verify-bananapi-spacemit-cm6-candidate.sh"


class BananaPiSpacemitCm6CandidateTests(unittest.TestCase):
    """驗證 CM6 legacy 候選使用固定且可追溯的受控政策。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = BOARD.read_text()
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapicm6"]

    def test_board_pins_all_sources_and_real_patch_directory(self) -> None:
        self.assertIn('KERNEL_TEST_TARGET="legacy"', self.board)
        self.assertIn('IMAGE_PARTITION_TABLE="msdos"', self.board)
        self.assertNotIn("branch:v2022.10-k1-v2.1", self.board)
        self.assertNotIn("branch:linux-6.6.36-k1-cm6", self.board)
        for source in self.config["source_commits"].values():
            self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")
            self.assertIn(f'commit:{source["revision"]}', self.board)
        self.assertTrue(KERNEL_PATCH.is_file())
        self.assertIn(
            'KERNELPATCHDIR="archive/bananapicm6-legacy"', self.board
        )

    def test_policy_covers_packages_kernel_and_firmware_license(self) -> None:
        package_line = next(
            line
            for line in self.board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)
        self.assertEqual(
            self.config["common_kernel_options"]["CONFIG_RTL8852BS"], "m"
        )
        self.assertEqual(
            self.config["common_kernel_options"][
                "CONFIG_USB_CONFIGFS_MASS_STORAGE"
            ],
            "y",
        )
        source_hash = hashlib.sha256(SOURCE_NOTE.read_bytes()).hexdigest()
        self.assertEqual(
            self.config["installed_firmware_blobs"][
                "/usr/share/doc/armbian-bsp-bananapicm6/"
                "esos.elf.SOURCE.zh-TW.md"
            ],
            source_hash,
        )

    def test_boot_chain_and_storage_are_fully_guarded(self) -> None:
        written = {item.split("@", 1)[0] for item in self.policy["uboot_payloads"]}
        package_only = set(self.policy["uboot_package_only_payloads"])
        minimums = {
            item.split("=", 1)[0]
            for item in self.policy["uboot_payload_minimum_sizes"]
        }
        self.assertEqual(len(written), 4)
        self.assertEqual(
            package_only, {"bootinfo_spinor.bin", "u-boot-env-default.bin"}
        )
        self.assertEqual(written | package_only, minimums)
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertEqual(self.policy["partition_start_sector"], 8192)
        self.assertEqual(self.policy["boot_configuration"], "extlinux")
        self.assertIn(
            "product_name=k1-x_deb1",
            self.policy["uboot_required_binary_strings"],
        )

    def test_dtb_identity_and_vendor_bootargs_are_controlled(self) -> None:
        self.assertEqual(self.policy["model"], "BananaPi BPI-CM6")
        self.assertIn("bananapi,bpi-cm6", self.policy["compatible"])
        self.assertIn("rdinit=/init", self.policy["dtb_forbidden_binary_strings"])
        patch = KERNEL_PATCH.read_text()
        self.assertIn('model = "BananaPi BPI-CM6";', patch)
        self.assertIn('compatible = "bananapi,bpi-cm6", "spacemit,k1-x";', patch)
        self.assertIn("/delete-property/ bootargs", patch)

    def test_dedicated_tools_use_cm6_contract_and_isolated_cache(self) -> None:
        runner = RUNNER.read_text()
        verifier = VERIFY.read_text()
        for required in (
            "run-bananapi-candidates-isolated-cache.sh",
            "build-bananapi-spacemit-candidates.sh",
            "bananapi-spacemit-k1-cm6-legacy.json",
            "bananapi-spacemit-cm6-cache-overlay",
        ):
            self.assertIn(required, runner)
        self.assertIn("verify-bananapi-spacemit-candidates.sh", verifier)
        self.assertIn("bananapi-spacemit-k1-cm6-legacy.json", verifier)
        self.assertIn("uboot_required_binary_strings", GENERIC_VERIFY.read_text())


if __name__ == "__main__":
    unittest.main()
