#!/usr/bin/env python3
"""共用隔離快取執行器的訊號與清理回歸測試。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/run-bananapi-candidates-isolated-cache.sh"


class BananaPiIsolatedCacheRunnerTests(unittest.TestCase):
    """確保 INT 與 TERM 不會被清理程序誤報為成功。"""

    @staticmethod
    def write_executable(path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def run_with_signal(
        self,
        signal_name: str,
        *,
        active_build: bool = False,
        allow_parallel: str = "no",
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            fake_bin = root / "bin"
            cache_lower = root / "lower"
            cache_target = root / "target"
            overlay_root = root / "overlay"
            mount_state = root / "mounted"
            builder = root / "builder.sh"
            fake_bin.mkdir()
            cache_lower.mkdir()

            self.write_executable(
                fake_bin / "findmnt",
                """
                #!/usr/bin/env bash
                printf 'ext4\n'
                """,
            )
            for name in ("flock", "mount", "umount"):
                self.write_executable(
                    fake_bin / name,
                    """
                    #!/usr/bin/env bash
                    exit 0
                    """,
                )
            self.write_executable(
                fake_bin / "pgrep",
                """
                #!/usr/bin/env bash
                if [[ "${FAKE_ACTIVE_BUILD:-no}" == "yes" ]]; then
                    printf '123 compile.sh build\\n'
                    exit 0
                fi
                exit 1
                """,
            )
            self.write_executable(
                fake_bin / "mountpoint",
                """
                #!/usr/bin/env bash
                [[ "${1:-}" == "-q" ]] || exit 2
                [[ -f "${FAKE_MOUNT_STATE:?}" ]]
                """,
            )
            self.write_executable(
                fake_bin / "sudo",
                """
                #!/usr/bin/env bash
                if [[ "${1:-}" == "-n" && "${2:-}" == "true" ]]; then
                    exit 0
                fi
                case "${1:-}" in
                    mount)
                        : >"${FAKE_MOUNT_STATE:?}"
                        ;;
                    umount)
                        rm -f -- "${FAKE_MOUNT_STATE:?}"
                        ;;
                    *)
                        exit 2
                        ;;
                esac
                """,
            )
            self.write_executable(
                builder,
                """
                #!/usr/bin/env bash
                kill -s "${TEST_SIGNAL:?}" "${PPID}"
                exit 0
                """,
            )

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "CANDIDATE_BUILDER": str(builder),
                    "CACHE_LOWER": str(cache_lower),
                    "CACHE_TARGET": str(cache_target),
                    "CACHE_OVERLAY_ROOT": str(overlay_root),
                    "FAKE_MOUNT_STATE": str(mount_state),
                    "FAKE_ACTIVE_BUILD": "yes" if active_build else "no",
                    "ALLOW_PARALLEL_ISOLATED_BUILDS": allow_parallel,
                    "TEST_SIGNAL": signal_name,
                }
            )
            result = subprocess.run(
                [str(RUNNER)],
                capture_output=True,
                text=True,
                env=environment,
                check=False,
                timeout=10,
            )
            self.assertFalse(mount_state.exists(), "訊號離開後仍保留掛載狀態")
            return result

    def test_term_returns_143_after_cleanup(self) -> None:
        result = self.run_with_signal("TERM")
        self.assertEqual(result.returncode, 143, result.stderr)

    def test_int_returns_130_after_cleanup(self) -> None:
        result = self.run_with_signal("INT")
        self.assertEqual(result.returncode, 130, result.stderr)

    def test_active_build_is_rejected_by_default(self) -> None:
        result = self.run_with_signal("TERM", active_build=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("拒絕", result.stderr)

    def test_active_build_requires_explicit_isolated_opt_in(self) -> None:
        result = self.run_with_signal(
            "TERM",
            active_build=True,
            allow_parallel="yes",
        )
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn("明確允許平行隔離建置", result.stderr)


if __name__ == "__main__":
    unittest.main()
