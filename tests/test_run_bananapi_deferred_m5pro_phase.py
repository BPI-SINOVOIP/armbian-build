#!/usr/bin/env python3
"""M5 Pro 延後階段受控接續工具的替身回歸測試。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run-bananapi-deferred-m5pro-phase.sh"
OLD_SOURCE = "c8673931c96c23c510dd29a28440e77f0b03286f"
OLD_CONTEXT = "6a9a29372b9ef64baaf790b24ccad2f3b4ba6940a6ddfa932ddd8bb08171a633"
NEW_CONTEXT = "d" * 64


class BananaPiDeferredM5ProPhaseTests(unittest.TestCase):
    """確認延後階段只在完整且無殘留的狀態下接續。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tool_repo = self.root / "tool-repo"
        self.build_repo = self.root / "build-repo"
        self.candidate = self.root / "candidate"
        self.state = self.root / "state"
        self.formal = self.root / "formal"
        self.cache_lower = self.root / "cache-lower"
        self.overlay = self.root / "overlay"
        self.fake_bin = self.root / "bin"
        self.call_log = self.root / "calls.log"
        for directory in (
            self.tool_repo / "tools",
            self.build_repo / "tools",
            self.candidate,
            self.state / "raw-items",
            self.state / "transactions",
            self.state / "boards",
            self.state / "items",
            self.formal,
            self.cache_lower,
            self.fake_bin,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.build_repo / "cache").mkdir()
        self.matrix = self.root / "matrix.tsv"
        self.matrix.write_text(
            "folder\tboard\tbranch\treleases\n"
            "bpi-m5pro\tbananapim5pro\tedge\t"
            "trixie,bookworm,jammy,noble,resolute\n",
            encoding="utf-8",
        )
        self.write_test_doubles()
        self.initialise_build_repository()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_executable(path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def initialise_build_repository(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.build_repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.build_repo), "config", "user.name", "測試者"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.build_repo),
                "config",
                "user.email",
                "test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.build_repo), "add", "tools"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.build_repo), "commit", "-qm", "建立測試建置器"],
            check=True,
        )
        self.source_commit = subprocess.check_output(
            ["git", "-C", str(self.build_repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(self.build_repo),
                "update-ref",
                "refs/remotes/origin/test",
                self.source_commit,
            ],
            check=True,
        )

    def write_test_doubles(self) -> None:
        self.write_executable(
            self.fake_bin / "pgrep",
            """
            #!/usr/bin/env bash
            if [[ "${TEST_ACTIVE_BUILD:-no}" == yes ]]; then
                printf '123 compile.sh build\n'
                exit 0
            fi
            exit 1
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/audit-bananapi-release-state.py",
            """
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            output = Path(args[args.index("--output-dir") + 1])
            output.mkdir(parents=True)
            final = "--candidate-input-policy" in args
            with Path(os.environ["TEST_CALL_LOG"]).open("a", encoding="utf-8") as log:
                log.write(
                    ("稽核-最終 " if final else "稽核-前置 ")
                    + " ".join(args)
                    + "\\n"
                )
            queue = output / "待辦佇列.tsv"
            header = "板目錄\\t板卡\\t分支\\t發行版\\t類型\\t動作\\t原因\\n"
            rows = []
            if final:
                count = int(os.environ.get("TEST_FINAL_COUNT", "0"))
            else:
                count = int(os.environ.get("TEST_PREFLIGHT_COUNT", "10"))
            combinations = [
                (release, profile)
                for release in ("trixie", "bookworm", "jammy", "noble", "resolute")
                for profile in ("minimal", "xfce")
            ]
            for index in range(count):
                folder = os.environ.get("TEST_PREFLIGHT_FOLDER", "bpi-m5pro")
                action = "建置缺少項目" if not final else "等待整板交易收斂"
                release, profile = combinations[index % len(combinations)]
                rows.append(
                    f"{folder}\\tbananapim5pro\\tedge\\t{release}\\t{profile}\\t"
                    f"{action}\\t替身項目 {index}\\n"
                )
            queue.write_text(header + "".join(rows), encoding="utf-8")
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/generate-bananapi-candidate-input-policy.py",
            """
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            output = Path(args[args.index("--output") + 1])
            output.write_text(
                "folder\\tsource_commit\\tbuild_context_sha256\\n"
                f"bpi-m5pro\\t{os.environ['SOURCE_COMMIT']}\\t"
                f"{os.environ['EXPECTED_BUILD_CONTEXT_SHA256']}\\n",
                encoding="utf-8",
            )
            with Path(os.environ["TEST_CALL_LOG"]).open("a", encoding="utf-8") as log:
                log.write("產生政策 " + " ".join(args) + "\\n")
            """,
        )
        self.write_executable(
            self.tool_repo / "tools/run-bananapi-candidates-isolated-cache.sh",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            printf '隔離執行 %s|%s|%s|%s|%s|%s|%s\\n' \\
                "${CANDIDATE_BUILDER}" "${CACHE_LOWER}" "${CACHE_TARGET}" \\
                "${CACHE_OVERLAY_ROOT}" "${XZ_THREADS}" "${MINIMUM_FREE_GIB}" "$*" \\
                >>"${TEST_CALL_LOG}"
            "${CANDIDATE_BUILDER}" "$@"
            """,
        )
        self.write_executable(
            self.build_repo / "tools/rebuild-bananapi-latest-release.sh",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            printf '重建 %s\\n' "$*" >>"${TEST_CALL_LOG}"
            if [[ "${TEST_BUILD_FAIL:-no}" == yes ]]; then
                printf '替身建置失敗。\\n' >&2
                exit 42
            fi
            release_dir="${RELEASE_ROOT}/bpi-m5pro"
            mkdir -p "${release_dir}" "${STATE_ROOT}/boards"
            for index in $(seq 1 10); do
                archive="${release_dir}/Armbian-test-${index}.img.xz"
                printf 'xz-%s\\n' "${index}" >"${archive}"
                printf '%064d  %s\\n' 0 "$(basename "${archive}")" >"${archive}.sha"
            done
            marker_context="${EXPECTED_BUILD_CONTEXT_SHA256}"
            if [[ "${TEST_MARKER_MISMATCH:-no}" == yes ]]; then
                marker_context="$(printf 'e%.0s' {1..64})"
            fi
            cat >"${STATE_ROOT}/boards/bpi-m5pro.complete" <<EOF
            source_commit=${SOURCE_COMMIT}
            build_context_sha256=${marker_context}
            folder=bpi-m5pro
            board=bananapim5pro
            branch=edge
            images=10
            status=complete
            EOF
            """,
        )

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
                "TOOL_REPO": str(self.tool_repo),
                "BUILD_REPO": str(self.build_repo),
                "MATRIX_FILE": str(self.matrix),
                "CANDIDATE_RELEASE": str(self.candidate),
                "CANDIDATE_STATE": str(self.state),
                "FORMAL_RELEASE": str(self.formal),
                "CACHE_LOWER": str(self.cache_lower),
                "CACHE_OVERLAY_ROOT": str(self.overlay),
                "SOURCE_COMMIT": self.source_commit,
                "SOURCE_REMOTE_REF": "origin/test",
                "EXPECTED_BUILD_CONTEXT_SHA256": NEW_CONTEXT,
                "ARMBIAN_CONTAINER_IMAGE": "registry.invalid/armbian@sha256:" + "a" * 64,
                "TEST_CALL_LOG": str(self.call_log),
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

    def test_preflight_success_uses_old_identity_and_m5_only_defaults(self) -> None:
        old_staging = self.candidate / f".staging-bpi-m5pro-{OLD_SOURCE[:12]}"
        old_staging.mkdir()

        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        preflight = next(line for line in calls.splitlines() if line.startswith("稽核-前置"))
        self.assertIn(f"--target-source-commit {OLD_SOURCE}", preflight)
        self.assertIn(f"--target-build-context {OLD_CONTEXT}", preflight)
        self.assertIn("--board bpi-m5pro", calls)
        self.assertIn("|6|120|--board bpi-m5pro", calls)
        self.assertFalse(old_staging.exists())

    def test_preflight_must_have_exactly_ten_missing_items(self) -> None:
        result = self.run_tool(TEST_PREFLIGHT_COUNT="9")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("恰好只剩", result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertNotIn("隔離執行", calls)

    def test_incremental_lock_conflict_is_rejected(self) -> None:
        lock = self.state / ".incremental-queue.lock"
        with lock.open("w", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_tool()

        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn("仍持有鎖", result.stderr)
        self.assertFalse(self.call_log.exists())

    def test_raw_or_transaction_residue_is_rejected(self) -> None:
        for directory, name in (
            (self.state / "raw-items", "殘留.ready"),
            (self.state / "transactions", "bpi-m5pro.state"),
        ):
            with self.subTest(directory=directory.name):
                residue = directory / name
                residue.write_text("不得略過\n", encoding="utf-8")
                result = self.run_tool()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("必須為空", result.stderr)
                self.assertTrue(residue.exists())
                residue.unlink()

    def test_nonempty_or_unrelated_staging_is_never_removed(self) -> None:
        old_staging = self.candidate / f".staging-bpi-m5pro-{OLD_SOURCE[:12]}"
        old_staging.mkdir()
        (old_staging / "證據.txt").write_text("保留\n", encoding="utf-8")
        unrelated = self.candidate / ".staging-bpi-other-123456789012"
        unrelated.mkdir()

        result = self.run_tool()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不是空目錄", result.stderr)
        self.assertTrue((old_staging / "證據.txt").is_file())
        self.assertTrue(unrelated.is_dir())

    def test_build_failure_stops_before_policy_and_final_audit(self) -> None:
        result = self.run_tool(TEST_BUILD_FAIL="yes")

        self.assertEqual(result.returncode, 42, result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertIn("重建 --board bpi-m5pro", calls)
        self.assertNotIn("產生政策", calls)
        self.assertNotIn("稽核-最終", calls)

    def test_mismatched_board_marker_is_rejected(self) -> None:
        result = self.run_tool(TEST_MARKER_MISMATCH="yes")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("建置內容雜湊不符", result.stderr)
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertNotIn("產生政策", calls)

    def test_successful_continuation_generates_policy_and_zero_pending_audit(self) -> None:
        result = self.run_tool()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("延後階段接續完成", result.stdout)
        marker = self.state / "boards/bpi-m5pro.complete"
        self.assertIn(f"source_commit={self.source_commit}", marker.read_text())
        self.assertEqual(len(list((self.candidate / "bpi-m5pro").glob("*.img.xz"))), 10)
        self.assertEqual(
            len(list((self.candidate / "bpi-m5pro").glob("*.img.xz.sha"))), 10
        )
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertIn("產生政策", calls)
        final = next(line for line in calls.splitlines() if line.startswith("稽核-最終"))
        self.assertIn("--candidate-input-policy", final)
        self.assertNotIn("--verify-xz", final)
        self.assertEqual(list((self.state / "raw-items").iterdir()), [])
        self.assertEqual(list((self.state / "transactions").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
