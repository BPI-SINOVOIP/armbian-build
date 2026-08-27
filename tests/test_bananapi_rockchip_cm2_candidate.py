#!/usr/bin/env python3
"""Banana Pi CM2 搭配 R2 Pro 載板候選回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD_PATH = ROOT / "config/boards/bananapicm2.wip"
CONFIG_PATH = (
    ROOT
    / "config/validation/bananapi-rockchip-rk3568-cm2-r2pro-current.json"
)
KERNEL_DTS = (
    ROOT
    / "patch/kernel/archive/rockchip64-6.18/dt"
    / "rk3568-bpi-cm2-r2pro-carrier.dts"
)
UBOOT_PATCH = (
    ROOT
    / "patch/u-boot/v2024.01/board_bananapicm2"
    / "add-cm2-r2pro-carrier-identity.patch"
)
POLICY_PATH = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-rockchip-cm2-r2pro-source-policy-20260827.md"
)
ENTRYPOINTS = (
    ROOT / "tools/build-bananapi-rockchip-cm2-r2pro-candidate.sh",
    ROOT / "tools/verify-bananapi-rockchip-cm2-r2pro-candidate.sh",
    ROOT / "tools/run-bananapi-rockchip-cm2-r2pro-candidate-isolated-cache.sh",
)


class BananaPiRockchipCM2CandidateTests(unittest.TestCase):
    """防止模組、載板、來源、授權與驗證邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board_text = BOARD_PATH.read_text()
        cls.config = json.loads(CONFIG_PATH.read_text())
        cls.policy = cls.config["boards"]["bananapicm2"]

    def test_identity_is_cm2_module_on_r2_pro_carrier_only(self) -> None:
        self.assertIn(
            'BOARD_NAME="Banana Pi CM2 on BPI-R2 Pro carrier"',
            self.board_text,
        )
        self.assertEqual(
            self.config["candidate_identity_scope"],
            "BPI-CM2 module on BPI-R2 Pro carrier board",
        )
        self.assertFalse(self.config["generic_cm2_supported"])
        self.assertEqual(self.policy["module"], "Banana Pi BPI-CM2")
        self.assertEqual(
            self.policy["carrier"],
            "Banana Pi BPI-R2 Pro carrier board",
        )
        self.assertFalse(self.policy["generic_module_image"])

    def test_board_uses_dedicated_dtb_and_defconfig(self) -> None:
        for expected in (
            'KERNEL_TARGET="current"',
            'BOOTCONFIG="bpi-cm2-r2pro-carrier-rk3568_defconfig"',
            'BOOT_FDT_FILE="rockchip/rk3568-bpi-cm2-r2pro-carrier.dtb"',
            'SRC_CMDLINE="console=ttyS2,1500000 console=tty0"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        self.assertNotIn('BOOTCONFIG="bpi-r2-pro-rk3568_defconfig"', self.board_text)
        self.assertNotIn('BOOT_FDT_FILE="rockchip/rk3568-bpi-r2-pro.dtb"', self.board_text)

    def test_all_movable_sources_are_fixed(self) -> None:
        expected = (
            'KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"',
            'BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"',
            'RKBIN_GIT_REF="commit:46c4793ea2dcea7c8331fce9f07b5c80561a0395"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
        )
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        for movable in ('KERNELBRANCH_BOARD="branch:', 'BOOTBRANCH_BOARD="branch:', 'RKBIN_GIT_REF="branch:'):
            self.assertNotIn(movable, self.board_text)

    def test_current_hook_overrides_family_sources(self) -> None:
        harness = f'''
SRC="{ROOT}"
BRANCH=current
KERNELSOURCE=movable-kernel
KERNELBRANCH=branch:movable-kernel
BOOTSOURCE=movable-uboot
BOOTBRANCH=branch:movable-uboot
ARMBIAN_FIRMWARE_GIT_REF=branch:movable-firmware
source "{BOARD_PATH}"
post_family_config_branch_current__bananapicm2_r2pro_pin_sources
printf 'kernel_source=%s\\nkernel=%s\\nuboot_source=%s\\nuboot=%s\\nfirmware=%s\\n' \\
    "$KERNELSOURCE" "$KERNELBRANCH" "$BOOTSOURCE" "$BOOTBRANCH" \\
    "$ARMBIAN_FIRMWARE_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "kernel_source=https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
            result.stdout,
        )
        self.assertIn(
            "kernel=commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5",
            result.stdout,
        )
        self.assertIn("uboot_source=https://github.com/u-boot/u-boot", result.stdout)
        self.assertIn(
            "uboot=commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e",
            result.stdout,
        )

    def test_rkbin_license_and_blobs_are_gated(self) -> None:
        blobs = self.config["rkbin_blobs"]
        self.assertEqual(len(blobs), 4)
        self.assertEqual(
            blobs["LICENSE.TXT"],
            "0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c",
        )
        for digest in blobs.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertFalse(self.config["rkbin_standalone_redistribution_authorized"])
        self.assertTrue(self.config["rkbin_distribution_review_required"])
        self.assertEqual(
            self.config["installed_file_sha256"][
                "/usr/share/doc/armbian-bsp-bananapicm2/rkbin.LICENSE.TXT"
            ],
            blobs["LICENSE.TXT"],
        )
        self.assertIn(
            "post_family_tweaks_bsp__bananapicm2_r2pro_rkbin_license",
            self.board_text,
        )

    def test_linux_dts_expresses_module_and_carrier(self) -> None:
        text = KERNEL_DTS.read_text()
        self.assertIn('#include "rk3568-bpi-r2-pro.dts"', text)
        self.assertIn(
            'model = "Banana Pi CM2 module on BPI-R2 Pro carrier board";',
            text,
        )
        self.assertIn('"sinovoip,rk3568-bpi-cm2-r2pro-carrier"', text)
        self.assertEqual(
            self.policy["dtb"],
            "rockchip/rk3568-bpi-cm2-r2pro-carrier.dtb",
        )
        self.assertNotIn("dtb_sha256", self.policy)

    def test_uboot_patch_has_dedicated_identity_and_full_indices(self) -> None:
        text = UBOOT_PATCH.read_text()
        for expected in (
            "rk3568-bpi-cm2-r2pro-carrier.dts",
            "bpi-cm2-r2pro-carrier-rk3568_defconfig",
            'CONFIG_DEFAULT_DEVICE_TREE="rk3568-bpi-cm2-r2pro-carrier"',
            'CONFIG_DEFAULT_FDT_FILE="rockchip/rk3568-bpi-cm2-r2pro-carrier.dtb"',
            'CONFIG_SYS_PROMPT="BPI-CM2-R2PRO> "',
            "index 9d28a485bec6d4e8dbe8967c4d3d9fed271117cf..aadc62c2687252b1468e3b454a927915cd72ae7f",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn("index 111111111111..222222222222", text)
        self.assertEqual(
            self.policy["uboot_defconfig"],
            "bpi-cm2-r2pro-carrier-rk3568_defconfig",
        )
        self.assertEqual(
            self.policy["uboot_binary_for_string_checks"],
            "u-boot.itb",
        )

    def test_carrier_io_contract_matches_declared_scope(self) -> None:
        self.assertEqual(self.policy["sd_node"], "/mmc@fe2b0000")
        self.assertIn("/mmc@fe310000=8", self.policy["additional_bus_widths"])
        self.assertIn(
            "/ethernet@fe2a0000/mdio/switch@1f:compatible=mediatek,mt7531",
            self.policy["required_string_properties"],
        )
        self.assertIn(
            "/usb@fcc00000:dr_mode=host",
            self.policy["required_string_properties"],
        )
        self.assertNotIn(
            "/usb@fcc00000:dr_mode=otg",
            self.policy["required_string_properties"],
        )

    def test_packages_cover_declared_validation_tools(self) -> None:
        package_line = next(
            line
            for line in self.board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)

    def test_dedicated_entrypoints_select_only_cm2_r2pro(self) -> None:
        for path in ENTRYPOINTS:
            with self.subTest(path=path.name):
                self.assertTrue(path.stat().st_mode & 0o111)
                subprocess.run(["bash", "-n", str(path)], check=True)
                text = path.read_text()
                self.assertIn("cm2-r2pro", text)
        for path in ENTRYPOINTS[:2]:
            self.assertIn('BOARDS="bananapicm2"', path.read_text())
            self.assertIn(
                "bananapi-rockchip-rk3568-cm2-r2pro-current.json",
                path.read_text(),
            )
        self.assertIn(
            "bananapi-rockchip-cm2-r2pro-cache-overlay",
            ENTRYPOINTS[2].read_text(),
        )

    def test_policy_blocks_generic_and_premature_l2_claims(self) -> None:
        text = POLICY_PATH.read_text()
        self.assertIn("不是通用 BPI-CM2 映像", text)
        self.assertIn("目前是 L2 待建候選", text)
        self.assertIn("沒有執行完整映像建置", text)
        self.assertIn("禁止獨立散布", text)
        self.assertIn(
            "463735988e09f1a7dc4a919c8b04043fda4b6980cf45a86b4a28b4b0536d0027",
            text,
        )
        self.assertIn("本候選沒有設定 OP-TEE", text)
        self.assertIn("任何其他 CM2 載板都必須建立自己的 DTS", text)


if __name__ == "__main__":
    unittest.main()
