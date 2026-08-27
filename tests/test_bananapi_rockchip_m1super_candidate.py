import importlib.util
import hashlib
import json
import lzma
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapim1super.wip"
VALIDATION = ROOT / "config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3528-bananapi-m1-super.dts"
UBOOT_DTS = (
    ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3528-bananapi-m1-super.dts"
)
UBOOT_CONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-m1-super-rk3528_defconfig"
)
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-rockchip-m1super-source-policy-20260827.md"
)
COMPONENT_EVIDENCE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-rockchip-m1super-component-build-20260827.md"
)
COMPONENT_VERIFIER = ROOT / "tools/verify-bananapi-rockchip-m1super-components.sh"
POLICY_CHECKER = ROOT / "tools/check-bananapi-rockchip-m1super-policy.py"
BUILD_ENTRY = ROOT / "tools/build-bananapi-rockchip-m1super-candidate.sh"
VERIFY_ENTRY = ROOT / "tools/verify-bananapi-rockchip-m1super-candidate.sh"
ROCKCHIP_BUILD = ROOT / "tools/build-bananapi-rockchip-candidates.sh"
PREFLIGHT_EVIDENCE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/F-rockchip-m1super-L1-preflight-20260827.md"
)
L2_EVIDENCE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/F-rockchip-m1super-L2-build-20260827.md"
)


