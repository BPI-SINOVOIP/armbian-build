#!/usr/bin/env python3
from __future__ import annotations

import csv
import fcntl
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "config/bananapi-latest-release-matrix.tsv"
STATUS = ROOT / "config/bananapi-optimization-status.json"
SCRIPT = ROOT / "tools/rebuild-bananapi-latest-release.sh"


def load_matrix() -> list[dict[str, str]]:
    with MATRIX.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


class BananaPiLatestFullRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = load_matrix()
        cls.status = json.loads(STATUS.read_text(encoding="utf-8"))

    def test_matrix_covers_active_l2_boards_and_variant(self) -> None:
        expected = {
            board
            for board, evidence in self.status["evidence"].items()
            if evidence["level"] == "L2" and board != "bananapir1"
        }
        expected.update(self.status["variants"])
        self.assertEqual({row["board"] for row in self.rows}, expected)
        self.assertEqual(len(self.rows), 45)

    def test_matrix_excludes_l0_and_eos(self) -> None:
        boards = {row["board"] for row in self.rows}
        self.assertTrue({"bananapicm2", "bananapim2c", "bananapim4super"}.isdisjoint(boards))
        self.assertNotIn("bananapir1", boards)

    def test_folders_and_boards_are_unique(self) -> None:
        folders = [row["folder"] for row in self.rows]
        boards = [row["board"] for row in self.rows]
        self.assertEqual(len(folders), len(set(folders)))
        self.assertEqual(len(boards), len(set(boards)))

    def test_matrix_has_exactly_444_images(self) -> None:
        image_count = sum(len(row["releases"].split(",")) * 2 for row in self.rows)
        self.assertEqual(image_count, 444)
        standard_releases = ["trixie", "bookworm", "jammy", "noble", "resolute"]
        riscv_releases = ["trixie", "jammy", "noble", "resolute"]
        for row in self.rows:
            releases = row["releases"].split(",")
            if row["board"] in {"bananapicm6", "bananapif3", "bananapism10"}:
                self.assertEqual(releases, riscv_releases)
            else:
                self.assertEqual(releases, standard_releases)

    def test_matrix_fields_are_path_safe(self) -> None:
        safe_name = re.compile(r"^[a-z0-9-]+$")
        for row in self.rows:
            self.assertRegex(row["folder"], safe_name)
            self.assertRegex(row["board"], safe_name)
            self.assertIn(row["branch"], {"current", "edge", "legacy", "vendor"})
            self.assertNotIn("..", row["folder"])

    def test_every_matrix_board_has_one_config(self) -> None:
        for row in self.rows:
            matches = [
                ROOT / "config/boards" / f"{row['board']}.{suffix}"
                for suffix in ("conf", "csc", "wip", "eos")
                if (ROOT / "config/boards" / f"{row['board']}.{suffix}").is_file()
            ]
            self.assertEqual(len(matches), 1, row["board"])

    def test_script_syntax_and_dry_run(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], cwd=ROOT, check=True)
        result = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("預定建置映像總數：444", result.stdout)

    def test_userpatches_hash_is_locale_independent(self) -> None:
        digests = set()
        for locale in ("C", "C.UTF-8", "en_US.UTF-8"):
            env = os.environ.copy()
            env.update(
                {
                    "BPI_REBUILD_LIBRARY_ONLY": "yes",
                    "LC_ALL": locale,
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"source {SCRIPT!s}; calculate_userpatches_hash",
                ],
                cwd=ROOT,
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )
            digests.add(result.stdout.strip())
        self.assertEqual(len(digests), 1)

    def run_library_shell(self, body: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release_root = root / "release"
            state_root = root / "state"
            release_root.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "BPI_REBUILD_LIBRARY_ONLY": "yes",
                    "RELEASE_ROOT": str(release_root),
                    "STATE_ROOT": str(state_root),
                }
            )
            script = f"""
set -Eeuo pipefail
source {SCRIPT!s}
matrix_sha256=test-matrix
userpatches_sha256=test-userpatches
build_context_sha256=test-context
mkdir -p "$STATE_ROOT/boards" "$STATE_ROOT/items" "$STATE_ROOT/logs" "$STATE_ROOT/framework-logs" "$STATE_ROOT/markers" "$STATE_ROOT/transactions"
{body}
"""
            return subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env=env,
                check=False,
                text=True,
                capture_output=True,
            )

    def test_transaction_recovers_previous_matrix(self) -> None:
        result = self.run_library_shell(
            r"""
previous="$RELEASE_ROOT/.previous-demo-$source_short"
mkdir -p "$previous" "$RELEASE_ROOT/demo"
printf old > "$previous/old.img.xz"
printf invalid > "$RELEASE_ROOT/demo/new.img.xz"
write_transaction demo new-installed yes
verify_board_dir() { return 1; }
recover_board_transaction demo bananapim5 current trixie 2
test -f "$RELEASE_ROOT/demo/old.img.xz"
test ! -e "$previous"
test ! -e "$(transaction_path demo)"
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_transaction_finishes_verified_matrix(self) -> None:
        result = self.run_library_shell(
            r"""
previous="$RELEASE_ROOT/.previous-demo-$source_short"
mkdir -p "$previous" "$RELEASE_ROOT/demo"
printf old > "$previous/old.img.xz"
printf new > "$RELEASE_ROOT/demo/new.img.xz"
write_transaction demo new-installed yes
verify_board_dir() { return 0; }
recover_board_transaction demo bananapim5 current trixie 2
test -f "$RELEASE_ROOT/demo/new.img.xz"
test ! -e "$previous"
test -f "$STATE_ROOT/boards/demo.complete"
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_transaction_refuses_missing_formal_and_previous_matrix(self) -> None:
        result = self.run_library_shell(
            r"""
write_transaction demo old-moved yes
if recover_board_transaction demo bananapim5 current trixie 2; then
	exit 9
fi
test -f "$(transaction_path demo)"
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_safe_remove_refuses_formal_directory(self) -> None:
        result = self.run_library_shell(
            r"""
mkdir -p "$RELEASE_ROOT/demo"
if safe_remove_work_dir "$RELEASE_ROOT/demo" "$RELEASE_ROOT"; then
	exit 9
fi
test -d "$RELEASE_ROOT/demo"
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_transaction_refuses_corrupt_state_with_previous_matrix(self) -> None:
        result = self.run_library_shell(
            r"""
previous="$RELEASE_ROOT/.previous-demo-$source_short"
mkdir -p "$previous" "$RELEASE_ROOT/demo"
printf old > "$previous/old.img.xz"
printf new > "$RELEASE_ROOT/demo/new.img.xz"
printf 'corrupt=yes\n' > "$(transaction_path demo)"
if recover_board_transaction demo bananapim5 current trixie 2; then
	exit 9
fi
test -f "$previous/old.img.xz"
test -f "$RELEASE_ROOT/demo/new.img.xz"
test -f "$(transaction_path demo)"
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sha_sidecar_rejects_wrong_archive_name(self) -> None:
        result = self.run_library_shell(
            r"""
archive="$RELEASE_ROOT/demo.img.xz"
printf payload | xz -c > "$archive"
digest="$(sha256sum "$archive" | awk '{ print $1 }')"
printf '%s  %s\n' "$digest" wrong.img.xz > "$archive.sha"
if verify_sha_sidecar "$archive" "$archive.sha" "$digest"; then
	exit 9
fi
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compile_entrypoint_honours_global_lock(self) -> None:
        lock_path = ROOT / "output/images/.armbian-build.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                ["bash", str(ROOT / "compile.sh"), "--help"],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 73)
        self.assertIn("另一個受控 Armbian 建置", result.stderr)

    def test_script_contains_release_provenance_and_full_gate(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        extension = (ROOT / "extensions/bananapi-build-provenance.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("ENABLE_EXTENSIONS=bananapi-build-provenance", script)
        self.assertIn("BPI_RELEASE_BUILD_CONTEXT_SHA256", script)
        self.assertIn('[[ "${rows}" -eq 45 && "${images}" -eq 444 ]]', script)
        self.assertIn("/etc/bananapi-build-provenance", script)
        self.assertIn("for _ in {1..20}", script)
        self.assertIn('mountpoint -q "${mount_dir}" && status=1', script)
        self.assertIn("${SDCARD}/etc/bananapi-build-provenance", extension)
        self.assertIn("--allow-full-rebuild", script)
        self.assertIn('repo_dir="${REPO_DIR:-', script)
        self.assertIn("EXPECTED_BUILD_CONTEXT_SHA256", script)
        self.assertIn("framework_log_sha256", script)
        self.assertIn('summary="${state_root}/runs/summary-${run_uuid}.tsv"', script)

    def test_legacy_completed_item_keeps_valid_primary_log(self) -> None:
        result = self.run_library_shell(
            r"""
stage="$RELEASE_ROOT/.staging-demo-$source_short"
mkdir -p "$stage"
archive="Armbian-test_Bananapim5_trixie_current_1.0_minimal.img.xz"
printf payload | xz -c > "$stage/$archive"
digest="$(sha256sum "$stage/$archive" | awk '{ print $1 }')"
printf '%s  %s\n' "$digest" "$archive" > "$stage/$archive.sha"
log="$STATE_ROOT/logs/demo-trixie-minimal.log"
printf '完整建置日誌\n' > "$log"
log_digest="$(sha256sum "$log" | awk '{ print $1 }')"
marker="$(item_marker_path demo trixie minimal)"
{
	printf 'source_commit=%s\n' "$source_commit"
	printf 'bsp_base_commit=%s\n' "$bsp_base_commit"
	printf 'matrix_sha256=%s\n' "$matrix_sha256"
	printf 'userpatches_sha256=%s\n' "$userpatches_sha256"
	printf 'build_context_sha256=%s\n' "$build_context_sha256"
	printf 'folder=demo\nboard=bananapim5\nbranch=current\n'
	printf 'release=trixie\nprofile=minimal\n'
	printf 'archive=%s\nsha256=%s\n' "$archive" "$digest"
	printf 'log=%s\nlog_sha256=%s\n' "$log" "$log_digest"
	printf 'framework_log=/tmp/已輪替.log\n'
} > "$marker"
item_is_complete "$stage" demo bananapim5 current trixie minimal
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovered_complete_board_does_not_enter_build_loop(self) -> None:
        result = self.run_library_shell(
            r"""
recover_board_transaction() { return 0; }
board_is_complete() { return 0; }
build_item() { printf '不應執行建置\n' >&2; return 99; }
find_board_file() { printf '/tmp/board.conf\n'; }
rebuild_board demo bananapim5 current trixie
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("不應執行建置", result.stderr)

    def test_script_supports_separate_read_only_boot_partition(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('mountpoint -q "${mount_dir}/boot"', script)
        self.assertIn("boot_partition=", script)
        self.assertIn(
            'mount -o ro,nosuid,nodev,noexec "${boot_partition}" "${mount_dir}/boot"',
            script,
        )

    def test_board_verification_rejects_wrong_board_filenames(self) -> None:
        result = self.run_library_shell(
            r"""
directory="$RELEASE_ROOT/demo"
mkdir -p "$directory"
for profile in minimal xfce; do
	if [[ "$profile" == minimal ]]; then suffix=minimal; else suffix=xfce_desktop; fi
		archive="Armbian-test_Wrongboard_trixie_current_1.0_$suffix.img.xz"
		printf payload | xz -c > "$directory/$archive"
		digest="$(sha256sum "$directory/$archive" | awk '{ print $1 }')"
		printf '%s  %s\n' "$digest" "$archive" > "$directory/$archive.sha"
	marker="$(item_marker_path demo trixie "$profile")"
	log="$STATE_ROOT/logs/demo-trixie-$profile.log"
	printf log > "$log"
	log_digest="$(sha256sum "$log" | awk '{ print $1 }')"
	{
		printf 'source_commit=%s\n' "$source_commit"
		printf 'bsp_base_commit=%s\n' "$bsp_base_commit"
		printf 'matrix_sha256=%s\n' "$matrix_sha256"
		printf 'userpatches_sha256=%s\n' "$userpatches_sha256"
		printf 'build_context_sha256=%s\n' "$build_context_sha256"
		printf 'folder=demo\nboard=bananapim5\nbranch=current\n'
		printf 'release=trixie\nprofile=%s\n' "$profile"
		printf 'archive=%s\nsha256=%s\n' "$archive" "$digest"
		printf 'log=%s\nlog_sha256=%s\n' "$log" "$log_digest"
	} > "$marker"
done
if verify_board_dir "$directory" demo bananapim5 current trixie; then
	exit 9
fi
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
