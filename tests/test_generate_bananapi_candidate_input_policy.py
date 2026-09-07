#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/generate-bananapi-candidate-input-policy.py"
MATRIX = ROOT / "config/bananapi-latest-release-matrix.tsv"
SOURCE_A = "a" * 40
SOURCE_B = "b" * 40
CONTEXT_A = "c" * 64
CONTEXT_B = "d" * 64


def load_script_module():
    name = "generate_bananapi_candidate_input_policy"
    specification = importlib.util.spec_from_file_location(name, SCRIPT)
    if specification is None or specification.loader is None:
        raise RuntimeError("無法載入候選輸入政策產生器")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


class GenerateBananaPiCandidateInputPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.state = self.root / "state"
        self.output = self.root / "policy.tsv"
        (self.state / "boards").mkdir(parents=True)
        (self.state / "items").mkdir()
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
                "releases": "jammy",
            },
        ]
        self.write_matrix(self.rows)

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

    def create_complete_board(
        self,
        row: dict[str, str],
        source_commit: str = SOURCE_A,
        build_context: str = CONTEXT_A,
        omit: set[tuple[str, str]] | None = None,
        item_identity_overrides: dict[
            tuple[str, str], tuple[str, str]
        ] | None = None,
    ) -> None:
        releases = row["releases"].split(",")
        expected = len(releases) * 2
        folder = row["folder"]
        board_marker = self.state / "boards" / f"{folder}.complete"
        board_marker.write_text(
            f"source_commit={source_commit}\n"
            f"build_context_sha256={build_context}\n"
            f"folder={folder}\n"
            f"board={row['board']}\n"
            f"branch={row['branch']}\n"
            f"images={expected}\n"
            "status=complete\n",
            encoding="utf-8",
        )
        for release in releases:
            for profile in ("minimal", "xfce"):
                key = (release, profile)
                if omit and key in omit:
                    continue
                item_source, item_context = (source_commit, build_context)
                if item_identity_overrides and key in item_identity_overrides:
                    item_source, item_context = item_identity_overrides[key]
                item_marker = (
                    self.state
                    / "items"
                    / f"{folder}-{release}-{profile}.complete"
                )
                item_marker.write_text(
                    f"source_commit={item_source}\n"
                    f"build_context_sha256={item_context}\n"
                    f"folder={folder}\n"
                    f"board={row['board']}\n"
                    f"branch={row['branch']}\n"
                    f"release={release}\n"
                    f"profile={profile}\n"
                    "status=complete\n",
                    encoding="utf-8",
                )

    def run_generator(
        self,
        matrix: Path | None = None,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--matrix",
                str(matrix or self.matrix),
                "--candidate-state",
                str(self.state),
                "--output",
                str(self.output),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def read_policy(self) -> list[dict[str, str]]:
        with self.output.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream, delimiter="\t"))

    def test_success_produces_exact_header_and_matrix_order(self) -> None:
        for row in self.rows:
            self.create_complete_board(row)

        result = self.run_generator()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.output.read_text(encoding="utf-8").splitlines()[0],
            "folder\tsource_commit\tbuild_context_sha256",
        )
        policy = self.read_policy()
        self.assertEqual([row["folder"] for row in policy], [
            "bpi-first",
            "bpi-second",
        ])
        self.assertTrue(all(row["source_commit"] == SOURCE_A for row in policy))
        self.assertTrue(
            all(row["build_context_sha256"] == CONTEXT_A for row in policy)
        )

    def test_controlled_matrix_produces_45_rows(self) -> None:
        with MATRIX.open(encoding="utf-8", newline="") as stream:
            controlled_rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(controlled_rows), 45)
        for row in controlled_rows:
            self.create_complete_board(row)

        result = self.run_generator(MATRIX)

        self.assertEqual(result.returncode, 0, result.stderr)
        policy = self.read_policy()
        self.assertEqual(len(policy), 45)
        self.assertEqual(
            [row["folder"] for row in policy],
            [row["folder"] for row in controlled_rows],
        )

    def test_mixed_sources_are_preserved_per_board(self) -> None:
        self.create_complete_board(self.rows[0], SOURCE_A, CONTEXT_A)
        self.create_complete_board(self.rows[1], SOURCE_B, CONTEXT_B)

        result = self.run_generator()

        self.assertEqual(result.returncode, 0, result.stderr)
        policy = self.read_policy()
        self.assertEqual(
            [(row["source_commit"], row["build_context_sha256"]) for row in policy],
            [(SOURCE_A, CONTEXT_A), (SOURCE_B, CONTEXT_B)],
        )

    def test_legacy_item_markers_require_explicit_migration_mode(self) -> None:
        for row in self.rows:
            self.create_complete_board(row)
        for marker in (self.state / "items").glob("*.complete"):
            marker.write_text(
                marker.read_text(encoding="utf-8").replace("status=complete\n", ""),
                encoding="utf-8",
            )

        strict = self.run_generator()

        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("缺少必要欄位 status", strict.stderr)
        self.assertFalse(self.output.exists())

        result = self.run_generator(None, "--allow-legacy-item-status")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("只可供受控遷移稽核使用", result.stdout)
        self.assertEqual(len(self.read_policy()), 2)

    def test_explicit_noncomplete_item_status_is_rejected(self) -> None:
        for row in self.rows:
            self.create_complete_board(row)
        marker = self.state / "items" / "bpi-first-trixie-minimal.complete"
        marker.write_text(
            marker.read_text(encoding="utf-8").replace(
                "status=complete", "status=failed"
            ),
            encoding="utf-8",
        )

        result = self.run_generator(None, "--allow-legacy-item-status")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("項目標記狀態不是 complete", result.stderr)
        self.assertFalse(self.output.exists())

    def test_missing_item_is_rejected(self) -> None:
        self.create_complete_board(self.rows[0], omit={("bookworm", "xfce")})
        self.create_complete_board(self.rows[1])

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少完整項目標記", result.stderr)
        self.assertFalse(self.output.exists())

    def test_missing_board_marker_is_rejected(self) -> None:
        self.create_complete_board(self.rows[0])

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少板級標記：bpi-second", result.stderr)
        self.assertFalse(self.output.exists())

    def test_item_and_board_identity_mismatch_is_rejected(self) -> None:
        self.create_complete_board(
            self.rows[0],
            item_identity_overrides={("trixie", "minimal"): (SOURCE_B, CONTEXT_B)},
        )
        self.create_complete_board(self.rows[1])

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("候選輸入不一致", result.stderr)
        self.assertFalse(self.output.exists())

    def test_extra_board_marker_is_rejected(self) -> None:
        for row in self.rows:
            self.create_complete_board(row)
        (self.state / "boards" / "bpi-extra.complete").write_text(
            f"source_commit={SOURCE_A}\n"
            f"build_context_sha256={CONTEXT_A}\n"
            "folder=bpi-extra\n"
            "board=bananapiextra\n"
            "branch=current\n"
            "images=2\n"
            "status=complete\n",
            encoding="utf-8",
        )

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("矩陣外板級標記", result.stderr)
        self.assertFalse(self.output.exists())

    def test_duplicate_field_wrong_matrix_header_and_illegal_hash_are_rejected(
        self,
    ) -> None:
        for row in self.rows:
            self.create_complete_board(row)
        marker = self.state / "boards" / "bpi-first.complete"
        marker.write_text(
            marker.read_text(encoding="utf-8") + f"source_commit={SOURCE_A}\n",
            encoding="utf-8",
        )
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("重複欄位", result.stderr)

        self.create_complete_board(self.rows[0], "無效", CONTEXT_A)
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("來源提交格式錯誤", result.stderr)

        self.create_complete_board(self.rows[0], SOURCE_A, "無效")
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("建置內容雜湊格式錯誤", result.stderr)

        self.matrix.write_text(
            "folder\tboard\tbranch\t錯誤欄位\n",
            encoding="utf-8",
        )
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("矩陣欄位錯誤", result.stderr)

    def test_atomic_write_keeps_existing_output_when_replace_fails(self) -> None:
        module = load_script_module()
        self.output.write_text("既有政策\n", encoding="utf-8")
        rows = [
            {
                "folder": "bpi-first",
                "source_commit": SOURCE_A,
                "build_context_sha256": CONTEXT_A,
            }
        ]

        with mock.patch.object(module.os, "replace", side_effect=OSError("模擬失敗")):
            with self.assertRaises(OSError):
                module.write_policy_atomic(self.output, rows)

        self.assertEqual(self.output.read_text(encoding="utf-8"), "既有政策\n")
        self.assertEqual(list(self.root.glob(f".{self.output.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
