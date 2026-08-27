#!/usr/bin/env python3
"""Banana Pi AIM7 RK3588 vendor 候選來源與契約回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapiaim7.wip"
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3588-bananapi-aim7.dts"
UBOOT_DTS = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3588-bananapi-aim7.dts"
)
UBOOT_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-aim7-rk3588_defconfig"
)
ARMSOM_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/armsom-aim7-io-rk3588_defconfig"
)
BUILD = ROOT / "tools/build-bananapi-rockchip-aim7-candidate.sh"
VERIFY = ROOT / "tools/verify-bananapi-rockchip-aim7-candidate.sh"
ISOLATED = ROOT / "tools/run-bananapi-rockchip-aim7-candidate-isolated-cache.sh"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-rockchip-aim7-source-policy-20260827.md"
)


class BananaPiRockchipAim7CandidateTests(unittest.TestCase):
    """防止 AIM7 來源、板級身分、授權與證據邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapiaim7"]
        cls.board_text = BOARD.read_text()

    def test_board_is_self_contained_and_vendor_only(self) -> None:
        self.assertNotIn(
            'source "${SRC}/config/boards/armsom-aim7-io.csc"',
            self.board_text,
        )
        for expected in (
            'BOARD_NAME="Banana Pi AIM7"',
            'BOARDFAMILY="rockchip-rk3588"',
            'BOOTCONFIG="bananapi-aim7-rk3588_defconfig"',
            'KERNEL_TARGET="vendor"',
            'KERNEL_TEST_TARGET="vendor"',
            'BOOT_FDT_FILE="rockchip/rk3588-bananapi-aim7.dtb"',
            'IMAGE_PARTITION_TABLE="gpt"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_board_pins_linux_uboot_and_rkbin(self) -> None:
        for expected in (
            'BOOTBRANCH_BOARD="commit:39cd993e5d6296635438e84f4576b3a9bf76f86e"',
            'KERNELBRANCH_BOARD="commit:c6157104418d012823413c02f9222f3fe123dd25"',
            'RKBIN_GIT_REF="commit:1d3c61008fa823936ae7a59615393f8294b64456"',
            'DDR_BLOB="rk35/rk3588_ddr_lp4_2112MHz_lp5_2400MHz_v1.20_20250926.bin"',
            'BL31_BLOB="rk35/rk3588_bl31_v1.48.elf"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        self.assertNotIn('BOOTBRANCH_BOARD="branch:', self.board_text)
        self.assertNotIn('KERNELBRANCH_BOARD="branch:', self.board_text)
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.board_text)

    def test_vendor_hook_overrides_movable_family_sources(self) -> None:
        harness = f'''
enable_extension() {{ :; }}
display_alert() {{ :; }}
SRC="{ROOT}"
BRANCH=vendor
HOSTRELEASE=jammy
source "{BOARD}"
source "{ROOT / 'config/sources/families/rockchip-rk3588.conf'}"
printf 'before_uboot=%s\n' "$BOOTBRANCH"
printf 'before_kernel=%s\n' "$KERNELBRANCH"
post_family_config_branch_vendor__bananapiaim7_pin_sources
printf 'uboot_source=%s\nuboot=%s\n' "$BOOTSOURCE" "$BOOTBRANCH"
printf 'kernel_source=%s\nkernel=%s\n' "$KERNELSOURCE" "$KERNELBRANCH"
printf 'rkbin_source=%s\nrkbin=%s\n' "$RKBIN_GIT_URL" "$RKBIN_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("before_uboot=branch:next-dev-v2024.10", result.stdout)
        self.assertIn("before_kernel=branch:rk-6.1-rkr5.1", result.stdout)
        self.assertIn(
            "uboot=commit:39cd993e5d6296635438e84f4576b3a9bf76f86e",
            result.stdout,
        )
        self.assertIn(
            "kernel=commit:c6157104418d012823413c02f9222f3fe123dd25",
            result.stdout,
        )
        self.assertIn(
            "rkbin=commit:1d3c61008fa823936ae7a59615393f8294b64456",
            result.stdout,
        )

    def test_linux_and_uboot_wrappers_only_change_identity(self) -> None:
        for path in (LINUX_DTS, UBOOT_DTS):
            text = path.read_text()
            with self.subTest(path=path):
                self.assertIn('#include "rk3588-armsom-aim7-io.dts"', text)
                self.assertIn('model = "Banana Pi AIM7";', text)
                self.assertIn('"bananapi,bpi-aim7"', text)
                self.assertIn('"armsom,aim7-io"', text)
                self.assertNotIn("status =", text)
                self.assertNotIn("num-lanes", text)

    def test_dedicated_defconfig_only_changes_board_identity(self) -> None:
        expected = ARMSOM_DEFCONFIG.read_text().replace(
            'CONFIG_DEFAULT_DEVICE_TREE="rk3588-armsom-aim7-io"\n',
            'CONFIG_DEFAULT_DEVICE_TREE="rk3588-bananapi-aim7"\n'
            'CONFIG_DEFAULT_FDT_FILE="rk3588-bananapi-aim7"\n',
        )
        self.assertEqual(UBOOT_DEFCONFIG.read_text(), expected)

    def test_validation_contract_matches_fixed_sources_and_payloads(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "vendor")
        self.assertEqual(self.config["kernel_family"], "rk35xx")
        self.assertEqual(
            self.config["linux_commit"],
            "c6157104418d012823413c02f9222f3fe123dd25",
        )
        self.assertEqual(
            self.config["rkbin_commit"],
            "1d3c61008fa823936ae7a59615393f8294b64456",
        )
        self.assertEqual(
            self.policy["uboot_revision"],
            "39cd993e5d6296635438e84f4576b3a9bf76f86e",
        )
        self.assertEqual(self.policy["uboot_defconfig"], UBOOT_DEFCONFIG.name)
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["idbloader.img@32768", "u-boot.itb@8388608"],
        )
        self.assertEqual(self.policy["partition_start_sector"], 32768)

    def test_rkbin_policy_hashes_blobs_and_installed_license(self) -> None:
        self.assertEqual(self.config["rkbin_license_path"], "LICENSE.TXT")
        self.assertTrue(self.config["rkbin_copy_and_distribution_grant_present"])
        self.assertFalse(self.config["rkbin_standalone_distribution_authorized"])
        self.assertFalse(self.config["rkbin_binary_modification_authorized"])
        self.assertTrue(self.config["rkbin_license_must_accompany_distribution"])
        self.assertIn("Rockchip 積體電路", self.config["rkbin_platform_constraint"])
        blobs = self.config["rkbin_blobs"]
        self.assertEqual(
            set(blobs),
            {
                "LICENSE.TXT",
                "rk35/rk3588_bl31_v1.48.elf",
                "rk35/rk3588_ddr_lp4_2112MHz_lp5_2400MHz_v1.20_20250926.bin",
                "rk35/rk3588_spl_loader_v1.16.113.bin",
            },
        )
        for digest in blobs.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        installed = self.config["installed_firmware_blobs"]
        self.assertEqual(
            installed["/usr/share/doc/armbian-bsp-bananapiaim7/rkbin.LICENSE.TXT"],
            blobs["LICENSE.TXT"],
        )
        self.assertIn(
            "post_family_tweaks_bsp__bananapiaim7_rkbin_license",
            self.board_text,
        )

    def test_component_evidence_is_machine_readable(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(evidence["linux_dtb_size"], 265522)
        self.assertEqual(evidence["idbloader_size"], 323584)
        self.assertEqual(evidence["uboot_spl_size"], 242776)
        self.assertEqual(evidence["uboot_dtb_size"], 10735)
        self.assertEqual(evidence["uboot_itb_size"], 1462784)
        for key, value in evidence.items():
            if key.endswith("_sha256"):
                self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_diagnostic_packages_cover_requested_interfaces(self) -> None:
        package_line = next(
            line
            for line in self.board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        board_packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= board_packages)
        for package in (
            "gpiod",
            "i2c-tools",
            "spi-tools",
            "pciutils",
            "nvme-cli",
            "libdrm-tests",
            "glmark2-es2",
            "vulkan-tools",
            "v4l-utils",
            "ffmpeg",
        ):
            with self.subTest(package=package):
                self.assertIn(package, board_packages)

    def test_kernel_contract_covers_gpu_vpu_npu_and_io(self) -> None:
        options = self.config["common_kernel_options"]
        for option in (
            "CONFIG_GPIO_CDEV",
            "CONFIG_I2C_CHARDEV",
            "CONFIG_SPI_SPIDEV",
            "CONFIG_PCIE_DW_ROCKCHIP",
            "CONFIG_DRM_ROCKCHIP",
            "CONFIG_MALI_BIFROST",
            "CONFIG_ROCKCHIP_MPP_SERVICE",
            "CONFIG_ROCKCHIP_MULTI_RGA",
            "CONFIG_ROCKCHIP_RKNPU",
            "CONFIG_USB_CONFIGFS_MASS_STORAGE",
        ):
            with self.subTest(option=option):
                self.assertEqual(options[option], "y")

        harness = f'''
opts_y=()
opts_m=()
source "{BOARD}"
custom_kernel_config__bananapiaim7_io_contract
printf '%s\n' "${{opts_y[@]}}"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        forced = set(result.stdout.split())
        for option in (
            "GPIO_CDEV",
            "I2C_CHARDEV",
            "SPI_SPIDEV",
            "PCIE_DW_ROCKCHIP",
            "MALI_BIFROST",
            "ROCKCHIP_MPP_SERVICE",
            "ROCKCHIP_MULTI_RGA",
            "ROCKCHIP_RKNPU",
            "USB_CONFIGFS_MASS_STORAGE",
        ):
            with self.subTest(forced_option=option):
                self.assertIn(option, forced)

    def test_static_topology_records_unresolved_hardware_limits(self) -> None:
        self.assertTrue(self.policy["static_topology_only"])
        self.assertFalse(self.policy["hardware_validation_completed"])
        self.assertFalse(self.config["candidate_public_release_approved"])
        self.assertIn(
            "/pcie@fe150000:num-lanes=1",
            self.policy["required_uint_properties"],
        )
        for node in (
            "/dsi@fde20000",
            "/dsi@fde30000",
            "/spi@feb00000",
            "/spi@feb10000",
        ):
            with self.subTest(node=node):
                self.assertIn(node, self.policy["required_disabled_nodes"])
        limitations = "\n".join(self.policy["known_static_limitations"])
        self.assertIn("PCIe", limitations)
        self.assertIn("不代表", limitations)

    def test_dedicated_entrypoints_are_thin_and_aim7_only(self) -> None:
        for path in (BUILD, VERIFY):
            text = path.read_text()
            with self.subTest(path=path):
                self.assertIn("bananapi-rockchip-rk3588-aim7-vendor.json", text)
                self.assertIn("bananapi-rockchip-rk3588-aim7-trixie-vendor-cli", text)
                self.assertIn('BOARDS="bananapiaim7"', text)
                self.assertNotIn("compile.sh", text)
        isolated_text = ISOLATED.read_text()
        self.assertIn("build-bananapi-rockchip-aim7-candidate.sh", isolated_text)
        self.assertIn("bananapi-rockchip-aim7-cache-overlay", isolated_text)
        self.assertNotIn("compile.sh", isolated_text)

    def test_policy_rejects_hardware_and_release_overclaim(self) -> None:
        text = POLICY.read_text()
        self.assertIn("不得宣稱硬體介面已通過", text)
        self.assertIn("不得核准候選對外發布", text)
        self.assertIn("不得獨立散布或修改", text)
        self.assertIn("必須附上相同授權文件", text)
        self.assertIn("num-lanes = 1", text)
        self.assertIn("不代表使用者空間驅動", text)


if __name__ == "__main__":
    unittest.main()
