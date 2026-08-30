#!/usr/bin/env python3
"""Banana Pi R3 Mini eMMC 候選來源與驗證契約回歸測試。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import lzma
import mailbox
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7986-r3mini-current.json"
BOARD = ROOT / "config/boards/bananapir3mini.wip"
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/filogic-6.12/patches.armbian/mt7986a-bananapi-bpi-r3-mini-emmc.patch"
)
KERNEL_SERIES = (
    ROOT / "patch/kernel/archive/filogic-6.12/series.conf",
    ROOT / "patch/kernel/archive/filogic-6.12/series.armbian",
)
UBOOT_PATCH = ROOT / "patch/u-boot/u-boot-filogic/453-add-bpi-r3-mini-u-boot-dts.patch"
GPT = ROOT / "packages/blobs/filogic/gpt"
AIROHA = ROOT / "packages/blobs/filogic/firmware/airoha"
POLICY_CHECK = ROOT / "tools/check-bananapi-filogic-r3mini-policy.sh"
FINALIZER = ROOT / "tools/finalize-bananapi-filogic-r3mini-verification.sh"
BUILD = ROOT / "tools/build-bananapi-filogic-r3mini-candidate.sh"
RUNNER = ROOT / "tools/run-bananapi-filogic-r3mini-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-filogic-r3mini-candidate.sh"
PYTHON_POLICY_CHECK = ROOT / "tools/check-bananapi-filogic-r3mini-policy.py"
SPEC = importlib.util.spec_from_file_location("r3mini_policy_check", PYTHON_POLICY_CHECK)
assert SPEC and SPEC.loader
POLICY_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY_MODULE)


class BananaPiFilogicR3MiniCandidateTests(unittest.TestCase):
    """防止 R3 Mini eMMC、啟動鏈、韌體與發布政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapir3mini"]
        cls.board_text = BOARD.read_text()

    def refresh_projection(self, config: dict[str, object]) -> None:
        config["source_contract_projection_sha256"] = (
            POLICY_MODULE.source_contract_projection_sha256(config)
        )

    def run_policy(
        self,
        config: dict[str, object],
        *arguments: str,
        refresh_projection: bool = True,
        output: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        if refresh_projection:
            self.refresh_projection(config)
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "r3mini-policy.json"
            policy.write_text(json.dumps(config, ensure_ascii=False))
            environment = os.environ.copy()
            environment["VALIDATION_CONFIG"] = str(policy)
            if output is not None:
                environment["OUTPUT_DIR"] = str(output)
            return subprocess.run(
                [str(POLICY_CHECK), *arguments],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )

    def valid_l2_config(self) -> dict[str, object]:
        promoted = json.loads(json.dumps(self.config))
        promoted["candidate_level"] = "L2 內部軟體候選"
        promoted["candidate_scope"] = "internal-l2"
        promoted["current_evidence_level"] = "L2"
        promoted["full_rootfs_image_built"] = True
        promoted["l2_contract_calibration_required"] = False
        promoted["release_gate"]["full_image_built"] = True
        promoted["release_gate"]["component_validation_only"] = False
        promoted["boards"]["bananapir3mini"]["required_partitions"][-1] = (
            "5:armbi_root:32768:4096"
        )
        promoted["boards"]["bananapir3mini"]["final_kernel_config_sha256"] = "8" * 64
        promoted["boards"]["bananapir3mini"]["final_uboot_config_sha256"] = "9" * 64
        promoted["boards"]["bananapir3mini"]["image_dtb_sha256"] = "a" * 64
        promoted["boards"]["bananapir3mini"]["dtb_sha256"] = "a" * 64
        promoted["boards"]["bananapir3mini"]["dtb_sha256_evidence_scope"] = "full-image-l2"
        promoted.pop("image_build_evidence", None)
        self.refresh_projection(promoted)
        return promoted

    def write_fake_l2_fixture(self, output: Path) -> tuple[dict[str, object], Path]:
        candidate = self.valid_l2_config()
        source_commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        source_tree = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        committed_config = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "show",
                f"{source_commit}:{CONFIG.relative_to(ROOT).as_posix()}",
            ],
            capture_output=True,
            check=True,
        ).stdout
        config_hash = hashlib.sha256(committed_config).hexdigest()
        board_dir = output / "bananapir3mini"
        board_dir.mkdir(parents=True)
        image = board_dir / "r3mini.img"
        archive = board_dir / "r3mini.img.xz"
        image.write_bytes((b"BPI-R3-MINI\0" * 128) + b"rootfs")
        with lzma.open(archive, "wb") as stream:
            stream.write(image.read_bytes())
        image_sha256 = hashlib.sha256(image.read_bytes()).hexdigest()
        archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

        payload = output / "UBOOT_PAYLOAD_EVIDENCE.tsv"
        payload.write_text(
            "board\tpayload\tplacement\toffset\tsize\tsha256\n"
            "bananapir3mini\tbl2.img\timage\t17408\t204889\t"
            "fcc79a31bc4ea8a1104584991b53ed61834a40171c765a3e3859270ba7509b9e\n"
            "bananapir3mini\tu-boot.fip\timage\t6815744\t510681\t"
            "848ee9f1f6f451658ad179f240231dde24831f301ef8e9a43744621bf6d18408\n"
            "bananapir3mini\tgpt\tpackage-only\t-\t17408\t"
            "beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d\n"
        )
        final_config = output / "FINAL_CONFIG_EVIDENCE.tsv"
        final_config.write_text(
            "board\tcomponent\tpath\tsha256\n"
            f"bananapir3mini\tkernel\tboot/config-test\t{'8' * 64}\n"
            f"bananapir3mini\tuboot\tusr/lib/u-boot/config\t{'9' * 64}\n"
        )
        matrix = output / "CANDIDATES.tsv"
        matrix.write_text(
            "board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\t"
            "img_path\txz_path\tsource_commit\tuboot_tag\n"
            f"bananapir3mini\ttrixie\tcli\t{image.stat().st_size}\t"
            f"{image_sha256}\t{archive.stat().st_size}\t{archive_sha256}\t"
            f"bananapir3mini/r3mini.img\tbananapir3mini/r3mini.img.xz\t"
            f"{source_commit}\tv2025.04\n"
        )
        matrix_hash = hashlib.sha256(matrix.read_bytes()).hexdigest()
        payload_hash = hashlib.sha256(payload.read_bytes()).hexdigest()
        final_config_hash = hashlib.sha256(final_config.read_bytes()).hexdigest()
        runtime_hash = POLICY_MODULE.canonical_sha256(
            candidate["firmware_runtime_sources"]
        )

        completion = {
            "status": "complete",
            "source_commit": source_commit,
            "source_tree": source_tree,
            "validation_config_sha256": config_hash,
            "candidates_sha256": matrix_hash,
            "source_contract_projection_sha256": candidate[
                "source_contract_projection_sha256"
            ],
            "firmware_runtime_sources_sha256": runtime_hash,
        }
        verification = {
            "status": "complete",
            "evidence_level": "L2",
            "source_commit": source_commit,
            "source_tree": source_tree,
            "verifier_commit": source_commit,
            "build_validation_config_sha256": config_hash,
            "verification_config_sha256": config_hash,
            "candidate_matrix_sha256": matrix_hash,
            "uboot_payload_manifest_sha256": payload_hash,
            "final_config_manifest_sha256": final_config_hash,
            "source_contract_projection_sha256": candidate[
                "source_contract_projection_sha256"
            ],
            "firmware_runtime_sources_sha256": runtime_hash,
            "material_reparsed": True,
            "calibration_mode": "formal",
            "material_image_sha256": image_sha256,
            "material_archive_sha256": archive_sha256,
            "read_only_content_verified": True,
            "hardware_tested": False,
            "emmc_image_contract": {
                "user_area": {"image_is_complete_cold_boot_installer": False},
                "boot0": {
                    "requires_separate_write": True,
                    "hardware_validated": False,
                },
            },
        }
        (output / "COMPLETION_STATUS.json").write_text(json.dumps(completion))
        status_path = output / "VERIFICATION_STATUS.json"
        status_path.write_text(json.dumps(verification))
        calibration = {
            "mode": "formal",
            "source_commit": source_commit,
            "source_tree": source_tree,
            "validation_config_sha256": config_hash,
            "source_contract_projection_sha256": candidate[
                "source_contract_projection_sha256"
            ],
        }
        calibration_path = output / "R3MINI_CALIBRATION.json"
        calibration_path.write_text(json.dumps(calibration))
        verification["calibration_manifest_sha256"] = hashlib.sha256(
            calibration_path.read_bytes()
        ).hexdigest()
        status_path.write_text(json.dumps(verification))
        return candidate, status_path

    def test_sources_are_exactly_pinned(self) -> None:
        expected = {
            'KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"',
            'BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"',
            'ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"',
            'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'MT76_FIRMWARE_GIT_REF_BOARD="commit:c5a3bd91aa735b669618610d5f0ebfa5786845a6"',
            'LINUX_FIRMWARE_GIT_REF_BOARD="commit:01205307636157a12c29e6a774bf83b218732050"',
        }
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        self.assertEqual(
            self.config["vendor_reference_commit"],
            "9bd78779f267a21c04c5bb4d16c32e83aae8d1d3",
        )
        self.assertEqual(
            self.config["firmware_ref"],
            "commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
        )
        self.assertTrue(self.config["verify_firmware_source_resolution"])
        self.assertTrue(self.config["verify_mt76_firmware_source_resolution"])
        self.assertTrue(self.config["verify_linux_firmware_source_contract"])
        self.assertEqual(
            [source["commit"] for source in self.config["firmware_runtime_sources"]],
            [
                "c5a3bd91aa735b669618610d5f0ebfa5786845a6",
                "01205307636157a12c29e6a774bf83b218732050",
            ],
        )
        self.assertIn(
            'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
            self.board_text,
        )

    def test_policy_rejects_firmware_source_drift(self) -> None:
        mutations = {
            "來源漂移": ("firmware_source", "https://example.invalid/firmware"),
            "引用漂移": ("firmware_ref", "branch:main"),
            "提交漂移": ("firmware_commit", "0" * 40),
            "停用解析守門": ("verify_firmware_source_resolution", False),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                invalid = json.loads(json.dumps(self.config))
                invalid[field] = value
                rejected = self.run_policy(invalid)
                self.assertNotEqual(rejected.returncode, 0)

    def test_release_gate_remains_blocked(self) -> None:
        self.assertEqual(self.config["candidate_level"], "L2 內部軟體候選")
        self.assertEqual(self.config["candidate_scope"], "internal-l2")
        self.assertEqual(self.config["allowed_evidence_levels"], ["L1", "L2"])
        self.assertTrue(self.config["component_build_completed"])
        self.assertTrue(self.config["full_rootfs_image_built"])
        self.assertFalse(self.config["public_release_authorized"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertFalse(self.config["hardware_validation_completed"])
        self.assertEqual(self.config["release_gate"]["status"], "blocked")
        self.assertFalse(self.config["release_gate"]["public_release_authorized"])
        self.assertFalse(self.config["release_gate"]["hardware_claims_allowed"])
        self.assertTrue(self.config["release_gate"]["full_image_built"])
        self.assertFalse(self.config["release_gate"]["component_validation_only"])
        self.assertNotIn("image_build_evidence", self.config)
        self.assertEqual(
            set(self.config["release_gate"]["required_blockers"]),
            set(self.config["public_release_blockers"]),
        )
        obj = self.config["atf_prebuilt_objects"][
            "plat/mediatek/mt7986/drivers/dram/release/dram.o"
        ]
        self.assertFalse(obj["redistribution_authorized"])
        self.assertEqual(
            obj["sha256"],
            "45acf44f2fe576991d7c0b13862cb41d1ffd37b37e1607e27ca4ddb31820fa79",
        )
        subprocess.run(
            [str(POLICY_CHECK), "--source-contract-only"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )

    def test_policy_state_machine_rejects_label_only_promotion(self) -> None:
        promoted = json.loads(json.dumps(self.config))
        promoted["candidate_level"] = "L2 內部軟體候選"
        rejected = self.run_policy(promoted)
        self.assertNotEqual(rejected.returncode, 0)

    def test_policy_state_machine_rejects_image_evidence_on_l1(self) -> None:
        invalid = json.loads(json.dumps(self.config))
        invalid["image_build_evidence"] = {"status": "complete"}
        rejected = self.run_policy(invalid)
        self.assertNotEqual(rejected.returncode, 0)

    def test_l2_source_contract_can_rebuild_without_old_output(self) -> None:
        accepted = self.run_policy(
            self.valid_l2_config(), "--source-contract-only"
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())

        with tempfile.TemporaryDirectory() as directory:
            rejected = self.run_policy(
                self.valid_l2_config(), output=Path(directory)
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("CANDIDATES.tsv", rejected.stderr.decode())

    def test_policy_state_machine_rejects_incomplete_l2_contract(self) -> None:
        mutations = {
            "保留萬用分割區": lambda data: data["boards"]["bananapir3mini"][
                "required_partitions"
            ].__setitem__(-1, "5:*:32768:*"),
            "缺少映像 DTB": lambda data: data["boards"]["bananapir3mini"].__setitem__(
                "image_dtb_sha256", None
            ),
            "缺少核心設定": lambda data: data["boards"]["bananapir3mini"].pop(
                "final_kernel_config_sha256"
            ),
            "缺少 U-Boot 設定": lambda data: data["boards"]["bananapir3mini"].pop(
                "final_uboot_config_sha256"
            ),
            "校準仍未完成": lambda data: data.__setitem__(
                "l2_contract_calibration_required", True
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                invalid = self.valid_l2_config()
                mutate(invalid)
                rejected = self.run_policy(invalid, "--source-contract-only")
                self.assertNotEqual(rejected.returncode, 0)

    def test_fake_tiny_image_and_manual_manifests_never_close_l2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            candidate, status = self.write_fake_l2_fixture(output)
            board = candidate["boards"]["bananapir3mini"]
            with self.assertRaisesRegex(SystemExit, "IMG 過小"):
                POLICY_MODULE.validate_l2_material(
                    candidate, board, CONFIG, output, status
                )
            policy = output / "l2-policy.json"
            policy.write_text(json.dumps(candidate, ensure_ascii=False))
            inspected = subprocess.run(
                [
                    "python3",
                    str(ROOT / "tools/inspect-bananapi-filogic-r3mini-material.py"),
                    "--validation",
                    str(policy),
                    "--output",
                    str(output),
                    "--mode",
                    "formal",
                    "--status",
                    str(status),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(inspected.returncode, 0)
            self.assertIn("32 MiB", inspected.stderr.decode())
            policy_rejected = self.run_policy(
                candidate,
                "--material-evidence-only",
                "--output",
                str(output),
                "--status",
                str(status),
                output=output,
            )
            self.assertNotEqual(policy_rejected.returncode, 0)
            self.assertIn("32 MiB", policy_rejected.stderr.decode())

    def test_projection_excludes_dynamic_evidence_but_invalidates_contract_drift(self) -> None:
        dynamic = json.loads(json.dumps(self.config))
        dynamic["component_build_evidence"]["completed_utc"] = "2099-01-01T00:00:00Z"
        accepted = self.run_policy(
            dynamic,
            "--source-contract-only",
            refresh_projection=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())

        drifted = json.loads(json.dumps(self.config))
        drifted["boards"]["bananapir3mini"]["root_partition_label"] = "漂移"
        rejected = self.run_policy(
            drifted,
            "--source-contract-only",
            refresh_projection=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("規範投影", rejected.stderr.decode())

    def test_l2_locks_calibrated_full_image_contract(self) -> None:
        self.assertEqual(self.config["current_evidence_level"], "L2")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertEqual(self.config["source_date_epoch"], 1787793187)
        self.assertFalse(self.config["l2_contract_calibration_required"])
        self.assertEqual(
            self.policy["image_dtb_sha256"],
            "5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab",
        )
        self.assertEqual(
            self.policy["dtb_sha256_evidence_scope"], "full-image-l2"
        )
        self.assertEqual(
            self.policy["final_kernel_config_sha256"],
            "2c6ea26327285e71fa778ccb360269796da043faa69d0fee4e467f1a0c36367e",
        )
        self.assertEqual(
            self.policy["final_uboot_config_sha256"],
            "c4a0328fdaa6b345c6c15096bf99caf7a5a05c68aad38f2ad9e3e241322d937a",
        )

    def test_boot_media_requires_emmc_boot0(self) -> None:
        self.assertEqual(self.policy["candidate_boot_media"], ["emmc"])
        self.assertEqual(self.policy["supported_boot_media"], [])
        self.assertIn("sd", self.policy["unsupported_boot_media"])
        contract = self.policy["boot_media_contract"]
        self.assertEqual(contract["cold_boot_source"], "emmc_boot0")
        self.assertFalse(contract["user_area_image_is_complete_cold_boot_installer"])
        self.assertTrue(contract["boot0_payload_requires_separate_write"])
        self.assertFalse(contract["boot0_hardware_validated"])
        self.assertFalse(contract["sd_boot_supported"])
        self.assertEqual(self.policy["emmc_user_area_target"], "/dev/mmcblk0")
        self.assertEqual(self.policy["emmc_boot0_target"], "/dev/mmcblk0boot0")
        self.assertEqual(self.policy["emmc_boot0_payload"], "bl2.img")
        self.assertTrue(self.policy["emmc_boot0_force_ro_required"])
        self.assertEqual(self.policy["emmc_boot_partition_enable"], "1 1")
        self.assertFalse(self.policy["automatic_emmc_install_authorized"])

    def test_component_evidence_locks_all_recorded_outputs(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(
            evidence["implementation_commit"],
            "717cdc7e91231a16d80b189f43dc6819a80fd739",
        )
        self.assertEqual(len(evidence["artifacts"]), 6)
        self.assertEqual(
            evidence["artifacts"]["linux-dtb"]["sha256"],
            self.policy["dtb_sha256"],
        )
        for name, artifact in evidence["artifacts"].items():
            with self.subTest(name=name):
                self.assertGreater(artifact["size"], 0)
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")

    def test_gpt_payload_and_environment_contract(self) -> None:
        self.assertEqual(
            hashlib.sha256(GPT.read_bytes()).hexdigest(),
            self.policy["gpt_template_sha256"],
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
                "5::32768:2750464",
            ],
        )
        env = self.policy["uboot_environment_contract"]
        self.assertEqual(env["partition_start_sector"], 8192)
        self.assertEqual(env["partition_sector_count"], 1024)
        self.assertEqual(env["copy_size_bytes"], 0x40000)
        self.assertEqual(env["resolved_copy_offsets_bytes"], [0x400000, 0x440000])
        self.assertEqual(
            self.policy["uboot_payload_maximum_sizes"],
            ["bl2.img=4176896", "gpt=17408", "u-boot.fip=4194304"],
        )
        self.assertEqual(
            self.policy["uboot_payload_sizes"],
            ["bl2.img=204889", "gpt=17408", "u-boot.fip=510681"],
        )
        self.assertEqual(self.policy["root_partition_start_sector"], 32768)
        self.assertEqual(self.policy["root_partition_label"], "armbi_root")
        self.assertEqual(self.policy["root_partition_filesystem_type"], "ext4")
        self.assertEqual(len(self.policy["required_partition_types"]), 5)
        self.assertEqual(
            self.policy["required_partition_types"][3],
            "4:c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
        )
        self.assertEqual(
            self.policy["uboot_payload_sha256"],
            [
                "bl2.img=fcc79a31bc4ea8a1104584991b53ed61834a40171c765a3e3859270ba7509b9e",
                "gpt=beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d",
                "u-boot.fip=848ee9f1f6f451658ad179f240231dde24831f301ef8e9a43744621bf6d18408",
            ],
        )

    def test_uboot_uses_standard_boot_and_safe_emmc(self) -> None:
        for value in (
            "post_config_uboot_target__bananapir3mini_standard_boot",
            "CONFIG_BOOTSTD_BOOTCOMMAND",
            "CONFIG_BOOTMETH_EXTLINUX",
            "CONFIG_ENV_MMC_PARTITION ubootenv",
            "CONFIG_ENV_OFFSET 0x400000",
            "CONFIG_ENV_OFFSET_REDUND 0x440000",
            "mediatek/mt7986a-bananapi-bpi-r3-mini.dtb",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        patch_text = UBOOT_PATCH.read_text()
        for value in (
            "bus-width = <8>;",
            "max-frequency = <200000000>;",
            "mmc-hs200-1_8v;",
            "no-mmc-hs400;",
            "non-removable;",
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch_text)
        contract = self.policy["uboot_dtb_contract"]
        self.assertEqual(contract["storage_node"], "/mmc@11230000")
        self.assertEqual(contract["bus_width"], 8)
        self.assertEqual(contract["max_frequency"], 200000000)

    def test_uboot_patch_mailbox_subject_is_parser_safe(self) -> None:
        messages = mailbox.mbox(UBOOT_PATCH)
        self.addCleanup(messages.close)
        self.assertEqual(len(messages), 1)
        subject = messages[0]["Subject"]
        self.assertIsInstance(subject, str)
        self.assertTrue(subject.strip())

    def test_linux_dtb_enables_only_hs200(self) -> None:
        patch_text = KERNEL_PATCH.read_text()
        for value in (
            "bus-width = <8>;",
            "max-frequency = <200000000>;",
            "cap-mmc-highspeed;",
            "cap-mmc-hw-reset;",
            "mmc-hs200-1_8v;",
            "no-mmc-hs400;",
            "non-removable;",
            'status = "okay";',
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch_text)
        for series in KERNEL_SERIES:
            self.assertIn(KERNEL_PATCH.name, series.read_text())
        self.assertEqual(
            self.policy["dtb_sha256"],
            "5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab",
        )

    def test_network_drivers_firmware_and_licenses_are_complete(self) -> None:
        self.assertIn("opts_m+=(AIR_EN8811H_PHY)", self.board_text)
        self.assertEqual(self.config["common_kernel_options"]["CONFIG_AIR_EN8811H_PHY"], "m")
        expected = {
            "EthMD32.DSP.bin": "3e4699ec709c836d5fce7c91bc5d205beb54aea326c4b70c7050b355784cbebd",
            "EthMD32.dm.bin": "874982b88330112c376e484cdce114cf2e1476ccbb901c87f80882f127ffb90f",
            "LICENSE.airoha": "ad548ca0ffb91ec655de0f28e13089ef1cd4e0deabb2f15a9289194990e62252",
            "SOURCE.md": "06480315ef0caa8a8ddfec7b1a01f73b5b712922d4705c85eda3719a476495aa",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256((AIROHA / filename).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)
        firmware = self.config["installed_firmware_blobs"]
        self.assertEqual(len(firmware), 14)
        self.assertIn("/lib/firmware/airoha/EthMD32.dm.bin", firmware)
        self.assertEqual(
            len([path for path in firmware if path.startswith("/lib/firmware/mediatek/")]),
            12,
        )
        self.assertIn("R3 Mini MT76 韌體固定來源", self.board_text)
        self.assertIn("R3 Mini Linux firmware 固定來源", self.board_text)
        self.assertIn("firmware-source-contract.tsv", self.board_text)
        self.assertEqual(
            self.config["installed_file_sha256"][
                "/usr/share/doc/armbian-bsp-bananapir3mini/firmware-source-contract.tsv"
            ],
            "b6dba3eaffbb1eb6eb4d9506e4be8c57d51a31619cabbbb52e133a02190cd74c",
        )

    def test_dedicated_entrypoints_select_only_r3mini(self) -> None:
        for path in (BUILD, RUNNER, VERIFIER):
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertIn("bananapi-filogic-mt7986-r3mini-current.json", text)
                self.assertIn(
                    "bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli",
                    text,
                )
                self.assertIn('BOARDS="bananapir3mini"', text)
        self.assertIn("bananapi-filogic-r3mini-cache-overlay", RUNNER.read_text())
        self.assertIn(
            'CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-filogic-r3mini-candidate.sh"',
            RUNNER.read_text(),
        )
        self.assertIn('minimum_free_gib="${MINIMUM_FREE_GIB:-80}"', RUNNER.read_text())
        self.assertIn("((minimum_free_gib >= 40))", RUNNER.read_text())
        self.assertIn('export MINIMUM_FREE_GIB="${minimum_free_gib}"', RUNNER.read_text())
        self.assertIn("ALLOW_INTERNAL_R3MINI_CANDIDATE=yes", RUNNER.read_text())
        self.assertIn("REQUIRE_ISOLATED_CACHE=yes", RUNNER.read_text())
        self.assertIn("禁止覆寫 ${variable}", RUNNER.read_text())
        self.assertIn('fixed_cache_lower="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"', RUNNER.read_text())
        self.assertIn("validate_fixed_overlay_mount", BUILD.read_text())
        for identity in ("lowerdir", "upperdir", "workdir"):
            self.assertIn(identity, BUILD.read_text())
        self.assertIn("check-bananapi-filogic-r3mini-policy.sh", BUILD.read_text())
        self.assertIn("check-bananapi-filogic-r3mini-policy.sh", VERIFIER.read_text())
        self.assertIn("finalize-bananapi-filogic-r3mini-verification.sh", VERIFIER.read_text())
        self.assertIn("VERIFICATION_PRE_COMPLETE_HOOK", VERIFIER.read_text())
        self.assertIn("VERIFY_ARCHIVES=yes", VERIFIER.read_text())
        self.assertIn('policy_evidence_level=L1', VERIFIER.read_text())
        self.assertIn('policy_evidence_level=L2', VERIFIER.read_text())
        self.assertIn(
            'VERIFICATION_EVIDENCE_LEVEL="${policy_evidence_level}"',
            VERIFIER.read_text(),
        )
        self.assertIn("write_entry_state in_progress", VERIFIER.read_text())
        self.assertIn("write_entry_state failed", VERIFIER.read_text())
        self.assertIn("REQUIRE_BUILD_VERIFIER_IDENTITY=yes", VERIFIER.read_text())
        self.assertIn("REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", VERIFIER.read_text())
        self.assertIn("VERIFICATION_DEFER_STATUS_PROMOTION=yes", VERIFIER.read_text())
        self.assertIn("inspect-bananapi-filogic-r3mini-material.py", VERIFIER.read_text())
        self.assertIn("--material-evidence-only", VERIFIER.read_text())
        self.assertIn("ALLOW_INTERNAL_R3MINI_CANDIDATE", BUILD.read_text())
        self.assertIn("REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", BUILD.read_text())
        self.assertIn("PUBLIC_RELEASE=no", BUILD.read_text())
        self.assertIn("HARDWARE_CLAIMS=no", BUILD.read_text())
        for variable in ("PUBLIC_RELEASE", "HARDWARE_CLAIMS"):
            environment = os.environ.copy()
            environment[variable] = "yes"
            rejected = subprocess.run(
                [str(BUILD)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )
            with self.subTest(variable=variable):
                self.assertNotEqual(rejected.returncode, 0)

        environment = os.environ.copy()
        environment["MINIMUM_FREE_GIB"] = "39"
        rejected = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("不得低於 40 GiB", rejected.stderr.decode())

        for variable in ("CACHE_LOWER", "CACHE_TARGET", "CACHE_OVERLAY_ROOT"):
            overridden = subprocess.run(
                [str(RUNNER)],
                cwd=ROOT,
                env={**os.environ, variable: "/tmp/覆寫"},
                check=False,
                capture_output=True,
            )
            with self.subTest(variable=variable):
                self.assertEqual(overridden.returncode, 2)
                self.assertIn("禁止覆寫", overridden.stderr.decode())

        timestamp_override = subprocess.run(
            [str(BUILD)],
            cwd=ROOT,
            env={
                **os.environ,
                "ALLOW_INTERNAL_R3MINI_CANDIDATE": "yes",
                "SOURCE_DATE_EPOCH": "1787793188",
            },
            check=False,
            capture_output=True,
        )
        self.assertEqual(timestamp_override.returncode, 2)
        self.assertIn("SOURCE_DATE_EPOCH", timestamp_override.stderr.decode())

        overlay_bypass = subprocess.run(
            [str(BUILD)],
            cwd=ROOT,
            env={
                **os.environ,
                "ALLOW_INTERNAL_R3MINI_CANDIDATE": "yes",
                "REQUIRE_ISOLATED_CACHE": "no",
            },
            check=False,
            capture_output=True,
        )
        self.assertNotEqual(overlay_bypass.returncode, 0)

    def test_preflight_failure_invalidates_old_success_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            status = output / "VERIFICATION_STATUS.json"
            status.write_text('{"status":"complete","evidence_level":"L1"}\n')
            result = subprocess.run(
                [str(VERIFIER)],
                cwd=ROOT,
                env={**os.environ, "OUTPUT_DIR": str(output)},
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            failed = json.loads(status.read_text())
            self.assertEqual(failed["status"], "failed")
            self.assertIn("失敗", failed["detail"])

    def test_common_tools_bind_source_timestamp_and_identity(self) -> None:
        builder = (ROOT / "tools/build-bananapi-sunxi-candidates.sh").read_text()
        verifier = (ROOT / "tools/verify-bananapi-sunxi-candidates.sh").read_text()
        for value in (
            "REQUIRE_SOURCE_DATE_EPOCH_METADATA",
            'build_parameters+=" SOURCE_DATE_EPOCH=${source_date_epoch}"',
            "assert_source_identity",
            "建置期間來源 HEAD 已改變",
            "source_date_epoch=%s",
        ):
            self.assertIn(value, builder)
        for value in (
            "REQUIRE_BUILD_VERIFIER_IDENTITY",
            "REQUIRE_SOURCE_DATE_EPOCH_METADATA",
            '"source_tree": "%s"',
            '"source_tree", "verifier_commit"',
            "assert_verifier_identity",
            "驗證期間 validation 已改變",
            "VERIFICATION_DEFER_STATUS_PROMOTION",
        ):
            self.assertIn(value, verifier)

    def test_finalizer_enforces_payload_bounds_and_release_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = output / "UBOOT_PAYLOAD_EVIDENCE.tsv"
            evidence.write_text(
                "board\tpayload\tplacement\toffset\tsize\tsha256\n"
                "bananapir3mini\tbl2.img\timage\t17408\t204889\t"
                + "fcc79a31bc4ea8a1104584991b53ed61834a40171c765a3e3859270ba7509b9e"
                + "\nbananapir3mini\tu-boot.fip\timage\t6815744\t510681\t"
                + "848ee9f1f6f451658ad179f240231dde24831f301ef8e9a43744621bf6d18408"
                + "\nbananapir3mini\tgpt\tpackage-only\t-\t17408\t"
                + "beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d"
                + "\n"
            )
            status = output / "VERIFICATION_STATUS.json.partial"
            status.write_text(json.dumps({"status": "complete", "evidence_level": "L2"}))
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output)
            subprocess.run(
                [str(FINALIZER), str(status)],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
            )
            result = json.loads(status.read_text())
            self.assertFalse(result["public_release_authorized"])
            self.assertFalse(result["hardware_claims_allowed"])
            self.assertFalse(result["hardware_validation_completed"])
            self.assertEqual(result["candidate_scope"], "internal-l2")
            self.assertTrue(result["full_rootfs_image_built"])
            self.assertTrue(result["internal_candidate_only"])
            self.assertEqual(result["release_gate"]["status"], "blocked")
            self.assertFalse(
                result["emmc_image_contract"]["user_area"][
                    "image_is_complete_cold_boot_installer"
                ]
            )
            self.assertEqual(
                result["emmc_image_contract"]["boot0"]["target"],
                "/dev/mmcblk0boot0",
            )
            self.assertEqual(
                result["emmc_image_contract"]["cold_boot_source"],
                "emmc_boot0",
            )
            self.assertTrue(
                result["emmc_image_contract"]["boot0"]["requires_separate_write"]
            )
            self.assertFalse(
                result["emmc_image_contract"]["boot0"]["hardware_validated"]
            )
            self.assertEqual(len(result["verified_payload_boundaries"]), 3)

            evidence.write_text(evidence.read_text().replace("\t510681\t", "\t4194305\t"))
            rejected = subprocess.run(
                [str(FINALIZER), str(status)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

            evidence.write_text(
                evidence.read_text()
                .replace("\t4194305\t", "\t510681\t")
                .replace(
                    "848ee9f1f6f451658ad179f240231dde24831f301ef8e9a43744621bf6d18408",
                    "0f25bdd6d6085226a8807615bfc5d13e98b27289a389a9fab90ad417950f949e",
                )
            )
            rejected = subprocess.run(
                [str(FINALIZER), str(status)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_finalizer_rejects_evidence_level_above_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "UBOOT_PAYLOAD_EVIDENCE.tsv").write_text(
                "board\tpayload\tplacement\toffset\tsize\tsha256\n"
                "bananapir3mini\tbl2.img\timage\t17408\t204889\t"
                "fcc79a31bc4ea8a1104584991b53ed61834a40171c765a3e3859270ba7509b9e\n"
                "bananapir3mini\tu-boot.fip\timage\t6815744\t510681\t"
                "848ee9f1f6f451658ad179f240231dde24831f301ef8e9a43744621bf6d18408\n"
                "bananapir3mini\tgpt\tpackage-only\t-\t17408\t"
                "beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d\n"
            )
            status = output / "VERIFICATION_STATUS.json.partial"
            status.write_text(json.dumps({"status": "complete", "evidence_level": "L2"}))
            l1_policy = json.loads(json.dumps(self.config))
            l1_policy["candidate_level"] = "L1 元件候選"
            l1_policy["candidate_scope"] = "internal-component-only"
            l1_policy["current_evidence_level"] = "L1"
            l1_policy["full_rootfs_image_built"] = False
            l1_policy["l2_contract_calibration_required"] = True
            l1_policy["release_gate"]["full_image_built"] = False
            l1_policy["release_gate"]["component_validation_only"] = True
            policy_path = output / "l1-policy.json"
            policy_path.write_text(json.dumps(l1_policy, ensure_ascii=False))
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output)
            environment["VALIDATION_CONFIG"] = str(policy_path)
            rejected = subprocess.run(
                [str(FINALIZER), str(status)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )
        self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
