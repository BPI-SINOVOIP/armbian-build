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
        self.assertEqual(self.validation["candidate_level"], "L2")
        self.assertFalse(self.validation["candidate_public_release_approved"])
        self.assertFalse(self.validation["hardware_validation_complete"])
        self.assertFalse(self.validation["firmware_redistribution_audit_complete"])
        self.assertFalse(self.validation["atf_source_build_available"])
        self.assertFalse(
            self.validation["identity_evidence"]["wifi_bom_conflict_resolved"]
        )

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
            self.board["dtb_sha256"],
            "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
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
            "L2 軟體候選",
            "不得直接對外散布",
            "wifi_bom_conflict_resolved=false",
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
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
