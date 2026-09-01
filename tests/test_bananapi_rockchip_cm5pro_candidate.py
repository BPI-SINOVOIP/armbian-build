#!/usr/bin/env python3
"""Banana Pi CM5 Pro 固定來源與內部候選守門回歸測試。"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapicm5pro.wip"
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3576-cm5pro-vendor.json"
KERNEL_DTS = (
    ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3576-bananapi-cm5-pro.dts"
)
UBOOT_DTS = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt"
    / "rk3576-bananapi-cm5-pro.dts"
)
UBOOT_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig"
    / "bananapi-cm5-pro-rk3576_defconfig"
)
DONOR_UBOOT_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig"
    / "armsom-cm5-io-rk3576_defconfig"
)
BUILDER = ROOT / "tools/build-bananapi-rockchip-cm5pro-candidate.sh"
RUNNER = ROOT / "tools/run-bananapi-rockchip-cm5pro-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-rockchip-cm5pro-candidate.sh"
COMPONENT_EXPORTER = ROOT / "tools/export-bananapi-rockchip-cm5pro-components.sh"
COMPONENT_VERIFIER = ROOT / "tools/verify-bananapi-rockchip-cm5pro-components.sh"
ROCKCHIP_BUILDER = ROOT / "tools/build-bananapi-rockchip-candidates.sh"
ROCKCHIP_VERIFIER = ROOT / "tools/verify-bananapi-rockchip-candidates.sh"
DRIVER_HARNESS = ROOT / "lib/functions/compilation/patch/drivers_network.sh"
DRIVER_COMPAT_PATCH = ROOT / "patch/misc/wireless-rtl8852bs-fixed-source-6.1.patch"
DRIVER_CACHE = ROOT / "lib/functions/compilation/patch/drivers-harness.sh"
KERNEL_ARTIFACT = ROOT / "lib/functions/artifacts/artifact-kernel.sh"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-rockchip-cm5pro-source-policy-20260827.md"
)
L2_EVIDENCE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-rockchip-cm5pro-L2-build-20260827.md"
)


class BananaPiRockchipCm5ProCandidateTests(unittest.TestCase):
    """防止 CM5 Pro 身分、來源、授權與介面契約退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board_text = BOARD.read_text()
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapicm5pro"]

    def test_board_remains_wip_and_is_self_contained(self) -> None:
        self.assertTrue(BOARD.is_file())
        self.assertFalse(BOARD.with_suffix(".conf").exists())
        self.assertNotIn("source ", self.board_text)
        self.assertNotIn("armsom-cm5-io.csc", self.board_text)
        for expected in (
            'BOARD_NAME="Banana Pi CM5 Pro"',
            'BOARDFAMILY="rk35xx"',
            'BOOTCONFIG="bananapi-cm5-pro-rk3576_defconfig"',
            'KERNEL_TEST_TARGET="vendor"',
            'BOOT_FDT_FILE="rockchip/rk3576-bananapi-cm5-pro.dtb"',
            'BOOT_SCENARIO="spl-blobs"',
            'IMAGE_PARTITION_TABLE="gpt"',
            'EXTRAWIFI="yes"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_all_external_sources_are_fixed(self) -> None:
        expected = {
            "linux_commit": "c6157104418d012823413c02f9222f3fe123dd25",
            "rkbin_commit": "1d3c61008fa823936ae7a59615393f8294b64456",
            "firmware_commit": "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            "wifi_driver_commit": "35d3e2660fd912c36777cc50dd43b3fbc805d56a",
        }
        for field, revision in expected.items():
            with self.subTest(field=field):
                self.assertEqual(self.config[field], revision)
                self.assertIn(f"commit:{revision}", self.board_text)
        self.assertEqual(
            self.policy["uboot_revision"],
            "39cd993e5d6296635438e84f4576b3a9bf76f86e",
        )
        self.assertIn(
            'BOOTBRANCH_BOARD="commit:39cd993e5d6296635438e84f4576b3a9bf76f86e"',
            self.board_text,
        )
        self.assertNotIn('BRANCH_BOARD="branch:', self.board_text)

    def test_vendor_hook_overrides_family_sources(self) -> None:
        harness = f'''
display_alert() {{ :; }}
SRC="{ROOT}"
BRANCH=vendor
BOOT_SOC=rk3576
HOSTRELEASE=jammy
source "{BOARD}"
source "{ROOT / 'config/sources/families/rk35xx.conf'}"
post_family_config_branch_vendor__bananapicm5pro_pin_sources
printf 'linux_source=%s\nlinux=%s\nuboot_source=%s\nuboot=%s\nrkbin=%s\nfirmware_source=%s\nfirmware=%s\nwifi_source=%s\nwifi=%s\n' \
    "$KERNELSOURCE" "$KERNELBRANCH" "$BOOTSOURCE" "$BOOTBRANCH" \
    "$RKBIN_GIT_REF" "$ARMBIAN_FIRMWARE_GIT_SOURCE" \
    "$ARMBIAN_FIRMWARE_GIT_REF" "$RTL8852BS_GIT_SOURCE" \
    "$RTL8852BS_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in (
            "linux_source=https://github.com/armbian/linux-rockchip.git",
            "linux=commit:c6157104418d012823413c02f9222f3fe123dd25",
            "uboot_source=https://github.com/radxa/u-boot.git",
            "uboot=commit:39cd993e5d6296635438e84f4576b3a9bf76f86e",
            "rkbin=commit:1d3c61008fa823936ae7a59615393f8294b64456",
            "firmware_source=https://github.com/armbian/firmware",
            "firmware=commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            "wifi_source=https://github.com/armbian/wifi-rtl8852bs.git",
            "wifi=commit:35d3e2660fd912c36777cc50dd43b3fbc805d56a",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, result.stdout)

    def test_banana_pi_dts_wrappers_are_dedicated(self) -> None:
        kernel_text = KERNEL_DTS.read_text()
        uboot_text = UBOOT_DTS.read_text()
        for text in (kernel_text, uboot_text):
            self.assertIn('#include "rk3576-armsom-cm5-io.dts"', text)
            self.assertIn('model = "Banana Pi CM5 Pro";', text)
            self.assertIn('"bananapi,bpi-cm5-pro"', text)
            self.assertIn('"rockchip,rk3576"', text)
        self.assertIn("&i2c3", kernel_text)
        self.assertIn("&spi3", kernel_text)
        self.assertIn("spidev@0", kernel_text)
        self.assertIn("spi-max-frequency = <50000000>;", kernel_text)

    def test_uboot_defconfig_only_changes_board_identity(self) -> None:
        donor = DONOR_UBOOT_DEFCONFIG.read_text()
        expected = donor.replace(
            'CONFIG_DEFAULT_DEVICE_TREE="rk3576-armsom-cm5-io"\n',
            'CONFIG_DEFAULT_DEVICE_TREE="rk3576-bananapi-cm5-pro"\n'
            'CONFIG_DEFAULT_FDT_FILE="rk3576-bananapi-cm5-pro"\n',
        ).replace(
            'CONFIG_ROCKCHIP_EARLY_DISTRO_DTB_PATH="/boot/dtb/rockchip/'
            'rk3576-armsom-cm5-io.dtb"',
            'CONFIG_ROCKCHIP_EARLY_DISTRO_DTB_PATH="/boot/dtb/rockchip/'
            'rk3576-bananapi-cm5-pro.dtb"',
        )
        self.assertEqual(UBOOT_DEFCONFIG.read_text(), expected)

    def test_l2_image_does_not_enable_release_or_hardware_claims(self) -> None:
        self.assertEqual(self.config["candidate_scope"], "internal-full-image")
        self.assertEqual(self.config["candidate_level"], "L2 軟體候選")
        self.assertEqual(self.config["evidence_level"], "L2")
        self.assertTrue(self.config["component_build_completed"])
        self.assertTrue(self.config["rootfs_image_built"])
        self.assertTrue(self.config["full_image_verified"])
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertFalse(self.config["banana_pi_carrier_equivalence_verified"])
        self.assertFalse(self.config["banana_pi_dedicated_base_dts_present"])
        self.assertFalse(self.policy["donor_hardware_equivalence_verified"])
        self.assertGreaterEqual(len(self.config["public_release_blockers"]), 3)

    def test_rkbin_and_firmware_boundaries_are_machine_readable(self) -> None:
        self.assertFalse(self.config["rkbin_standalone_distribution_authorized"])
        self.assertFalse(self.config["rkbin_binary_modification_authorized"])
        self.assertTrue(self.config["rkbin_license_must_accompany_distribution"])
        self.assertFalse(self.config["firmware_per_file_license_evidence_present"])
        self.assertFalse(self.config["firmware_redistribution_authorized"])
        self.assertTrue(self.config["wifi_driver_is_out_of_tree"])
        self.assertFalse(self.config["wifi_driver_upstream_audited"])
        for manifest in (
            self.config["rkbin_blobs"],
            self.config["installed_firmware_blobs"],
            self.config["installed_file_sha256"],
            self.config["wifi_driver_files"],
        ):
            for path, digest in manifest.items():
                with self.subTest(path=path):
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn(
            "post_family_tweaks_bsp__bananapicm5pro_licenses",
            self.board_text,
        )
        harness = DRIVER_HARNESS.read_text()
        self.assertIn("RTL8852BS_GIT_SOURCE", harness)
        self.assertIn("RTL8852BS_GIT_REF", harness)
        default_revision = "58840d11af91d0b72bc830980b4aff740a37b5e3"
        self.assertIn(default_revision, harness)
        for path in (DRIVER_CACHE, KERNEL_ARTIFACT):
            text = path.read_text()
            self.assertIn("RTL8852BS_GIT_SOURCE", text)
            self.assertIn("RTL8852BS_GIT_REF", text)
            self.assertIn(default_revision, text)
        self.assertIn(self.config["wifi_driver_commit"], self.board_text)

    def test_fixed_wifi_driver_supports_linux_6_1(self) -> None:
        harness = DRIVER_HARNESS.read_text()
        patch = DRIVER_COMPAT_PATCH.read_text()
        self.assertIn(DRIVER_COMPAT_PATCH.name, harness)
        self.assertIn(
            "(LINUX_VERSION_CODE < KERNEL_VERSION(6, 1, 0))",
            patch,
        )
        self.assertIn(
            "rtl8852bs/os_dep/linux/wifi_regd.c",
            patch,
        )
        self.assertIn("#define RTW_WARN_LMT(x,...)", patch)
        self.assertIn("rtl8852bs/include/rtw_debug.h", patch)

    def test_io_accelerator_and_diagnostic_contract_is_complete(self) -> None:
        packages = set(self.config["common_packages"])
        for package in (
            "gpiod",
            "i2c-tools",
            "python3-spidev",
            "spi-tools",
            "pciutils",
            "nvme-cli",
            "smartmontools",
            "hdparm",
            "mesa-utils",
            "glmark2-es2",
            "vainfo",
            "vulkan-tools",
            "clinfo",
            "libdrm-tests",
            "v4l-utils",
            "ffmpeg",
            "gstreamer1.0-tools",
        ):
            with self.subTest(package=package):
                self.assertIn(package, packages)
        options = self.config["common_kernel_options"]
        for option in (
            "CONFIG_GPIO_CDEV",
            "CONFIG_I2C_RK3X",
            "CONFIG_SPI_ROCKCHIP",
            "CONFIG_MMC_DW_ROCKCHIP",
            "CONFIG_MMC_SDHCI_OF_DWCMSHC",
            "CONFIG_PCIE_DW_ROCKCHIP",
            "CONFIG_DRM_ROCKCHIP",
            "CONFIG_MALI_BIFROST",
            "CONFIG_ROCKCHIP_MPP_SERVICE",
            "CONFIG_ROCKCHIP_MULTI_RGA",
            "CONFIG_ROCKCHIP_RKNPU",
        ):
            with self.subTest(option=option):
                self.assertEqual(options[option], "y")

    def test_dtb_and_boot_contract_is_exact(self) -> None:
        self.assertEqual(
            self.policy["dtb_sha256"],
            "399683fe7447c160f5e4255309a59f133c5427dc86eab60a93d61a1aab65aee8",
        )
        self.assertEqual(self.policy["dtb"], "rockchip/rk3576-bananapi-cm5-pro.dtb")
        self.assertEqual(self.policy["model"], "Banana Pi CM5 Pro")
        self.assertEqual(self.policy["partition_table"], "gpt")
        self.assertEqual(self.policy["partition_start_sector"], 32768)
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["idbloader.img@32768", "u-boot.itb@8388608"],
        )
        for node in (
            "/mmc@2a310000",
            "/mmc@2a330000",
            "/i2c@2ac60000",
            "/spi@2ad20000/spidev@0",
            "/pcie@2a200000",
            "/gpu@27800000",
            "/npu@27700000",
            "/rkvdec@27b00000",
            "/rkvenc-core@27a00000",
            "/hdmi@27da0000",
            "/dp@27e40000",
        ):
            with self.subTest(node=node):
                self.assertIn(node, self.policy["required_status_nodes"])

    def test_dedicated_entrypoints_are_isolated_and_read_only(self) -> None:
        for path in (BUILDER, VERIFIER):
            text = path.read_text()
            self.assertIn("bananapi-rockchip-rk3576-cm5pro-vendor.json", text)
            self.assertIn("bananapi-rockchip-rk3576-cm5pro-trixie-vendor-cli", text)
            self.assertIn('BOARDS="bananapicm5pro"', text)
        self.assertIn("build-bananapi-rockchip-cm5pro-candidate.sh", RUNNER.read_text())
        self.assertIn("bananapi-rockchip-cm5pro-cache-overlay", RUNNER.read_text())
        self.assertIn("verify-bananapi-rockchip-candidates.sh", VERIFIER.read_text())
        self.assertIn("WIFI_DRIVER_EVIDENCE.tsv", ROCKCHIP_BUILDER.read_text())
        self.assertIn("WIFI_DRIVER_STATUS.json", ROCKCHIP_BUILDER.read_text())
        self.assertIn("WIFI_DRIVER_EVIDENCE.tsv", ROCKCHIP_VERIFIER.read_text())
        self.assertIn("wifi_driver_manifest_sha256", ROCKCHIP_VERIFIER.read_text())

        evidence = self.config["component_build_evidence"]
        self.assertEqual(evidence["portable_artifact_count"], 10)
        self.assertRegex(evidence["portable_manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(evidence["artifacts"]), 10)
        for item in evidence["artifacts"].values():
            self.assertGreater(item["size"], 0)
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
        for path in (COMPONENT_EXPORTER, COMPONENT_VERIFIER):
            self.assertTrue(path.is_file())
            self.assertIn("component_build_evidence", path.read_text())

    def test_policy_does_not_promote_static_results_to_hardware_evidence(self) -> None:
        text = POLICY.read_text()
        self.assertIn("完整 L2 軟體候選", text)
        self.assertIn("不證明任何周邊或加速器功能", text)
        self.assertIn("板檔保留 `.wip`", text)
        self.assertIn("完整根檔案系統映像已通過", text)

        image = self.config["full_image_evidence"]
        self.assertEqual(image["raw_size"], 2403336192)
        self.assertEqual(image["xz_size"], 473269684)
        for field in (
            "source_commit",
            "verifier_commit",
            "raw_sha256",
            "xz_sha256",
            "uboot_payload_manifest_sha256",
        ):
            self.assertRegex(image[field], r"^[0-9a-f]{40}$" if "commit" in field else r"^[0-9a-f]{64}$")
        evidence_text = L2_EVIDENCE.read_text()
        self.assertIn(image["raw_sha256"], evidence_text)
        self.assertIn(image["xz_sha256"], evidence_text)
        self.assertIn("不代表實體板已開機", evidence_text)


if __name__ == "__main__":
    unittest.main()
