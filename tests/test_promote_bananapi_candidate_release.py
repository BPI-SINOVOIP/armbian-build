#!/usr/bin/env python3
from __future__ import annotations

import csv
import fcntl
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/promote-bananapi-candidate-release.sh"
RELEASE_NOTES = ("Release-Notes-zh-TW.md", "Release-Notes-English.md")


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
            (directory / "Release-Notes-English.md").write_bytes(
                f"```text\r\nboard={board}\r\nbranch={branch}\r\n```\r\n".encode()
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
        self,
        *extra: str,
        env: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
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
            pass_fds=pass_fds,
        )

    def tool_command(self, *extra: str) -> list[str]:
        return [
            "bash",
            str(SCRIPT),
            "--candidate-release",
            str(self.candidate),
            "--formal-release",
            str(self.formal),
            "--matrix",
            str(self.matrix),
            *extra,
        ]

    @staticmethod
    def fixed_lock_path(root: Path) -> Path:
        return root.parent / f".{root.name}.build.lock"

    def wait_for_path(
        self, path: Path, process: subprocess.Popen[str], timeout: float = 5.0
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(
                    f"子程序提前結束：{process.returncode}\n標準輸出：{stdout}\n錯誤輸出：{stderr}"
                )
            time.sleep(0.01)
        self.fail(f"等待同步點逾時：{path}")

    def candidate_snapshot(self) -> dict[str, tuple[bytes, int]]:
        return {
            str(path.relative_to(self.candidate)): (path.read_bytes(), path.stat().st_ino)
            for path in self.candidate.rglob("*")
            if path.is_file() and path.name != ".latest-rebuild.lock"
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
        for folder, *_ in self.rows:
            for name in RELEASE_NOTES:
                self.assertIn(f"{folder}/{name}", candidate_before)

        result = self.run_tool("--execute")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("正式發布提升完成", result.stdout)
        self.assertEqual(self.candidate_snapshot(), candidate_before)
        self.assertEqual(
            sorted(path.name for path in self.formal.iterdir()),
            sorted([*(folder for folder, *_ in self.rows), ".latest-rebuild.lock"]),
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

    def test_missing_note_and_uncontrolled_file_are_rejected(self) -> None:
        note = self.candidate / "bpi-demo-a/Release-Notes-zh-TW.md"
        note.unlink()
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少繁體中文發行說明", result.stderr)

        note.write_text("# 候選發行說明\n", encoding="utf-8")
        uncontrolled = self.candidate / "bpi-demo-a/暫存紀錄.txt"
        uncontrolled.write_text("不得發布\n", encoding="utf-8")
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("含未受控檔案", result.stderr)

    def test_missing_english_note_is_rejected_before_promotion(self) -> None:
        note = self.candidate / "bpi-demo-b/Release-Notes-English.md"
        note.unlink()
        candidate_before = self.candidate_snapshot()

        for extra in ((), ("--execute",)):
            with self.subTest(extra=extra):
                result = self.run_tool(*extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("缺少英文發行說明", result.stderr)
                self.assertIn(str(note), result.stderr)
                self.assertEqual(self.candidate_snapshot(), candidate_before)
                self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
                self.assertEqual(self.transaction_residue(), [])

    def test_symlinked_release_notes_are_rejected_before_promotion(self) -> None:
        for name in RELEASE_NOTES:
            with self.subTest(name=name):
                note = self.candidate / "bpi-demo-a" / name
                content = note.read_bytes()
                target = self.root / name
                note.rename(target)
                note.symlink_to(target)
                try:
                    result = self.run_tool("--execute")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("只允許第一層實體檔案", result.stderr)
                    self.assertIn(str(note), result.stderr)
                    self.assertTrue(note.is_symlink())
                    self.assertEqual(target.read_bytes(), content)
                    self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
                    self.assertEqual(self.transaction_residue(), [])
                finally:
                    note.unlink()
                    target.rename(note)

    def test_empty_release_notes_are_rejected_before_promotion(self) -> None:
        for name in RELEASE_NOTES:
            with self.subTest(name=name):
                note = self.candidate / "bpi-demo-a" / name
                content = note.read_bytes()
                note.write_bytes(b"")
                try:
                    result = self.run_tool("--execute")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("不是正常非空檔案", result.stderr)
                    self.assertIn(str(note), result.stderr)
                    self.assertEqual(note.read_bytes(), b"")
                    self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
                    self.assertEqual(self.transaction_residue(), [])
                finally:
                    note.write_bytes(content)

    def test_release_note_directories_are_rejected_before_promotion(self) -> None:
        for name in RELEASE_NOTES:
            with self.subTest(name=name):
                note = self.candidate / "bpi-demo-a" / name
                content = note.read_bytes()
                note.unlink()
                note.mkdir()
                try:
                    result = self.run_tool("--execute")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("只允許第一層實體檔案", result.stderr)
                    self.assertIn(str(note), result.stderr)
                    self.assertTrue(note.is_dir())
                    self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
                    self.assertEqual(self.transaction_residue(), [])
                finally:
                    note.rmdir()
                    note.write_bytes(content)

    def test_uncontrolled_note_names_are_rejected_before_promotion(self) -> None:
        for name in (
            "Release-Notes.md",
            "Release-Notes-en.md",
            "Release-Notes-en-US.md",
            "Release-Notes-English.md.bak",
            "Release-Notes-english.md",
            "README.md",
        ):
            with self.subTest(name=name):
                note = self.candidate / "bpi-demo-a" / name
                note.write_text("不得發布\n", encoding="utf-8")
                try:
                    result = self.run_tool("--execute")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("含未受控檔案", result.stderr)
                    self.assertIn(str(note), result.stderr)
                    self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
                    self.assertEqual(self.transaction_residue(), [])
                finally:
                    note.unlink()

    def test_controlled_matrix_still_promotes_45_boards_and_444_images(self) -> None:
        with (ROOT / "config/bananapi-latest-release-matrix.tsv").open(
            encoding="utf-8", newline=""
        ) as stream:
            self.rows = [
                (
                    row["folder"],
                    row["board"],
                    row["branch"],
                    tuple(row["releases"].split(",")),
                )
                for row in csv.DictReader(stream, delimiter="\t")
            ]
        self.assertEqual(len(self.rows), 45)
        self.assertEqual(sum(len(releases) * 2 for *_, releases in self.rows), 444)
        shutil.rmtree(self.candidate)
        self.candidate.mkdir()
        self.write_matrix()
        self.create_complete_candidate()
        candidate_before = self.candidate_snapshot()

        result = self.run_tool("--execute")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("45 個板目錄、444 個映像", result.stdout)
        self.assertEqual(sum(path.is_dir() for path in self.formal.iterdir()), 45)
        self.assertEqual(len(list(self.formal.rglob("*.img.xz"))), 444)
        self.assertEqual(len(list(self.formal.rglob("*.img.xz.sha"))), 444)
        for name in RELEASE_NOTES:
            self.assertEqual(len(list(self.formal.rglob(name))), 45)
        for relative, (content, inode) in candidate_before.items():
            promoted = self.formal / relative
            self.assertFalse(promoted.is_symlink())
            self.assertEqual(promoted.read_bytes(), content)
            self.assertEqual(promoted.stat().st_ino, inode)
        self.assertEqual(self.candidate_snapshot(), candidate_before)

    def test_missing_staged_english_note_aborts_before_formal_switch(self) -> None:
        fake_bin = self.root / "fake-note-ln-bin"
        fake_bin.mkdir()
        real_ln = shutil.which("ln")
        self.assertIsNotNone(real_ln)
        wrapper = fake_bin / "ln"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'"{real_ln}" "$@"\n'
            'target_path="${@: -1}"\n'
            'if [[ "$target_path" == */Release-Notes-zh-TW.md ]]; then\n'
            '  rm -- "${target_path%/*}/Release-Notes-English.md"\n'
            "fi\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        candidate_before = self.candidate_snapshot()

        result = self.run_tool(
            "--execute", env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少英文發行說明", result.stderr)
        self.assertIn(".formal.staging-", result.stderr)
        self.assertEqual(self.candidate_snapshot(), candidate_before)
        self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
        self.assertEqual(self.transaction_residue(), [])

    def test_invalid_promoted_english_note_rolls_back_the_formal_release(self) -> None:
        fake_bin = self.root / "fake-note-mv-bin"
        fake_bin.mkdir()
        real_mv = shutil.which("mv")
        real_cp = shutil.which("cp")
        self.assertIsNotNone(real_mv)
        self.assertIsNotNone(real_cp)
        wrapper = fake_bin / "mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'"{real_mv}" "$@"\n'
            'source_path="${@: -2:1}"\n'
            'target_path="${@: -1}"\n'
            'if [[ "$source_path" == "$TEST_PARENT/.formal.staging-"* &&\n'
            '    "$target_path" == "$TEST_FORMAL" ]]; then\n'
            '  note="$TEST_FORMAL/bpi-demo-b/Release-Notes-English.md"\n'
            '  original="$TEST_CANDIDATE/bpi-demo-b/Release-Notes-English.md"\n'
            '  rm -- "$note"\n'
            '  case "$TEST_NOTE_MODE" in\n'
            "  missing) ;;\n"
            '  empty) : >"$note" ;;\n'
            '  symlink) ln -s -- "$original" "$note" ;;\n'
            f'  copy) "{real_cp}" -- "$original" "$note" ;;\n'
            "  esac\n"
            "fi\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        candidate_before = self.candidate_snapshot()
        for mode, message in (
            ("missing", "缺少英文發行說明"),
            ("empty", "不是正常非空檔案"),
            ("symlink", "不是正常非空檔案"),
            ("copy", "不是候選原件的硬連結"),
        ):
            with self.subTest(mode=mode):
                result = self.run_tool(
                    "--execute",
                    env={
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "TEST_PARENT": str(self.root),
                        "TEST_FORMAL": str(self.formal),
                        "TEST_CANDIDATE": str(self.candidate),
                        "TEST_NOTE_MODE": mode,
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertIn("已將舊正式版本復原", result.stderr)
                self.assertEqual(self.candidate_snapshot(), candidate_before)
                self.assertEqual(
                    (self.formal / "舊板目錄/舊版本.txt").read_text(encoding="utf-8"),
                    "舊正式版本\n",
                )
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

    def test_competing_formal_directory_is_preserved_while_old_release_recovers(
        self,
    ) -> None:
        fake_bin = self.root / "fake-collision-mv-bin"
        fake_bin.mkdir()
        counter = self.root / "collision-mv-count"
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        wrapper = fake_bin / "mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "count=0\n"
            "[[ ! -f \"$TEST_COUNTER\" ]] || count=$(<\"$TEST_COUNTER\")\n"
            "count=$((count + 1))\n"
            "printf '%s\\n' \"$count\" >\"$TEST_COUNTER\"\n"
            "if [[ \"$count\" == 2 ]]; then\n"
            "  mkdir \"$TEST_FORMAL\"\n"
            "  printf '不得刪除\\n' >\"$TEST_FORMAL/不明資料.txt\"\n"
            "  exit 73\n"
            "fi\n"
            f"exec {real_mv} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        result = self.run_tool(
            "--execute",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "TEST_COUNTER": str(counter),
                "TEST_FORMAL": str(self.formal),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不明路徑保留於", result.stderr)
        self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())
        retained = list(self.root.glob(".formal.failed-rollback-*"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(
            (retained[0] / "不明資料.txt").read_text(encoding="utf-8"),
            "不得刪除\n",
        )
        self.assertEqual(list(self.root.glob(".formal.previous-*")), [])
        self.assertEqual(list(self.root.glob(".formal.staging-*")), [])

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

    def test_formal_root_symlink_is_rejected_before_realpath_resolution(self) -> None:
        real_formal = self.root / "正式目錄實體"
        self.formal.rename(real_formal)
        self.formal.symlink_to(real_formal, target_is_directory=True)

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("正式發布根目錄不得為符號連結", result.stderr)
        self.assertTrue((real_formal / "舊板目錄/舊版本.txt").is_file())

    def test_exact_unlocked_compat_lock_is_allowed_and_locked_one_is_rejected(
        self,
    ) -> None:
        build_lock = self.candidate / ".latest-rebuild.lock"
        build_lock.touch()
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)

        with build_lock.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仍有建置器持有根內相容鎖", result.stderr)

    def test_exact_formal_build_lock_is_archived_only_when_unlocked(self) -> None:
        build_lock = self.formal / ".latest-rebuild.lock"
        build_lock.touch()
        old_lock_inode = build_lock.stat().st_ino
        result = self.run_tool("--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.formal / ".latest-rebuild.lock").is_file())
        self.assertEqual(
            (self.formal / ".latest-rebuild.lock").stat().st_ino, old_lock_inode
        )
        previous = list(self.root.glob(".formal.previous-*"))
        self.assertEqual(len(previous), 1)
        self.assertTrue((previous[0] / ".latest-rebuild.lock").is_file())
        self.assertEqual(
            (previous[0] / ".latest-rebuild.lock").stat().st_ino, old_lock_inode
        )

        # 重新建立測試現場，確認被占用的正式建置鎖會阻擋交易。
        current_lock = self.formal / ".latest-rebuild.lock"
        current_lock.touch()
        with current_lock.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("正式發布仍有建置器持有根內相容鎖", result.stderr)

    def test_missing_fixed_locks_are_created_outside_release_roots(self) -> None:
        candidate_lock = self.fixed_lock_path(self.candidate)
        formal_lock = self.fixed_lock_path(self.formal)
        candidate_before = self.candidate_snapshot()

        self.assertFalse(candidate_lock.exists())
        self.assertFalse(formal_lock.exists())
        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(candidate_lock.is_file())
        self.assertTrue(formal_lock.is_file())
        self.assertEqual(self.candidate_snapshot(), candidate_before)
        self.assertFalse((self.candidate / ".latest-rebuild.lock").exists())
        self.assertFalse((self.formal / ".latest-rebuild.lock").exists())

    def test_fixed_candidate_and_formal_lock_conflicts_are_rejected(self) -> None:
        cases = (
            (self.fixed_lock_path(self.candidate), "候選發布固定鎖"),
            (self.fixed_lock_path(self.formal), "正式發布固定鎖"),
        )
        for lock_path, message in cases:
            with self.subTest(lock_path=lock_path):
                lock_path.touch(exist_ok=True)
                with lock_path.open("r+", encoding="utf-8") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = self.run_tool()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_fixed_lock_symlink_is_rejected_before_opening_target(self) -> None:
        target = self.root / "固定鎖目標"
        target.write_text("不得變更\n", encoding="utf-8")
        self.fixed_lock_path(self.candidate).symlink_to(target)

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("固定鎖不得為符號連結", result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "不得變更\n")

    def test_inherited_fixed_lock_fds_are_verified_and_reused(self) -> None:
        candidate_lock = self.fixed_lock_path(self.candidate)
        formal_lock = self.fixed_lock_path(self.formal)
        candidate_fd = os.open(candidate_lock, os.O_RDWR | os.O_CREAT, 0o600)
        formal_fd = os.open(formal_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(candidate_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(formal_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool(
                env={
                    "BANANAPI_CANDIDATE_BUILD_LOCK_FD": str(candidate_fd),
                    "BANANAPI_FORMAL_BUILD_LOCK_FD": str(formal_fd),
                },
                pass_fds=(candidate_fd, formal_fd),
            )
        finally:
            os.close(candidate_fd)
            os.close(formal_fd)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inherited_fixed_lock_fd_rejects_invalid_number_and_wrong_path(
        self,
    ) -> None:
        result = self.run_tool(
            env={"BANANAPI_CANDIDATE_BUILD_LOCK_FD": "不是數字"}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("鎖 FD 必須是數字", result.stderr)

        result = self.run_tool(
            env={"BANANAPI_CANDIDATE_BUILD_LOCK_FD": "999999"}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("鎖 FD 未開啟", result.stderr)

        wrong_lock = self.root / ".錯誤.build.lock"
        wrong_fd = os.open(wrong_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            result = self.run_tool(
                env={"BANANAPI_CANDIDATE_BUILD_LOCK_FD": str(wrong_fd)},
                pass_fds=(wrong_fd,),
            )
        finally:
            os.close(wrong_fd)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("鎖 FD 路徑不符", result.stderr)

    def test_read_only_root_compatibility_locks_are_supported(self) -> None:
        candidate_lock = self.candidate / ".latest-rebuild.lock"
        formal_lock = self.formal / ".latest-rebuild.lock"
        candidate_lock.touch(mode=0o444)
        formal_lock.touch(mode=0o444)
        candidate_lock.chmod(0o444)
        formal_lock.chmod(0o444)

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_root_compatibility_lock_symlink_is_rejected_before_opening_target(
        self,
    ) -> None:
        target = self.root / "相容鎖目標"
        target.write_text("不得變更\n", encoding="utf-8")
        (self.candidate / ".latest-rebuild.lock").symlink_to(target)

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("根內相容鎖不得為符號連結", result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "不得變更\n")

    def test_fixed_locks_remain_held_during_formal_directory_switch(self) -> None:
        fake_bin = self.root / "fake-paused-mv-bin"
        fake_bin.mkdir()
        counter = self.root / "paused-mv-count"
        old_moved = self.root / "old-moved"
        resume = self.root / "resume"
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        wrapper = fake_bin / "mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "count=0\n"
            "[[ ! -f \"$TEST_COUNTER\" ]] || count=$(<\"$TEST_COUNTER\")\n"
            "count=$((count + 1))\n"
            "printf '%s\\n' \"$count\" >\"$TEST_COUNTER\"\n"
            "if [[ \"$count\" == 2 ]]; then\n"
            "  : >\"$TEST_OLD_MOVED\"\n"
            "  while [[ ! -e \"$TEST_RESUME\" ]]; do sleep 0.01; done\n"
            "fi\n"
            f"exec {real_mv} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TEST_COUNTER": str(counter),
            "TEST_OLD_MOVED": str(old_moved),
            "TEST_RESUME": str(resume),
        }
        process = subprocess.Popen(
            self.tool_command("--execute"),
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.wait_for_path(old_moved, process)
            self.assertFalse(self.formal.exists())
            staging = list(self.root.glob(".formal.staging-*"))
            self.assertEqual(len(staging), 1)
            for folder, *_ in self.rows:
                for name in RELEASE_NOTES:
                    source = self.candidate / folder / name
                    staged = staging[0] / folder / name
                    self.assertFalse(staged.is_symlink())
                    self.assertEqual(staged.read_bytes(), source.read_bytes())
                    self.assertEqual(staged.stat().st_ino, source.stat().st_ino)
            for lock_path in (
                self.fixed_lock_path(self.candidate),
                self.fixed_lock_path(self.formal),
            ):
                descriptor = os.open(lock_path, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(descriptor)
        finally:
            resume.touch()
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, f"{stdout}\n{stderr}")
        self.assertTrue(self.formal.is_dir())

    def test_sigkill_during_staging_creation_leaves_predeclared_journal(self) -> None:
        fake_bin = self.root / "fake-killing-mkdir-bin"
        fake_bin.mkdir()
        marker = self.root / "提升交易.tsv"
        real_mkdir = shutil.which("mkdir")
        self.assertIsNotNone(real_mkdir)
        wrapper = fake_bin / "mkdir"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'"{real_mkdir}" "$@"\n'
            'last="${@: -1}"\n'
            'if [[ "$last" == "$TEST_PARENT/.formal.staging-"* ]]; then\n'
            '  kill -KILL "$PPID"\n'
            "  sleep 1\n"
            "fi\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        result = self.run_tool(
            "--execute",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "TEST_PARENT": str(self.root),
                "BANANAPI_PROMOTION_COMMIT_MARKER": str(marker),
            },
        )

        self.assertEqual(result.returncode, -9, result.stderr)
        self.assertIn("狀態\t準備", marker.read_text(encoding="utf-8"))
        self.assertEqual(len(list(self.root.glob(".formal.staging-*"))), 1)
        self.assertTrue((self.formal / "舊板目錄/舊版本.txt").is_file())

    def test_failed_promoted_rollback_retains_journal_for_parent_recovery(
        self,
    ) -> None:
        fake_bin = self.root / "fake-rollback-failure-bin"
        fake_bin.mkdir()
        marker = self.root / "提升交易.tsv"
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        wrapper = fake_bin / "mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'source_path="${@: -2:1}"\n'
            'target_path="${@: -1}"\n'
            'if [[ "$source_path" == "$TEST_PARENT/.formal.staging-"* &&\n'
            '    "$target_path" == "$TEST_FORMAL" ]]; then\n'
            f'  "{real_mv}" "$@"\n'
            '  rm -f -- "$TEST_FORMAL/.latest-rebuild.lock"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$source_path" == "$TEST_FORMAL" &&\n'
            '    "$target_path" == "$TEST_PARENT/.formal.failed-rollback-"* ]]; then\n'
            "  exit 73\n"
            "fi\n"
            f'exec "{real_mv}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        result = self.run_tool(
            "--execute",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "TEST_PARENT": str(self.root),
                "TEST_FORMAL": str(self.formal),
                "BANANAPI_PROMOTION_COMMIT_MARKER": str(marker),
            },
        )

        self.assertEqual(result.returncode, 94, result.stderr)
        self.assertIn("保留交易日誌供父程序復原", result.stderr)
        self.assertIn("狀態\t已備份", marker.read_text(encoding="utf-8"))
        self.assertEqual(len(list(self.root.glob(".formal.previous-*"))), 1)
        self.assertTrue((self.formal / "bpi-demo-a").is_dir())

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
