#!/usr/bin/env python3
"""Banana Pi Meson 第一批來源政策回歸測試。"""

from __future__ import annotations

import unittest
from pathlib import Path

from unidiff import PatchSet


ROOT = Path(__file__).resolve().parents[1]
BOARDS = (
    "bananapim5",
    "bananapim2pro",
    "bananapicm4io",
    "bananapim2s",
)
FIP_COMMIT = "e11ae32f65219e9cba903e9744f216239b41386a"
IO_PACKAGES = {
    "gpiod",
    "i2c-tools",
    "python3-libgpiod",
    "python3-spidev",
    "v4l-utils",
}
RADIO_PACKAGES = {"rfkill", "bluetooth", "bluez", "bluez-tools"}


class BananaPiMesonSourcePolicyTests(unittest.TestCase):
    """防止第一批板卡失去來源固定與板級修正邊界。"""

    @staticmethod
    def board_text(board: str) -> str:
        return (ROOT / "config" / "boards" / f"{board}.conf").read_text()

    def test_all_boards_include_standard_io_and_radio_packages(self) -> None:
        for board in BOARDS:
            with self.subTest(board=board):
                text = self.board_text(board)
                package_line = next(
                    line
                    for line in text.splitlines()
                    if line.startswith('PACKAGE_LIST_BOARD="')
                )
                packages = set(package_line.split('"', 2)[1].split())
                self.assertTrue(IO_PACKAGES <= packages)
                self.assertTrue(RADIO_PACKAGES <= packages)

    def test_all_boards_pin_the_same_fip_commit(self) -> None:
        for board in BOARDS:
            with self.subTest(board=board):
                text = self.board_text(board)
                self.assertIn(f'"commit:{FIP_COMMIT}"', text)
                self.assertNotIn(
                    '"amlogic-boot-fip" "branch:master"',
                    text,
                )

    def test_board_fip_pin_runs_after_family_fetch(self) -> None:
        for board in BOARDS:
            with self.subTest(board=board):
                self.assertIn(
                    "fetch_sources_tools__900_bananapi_amlogic_fip",
                    self.board_text(board),
                )

    def test_a311d_boards_do_not_force_performance_governor(self) -> None:
        for board in ("bananapicm4io", "bananapim2s"):
            with self.subTest(board=board):
                text = self.board_text(board)
                self.assertIn("GOVERNOR=ondemand", text)
                self.assertNotIn("GOVERNOR=performance", text)

    def test_hynix_kernel_override_is_m5_only(self) -> None:
        patch = (
            ROOT
            / "patch/kernel/archive/meson64-6.18"
            / "board-bananapim5-hynix-emmc-stability.patch"
        ).read_text()
        self.assertIn("meson-sm1-bananapi-m5.dts", patch)
        self.assertNotIn("meson-sm1-bananapi.dtsi", patch)
        self.assertNotIn("bananapi-m2-pro", patch)
        self.assertIn("max-frequency = <100000000>;", patch)
        self.assertIn("no-mmc-hs400;", patch)

    def test_uboot_legacy_override_is_m5_only(self) -> None:
        m5_patch = (
            ROOT
            / "patch/u-boot/v2024.07/board_bananapim5"
            / "001-bananapi-m5-conservative-emmc.patch"
        )
        m2pro_directory = (
            ROOT / "patch/u-boot/v2024.07/board_bananapim2pro"
        )
        self.assertTrue(m5_patch.is_file())
        self.assertIn("u-boot,legacy-mmc", m5_patch.read_text())
        self.assertFalse(
            m2pro_directory.exists()
            and any(m2pro_directory.glob("*emmc*.patch"))
        )

    def test_cvbs_is_not_disabled_by_default(self) -> None:
        matching = list(
            (ROOT / "patch/kernel/archive/meson64-6.18").glob(
                "*bananapim5*cvbs*.patch"
            )
        )
        self.assertEqual(matching, [])

    def test_bananapi_patch_mailbox_subjects_are_ascii(self) -> None:
        for patch in (ROOT / "patch").rglob("*.patch"):
            relative = patch.relative_to(ROOT)
            if "bananapi" not in relative.as_posix().lower():
                continue
            with patch.open("rb") as stream:
                subject = next(
                    (
                        line.rstrip(b"\r\n")
                        for line in stream
                        if line.startswith(b"Subject: ")
                    ),
                    None,
                )
            if subject is None:
                continue
            with self.subTest(patch=relative):
                subject.decode("ascii")

    def test_bananapi_unified_diffs_are_parseable(self) -> None:
        for patch in (ROOT / "patch").rglob("*.patch"):
            relative = patch.relative_to(ROOT)
            if "bananapi" not in relative.as_posix().lower():
                continue
            text = patch.read_text()
            if "diff --git " not in text:
                continue
            with self.subTest(patch=relative):
                PatchSet(text.splitlines(keepends=True))

    def test_m5_modified_patch_indexes_are_not_zero(self) -> None:
        patches = (
            ROOT
            / "patch/kernel/archive/meson64-6.18"
            / "board-bananapim5-hynix-emmc-stability.patch",
            ROOT
            / "patch/u-boot/v2024.07/board_bananapim5"
            / "001-bananapi-m5-conservative-emmc.patch",
        )
        for patch in patches:
            with self.subTest(patch=patch.relative_to(ROOT)):
                for line in patch.read_text().splitlines():
                    if line.startswith("index "):
                        self.assertNotRegex(line, r"\.\.0{7,}")


if __name__ == "__main__":
    unittest.main()
