#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "config/bananapi-latest-release-matrix.tsv"
STATUS = ROOT / "config/bananapi-optimization-status.json"
SCRIPT = ROOT / "tools/rebuild-bananapi-latest-release.sh"


def load_matrix() -> list[dict[str, str]]:
    with MATRIX.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


class BananaPiLatestFullRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = load_matrix()
        cls.status = json.loads(STATUS.read_text(encoding="utf-8"))

    def test_matrix_covers_active_l2_boards_and_variant(self) -> None:
        expected = {
            board
            for board, evidence in self.status["evidence"].items()
            if evidence["level"] == "L2" and board != "bananapir1"
        }
        expected.update(self.status["variants"])
        self.assertEqual({row["board"] for row in self.rows}, expected)
        self.assertEqual(len(self.rows), 45)

    def test_matrix_excludes_l0_and_eos(self) -> None:
        boards = {row["board"] for row in self.rows}
        self.assertTrue({"bananapicm2", "bananapim2c", "bananapim4super"}.isdisjoint(boards))
        self.assertNotIn("bananapir1", boards)

    def test_folders_and_boards_are_unique(self) -> None:
        folders = [row["folder"] for row in self.rows]
        boards = [row["board"] for row in self.rows]
        self.assertEqual(len(folders), len(set(folders)))
        self.assertEqual(len(boards), len(set(boards)))

    def test_matrix_has_exactly_444_images(self) -> None:
        image_count = sum(len(row["releases"].split(",")) * 2 for row in self.rows)
        self.assertEqual(image_count, 444)
        for row in self.rows:
            releases = row["releases"].split(",")
            self.assertEqual(releases[0], "trixie")
            if row["board"] in {"bananapicm6", "bananapif3", "bananapism10"}:
                self.assertNotIn("bookworm", releases)
                self.assertEqual(len(releases), 4)
            else:
                self.assertEqual(len(releases), 5)

    def test_every_matrix_board_has_one_config(self) -> None:
        for row in self.rows:
            matches = [
                ROOT / "config/boards" / f"{row['board']}.{suffix}"
                for suffix in ("conf", "csc", "wip", "eos")
                if (ROOT / "config/boards" / f"{row['board']}.{suffix}").is_file()
            ]
            self.assertEqual(len(matches), 1, row["board"])

    def test_script_syntax_and_dry_run(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], cwd=ROOT, check=True)
        result = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("預定建置映像總數：444", result.stdout)


if __name__ == "__main__":
    unittest.main()
