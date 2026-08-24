#!/usr/bin/env python3
"""BPI-M4 Berry H618 完整重建矩陣回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
SCRIPT = REPO_DIR / "tools/build-bpi-m4berry-h618-optimized-matrix.sh"


class M4BerryOptimizedMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_matrix_has_five_releases_and_two_profiles(self) -> None:
        self.assertIn("bookworm trixie jammy noble resolute", self.script)
        self.assertIn("cli xfce", self.script)
        self.assertIn("expected_count=", self.script)

    def test_xfce_uses_current_noninteractive_desktop_tier(self) -> None:
        self.assertIn("DESKTOP_TIER=mid", self.script)
        self.assertNotIn("DESKTOP_ENVIRONMENT_CONFIG_NAME", self.script)

    def test_each_entry_is_a_full_build(self) -> None:
        self.assertIn("./compile.sh", self.script)
        self.assertIn("build BOARD=bananapim4berry", self.script)
        self.assertIn("build_method=full_compile_sh_build", self.script)
        self.assertNotIn("dd if=", self.script)

    def test_raw_and_xz_are_both_kept_and_verified(self) -> None:
        self.assertIn("COMPRESS_OUTPUTIMAGE=sha,img", self.script)
        self.assertIn('xz -T0 -6 --stdout "${image}"', self.script)
        self.assertIn('mv "${image}.xz.partial" "${image}.xz"', self.script)
        self.assertIn('xz -t "${image}.xz"', self.script)
        self.assertIn("raw_sha256", self.script)
        self.assertIn("xz_sha256", self.script)


if __name__ == "__main__":
    unittest.main()
