#!/usr/bin/env python3
"""BPI-M4 Zero EMAC 十映像矩陣回歸測試。"""

from pathlib import Path
import hashlib
import lzma
import os
import subprocess
import tempfile
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

    def _create_existing_item(
        self,
        root: Path,
        *,
        metadata_overrides: dict[str, str] | None = None,
        archive_payload: bytes | None = None,
    ) -> tuple[Path, Path, Path, str]:
        output_dir = root / "output"
        work_dir = root / "work"
        output_dir.mkdir()
        work_dir.mkdir()
        image = work_dir / (
            "Armbian-test_Bananapim4zeroemac_bookworm_current_6.18.48_"
            "minimal_a1-h618-optimized-emac-792mhz.img"
        )
        image.write_bytes(b"BPI-M4 Zero EMAC metadata regression image\n")
        archive = output_dir / f"{image.name}.xz"
        with lzma.open(archive, "wb", format=lzma.FORMAT_XZ) as stream:
            stream.write(archive_payload or image.read_bytes())

        source_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_DIR,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        userpatches_records = b""
        for path in sorted((REPO_DIR / "userpatches").rglob("*")):
            if path.is_file() or path.is_symlink():
                relative_path = path.relative_to(REPO_DIR).as_posix()
                userpatches_records += (
                    hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
                    + b"  "
                    + relative_path.encode("utf-8")
                    + b"\n"
                )
        userpatches_sha256 = hashlib.sha256(userpatches_records).hexdigest()
        values = {
            "board": "bananapim4zeroemac",
            "release": "bookworm",
            "profile": "cli",
            "build_method": "full_compile_sh_build",
            "source_commit": source_commit,
            "userpatches_sha256": userpatches_sha256,
            "kernel_branch": "current",
            "dram_clock_mhz": "792",
            "cma_mib": "256",
            "raw_size": str(image.stat().st_size),
            "raw_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "xz_size": str(archive.stat().st_size),
            "xz_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        values.update(metadata_overrides or {})
        metadata = work_dir / f"{image.name}.metadata.txt"
        metadata.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        return output_dir, work_dir, metadata, source_commit

    def _run_builder(self, root: Path, output_dir: Path, work_dir: Path):
        environment = os.environ.copy()
        environment.update(
            {
                "OUTPUT_DIR": str(output_dir),
                "WORK_DIR": str(work_dir),
                "LOG_DIR": str(root / "logs"),
                "RELEASES": "bookworm",
                "PROFILES": "cli",
            }
        )
        return subprocess.run(
            ["bash", str(BUILD_SCRIPT)],
            cwd=REPO_DIR,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_resume_preserves_original_source_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir, work_dir, metadata, source_commit = self._create_existing_item(
                root
            )
            original_metadata = metadata.read_text(encoding="utf-8")

            result = self._run_builder(root, output_dir, work_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("沿用已完成項目：bookworm cli", result.stdout)
            self.assertEqual(original_metadata, metadata.read_text(encoding="utf-8"))
            self.assertIn(
                f"source_commit={source_commit}", metadata.read_text(encoding="utf-8")
            )

    def test_resume_accepts_legacy_metadata_without_userpatches_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir, work_dir, metadata, _ = self._create_existing_item(root)
            metadata.write_text(
                "".join(
                    line
                    for line in metadata.read_text(encoding="utf-8").splitlines(True)
                    if not line.startswith("userpatches_sha256=")
                ),
                encoding="utf-8",
            )

            result = self._run_builder(root, output_dir, work_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("尚未記錄 userpatches 指紋的舊產物", result.stderr)

    def test_resume_rejects_mismatched_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir, work_dir, metadata, _ = self._create_existing_item(
                root, metadata_overrides={"raw_sha256": "0" * 64}
            )
            original_metadata = metadata.read_text(encoding="utf-8")

            result = self._run_builder(root, output_dir, work_dir)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("拒絕沿用既有項目 bookworm cli", result.stderr)
            self.assertIn("raw_sha256 與映像不符", result.stderr)
            self.assertIn("請移除該項目的原始映像", result.stderr)
            self.assertNotIn("完整建置 bookworm cli", result.stdout)
            self.assertEqual(original_metadata, metadata.read_text(encoding="utf-8"))

    def test_resume_rejects_archive_with_different_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir, work_dir, _, _ = self._create_existing_item(
                root, archive_payload=b"different raw image content\n"
            )

            result = self._run_builder(root, output_dir, work_dir)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("壓縮檔解壓內容與原始映像不一致", result.stderr)
            self.assertNotIn("完整建置 bookworm cli", result.stdout)

    def test_new_build_requires_stable_clean_source(self) -> None:
        self.assertIn('cat-file -e "${source_commit}^{commit}"', self.builder)
        self.assertIn('assert_clean_source "建置前"', self.builder)
        self.assertIn('assert_clean_source "建置後"', self.builder)
        self.assertIn('git -C "${repo_dir}" diff --quiet --', self.builder)
        self.assertIn('git -C "${repo_dir}" diff --cached --quiet --', self.builder)
        self.assertIn("fingerprint_userpatches", self.builder)
        self.assertIn('userpatches_sha256="$(fingerprint_userpatches)"', self.builder)
        self.assertIn('printf \'userpatches_sha256=%s\\n\'', self.builder)
        self.assertIn("ls-files --others --exclude-standard", self.builder)
        self.assertIn("config patch packages lib tools", self.builder)
        self.assertIn("config-*.conf", self.builder)
        self.assertIn('decompressed_raw_sha256="$(xz -dc -- "${archive}"', self.builder)

    def test_verifier_checks_archive_identity_and_read_only_content(self) -> None:
        self.assertIn('xz -dc -- "${archive}"', self.verifier)
        self.assertIn("--partscan --read-only", self.verifier)
        self.assertIn("mount -o ro,noload", self.verifier)
        self.assertIn("verify-bpi-m4zero-emac-image.sh", self.verifier)

    def test_verifier_checks_release_and_profile(self) -> None:
        self.assertIn("VERSION_CODENAME", self.verifier)
        self.assertIn("BOARD=bananapim4zeroemac", self.verifier)
        self.assertIn("KERNEL_TARGET=current", self.verifier)
        self.assertIn("xfce4", self.verifier)
        self.assertIn("M4 Zero EMAC 十映像矩陣全部通過唯讀驗證", self.verifier)


if __name__ == "__main__":
    unittest.main()
