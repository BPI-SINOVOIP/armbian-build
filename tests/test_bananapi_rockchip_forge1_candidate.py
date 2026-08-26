#!/usr/bin/env python3
"""Banana Pi BPI-Forge1 RK3506J 候選來源與映像契約回歸測試。"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapiforge1.wip"
BOOT_SCRIPT = ROOT / "config/bootscripts/boot-rk3506-forge1.cmd"
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3506-forge1-vendor.json"
UBOOT_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-rk3506/forge1-bpi-identity-and-boot.patch"
)
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/bananapiforge1-vendor/001-identify-bananapi-forge1.patch"
)
RUNNER = ROOT / "tools/run-bananapi-rockchip-forge1-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-rockchip-forge1-candidate.sh"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-rockchip-forge1-source-policy-20260827.md"
)


class BananaPiRockchipForge1CandidateTests(unittest.TestCase):
    """防止 Forge1 固定來源、啟動鏈、授權及 I/O 契約退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.board = cls.config["boards"]["bananapiforge1"]
        cls.board_text = BOARD.read_text()

    def test_board_is_self_contained_and_sources_are_fixed(self) -> None:
        self.assertNotIn("armsom-forge1.csc", self.board_text)
        for expected in (
            'BOOTCONFIG="forge1-rk3506j_defconfig"',
            'KERNEL_TEST_TARGET="vendor"',
            'SERIALCON="ttyFIQ0:1500000"',
            'BOOTBRANCH="commit:a72ec1294fc6ba6b0bfd5ebc912a7bed2dc2513d"',
            'KERNELBRANCH="commit:c6157104418d012823413c02f9222f3fe123dd25"',
            'RKBIN_GIT_REF="commit:1d3c61008fa823936ae7a59615393f8294b64456"',
            'KERNELPATCHDIR="archive/bananapiforge1-vendor"',
            'BOOTSCRIPT="boot-rk3506-forge1.cmd:boot.cmd"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_validation_sources_match_board_contract(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "vendor")
        self.assertEqual(self.config["kernel_family"], "rockchip")
        self.assertEqual(
            self.config["linux_commit"],
            "c6157104418d012823413c02f9222f3fe123dd25",
        )
        self.assertEqual(
            self.config["rkbin_commit"],
            "1d3c61008fa823936ae7a59615393f8294b64456",
        )
        self.assertEqual(
            self.board["uboot_revision"],
            "a72ec1294fc6ba6b0bfd5ebc912a7bed2dc2513d",
        )
        self.assertEqual(self.board["uboot_defconfig"], "forge1-rk3506j_defconfig")

    def test_rkbin_blobs_and_license_use_full_source_hashes(self) -> None:
        expected = {
            "LICENSE.TXT": (
                "0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c"
            ),
            "rk35/rk3506b_ddr_750MHz_v1.06.bin": (
                "14a607be903eff6c0984cdbeda77e7ce2963afad74aa900cad17149ec3fc65a7"
            ),
            "rk35/rk3506_tee_v2.10.bin": (
                "93603ca22cdf22e47ac130e4ac386cdf9474443ab076039807dfc2d5d30b7ecd"
            ),
        }
        self.assertEqual(self.config["rkbin_blobs"], expected)
        for digest in expected.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.config["installed_firmware_blobs"][
                "/usr/share/doc/armbian-bsp-bananapiforge1/rkbin.LICENSE.TXT"
            ],
            expected["LICENSE.TXT"],
        )
        self.assertIn("post_family_tweaks_bsp__bananapiforge1_rkbin_license", self.board_text)

    def test_boot_script_uses_fiq_console_and_vendor_kernel_dtb(self) -> None:
        text = BOOT_SCRIPT.read_text()
        self.assertNotIn("ttyS2", text)
        self.assertIn("console=ttyFIQ0,1500000n8", text)
        self.assertIn('setenv fdtfile "rk3506b-bananapi-forge1.dtb"', text)
        self.assertIn("bootz ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}", text)

    def test_uboot_payload_and_partition_reservation_are_explicit(self) -> None:
        self.assertEqual(self.board["uboot_payloads"], ["u-boot-rockchip.bin@32768"])
        self.assertEqual(
            self.board["uboot_binary_for_string_checks"],
            "u-boot-rockchip.bin",
        )
        self.assertEqual(self.board["partition_start_sector"], 32768)
        self.assertEqual(self.board["logical_sector_size"], 512)
        self.assertEqual(self.board["partition_table"], "gpt")
        self.assertIn(
            "rk35/rk3506b_ddr_750MHz_v1.06.bin",
            self.board["uboot_target_make_contains"],
        )
        self.assertIn(
            "rk35/rk3506_tee_v2.10.bin",
            self.board["uboot_target_make_contains"],
        )

    def test_usb_hid_and_common_io_tools_are_required(self) -> None:
        options = self.config["common_kernel_options"]
        for option, value in {
            "CONFIG_GPIO_CDEV": "y",
            "CONFIG_HID": "y",
            "CONFIG_HID_GENERIC": "y",
            "CONFIG_HIDRAW": "y",
            "CONFIG_USB": "y",
            "CONFIG_USB_CONFIGFS": "y",
            "CONFIG_USB_CONFIGFS_MASS_STORAGE": "y",
            "CONFIG_USB_DWC2": "y",
            "CONFIG_USB_GADGET": "y",
            "CONFIG_USB_HID": "y",
            "CONFIG_USB_STORAGE": "m",
            "CONFIG_I2C_CHARDEV": "y",
            "CONFIG_SPI_SPIDEV": "y",
        }.items():
            with self.subTest(option=option):
                self.assertEqual(options[option], value)
        for package in (
            "gpiod",
            "i2c-tools",
            "python3-spidev",
            "spi-tools",
            "usbutils",
            "usb-modeswitch",
            "evtest",
        ):
            with self.subTest(package=package):
                self.assertIn(package, self.config["common_packages"])

    def test_board_does_not_claim_onboard_wireless(self) -> None:
        packages = set(self.config["common_packages"])
        self.assertTrue(packages.isdisjoint({"bluez", "bluetooth", "iw", "rfkill"}))
        self.assertNotIn("firmware_source", self.config)
        self.assertNotIn("ARMBIAN_FIRMWARE", self.board_text)

    def test_board_identity_patches_are_dedicated(self) -> None:
        uboot_text = UBOOT_PATCH.read_text()
        kernel_text = KERNEL_PATCH.read_text()
        for text in (uboot_text, kernel_text):
            self.assertIn('model = "Banana Pi BPI-Forge1";', text)
            self.assertIn('"bananapi,bpi-forge1"', text)
            subject = re.search(r"^Subject: (.+)$", text, re.MULTILINE)
            self.assertIsNotNone(subject)
            self.assertTrue(subject.group(1).isascii())
        self.assertIn("forge1-rk3506j_defconfig", uboot_text)
        self.assertIn("rk3506j-bananapi-forge1.dts", uboot_text)
        self.assertIn("rk3506b-bananapi-forge1.dts", kernel_text)
        self.assertIn("CONFIG_CMD_BTRFS=y", uboot_text)

    def test_dedicated_entrypoints_select_only_forge1(self) -> None:
        for path in (RUNNER, VERIFIER):
            text = path.read_text()
            self.assertIn("bananapi-rockchip-rk3506-forge1-vendor.json", text)
            self.assertIn("bananapi-rockchip-rk3506-forge1-trixie-vendor-cli", text)
            self.assertIn('BOARDS="bananapiforge1"', text)
        self.assertIn("bananapi-rockchip-forge1-cache-overlay", RUNNER.read_text())

    def test_source_policy_marks_build_and_hardware_as_pending(self) -> None:
        text = POLICY.read_text()
        self.assertIn("尚未執行完整映像建置", text)
        self.assertIn("不得宣稱已達 L2", text)
        self.assertIn("板上沒有 Wi-Fi／Bluetooth", text)


if __name__ == "__main__":
    unittest.main()
