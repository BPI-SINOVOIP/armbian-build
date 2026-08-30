#!/usr/bin/env python3
"""Banana Pi Rockchip 板級來源政策回歸測試。"""

from pathlib import Path
import os
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
RKBIN_COMMIT = "46c4793ea2dcea7c8331fce9f07b5c80561a0395"
M7_RKBIN_COMMIT = "1d3c61008fa823936ae7a59615393f8294b64456"
M7_UBOOT_COMMIT = "39cd993e5d6296635438e84f4576b3a9bf76f86e"
M5PRO_LINUX_COMMIT = "458c6079fc1d41d564c37679c8ace02cd83ee817"
M5PRO_UBOOT_COMMIT = "39cd993e5d6296635438e84f4576b3a9bf76f86e"
M5PRO_RKBIN_COMMIT = "1d3c61008fa823936ae7a59615393f8294b64456"
R2PRO_LINUX_COMMIT = "1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"
R2PRO_UBOOT_COMMIT = "866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"
R2PRO_RKBIN_COMMIT = "46c4793ea2dcea7c8331fce9f07b5c80561a0395"
IO_PACKAGES = {
    "gpiod",
    "i2c-tools",
    "python3-libgpiod",
    "python3-spidev",
    "v4l-utils",
}
RADIO_PACKAGES = {"rfkill", "bluetooth", "bluez", "bluez-tools"}


