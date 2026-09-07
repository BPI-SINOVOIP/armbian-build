#!/usr/bin/env python3
"""以真實提升工具驗證全矩陣收斂與正式版本回滾。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "tools/finalize-bananapi-mixed-source-release.sh"
REAL_PROMOTER = ROOT / "tools/promote-bananapi-candidate-release.sh"
REAL_NOTE_GENERATOR = ROOT / "tools/generate-bananapi-release-notes.py"
RELEASES_FIVE = ("trixie", "bookworm", "jammy", "noble", "resolute")
RELEASES_FOUR = ("trixie", "bookworm", "jammy", "noble")
PROFILES = ("minimal", "xfce_desktop")


class BananaPiMixedSourceFinalizerIntegrationTests(unittest.TestCase):
    """確認真實提升、硬連結、重新稽核與失敗回復形成完整交易。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tool_repo = self.root / "tool-repo"
        self.candidate = self.root / "candidate"
        self.state = self.root / "state"
        self.formal = self.root / "formal"
        self.fake_bin = self.root / "bin"
        self.matrix = self.root / "matrix.tsv"
        self.audit_count = self.root / "audit-count"
        self.audit_log = self.root / "audit.log"
        self.formal_observation = self.root / "正式提升觀察.txt"

        for directory in (
            self.tool_repo / "tools",
            self.candidate,
            self.state / "raw-items",
            self.state / "transactions",
            self.formal,
            self.fake_bin,
        ):
            directory.mkdir(parents=True)

        self.rows = self.write_matrix()
        self.write_complete_candidate()
        self.write_old_formal_release()
        self.write_tools()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_executable(path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def write_matrix(self) -> list[tuple[str, str, str, tuple[str, ...]]]:
        rows: list[tuple[str, str, str, tuple[str, ...]]] = []
        lines = ["folder\tboard\tbranch\treleases"]
        for index in range(1, 46):
            releases = RELEASES_FIVE if index <= 42 else RELEASES_FOUR
            folder = f"bpi-test-{index:02d}"
            board = f"bananapitest{index:02d}"
            branch = "current"
            rows.append((folder, board, branch, releases))
            lines.append(f"{folder}\t{board}\t{branch}\t{','.join(releases)}")
        self.matrix.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return rows

    def write_complete_candidate(self) -> None:
        image_count = 0
        for folder, board, branch, releases in self.rows:
            directory = self.candidate / folder
            directory.mkdir()
            board_token = board[0].upper() + board[1:]
            for release in releases:
                for profile in PROFILES:
                    archive = directory / (
                        f"Armbian-integration_{board_token}_{release}_{branch}_"
                        f"1.0_{profile}.img.xz"
                    )
                    archive.write_bytes(
                        f"{folder}\t{board}\t{release}\t{profile}\n".encode()
                    )
                    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                    Path(f"{archive}.sha").write_text(
                        f"{digest}  {archive.name}\n", encoding="utf-8"
                    )
                    image_count += 1
        self.assertEqual(image_count, 444)

    def write_old_formal_release(self) -> None:
        old_board = self.formal / "舊正式板目錄"
        old_board.mkdir()
        self.old_formal_file = old_board / "舊正式版本.txt"
        self.old_formal_file.write_text("舊正式版本\n", encoding="utf-8")
        self.old_staging = self.formal / ".staging-舊空暫存"
        self.old_staging.mkdir(mode=0o750)
        self.old_formal_inode = self.old_formal_file.stat().st_ino
        self.old_staging_inode = self.old_staging.stat().st_ino
        self.old_staging_mode = self.old_staging.stat().st_mode & 0o777

    def write_tools(self) -> None:
        shutil.copy2(
            REAL_PROMOTER,
            self.tool_repo / "tools/promote-bananapi-candidate-release.sh",
        )
        shutil.copy2(
            REAL_NOTE_GENERATOR,
            self.tool_repo / "tools/generate-bananapi-release-notes.py",
        )
        self.write_executable(
            self.fake_bin / "pgrep",
            r"""
            #!/usr/bin/env bash
            exit 1
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/generate-bananapi-candidate-input-policy.py",
            r"""
            #!/usr/bin/env python3
            import argparse
            import csv
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--matrix", type=Path, required=True)
            parser.add_argument("--candidate-state", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()

            if not (args.candidate_state / "raw-items").is_dir():
                raise SystemExit("候選狀態缺少 raw-items 目錄")
            if not (args.candidate_state / "transactions").is_dir():
                raise SystemExit("候選狀態缺少 transactions 目錄")
            with args.matrix.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
            if len(rows) != 45 or len({row["folder"] for row in rows}) != 45:
                raise SystemExit("政策替身收到的矩陣不是 45 板")

            lines = ["folder\tsource_commit\tbuild_context_sha256"]
            for row in rows:
                lines.append(f"{row['folder']}\t{'a' * 40}\t{'b' * 64}")
            args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/audit-bananapi-release-state.py",
            r"""
            #!/usr/bin/env python3
            import argparse
            import csv
            import fcntl
            import hashlib
            import os
            from pathlib import Path
            import runpy
            import shutil

            ItemKey = runpy.run_path(os.environ["TEST_REAL_AUDIT"])["ItemKey"]
            parser = argparse.ArgumentParser()
            parser.add_argument("--matrix", type=Path, required=True)
            parser.add_argument("--formal-release", type=Path, required=True)
            parser.add_argument("--candidate", required=True)
            parser.add_argument("--candidate-input-policy", type=Path, required=True)
            parser.add_argument("--output-dir", type=Path, required=True)
            parser.add_argument("--verify-digests", action="store_true")
            parser.add_argument("--verify-xz", action="store_true")
            parser.add_argument("--verification-workers", type=int, default=1)
            args = parser.parse_args()

            if not args.verify_digests or not args.verify_xz:
                raise SystemExit("整合稽核必須要求雜湊與 XZ 驗證旗標")

            def verify_inherited_lock(variable, root):
                descriptor_text = os.environ.get(variable, "")
                if not descriptor_text.isdigit():
                    raise SystemExit(f"{variable} 沒有可繼承的鎖 FD")
                descriptor = int(descriptor_text)
                descriptor_path = Path(f"/proc/self/fd/{descriptor}")
                expected = root.parent / f".{root.name}.build.lock"
                try:
                    actual = Path(os.readlink(descriptor_path))
                except OSError as error:
                    raise SystemExit(f"無法讀取繼承鎖 FD：{error}") from error
                if actual != expected or not os.path.samefile(descriptor_path, expected):
                    raise SystemExit(f"繼承鎖 FD 路徑或 inode 不符：{variable}")
                probe = os.open(expected, os.O_RDWR)
                try:
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        raise SystemExit(f"固定外部鎖未保持鎖定：{expected}")
                finally:
                    os.close(probe)

            candidate_root = Path(os.environ["TEST_CANDIDATE_ROOT"])
            formal_root = Path(os.environ["TEST_FORMAL_ROOT"])
            verify_inherited_lock("BANANAPI_CANDIDATE_BUILD_LOCK_FD", candidate_root)
            verify_inherited_lock("BANANAPI_FORMAL_BUILD_LOCK_FD", formal_root)

            source_name, release_text, state_text = args.candidate.split("|", 2)
            release_root = Path(release_text)
            state_root = Path(state_text)
            if state_root != Path(os.environ["TEST_STATE_ROOT"]):
                raise SystemExit("稽核替身收到錯誤的候選狀態目錄")

            with args.matrix.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
            if len(rows) != 45:
                raise SystemExit("稽核替身收到的矩陣不是 45 板")

            expected_folders = {row["folder"] for row in rows}
            actual_folders = {
                path.name
                for path in release_root.iterdir()
                if path.is_dir() and not path.is_symlink()
            }
            if actual_folders != expected_folders:
                raise SystemExit("稽核來源沒有精確包含 45 個板目錄")

            ledger_rows = []
            board_rows = []
            image_count = 0
            for row in rows:
                folder = row["folder"]
                board = row["board"]
                branch = row["branch"]
                releases = tuple(row["releases"].split(","))
                board_token = board[0].upper() + board[1:]
                directory = release_root / folder
                note = directory / "Release-Notes-zh-TW.md"
                if note.is_symlink() or not note.is_file() or note.stat().st_size == 0:
                    raise SystemExit(f"板目錄缺少繁中發行說明：{folder}")
                expected_names = {note.name}
                for release in releases:
                    for profile in ("minimal", "xfce_desktop"):
                        archive = directory / (
                            f"Armbian-integration_{board_token}_{release}_{branch}_"
                            f"1.0_{profile}.img.xz"
                        )
                        sidecar = Path(f"{archive}.sha")
                        if archive.is_symlink() or not archive.is_file():
                            raise SystemExit(f"缺少候選映像：{archive}")
                        if sidecar.is_symlink() or not sidecar.is_file():
                            raise SystemExit(f"缺少 SHA 邊車：{sidecar}")
                        fields = sidecar.read_text(encoding="utf-8").split()
                        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                        if fields != [digest, archive.name]:
                            raise SystemExit(f"SHA 邊車驗證失敗：{sidecar}")
                        expected_names.update((archive.name, sidecar.name))
                        profile_name = "minimal" if profile == "minimal" else "xfce"
                        key = ItemKey(folder, board, branch, release, profile_name).value
                        ledger_rows.append(
                            "\t".join(
                                (
                                    key,
                                    folder,
                                    board,
                                    branch,
                                    release,
                                    profile_name,
                                    "已驗證候選",
                                    source_name,
                                    str(archive),
                                    digest,
                                    "a" * 40,
                                    "b" * 64,
                                    "不再建置",
                                )
                            )
                        )
                        image_count += 1
                actual_names = {path.name for path in directory.iterdir()}
                if actual_names != expected_names:
                    raise SystemExit(f"板目錄含矩陣外項目：{folder}")
                board_rows.append(
                    f"{folder}\t{board}\t{branch}\t{len(releases) * 2}"
                    f"\t沿用完整候選\t{source_name}"
                )
            if image_count != 444:
                raise SystemExit(f"稽核替身實際只驗證 {image_count} 個映像")

            counter = Path(os.environ["TEST_AUDIT_COUNT"])
            count = int(counter.read_text(encoding="utf-8") if counter.exists() else "0") + 1
            counter.write_text(str(count), encoding="utf-8")
            with Path(os.environ["TEST_AUDIT_LOG"]).open("a", encoding="utf-8") as log:
                log.write(f"{count}\t{source_name}\t{release_root}\t{image_count}\n")
            if count == 2:
                compatibility_lock = release_root / ".latest-rebuild.lock"
                descriptor = os.open(compatibility_lock, os.O_RDONLY)
                try:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        raise SystemExit("正式重新稽核期間根內相容鎖未被持有")
                finally:
                    os.close(descriptor)
                Path(os.environ["TEST_FORMAL_OBSERVATION"]).write_text(
                    "正式目錄已包含 45 板與 444 個有效 SHA 映像。\n",
                    encoding="utf-8",
                )
                if os.environ.get("TEST_FORMAL_AUDIT_FAIL") == "yes":
                    raise SystemExit(32)

            args.output_dir.mkdir(parents=True)
            (args.output_dir / "映像盤點.tsv").write_text(
                "唯一鍵\t板目錄\t板卡\t分支\t發行版\t類型\t狀態\t選用來源\t"
                "映像\tSHA256\t來源提交\t建置內容雜湊\t處置\n"
                + "\n".join(ledger_rows)
                + "\n",
                encoding="utf-8",
            )
            (args.output_dir / "板卡決策.tsv").write_text(
                "板目錄\t板卡\t分支\t預期映像數\t決策\t選用來源\n"
                + "\n".join(board_rows)
                + "\n",
                encoding="utf-8",
            )
            empty_outputs = {
                "待辦佇列.tsv": "板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因\n",
                "中止產物.tsv": "候選來源\t類別\t大小bytes\t處置\t路徑\n",
                "舊暫存目錄.tsv": "目錄\t檔案數\t大小bytes\t處置\t路徑\n",
                "候選交易殘留.tsv": (
                    "候選來源\t類別\t板目錄\t項目\t檔案數\t大小bytes\t處置\t路徑\n"
                ),
            }
            for name, content in empty_outputs.items():
                (args.output_dir / name).write_text(content, encoding="utf-8")
            extras = [
                path
                for path in sorted(args.formal_release.iterdir())
                if path.is_dir()
                and not path.name.startswith(".")
                and path.name not in expected_folders
            ]
            (args.output_dir / "矩陣外項目.tsv").write_text(
                "板目錄\t映像數\t處置\t路徑\n"
                + "".join(
                    f"{path.name}\t{len(list(path.glob('*.img.xz')))}\t"
                    f"不屬於目前矩陣，先保留待封存\t{path}\n"
                    for path in extras
                ),
                encoding="utf-8",
            )
            shutil.copyfile(
                args.candidate_input_policy,
                args.output_dir / "候選輸入政策.tsv",
            )
            """,
        )

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
                "TOOL_REPO": str(self.tool_repo),
                "MATRIX_FILE": str(self.matrix),
                "CANDIDATE_RELEASE": str(self.candidate),
                "CANDIDATE_STATE": str(self.state),
                "FORMAL_RELEASE": str(self.formal),
                "TEST_AUDIT_COUNT": str(self.audit_count),
                "TEST_AUDIT_LOG": str(self.audit_log),
                "TEST_REAL_AUDIT": str(ROOT / "tools/audit-bananapi-release-state.py"),
                "TEST_CANDIDATE_ROOT": str(self.candidate),
                "TEST_FORMAL_ROOT": str(self.formal),
                "TEST_STATE_ROOT": str(self.state),
                "TEST_FORMAL_OBSERVATION": str(self.formal_observation),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        environment.update(overrides)
        return environment

    def run_finalizer(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(FINALIZER)],
            cwd=ROOT,
            env=self.environment(**overrides),
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )

    def install_killing_mv(self) -> None:
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        self.write_executable(
            self.fake_bin / "mv",
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            source_path="${{@: -2:1}}"
            target_path="${{@: -1}}"
            trigger=no
            if [[ "${{TEST_KILL_PROMOTER_PHASE:-}}" == 已備份 &&
                "${{source_path}}" == "${{TEST_FORMAL_PATH}}" &&
                "${{target_path}}" == "${{TEST_FORMAL_PARENT}}/.formal.previous-"* ]]; then
                trigger=yes
            fi
            if [[ "${{TEST_KILL_PROMOTER_PHASE:-}}" == 已提升 &&
                "${{source_path}}" == "${{TEST_FORMAL_PARENT}}/.formal.staging-"* &&
                "${{target_path}}" == "${{TEST_FORMAL_PATH}}" ]]; then
                trigger=yes
            fi
            "{real_mv}" "$@"
            if [[ "${{trigger}}" == yes ]]; then
                if [[ "${{TEST_PROMOTION_JOURNAL_DAMAGE:-}}" == 損壞 ]]; then
                    printf '內容已損壞\n' >"${{BANANAPI_PROMOTION_COMMIT_MARKER}}"
                elif [[ "${{TEST_PROMOTION_JOURNAL_DAMAGE:-}}" == 遺失 ]]; then
                    rm -f -- "${{BANANAPI_PROMOTION_COMMIT_MARKER}}"
                fi
                kill -KILL "${{PPID}}"
                sleep 1
            fi
            """,
        )

    def transaction_residue(self) -> list[Path]:
        patterns = (
            ".formal.staging-*",
            ".formal.failed-*",
            ".formal.previous-*",
        )
        return sorted(path for pattern in patterns for path in self.root.glob(pattern))

    def test_real_promoter_creates_hardlinks_and_reaudits_formal_release(self) -> None:
        result = self.run_finalizer()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.formal_observation.is_file())
        audit_calls = self.audit_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(audit_calls), 2)
        self.assertEqual(audit_calls[0].split("\t")[1], "整併候選")
        self.assertEqual(audit_calls[1].split("\t")[1], "正式發布")
        self.assertTrue(audit_calls[1].endswith("\t444"))

        candidate_files = sorted(
            path
            for path in self.candidate.rglob("*")
            if path.is_file() and path.name != ".latest-rebuild.lock"
        )
        self.assertEqual(len(candidate_files), 444 * 2 + 45)
        for candidate_file in candidate_files:
            relative = candidate_file.relative_to(self.candidate)
            promoted_file = self.formal / relative
            self.assertTrue(promoted_file.is_file(), relative)
            self.assertTrue(os.path.samefile(candidate_file, promoted_file), relative)
            self.assertEqual(candidate_file.read_bytes(), promoted_file.read_bytes())

        previous = list(self.root.glob(".formal.previous-*"))
        self.assertEqual(len(previous), 1)
        restored_old_file = previous[0] / "舊正式板目錄/舊正式版本.txt"
        self.assertEqual(restored_old_file.read_text(encoding="utf-8"), "舊正式版本\n")
        self.assertEqual(restored_old_file.stat().st_ino, self.old_formal_inode)
        self.assertFalse((previous[0] / self.old_staging.name).exists())

        outputs = list(self.state.glob("final-*-*"))
        self.assertEqual(len(outputs), 1)
        self.assertTrue((outputs[0] / "候選完整稽核/映像盤點.tsv").is_file())
        self.assertTrue((outputs[0] / "正式完整稽核/映像盤點.tsv").is_file())
        status = (outputs[0] / "執行狀態.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t成功", status)
        self.assertIn("正式提升完成\tyes", status)
        self.assertIn("正式重新稽核通過\tyes", status)
        marker = (outputs[0] / "提升提交標記.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t已提交", marker)
        self.assertIn(f"正式路徑\t{self.formal}", marker)
        archived_staging = outputs[0] / "提升前空暫存" / self.old_staging.name
        self.assertTrue(archived_staging.is_dir())
        self.assertEqual(archived_staging.stat().st_ino, self.old_staging_inode)
        self.assertTrue((self.root / ".candidate.build.lock").is_file())
        self.assertTrue((self.root / ".formal.build.lock").is_file())
        self.assertTrue((self.candidate / ".latest-rebuild.lock").is_file())
        self.assertTrue((self.formal / ".latest-rebuild.lock").is_file())
        self.assertTrue(
            os.path.samefile(
                self.formal / ".latest-rebuild.lock",
                previous[0] / ".latest-rebuild.lock",
            )
        )

    def test_formal_reaudit_failure_atomically_restores_old_formal_release(
        self,
    ) -> None:
        result = self.run_finalizer(TEST_FORMAL_AUDIT_FAIL="yes")

        self.assertEqual(result.returncode, 32, result.stderr)
        self.assertTrue(self.formal_observation.is_file())
        self.assertEqual(self.audit_count.read_text(encoding="utf-8"), "2")
        self.assertTrue(self.old_formal_file.is_file())
        self.assertEqual(
            self.old_formal_file.read_text(encoding="utf-8"), "舊正式版本\n"
        )
        self.assertEqual(self.old_formal_file.stat().st_ino, self.old_formal_inode)
        self.assertTrue(self.old_staging.is_dir())
        self.assertEqual(self.old_staging.stat().st_ino, self.old_staging_inode)
        self.assertEqual(self.old_staging.stat().st_mode & 0o777, self.old_staging_mode)
        self.assertEqual(self.transaction_residue(), [])
        self.assertEqual(list(self.state.glob("final-*-*")), [])

        failures = list(self.state.glob("failed-final-*-*"))
        self.assertEqual(len(failures), 1)
        status = (failures[0] / "執行狀態.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t失敗", status)
        self.assertIn("退出碼\t32", status)
        self.assertIn("正式提升完成\tno", status)
        self.assertIn("正式重新稽核通過\tno", status)
        self.assertIn("已復原提升前正式版本", result.stderr)

    def test_sigkill_after_old_formal_move_recovers_and_archives_staging(
        self,
    ) -> None:
        self.install_killing_mv()
        result = self.run_finalizer(
            TEST_KILL_PROMOTER_PHASE="已備份",
            TEST_FORMAL_PATH=str(self.formal),
            TEST_FORMAL_PARENT=str(self.root),
        )

        self.assertEqual(result.returncode, 137, result.stderr)
        self.assertTrue(self.old_formal_file.is_file())
        self.assertEqual(self.old_formal_file.stat().st_ino, self.old_formal_inode)
        self.assertTrue(self.old_staging.is_dir())
        self.assertEqual(self.transaction_residue(), [])
        failures = list(self.state.glob("failed-final-*-*"))
        self.assertEqual(len(failures), 1)
        self.assertTrue((failures[0] / "中斷提升候選暫存").is_dir())
        marker = (failures[0] / "提升提交標記.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t準備", marker)

    def test_sigkill_after_new_formal_move_uses_journal_to_roll_back(self) -> None:
        self.install_killing_mv()
        result = self.run_finalizer(
            TEST_KILL_PROMOTER_PHASE="已提升",
            TEST_FORMAL_PATH=str(self.formal),
            TEST_FORMAL_PARENT=str(self.root),
        )

        self.assertEqual(result.returncode, 137, result.stderr)
        self.assertTrue(self.old_formal_file.is_file())
        self.assertEqual(self.old_formal_file.stat().st_ino, self.old_formal_inode)
        self.assertTrue(self.old_staging.is_dir())
        self.assertEqual(self.transaction_residue(), [])
        self.assertIn("正式目錄就位後中斷", result.stderr)
        failures = list(self.state.glob("failed-final-*-*"))
        self.assertEqual(len(failures), 1)
        marker = (failures[0] / "提升提交標記.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t已備份", marker)

    def test_corrupt_journal_after_promotion_uses_unique_topology_to_roll_back(
        self,
    ) -> None:
        self.install_killing_mv()
        result = self.run_finalizer(
            TEST_KILL_PROMOTER_PHASE="已提升",
            TEST_PROMOTION_JOURNAL_DAMAGE="損壞",
            TEST_FORMAL_PATH=str(self.formal),
            TEST_FORMAL_PARENT=str(self.root),
        )

        self.assertEqual(result.returncode, 137, result.stderr)
        self.assertTrue(self.old_formal_file.is_file())
        self.assertEqual(self.old_formal_file.stat().st_ino, self.old_formal_inode)
        self.assertEqual(self.transaction_residue(), [])
        self.assertIn("日誌遺失或損壞", result.stderr)
        self.assertIn("正式目錄就位後中斷", result.stderr)

    def test_missing_journal_after_promotion_uses_unique_topology_to_roll_back(
        self,
    ) -> None:
        self.install_killing_mv()
        result = self.run_finalizer(
            TEST_KILL_PROMOTER_PHASE="已提升",
            TEST_PROMOTION_JOURNAL_DAMAGE="遺失",
            TEST_FORMAL_PATH=str(self.formal),
            TEST_FORMAL_PARENT=str(self.root),
        )

        self.assertEqual(result.returncode, 137, result.stderr)
        self.assertTrue(self.old_formal_file.is_file())
        self.assertEqual(self.old_formal_file.stat().st_ino, self.old_formal_inode)
        self.assertEqual(self.transaction_residue(), [])
        self.assertIn("日誌遺失或損壞", result.stderr)
        self.assertIn("正式目錄就位後中斷", result.stderr)


if __name__ == "__main__":
    unittest.main()