class BananaPiM1SuperCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.policy_text = POLICY.read_text(encoding="utf-8")
        cls.component_evidence_text = COMPONENT_EVIDENCE.read_text(encoding="utf-8")
        cls.linux_dts = LINUX_DTS.read_text(encoding="utf-8")
        cls.uboot_dts = UBOOT_DTS.read_text(encoding="utf-8")
        cls.uboot_config = UBOOT_CONFIG.read_text(encoding="utf-8")
        cls.validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        cls.board = cls.validation["boards"]["bananapim1super"]
        spec = importlib.util.spec_from_file_location(
            "m1super_policy_checker", POLICY_CHECKER
        )
        cls.policy_checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy_checker)

    def test_board_keeps_wip_and_drops_foreign_board_file_inheritance(self):
        self.assertTrue(BOARD.is_file())
        self.assertFalse((BOARD.parent / "bananapim1super.conf").exists())
        self.assertFalse((BOARD.parent / "bananapim1super.csc").exists())
        self.assertNotIn("armsom-sige1.csc", self.board_text)
        self.assertNotIn("hinlink_rk3528_defconfig", self.board_text)
        self.assertIn(
            'BOOTCONFIG="bananapi-m1-super-rk3528_defconfig"', self.board_text
        )
        self.assertIn(
            'BOOT_FDT_FILE="rockchip/rk3528-bananapi-m1-super.dtb"', self.board_text
        )

    def test_post_family_hook_pins_build_paths_and_disables_source_atf(self):
        for required in (
            'declare -g BOOTPATCHDIR="legacy/u-boot-radxa-rk35xx"',
            'declare -g KERNELPATCHDIR="rk35xx-vendor-6.1"',
            'declare -g LINUXCONFIG="linux-rk35xx-vendor"',
            'declare -g ATF_COMPILE="no"',
            'declare -g ATFSOURCE=""',
            'declare -g ATFBRANCH=""',
        ):
            self.assertIn(required, self.board_text)

    def test_firmware_source_and_ref_are_fixed_in_board_file(self):
        for required in (
            'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
            'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
        ):
            self.assertIn(required, self.board_text)

    def test_sources_are_fixed_to_commits(self):
        expected = {
            "linux_commit": "c6157104418d012823413c02f9222f3fe123dd25",
            "firmware_commit": "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            "atf_commit": "c17351450c8a513ca3f30f936e26a71db693a145",
            "rkbin_commit": "1d3c61008fa823936ae7a59615393f8294b64456",
        }
        for key, value in expected.items():
            self.assertEqual(self.validation[key], value)
            self.assertRegex(value, r"^[0-9a-f]{40}$")
        self.assertEqual(
            self.board["uboot_revision"],
            "39cd993e5d6296635438e84f4576b3a9bf76f86e",
        )

    def test_public_release_and_hardware_claims_are_blocked(self):
        self.assertEqual(self.validation["candidate_level"], "L1 元件候選")
        self.assertEqual(self.validation["candidate_scope"], "internal-component-only")
        self.assertTrue(self.validation["component_build_completed"])
        self.assertFalse(self.validation["full_image_built"])
        self.assertFalse(self.validation["rootfs_image_built"])
        self.assertFalse(self.validation["candidate_public_release_approved"])
        self.assertFalse(self.validation["public_release_allowed"])
        self.assertFalse(self.validation["hardware_validation_complete"])
        self.assertFalse(self.validation["hardware_claims_allowed"])
        self.assertFalse(self.validation["firmware_redistribution_audit_complete"])
        self.assertFalse(self.validation["atf_source_build_available"])
        self.assertFalse(
            self.validation["identity_evidence"]["wifi_bom_conflict_resolved"]
        )

    def test_current_state_is_honest_l1_without_material_evidence(self):
        self.policy_checker.validate_contract_projection(self.validation, False)
        self.policy_checker.validate_candidate_state(self.validation)
        self.assertNotIn("image_build_evidence", self.validation)
        self.assertIsNone(self.board["image_dtb_sha256"])
        self.assertEqual(
            self.board["dtb_sha256_evidence_scope"], "preflight-contract-l1"
        )
        self.assertEqual(
            self.board["component_dtb_sha256"],
            "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
        )
        self.assertEqual(self.board["dtb_sha256"], self.board["component_dtb_sha256"])

    def test_source_contract_phase_does_not_require_existing_output(self):
        rebuild_policy = json.loads(json.dumps(self.validation))
        self.policy_checker.validate_contract_projection(rebuild_policy, False)
        self.policy_checker.validate_candidate_state(rebuild_policy)

        promoted_policy = json.loads(json.dumps(rebuild_policy))
        promoted_policy["candidate_level"] = "L2 內部軟體候選"
        promoted_policy["candidate_scope"] = "internal-l2"
        self.policy_checker.validate_candidate_state(
            promoted_policy, require_material_binding=False
        )
        build_text = BUILD_ENTRY.read_text(encoding="utf-8")
        verify_text = VERIFY_ENTRY.read_text(encoding="utf-8")
        self.assertIn('"${policy_checker}" --phase source-contract', build_text)
        self.assertIn('"${policy_checker}" --phase source-contract', verify_text)

    def test_contract_projection_rejects_requirement_drift_and_old_evidence(self):
        self.assertEqual(
            self.policy_checker.contract_projection_sha256(self.validation),
            self.validation["contract_projection_sha256"],
        )

        forgotten_hash = json.loads(json.dumps(self.validation))
        forgotten_hash["common_packages"].append("新增診斷套件")
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_contract_projection(forgotten_hash, False)

        stale_evidence = json.loads(json.dumps(forgotten_hash))
        stale_evidence["candidate_level"] = "L2 內部軟體候選"
        stale_evidence["candidate_scope"] = "internal-l2"
        stale_evidence["image_build_evidence"] = {
            "contract_projection_sha256": self.validation[
                "contract_projection_sha256"
            ]
        }
        stale_evidence["contract_projection_sha256"] = (
            self.policy_checker.contract_projection_sha256(stale_evidence)
        )
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_contract_projection(stale_evidence, True)

    def test_xz_stream_must_decode_to_the_same_image(self):
        image_bytes = (b"BPI-M1-Super\x00" * 8192) + "完成".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "candidate.img"
            archive = root / "candidate.img.xz"
            image.write_bytes(image_bytes)
            archive.write_bytes(lzma.compress(image_bytes))
            evidence = {"image": {"sha256": hashlib.sha256(image_bytes).hexdigest()}}
            self.policy_checker.validate_xz_stream_matches_image(
                image, archive, evidence
            )

            archive.write_bytes(lzma.compress(image_bytes + "漂移".encode("utf-8")))
            with self.assertRaises(SystemExit):
                self.policy_checker.validate_xz_stream_matches_image(
                    image, archive, evidence
                )

    def test_xz_structure_rejects_trailing_garbage(self):
        image_bytes = b"M1-Super-XZ" * 4096
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "candidate.img"
            archive = root / "candidate.img.xz"
            image.write_bytes(image_bytes)
            archive.write_bytes(lzma.compress(image_bytes) + b"trailing-garbage")
            evidence = {"image": {"sha256": hashlib.sha256(image_bytes).hexdigest()}}
            with self.assertRaises(SystemExit):
                self.policy_checker.validate_xz_stream_matches_image(
                    image, archive, evidence
                )

    def test_artifact_paths_are_confined_to_the_fixed_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output/fixed-m1super"
            board_dir = output / "bananapim1super"
            board_dir.mkdir(parents=True)
            image = board_dir / "candidate.img"
            image.write_bytes(b"image")
            with (
                mock.patch.object(self.policy_checker, "ROOT", root),
                mock.patch.object(self.policy_checker, "OUTPUT_DIR", output),
            ):
                self.assertEqual(
                    self.policy_checker.resolve_artifact(
                        "output/fixed-m1super/bananapim1super/candidate.img",
                        "測試 IMG",
                    ),
                    image,
                )
                self.assertEqual(
                    self.policy_checker.resolve_matrix_artifact(
                        "bananapim1super/candidate.img", "測試矩陣 IMG"
                    ),
                    image,
                )
                for escaped in (
                    "output/fixed-m1super/bananapim1super/../bananapim1super/candidate.img",
                    "../fixed-m1super/bananapim1super/candidate.img",
                ):
                    with self.assertRaises(SystemExit):
                        self.policy_checker.resolve_artifact(escaped, "偽造 IMG")
                with self.assertRaises(SystemExit):
                    self.policy_checker.resolve_matrix_artifact(
                        "bananapim1super/../candidate.img", "偽造矩陣 IMG"
                    )

    def test_linux_dtb_claim_must_match_board_contract(self):
        policy = json.loads(json.dumps(self.validation))
        digest = policy["boards"]["bananapim1super"]["component_dtb_sha256"]
        policy["boards"]["bananapim1super"]["image_dtb_sha256"] = digest
        evidence = {"linux_dtb": {"path": self.board["dtb"], "sha256": digest}}
        self.policy_checker.validate_linux_dtb_claim(policy, evidence)
        evidence["linux_dtb"]["path"] = "rockchip/foreign-board.dtb"
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_linux_dtb_claim(policy, evidence)

    def test_uboot_manifest_is_rechecked_against_contract_and_image_bytes(self):
        payload = "固定載荷內容".encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        policy = {
            "boards": {
                "bananapim1super": {
                    "uboot_payloads": ["u-boot.itb@4096"],
                    "uboot_payload_sizes": [f"u-boot.itb={len(payload)}"],
                    "uboot_payload_sha256": [f"u-boot.itb={digest}"],
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "candidate.img"
            manifest = root / "UBOOT_PAYLOAD_EVIDENCE.tsv"
            image.write_bytes((b"\x00" * 4096) + payload)
            manifest.write_text(
                "board\tpayload\tplacement\toffset\tsize\tsha256\n"
                f"bananapim1super\tu-boot.itb\timage\t4096\t{len(payload)}\t{digest}\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                self.policy_checker, "UBOOT_PAYLOAD_EVIDENCE", manifest
            ):
                self.policy_checker.validate_uboot_payload_manifest(policy, image)

                drifted_payload = "共同偽造內容".encode("utf-8")
                drifted_digest = hashlib.sha256(drifted_payload).hexdigest()
                image.write_bytes((b"\x00" * 4096) + drifted_payload)
                manifest.write_text(
                    "board\tpayload\tplacement\toffset\tsize\tsha256\n"
                    f"bananapim1super\tu-boot.itb\timage\t4096\t{len(drifted_payload)}\t{drifted_digest}\n",
                    encoding="utf-8",
                )
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_uboot_payload_manifest(policy, image)

    def test_rkbin_manifest_is_parsed_instead_of_only_hashing_the_file(self):
        policy = {"rkbin_blobs": {"LICENSE.TXT": "a" * 64}}
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "RKBIN_EVIDENCE.tsv"
            manifest.write_text(
                "path\tsha256\nLICENSE.TXT\t" + ("a" * 64) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(self.policy_checker, "RKBIN_EVIDENCE", manifest):
                self.policy_checker.validate_rkbin_manifest(policy)
                manifest.write_text(
                    "path\tsha256\nOTHER.bin\t" + ("b" * 64) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_rkbin_manifest(policy)

    def test_final_config_manifest_is_bound_to_expected_image_paths(self):
        policy = json.loads(json.dumps(self.validation))
        board = policy["boards"]["bananapim1super"]
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "FINAL_CONFIG_EVIDENCE.tsv"
            manifest.write_text(
                "board\tcomponent\tpath\tsha256\n"
                "bananapim1super\tkernel\tboot/config-6.1.115-vendor-rk35xx\t"
                + board["final_kernel_config_sha256"]
                + "\n"
                "bananapim1super\tuboot\tusr/lib/linux-u-boot-vendor-bananapim1super/u-boot-config-target-1\t"
                + board["final_uboot_config_sha256"]
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                self.policy_checker, "FINAL_CONFIG_EVIDENCE", manifest
            ):
                expected_files = self.policy_checker.validate_final_config_manifest(
                    policy
                )
                self.assertEqual(
                    expected_files["boot/config-6.1.115-vendor-rk35xx"],
                    board["final_kernel_config_sha256"],
                )
                manifest.write_text(
                    manifest.read_text(encoding="utf-8").replace(
                        "boot/config-6.1.115-vendor-rk35xx",
                        "boot/config-偽造版本",
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_final_config_manifest(policy)

    def test_fixed_timestamp_is_bound_across_evidence_status_and_metadata(self):
        expected = self.validation["source_date_epoch"]
        evidence = {"source_date_epoch": expected}
        completion = {"source_date_epoch": expected}
        verification = {"source_date_epoch": expected}
        metadata = {"source_date_epoch": str(expected)}
        self.policy_checker.validate_source_date_epoch_binding(
            self.validation, evidence, completion, verification, metadata
        )
        verification["source_date_epoch"] = expected + 1
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_source_date_epoch_binding(
                self.validation, evidence, completion, verification, metadata
            )

    def test_material_completion_rejects_stale_or_jointly_edited_status(self):
        record = {
            "source_commit": "1" * 40,
            "verifier_commit": "2" * 40,
            "contract_projection_sha256": "3" * 64,
            "source_date_epoch": 1787082913,
            "common_verification_status_sha256": "4" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence_path = output / "M1SUPER_MATERIAL_EVIDENCE.json"
            status_path = output / "M1SUPER_MATERIAL_STATUS.json"
            with (
                mock.patch.object(self.policy_checker, "OUTPUT_DIR", output),
                mock.patch.object(
                    self.policy_checker, "MATERIAL_EVIDENCE", evidence_path
                ),
                mock.patch.object(self.policy_checker, "MATERIAL_STATUS", status_path),
            ):
                self.policy_checker.write_material_completion(record)
                self.policy_checker.validate_material_completion(record)
                stale = json.loads(status_path.read_text(encoding="utf-8"))
                stale["common_verification_status_sha256"] = "5" * 64
                status_path.write_text(
                    json.dumps(stale, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_material_completion(record)

    def test_gpt_contract_rejects_partition_drift(self):
        board = self.validation["boards"]["bananapim1super"]
        partition_table = {
            "partitiontable": {
                "label": "gpt",
                "unit": "sectors",
                "sectorsize": 512,
                "partitions": [
                    {
                        "start": 32768,
                        "size": 4691968,
                        "type": board["required_partition_types"][0].split(":", 1)[1],
                        "name": "rootfs",
                    }
                ],
            }
        }
        completed = mock.Mock(returncode=0, stdout="No problems found")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "candidate.img"
            image.write_bytes(b"gpt")
            with (
                mock.patch.object(
                    self.policy_checker.shutil, "which", return_value="/usr/bin/tool"
                ),
                mock.patch.object(
                    self.policy_checker.subprocess, "run", return_value=completed
                ),
                mock.patch.object(
                    self.policy_checker.subprocess,
                    "check_output",
                    side_effect=lambda *args, **kwargs: json.dumps(partition_table),
                ),
            ):
                summary = self.policy_checker.validate_gpt_contract(
                    self.validation, image
                )
                self.assertTrue(summary["crc_and_structure_verified"])
                partition_table["partitiontable"]["partitions"][0]["size"] -= 1
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_gpt_contract(self.validation, image)

    def test_package_parser_accepts_installed_packages_and_provides(self):
        status_text = (
            "Package: direct-package\n"
            "Status: install ok installed\n\n"
            "Package: provider-package\n"
            "Status: install ok installed\n"
            "Provides: virtual-package (= 1), another-virtual\n\n"
            "Package: removed-package\n"
            "Status: deinstall ok config-files\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status"
            status.write_text(status_text, encoding="utf-8")
            installed = self.policy_checker.installed_package_names(status)
        self.assertIn("direct-package", installed)
        self.assertIn("virtual-package", installed)
        self.assertIn("another-virtual", installed)
        self.assertNotIn("removed-package", installed)

    def test_state_machine_rejects_mixed_or_unproven_states(self):
        mixed = json.loads(json.dumps(self.validation))
        mixed["candidate_scope"] = "internal-l2"
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_candidate_state(mixed)

        unproven_l2 = json.loads(json.dumps(self.validation))
        unproven_l2["candidate_level"] = "L2 內部軟體候選"
        unproven_l2["candidate_scope"] = "internal-l2"
        unproven_l2["full_image_built"] = True
        unproven_l2["rootfs_image_built"] = True
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_candidate_state(unproven_l2)

        false_claim = json.loads(json.dumps(self.validation))
        false_claim["hardware_claims_allowed"] = True
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_candidate_state(false_claim)

    def test_rkbin_license_and_hashes_are_machine_readable(self):
        self.assertTrue(self.validation["rkbin_copy_and_distribution_grant_present"])
        self.assertFalse(self.validation["rkbin_standalone_distribution_authorized"])
        self.assertFalse(self.validation["rkbin_binary_modification_authorized"])
        self.assertTrue(self.validation["rkbin_license_must_accompany_distribution"])
        blobs = self.validation["rkbin_blobs"]
        self.assertEqual(len(blobs), 4)
        for digest in blobs.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn(
            "/usr/share/doc/armbian-bsp-bananapim1super/rkbin.LICENSE.TXT",
            self.validation["installed_firmware_blobs"],
        )

    def test_component_build_hashes_and_limits_are_machine_readable(self):
        evidence = self.validation["component_build_evidence"]
        self.assertEqual(
            evidence["portable_manifest_sha256"],
            "ef452fbc47115ffc34359c44a202733217ff32e95d946c160f8e4ea1ebc3b22a",
        )
        self.assertEqual(evidence["portable_artifact_count"], 6)
        self.assertFalse(evidence["full_rootfs_image_built"])
        self.assertFalse(evidence["hardware_tested"])
        self.assertFalse(evidence["armbian_uboot_patch_stack_complete"])
        expected = {
            "linux_dtb": "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
            "uboot_spl": "43c518cf0f5c98c7228d22920c47d5d22e151536fa8e8a984b3522d76b2430be",
            "uboot_dtb": "b5bdc6143f8a3d2462e12a5a943c0953e85bb7beb9ac499b3d9552540dce9a81",
            "uboot_fit": "7d095910efac37607dbb65389603aa672b77492c4557f5637ab4ad5a68272f6c",
            "idbloader": "513c843f4cb97c3a62508d5b1238b676e29a997eaeeb382a61b808a3198e2c3c",
        }
        for artifact, digest in expected.items():
            self.assertEqual(evidence[artifact]["sha256"], digest)

    def test_provisional_ap6275s_contract_is_source_bounded(self):
        self.assertTrue(self.validation["verify_firmware_source_resolution"])
        self.assertEqual(
            self.validation["firmware_source"], "https://github.com/armbian/firmware"
        )
        contract = self.validation["provisional_wireless_contract"]
        self.assertEqual(contract["contract_id"], "provisional-ap6275s")
        self.assertFalse(contract["bom_identity_confirmed"])
        self.assertFalse(contract["bluetooth_firmware_identity_confirmed"])
        self.assertFalse(contract["runtime_hardware_validated"])
        self.assertEqual(contract["wifi_driver"], "brcmfmac")
        self.assertEqual(contract["bluetooth_driver"], "hci_uart")
        expected_blobs = {
            "/lib/firmware/brcm/brcmfmac43752-sdio.bin": "46f62076768e50938d0e29b306b24d4663de20b07b474c4759d5801fcbf0bdde",
            "/lib/firmware/brcm/brcmfmac43752-sdio.clm_blob": "5143146e1923f87f7aab8df043abcf89a657fa9fdc3b22a38806399730d9a97a",
            "/lib/firmware/brcm/brcmfmac43752-sdio.txt": "2d2723101fe9c66c853ddb1e2d715851ba100a4390f8ac72fc84dd35736cc66f",
        }
        self.assertEqual(contract["required_wifi_firmware_blobs"], expected_blobs)
        for path, digest in expected_blobs.items():
            self.assertEqual(self.validation["installed_firmware_blobs"][path], digest)
        self.assertEqual(
            set(self.validation["required_kernel_module_paths"]),
            {
                "kernel/drivers/bluetooth/hci_uart.ko",
                "kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac/brcmfmac.ko",
            },
        )
        self.assertEqual(
            self.validation["common_kernel_options"]["CONFIG_BRCMFMAC_SDIO"], "y"
        )

    def test_linux_dts_has_dedicated_identity_and_evidence_bounded_io(self):
        self.assertIn('#include "rk3528-armsom-sige1.dts"', self.linux_dts)
        self.assertIn('model = "Banana Pi M1 Super";', self.linux_dts)
        self.assertIn('"bananapi,bpi-m1-super"', self.linux_dts)
        for node in ("&i2c0", "&i2c1", "&spi0"):
            self.assertIn(node, self.linux_dts)
        self.assertEqual(self.linux_dts.count('compatible = "rockchip,spidev";'), 2)
        self.assertIn('wifi_chip_type = "ap6275s";', self.linux_dts)
        self.assertNotIn("Hinlink H28K", self.linux_dts)

    def test_uboot_has_dedicated_dts_and_defconfig(self):
        self.assertIn('model = "Banana Pi M1 Super";', self.uboot_dts)
        self.assertIn('"bananapi,bpi-m1-super"', self.uboot_dts)
        self.assertNotIn("Hinlink H28K", self.uboot_dts)
        self.assertIn(
            'CONFIG_DEFAULT_DEVICE_TREE="rk3528-bananapi-m1-super"',
            self.uboot_config,
        )
        self.assertIn(
            'CONFIG_DEFAULT_FDT_FILE="rk3528-bananapi-m1-super"',
            self.uboot_config,
        )
        self.assertNotIn("rk3528-hinlink-h28k", self.uboot_config)

    def test_validation_targets_dedicated_artifacts(self):
        self.assertEqual(self.board["dtb"], "rockchip/rk3528-bananapi-m1-super.dtb")
        self.assertEqual(
            self.board["component_dtb_sha256"],
            "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
        )
        self.assertIsNone(self.board["image_dtb_sha256"])
        self.assertEqual(self.board["dtb_sha256"], self.board["component_dtb_sha256"])
        self.assertEqual(
            self.board["dtb_sha256_evidence_scope"], "preflight-contract-l1"
        )
        self.assertEqual(
            self.board["uboot_defconfig"],
            "bananapi-m1-super-rk3528_defconfig",
        )
        self.assertEqual(
            self.board["compatible"],
            ["bananapi,bpi-m1-super", "armsom,sige1", "rockchip,rk3528"],
        )
        self.assertIn("Hinlink H28K", self.board["uboot_forbidden_binary_strings"])
        self.assertIn("idbloader.img@32768", self.board["uboot_payloads"])
        self.assertIn("u-boot.itb@8388608", self.board["uboot_payloads"])

    def test_preflight_contract_is_exact_but_not_formal_image_evidence(self):
        self.assertEqual(self.validation["source_date_epoch"], 1787082913)
        self.assertEqual(
            self.board["uboot_payload_sizes"],
            ["idbloader.img=311296", "u-boot.itb=1320960"],
        )
        self.assertEqual(
            self.board["uboot_payload_sha256"],
            [
                "idbloader.img=ecd35b1d69c4b87e2ba170017f58c2f67f44c178dbb7df3488d9b88c26847355",
                "u-boot.itb=ee2067f149cfc6c74f84c5c09880673dcda9133d4593ec20e9fc6e328f6bd59a",
            ],
        )
        self.assertEqual(self.board["required_partitions"], ["1:*:32768:4691968"])
        self.assertEqual(
            self.board["required_partition_types"],
            ["1:b921b045-1df0-41c3-af44-4c6f280d3fae"],
        )
        self.assertEqual(self.board["root_partition_start_sector"], 32768)
        self.assertEqual(self.board["root_partition_label"], "armbi_root")
        self.assertEqual(self.board["root_partition_filesystem_type"], "ext4")
        self.assertEqual(
            self.board["final_kernel_config_sha256"],
            "24edbbaabf1bd7960e7c2647ec7e96c25e2e9bf4de5a440c30827eb15b162e9e",
        )
        self.assertEqual(
            self.board["final_uboot_config_sha256"],
            "c56f7986bc9d636d51439509c4ad43b8adc247b97783717de61553bba8c7bf60",
        )

    def test_packages_cover_requested_diagnostics_without_duplicates(self):
        packages = self.validation["common_packages"]
        self.assertEqual(len(packages), len(set(packages)))
        required = {
            "gpiod",
            "i2c-tools",
            "python3-spidev",
            "spi-tools",
            "mmc-utils",
            "ethtool",
            "pciutils",
            "usbutils",
            "mesa-utils",
            "kmscube",
            "vainfo",
            "v4l-utils",
            "ffmpeg",
        }
        self.assertTrue(required.issubset(packages))

    def test_kernel_contract_includes_io_gpu_vpu_and_otg(self):
        options = self.validation["common_kernel_options"]
        expected = {
            "CONFIG_DRM_LIMA": "m",
            "CONFIG_DRM_ROCKCHIP": "y",
            "CONFIG_GPIO_CDEV": "y",
            "CONFIG_I2C_RK3X": "y",
            "CONFIG_MMC_SDHCI_OF_DWCMSHC": "y",
            "CONFIG_ROCKCHIP_MPP_SERVICE": "y",
            "CONFIG_SPI_SPIDEV": "y",
            "CONFIG_USB_CONFIGFS_MASS_STORAGE": "y",
            "CONFIG_USB_GADGET": "y",
        }
        for key, value in expected.items():
            self.assertEqual(options[key], value)

    def test_policy_document_records_limits_and_evidence(self):
        for required in (
            "L1 元件候選",
            "L2 內部軟體候選",
            "候選狀態機",
            "不得直接對外散布",
            "wifi_bom_conflict_resolved=false",
            "provisional-ap6275s",
            "component_dtb_sha256",
            "verify_firmware_source_resolution",
            "BPI-M1S_ArmSoM-Sige1_V1.2_SCH_20240727.pdf",
            "71d9122b2d6d30916928cc123ce2cece314c922893623a4e6e7d8d2810b279dd",
        ):
            self.assertIn(required, self.policy_text)

    def test_component_document_records_build_scope_and_hashes(self):
        for required in (
            "成功建置 Banana Pi M1 Super 專屬 Linux DTB",
            "full_rootfs_image_built=false",
            "armbian_uboot_patch_stack_complete=false",
            "513c843f4cb97c3a62508d5b1238b676e29a997eaeeb382a61b808a3198e2c3c",
            "不代表板子已開機",
        ):
            self.assertIn(required, self.component_evidence_text)

    def test_candidate_entrypoints_are_present(self):
        for relative in (
            "tools/build-bananapi-rockchip-m1super-candidate.sh",
            "tools/run-bananapi-rockchip-m1super-candidate-isolated-cache.sh",
            "tools/verify-bananapi-rockchip-m1super-candidate.sh",
            "tools/check-bananapi-rockchip-m1super-policy.py",
            "tools/verify-bananapi-rockchip-m1super-components.sh",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_entrypoints_enforce_state_and_l2_archive_checks(self):
        build_text = BUILD_ENTRY.read_text(encoding="utf-8")
        verify_text = VERIFY_ENTRY.read_text(encoding="utf-8")
        self.assertIn('"L1 元件候選" | "L2 內部軟體候選"', build_text)
        self.assertIn('export SOURCE_DATE_EPOCH="${source_date_epoch}"', build_text)
        self.assertIn("SOURCE_DATE_EPOCH 與固定契約不符", build_text)
        self.assertIn('"${policy_checker}" --phase source-contract', build_text)
        self.assertIn("export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", build_text)
        self.assertIn("pending_verification", build_text)
        self.assertIn("write_material_state failed", build_text)
        self.assertIn("只允許固定輸出目錄", build_text)
        self.assertIn("write_entry_state failed", verify_text)
        self.assertIn("write_material_state failed", verify_text)
        self.assertIn("只允許固定輸出目錄", verify_text)
        self.assertIn("calibration_complete", verify_text)
        self.assertIn("verify-bananapi-rockchip-candidates.sh", verify_text)
        self.assertIn("export VERIFY_ARCHIVES=yes", verify_text)
        self.assertIn("export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", verify_text)
        self.assertIn('"L1 元件候選") verification_evidence_level=L1', verify_text)
        self.assertIn(
            'export VERIFICATION_EVIDENCE_LEVEL="${verification_evidence_level}"',
            verify_text,
        )
        common_position = verify_text.index('"${verifier}" "$@"')
        finalize_position = verify_text.index("--finalize-material-status")
        readback_position = verify_text.rindex(
            '"${policy_checker}" --phase material-evidence --evidence-source live'
        )
        self.assertLess(common_position, finalize_position)
        self.assertLess(finalize_position, readback_position)
        self.assertIn('rm -f "${material_evidence}"', verify_text)
        checker_text = POLICY_CHECKER.read_text(encoding="utf-8")
        self.assertIn('choices=("source-contract", "material-evidence")', checker_text)
        self.assertIn('default="source-contract"', checker_text)
        self.assertIn('choices=("historical", "live")', checker_text)
        self.assertIn('"--read-only"', checker_text)
        for required in (
            '"sgdisk", "--verify"',
            '"sfdisk", "--json"',
            "installed_package_names",
            "required_kernel_module_paths",
            "armbianEnv.txt",
            "validate_image_source_metadata",
            "M1SUPER_MATERIAL_STATUS.json",
        ):
            self.assertIn(required, checker_text)

    def test_entrypoints_reject_nonfixed_output_directory_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            rejected = Path(directory) / "不得寫入"
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(rejected)
            for entrypoint in (BUILD_ENTRY, VERIFY_ENTRY):
                result = subprocess.run(
                    [str(entrypoint)],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, entrypoint.name)
                self.assertIn("只允許固定輸出目錄", result.stderr)
                self.assertFalse(rejected.exists())

    def test_policy_cli_requires_explicit_material_evidence_source(self):
        environment = os.environ.copy()
        environment["PUBLIC_RELEASE"] = "no"
        environment["HARDWARE_CLAIMS"] = "no"

        source = subprocess.run(
            [str(POLICY_CHECKER)],
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(source.returncode, 0, source.stderr)
        self.assertIn("建置前來源契約", source.stdout)

        for arguments in (
            ["--phase", "material-evidence"],
            ["--phase", "source-contract", "--evidence-source", "live"],
        ):
            result = subprocess.run(
                [str(POLICY_CHECKER), *arguments],
                cwd=ROOT,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0, arguments)
            self.assertIn("物質驗證必須明確選擇", result.stderr)

    def test_policy_rejects_ambiguous_release_environment_values(self):
        for variable in ("PUBLIC_RELEASE", "HARDWARE_CLAIMS"):
            environment = os.environ.copy()
            environment["PUBLIC_RELEASE"] = "no"
            environment["HARDWARE_CLAIMS"] = "no"
            environment[variable] = "NO"
            result = subprocess.run(
                [str(POLICY_CHECKER)],
                cwd=ROOT,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0, variable)

    def test_common_rockchip_builder_rejects_source_commit_races(self):
        text = ROCKCHIP_BUILD.read_text(encoding="utf-8")
        for required in (
            'matrix_file="${output_dir}/CANDIDATES.tsv"',
            "候選映像矩陣包含不一致的來源提交",
            "建置期間來源提交已改變",
            "建立來源證據期間來源提交已改變",
            'source_commit="${candidate_commit}"',
        ):
            self.assertIn(required, text)

    def test_preflight_document_records_the_race_and_evidence_limits(self):
        text = PREFLIGHT_EVIDENCE.read_text(encoding="utf-8")
        for required in (
            "b78271c3fda74adcf060ac61a7f8363023a006a7eceee0661d1a511db927691a",
            "370136e6613aea22fbf46aa4effb2db86a71747668885e6deb498e3cc6551356",
            "來源提交競態",
            "L1",
            "不代表實機",
        ):
            self.assertIn(required, text)

    def test_l2_document_records_material_evidence_and_limits(self):
        text = L2_EVIDENCE.read_text(encoding="utf-8")
        for required in (
            "bc30fcb7016b3f4fb2b0888ca130646465857fe38c8041c75b4d05ea27f43324",
            "480e845023f838208f6099d29fb291a337fbd2c54aaa8a70df6a8e6252ebd9f4",
            "8c6533a10c3ec97e0565c46ef34ab857fca7d4d4",
            "L2",
            "未進行實機",
            "不得公開發布",
        ):
            self.assertIn(required, text)

    def test_component_verifier_is_evidence_bounded(self):
        text = COMPONENT_VERIFIER.read_text(encoding="utf-8")
        self.assertIn("portable_manifest_sha256", text)
        self.assertIn("不得包含原始碼或建置樹", text)
        self.assertIn("不代表完整映像、實機或公開發布通過", text)


if __name__ == "__main__":
    unittest.main()
