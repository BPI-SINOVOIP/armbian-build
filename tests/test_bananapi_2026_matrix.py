#!/usr/bin/env python3
"""Banana Pi 2026 全系列建置矩陣回歸測試。"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "b-bananapi-2026"


class BananaPi2026MatrixTests(unittest.TestCase):
    """驗證板卡選取、架構判定與無顯示輸出建置政策。"""

    def run_builder(
        self, action: str, *arguments: str, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        """以固定的小型矩陣執行建置器的唯讀動作。"""
        environment = os.environ.copy()
        for name in (
            "BOARDS",
            "BRANCHES",
            "RELEASES",
            "IMAGE_TYPES",
            "INCLUDE_LAMOBO",
            "INCLUDE_WIP",
            "ALL_BRANCHES",
            "ALLOW_HEADLESS_DESKTOP",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "RELEASES": "trixie",
                "IMAGE_TYPES": "server",
                "INCLUDE_LAMOBO": "no",
                "INCLUDE_WIP": "no",
                "ALL_BRANCHES": "no",
                "ALLOW_HEADLESS_DESKTOP": "no",
            }
        )
        environment.update(overrides)
        return subprocess.run(
            [str(SCRIPT), action, *arguments],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def selected_boards(output: str) -> set[str]:
        """由 list 輸出擷取板卡識別碼。"""
        return {
            line.split("\t", 1)[0]
            for line in output.splitlines()
            if "\t" in line
        }

    @staticmethod
    def board_ids(*patterns: str) -> set[str]:
        """依板卡檔案樣式建立預期識別碼集合。"""
        return {
            path.stem
            for pattern in patterns
            for path in (ROOT / "config/boards").glob(pattern)
        }

    def test_default_selection_includes_all_supported_bpi_boards(self) -> None:
        expected = self.board_ids(
            "bananapi*.conf",
            "bananapi*.csc",
            "bananapi*.eos",
            "bpi-*.conf",
            "bpi-*.csc",
            "bpi-*.eos",
        )
        result = self.run_builder("list")
        selected = self.selected_boards(result.stdout)

        self.assertIn("bpi-ai2n", selected, "預設矩陣未納入 BPI-AI2N")
        self.assertEqual(selected, expected, "預設矩陣的板卡集合不完整")

    def test_include_wip_selection_is_complete(self) -> None:
        expected = self.board_ids(
            "bananapi*.conf",
            "bananapi*.csc",
            "bananapi*.eos",
            "bpi-*.conf",
            "bpi-*.csc",
            "bpi-*.eos",
            "bananapi*.wip",
            "bpi-*.wip",
        )
        result = self.run_builder("list", INCLUDE_WIP="yes")

        self.assertEqual(
            self.selected_boards(result.stdout),
            expected,
            "啟用 INCLUDE_WIP 後的板卡集合不完整",
        )

    def test_spacemit_k3_bpi_uses_riscv64(self) -> None:
        result = self.run_builder("list", "--board", "bananapism10")
        fields = result.stdout.strip().split("\t")

        self.assertGreaterEqual(len(fields), 3, "BPI-SM10 清單輸出欄位不足")
        self.assertEqual(fields[2], "riscv64")

    def test_headless_board_skips_desktop_by_default(self) -> None:
        result = self.run_builder(
            "dry-run",
            "--board",
            "bananapir3",
            IMAGE_TYPES="desktop",
        )

        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("預設不建置 desktop", result.stderr)
        self.assertNotIn(
            "ALLOW_HEADLESS_DESKTOP=yes", result.stdout + result.stderr
        )

    def test_headless_desktop_requires_explicit_opt_in(self) -> None:
        result = self.run_builder(
            "dry-run",
            "--board",
            "bananapir3",
            IMAGE_TYPES="desktop",
            ALLOW_HEADLESS_DESKTOP="yes",
        )

        self.assertIn("BUILD_DESKTOP=yes", result.stdout)
        self.assertIn("ALLOW_HEADLESS_DESKTOP=yes", result.stdout)

    def test_ai2n_release_prefix_is_discovered(self) -> None:
        """AI2N 的特殊檔名前綴仍須被矩陣工具找到。"""
        function_text = subprocess.run(
            [
                "awk",
                "/^find_latest_image\\(\\)/,/^}/",
                str(SCRIPT),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        with tempfile.TemporaryDirectory() as temporary_directory:
            image_directory = Path(temporary_directory) / "output/images"
            image_directory.mkdir(parents=True)
            image = image_directory / (
                "Bananapi-Armbian_26.05.0-trunk_"
                "Bpi-ai2n_trixie_legacy_6.1.107.img"
            )
            image.touch()
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    function_text
                    + "\nfind_latest_image bpi-ai2n trixie legacy server",
                ],
                cwd=temporary_directory,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.stdout.strip(), f"output/images/{image.name}")

    def test_missing_image_turns_successful_compile_into_failure(self) -> None:
        """編譯器退出成功但沒有映像時，不得在摘要中誤記成功。"""
        function_text = subprocess.run(
            ["awk", "/^run_one\\(\\)/,/^}/", str(SCRIPT)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        shell = """
make_command() { printf 'true\\n'; }
find_latest_image() { return 0; }
compress_image() { return 0; }
SKIP_EXISTING=no
""" + function_text + "\nrun_one board current trixie server build.log"

        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                ["bash", "-c", shell],
                cwd=temporary_directory,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("no matching image found", result.stderr)


if __name__ == "__main__":
    unittest.main()
