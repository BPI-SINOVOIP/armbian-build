#!/usr/bin/env python3
"""Banana Pi M6 固定來源、授權邊界與候選工具回歸測試。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapim6.wip"
FAMILY = ROOT / "config/sources/families/vs680.conf"
CONFIG = ROOT / "config/validation/bananapi-vs680-m6-legacy.json"
KERNEL_CONFIG = ROOT / "config/kernel/linux-vs680-legacy.config"
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/bananapim6-legacy"
    / "001-identify-bananapi-m6-and-retain-vs680-compatibility.patch"
)
UBOOT_PATCH = (
    ROOT
    / "patch/u-boot/legacy/u-boot-vs680-bananapim6"
    / "001-identify-bananapi-m6.patch"
)
SOURCE_NOTE = ROOT / "packages/blobs/vs680/SOURCE.zh-TW.md"
SOURCE_VERIFY = ROOT / "tools/verify-bananapi-vs680-m6-sources.sh"
COMPONENT_BUILD = ROOT / "tools/build-bananapi-vs680-m6-components.sh"
COMPONENT_VERIFY = ROOT / "tools/verify-bananapi-vs680-m6-components.sh"
COMPONENT_RUNNER = (
    ROOT / "tools/run-bananapi-vs680-m6-components-isolated-cache.sh"
)
CANDIDATE_BUILD = ROOT / "tools/build-bananapi-vs680-m6-candidate.sh"
CANDIDATE_RUNNER = (
    ROOT / "tools/run-bananapi-vs680-m6-candidate-isolated-cache.sh"
)
CANDIDATE_VERIFY = ROOT / "tools/verify-bananapi-vs680-m6-candidate.sh"


class BananaPiVs680M6CandidateTests(unittest.TestCase):
    """防止 M6 候選失去來源固定、板級身分或發布限制。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = BOARD.read_text(encoding="utf-8")
        cls.family = FAMILY.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapim6"]

    def test_board_stays_wip_and_policy_forbids_claims(self) -> None:
        self.assertTrue(BOARD.is_file())
        self.assertFalse((BOARD.parent / "bananapim6.conf").exists())
        self.assertEqual(self.config["current_evidence_level"], "L1")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertTrue(self.config["component_build_completed"])
        self.assertFalse(self.config["full_rootfs_image_built"])
        self.assertFalse(
            self.config["license_policy"][
                "opaque_payload_redistribution_verified"
            ]
        )

    def test_all_selectable_sources_are_fixed_commits(self) -> None:
        for component, source in self.config["source_commits"].items():
            with self.subTest(component=component):
                self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")
                self.assertEqual(source["ref"], f"commit:{source['revision']}")
        self.assertNotIn("branch:", self.board)
        self.assertIn(
            'BOOTPATCHDIR="legacy/u-boot-vs680-bananapim6"', self.board
        )
        self.assertIn(
            'KERNELPATCHDIR="archive/bananapim6-legacy"', self.board
        )

    def test_board_hook_overrides_family_moving_branches(self) -> None:
        script = f"""
set -euo pipefail
source {BOARD}
source {FAMILY}
post_family_config__bananapim6_pin_vendor_sources
printf '%s\n' "$BOOTBRANCH" "$KERNELBRANCH" \
    "$ARMBIAN_FIRMWARE_GIT_REF" "$IMAGE_PARTITION_TABLE" "$ATF_COMPILE"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "commit:ccca1c75bb6d06470b8a3f6104068b43763ee468",
                "commit:3229415e99a06edc972948c0a856cbcf7de7ce55",
                "commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
                "msdos",
                "no",
            ],
        )

    def test_exact_identity_is_added_and_inheritance_is_explicit(self) -> None:
        kernel_patch = KERNEL_PATCH.read_text(encoding="utf-8")
        uboot_patch = UBOOT_PATCH.read_text(encoding="utf-8")
        for patch in (kernel_patch, uboot_patch):
            self.assertIn('model = "Banana Pi M6";', patch)
            self.assertIn("sinovoip,bananapi-m6", patch)
        self.assertIn("syna,vs680-evk", kernel_patch)
        self.assertIn("Synaptics,asserial", uboot_patch)
        self.assertEqual(
            set(self.policy["inherited_compatibles"]),
            {
                "linux:syna,vs680-evk",
                "uboot:Synaptics,vs680",
                "uboot:Synaptics,asserial",
            },
        )

    def test_opaque_payloads_are_hashed_and_not_releaseable(self) -> None:
        tzk = ROOT / "packages/blobs/vs680/bpi-m6-tzk-4MB.bin"
        tzk_policy = self.config["opaque_boot_payloads"][
            "packages/blobs/vs680/bpi-m6-tzk-4MB.bin"
        ]
        self.assertEqual(tzk.stat().st_size, tzk_policy["size"])
        self.assertEqual(
            hashlib.sha256(tzk.read_bytes()).hexdigest(),
            tzk_policy["sha256"],
        )
        for payload in self.config["opaque_boot_payloads"].values():
            self.assertFalse(payload["source_available"])
            self.assertFalse(payload["rebuild_available"])
            self.assertFalse(payload["redistribution_license_verified"])
        self.assertIn("逐檔授權", SOURCE_NOTE.read_text(encoding="utf-8"))

    def test_boot_payload_overlap_and_partition_contract_are_explicit(self) -> None:
        overlap = self.policy["payload_overlap_policy"]
        self.assertTrue(overlap["allowed"])
        self.assertEqual(
            self.policy["payload_write_order"],
            ["bpi-m6-tzk-4MB.bin", "u-boot.bin"],
        )
        self.assertEqual(overlap["overlap_starts_at_image_offset"], 2097152)
        self.assertLess(
            overlap["overlap_starts_at_image_offset"],
            512
            + self.config["opaque_boot_payloads"][
                "packages/blobs/vs680/bpi-m6-tzk-4MB.bin"
            ]["size"],
        )
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertEqual(self.policy["partition_start_sector"], 204800)
        self.assertIn("bpi-m6-tzk-4MB.bin", self.family)

    def test_packages_and_kernel_options_match_contract(self) -> None:
        package_line = next(
            line
            for line in self.board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        board_packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= board_packages)
        kernel_config = KERNEL_CONFIG.read_text(encoding="utf-8")
        for option, value in self.config["common_kernel_options"].items():
            with self.subTest(option=option):
                self.assertIn(f"{option}={value}\n", kernel_config)

    def test_component_scope_excludes_rootfs_and_unavailable_atf(self) -> None:
        self.assertEqual(
            self.config["component_build_scope"],
            ["uboot", "linux-image", "linux-dtb"],
        )
        self.assertIn("atf", self.config["component_build_exclusions"])
        component = COMPONENT_BUILD.read_text(encoding="utf-8")
        self.assertIn("./compile.sh uboot", component)
        self.assertIn("./compile.sh kernel", component)
        self.assertNotIn("./compile.sh build", component)
        self.assertIn("不應建立完整 IMG", component)
        self.assertIn("COMPONENT_VERIFICATION.json", component)
        component_verify = COMPONENT_VERIFY.read_text(encoding="utf-8")
        self.assertIn("component_build_evidence", component_verify)
        self.assertIn("元件套件內容雜湊不符", component_verify)

    def test_component_evidence_locks_actual_outputs(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(
            evidence["source_commit"],
            "b6339cf4a2135e3ad75992f7574889d5ff34a249",
        )
        self.assertEqual(
            evidence["dtb_sha256"],
            "52c58e8a1413fd644b812480215350410659371083afa9930684df5752625413",
        )
        self.assertEqual(evidence["dtb_sha256"], self.policy["dtb_sha256"])
        self.assertEqual(
            evidence["uboot_sha256"],
            "4d8158b3ed44de9384fabb009a0639cbe2c83e964a32724b5c87ce9911f72bda",
        )
        self.assertIn(
            f"u-boot.bin={evidence['uboot_sha256']}",
            self.policy["uboot_payload_sha256"],
        )
        self.assertEqual(len(evidence["packages"]), 5)
        for digest in evidence["packages"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_build_and_read_only_verification_tools_are_separate(self) -> None:
        component_runner = COMPONENT_RUNNER.read_text(encoding="utf-8")
        candidate_runner = CANDIDATE_RUNNER.read_text(encoding="utf-8")
        candidate_build = CANDIDATE_BUILD.read_text(encoding="utf-8")
        verifier = CANDIDATE_VERIFY.read_text(encoding="utf-8")
        self.assertIn("build-bananapi-vs680-m6-components.sh", component_runner)
        self.assertIn("build-bananapi-vs680-m6-candidate.sh", candidate_runner)
        self.assertIn("bananapim6", candidate_build)
        for required in (
            "losetup --find --show --read-only --partscan",
            "mount -o ro,nosuid,nodev,noexec",
            "sfdisk --json",
            "payload_overlap_policy",
            "契約缺少 U-Boot 成品雜湊",
            "public_release_allowed\": false",
        ):
            with self.subTest(required=required):
                self.assertIn(required, verifier)

    def test_source_hashes_and_blockers_are_machine_readable(self) -> None:
        for relative, expected in self.config["source_file_sha256"].items():
            with self.subTest(relative=relative):
                actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)
        self.assertGreaterEqual(len(self.config["blockers"]), 5)
        self.assertEqual(
            self.config["official_evidence"]["armbian_historical_commit"],
            "9163a04ca984461bec2516e9be0acd8a990863b9",
        )
        self.assertGreaterEqual(len(self.config["local_evidence"]), 4)
        for evidence in self.config["local_evidence"]:
            self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")
        joined = "\n".join(self.config["blockers"])
        for required in ("TZK", "ATF", "DTS", "原理圖", "實機"):
            self.assertIn(required, joined)
        self.assertTrue(SOURCE_VERIFY.is_file())


if __name__ == "__main__":
    unittest.main()
