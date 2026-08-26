#!/usr/bin/env python3
"""Banana Pi SpacemiT 候選映像工具回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-spacemit-k1-f3-current.json"
BUILD_SCRIPT = ROOT / "tools/build-bananapi-spacemit-candidates.sh"
VERIFY_SCRIPT = ROOT / "tools/verify-bananapi-spacemit-candidates.sh"
GENERIC_VERIFY = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"


class BananaPiSpacemitCandidateToolTests(unittest.TestCase):
    """驗證 F3 啟動鏈、來源證據與唯讀守門。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())

    def test_source_policy_has_worktrees_and_exact_revisions(self) -> None:
        sources = self.config["source_commits"]
        self.assertEqual(set(sources), {"linux", "opensbi", "uboot"})
        for name, source in sources.items():
            with self.subTest(name=name):
                self.assertEqual(source["ref"], f"commit:{source['revision']}")
                self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")
                self.assertTrue(source["worktree"].startswith("cache/sources/"))

    def test_f3_policy_covers_extlinux_and_storage_properties(self) -> None:
        policy = self.config["boards"]["bananapif3"]
        self.assertEqual(policy["boot_configuration"], "extlinux")
        self.assertEqual(policy["partition_start_sector"], 8192)
        self.assertEqual(
            policy["extlinux_fdt"],
            "/boot/dtb/spacemit/k1-bananapi-f3.dtb",
        )
        self.assertIn(
            "/soc/sdh@d4281000:mmc-hs400-enhanced-strobe",
            policy["required_boolean_properties"],
        )
        self.assertIn(
            "/soc/usb3@0/dwc3@c0a00000:dr_mode=host",
            policy["required_string_properties"],
        )

    def test_all_six_uboot_payloads_are_guarded(self) -> None:
        policy = self.config["boards"]["bananapif3"]
        written = {item.split("@", 1)[0] for item in policy["uboot_payloads"]}
        package_only = set(policy["uboot_package_only_payloads"])
        minimums = {
            item.split("=", 1)[0]
            for item in policy["uboot_payload_minimum_sizes"]
        }
        self.assertEqual(len(written), 4)
        self.assertEqual(
            package_only,
            {"bootinfo_spinor.bin", "u-boot-env-default.bin"},
        )
        self.assertEqual(written | package_only, minimums)

    def test_generic_verifier_supports_spacemit_rules(self) -> None:
        text = GENERIC_VERIFY.read_text()
        for required in (
            "uboot_package_only_payloads",
            "uboot_payload_minimum_sizes",
            "partition_start_sector",
            "boot_configuration",
            "extlinux_fdt",
            "required_boolean_properties",
            "required_string_properties",
            "installed_firmware_blobs",
            "UBOOT_PAYLOAD_EVIDENCE.tsv",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("payload_size > 32768", text)
        self.assertIn("checked_minimum=1", text)

    def test_partition_start_check_uses_portable_sysfs_interface(self) -> None:
        text = GENERIC_VERIFY.read_text()
        self.assertIn("read_partition_start_sector", text)
        self.assertIn('/sys/class/block/${block_name}/start', text)
        self.assertNotIn("lsblk -nrno START", text)

    def test_wrappers_preserve_source_evidence(self) -> None:
        build_text = BUILD_SCRIPT.read_text()
        verify_text = VERIFY_SCRIPT.read_text()
        for required in (
            "SPACEMIT_SOURCE_EVIDENCE.tsv",
            "SPACEMIT_SOURCE_STATUS.json",
            'git -C "${worktree_path}" rev-parse HEAD',
            "firmware_blobs",
            "manifest_sha256",
        ):
            with self.subTest(required=required):
                self.assertIn(required, build_text)
        for required in (
            "candidate_commit",
            "build_config_sha256",
            "cmp --silent",
            "spacemit_source_manifest_sha256",
            "source_revisions",
            "GENERIC_CANDIDATE_VERIFIER",
        ):
            with self.subTest(required=required):
                self.assertIn(required, verify_text)


if __name__ == "__main__":
    unittest.main()
