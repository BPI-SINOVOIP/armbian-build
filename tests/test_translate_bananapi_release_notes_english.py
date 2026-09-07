#!/usr/bin/env python3
"""聚焦檢查簡版翻譯、整批前置拒絕與英文原子寫入。"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/translate-bananapi-release-notes-english.py"
CONTROLLED_MATRIX = ROOT / "config/bananapi-latest-release-matrix.tsv"
CHINESE_NAME = "Release-Notes-zh-TW.md"
ENGLISH_NAME = "Release-Notes-English.md"
BSP_COMMIT = "8893355b34efc97a1e7677c6541beb177ec014e1"
SOURCE_COMMITS = (
    "c8673931c96c23c510dd29a28440e77f0b03286f",
    "4a8086c3cd1a4f92a80ccb1d896f527ca4f5f6e1",
)

# 獨立保留實際簡版來源範本，避免測試由待測翻譯器反向產生中文。
CHINESE_TEMPLATE = """# {board} 最新內部候選映像

BSP 整合基準提交：`{bsp_commit}`

建置工具與最終來源提交：`{source_commit}`

建置矩陣 SHA-256：`{matrix_sha256}`

核心分支：`{branch}`

發行版：`{releases}`

本目錄包含 {count} 個映像，分別為精簡命令列版與 XFCE 桌面版。所有映像均由上述最終來源提交執行 `compile.sh build`；第一個 Trixie 精簡映像另強制清理並重建 U-Boot、Kernel、ATF 與 Crust 等實際適用元件。同板後續映像只可沿用本輪已驗證的元件快取。

