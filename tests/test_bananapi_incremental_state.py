#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/prepare-bananapi-incremental-state.py"
SOURCE_COMMIT = "a" * 40
BUILD_CONTEXT = "b" * 64


class BananaPiIncrementalStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release = self.root / "來源發布"
        self.state = self.root / "來源狀態"
        self.target_release = self.root / "整併發布"
        self.target_state = self.root / "整併狀態"
        (self.state / "items").mkdir(parents=True)
        (self.state / "logs").mkdir()
        stage = self.release / f".staging-bpi-demo-{SOURCE_COMMIT[:12]}"
        stage.mkdir(parents=True)
        self.archive = (
            stage / "Armbian-test_Bananapim5_trixie_current_1.0_minimal.img.xz"
        )
        self.archive.write_bytes("映像".encode())
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        Path(f"{self.archive}.sha").write_text(
            f"{self.digest}  {self.archive.name}\n", encoding="utf-8"
        )
        self.log = self.state / "logs" / "建置.log"
        self.log.write_text("完整日誌\n", encoding="utf-8")
        log_digest = hashlib.sha256(self.log.read_bytes()).hexdigest()
        self.marker = self.state / "items" / "bpi-demo-trixie-minimal.complete"
        self.marker.write_text(
            f"source_commit={SOURCE_COMMIT}\n"
            f"build_context_sha256={BUILD_CONTEXT}\n"
            "folder=bpi-demo\n"
            "board=bananapim5\n"
            "branch=current\n"
            "release=trixie\n"
            "profile=minimal\n"
            f"archive={self.archive.name}\n"
            f"sha256={self.digest}\n"
            f"log={self.log}\n"
            f"log_sha256={log_digest}\n",
            encoding="utf-8",
        )
        self.ledger = self.root / "帳本.tsv"
        with self.ledger.open("w", encoding="utf-8", newline="") as stream:
            fields = [
                "唯一鍵",
                "板目錄",
                "板卡",
                "分支",
                "發行版",
                "類型",
                "狀態",
                "選用來源",
                "映像",
                "SHA256",
                "來源提交",
                "建置內容雜湊",
            ]
            writer = csv.DictWriter(stream, fields, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerow(
                {
                    "唯一鍵": "bpi-demo/bananapim5/current/trixie/minimal",
                    "板目錄": "bpi-demo",
                    "板卡": "bananapim5",
                    "分支": "current",
                    "發行版": "trixie",
                    "類型": "minimal",
                    "狀態": "本輪已完成",
                    "選用來源": "來源一",
                    "映像": str(self.archive),
                    "SHA256": self.digest,
                    "來源提交": SOURCE_COMMIT,
                    "建置內容雜湊": BUILD_CONTEXT,
                }
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_tool(
        self, source_commit: str = SOURCE_COMMIT
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--ledger",
                str(self.ledger),
                "--candidate",
                f"來源一|{self.release}|{self.state}",
                "--target-release",
                str(self.target_release),
                "--target-state",
                str(self.target_state),
                "--source-commit",
                source_commit,
                "--build-context",
                BUILD_CONTEXT,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_imports_completed_item_without_copying_archive(self) -> None:
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        imported = (
            self.target_release
            / f".staging-bpi-demo-{SOURCE_COMMIT[:12]}"
            / self.archive.name
        )
        self.assertEqual(imported.stat().st_ino, self.archive.stat().st_ino)
        marker = self.target_state / "items" / self.marker.name
        self.assertIn(
            f"log={self.target_state}/logs/來源一-{self.log.name}",
            marker.read_text(encoding="utf-8"),
        )
        self.assertEqual(len(list((self.target_state / "items").iterdir())), 1)

    def test_rejects_mismatched_source_identity(self) -> None:
        result = self.run_tool("c" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("來源提交不符", result.stderr)

    def test_is_idempotent(self) -> None:
        first = self.run_tool()
        second = self.run_tool()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)


if __name__ == "__main__":
    unittest.main()
