#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import lzma
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/audit-bananapi-release-state.py"


class BananaPiReleaseStateAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.formal = self.root / "formal"
        self.candidate = self.root / "candidate"
        self.state = self.root / "state"
        self.output = self.root / "output"
        self.formal.mkdir()
        self.candidate.mkdir()
        (self.state / "items").mkdir(parents=True)
        (self.state / "boards").mkdir()
        (self.state / "logs").mkdir()
        self.matrix.write_text(
            "folder\tboard\tbranch\treleases\n"
            "bpi-demo\tbananapidemonstration\tcurrent\ttrixie,bookworm\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_archive(
        self, directory: Path, release: str, profile: str, tag: str = ""
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "minimal" if profile == "minimal" else "xfce_desktop"
        archive = directory / (
            "Armbian-test_Bananapidemonstration_"
            f"{release}_current_1.0_{suffix}{tag}.img.xz"
        )
        archive.write_bytes(lzma.compress(f"{release}-{profile}".encode()))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha").write_text(
            f"{digest}  {archive.name}\n", encoding="utf-8"
        )
        return archive

    def create_candidate_item(self, release: str, profile: str) -> None:
        stage = self.candidate / ".staging-bpi-demo-source"
        archive = self.create_archive(stage, release, profile)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        log = self.state / "logs" / f"bpi-demo-{release}-{profile}.log"
        log.write_text("成功建置\n", encoding="utf-8")
        log_digest = hashlib.sha256(log.read_bytes()).hexdigest()
        marker = self.state / "items" / f"bpi-demo-{release}-{profile}.complete"
        marker.write_text(
            "source_commit=" + "a" * 40 + "\n"
            "build_context_sha256=" + "b" * 64 + "\n"
            "folder=bpi-demo\n"
            "board=bananapidemonstration\n"
            "branch=current\n"
            f"release={release}\n"
            f"profile={profile}\n"
            f"archive={archive.name}\n"
            f"sha256={digest}\n"
            f"log={log}\n"
            f"log_sha256={log_digest}\n",
            encoding="utf-8",
        )

    def run_audit(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--matrix",
                str(self.matrix),
                "--formal-release",
                str(self.formal),
                "--candidate",
                f"測試候選|{self.candidate}|{self.state}",
                "--output-dir",
                str(self.output),
                "--verify-digests",
                "--verify-xz",
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def read_tsv(self, name: str) -> list[dict[str, str]]:
        with (self.output / name).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream, delimiter="\t"))

    def test_complete_formal_board_wins_over_partial_candidate(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(self.formal / "bpi-demo", release, profile)
        self.create_candidate_item("trixie", "minimal")
        result = self.run_audit("--reuse-formal")
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "沿用既有正式")
        candidates = self.read_tsv("候選處置.tsv")
        self.assertEqual(candidates[0]["處置"], "未採用部分候選")
        self.assertEqual(self.read_tsv("待辦佇列.tsv"), [])

    def test_formal_board_is_only_a_baseline_by_default(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(self.formal / "bpi-demo", release, profile)
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "本輪全板待建")
        self.assertEqual(len(self.read_tsv("待辦佇列.tsv")), 4)

    def test_partial_candidate_is_kept_and_only_missing_items_are_queued(self) -> None:
        self.create_candidate_item("trixie", "minimal")
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "保留部分候選並補缺")
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(len(queue), 3)
        ledger = self.read_tsv("映像盤點.tsv")
        self.assertEqual(sum(row["狀態"] == "本輪已完成" for row in ledger), 1)

    def test_complete_candidate_requires_only_board_verification(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_candidate_item(release, profile)
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "候選只補整板驗證")
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["動作"], "補整板驗證")

    def test_missing_items_are_sorted_and_never_invented(self) -> None:
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(len(queue), 4)
        self.assertEqual(
            [(row["發行版"], row["類型"]) for row in queue],
            [
                ("trixie", "minimal"),
                ("trixie", "xfce"),
                ("bookworm", "minimal"),
                ("bookworm", "xfce"),
            ],
        )
        self.assertEqual(self.read_tsv("中止產物.tsv"), [])
        self.assertEqual(self.read_tsv("舊暫存目錄.tsv"), [])

    def test_valid_board_marker_selects_complete_candidate(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_candidate_item(release, profile)
        (self.state / "boards" / "bpi-demo.complete").write_text(
            "folder=bpi-demo\n"
            "board=bananapidemonstration\n"
            "branch=current\n"
            "images=4\n"
            "status=complete\n",
            encoding="utf-8",
        )
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "沿用完整候選")
        self.assertEqual(
            {row["處置"] for row in self.read_tsv("候選處置.tsv")}, {"採用"}
        )

    def test_profile_marker_can_precede_board_specific_tag(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(
                    self.formal / "bpi-demo", release, profile, "_board-tag"
                )
        result = self.run_audit("--reuse-formal")
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "沿用既有正式")
        self.assertEqual(len(self.read_tsv("映像盤點.tsv")), 4)


if __name__ == "__main__":
    unittest.main()