每個映像均通過原始映像唯讀內容與板型檢查、SHA-256 及 XZ 串流完整性檢查。這是軟體候選結果，不代表未執行的實機、全介面、長時間壓力、量產或再散布門檻已通過。燒錄前請再次核對同名 `.img.xz.sha`。
"""


def load_module():
    name = "translate_bananapi_release_notes_english"
    specification = importlib.util.spec_from_file_location(name, SCRIPT)
    if specification is None or specification.loader is None:
        raise RuntimeError("無法載入英文發行說明翻譯器")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


MODULE = load_module()


class TranslateBananaPiReleaseNotesEnglishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        self.rows = [
            {
                "folder": "bpi-m1", "board": "bananapi", "branch": "current",
                "releases": "trixie,bookworm,jammy,noble,resolute",
            },
            {
                "folder": "bpi-cm6", "board": "bananapicm6", "branch": "legacy",
                "releases": "trixie,jammy,noble,resolute",
            },
        ]
        self.write_matrix()
        self.create_candidate()

    def write_matrix(self) -> None:
        with self.matrix.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, ["folder", "board", "branch", "releases"],
                delimiter="\t", lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(self.rows)

    def create_candidate(self) -> None:
        matrix_sha256 = hashlib.sha256(self.matrix.read_bytes()).hexdigest()
        for number, row in enumerate(self.rows):
            directory = self.candidate / row["folder"]
            directory.mkdir()
            token = row["board"][0].upper() + row["board"][1:]
            for release in row["releases"].split(","):
                for suffix in ("minimal", "xfce_desktop"):
                    archive = directory / (
                        f"Armbian-test_{token}_{release}_{row['branch']}_6.6.75_{suffix}.img.xz"
                    )
                    archive.write_bytes(b"\x00")
                    Path(f"{archive}.sha").write_bytes(b"\x00")
            (directory / CHINESE_NAME).write_text(
                CHINESE_TEMPLATE.format(
                    **row, bsp_commit=BSP_COMMIT,
                    source_commit=SOURCE_COMMITS[number % len(SOURCE_COMMITS)],
                    matrix_sha256=matrix_sha256,
                    count=len(row["releases"].split(",")) * 2,
                ),
                encoding="utf-8",
            )

    def note(self, number: int = 0, *, chinese: bool = False) -> Path:
        return self.candidate / self.rows[number]["folder"] / (
            CHINESE_NAME if chinese else ENGLISH_NAME
        )

    def run_tool(self, *extra: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, "-B", str(script), "--matrix", str(self.matrix),
                "--candidate-release", str(self.candidate), *extra,
            ],
            cwd=self.root, capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def snapshot(self) -> dict[str, tuple[bytes, int, int]]:
        return {
            str(path.relative_to(self.candidate)): (
                path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns,
            )
            for path in self.candidate.rglob("*") if path.is_file()
        }

    def assert_rejected_without_writes(self, *extra: str) -> subprocess.CompletedProcess[str]:
        before = self.snapshot()
        result = self.run_tool(*extra)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("錯誤：", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.candidate.rglob("*.tmp")), [])
        return result

    def test_metadata_and_both_complete_paragraphs_are_faithful_for_8_and_10(self) -> None:
        before = {self.note(i, chinese=True): self.note(i, chinese=True).read_bytes() for i in range(2)}
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 板", result.stdout)
        for number, row in enumerate(self.rows):
            source = self.note(number, chinese=True).read_text(encoding="utf-8")
            translated = self.note(number).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"`([^`]+)`", translated), re.findall(r"`([^`]+)`", source))
            self.assertTrue(translated.startswith(f"# {row['board']} Latest Internal Candidate Images\n"))
            self.assertIn(SOURCE_COMMITS[number], translated)
            self.assertEqual(len(translated.rstrip("\n").split("\n\n")), 8)
            paragraphs = translated.rstrip("\n").split("\n\n")
            count = len(row["releases"].split(",")) * 2
            self.assertEqual(
                paragraphs[-2],
                f"This directory contains {count} images, in minimal command-line and XFCE desktop variants. "
                "All images were built by running `compile.sh build` from the final source commit above; "
                "for the first Trixie minimal image, applicable components such as U-Boot, Kernel, ATF, "
                "and Crust were also forcibly cleaned and rebuilt. Subsequent images for the same board "
                "may only reuse component caches verified in this build round.",
            )
            self.assertEqual(
                paragraphs[-1],
                "Each image has passed read-only checks of the raw image contents and board type, "
                "SHA-256 verification, and XZ stream integrity checks. This is a software candidate "
                "result and does not mean that unperformed hardware, full-interface, long-duration "
                "stress, mass-production, or redistribution gates have passed. Before flashing, "
                "recheck the matching `.img.xz.sha` file.",
            )
            self.assertIsNone(re.search(r"[\u3400-\u9fff]", translated))
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_controlled_matrix_generates_45_translations_only_in_temporary_directory(self) -> None:
        shutil.rmtree(self.candidate)
        self.candidate.mkdir()
        with CONTROLLED_MATRIX.open(encoding="utf-8", newline="") as stream:
            self.rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(self.rows), 45)
        shutil.copyfile(CONTROLLED_MATRIX, self.matrix)
        self.create_candidate()
        before = self.snapshot()
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.candidate.glob(f"*/{ENGLISH_NAME}"))), 45)
        after = self.snapshot()
        self.assertEqual({key: after[key] for key in before}, before)
        checked = self.run_tool("--check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("45 板", checked.stdout)

        self.note().write_text("既有英文檔待更新\n", encoding="utf-8")
        last_source = self.note(44, chinese=True)
        last_source.write_bytes(last_source.read_bytes() + "\n新增限制：不得發布。\n".encode())
        result = self.assert_rejected_without_writes("--replace")
        self.assertIn("未知文字或限制", result.stderr)

    @unittest.skipUnless(os.environ.get("BANANAPI_TRANSLATION_SOURCE"), "未指定唯讀實際來源")
    def test_actual_45_sources_pass_readonly_preflight_without_image_reads(self) -> None:
        root = Path(os.environ["BANANAPI_TRANSLATION_SOURCE"])
        rows, digest = MODULE.read_matrix(CONTROLLED_MATRIX)
        self.assertEqual(len(rows), 45)
        sources = {root / row.folder / CHINESE_NAME for row in rows}
        before = {path: path.read_bytes() for path in sources}
        targets_before = {
            root / row.folder / ENGLISH_NAME: (root / row.folder / ENGLISH_NAME).exists()
            for row in rows
        }
        read_bytes = Path.read_bytes

        def guarded_read(path: Path) -> bytes:
            self.assertIn(path.name, (CHINESE_NAME, ENGLISH_NAME))
            return read_bytes(path)

        with mock.patch.object(Path, "read_bytes", guarded_read):
            with mock.patch.object(MODULE, "write_note_atomic", side_effect=AssertionError("實際來源不得寫入")):
                notes = MODULE.prepare_notes(root, rows, digest, replace=True, check=False)
        self.assertEqual(len(notes), 45)
        for row, note in zip(rows, notes):
            self.assertEqual(note.target, root / row.folder / ENGLISH_NAME)
            self.assertIn(f"# {row.board} ".encode(), note.content)
        self.assertEqual({path: path.read_bytes() for path in sources}, before)
        self.assertEqual({path: path.exists() for path in targets_before}, targets_before)

    def test_unknown_or_missing_chinese_is_rejected_for_whole_batch_even_with_replace(self) -> None:
        source = self.note(1, chinese=True)
        original = source.read_text(encoding="utf-8")
        alterations = (
            original + "\n新增限制：不得對外發布。\n",
            original.replace("同板後續映像只可沿用本輪已驗證的元件快取。", ""),
            original.replace("不代表未執行", "代表已執行"),
            original.replace("實際適用元件", "所有元件"),
            original.replace("原始映像唯讀內容與板型檢查、", ""),
            original.replace("燒錄前請再次核對同名 `.img.xz.sha`。", ""),
            original.replace("\n", "\r\n"),
            original.rstrip("\n"),
            "\ufeff" + original,
        )
        for altered in alterations:
            for options in ((), ("--replace",), ("--check",)):
                with self.subTest(altered=altered, options=options):
                    source.write_bytes(altered.encode("utf-8"))
                    if options == ("--check",):
                        rows, digest = MODULE.read_matrix(self.matrix)
                        self.note().write_bytes(MODULE.translate_note(self.note(chinese=True).read_bytes(), rows[0], digest))
                    result = self.assert_rejected_without_writes(*options)
                    self.assertIn("未知文字或限制", result.stderr)

    def test_wrong_board_branch_releases_count_and_matrix_digest_are_rejected(self) -> None:
        source = self.note(1, chinese=True)
        original = source.read_text(encoding="utf-8")
        alterations = (
            ("# bananapicm6 ", "# bananapi "),
            ("`legacy`", "`edge`"),
            ("`trixie,jammy,noble,resolute`", "`jammy,trixie,noble,resolute`"),
            ("本目錄包含 8 個", "本目錄包含 10 個"),
            (hashlib.sha256(self.matrix.read_bytes()).hexdigest(), "a" * 64),
        )
        for old, new in alterations:
            with self.subTest(field=old):
                source.write_text(original.replace(old, new), encoding="utf-8")
                result = self.assert_rejected_without_writes()
                self.assertIn("不符矩陣", result.stderr)

    def test_invalid_utf8_and_commit_format_are_rejected(self) -> None:
        source = self.note(1, chinese=True)
        original = source.read_bytes()
        for data in (original + b"\xff", original.replace(BSP_COMMIT.encode(), b"abc")):
            with self.subTest(data=data):
                source.write_bytes(data)
                self.assert_rejected_without_writes()

    def test_missing_source_on_last_board_prevents_first_write(self) -> None:
        self.note(1, chinese=True).unlink()
        self.assert_rejected_without_writes()

    def test_existing_equal_english_is_never_rewritten_in_any_mode(self) -> None:
        self.assertEqual(self.run_tool().returncode, 0)
        before = self.snapshot()
        for options in ((), ("--replace",), ("--check",)):
            with self.subTest(options=options):
                result = self.run_tool(*options)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_different_last_english_requires_replace_before_any_write(self) -> None:
        self.note(1).write_text("既有英文檔待更新\n", encoding="utf-8")
        result = self.assert_rejected_without_writes()
        self.assertIn("--replace", result.stderr)
        source_before = self.note(1, chinese=True).read_bytes()
        target_inode = self.note(1).stat().st_ino
        replaced = self.run_tool("--replace")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertNotEqual(self.note(1).stat().st_ino, target_inode)
        self.assertEqual(self.note(1, chinese=True).read_bytes(), source_before)
        self.assertEqual(self.run_tool("--check").returncode, 0)

    def test_check_is_readonly_for_missing_stale_or_outdated_translation(self) -> None:
        self.assert_rejected_without_writes("--check")
        self.assertEqual(self.run_tool().returncode, 0)
        source = self.note(1, chinese=True)
        source.write_bytes(source.read_bytes().replace(SOURCE_COMMITS[1].encode(), b"b" * 40))
        result = self.assert_rejected_without_writes("--check")
        self.assertIn("與中文翻譯不一致", result.stderr)
        self.assertEqual(self.run_tool("--replace").returncode, 0)
        english = self.note(1)
        english.write_bytes(english.read_bytes().replace(b"may only reuse", b"may reuse"))
        self.assert_rejected_without_writes("--check")

    def test_check_and_replace_cannot_be_combined(self) -> None:
        result = self.assert_rejected_without_writes("--check", "--replace")
        self.assertEqual(result.returncode, 2)

    def test_english_symlinks_including_dangling_are_rejected_in_every_mode(self) -> None:
        rows, digest = MODULE.read_matrix(self.matrix)
        self.note().write_bytes(MODULE.translate_note(self.note(chinese=True).read_bytes(), rows[0], digest))
        for destination in (self.note(1, chinese=True), self.root / "missing"):
            self.note(1).symlink_to(destination)
            for options in ((), ("--replace",), ("--check",)):
                with self.subTest(destination=destination, options=options):
                    result = self.assert_rejected_without_writes(*options)
                    self.assertIn("符號連結", result.stderr)
                    self.assertTrue(self.note(1).is_symlink())
            self.note(1).unlink()

    def test_source_matrix_board_root_and_ancestor_symlinks_are_rejected(self) -> None:
        for path in (self.note(1, chinese=True), self.matrix, self.note(1).parent, self.candidate):
            with self.subTest(path=path):
                moved = self.root / "moved"
                path.rename(moved)
                path.symlink_to(moved)
                result = self.run_tool("--replace")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("符號連結", result.stderr)
                self.assertEqual(list(self.candidate.glob(f"*/{ENGLISH_NAME}")), [])
                path.unlink()
                moved.rename(path)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.candidate = alias / "candidate"
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符號連結", result.stderr)

    def test_nonregular_english_and_out_of_matrix_directory_are_rejected(self) -> None:
        self.note(1).mkdir()
        self.assert_rejected_without_writes("--replace")
        self.note(1).rmdir()
        (self.candidate / "bpi-extra").mkdir()
        result = self.assert_rejected_without_writes()
        self.assertIn("矩陣外板目錄", result.stderr)

    def test_missing_board_and_missing_or_wrong_artifact_are_rejected(self) -> None:
        directory = self.note(1).parent
        outside = self.root / "outside"
        directory.rename(outside)
        self.assert_rejected_without_writes()
        outside.rename(directory)
        archive = next(directory.glob("*.img.xz"))
        sidecar = Path(f"{archive}.sha")
        sidecar.unlink()
        self.assert_rejected_without_writes()
        sidecar.write_bytes(b"\x00")
        wrong = archive.with_name(archive.name.replace("Bananapicm6", "Bananapi"))
        archive.rename(wrong)
        sidecar.rename(Path(f"{wrong}.sha"))
        result = self.assert_rejected_without_writes()
        self.assertIn("未唯一對應矩陣", result.stderr)

    def test_invalid_matrix_is_rejected_before_any_write(self) -> None:
        original = self.matrix.read_bytes()
        alternatives = (
            original.replace(b"folder\tboard", b"board\tfolder"),
            original + original.splitlines(keepends=True)[1],
            original.replace(b"bpi-m1\t", b"../bpi-m1\t"),
            original.replace(b"trixie,bookworm", b"trixie,trixie"),
            b"folder\tboard\tbranch\treleases\n",
        )
        for data in alternatives:
            with self.subTest(data=data):
                self.matrix.write_bytes(data)
                self.assert_rejected_without_writes()

    def test_failed_atomic_replace_preserves_existing_bytes_and_removes_temporary(self) -> None:
        target = self.note()
        target.write_bytes(b"\x00")
        note = MODULE.PreparedNote(target, b"\x01", b"\x00")
        with mock.patch.object(MODULE.os, "replace", side_effect=OSError("模擬失敗")):
            with self.assertRaises(OSError):
                MODULE.write_note_atomic(note)
        self.assertEqual(target.read_bytes(), b"\x00")
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_atomic_writer_refuses_chinese_and_creation_races(self) -> None:
        chinese = self.note(chinese=True)
        original = chinese.read_bytes()
        with self.assertRaisesRegex(ValueError, "只允許寫入英文"):
            MODULE.write_note_atomic(MODULE.PreparedNote(chinese, b"\x00", original))
        self.assertEqual(chinese.read_bytes(), original)
        self.note().write_bytes(b"\x00")
        with self.assertRaisesRegex(ValueError, "拒絕覆寫"):
            MODULE.write_note_atomic(MODULE.PreparedNote(self.note(), b"\x01", None))
        self.assertEqual(self.note().read_bytes(), b"\x00")
        self.assertEqual(list(self.note().parent.glob("*.tmp")), [])

    def test_no_image_reads_or_external_verifiers_and_check_never_calls_writer(self) -> None:
        read_bytes = Path.read_bytes

        def guarded_read(path: Path) -> bytes:
            self.assertNotIn(path.suffix, (".xz", ".sha"))
            return read_bytes(path)

        arguments = ["--matrix", str(self.matrix), "--candidate-release", str(self.candidate)]
        with mock.patch.object(Path, "read_bytes", guarded_read), contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(subprocess, "run", side_effect=AssertionError("不得執行外部核驗")):
                self.assertEqual(MODULE.main(arguments), 0)
            with mock.patch.object(MODULE, "write_note_atomic", side_effect=AssertionError("唯讀不得寫入")):
                self.assertEqual(MODULE.main([*arguments, "--check"]), 0)

    def test_standalone_tool_runs_from_snapshot_tools_without_repository_helpers(self) -> None:
        snapshot_tools = self.root / "snapshot" / "tools"
        snapshot_tools.mkdir(parents=True)
        relocated = snapshot_tools / SCRIPT.name
        shutil.copyfile(SCRIPT, relocated)
        result = self.run_tool(script=relocated)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_tool("--check", script=relocated).returncode, 0)

    def test_help_and_success_output_are_traditional_chinese(self) -> None:
        help_result = self.run_tool("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("用法：", help_result.stdout)
        self.assertIn("唯讀檢查", help_result.stdout)
        self.assertNotIn("usage:", help_result.stdout)
        result = self.run_tool()
        self.assertIn("英文說明處理完成", result.stdout)
        self.assertIn("中文檔未修改", result.stdout)


if __name__ == "__main__":
    unittest.main()
