#!/usr/bin/env python3
from __future__ import annotations

import csv
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
mkdir -p "$STATE_ROOT/boards" "$STATE_ROOT/items" "$STATE_ROOT/transactions"
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
	{
		printf 'source_commit=%s\n' "$source_commit"
		printf 'bsp_base_commit=%s\n' "$bsp_base_commit"
		printf 'matrix_sha256=%s\n' "$matrix_sha256"
		printf 'userpatches_sha256=%s\n' "$userpatches_sha256"
		printf 'folder=demo\nboard=bananapim5\nbranch=current\n'
		printf 'release=trixie\nprofile=%s\n' "$profile"
		printf 'archive=%s\nsha256=%s\n' "$archive" "$digest"
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
