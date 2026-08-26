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
fetch_from_repo() {{ printf '%s\\n' "$3"; }}
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


if __name__ == "__main__":
    unittest.main()
