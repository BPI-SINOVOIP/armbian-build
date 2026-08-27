#!/usr/bin/env python3
"""Banana Pi M6 固定來源、授權邊界與候選工具回歸測試。"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


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
GENERIC_VERIFY = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
GENERIC_BUILD = ROOT / "tools/build-bananapi-sunxi-candidates.sh"
POLICY_CHECK = ROOT / "tools/check-bananapi-vs680-m6-policy.py"
EVIDENCE_DOCUMENT = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-vs680-m6-source-policy-20260827.md"
)


class BananaPiVs680M6CandidateTests(unittest.TestCase):
    """防止 M6 候選失去來源固定、板級身分或發布限制。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = BOARD.read_text(encoding="utf-8")
        cls.family = FAMILY.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapim6"]
        spec = importlib.util.spec_from_file_location("m6_policy_checker", POLICY_CHECK)
        cls.policy_checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy_checker)

    def test_board_stays_wip_and_policy_forbids_claims(self) -> None:
        self.assertTrue(BOARD.is_file())
        self.assertFalse((BOARD.parent / "bananapim6.conf").exists())
        self.assertEqual(self.config["candidate_level"], "L1 元件候選")
        self.assertEqual(self.config["candidate_scope"], "internal-component-only")
        self.assertEqual(self.config["current_evidence_level"], "L1")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertTrue(self.config["component_build_completed"])
        self.assertFalse(self.config["rootfs_image_built"])
        self.assertFalse(self.config["full_image_built"])
        self.assertFalse(self.config["full_rootfs_image_built"])
        self.assertFalse(self.config["hardware_validation_complete"])
        self.assertFalse(self.config["candidate_public_release_approved"])
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
        self.assertTrue(self.config["verify_firmware_source_resolution"])
        self.assertIn(
            'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
            self.board,
        )
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
    "$ARMBIAN_FIRMWARE_GIT_SOURCE" "$ARMBIAN_FIRMWARE_GIT_REF" \
    "$IMAGE_PARTITION_TABLE" "$ATF_COMPILE"
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
                "https://github.com/armbian/firmware",
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
        self.assertEqual(self.policy["root_partition_start_sector"], 729088)
        self.assertEqual(
            self.policy["required_partitions"],
            ["1:*:204800:524288", "2:*:729088:*"],
        )
        self.assertEqual(self.policy["required_partition_types"], ["1:ea", "2:83"])
        self.assertEqual(self.policy["boot_partition_label"], "BPI-BOOT")
        self.assertEqual(self.policy["root_partition_label"], "BPI-ROOT")
        self.assertEqual(self.policy["boot_partition_filesystem_type"], "vfat")
        self.assertEqual(self.policy["root_partition_filesystem_type"], "ext4")
        self.assertEqual(self.policy["boot_configuration"], "separate_fat_armbian_env")
        self.assertEqual(
            self.policy["uboot_payload_sizes"],
            ["bpi-m6-tzk-4MB.bin=4193792", "u-boot.bin=616575"],
        )
        self.assertIn("bpi-m6-tzk-4MB.bin", self.family)
        self.assertLess(
            self.family.index('dd if="${tzk_payload}" of="$2" bs=512 seek=1'),
            self.family.index('dd if="$1/u-boot.bin" of="$2" bs=1k seek=2048'),
        )

    def test_final_configuration_and_boot_script_hashes_are_locked(self) -> None:
        self.assertEqual(
            self.policy["final_kernel_config_sha256"],
            "b67480db7854ea797a1813102b2ef1c7a1312c9291797912612368821b058786",
        )
        self.assertEqual(
            self.policy["final_uboot_config_sha256"],
            "f31af0f1449901eb3834fd17e9c8c69034bd50b126a29108168683ba6b38c1f6",
        )
        boot_script = ROOT / self.policy["boot_script_source"]
        self.assertEqual(
            hashlib.sha256(boot_script.read_bytes()).hexdigest(),
            self.policy["boot_script_source_sha256"],
        )

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
        self.assertEqual(
            evidence["dtb_sha256"], self.policy["component_dtb_sha256"]
        )
        self.assertIsNone(self.policy["image_dtb_sha256"])
        self.assertNotIn("dtb_sha256", self.policy)
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
        generic = GENERIC_VERIFY.read_text(encoding="utf-8")
        self.assertIn("build-bananapi-vs680-m6-components.sh", component_runner)
        self.assertIn("build-bananapi-vs680-m6-candidate.sh", candidate_runner)
        self.assertIn("ALLOW_INTERNAL_M6_CANDIDATE=yes", candidate_runner)
        self.assertIn("REQUIRE_ISOLATED_CACHE=yes", candidate_runner)
        self.assertIn('CACHE_TARGET="${repo_dir}/cache"', candidate_runner)
        self.assertIn(
            'CACHE_LOWER="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"',
            candidate_runner,
        )
        self.assertIn("bananapim6", candidate_build)
        self.assertIn("check-bananapi-vs680-m6-policy.py", candidate_build)
        self.assertIn("verify-bananapi-vs680-m6-sources.sh", candidate_build)
        self.assertIn('SOURCE_DATE_EPOCH="${expected_source_date_epoch}"', candidate_build)
        self.assertIn("REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", candidate_build)
        self.assertIn("validate_fixed_overlay_mount", candidate_build)
        self.assertIn("bananapi-vs680-m6-candidate-cache-overlay", candidate_build)
        self.assertIn("只允許固定輸出目錄", candidate_build)
        self.assertIn('[[ "${REQUIRE_ISOLATED_CACHE:-yes}" == yes ]]', candidate_build)
        self.assertIn("BPI-M6 建置不得停用 OverlayFS", candidate_build)
        self.assertIn('MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-40}"', candidate_build)
        self.assertIn("verify-bananapi-sunxi-candidates.sh", verifier)
        self.assertIn("check-bananapi-vs680-m6-policy.py", verifier)
        self.assertIn("verify-bananapi-vs680-m6-sources.sh", verifier)
        self.assertIn("VERIFY_ARCHIVES=yes", verifier)
        self.assertIn("REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", verifier)
        self.assertIn("REQUIRE_BUILD_VERIFIER_IDENTITY=yes", verifier)
        self.assertIn("VERIFICATION_DEFER_STATUS_PROMOTION=yes", verifier)
        self.assertIn("--phase calibration", verifier)
        self.assertIn("--finalize-calibration", verifier)
        self.assertIn("--phase material-evidence", verifier)
        self.assertIn("--finalize-material-status", verifier)
        self.assertIn('"${status_file}.partial"', verifier)
        self.assertNotIn("VALIDATION_CONFIG:-", verifier)
        self.assertIn("候選層級、範圍與證據等級不成對", verifier)
        self.assertIn(
            'export VERIFICATION_EVIDENCE_LEVEL="${verification_evidence_level}"',
            verifier,
        )
        self.assertNotIn('"evidence_level": "L2"', verifier)
        self.assertIn("禁止沿用舊成功狀態", verifier)
        for required in (
            '"public_release_allowed": False',
            '"hardware_claims_allowed": False',
            '"opaque_payload_redistribution_verified": False',
            "VERIFICATION_EXTRA_STATUS_JSON",
        ):
            self.assertIn(required, verifier)
        for required in (
            "losetup --find --show --partscan --read-only",
            "mount -o ro,noload,nosuid,nodev,noexec",
            "separate_fat_armbian_env",
            "image-controlled-overlap",
            "FINAL_CONFIG_EVIDENCE.tsv",
            "UBOOT_PAYLOAD_EVIDENCE.tsv",
        ):
            with self.subTest(required=required):
                self.assertIn(required, generic)

    def test_common_evidence_chain_locks_time_tree_completion_and_xz(self) -> None:
        builder = GENERIC_BUILD.read_text(encoding="utf-8")
        verifier = GENERIC_VERIFY.read_text(encoding="utf-8")
        for required in (
            "source_date_epoch",
            "建置期間來源提交已改變",
            "建置期間來源 tree 已改變",
            "建置期間 validation 已改變",
            "建立完成證據期間來源提交或 tree 已改變",
        ):
            with self.subTest(tool="builder", required=required):
                self.assertIn(required, builder)
        self.assertNotIn('source_date_epoch=""', builder)
        self.assertIn("build_evidence_level", builder)
        self.assertIn("current_evidence_level", builder)
        self.assertIn("evidence_level=%s", builder)
        for required in (
            "boot_partition_filesystem_type",
            "candidate_source_tree",
            "completion_status_sha256",
            "source_date_epoch",
            "xz_stream_verified",
            "L2 建置與驗證使用的 validation 雜湊不一致",
        ):
            with self.subTest(tool="verifier", required=required):
                self.assertIn(required, verifier)
        self.assertIn('"evidence_level ${verification_evidence_level}"', verifier)

    def run_policy(self, config: dict[str, object]) -> subprocess.CompletedProcess[str]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8"
        ) as stream:
            json.dump(config, stream, ensure_ascii=False)
            stream.flush()
            return subprocess.run(
                ["python3", str(POLICY_CHECK), stream.name],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def test_policy_accepts_current_l1_and_rejects_label_only_l2(self) -> None:
        accepted = subprocess.run(
            ["python3", str(POLICY_CHECK), str(CONFIG)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        mutated = json.loads(CONFIG.read_text(encoding="utf-8"))
        mutated["candidate_level"] = "L2 內部軟體候選"
        rejected = self.run_policy(mutated)
        self.assertNotEqual(rejected.returncode, 0)

    def test_policy_rejects_source_and_firmware_drift(self) -> None:
        for path, value in (
            (("linux_ref",), "commit:" + "0" * 40),
            (("source_commits", "uboot", "revision"), "1" * 40),
            (("firmware_commit",), "2" * 40),
            (("firmware_ref",), "commit:" + "3" * 40),
            (("source_commits", "firmware", "revision"), "4" * 40),
            (("verify_firmware_source_resolution",), False),
        ):
            mutated = json.loads(CONFIG.read_text(encoding="utf-8"))
            target = mutated
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path):
                self.assertNotEqual(self.run_policy(mutated).returncode, 0)

    def test_policy_rejects_any_release_hardware_or_opaque_claim(self) -> None:
        for path in (
            ("candidate_public_release_approved",),
            ("public_release_allowed",),
            ("hardware_validation_complete",),
            ("hardware_claims_allowed",),
            ("firmware_redistribution_license_verified",),
            ("license_policy", "opaque_payload_redistribution_verified"),
        ):
            mutated = copy.deepcopy(self.config)
            target = mutated
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = True
            with self.subTest(path=path):
                self.assertNotEqual(self.run_policy(mutated).returncode, 0)

    def test_build_entry_rejects_isolation_and_epoch_bypass_without_building(self) -> None:
        for overrides in (
            {"REQUIRE_ISOLATED_CACHE": "no"},
            {"SOURCE_DATE_EPOCH": "1717001895"},
            {"PUBLIC_RELEASE": "yes"},
            {"HARDWARE_CLAIMS": "yes"},
        ):
            environment = os.environ.copy()
            environment.update(
                {
                    "ALLOW_INTERNAL_M6_CANDIDATE": "yes",
                    "REQUIRE_ISOLATED_CACHE": "yes",
                }
            )
            environment.update(overrides)
            result = subprocess.run(
                [str(CANDIDATE_BUILD)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            with self.subTest(overrides=overrides):
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("完整建置", result.stdout + result.stderr)

    def test_policy_rejects_missing_or_wrong_filesystem_contract(self) -> None:
        for field, value in (
            ("boot_partition_filesystem_type", None),
            ("boot_partition_filesystem_type", "ext4"),
            ("root_partition_filesystem_type", None),
            ("root_partition_filesystem_type", "vfat"),
        ):
            mutated = json.loads(CONFIG.read_text(encoding="utf-8"))
            if value is None:
                del mutated["boards"]["bananapim6"][field]
            else:
                mutated["boards"]["bananapim6"][field] = value
            with self.subTest(field=field, value=value):
                self.assertNotEqual(self.run_policy(mutated).returncode, 0)

    def test_policy_rejects_boot_chain_and_payload_contract_drift(self) -> None:
        mutations = (
            ("source_date_epoch", 1717001895),
            ("final_kernel_config_sha256", "0" * 64),
            ("final_uboot_config_sha256", "1" * 64),
            ("required_partitions", ["1:*:204800:1", "2:*:204801:*"]),
            ("required_partition_types", ["1:83", "2:ea"]),
            ("boot_partition_label", "錯誤"),
            ("root_partition_label", "錯誤"),
            ("boot_configuration", "armbian_env"),
            ("boot_script_source_sha256", "2" * 64),
            ("payload_write_order", ["u-boot.bin", "bpi-m6-tzk-4MB.bin"]),
            ("uboot_payload_sizes", ["bpi-m6-tzk-4MB.bin=1", "u-boot.bin=2"]),
            ("uboot_payload_sha256", ["bpi-m6-tzk-4MB.bin=" + "3" * 64, "u-boot.bin=" + "4" * 64]),
        )
        for field, value in mutations:
            mutated = copy.deepcopy(self.config)
            if field == "source_date_epoch":
                mutated[field] = value
            else:
                mutated["boards"]["bananapim6"][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.run_policy(mutated).returncode, 0)

        for field, value in (
            ("overlap_starts_at_image_offset", 2097153),
            ("earlier_payload", "u-boot.bin"),
            ("later_payload", "bpi-m6-tzk-4MB.bin"),
            ("allowed", False),
        ):
            mutated = copy.deepcopy(self.config)
            mutated["boards"]["bananapim6"]["payload_overlap_policy"][field] = value
            with self.subTest(overlap_field=field):
                self.assertNotEqual(self.run_policy(mutated).returncode, 0)

    def test_policy_rejects_release_and_hardware_environments(self) -> None:
        for variable in ("PUBLIC_RELEASE", "HARDWARE_CLAIMS"):
            environment = os.environ.copy()
            environment[variable] = "yes"
            result = subprocess.run(
                ["python3", str(POLICY_CHECK), str(CONFIG)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            with self.subTest(variable=variable):
                self.assertNotEqual(result.returncode, 0)

    def test_l2_shape_requires_bound_tree_completion_and_xz_evidence(self) -> None:
        candidate = copy.deepcopy(self.config)
        candidate["candidate_level"] = "L2 內部軟體候選"
        candidate["candidate_scope"] = "internal-l2"
        candidate["current_evidence_level"] = "L2"
        candidate["rootfs_image_built"] = True
        candidate["full_image_built"] = True
        candidate["full_rootfs_image_built"] = True
        image_dtb = "a" * 64
        source_commit = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        source_tree = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", f"{source_commit}^{{tree}}"],
            text=True,
        ).strip()
        build_validation = hashlib.sha256(
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "show",
                    f"{source_commit}:config/validation/bananapi-vs680-m6-legacy.json",
                ]
            )
        ).hexdigest()
        candidate["image_build_evidence"] = {
            "status": "complete",
            "evidence_level": "L2",
            "read_only_content_verified": True,
            "hardware_tested": False,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "verifier_commit": source_commit,
            "source_date_epoch": 1717001894,
            "build_validation_config_sha256": build_validation,
            "verification_config_sha256": build_validation,
            "candidate_matrix_sha256": "4" * 64,
            "completion_status_sha256": "5" * 64,
            "verification_manifest_sha256": "a" * 64,
            "uboot_payload_manifest_sha256": "6" * 64,
            "final_config_manifest_sha256": "7" * 64,
            "source_contract_projection_sha256": self.policy_checker.contract_projection_sha256(candidate),
            "xz_stream_verified": True,
            "image": {"path": "bananapim6/test.img", "size": 1, "sha256": "8" * 64},
            "archive": {"path": "bananapim6/test.img.xz", "size": 1, "sha256": "9" * 64},
            "linux_dtb": {"sha256": image_dtb},
        }
        board = candidate["boards"]["bananapim6"]
        board["image_dtb_sha256"] = image_dtb
        board["dtb_sha256"] = image_dtb
        board["dtb_sha256_evidence_scope"] = "full-image-l2"
        status = {"evidence": {"bananapim6": {"level": "L2"}}}
        self.policy_checker.validate_candidate_state(candidate, status)

        for field, value in (
            ("source_tree", None),
            ("source_commit", "f" * 40),
            ("completion_status_sha256", None),
            ("source_date_epoch", 1),
            ("xz_stream_verified", False),
        ):
            broken = copy.deepcopy(candidate)
            if value is None:
                del broken["image_build_evidence"][field]
            else:
                broken["image_build_evidence"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_candidate_state(broken, status)

    def test_source_contract_allows_unbound_l2_calibration_contract(self) -> None:
        candidate = copy.deepcopy(self.config)
        candidate["candidate_level"] = "L2 內部軟體候選"
        candidate["candidate_scope"] = "internal-l2"
        candidate["current_evidence_level"] = "L2"
        status = {"evidence": {"bananapim6": {"level": "L1"}}}
        self.policy_checker.validate_candidate_state(
            candidate, status, require_material_binding=False
        )
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_candidate_state(candidate, status)

    def test_contract_projection_excludes_state_but_rejects_requirement_drift(self) -> None:
        baseline = self.policy_checker.contract_projection_sha256(self.config)
        self.assertEqual(
            self.config["source_contract_projection_sha256"], baseline
        )
        changed_state = copy.deepcopy(self.config)
        changed_state["candidate_level"] = "L2 內部軟體候選"
        changed_state["candidate_scope"] = "internal-l2"
        changed_state["current_evidence_level"] = "L2"
        changed_state["full_image_built"] = True
        changed_state["boards"]["bananapim6"]["image_dtb_sha256"] = "1" * 64
        self.assertEqual(
            self.policy_checker.contract_projection_sha256(changed_state), baseline
        )
        changed_requirement = copy.deepcopy(changed_state)
        changed_requirement["common_packages"].append("新增診斷套件")
        self.assertNotEqual(
            self.policy_checker.contract_projection_sha256(changed_requirement),
            baseline,
        )

    def test_policy_cli_requires_explicit_material_source_and_tracked_history(self) -> None:
        environment = os.environ.copy()
        environment["PUBLIC_RELEASE"] = "no"
        environment["HARDWARE_CLAIMS"] = "no"
        for arguments in (
            ["--phase", "material-evidence"],
            ["--phase", "calibration", "--evidence-source", "live"],
            ["--phase", "source-contract", "--evidence-source", "live"],
            ["--finalize-material-status"],
        ):
            result = subprocess.run(
                ["python3", str(POLICY_CHECK), *arguments],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            with self.subTest(arguments=arguments):
                self.assertNotEqual(result.returncode, 0)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8"
        ) as stream:
            json.dump(self.config, stream, ensure_ascii=False)
            stream.flush()
            result = subprocess.run(
                [
                    "python3",
                    str(POLICY_CHECK),
                    stream.name,
                    "--phase",
                    "material-evidence",
                    "--evidence-source",
                    "historical",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("版本控制內固定 M6 契約", result.stderr)

    def test_xz_stream_must_decode_to_exact_image(self) -> None:
        content = (b"BPI-M6-material\0" * 4096) + "完成".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "candidate.img"
            archive = Path(directory) / "candidate.img.xz"
            image.write_bytes(content)
            archive.write_bytes(lzma.compress(content))
            evidence = {"image": {"sha256": hashlib.sha256(content).hexdigest()}}
            self.policy_checker.validate_xz_stream_matches_image(
                image, archive, evidence
            )
            archive.write_bytes(lzma.compress(content + b"drift"))
            with self.assertRaises(SystemExit):
                self.policy_checker.validate_xz_stream_matches_image(
                    image, archive, evidence
                )

    def test_dual_partition_contract_rejects_layout_drift(self) -> None:
        table = {
            "partitiontable": {
                "label": "dos",
                "unit": "sectors",
                "sectorsize": 512,
                "partitions": [
                    {"start": 204800, "size": 524288, "type": "ea"},
                    {"start": 729088, "size": 1000000, "type": "83"},
                ],
            }
        }
        with (
            mock.patch.object(self.policy_checker.shutil, "which", return_value="/usr/bin/sfdisk"),
            mock.patch.object(
                self.policy_checker.subprocess,
                "check_output",
                side_effect=lambda *args, **kwargs: json.dumps(table),
            ),
        ):
            summary = self.policy_checker.validate_dual_partition_contract(
                self.config, Path("candidate.img")
            )
            self.assertEqual(len(summary["partitions"]), 2)
            table["partitiontable"]["partitions"][1]["start"] += 1
            with self.assertRaises(SystemExit):
                self.policy_checker.validate_dual_partition_contract(
                    self.config, Path("candidate.img")
                )

    def test_payload_overlap_rechecks_tzk_prefix_uboot_and_tzk_tail(self) -> None:
        policy = copy.deepcopy(self.config)
        board = policy["boards"]["bananapim6"]
        tzk = b"0123456789ABCDEFGHIJ"
        uboot = b"boot"
        board["uboot_payloads"] = ["tzk.bin@4", "u-boot.bin@12"]
        board["payload_write_order"] = ["tzk.bin", "u-boot.bin"]
        board["payload_overlap_policy"] = {
            "allowed": True,
            "earlier_payload": "tzk.bin",
            "later_payload": "u-boot.bin",
            "overlap_starts_at_image_offset": 12,
        }
        board["uboot_payload_sizes"] = ["tzk.bin=20", "u-boot.bin=4"]
        board["uboot_payload_sha256"] = [
            f"tzk.bin={hashlib.sha256(tzk).hexdigest()}",
            f"u-boot.bin={hashlib.sha256(uboot).hexdigest()}",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tzk_path = root / "tzk.bin"
            image = root / "candidate.img"
            manifest = root / "UBOOT_PAYLOAD_EVIDENCE.tsv"
            tzk_path.write_bytes(tzk)
            image_data = bytearray(32)
            image_data[4:24] = tzk
            image_data[12:16] = uboot
            image.write_bytes(image_data)
            manifest.write_text(
                "board\tpayload\tplacement\toffset\tsize\tsha256\n"
                f"bananapim6\ttzk.bin\timage-controlled-overlap\t4\t20\t{hashlib.sha256(tzk).hexdigest()}\n"
                f"bananapim6\tu-boot.bin\timage-controlled-overlap\t12\t4\t{hashlib.sha256(uboot).hexdigest()}\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(self.policy_checker, "TZK_SOURCE", tzk_path),
                mock.patch.object(
                    self.policy_checker, "UBOOT_PAYLOAD_EVIDENCE", manifest
                ),
            ):
                summary = self.policy_checker.validate_payload_overlap_manifest(
                    policy, image
                )
                self.assertEqual(summary["tail_size"], 8)
                image_data[20] ^= 0x01
                image.write_bytes(image_data)
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_payload_overlap_manifest(
                        policy, image
                    )

    def test_live_material_loader_rebuilds_evidence_from_current_outputs(self) -> None:
        (ROOT / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory)
            board_output = output / "bananapim6"
            board_output.mkdir()
            image = board_output / "candidate.img"
            archive = board_output / "candidate.img.xz"
            image.write_bytes(b"M6 image")
            archive.write_bytes(lzma.compress(image.read_bytes()))
            source_commit = "1" * 40
            source_tree = "2" * 40
            validation_hash = "3" * 64
            matrix = output / "CANDIDATES.tsv"
            matrix.write_text(
                "board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_path\txz_path\tsource_commit\tuboot_tag\n"
                f"bananapim6\ttrixie\tcli\t{image.stat().st_size}\t{hashlib.sha256(image.read_bytes()).hexdigest()}\t"
                f"{archive.stat().st_size}\t{hashlib.sha256(archive.read_bytes()).hexdigest()}\t"
                f"bananapim6/{image.name}\tbananapim6/{archive.name}\t{source_commit}\tv2019.10\n",
                encoding="utf-8",
            )
            completion = output / "COMPLETION_STATUS.json"
            projection = self.policy_checker.contract_projection_sha256(self.config)
            completion.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "source_tree": source_tree,
                        "validation_config_sha256": validation_hash,
                        "source_contract_projection_sha256": projection,
                    }
                ),
                encoding="utf-8",
            )
            verification = output / "VERIFICATION_STATUS.json"
            verification.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "evidence_level": "L2",
                        "verifier_commit": source_commit,
                        "verification_config_sha256": validation_hash,
                        "source_contract_projection_sha256": projection,
                        "xz_stream_verified": True,
                        "verified_utc": "2026-08-28T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            verification_manifest = output / "VERIFICATION.tsv"
            verification_manifest.write_text(
                "board\tidentity\tread_only_content\tevidence_level\n"
                "bananapim6\tpass\tpass\tL2\n",
                encoding="utf-8",
            )
            payload_manifest = output / "UBOOT_PAYLOAD_EVIDENCE.tsv"
            payload_manifest.write_text("物質載荷\n", encoding="utf-8")
            final_manifest = output / "FINAL_CONFIG_EVIDENCE.tsv"
            final_manifest.write_text(
                "board\tcomponent\tpath\tsha256\n"
                f"bananapim6\tkernel\tboot/config-test\t{self.policy_checker.FINAL_KERNEL_CONFIG}\n"
                f"bananapim6\tuboot\tusr/lib/u-boot-config-target-1\t{self.policy_checker.FINAL_UBOOT_CONFIG}\n",
                encoding="utf-8",
            )
            inspection = {
                "linux_dtb": {
                    "path": self.policy["dtb"],
                    "sha256": "4" * 64,
                }
            }
            with mock.patch.multiple(
                self.policy_checker,
                OUTPUT_DIR=output,
                MATRIX=matrix,
                COMPLETION_STATUS=completion,
                VERIFICATION_STATUS=verification,
                VERIFICATION_MANIFEST=verification_manifest,
                UBOOT_PAYLOAD_EVIDENCE=payload_manifest,
                FINAL_CONFIG_EVIDENCE=final_manifest,
                METADATA=board_output / "artifact.metadata.txt",
            ), mock.patch.object(
                self.policy_checker,
                "inspect_read_only_image",
                return_value=inspection,
            ), mock.patch.object(
                self.policy_checker, "validate_artifact_metadata"
            ):
                evidence = self.policy_checker.load_live_material_evidence(
                    self.config
                )
            self.assertEqual(evidence["source_commit"], source_commit)
            self.assertEqual(evidence["linux_dtb"], inspection["linux_dtb"])
            self.assertEqual(
                evidence["candidate_matrix_sha256"],
                hashlib.sha256(matrix.read_bytes()).hexdigest(),
            )

    def test_material_completion_uses_atomic_files_and_second_readback(self) -> None:
        record = {
            "source_commit": "1" * 40,
            "verifier_commit": "1" * 40,
            "source_contract_projection_sha256": "2" * 64,
            "source_date_epoch": 1717001894,
            "common_verification_status_sha256": "3" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = output / "M6_MATERIAL_EVIDENCE.json"
            status = output / "M6_MATERIAL_STATUS.json"
            with mock.patch.multiple(
                self.policy_checker,
                OUTPUT_DIR=output,
                MATERIAL_EVIDENCE=evidence,
                MATERIAL_STATUS=status,
            ):
                self.policy_checker.write_material_completion(record)
                self.policy_checker.validate_material_completion(record)
                self.assertFalse(Path(f"{evidence}.partial").exists())
                self.assertFalse(Path(f"{status}.partial").exists())
                stale = json.loads(status.read_text(encoding="utf-8"))
                stale["common_verification_status_sha256"] = "4" * 64
                status.write_text(
                    json.dumps(stale, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_material_completion(record)

    def test_calibration_completion_is_atomic_and_reparsed(self) -> None:
        record = {
            "schema_version": 1,
            "status": "calibration_complete",
            "evidence_level": "L1",
        }
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "M6_CALIBRATION.json"
            with mock.patch.object(
                self.policy_checker, "CALIBRATION_EVIDENCE", evidence
            ):
                self.policy_checker.write_calibration_completion(record)
            self.assertEqual(
                json.loads(evidence.read_text(encoding="utf-8")), record
            )
            self.assertFalse(Path(f"{evidence}.partial").exists())

    def test_material_artifact_path_cannot_escape_fixed_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with mock.patch.object(self.policy_checker, "OUTPUT_DIR", output):
                with self.assertRaises(SystemExit):
                    self.policy_checker.resolve_matrix_artifact(
                        "bananapim6/../escape.img", ".img", "L2 IMG"
                    )

    def test_m6_verifier_rejects_external_output_without_altering_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            status = output / "VERIFICATION_STATUS.json"
            status.write_text(
                json.dumps({"status": "complete", "evidence_level": "L2"}),
                encoding="utf-8",
            )
            stale_files = (
                output / "VERIFICATION.tsv",
                output / "UBOOT_PAYLOAD_EVIDENCE.tsv",
                output / "FINAL_CONFIG_EVIDENCE.tsv",
            )
            for stale in stale_files:
                stale.write_text("舊證據\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output)
            result = subprocess.run(
                [str(CANDIDATE_VERIFY)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            state = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "complete")
            self.assertEqual(state["evidence_level"], "L2")
            for stale in stale_files:
                self.assertTrue(stale.exists())

    def test_m6_verifier_ignores_external_validation_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            status = output / "VERIFICATION_STATUS.json"
            validation = output / "m6-l2-validation.json"
            mutated = json.loads(CONFIG.read_text(encoding="utf-8"))
            mutated["candidate_level"] = "損壞的外部狀態"
            validation.write_text(
                json.dumps(mutated, ensure_ascii=False),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output)
            environment["VALIDATION_CONFIG"] = str(validation)
            result = subprocess.run(
                [str(CANDIDATE_VERIFY)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(status.exists())

    def test_evidence_document_keeps_internal_l1_and_hardware_limits(self) -> None:
        text = EVIDENCE_DOCUMENT.read_text(encoding="utf-8")
        for expected in (
            "現階段證據等級為 L1",
            "不產生實機支援聲明",
            "不得公開發布完整映像",
            "第一次完整映像預檢必須能連線",
            "TZK 與 U-Boot `sm.bin` 缺少原始碼",
            "尚未以本候選完成 SD/eMMC",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

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

    def test_source_patch_checks_use_fixed_commit_temporary_index(self) -> None:
        verifier = SOURCE_VERIFY.read_text(encoding="utf-8")
        self.assertIn("verify_patch_against_commit", verifier)
        self.assertIn('GIT_INDEX_FILE="${temporary_index}"', verifier)
        self.assertIn('read-tree "${revision}"', verifier)
        self.assertIn("apply --cached --check", verifier)
        self.assertNotIn('git -C "${linux_tree}" apply --check', verifier)


if __name__ == "__main__":
    unittest.main()
