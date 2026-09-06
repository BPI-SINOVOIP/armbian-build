#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/generate-bananapi-release-notes.py"
CONTROLLED_MATRIX = ROOT / "config/bananapi-latest-release-matrix.tsv"
NOTE_NAME = "Release-Notes-zh-TW.md"


def load_script_module():
    name = "generate_bananapi_release_notes"
    specification = importlib.util.spec_from_file_location(name, SCRIPT)
    if specification is None or specification.loader is None:
        raise RuntimeError("無法載入逐板候選發行說明產生器")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


class GenerateBananaPiReleaseNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        self.rows = [
            {
                "folder": "bpi-first",
                "board": "bananapifirst",
                "branch": "current",
                "releases": "trixie,bookworm",
            },
            {
                "folder": "bpi-second",
                "board": "bananapisecond",
                "branch": "edge",
                "releases": "noble",
            },
        ]
        self.write_matrix(self.rows)
        self.create_candidate(self.rows)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_matrix(self, rows: list[dict[str, str]]) -> None:
        with self.matrix.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                ["folder", "board", "branch", "releases"],
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)

    def create_candidate(
        self,
        rows: list[dict[str, str]],
        versions: dict[tuple[str, str, str], str] | None = None,
    ) -> None:
        for row in rows:
            directory = self.candidate / row["folder"]
            directory.mkdir(exist_ok=True)
            token = row["board"][0].upper() + row["board"][1:]
            for release in row["releases"].split(","):
                for profile, suffix in (
                    ("minimal", "minimal"),
                    ("xfce", "xfce_desktop"),
                ):
                    version = (versions or {}).get(
                        (row["folder"], release, profile), "6.18.49"
                    )
                    archive = directory / (
                        "Armbian-test_"
                        f"{token}_{release}_{row['branch']}_{version}_{suffix}.img.xz"
                    )
                    archive.write_bytes(
                        f"{row['folder']}-{release}-{profile}".encode()
                    )
                    Path(f"{archive}.sha").write_text(
                        f"{'a' * 64}  {archive.name}\n", encoding="utf-8"
                    )

    def run_generator(
        self,
        *extra: str,
        matrix: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--matrix",
                str(matrix or self.matrix),
                "--candidate-release",
                str(self.candidate),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_generates_notes_for_complete_matrix(self) -> None:
        result = self.run_generator()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 板", result.stdout)
        for row in self.rows:
            note = self.candidate / row["folder"] / NOTE_NAME
            self.assertTrue(note.is_file())
            content = note.read_text(encoding="utf-8")
            self.assertIn(f"`{row['folder']}`", content)
            self.assertIn(f"`{row['board']}`", content)
            self.assertIn(f"`{row['branch']}`", content)
            self.assertIn("軟體候選，尚待逐板硬體 Gate", content)
            self.assertIn("L3、量產核准或對外散布授權", content)
            table_rows = [
                line for line in content.splitlines() if line.startswith("| `")
            ]
            self.assertEqual(len(table_rows), len(row["releases"].split(",")) * 2)
            self.assertTrue(all(len(line.split("|")) == 8 for line in table_rows))

    def test_controlled_matrix_generates_exactly_45_board_notes(self) -> None:
        for path in self.candidate.iterdir():
            for child in path.iterdir():
                child.unlink()
            path.rmdir()
        with CONTROLLED_MATRIX.open(encoding="utf-8", newline="") as stream:
            controlled_rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(controlled_rows), 45)
        self.create_candidate(controlled_rows)

        result = self.run_generator(matrix=CONTROLLED_MATRIX)

        self.assertEqual(result.returncode, 0, result.stderr)
        notes = list(self.candidate.glob(f"*/{NOTE_NAME}"))
        self.assertEqual(len(notes), 45)
        self.assertEqual(
            {note.parent.name for note in notes},
            {row["folder"] for row in controlled_rows},
        )

    def test_actual_kernel_versions_are_parsed_per_image(self) -> None:
        for directory in self.candidate.iterdir():
            for path in directory.iterdir():
                path.unlink()
        versions = {
            ("bpi-first", "trixie", "minimal"): "6.6.75-current-sunxi64",
            ("bpi-first", "trixie", "xfce"): "6.18.49",
            ("bpi-first", "bookworm", "minimal"): "0",
            ("bpi-first", "bookworm", "xfce"): "7.0.14-edge-rockchip64",
        }
        self.create_candidate(self.rows, versions)

        result = self.run_generator()

        self.assertEqual(result.returncode, 0, result.stderr)
        content = (self.candidate / "bpi-first" / NOTE_NAME).read_text(
            encoding="utf-8"
        )
        for version in versions.values():
            self.assertIn(f"`{version}`", content)

    def test_missing_image_is_rejected_before_any_note_is_written(self) -> None:
        archive = next((self.candidate / "bpi-first").glob("*trixie*minimal.img.xz"))
        archive.unlink()
        Path(f"{archive}.sha").unlink()

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少矩陣映像", result.stderr)
        self.assertEqual(list(self.candidate.glob(f"*/{NOTE_NAME}")), [])

    def test_duplicate_matrix_match_is_rejected(self) -> None:
        row = self.rows[0]
        token = row["board"][0].upper() + row["board"][1:]
        duplicate = self.candidate / row["folder"] / (
            f"Armbian-extra_{token}_trixie_current_6.19.1_minimal.img.xz"
        )
        duplicate.write_bytes(b"duplicate")
        Path(f"{duplicate}.sha").write_text(
            f"{'b' * 64}  {duplicate.name}\n", encoding="utf-8"
        )

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("矩陣映像重複匹配", result.stderr)
        self.assertEqual(list(self.candidate.glob(f"*/{NOTE_NAME}")), [])

    def test_existing_note_requires_replace_and_replace_updates_it(self) -> None:
        note = self.candidate / "bpi-first" / NOTE_NAME
        note.write_text("既有發行說明\n", encoding="utf-8")

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("如需取代請加入 --replace", result.stderr)
        self.assertEqual(note.read_text(encoding="utf-8"), "既有發行說明\n")
        self.assertFalse((self.candidate / "bpi-second" / NOTE_NAME).exists())

        result = self.run_generator("--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("已原子取代", result.stdout)
        self.assertIn("# Banana Pi 候選映像發行說明", note.read_text(encoding="utf-8"))

    def test_atomic_replace_preserves_existing_note_when_replace_fails(self) -> None:
        module = load_script_module()
        note = self.candidate / "bpi-first" / NOTE_NAME
        note.write_text("既有發行說明\n", encoding="utf-8")

        with mock.patch.object(module.os, "replace", side_effect=OSError("模擬失敗")):
            with self.assertRaises(OSError):
                module.write_text_atomic(note, "新的發行說明\n", replace=True)

        self.assertEqual(note.read_text(encoding="utf-8"), "既有發行說明\n")
        self.assertEqual(list(note.parent.glob(f".{NOTE_NAME}.*.tmp")), [])

    def test_symlinks_and_out_of_matrix_board_directories_are_rejected(self) -> None:
        extra = self.candidate / "bpi-extra"
        extra.mkdir()
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("矩陣外板目錄", result.stderr)
        extra.rmdir()

        target = next((self.candidate / "bpi-first").glob("*.img.xz"))
        link = self.candidate / "bpi-first" / "不允許的連結"
        link.symlink_to(target)
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不得含符號連結", result.stderr)

    def test_generated_prose_passes_traditional_chinese_gate(self) -> None:
        result = self.run_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (self.candidate / "bpi-first" / NOTE_NAME).read_text(
            encoding="utf-8"
        )

        required_traditional = ("發行", "實際", "檔案", "軟體", "硬體", "燒錄")
        prohibited_simplified = ("发行", "实际", "文件验证", "软件", "硬件", "烧录")
        for phrase in required_traditional:
            self.assertIn(phrase, content)
        for phrase in prohibited_simplified:
            self.assertNotIn(phrase, content)

        outside_code: list[str] = []
        in_code = False
        for line in content.splitlines():
            if line.startswith("```"):
                in_code = not in_code
                continue
            if not in_code and not line.startswith("|"):
                outside_code.append(line)
        prose = "\n".join(outside_code)
        self.assertIsNone(
            re.search(r"\b[A-Za-z]{2,}\s+[A-Za-z]{2,}\s+[A-Za-z]{2,}\b", prose)
        )


if __name__ == "__main__":
    unittest.main()
