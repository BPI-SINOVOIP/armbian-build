#!/usr/bin/env python3
"""BPI-M4 Zero EMAC 十映像矩陣回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_DIR / "tools/build-bpi-m4zero-emac-matrix.sh"
VERIFY_SCRIPT = REPO_DIR / "tools/verify-bpi-m4zero-emac-matrix.sh"


class M4ZeroEmacMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = BUILD_SCRIPT.read_text(encoding="utf-8")
        cls.verifier = VERIFY_SCRIPT.read_text(encoding="utf-8")

    def test_matrix_has_five_releases_and_two_profiles(self) -> None:
        self.assertIn("bookworm trixie jammy noble resolute", self.builder)
        self.assertIn("cli xfce", self.builder)
        self.assertIn("expected_count=", self.builder)

    def test_every_entry_uses_full_board_build(self) -> None:
        self.assertIn("./compile.sh", self.builder)
        self.assertIn("build BOARD=bananapim4zeroemac", self.builder)
        self.assertIn("build_method=full_compile_sh_build", self.builder)
        self.assertNotIn("dd if=", self.builder)

    def test_cli_is_minimal_and_desktop_is_xfce(self) -> None:
        self.assertIn("BUILD_DESKTOP=no BUILD_MINIMAL=yes", self.builder)
        self.assertIn("BUILD_DESKTOP=yes BUILD_MINIMAL=no", self.builder)
        self.assertIn("DESKTOP_ENVIRONMENT=xfce", self.builder)
        self.assertIn("DESKTOP_TIER=mid", self.builder)

    def test_delivery_keeps_xz_separate_from_temporary_raw_images(self) -> None:
        self.assertIn("output_dir=", self.builder)
        self.assertIn("work_dir=", self.builder)
        self.assertIn('xz -T0 -6 --stdout "${image}"', self.builder)
        self.assertIn('xz -t "${archive}"', self.builder)
        self.assertIn('>"${archive}.sha"', self.builder)

    def test_verifier_checks_archive_identity_and_read_only_content(self) -> None:
        self.assertIn('xz -dc -- "${archive}"', self.verifier)
        self.assertIn("--partscan --read-only", self.verifier)
        self.assertIn("mount -o ro,noload", self.verifier)
        self.assertIn("verify-bpi-m4zero-emac-image.sh", self.verifier)

    def test_verifier_checks_release_and_profile(self) -> None:
        self.assertIn("VERSION_CODENAME", self.verifier)
        self.assertIn("BOARD=bananapim4zeroemac", self.verifier)
        self.assertIn("BRANCH=current", self.verifier)
        self.assertIn("xfce4", self.verifier)
        self.assertIn("M4 Zero EMAC 十映像矩陣全部通過唯讀驗證", self.verifier)


if __name__ == "__main__":
    unittest.main()
