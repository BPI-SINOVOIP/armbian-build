import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapim1super.wip"
VALIDATION = ROOT / "config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3528-bananapi-m1-super.dts"
UBOOT_DTS = ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3528-bananapi-m1-super.dts"
UBOOT_CONFIG = ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-m1-super-rk3528_defconfig"
POLICY = ROOT / "docs/evidence/bananapi-family-optimization/E-rockchip-m1super-source-policy-20260827.md"
COMPONENT_EVIDENCE = ROOT / "docs/evidence/bananapi-family-optimization/E-rockchip-m1super-component-build-20260827.md"
COMPONENT_VERIFIER = ROOT / "tools/verify-bananapi-rockchip-m1super-components.sh"
POLICY_CHECKER = ROOT / "tools/check-bananapi-rockchip-m1super-policy.py"
BUILD_ENTRY = ROOT / "tools/build-bananapi-rockchip-m1super-candidate.sh"
VERIFY_ENTRY = ROOT / "tools/verify-bananapi-rockchip-m1super-candidate.sh"
ROCKCHIP_BUILD = ROOT / "tools/build-bananapi-rockchip-candidates.sh"
PREFLIGHT_EVIDENCE = ROOT / "docs/evidence/bananapi-family-optimization/F-rockchip-m1super-L1-preflight-20260827.md"


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
        spec = importlib.util.spec_from_file_location("m1super_policy_checker", POLICY_CHECKER)
        cls.policy_checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy_checker)

    def test_board_keeps_wip_and_drops_foreign_board_file_inheritance(self):
        self.assertTrue(BOARD.is_file())
        self.assertFalse((BOARD.parent / "bananapim1super.conf").exists())
        self.assertFalse((BOARD.parent / "bananapim1super.csc").exists())
        self.assertNotIn("armsom-sige1.csc", self.board_text)
        self.assertNotIn("hinlink_rk3528_defconfig", self.board_text)
        self.assertIn('BOOTCONFIG="bananapi-m1-super-rk3528_defconfig"', self.board_text)
        self.assertIn('BOOT_FDT_FILE="rockchip/rk3528-bananapi-m1-super.dtb"', self.board_text)

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

    def test_current_state_is_honest_l1_without_image_evidence(self):
        self.policy_checker.validate_candidate_state(self.validation)
        self.assertNotIn("image_build_evidence", self.validation)
        self.assertIsNone(self.board["image_dtb_sha256"])
        self.assertEqual(self.board["dtb_sha256_evidence_scope"], "preflight-contract-l1")
        self.assertEqual(
            self.board["component_dtb_sha256"],
            "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
        )
        self.assertEqual(self.board["dtb_sha256"], self.board["component_dtb_sha256"])

    def test_state_machine_accepts_a_complete_internal_l2_shape(self):
        candidate = copy.deepcopy(self.validation)
        image_dtb_sha256 = "a" * 64
        candidate["candidate_level"] = "L2 內部軟體候選"
        candidate["candidate_scope"] = "internal-l2"
        candidate["rootfs_image_built"] = True
        candidate["image_build_evidence"] = {
            "status": "complete",
            "evidence_level": "L2",
            "full_rootfs_image_built": True,
            "hardware_tested": False,
            "read_only_content_verified": True,
            "source_commit": "1" * 40,
            "verifier_commit": "1" * 40,
            "build_validation_config_sha256": "2" * 64,
            "verification_config_sha256": "2" * 64,
            "candidate_matrix_sha256": "3" * 64,
            "uboot_payload_manifest_sha256": "4" * 64,
            "final_config_manifest_sha256": "5" * 64,
            "image": {"size": 1, "sha256": "6" * 64},
            "archive": {"size": 1, "sha256": "7" * 64},
            "linux_dtb": {
                "path": "rockchip/rk3528-bananapi-m1-super.dtb",
                "sha256": image_dtb_sha256,
            },
        }
        candidate["boards"]["bananapim1super"]["image_dtb_sha256"] = image_dtb_sha256
        candidate["boards"]["bananapim1super"]["dtb_sha256"] = image_dtb_sha256
        candidate["boards"]["bananapim1super"]["dtb_sha256_evidence_scope"] = "full-image-l2"
        candidate["boards"]["bananapim1super"]["uboot_payload_sha256"] = [
            f"idbloader.img={'8' * 64}",
            f"u-boot.itb={'9' * 64}",
        ]
        self.policy_checker.validate_candidate_state(candidate)

    def test_state_machine_rejects_mixed_or_unproven_states(self):
        mixed = copy.deepcopy(self.validation)
        mixed["candidate_scope"] = "internal-l2"
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_candidate_state(mixed)

        unproven_l2 = copy.deepcopy(self.validation)
        unproven_l2["candidate_level"] = "L2 內部軟體候選"
        unproven_l2["candidate_scope"] = "internal-l2"
        unproven_l2["rootfs_image_built"] = True
        with self.assertRaises(SystemExit):
            self.policy_checker.validate_candidate_state(unproven_l2)

        false_claim = copy.deepcopy(self.validation)
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
        self.assertEqual(
            self.board["dtb"], "rockchip/rk3528-bananapi-m1-super.dtb"
        )
        self.assertEqual(
            self.board["component_dtb_sha256"],
            "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
        )
        self.assertIsNone(self.board["image_dtb_sha256"])
        self.assertEqual(self.board["dtb_sha256"], self.board["component_dtb_sha256"])
        self.assertEqual(self.board["dtb_sha256_evidence_scope"], "preflight-contract-l1")
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
        self.assertNotIn("uboot_payload_sha256", self.board)
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
        self.assertIn("write_entry_state failed", verify_text)
        self.assertIn("verify-bananapi-rockchip-candidates.sh", verify_text)
        self.assertIn("export VERIFY_ARCHIVES=yes", verify_text)
        self.assertIn('"L1 元件候選") verification_evidence_level=L1', verify_text)
        self.assertIn('export VERIFICATION_EVIDENCE_LEVEL="${verification_evidence_level}"', verify_text)

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

    def test_component_verifier_is_evidence_bounded(self):
        text = COMPONENT_VERIFIER.read_text(encoding="utf-8")
        self.assertIn("portable_manifest_sha256", text)
        self.assertIn("不得包含原始碼或建置樹", text)
        self.assertIn("不代表完整映像、實機或公開發布通過", text)


if __name__ == "__main__":
    unittest.main()
