#!/usr/bin/env python3
"""全矩陣混合來源發布收斂工具的替身回歸測試。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/finalize-bananapi-mixed-source-release.sh"


class BananaPiMixedSourceFinalizerTests(unittest.TestCase):
    """確認所有昂貴步驟的順序、守門與失敗邊界。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tool_repo = self.root / "tool-repo"
        self.candidate = self.root / "candidate"
        self.state = self.root / "state"
        self.formal = self.root / "formal"
        self.fake_bin = self.root / "bin"
        self.call_log = self.root / "calls.log"
        self.audit_count = self.root / "audit-count"
        self.matrix = self.root / "matrix.tsv"
        for directory in (
            self.tool_repo / "tools",
            self.candidate,
            self.state / "raw-items",
            self.state / "transactions",
            self.formal,
            self.fake_bin,
        ):
            directory.mkdir(parents=True)
        old_board = self.formal / "舊板目錄"
        old_board.mkdir()
        (old_board / "舊正式檔案.txt").write_text(
            "舊正式版本\n", encoding="utf-8"
        )
        self.write_matrix()
        for index in range(1, 46):
            directory = self.candidate / f"bpi-test-{index:02d}"
            directory.mkdir()
            (directory / "Release-Notes-zh-TW.md").write_text(
                f"原始繁中說明 {index}\n", encoding="utf-8"
            )
        self.write_test_doubles()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_executable(path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def write_matrix(self) -> None:
        releases_five = "trixie,bookworm,jammy,noble,resolute"
        releases_four = "trixie,bookworm,jammy,noble"
        lines = ["folder\tboard\tbranch\treleases"]
        for index in range(1, 46):
            releases = releases_five if index <= 42 else releases_four
            lines.append(
                f"bpi-test-{index:02d}\tbananapitest{index:02d}\tcurrent\t{releases}"
            )
        self.matrix.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_test_doubles(self) -> None:
        self.write_executable(
            self.fake_bin / "pgrep",
            r"""
            #!/usr/bin/env bash
            if [[ "${TEST_COMPILE_ACTIVE:-no}" == yes ]]; then
                printf '777 compile.sh build BOARD=test\n'
                exit 0
            fi
            exit 1
            """,
        )
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        self.write_executable(
            self.fake_bin / "mv",
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            source_path="${{@: -2:1}}"
            target_path="${{@: -1}}"
            if [[ "${{TEST_FINAL_PUBLISH_MV_FAIL:-no}}" == yes &&
                "${{source_path}}" == */.final-*.staging &&
                "${{target_path}}" == */final-* ]]; then
                exit 73
            fi
            "{real_mv}" "$@"
            if [[ "${{TEST_INTERRUPT_AFTER_STAGING_MOVE:-no}}" == yes &&
                "${{target_path}}" == */提升前空暫存/.staging-empty ]]; then
                kill -TERM "${{PPID}}"
                sleep 1
            fi
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/generate-bananapi-candidate-input-policy.py",
            r"""
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            output = Path(args[args.index("--output") + 1])
            lines = ["folder\tsource_commit\tbuild_context_sha256"]
            for index in range(1, 46):
                lines.append(f"bpi-test-{index:02d}\t{'a' * 40}\t{'b' * 64}")
            output.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with Path(os.environ["TEST_CALL_LOG"]).open("a", encoding="utf-8") as log:
                log.write("政策 " + " ".join(args) + "\n")
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/generate-bananapi-release-notes.py",
            "raise SystemExit('不得重新產生繁中說明')\n",
        )
        self.write_executable(
            self.tool_repo / "tools/translate-bananapi-release-notes-english.py",
            r"""
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            import sys

            if os.environ.get("TEST_MUTATE_CHINESE") == "yes":
                args = sys.argv[1:]
                root = Path(args[args.index("--candidate-release") + 1])
                (root / "bpi-test-01/Release-Notes-zh-TW.md").write_text(
                    "非預期修改\n", encoding="utf-8"
                )
            if os.environ.get("TEST_MUTATE_INPUTS") == "yes":
                Path(os.environ["TEST_ORIGINAL_MATRIX"]).write_text(
                    "已在快照後改變\n", encoding="utf-8"
                )
                Path(os.environ["TEST_ORIGINAL_AUDIT"]).write_text(
                    "#!/usr/bin/env python3\nraise SystemExit(99)\n",
                    encoding="utf-8",
                )
            with Path(os.environ["TEST_CALL_LOG"]).open("a", encoding="utf-8") as log:
                log.write("說明 " + " ".join(sys.argv[1:]) + "\n")
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/audit-bananapi-release-state.py",
            r"""
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            import runpy
            import shutil
            import sys

            ItemKey = runpy.run_path(os.environ["TEST_REAL_AUDIT"])["ItemKey"]
            args = sys.argv[1:]
            counter = Path(os.environ["TEST_AUDIT_COUNT"])
            count = int(counter.read_text() if counter.exists() else "0") + 1
            counter.write_text(str(count), encoding="utf-8")
            kind = "候選稽核" if count == 1 else "正式稽核"
            with Path(os.environ["TEST_CALL_LOG"]).open("a", encoding="utf-8") as log:
                log.write(kind + " " + " ".join(args) + "\n")
            if count == 1 and os.environ.get("TEST_CANDIDATE_AUDIT_FAIL") == "yes":
                raise SystemExit(31)
            if count == 2 and os.environ.get("TEST_FORMAL_AUDIT_FAIL") == "yes":
                raise SystemExit(32)

            output = Path(args[args.index("--output-dir") + 1])
            output.mkdir(parents=True)

            matrix = Path(args[args.index("--matrix") + 1])
            matrix_rows = []
            for line in matrix.read_text(encoding="utf-8").splitlines()[1:]:
                folder, board, branch, releases = line.split("\t")
                matrix_rows.append((folder, board, branch, releases.split(",")))
            ledger_header = (
                "唯一鍵\t板目錄\t板卡\t分支\t發行版\t類型\t狀態\t選用來源\t"
                "映像\tSHA256\t來源提交\t建置內容雜湊\t處置\n"
            )
            ledger_rows = []
            for folder, board, branch, releases in matrix_rows:
                for release in releases:
                    for profile in ("minimal", "xfce"):
                        key = ItemKey(folder, board, branch, release, profile).value
                        ledger_rows.append(
                            f"{key}\t{folder}\t{board}\t{branch}\t{release}\t{profile}\t"
                            f"已驗證候選\t來源\t{key}.img.xz\t{'c' * 64}\t"
                            f"{'a' * 40}\t{'b' * 64}\t不再建置\n"
                        )
            if os.environ.get("TEST_DUPLICATE_LEDGER") == "yes":
                ledger_rows[-1] = ledger_rows[0]
            (output / "映像盤點.tsv").write_text(
                ledger_header + "".join(ledger_rows), encoding="utf-8"
            )
            board_header = "板目錄\t板卡\t分支\t預期映像數\t決策\t選用來源\n"
            board_rows = [
                f"{folder}\t{board}\t{branch}\t{len(releases) * 2}\t"
                f"沿用完整候選\t來源\n"
                for folder, board, branch, releases in matrix_rows
            ]
            if os.environ.get("TEST_WRONG_BOARD_DECISION") == "yes":
                board_rows[0] = board_rows[0].replace("\t10\t", "\t8\t", 1)
            (output / "板卡決策.tsv").write_text(
                board_header + "".join(board_rows), encoding="utf-8"
            )
            empty_files = {
                "待辦佇列.tsv": "板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因\n",
                "中止產物.tsv": "候選來源\t類別\t大小bytes\t處置\t路徑\n",
                "舊暫存目錄.tsv": "目錄\t檔案數\t大小bytes\t處置\t路徑\n",
                "矩陣外項目.tsv": "板目錄\t映像數\t處置\t路徑\n",
                "候選交易殘留.tsv": (
                    "候選來源\t類別\t板目錄\t項目\t檔案數\t大小bytes\t處置\t路徑\n"
                ),
            }
            for name, content in empty_files.items():
                (output / name).write_text(content, encoding="utf-8")
            policy = Path(args[args.index("--candidate-input-policy") + 1])
            shutil.copyfile(policy, output / "候選輸入政策.tsv")
            if os.environ.get("TEST_STATUS_PATH_CONFLICT") == "yes":
                (output.parent / "執行狀態.tsv").mkdir(exist_ok=True)
            if os.environ.get("TEST_FINAL_OUTPUT_CONFLICT") == "yes":
                target_name = output.parent.name[1:-8]
                conflict = output.parent.parent / target_name
                conflict.mkdir(exist_ok=True)
                (conflict / "不明內容.txt").write_text(
                    "不得覆寫\n", encoding="utf-8"
                )
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/promote-bananapi-candidate-release.sh",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            candidate=""
            formal=""
            mode=預演
            arguments=("$@")
            while (($#)); do
                case "$1" in
                    --candidate-release) candidate="$2"; shift 2 ;;
                    --formal-release) formal="$2"; shift 2 ;;
                    --execute) mode=執行; shift ;;
                    *) shift ;;
                esac
            done
            for specification in \
                "BANANAPI_CANDIDATE_BUILD_LOCK_FD:${candidate}" \
	            "BANANAPI_FORMAL_BUILD_LOCK_FD:${formal}" \
	            "BANANAPI_CANDIDATE_COMPAT_LOCK_FD:${candidate}/.latest-rebuild.lock" \
	            "BANANAPI_FORMAL_COMPAT_LOCK_FD:${formal}/.latest-rebuild.lock"; do
                variable="${specification%%:*}"
	            expected_input="${specification#*:}"
                descriptor="${!variable:-}"
                [[ "${descriptor}" =~ ^[0-9]+$ ]]
	            if [[ "${variable}" == *_COMPAT_* ]]; then
	                expected="${expected_input}"
	            else
	                expected="$(dirname "${expected_input}")/.$(basename "${expected_input}").build.lock"
	            fi
                [[ "$(readlink -f "/proc/self/fd/${descriptor}")" == "${expected}" ]]
                flock -n "${descriptor}"
            done
            printf '提升-%s %s\n' "${mode}" "${arguments[*]}" >>"${TEST_CALL_LOG}"
            if [[ "${mode}" == 執行 && "${TEST_PROMOTE_FAIL:-no}" == yes ]]; then
                if [[ "${TEST_RESTORE_STAGING_CONFLICT:-no}" == yes ]]; then
                    mkdir -- "${formal}/.staging-empty"
                fi
                exit 41
            fi
            if [[ "${mode}" == 執行 ]]; then
                previous="$(dirname "${formal}")/.$(basename "${formal}").previous-test"
	            staging="$(dirname "${formal}")/.$(basename "${formal}").staging-test"
                mv -T -- "${formal}" "${previous}"
                mkdir -- "${formal}"
	            cp -al -- "${candidate}"/bpi-test-* "${formal}/"
	            ln -- "${previous}/.latest-rebuild.lock" \
	                "${formal}/.latest-rebuild.lock"
	            marker="${BANANAPI_PROMOTION_COMMIT_MARKER:?}"
	            {
	                printf '欄位\t值\n'
	                printf '狀態\t已提交\n'
	                printf '正式路徑\t%s\n' "${formal}"
	                printf 'previous\t%s\n' "${previous}"
	                printf 'staging\t%s\n' "${staging}"
	            } >"${marker}"
	            if [[ "${TEST_PROMOTE_COMMITTED_BUT_FAIL:-no}" == yes ]]; then
	                exit 42
	            fi
            fi
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
                "TEST_CALL_LOG": str(self.call_log),
                "TEST_AUDIT_COUNT": str(self.audit_count),
                "TEST_REAL_AUDIT": str(ROOT / "tools/audit-bananapi-release-state.py"),
                "TEST_ORIGINAL_MATRIX": str(self.matrix),
                "TEST_ORIGINAL_AUDIT": str(
                    self.tool_repo / "tools/audit-bananapi-release-state.py"
                ),
            }
        )
        environment.update(overrides)
        return environment

    def run_tool(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=self.environment(**overrides),
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )

    def final_outputs(self) -> list[Path]:
        return sorted(self.state.glob("final-*-*"))

    def failed_outputs(self) -> list[Path]:
        return sorted(self.state.glob("failed-final-*-*"))

    def old_formal_file(self) -> Path:
        return self.formal / "舊板目錄/舊正式檔案.txt"

    def test_success_uses_exact_order_and_full_audits(self) -> None:
        empty_staging = self.formal / ".staging-old"
        empty_staging.mkdir()

        result = self.run_tool(VERIFICATION_WORKERS="4")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.call_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [line.split(" ", 1)[0] for line in calls],
            ["政策", "說明", "說明", "候選稽核", "提升-預演", "提升-執行", "正式稽核", "說明"],
        )
        for line in (calls[3], calls[6]):
            self.assertIn("--verification-workers 4", line)
            self.assertIn("--verify-digests", line)
            self.assertIn("--verify-xz", line)
            self.assertIn("--candidate-input-policy", line)
        self.assertIn("--replace", calls[1])
        self.assertIn("--check", calls[2])
        self.assertIn("--check", calls[7])
        self.assertFalse(empty_staging.exists())
        outputs = self.final_outputs()
        self.assertEqual(len(outputs), 1)
        self.assertTrue((outputs[0] / "候選輸入政策.tsv").is_file())
        self.assertTrue((outputs[0] / "正式輸入政策.tsv").is_file())
        self.assertTrue((outputs[0] / "收斂結果.tsv").is_file())
        self.assertEqual(
            len((outputs[0] / "繁中說明原始雜湊.tsv").read_text().splitlines()), 46
        )
        status = (outputs[0] / "執行狀態.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t成功", status)
        self.assertIn("正式重新稽核通過\tyes", status)
        self.assertTrue((outputs[0] / "輸入快照.tsv").is_file())
        previous = list(self.root.glob(".formal.previous-*"))
        self.assertEqual(len(previous), 1)
        self.assertIn(str(previous[0]), result.stdout)

    def test_invalid_verification_worker_count_stops_before_writes(self) -> None:
        for value in ("0", "17", "invalid", "1.5"):
            with self.subTest(value=value):
                result = self.run_tool(VERIFICATION_WORKERS=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("VERIFICATION_WORKERS", result.stderr)
                self.assertFalse(self.call_log.exists())

    def test_matrix_and_tools_use_one_immutable_snapshot(self) -> None:
        result = self.run_tool(TEST_MUTATE_INPUTS="yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.final_outputs()[0]
        snapshot_matrix = output / "輸入快照/受控矩陣.tsv"
        self.assertIn("folder\tboard\tbranch\treleases", snapshot_matrix.read_text())
        self.assertEqual(self.audit_count.read_text(encoding="utf-8"), "2")
        calls = self.call_log.read_text(encoding="utf-8")
        matrix_arguments = [
            line.split("--matrix ", 1)[1].split(" ", 1)[0]
            for line in calls.splitlines()
            if "--matrix " in line
        ]
        self.assertEqual(len(matrix_arguments), 8)
        self.assertEqual(len(set(matrix_arguments)), 1)
        self.assertIn("/輸入快照/受控矩陣.tsv", matrix_arguments[0])
        manifest = (output / "輸入快照.tsv").read_text(encoding="utf-8")
        self.assertIn("輸入快照/受控矩陣.tsv", manifest)
        self.assertNotIn("/.final-", manifest)

    def test_chinese_notes_must_not_change_during_translation(self) -> None:
        result = self.run_tool(TEST_MUTATE_CHINESE="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("繁中說明與翻譯前原始雜湊不同", result.stderr)
        self.assertNotIn("提升-", self.call_log.read_text(encoding="utf-8"))
        self.assertTrue(self.old_formal_file().is_file())

    def test_audit_must_exactly_cover_matrix_keys_and_board_decisions(self) -> None:
        for variable, expected in (
            ("TEST_DUPLICATE_LEDGER", "重複唯一鍵"),
            ("TEST_WRONG_BOARD_DECISION", "板卡決策沒有精確覆蓋"),
        ):
            with self.subTest(variable=variable):
                result = self.run_tool(**{variable: "yes"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("提升-", self.call_log.read_text(encoding="utf-8"))
                self.call_log.unlink()
                self.audit_count.unlink()

    def test_incremental_lock_conflict_stops_every_step(self) -> None:
        lock = self.state / ".incremental-queue.lock"
        with lock.open("w", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool()

        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn("仍持有鎖", result.stderr)
        self.assertFalse(self.call_log.exists())

    def test_stable_candidate_or_formal_lock_conflict_stops_every_step(self) -> None:
        lock_paths = (
            self.root / ".candidate.build.lock",
            self.root / ".formal.build.lock",
        )
        for lock in lock_paths:
            with self.subTest(lock=lock):
                with lock.open("w", encoding="utf-8") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = self.run_tool()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("持有固定鎖", result.stderr)
                self.assertFalse(self.call_log.exists())

    def test_compile_process_is_rejected(self) -> None:
        result = self.run_tool(TEST_COMPILE_ACTIVE="yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("進行中的 compile.sh", result.stderr)
        self.assertFalse(self.call_log.exists())

    def test_state_and_candidate_residues_are_preserved(self) -> None:
        cases = (
            (self.state / "raw-items/image.ready", "raw-items"),
            (self.state / "transactions/board.state", "transactions"),
            (self.candidate / ".staging-board", "staging"),
            (self.candidate / ".failed-board", "failed"),
            (self.candidate / ".previous-board", "previous"),
        )
        for residue, expected in cases:
            with self.subTest(residue=residue):
                residue.parent.mkdir(parents=True, exist_ok=True)
                residue.write_text("保留現場\n", encoding="utf-8")
                result = self.run_tool()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertTrue(residue.exists())
                residue.unlink()

    def test_preexisting_parent_transaction_residue_is_not_treated_as_this_run(
        self,
    ) -> None:
        previous = self.root / ".formal.previous-old"
        staging = self.root / ".formal.staging-old"
        previous.mkdir()
        staging.mkdir()
        previous_evidence = previous / "既有 previous.txt"
        staging_evidence = staging / "既有 staging.txt"
        previous_evidence.write_text("不可改動\n", encoding="utf-8")
        staging_evidence.write_text("不可改動\n", encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("正式發布父目錄含既有交易殘留", result.stderr)
        self.assertEqual(previous_evidence.read_text(encoding="utf-8"), "不可改動\n")
        self.assertEqual(staging_evidence.read_text(encoding="utf-8"), "不可改動\n")
        self.assertTrue(self.old_formal_file().is_file())

    def test_nonempty_or_non_directory_formal_staging_is_preserved(self) -> None:
        nonempty = self.formal / ".staging-nonempty"
        nonempty.mkdir()
        evidence = nonempty / "證據.txt"
        evidence.write_text("不可刪除\n", encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不是空目錄", result.stderr)
        self.assertTrue(evidence.is_file())
        nonempty.rename(self.formal / ".kept")
        nondirectory = self.formal / ".staging-file"
        nondirectory.write_text("不可刪除\n", encoding="utf-8")

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不是實體目錄", result.stderr)
        self.assertTrue(nondirectory.is_file())

    def test_candidate_audit_failure_blocks_promotion_and_restores_empty_staging(self) -> None:
        empty_staging = self.formal / ".staging-empty"
        empty_staging.mkdir()

        result = self.run_tool(TEST_CANDIDATE_AUDIT_FAIL="yes")

        self.assertEqual(result.returncode, 31, result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertIn("候選稽核", calls)
        self.assertNotIn("提升-", calls)
        self.assertTrue(empty_staging.is_dir())
        self.assertTrue(self.old_formal_file().is_file())

    def test_promotion_failure_stops_before_formal_audit_and_restores_staging(
        self,
    ) -> None:
        empty_staging = self.formal / ".staging-empty"
        empty_staging.mkdir(mode=0o750)
        inode = empty_staging.stat().st_ino
        mode = empty_staging.stat().st_mode & 0o777
        result = self.run_tool(TEST_PROMOTE_FAIL="yes")

        self.assertEqual(result.returncode, 41, result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertIn("提升-預演", calls)
        self.assertIn("提升-執行", calls)
        self.assertNotIn("正式稽核", calls)
        self.assertTrue(self.old_formal_file().is_file())
        self.assertEqual(empty_staging.stat().st_ino, inode)
        self.assertEqual(empty_staging.stat().st_mode & 0o777, mode)
        self.assertEqual(len(self.final_outputs()), 0)
        self.assertEqual(len(self.failed_outputs()), 1)

    def test_staging_restore_conflict_changes_exit_status_and_preserves_archive(
        self,
    ) -> None:
        empty_staging = self.formal / ".staging-empty"
        empty_staging.mkdir()

        result = self.run_tool(
            TEST_PROMOTE_FAIL="yes", TEST_RESTORE_STAGING_CONFLICT="yes"
        )

        self.assertEqual(result.returncode, 95, result.stderr)
        self.assertIn("復原路徑已被佔用", result.stderr)
        failures = self.failed_outputs()
        self.assertEqual(len(failures), 1)
        self.assertTrue((failures[0] / "提升前空暫存/.staging-empty").is_dir())

    def test_signal_after_staging_move_restores_the_registered_inode(self) -> None:
        empty_staging = self.formal / ".staging-empty"
        empty_staging.mkdir(mode=0o750)
        inode = empty_staging.stat().st_ino

        result = self.run_tool(TEST_INTERRUPT_AFTER_STAGING_MOVE="yes")

        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertTrue(empty_staging.is_dir())
        self.assertEqual(empty_staging.stat().st_ino, inode)
        self.assertTrue(self.old_formal_file().is_file())

    def test_formal_reaudit_failure_rolls_back_the_formal_release(self) -> None:
        result = self.run_tool(TEST_FORMAL_AUDIT_FAIL="yes")

        self.assertEqual(result.returncode, 32, result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertIn("正式稽核", calls)
        previous = list(self.root.glob(".formal.previous-*"))
        self.assertEqual(previous, [])
        self.assertTrue(self.old_formal_file().is_file())
        self.assertIn("已復原提升前正式版本", result.stderr)
        self.assertEqual(len(self.final_outputs()), 0)
        failures = self.failed_outputs()
        self.assertEqual(len(failures), 1)
        status = (failures[0] / "執行狀態.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t失敗", status)
        self.assertIn("正式重新稽核通過\tno", status)

    def test_committed_child_failure_is_detected_and_rolled_back(self) -> None:
        result = self.run_tool(TEST_PROMOTE_COMMITTED_BUT_FAIL="yes")

        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertTrue(self.old_formal_file().is_file())
        self.assertEqual(list(self.root.glob(".formal.previous-*")), [])
        self.assertIn("已復原提升前正式版本", result.stderr)
        failures = self.failed_outputs()
        self.assertEqual(len(failures), 1)
        marker = failures[0] / "提升提交標記.tsv"
        self.assertIn("狀態\t已提交", marker.read_text(encoding="utf-8"))

    def test_dangerous_overlapping_paths_are_rejected_before_changes(self) -> None:
        original = self.old_formal_file().read_bytes()
        result = self.run_tool(CANDIDATE_RELEASE=str(self.formal))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不得相同或互相包含", result.stderr)
        self.assertEqual(self.old_formal_file().read_bytes(), original)
        self.assertFalse(self.call_log.exists())

    def test_status_file_conflict_returns_failure_and_keeps_staging_evidence(
        self,
    ) -> None:
        result = self.run_tool(TEST_STATUS_PATH_CONFLICT="yes")

        self.assertEqual(result.returncode, 94, result.stderr)
        self.assertIn("無法發布執行狀態檔", result.stderr)
        self.assertEqual(self.final_outputs(), [])
        self.assertEqual(self.failed_outputs(), [])
        staging = list(self.state.glob(".final-*.staging"))
        self.assertEqual(len(staging), 1)

    def test_final_target_conflict_rewrites_status_and_preserves_unknown_target(
        self,
    ) -> None:
        result = self.run_tool(TEST_FINAL_OUTPUT_CONFLICT="yes")

        self.assertEqual(result.returncode, 92, result.stderr)
        unknown_targets = [
            path
            for path in self.state.glob("final-*-*")
            if (path / "不明內容.txt").is_file()
        ]
        self.assertEqual(len(unknown_targets), 1)
        self.assertEqual(
            (unknown_targets[0] / "不明內容.txt").read_text(encoding="utf-8"),
            "不得覆寫\n",
        )
        staging = list(self.state.glob(".final-*.staging"))
        self.assertEqual(len(staging), 1)
        status = (staging[0] / "執行狀態.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t失敗", status)
        self.assertIn("退出碼\t92", status)
        self.assertIn(str(staging[0]), result.stderr)

    def test_final_directory_move_failure_rewrites_status_to_actual_exit_code(
        self,
    ) -> None:
        result = self.run_tool(TEST_FINAL_PUBLISH_MV_FAIL="yes")

        self.assertEqual(result.returncode, 93, result.stderr)
        staging = list(self.state.glob(".final-*.staging"))
        self.assertEqual(len(staging), 1)
        status = (staging[0] / "執行狀態.tsv").read_text(encoding="utf-8")
        self.assertIn("狀態\t失敗", status)
        self.assertIn("退出碼\t93", status)


if __name__ == "__main__":
    unittest.main()
