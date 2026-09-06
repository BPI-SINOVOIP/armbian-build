#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/promote-bananapi-candidate-release.sh"


class BananaPiCandidatePromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.formal = self.root / "formal"
        self.matrix = self.root / "matrix.tsv"
        self.audit = self.root / "audit"
        self.candidate.mkdir()
        self.formal.mkdir()
        self.rows = [
            ("bpi-demo-a", "bananapidemonstrationa", "current", ("trixie", "bookworm")),
            ("bpi-demo-b", "bananapidemonstrationb", "edge", ("noble",)),
        ]
        self.write_matrix()
        self.create_complete_candidate()
        old_board = self.formal / "舊板目錄"
        old_board.mkdir()
        (old_board / "舊版本.txt").write_text("舊正式版本\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_matrix(self) -> None:
        lines = ["folder\tboard\tbranch\treleases"]
        for folder, board, branch, releases in self.rows:
            lines.append(f"{folder}\t{board}\t{branch}\t{','.join(releases)}")
        self.matrix.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def create_complete_candidate(self) -> None:
        for folder, board, branch, releases in self.rows:
            directory = self.candidate / folder
            directory.mkdir()
            (directory / "Release-Notes-zh-TW.md").write_text(
                f"# {folder} 發布說明\n", encoding="utf-8"
            )
            token = board[0].upper() + board[1:]
            for release in releases:
                for suffix in ("minimal", "xfce_desktop"):
                    archive = directory / (
                        "Armbian-test_"
                        f"{token}_{release}_{branch}_1.0_{suffix}.img.xz"
                    )
                    archive.write_bytes(f"{folder}-{release}-{suffix}".encode())
                    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                    Path(f"{archive}.sha").write_text(
                        f"{digest}  {archive.name}\n", encoding="utf-8"
                    )

    def run_tool(
        self, *extra: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--candidate-release",
                str(self.candidate),
                "--formal-release",
                str(self.formal),
                "--matrix",
                str(self.matrix),
                *extra,
            ],
            cwd=ROOT,
            env={**os.environ, **(env or {})},
            text=True,
            capture_output=True,
            check=False,
        )

    def candidate_snapshot(self) -> dict[str, tuple[bytes, int]]:
        return {
            str(path.relative_to(self.candidate)): (path.read_bytes(), path.stat().st_ino)
            for path in self.candidate.rglob("*")
            if path.is_file()
        }

    def transaction_residue(self) -> list[Path]:
        patterns = (
            ".formal.staging-*",
            ".formal.failed-*",
            ".formal.previous-*",
        )
        return [path for pattern in patterns for path in self.root.glob(pattern)]

    def test_default_dry_run_does_not_change_either_release(self) -> None:
        candidate_before = self.candidate_snapshot()
        formal_before = (self.formal / "舊板目錄/舊版本.txt").read_bytes()

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("預演完成", result.stdout)
        self.assertEqual(self.candidate_snapshot(), candidate_before)
        self.assertEqual(
            (self.formal / "舊板目錄/舊版本.txt").read_bytes(), formal_before
        )
        self.assertEqual(self.transaction_residue(), [])

    def test_execute_atomically_promotes_hardlinks_and_retains_previous(self) -> None:
        candidate_before = self.candidate_snapshot()

        result = self.run_tool("--execute")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("正式發布提升完成", result.stdout)
        self.assertEqual(self.candidate_snapshot(), candidate_before)
        self.assertEqual(
            sorted(path.name for path in self.formal.iterdir()),
            sorted(folder for folder, *_ in self.rows),
        )
        for relative, (content, inode) in candidate_before.items():
            promoted = self.formal / relative
            self.assertEqual(promoted.read_bytes(), content)
            self.assertEqual(promoted.stat().st_ino, inode)
        previous = list(self.root.glob(".formal.previous-*"))
        self.assertEqual(len(previous), 1)
        self.assertEqual(
            (previous[0] / "舊板目錄/舊版本.txt").read_text(encoding="utf-8"),
            "舊正式版本\n",
        )
        self.assertEqual(list(self.root.glob(".formal.staging-*")), [])
        self.assertEqual(list(self.root.glob(".formal.failed-*")), [])

    def test_missing_archive_fails_before_the_formal_release_changes(self) -> None:
        missing = next(self.candidate.rglob("*.img.xz"))
        missing.unlink()

        result = self.run_tool("--execute")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("映像數量錯誤", result.stderr)
        self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
        self.assertEqual(self.transaction_residue(), [])

    def test_cross_filesystem_test_double_is_rejected_without_copying(self) -> None:
        fake_bin = self.root / "fake-stat-bin"
        fake_bin.mkdir()
        real_stat = shutil.which("stat")
        self.assertIsNotNone(real_stat)
        wrapper = fake_bin / "stat"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "last=\"${!#}\"\n"
            "if [[ \"$last\" == \"$TEST_CANDIDATE\" ]]; then echo 101; exit 0; fi\n"
            "if [[ \"$last\" == \"$TEST_FORMAL\" ]]; then echo 202; exit 0; fi\n"
            "if [[ \"$last\" == \"$TEST_PARENT\" ]]; then echo 202; exit 0; fi\n"
            f"exec {real_stat} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        result = self.run_tool(
            "--execute",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "TEST_CANDIDATE": str(self.candidate.resolve()),
                "TEST_FORMAL": str(self.formal.resolve()),
                "TEST_PARENT": str(self.root.resolve()),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不在同一檔案系統", result.stderr)
        self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
        self.assertEqual(self.transaction_residue(), [])

    def test_second_rename_failure_restores_the_old_formal_release(self) -> None:
        fake_bin = self.root / "fake-mv-bin"
        fake_bin.mkdir()
        counter = self.root / "mv-count"
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        wrapper = fake_bin / "mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "count=0\n"
            "[[ ! -f \"$TEST_COUNTER\" ]] || count=$(<\"$TEST_COUNTER\")\n"
            "count=$((count + 1))\n"
            "printf '%s\\n' \"$count\" >\"$TEST_COUNTER\"\n"
            "if [[ \"$count\" == 2 ]]; then exit 73; fi\n"
            f"exec {real_mv} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        result = self.run_tool(
            "--execute",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "TEST_COUNTER": str(counter),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("無法把候選 staging 原子切換", result.stderr)
        self.assertIn("已將舊正式版本復原", result.stderr)
        self.assertEqual(
            (self.formal / "舊板目錄/舊版本.txt").read_text(encoding="utf-8"),
            "舊正式版本\n",
        )
        self.assertEqual(self.transaction_residue(), [])

    def test_parent_directory_lock_blocks_a_second_transaction(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool("--execute")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("另一個發布提升交易正鎖定", result.stderr)
        self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
        self.assertEqual(self.transaction_residue(), [])

    def test_root_regular_file_and_residual_directory_are_rejected(self) -> None:
        root_file = self.candidate / "不允許.txt"
        root_file.write_text("不得放在根目錄\n", encoding="utf-8")
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("根目錄不得含一般檔案", result.stderr)

        root_file.unlink()
        residual = self.candidate / ".staging-bpi-demo-a-test"
        residual.mkdir()
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("殘留交易目錄", result.stderr)

    def test_optional_audit_output_is_a_strict_additional_gate(self) -> None:
        self.audit.mkdir()
        expected_images = sum(len(releases) * 2 for *_, releases in self.rows)
        ledger_header = (
            "唯一鍵\t板目錄\t板卡\t分支\t發行版\t類型\t狀態\t選用來源\t"
            "映像\tSHA256\t來源提交\t建置內容雜湊\t處置\n"
        )
        ledger_rows = [
            f"鍵{i}\t板{i}\t板卡\t分支\t版本\t類型\t已驗證候選\t候選\t映像\t雜湊\t提交\t內容\t不再建置"
            for i in range(expected_images)
        ]
        (self.audit / "映像盤點.tsv").write_text(
            ledger_header + "\n".join(ledger_rows) + "\n", encoding="utf-8"
        )
        board_header = "板目錄\t板卡\t分支\t預期映像數\t決策\t選用來源\n"
        board_rows = [
            f"{folder}\t{board}\t{branch}\t{len(releases) * 2}\t沿用完整候選\t候選"
            for folder, board, branch, releases in self.rows
        ]
        (self.audit / "板卡決策.tsv").write_text(
            board_header + "\n".join(board_rows) + "\n", encoding="utf-8"
        )
        policy_header = "folder\tsource_commit\tbuild_context_sha256\n"
        policy_rows = [
            f"{folder}\t{'a' * 40}\t{'b' * 64}"
            for folder, *_ in self.rows
        ]
        (self.audit / "候選輸入政策.tsv").write_text(
            policy_header + "\n".join(policy_rows) + "\n", encoding="utf-8"
        )
        empty_tables = {
            "待辦佇列.tsv": "板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因\n",
            "中止產物.tsv": "候選來源\t類別\t大小bytes\t處置\t路徑\n",
            "舊暫存目錄.tsv": "目錄\t檔案數\t大小bytes\t處置\t路徑\n",
            "矩陣外項目.tsv": "板目錄\t映像數\t處置\t路徑\n",
            "候選交易殘留.tsv": (
                "候選來源\t類別\t板目錄\t項目\t檔案數\t大小bytes\t處置\t路徑\n"
            ),
        }
        for name, header in empty_tables.items():
            (self.audit / name).write_text(header, encoding="utf-8")

        result = self.run_tool("--audit-output", str(self.audit))
        self.assertEqual(result.returncode, 0, result.stderr)

        with (self.audit / "待辦佇列.tsv").open("a", encoding="utf-8") as stream:
            stream.write("bpi-demo-a\t板卡\tcurrent\ttrixie\tminimal\t建置\t缺少\n")
        result = self.run_tool("--audit-output", str(self.audit))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仍有阻擋項目", result.stderr)

    def test_audit_output_requires_complete_policy_and_clean_transactions(self) -> None:
        self.audit.mkdir()
        expected_images = sum(len(releases) * 2 for *_, releases in self.rows)
        ledger_header = (
            "唯一鍵\t板目錄\t板卡\t分支\t發行版\t類型\t狀態\t選用來源\t"
            "映像\tSHA256\t來源提交\t建置內容雜湊\t處置\n"
        )
        ledger_rows = [
            f"鍵{i}\t板{i}\t板卡\t分支\t版本\t類型\t已驗證候選\t候選\t映像\t雜湊\t提交\t內容\t不再建置"
            for i in range(expected_images)
        ]
        (self.audit / "映像盤點.tsv").write_text(
            ledger_header + "\n".join(ledger_rows) + "\n", encoding="utf-8"
        )
        board_rows = [
            f"{folder}\t{board}\t{branch}\t{len(releases) * 2}\t沿用完整候選\t候選"
            for folder, board, branch, releases in self.rows
        ]
        (self.audit / "板卡決策.tsv").write_text(
            "板目錄\t板卡\t分支\t預期映像數\t決策\t選用來源\n"
            + "\n".join(board_rows)
            + "\n",
            encoding="utf-8",
        )
        empty_tables = {
            "待辦佇列.tsv": "板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因\n",
            "中止產物.tsv": "候選來源\t類別\t大小bytes\t處置\t路徑\n",
            "舊暫存目錄.tsv": "目錄\t檔案數\t大小bytes\t處置\t路徑\n",
            "矩陣外項目.tsv": "板目錄\t映像數\t處置\t路徑\n",
            "候選交易殘留.tsv": (
                "候選來源\t類別\t板目錄\t項目\t檔案數\t大小bytes\t處置\t路徑\n"
            ),
        }
        for name, header in empty_tables.items():
            (self.audit / name).write_text(header, encoding="utf-8")

        result = self.run_tool("--audit-output", str(self.audit))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("候選輸入政策.tsv", result.stderr)

        policy_rows = [
            f"{folder}\t{'a' * 40}\t{'b' * 64}"
            for folder, *_ in self.rows
        ]
        (self.audit / "候選輸入政策.tsv").write_text(
            "folder\tsource_commit\tbuild_context_sha256\n"
            + "\n".join(policy_rows)
            + "\n",
            encoding="utf-8",
        )
        with (self.audit / "候選交易殘留.tsv").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write("候選\t交易狀態\tbpi-demo-a\t狀態\t1\t0\t等待\t路徑\n")
        result = self.run_tool("--audit-output", str(self.audit))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("候選交易殘留.tsv", result.stderr)


if __name__ == "__main__":
    unittest.main()
