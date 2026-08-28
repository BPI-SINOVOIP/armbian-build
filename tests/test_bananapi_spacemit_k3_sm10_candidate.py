#!/usr/bin/env python3
"""Banana Pi SM10 固定來源與候選邊界回歸測試。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapism10.wip"
FAMILY = ROOT / "config/sources/families/spacemit-k3-bpi.conf"
CONFIG = ROOT / "config/validation/bananapi-spacemit-k3-sm10-current.json"
POLICY = ROOT / "tools/check-bananapi-spacemit-k3-sm10-policy.py"
SOURCE_VERIFY = ROOT / "tools/verify-bananapi-spacemit-k3-sm10-sources.sh"
COMPONENT_BUILD = ROOT / "tools/build-bananapi-spacemit-k3-sm10-components.sh"
COMPONENT_VERIFY = ROOT / "tools/verify-bananapi-spacemit-k3-sm10-components.sh"
IMAGE_BUILD = ROOT / "tools/build-bananapi-spacemit-k3-sm10-candidate.sh"
IMAGE_RUNNER = (
    ROOT / "tools/run-bananapi-spacemit-k3-sm10-candidate-isolated-cache.sh"
)
IMAGE_VERIFY = ROOT / "tools/verify-bananapi-spacemit-k3-sm10-candidate.sh"
GENERIC_IMAGE_VERIFY = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
IMAGE_FINALIZER = (
    ROOT / "tools/finalize-bananapi-spacemit-k3-sm10-verification.py"
)
LINUX_DTS = (
    ROOT
    / "patch/kernel/archive/spacemit-k3-bpi-6.18/dt/"
    "k3-bananapi-sm10.dts"
)
DT_MAKEFILE = LINUX_DTS.with_name("Makefile")
BOOT_ENV = ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env_k3.txt"
POLICY_DOC = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/"
    "E-spacemit-k3-sm10-source-policy-20260827.md"
)


class BananaPiSpacemitK3Sm10CandidateTests(unittest.TestCase):
    """防止 SM10 來源、拓撲、授權與發布政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.board = cls.config["boards"]["bananapism10"]
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.family_text = FAMILY.read_text(encoding="utf-8")

    def test_manifest_and_all_projects_are_exactly_pinned(self) -> None:
        sdk = self.config["sdk"]
        self.assertEqual(
            sdk["manifest_commit"],
            "6d767b42fdbd759dc9511b8a13523c3de42aaa5a",
        )
        self.assertEqual(sdk["project_count"], 20)
        self.assertEqual(len(self.config["source_commits"]), 20)
        for path, revision in self.config["source_commits"].items():
            with self.subTest(path=path):
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_core_components_match_vendor_manifest(self) -> None:
        expected = {
            "linux": "27275ec8240cc49af3a525b8bc325d9b5029fb81",
            "uboot": "1b10c8119e1a9b5451a4236f6b384f7c91eed1e2",
            "opensbi": "3e2f9efc9660b8d5fcae4e0b6495f306d5c64078",
            "esos": "92a8baf250e42853a094a7af6f7ee849adb3de4a",
        }
        for name, revision in expected.items():
            with self.subTest(name=name):
                source = self.config["component_sources"][name]
                self.assertEqual(source["revision"], revision)
                self.assertEqual(source["ref"], f"commit:{revision}")
                self.assertIn(revision, self.board_text)

    def test_armbian_firmware_is_applied_as_an_exact_commit(self) -> None:
        revision = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
        self.assertIn(
            'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
            self.board_text,
        )
        self.assertIn(
            f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{revision}"',
            self.board_text,
        )
        self.assertIn(
            'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
            self.board_text,
        )
        self.assertIn(
            'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
            self.board_text,
        )

    def test_candidate_remains_wip_and_blocks_unsupported_claims(self) -> None:
        self.assertTrue(BOARD.name.endswith(".wip"))
        self.assertEqual(
            self.config["candidate_level"], "L1 完整映像校準候選"
        )
        self.assertEqual(self.config["candidate_scope"], "internal-l1-calibration")
        self.assertEqual(self.config["current_evidence_level"], "L1")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertTrue(self.config["component_build_completed"])
        self.assertFalse(self.config["full_image_built"])
        self.assertFalse(self.config["rootfs_image_built"])
        self.assertFalse(self.config["full_rootfs_image_built"])
        for field in (
            "public_release_allowed",
            "public_distribution_approved",
            "hardware_claims_allowed",
            "secure_boot_claim_allowed",
        ):
            with self.subTest(field=field):
                self.assertIs(self.config[field], False)
        self.assertGreaterEqual(len(self.config["public_distribution_blockers"]), 6)
        self.assertFalse(self.board["topology_equivalence_verified"])
        self.assertFalse(self.board["uboot_control_dtb_identity_is_bananapi_specific"])
        self.assertEqual(self.config["candidate_boot_media"], ["sd"])
        self.assertEqual(self.config["supported_boot_media"], [])

    def test_linux_identity_is_dedicated_but_topology_is_conservative(self) -> None:
        text = LINUX_DTS.read_text(encoding="utf-8")
        self.assertIn('#include "k3_com260.dts"', text)
        self.assertIn('model = "BananaPi BPI-SM10";', text)
        self.assertIn(
            'compatible = "bananapi,bpi-sm10", "spacemit,k3-com260";',
            text,
        )
        self.assertIn("k3-bananapi-sm10.dtb", DT_MAKEFILE.read_text())
        self.assertEqual(self.board["uboot_control_dtb"], "k3_com260.dtb")
        self.assertIn('BOOT_FDT_FILE="spacemit/k3-bananapi-sm10.dtb"', self.board_text)

    def test_boot_layout_and_environment_match_vendor_contract(self) -> None:
        self.assertEqual(self.board["partition_table"], "gpt")
        self.assertEqual(self.board["boot_partition_start_sector"], 24576)
        self.assertEqual(self.board["partition_start_sector"], 24576)
        self.assertEqual(self.board["root_partition_start_sector"], 548864)
        self.assertEqual(
            self.board["required_partitions"],
            ["1:bootfs:24576:*", "2:rootfs:548864:*"],
        )
        self.assertEqual(self.board["boot_configuration"], "env_k3")
        self.assertEqual(
            self.board["uboot_payloads"],
            [
                "env.bin@655360",
                "bootinfo_block.bin@1048576",
                "FSBL.bin@1572864",
                "esos.itb@4194304",
                "fw_dynamic.itb@7340032",
                "u-boot.itb@8388608",
            ],
        )
        env = BOOT_ENV.read_text(encoding="utf-8")
        for key, value in self.board["boot_environment"].items():
            self.assertIn(f"{key}={value}\n", env)

    def test_all_vendored_boot_files_have_fixed_hashes(self) -> None:
        hashes = self.config["bootloader_blobs"]
        blob_directory = ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10"
        self.assertEqual(len(hashes), 11)
        self.assertEqual(
            set(hashes),
            {
                str(path.relative_to(ROOT))
                for path in blob_directory.iterdir()
                if path.is_file()
            },
        )
        for relative, expected in hashes.items():
            with self.subTest(relative=relative):
                actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)

    def test_source_built_and_prebuilt_assets_are_separated(self) -> None:
        source_built = self.config["source_built_boot_assets"]
        prebuilt = self.config["runtime_prebuilt_assets"]
        self.assertEqual(
            {Path(path).name for path in source_built},
            {
                "env.bin",
                "bootinfo_block.bin",
                "FSBL.bin",
                "fw_dynamic.itb",
                "u-boot.itb",
                "uboot.config",
                "u-boot-env-default.bin",
            },
        )
        self.assertEqual(len(prebuilt), 3)
        self.assertIn(
            "packages/blobs/riscv64/spacemit-k3/bpi-sm10/bianbu.bmp",
            prebuilt,
        )
        for relative, asset in source_built.items():
            with self.subTest(relative=relative):
                self.assertGreaterEqual(asset["reproducible_rebuild_count"], 2)
                self.assertEqual(
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                    asset["sha256"],
                )
        for asset in prebuilt.values():
            self.assertFalse(asset["source_build_available"])
            self.assertFalse(asset["redistribution_license_verified"])
        env_path = (
            ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env.bin"
        )
        self.assertEqual(
            env_path.read_bytes(),
            env_path.with_name("u-boot-env-default.bin").read_bytes(),
        )

    def test_source_built_uboot_contains_required_boot_contract(self) -> None:
        binary = (
            ROOT
            / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/u-boot.itb"
        ).read_bytes()
        for fragment in self.board["uboot_required_binary_strings"]:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment.encode(), binary)

    def test_riscv_uses_opensbi_instead_of_trusted_firmware_a(self) -> None:
        atf = self.config["trusted_firmware_a"]
        self.assertIs(atf["applicable"], False)
        self.assertIn("OpenSBI", atf["replacement_stage"])
        self.assertTrue(
            any("fw_dynamic.itb" in stage for stage in self.config["boot_chain"])
        )

    def test_private_keys_and_incomplete_firmware_licenses_stay_blocked(self) -> None:
        self.assertGreaterEqual(len(self.config["private_signing_keys_in_sdk"]), 6)
        self.assertEqual(
            self.config["license_status"]["vpu_firmware"],
            "逐檔沒有可確認授權",
        )
        self.assertIn("樣板", self.config["license_status"]["powervr"])
        policy_text = POLICY_DOC.read_text(encoding="utf-8")
        for phrase in ("公開發布前", "私鑰", "沒有可確認的授權", "實機"):
            self.assertIn(phrase, policy_text)

    def test_policy_checker_rejects_false_public_approval(self) -> None:
        passed = subprocess.run(
            [str(POLICY), str(CONFIG)], text=True, capture_output=True, check=False
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)

        mutated = json.loads(json.dumps(self.config))
        mutated["public_distribution_approved"] = True
        with tempfile.TemporaryDirectory(prefix="sm10-policy-") as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(
                json.dumps(mutated, ensure_ascii=False), encoding="utf-8"
            )
            rejected = subprocess.run(
                [str(POLICY), str(path)], text=True, capture_output=True, check=False
            )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("來源契約投影雜湊不符", rejected.stderr)

    def test_source_contract_projection_is_machine_reproducible(self) -> None:
        projection = subprocess.run(
            [str(POLICY), "--print-source-contract-projection-sha256"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(projection.returncode, 0, projection.stderr)
        self.assertEqual(
            projection.stdout.strip(),
            self.config["source_contract_projection_sha256"],
        )

    def test_l2_transition_rejects_wildcard_gpt_contract(self) -> None:
        mutated = json.loads(json.dumps(self.config))
        mutated["candidate_level"] = "L2 內部軟體候選"
        mutated["candidate_scope"] = "internal-l2"
        mutated["current_evidence_level"] = "L2"
        with tempfile.TemporaryDirectory(prefix="sm10-l2-transition-") as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(
                json.dumps(mutated, ensure_ascii=False), encoding="utf-8"
            )
            rejected = subprocess.run(
                [str(POLICY), str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("L2 GPT 契約不得含萬用值", rejected.stderr)

    def test_l1_contract_rejects_historical_image_promotion(self) -> None:
        rejected = subprocess.run(
            [str(POLICY), str(CONFIG), "--verify-historical-image"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("L1 校準契約不能執行歷史映像重驗", rejected.stderr)

    def test_dedicated_tools_keep_component_and_full_image_paths_separate(self) -> None:
        source_text = SOURCE_VERIFY.read_text(encoding="utf-8")
        component_text = COMPONENT_BUILD.read_text(encoding="utf-8")
        component_verify_text = COMPONENT_VERIFY.read_text(encoding="utf-8")
        build_text = IMAGE_BUILD.read_text(encoding="utf-8")
        runner_text = IMAGE_RUNNER.read_text(encoding="utf-8")
        verify_text = IMAGE_VERIFY.read_text(encoding="utf-8")
        generic_verify_text = GENERIC_IMAGE_VERIFY.read_text(encoding="utf-8")
        finalizer_text = IMAGE_FINALIZER.read_text(encoding="utf-8")

        self.assertIn("source_commits", source_text)
        self.assertIn('cd "${sdk_root}" && repo manifest', source_text)
        self.assertIn("--shared --no-checkout", component_text)
        self.assertIn("k3-bananapi-sm10.dtb", component_text)
        self.assertIn("harbor.spacemit.com/bianbu/k3-bsp-builder:latest", component_text)
        self.assertIn("COMPONENT_CONTAINER_IMAGE_ID", component_text)
        self.assertIn('"dtb_compatible": compatible.split()', component_text)
        self.assertNotIn("./compile.sh", component_text)
        self.assertIn("component_build_evidence", component_verify_text)
        self.assertIn("不得包含 SDK 原始碼或私鑰", component_verify_text)
        self.assertIn("deploy_spacemit_k3_uboot_target_with_evidence", self.family_text)
        self.assertIn("u-boot-config-target-${uboot_target_counter}", self.family_text)
        self.assertIn("u-boot-metadata-target-${uboot_target_counter}.sh", self.family_text)
        self.assertIn("build-bananapi-sunxi-candidates.sh", build_text)
        self.assertIn("SOURCE_DATE_EPOCH=1777390324", build_text)
        self.assertIn('MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-80}"', build_text)
        self.assertIn("bananapi-spacemit-k3-sm10-cache-overlay", runner_text)
        self.assertIn('expected_identity="66306 96224797"', runner_text)
        self.assertIn("verify-bananapi-sunxi-candidates.sh", verify_text)
        self.assertIn("VERIFICATION_DEFER_STATUS_PROMOTION", verify_text)
        self.assertIn("losetup --find --show --partscan --read-only", generic_verify_text)
        self.assertIn("mount -o ro", generic_verify_text)
        self.assertIn("env_k3)", generic_verify_text)
        self.assertIn("root_partuuid", generic_verify_text)
        self.assertGreaterEqual(generic_verify_text.count("if value is None:"), 3)
        self.assertIn("SM10_CALIBRATION.json", finalizer_text)
        self.assertIn("SM10_MATERIAL_EVIDENCE.json", finalizer_text)

    def test_component_evidence_locks_actual_outputs(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(
            evidence["implementation_commit"],
            "a511c357d1120a18d608cbc798e60f172463c031",
        )
        self.assertRegex(evidence["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(evidence["status_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(evidence["artifacts"]), 10)
        self.assertEqual(
            evidence["artifacts"]["k3-bananapi-sm10.dtb"]["sha256"],
            "a74520d979cc62fcdb12dfddd97c7968900109df6a33ae34c1489d87a34695ba",
        )
        for artifact, values in evidence["artifacts"].items():
            with self.subTest(artifact=artifact):
                self.assertGreater(values["size"], 0)
                self.assertRegex(values["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
