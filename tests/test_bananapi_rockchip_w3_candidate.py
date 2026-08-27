#!/usr/bin/env python3
"""Banana Pi W3 RK3588 vendor 候選來源與映像契約回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapiw3.wip"
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3588-w3-vendor.json"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3588-bananapi-w3.dts"
UBOOT_DTS = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3588-bananapi-w3.dts"
)
UBOOT_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-w3-rk3588_defconfig"
)
UBOOT_COMMON_PATCH = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx"
    / "rk3566-Add-rk3566-to-soc-name.patch"
)
ARMSOM_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/armsom-w3-rk3588_defconfig"
)
BUILD = ROOT / "tools/build-bananapi-rockchip-w3-candidate.sh"
VERIFY = ROOT / "tools/verify-bananapi-rockchip-w3-candidate.sh"
ISOLATED = ROOT / "tools/run-bananapi-rockchip-w3-candidate-isolated-cache.sh"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-rockchip-w3-source-policy-20260827.md"
)


class BananaPiRockchipW3CandidateTests(unittest.TestCase):
    """防止 W3 固定來源、板級身分、啟動鏈及 I/O 契約退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapiw3"]
        cls.board_text = BOARD.read_text()

    def test_board_is_self_contained_and_vendor_only(self) -> None:
        self.assertNotIn('source "${SRC}/config/boards/armsom-w3.csc"', self.board_text)
        for expected in (
            'BOARD_NAME="Banana Pi W3"',
            'BOARDFAMILY="rockchip-rk3588"',
            'BOOTCONFIG="bananapi-w3-rk3588_defconfig"',
            'KERNEL_TARGET="vendor"',
            'KERNEL_TEST_TARGET="vendor"',
            'BOOT_FDT_FILE="rockchip/rk3588-bananapi-w3.dtb"',
            'IMAGE_PARTITION_TABLE="gpt"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_board_pins_all_requested_sources(self) -> None:
        for expected in (
            'BOOTBRANCH_BOARD="commit:39cd993e5d6296635438e84f4576b3a9bf76f86e"',
            'KERNELBRANCH_BOARD="commit:c6157104418d012823413c02f9222f3fe123dd25"',
            'RKBIN_GIT_REF="commit:1d3c61008fa823936ae7a59615393f8294b64456"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'DDR_BLOB="rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.11.bin"',
            'BL31_BLOB="rk35/rk3588_bl31_v1.38.elf"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        self.assertNotIn('BOOTBRANCH_BOARD="branch:', self.board_text)
        self.assertNotIn('KERNELBRANCH_BOARD="branch:', self.board_text)
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.board_text)
        self.assertNotIn('ARMBIAN_FIRMWARE_GIT_REF_BOARD="branch:', self.board_text)

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
post_family_config_branch_vendor__bananapiw3_pin_sources
printf 'uboot_source=%s\nuboot=%s\n' "$BOOTSOURCE" "$BOOTBRANCH"
printf 'kernel_source=%s\nkernel=%s\n' "$KERNELSOURCE" "$KERNELBRANCH"
printf 'rkbin_source=%s\nrkbin=%s\n' "$RKBIN_GIT_URL" "$RKBIN_GIT_REF"
printf 'firmware=%s\n' "$ARMBIAN_FIRMWARE_GIT_REF"
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
        self.assertIn("uboot_source=https://github.com/radxa/u-boot.git", result.stdout)
        self.assertIn(
            "uboot=commit:39cd993e5d6296635438e84f4576b3a9bf76f86e",
            result.stdout,
        )
        self.assertIn(
            "kernel_source=https://github.com/armbian/linux-rockchip.git",
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
        self.assertIn(
            "firmware=commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            result.stdout,
        )

    def test_shared_uboot_patch_has_canonical_metadata(self) -> None:
        text = UBOOT_COMMON_PATCH.read_text()
        self.assertIn(
            "index 3aca2af44621481f8e7bf9ed93d6178e2ad0c653"
            "..2baee74c8b008b9a33d0b68c06dd6f5890219ba0",
            text,
        )
        self.assertIn("@@ -140,7 +140,8 @@", text)
        self.assertNotIn("111111111111", text)
        self.assertNotIn("222222222222", text)

    def test_linux_and_uboot_wrappers_identify_w3(self) -> None:
        linux_text = LINUX_DTS.read_text()
        uboot_text = UBOOT_DTS.read_text()
        for text in (linux_text, uboot_text):
            self.assertIn('#include "rk3588-armsom-w3.dts"', text)
            self.assertIn('model = "Banana Pi W3";', text)
            self.assertIn('"bananapi,bpi-w3"', text)
        self.assertIn('"armsom,w3"', linux_text)
        self.assertIn('"rockchip,rk3588-armsom-w3"', uboot_text)

    def test_dedicated_defconfig_only_changes_board_identity(self) -> None:
        expected = ARMSOM_DEFCONFIG.read_text().replace(
            'CONFIG_DEFAULT_DEVICE_TREE="rk3588-armsom-w3"\n',
            'CONFIG_DEFAULT_DEVICE_TREE="rk3588-bananapi-w3"\n'
            'CONFIG_DEFAULT_FDT_FILE="rk3588-bananapi-w3"\n',
        )
        self.assertEqual(UBOOT_DEFCONFIG.read_text(), expected)

    def test_validation_contract_matches_fixed_sources_and_payloads(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "vendor")
        self.assertEqual(self.config["kernel_family"], "rk35xx")
        self.assertEqual(self.policy["family"], "rk35xx")
        self.assertEqual(
            self.config["linux_commit"],
            "c6157104418d012823413c02f9222f3fe123dd25",
        )
        self.assertEqual(
            self.config["rkbin_commit"],
            "1d3c61008fa823936ae7a59615393f8294b64456",
        )
        self.assertEqual(
            self.config["firmware_ref"],
            "commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
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
        self.assertEqual(self.policy["partition_table"], "gpt")

    def test_rkbin_blobs_and_installed_license_are_hashed(self) -> None:
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
                "rk35/rk3588_bl31_v1.38.elf",
                "rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.11.bin",
                "rk35/rk3588_spl_loader_v1.16.113.bin",
            },
        )
        for digest in blobs.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        installed = self.config["installed_firmware_blobs"]
        self.assertEqual(
            installed["/usr/share/doc/armbian-bsp-bananapiw3/rkbin.LICENSE.TXT"],
            blobs["LICENSE.TXT"],
        )
        self.assertIn("post_family_tweaks_bsp__bananapiw3_rkbin_license", self.board_text)

    def test_io_packages_and_kernel_options_cover_w3_interfaces(self) -> None:
        package_line = next(
            line
            for line in self.board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        board_packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= board_packages)
        options = self.config["common_kernel_options"]
        for option, value in {
            "CONFIG_AP6XXX": "m",
            "CONFIG_BRCMFMAC_SDIO": "y",
            "CONFIG_BT_HCIUART_BCM": "y",
            "CONFIG_GPIO_CDEV": "y",
            "CONFIG_MALI_BIFROST": "y",
            "CONFIG_ROCKCHIP_MPP_SERVICE": "y",
            "CONFIG_ROCKCHIP_RKNPU": "y",
            "CONFIG_SPI_SPIDEV": "y",
            "CONFIG_USB_CONFIGFS_MASS_STORAGE": "y",
            "CONFIG_USB_HID": "y",
            "CONFIG_USB_ROLE_SWITCH": "y",
            "CONFIG_VIDEO_ROCKCHIP_HDMIRX": "y",
        }.items():
            with self.subTest(option=option):
                self.assertEqual(options[option], value)

    def test_dtb_contract_covers_identity_storage_and_accelerators(self) -> None:
        self.assertEqual(self.policy["model"], "Banana Pi W3")
        self.assertIn("bananapi,bpi-w3", self.policy["compatible"])
        self.assertEqual(self.policy["sd_node"], "/mmc@fe2c0000")
        self.assertEqual(self.policy["sd_bus_width"], 4)
        self.assertIn("/mmc@fe2e0000=8", self.policy["additional_bus_widths"])
        for node in (
            "/gpu@fb000000",
            "/npu@fdab0000",
            "/hdmirx-controller@fdee0000",
            "/spi@fe2b0000",
            "/usbdrd3_0/usb@fc000000",
            "/vop@fdd90000",
        ):
            with self.subTest(node=node):
                self.assertIn(node, self.policy["required_status_nodes"])
        self.assertIn(
            "/gpu-panthor@fb000000",
            self.policy["required_disabled_nodes"],
        )
        self.assertIn(
            "/usbdrd3_0/usb@fc000000:dr_mode=otg",
            self.policy["required_string_properties"],
        )

    def test_dedicated_entrypoints_are_thin_and_w3_only(self) -> None:
        for path in (BUILD, VERIFY):
            text = path.read_text()
            self.assertIn("bananapi-rockchip-rk3588-w3-vendor.json", text)
            self.assertIn("bananapi-rockchip-rk3588-w3-trixie-vendor-cli", text)
            self.assertIn('BOARDS="bananapiw3"', text)
            self.assertNotIn("compile.sh", text)
        isolated_text = ISOLATED.read_text()
        self.assertIn("build-bananapi-rockchip-w3-candidate.sh", isolated_text)
        self.assertIn("bananapi-rockchip-w3-cache-overlay", isolated_text)
        self.assertNotIn("compile.sh", isolated_text)

    def test_policy_keeps_hardware_claims_pending(self) -> None:
        text = POLICY.read_text()
        self.assertIn("目前只建立 L2 軟體候選", text)
        self.assertIn("尚未建立實體板 L3 證據", text)
        self.assertIn("不得宣稱硬體介面已通過", text)
        self.assertIn("不得獨立散布", text)
        self.assertIn("必須隨散布內容附上相同授權文件", text)


if __name__ == "__main__":
    unittest.main()
