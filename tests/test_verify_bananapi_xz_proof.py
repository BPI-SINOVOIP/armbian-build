#!/usr/bin/env python3
"""候選 XZ 證據摘要的聚焦回歸測試。"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/verify-bananapi-xz-proof.py"
SPEC = importlib.util.spec_from_file_location("xz_proof", SCRIPT)
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


class VerifyBananaPiXzProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.state = self.root / "state"
        self.evidence = self.state / "migrations" / "result"
        self.audit = self.evidence / "完整性稽核"
        self.fresh = self.root / "fresh"
        self.output = self.root / "proof.json"
        for directory in (self.audit, self.fresh, self.state / "boards", self.state / "items", self.root / "images"):
            directory.mkdir(parents=True)
        self.write_tsv(self.matrix, PROOF.MATRIX_FIELDS, [["bpi-test", "bananapitest", "current", "trixie"]])
        self.matrix_digest = self.sha(self.matrix)
        self.status = {**PROOF.SUCCESS, "矩陣SHA256": self.matrix_digest}
        self.write_status()
        policy = [["bpi-test", "a" * 40, "b" * 64]]
        for path in (self.audit / "候選輸入政策.tsv", self.evidence / "逐板候選輸入政策.tsv", self.fresh / "候選輸入政策.tsv"):
            self.write_tsv(path, PROOF.POLICY_FIELDS, policy)
        self.board = {
            "folder": "bpi-test", "board": "bananapitest", "branch": "current",
            "source_commit": "a" * 40, "build_context_sha256": "b" * 64,
            "bsp_base_commit": "c" * 40, "userpatches_sha256": "d" * 64,
            "matrix_sha256": self.matrix_digest, "images": "2", "status": "complete",
        }
        self.write_marker(self.state / "boards" / "bpi-test.complete", self.board)
        rows = []
        for profile, digest in (("minimal", "e" * 64), ("xfce", "f" * 64)):
            archive = self.root / "images" / f"{profile}.img.xz"
            os.mkfifo(archive)
            marker = {key: value for key, value in self.board.items() if key != "images"}
            marker.update(release="trixie", profile=profile, archive=archive.name, sha256=digest)
            self.write_marker(self.state / "items" / f"bpi-test-trixie-{profile}.complete", marker)
            rows.append([
                f"bpi-test/bananapitest/current/trixie/{profile}", "bpi-test", "bananapitest",
                "current", "trixie", profile, "已驗證候選", "整併候選", str(archive),
                digest, "a" * 40, "b" * 64, "不再建置",
            ])
        self.write_tsv(self.audit / "映像盤點.tsv", PROOF.INVENTORY_FIELDS, rows)
        self.write_tsv(self.fresh / "映像盤點.tsv", PROOF.INVENTORY_FIELDS, rows)

    @staticmethod
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def write_tsv(path: Path, fields: list[str], rows: list[list[str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(fields)
            writer.writerows(rows)

    @staticmethod
    def write_marker(path: Path, values: dict[str, str]) -> None:
        path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")

    def write_status(self) -> None:
        self.write_tsv(self.evidence / "執行狀態.tsv", ["欄位", "值"], list(self.status.items()))

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--matrix", str(self.matrix), *args],
                              text=True, capture_output=True, check=False, timeout=5)

    def create(self) -> subprocess.CompletedProcess[str]:
        return self.run_tool("--candidate-state", str(self.state), "--migration-evidence", str(self.evidence),
                             "--output", str(self.output))

    def verify(self, digest: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_tool("--proof", str(self.output), "--proof-sha256", digest or self.sha(self.output),
                             "--audit-output", str(self.fresh))

    def assert_rejected(self, result: subprocess.CompletedProcess[str], text: str = "") -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        if text:
            self.assertIn(text, result.stderr)

    def test_round_trip_does_not_read_xz_or_modify_markers(self) -> None:
        before = {path: path.read_bytes() for path in self.state.rglob("*.complete")}
        poison = self.evidence / "不得執行.py"
        poison.write_text("raise RuntimeError('不得執行證據程式')\n", encoding="utf-8")
        created = self.create()
        self.assertEqual(created.returncode, 0, created.stderr)
        proof = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(len(proof["items"]), 2)
        self.assertEqual(proof["matrix_sha256"], self.matrix_digest)
        self.assertEqual(len(proof["source_files"]), 8)
        for path, digest in proof["source_files"].items():
            self.assertEqual(self.sha(Path(path)), digest)
        self.assertEqual(self.output.stat().st_nlink, 1)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        inventory = self.fresh / "映像盤點.tsv"
        inventory.write_text(inventory.read_text(encoding="utf-8").replace("整併候選", "正式發布").replace(str(self.root / "images"), "/正式發布"), encoding="utf-8")
        shutil.rmtree(self.evidence)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        verified = self.verify()
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_success_flags_and_matrix_digest_are_required(self) -> None:
        original = dict(self.status)
        for key in original:
            for missing in (True, False):
                with self.subTest(key=key, missing=missing):
                    self.status = dict(original)
                    if missing:
                        del self.status[key]
                    else:
                        self.status[key] = "不符"
                    self.write_status()
                    self.assert_rejected(self.create(), "遷移成功證據")
                    self.assertFalse(self.output.exists())

    def test_creation_rejects_inventory_schema_duplicates_and_metadata(self) -> None:
        path = self.audit / "映像盤點.tsv"
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines()
        cases = [
            original.replace("SHA256", "錯誤欄位", 1),
            "\n".join(lines[:-1]) + "\n",
            original + lines[1] + "\n",
            original.replace("a" * 40, "a" * 39),
            original.replace("e" * 64, "g" * 64),
            original.replace("e" * 64, "9" * 64),
            original.replace("b" * 64, "8" * 64),
            original.replace("\tbananapitest\t", "\twrongboard\t", 1),
            original.replace("\t已驗證候選\t", "\t未完成\t", 1),
            original.replace("\t" + "e" * 64, "\t", 1),
            original.replace("\t不再建置\n", "\t不再建置\t多餘\n", 1),
        ]
        for number, content in enumerate(cases):
            with self.subTest(case=number):
                path.write_text(content, encoding="utf-8")
                self.assert_rejected(self.create())
                self.assertFalse(self.output.exists())

    def test_creation_rejects_changed_policies_and_marker_identity(self) -> None:
        paths = [self.audit / "候選輸入政策.tsv", self.evidence / "逐板候選輸入政策.tsv",
                 self.state / "boards" / "bpi-test.complete", self.state / "items" / "bpi-test-trixie-minimal.complete"]
        for path in paths:
            original = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                path.write_text(original.replace("a" * 40, "1" * 40), encoding="utf-8")
                self.assert_rejected(self.create())
                path.write_text(original, encoding="utf-8")
        marker = paths[-1]
        marker.write_text(marker.read_text(encoding="utf-8").replace("status=complete\n", ""), encoding="utf-8")
        self.assert_rejected(self.create(), "項目標記")

    def test_rejects_evidence_outside_migrations(self) -> None:
        moved = self.root / "outside"
        shutil.move(self.evidence, moved)
        self.evidence = moved
        self.assert_rejected(self.create(), "migrations")

    def test_rejects_symlinks_hardlinks_and_special_metadata(self) -> None:
        for path in (self.evidence / "執行狀態.tsv", self.audit / "映像盤點.tsv", self.state / "items" / "bpi-test-trixie-minimal.complete", self.matrix):
            with self.subTest(path=path):
                target = path.with_name(path.name + ".original")
                path.rename(target)
                path.symlink_to(target)
                self.assert_rejected(self.create(), "連結")
                path.unlink()
                os.link(target, path)
                self.assert_rejected(self.create(), "連結")
                path.unlink()
                os.mkfifo(path)
                self.assert_rejected(self.create(), "非正常檔案")
                path.unlink()
                target.rename(path)
        parent = self.state / "migrations"
        target = self.state / "renamed"
        parent.rename(target)
        parent.symlink_to(target, target_is_directory=True)
        self.assert_rejected(self.create(), "連結")

    def test_matrix_rejects_duplicate_release_and_extra_fields(self) -> None:
        original = self.matrix.read_text(encoding="utf-8")
        for content in (original.replace("trixie\n", "trixie,trixie\n"),
                        original + original.splitlines()[1] + "\n",
                        original.replace("trixie\n", "trixie\t多餘\n"),
                        original.replace("bpi-test", "../bpi-test")):
            self.matrix.write_text(content, encoding="utf-8")
            self.assert_rejected(self.create())

    def test_verification_rejects_proof_digest_and_matrix_changes(self) -> None:
        self.assertEqual(self.create().returncode, 0)
        for digest in ("0" * 64, "無效"):
            self.assert_rejected(self.verify(digest), "摘要 SHA256")
        self.matrix.write_text(self.matrix.read_text(encoding="utf-8").replace("trixie", "bookworm"), encoding="utf-8")
        self.assert_rejected(self.verify(), "矩陣 SHA256")

    def test_verification_rejects_changed_missing_or_duplicate_audit_items(self) -> None:
        self.assertEqual(self.create().returncode, 0)
        path = self.fresh / "映像盤點.tsv"
        original = path.read_text(encoding="utf-8")
        for content in (original.replace("e" * 64, "1" * 64), original.replace("a" * 40, "2" * 40),
                        original.replace("b" * 64, "3" * 64), "\n".join(original.splitlines()[:-1]) + "\n",
                        original + original.splitlines()[1] + "\n", original.replace("\tminimal\t", "\txfce\t", 1)):
            with self.subTest(content=content[:40]):
                path.write_text(content, encoding="utf-8")
                self.assert_rejected(self.verify())

    def test_verification_rejects_malformed_proof_even_with_matching_digest(self) -> None:
        self.assertEqual(self.create().returncode, 0)
        original = json.loads(self.output.read_text(encoding="utf-8"))
        for fault in ("duplicate_item", "missing_item", "missing_status", "missing_sources", "missing_source", "invalid_hash", "missing_metadata", "version"):
            with self.subTest(fault=fault):
                proof = json.loads(json.dumps(original))
                if fault == "duplicate_item":
                    proof["items"].append(proof["items"][0])
                elif fault == "missing_item":
                    proof["items"].pop()
                elif fault == "missing_status":
                    del proof["migration_status"]["完整SHA256與XZ稽核"]
                elif fault == "missing_sources":
                    proof["source_files"] = {}
                elif fault == "missing_source":
                    proof["source_files"].pop(str(self.evidence / "執行狀態.tsv"))
                elif fault == "invalid_hash":
                    proof["items"][0]["sha256"] = "無效"
                elif fault == "missing_metadata":
                    del proof["items"][0]["source_commit"]
                else:
                    proof["version"] = True
                self.output.write_text(json.dumps(proof), encoding="utf-8")
                self.assert_rejected(self.verify())
        self.output.write_text('{"version": 1, "version": 1}', encoding="utf-8")
        self.assert_rejected(self.verify(), "重複欄位")

    def test_output_is_atomic_and_never_overwrites_sources(self) -> None:
        self.assertEqual(self.create().returncode, 0)
        before = self.output.read_bytes()
        self.assert_rejected(self.create(), "拒絕覆寫")
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".proof.json.*.tmp")), [])
        self.output.unlink()
        self.output.symlink_to(self.matrix)
        before = self.matrix.read_bytes()
        self.assert_rejected(self.create(), "拒絕覆寫")
        self.assertEqual(self.matrix.read_bytes(), before)
        self.output = self.state / "items" / "extra.complete"
        self.assert_rejected(self.create(), "不得位於標記目錄")
        self.assertFalse(self.output.exists())

    def test_duplicate_status_policy_and_marker_keys_are_rejected(self) -> None:
        for path in (self.evidence / "執行狀態.tsv", self.audit / "候選輸入政策.tsv",
                     self.evidence / "逐板候選輸入政策.tsv"):
            original = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                path.write_text(original + original.splitlines()[1] + "\n", encoding="utf-8")
                self.assert_rejected(self.create(), "重複鍵")
                path.write_text(original, encoding="utf-8")
        marker = self.state / "items" / "bpi-test-trixie-minimal.complete"
        marker.write_text(marker.read_text(encoding="utf-8") + "status=complete\n", encoding="utf-8")
        self.assert_rejected(self.create(), "重複")

    def test_changed_source_is_rejected_before_publication(self) -> None:
        sources = {}
        PROOF.read_bytes(self.matrix, sources)
        self.matrix.write_text(self.matrix.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "遭異動"):
            PROOF.read_bytes(self.matrix, sources)

    def test_verification_rejects_linked_proof_and_audit_directory(self) -> None:
        self.assertEqual(self.create().returncode, 0)
        digest = self.sha(self.output)
        target = self.output.with_suffix(".original")
        self.output.rename(target)
        self.output.symlink_to(target)
        self.assert_rejected(self.verify(digest), "連結")
        self.output.unlink()
        target.rename(self.output)
        moved = self.root / "renamed-audit"
        self.fresh.rename(moved)
        self.fresh.symlink_to(moved, target_is_directory=True)
        self.assert_rejected(self.verify(), "連結")

    def test_cli_requires_one_complete_mode(self) -> None:
        self.assert_rejected(self.run_tool(), "不得混用")
        self.assert_rejected(self.run_tool("--proof", str(self.output)), "不得混用")
        self.assert_rejected(self.run_tool("--candidate-state", str(self.state), "--migration-evidence", str(self.evidence),
                                           "--output", str(self.output), "--proof", str(self.output)), "不得混用")


if __name__ == "__main__":
    unittest.main()