class BananaPiRockchipSourcePolicyTests(unittest.TestCase):
    """防止 P2 Pro 的啟動二進位來源與基本工具政策倒退。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = (ROOT / "config/boards/bananapip2pro.wip").read_text()
        cls.m7_board = (ROOT / "config/boards/bananapim7.conf").read_text()
        cls.m5pro_board = (ROOT / "config/boards/bananapim5pro.conf").read_text()
        cls.r2pro_board = (ROOT / "config/boards/bananapir2pro.csc").read_text()
        cls.extension = (ROOT / "extensions/rkbin-tools.sh").read_text()

    def test_rkbin_extension_accepts_an_immutable_ref(self) -> None:
        self.assertIn("RKBIN_GIT_REF", self.extension)
        self.assertIn(
            'RKBIN_GIT_REF:-branch:${RKBIN_GIT_BRANCH:-master}',
            self.extension,
        )

    def test_rkbin_ref_is_passed_to_fetch_from_repo(self) -> None:
        cases = (
            ({}, "branch:master"),
            ({"RKBIN_GIT_BRANCH": "vendor"}, "branch:vendor"),
            (
                {"RKBIN_GIT_REF": "", "RKBIN_GIT_BRANCH": "fallback"},
                "branch:fallback",
            ),
            ({"RKBIN_GIT_REF": f"commit:{RKBIN_COMMIT}"}, f"commit:{RKBIN_COMMIT}"),
            (
                {
                    "RKBIN_GIT_REF": f"commit:{RKBIN_COMMIT}",
                    "RKBIN_GIT_BRANCH": "ignored",
                },
                f"commit:{RKBIN_COMMIT}",
            ),
        )
        harness = f'''
fetch_from_repo() {{ checked_out_revision=0123456789abcdef0123456789abcdef01234567; printf '%s\\n' "$3"; }}
source "{ROOT / 'extensions/rkbin-tools.sh'}"
fetch_sources_tools__rkbin_tools
'''
        for variables, expected in cases:
            with self.subTest(variables=variables):
                environment = os.environ.copy()
                environment.pop("RKBIN_GIT_REF", None)
                environment.pop("RKBIN_GIT_BRANCH", None)
                environment.update(variables)
                result = subprocess.run(
                    ["bash", "-c", harness],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_p2_pro_pins_rkbin_commit(self) -> None:
        self.assertIn(
            f'RKBIN_GIT_REF="commit:{RKBIN_COMMIT}"',
            self.board,
        )
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.board)
        match = re.search(r'RKBIN_GIT_REF="(commit:[0-9a-f]+)"', self.board)
        self.assertIsNotNone(match)
        self.assertRegex(match.group(1), r"^commit:[0-9a-f]{40}$")

    def test_p2_pro_uses_expected_rk3308_blobs(self) -> None:
        self.assertIn(
            'DDR_BLOB="rk33/rk3308_ddr_589MHz_uart2_m1_v1.30.bin"',
            self.board,
        )
        self.assertIn(
            'BL31_BLOB="rk33/rk3308_bl31_v2.26.elf"',
            self.board,
        )
        self.assertIn(
            'MINILOADER_BLOB="rk33/rk3308_miniloader_sd_nand_v1.13.bin"',
            self.board,
        )

    def test_p2_pro_includes_standard_io_and_radio_packages(self) -> None:
        package_line = next(
            line
            for line in self.board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(IO_PACKAGES <= packages)
        self.assertTrue(RADIO_PACKAGES <= packages)

    def test_m7_pins_uboot_and_rkbin_commits(self) -> None:
        self.assertIn(f'BOOTBRANCH_BOARD="commit:{M7_UBOOT_COMMIT}"', self.m7_board)
        self.assertIn(f'RKBIN_GIT_REF="commit:{M7_RKBIN_COMMIT}"', self.m7_board)
        self.assertNotIn('BOOTBRANCH_BOARD="branch:', self.m7_board)
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.m7_board)

    def test_m7_preserves_validated_rk3588_blobs(self) -> None:
        self.assertIn(
            'DDR_BLOB="rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.11.bin"',
            self.m7_board,
        )
        self.assertIn('BL31_BLOB="rk35/rk3588_bl31_v1.38.elf"', self.m7_board)

    def test_m7_current_hook_overrides_family_branch(self) -> None:
        harness = f'''
enable_extension() {{ :; }}
display_alert() {{ :; }}
SRC="{ROOT}"
BRANCH=current
source "{ROOT / 'config/boards/bananapim7.conf'}"
source "{ROOT / 'config/sources/families/rockchip-rk3588.conf'}"
printf 'before=%s\\n' "$BOOTBRANCH"
post_family_config_branch_current__bananapim7_pin_uboot
printf 'after=%s\\n' "$BOOTBRANCH"
printf 'ddr=%s\\nbl31=%s\\n' "$DDR_BLOB" "$BL31_BLOB"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("before=branch:next-dev-v2024.10", result.stdout)
        self.assertIn(f"after=commit:{M7_UBOOT_COMMIT}", result.stdout)
        self.assertIn(
            "ddr=rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.11.bin",
            result.stdout,
        )
        self.assertIn("bl31=rk35/rk3588_bl31_v1.38.elf", result.stdout)

    def test_m7_includes_standard_io_and_radio_packages(self) -> None:
        package_line = next(
            line
            for line in self.m7_board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(IO_PACKAGES <= packages)
        self.assertTrue(RADIO_PACKAGES <= packages)
        self.assertTrue({"pciutils", "nvme-cli"} <= packages)

    def test_m5_pro_pins_edge_sources_and_rk3576_blobs(self) -> None:
        for expected in (
            f'KERNELBRANCH_BOARD="commit:{M5PRO_LINUX_COMMIT}"',
            f'BOOTBRANCH_BOARD="commit:{M5PRO_UBOOT_COMMIT}"',
            f'RKBIN_GIT_REF="commit:{M5PRO_RKBIN_COMMIT}"',
            'DDR_BLOB="rk35/rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.08.bin"',
            'BL31_BLOB="rk35/rk3576_bl31_v1.20.elf"',
            'BOOST_BLOB="rk35/rk3576_boost_v1.02.bin"',
            'USBPLUG_BLOB="rk35/rk3576_usbplug_v1.03.bin"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.m5pro_board)
        self.assertNotIn('KERNELBRANCH_BOARD="branch:', self.m5pro_board)
        self.assertNotIn('BOOTBRANCH_BOARD="branch:', self.m5pro_board)
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.m5pro_board)

    def test_m5_pro_edge_hook_overrides_movable_family_sources(self) -> None:
        harness = f'''
display_alert() {{ :; }}
SRC="{ROOT}"
BRANCH=edge
BOOT_SOC=rk3576
HOSTRELEASE=jammy
source "{ROOT / 'config/boards/bananapim5pro.conf'}"
source "{ROOT / 'config/sources/families/rk35xx.conf'}"
printf 'before_kernel=%s\\n' "$KERNELBRANCH"
printf 'before_uboot=%s\\n' "$BOOTBRANCH"
post_family_config_branch_edge__bananapim5pro_pin_sources
printf 'kernel_source=%s\\nkernel=%s\\nuboot=%s\\nrkbin=%s\\n' \
    "$KERNELSOURCE" "$KERNELBRANCH" "$BOOTBRANCH" "$RKBIN_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("before_kernel=\n", result.stdout)
        self.assertIn("before_uboot=branch:next-dev-v2024.10", result.stdout)
        self.assertIn(
            "kernel_source=https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
            result.stdout,
        )
        self.assertIn(f"kernel=commit:{M5PRO_LINUX_COMMIT}", result.stdout)
        self.assertIn(f"uboot=commit:{M5PRO_UBOOT_COMMIT}", result.stdout)
        self.assertIn(f"rkbin=commit:{M5PRO_RKBIN_COMMIT}", result.stdout)

    def test_m5_pro_includes_standard_io_and_radio_packages(self) -> None:
        package_line = next(
            line
            for line in self.m5pro_board.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(IO_PACKAGES <= packages)
        self.assertTrue(RADIO_PACKAGES <= packages)
        self.assertTrue({"pciutils", "nvme-cli", "usbutils", "iw", "ethtool"} <= packages)

    def test_linux_7_0_does_not_reapply_upstream_sysrq_fix(self) -> None:
        obsolete_patch = (
            ROOT
            / "patch/kernel/archive/rockchip64-7.0"
            / "general-serial-8250-fix-sysrq-break-dw-apb.patch"
        )
        self.assertFalse(obsolete_patch.exists())

    def test_r2_pro_pins_current_sources_and_rk3568_blobs(self) -> None:
        for expected in (
            f'KERNELBRANCH_BOARD="commit:{R2PRO_LINUX_COMMIT}"',
            f'BOOTBRANCH_BOARD="commit:{R2PRO_UBOOT_COMMIT}"',
            f'RKBIN_GIT_REF="commit:{R2PRO_RKBIN_COMMIT}"',
            'DDR_BLOB="rk35/rk3568_ddr_1560MHz_v1.21.bin"',
            'BL31_BLOB="rk35/rk3568_bl31_v1.44.elf"',
            'ROCKUSB_BLOB="rk35/rk356x_spl_loader_v1.21.113.bin"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.r2pro_board)
        self.assertNotIn('KERNELBRANCH_BOARD="branch:', self.r2pro_board)
        self.assertNotIn('BOOTBRANCH_BOARD="branch:', self.r2pro_board)
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.r2pro_board)

    def test_r2_pro_current_hook_overrides_family_sources(self) -> None:
        harness = f'''
SRC="{ROOT}"
BRANCH=current
KERNELSOURCE=movable-kernel
KERNELBRANCH=branch:movable-kernel
BOOTBRANCH=branch:movable-uboot
ARMBIAN_FIRMWARE_GIT_REF=branch:movable-firmware
source "{ROOT / 'config/boards/bananapir2pro.csc'}"
post_family_config_branch_current__bananapir2pro_pin_sources
printf 'kernel_source=%s\\nkernel=%s\\nuboot=%s\\nrkbin=%s\\nfirmware=%s\\n' \\
    "$KERNELSOURCE" "$KERNELBRANCH" "$BOOTBRANCH" "$RKBIN_GIT_REF" \\
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
        self.assertIn(f"kernel=commit:{R2PRO_LINUX_COMMIT}", result.stdout)
        self.assertIn(f"uboot=commit:{R2PRO_UBOOT_COMMIT}", result.stdout)
        self.assertIn(f"rkbin=commit:{R2PRO_RKBIN_COMMIT}", result.stdout)
        self.assertIn(
            "firmware=commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
