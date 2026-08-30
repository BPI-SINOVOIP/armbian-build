#!/usr/bin/env python3
"""BPI-M4 Berry H618 完整重建矩陣回歸測試。"""

import hashlib
import lzma
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
SCRIPT = REPO_DIR / "tools/build-bpi-m4berry-h618-optimized-matrix.sh"
VERIFY_SCRIPT = REPO_DIR / "tools/verify-bpi-m4berry-h618-optimized-matrix.sh"


class M4BerryOptimizedMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def write_existing_artifact(
        self,
        output_dir: Path,
        source_commit: str,
        raw_data: bytes = b"m4berry-image",
        archived_data: bytes | None = None,
        include_metadata: bool = True,
    ) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        image = output_dir / (
            "Armbian-test_Bananapim4berry_bookworm_current_6.18.46_"
            "minimal_a1-h618-optimized-792mhz.img"
        )
        archive = Path(f"{image}.xz")
        image.write_bytes(raw_data)
        archive.write_bytes(lzma.compress(archived_data or raw_data, format=lzma.FORMAT_XZ))

        if include_metadata:
            raw_sha256 = hashlib.sha256(raw_data).hexdigest()
            xz_data = archive.read_bytes()
            xz_sha256 = hashlib.sha256(xz_data).hexdigest()
            metadata = Path(f"{image}.metadata.txt")
            metadata.write_text(
                "\n".join(
                    (
                        "board=bananapim4berry",
                        "release=bookworm",
                        "profile=cli",
                        "build_method=full_compile_sh_build",
                        f"source_commit={source_commit}",
                        "kernel_branch=current",
                        "dram_clock_mhz=792",
                        "cma_mib=256",
                        f"raw_size={len(raw_data)}",
                        f"raw_sha256={raw_sha256}",
                        f"xz_size={len(xz_data)}",
                        f"xz_sha256={xz_sha256}",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
        return image, archive

    def run_build_guard(self, output_dir: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "OUTPUT_DIR": str(output_dir),
                "RELEASES": "bookworm",
                "PROFILES": "cli",
            }
        )
        return subprocess.run(
            [str(SCRIPT)],
            cwd=REPO_DIR,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

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
        self.assertIn('xz -dc -- "${image}.xz"', self.script)
        self.assertIn("decompressed_sha256", self.script)
        self.assertIn("raw_sha256", self.script)
        self.assertIn("xz_sha256", self.script)

    def test_source_commit_is_captured_once_for_the_whole_matrix(self) -> None:
        snapshot = 'source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"'
        self.assertIn(snapshot, self.script)
        self.assertEqual(self.script.count("rev-parse HEAD"), 1)
        self.assertIn(
            "printf 'source_commit=%s\\n' \"${artifact_source_commit}\"",
            self.script,
        )

    def test_existing_artifact_keeps_recorded_source_commit(self) -> None:
        recorded_commit = "1" * 40
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            image, _ = self.write_existing_artifact(output_dir, recorded_commit)
            metadata = Path(f"{image}.metadata.txt")
            original_metadata = metadata.read_bytes()

            result = self.run_build_guard(output_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(metadata.read_bytes(), original_metadata)
            matrix_fields = (output_dir / "MATRIX.tsv").read_text(
                encoding="utf-8"
            ).splitlines()[1].split("\t")
            self.assertEqual(matrix_fields[-1], recorded_commit)

    def test_existing_artifact_without_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            image, _ = self.write_existing_artifact(
                output_dir,
                "2" * 40,
                include_metadata=False,
            )

            result = self.run_build_guard(output_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("缺少可信中繼資料", result.stderr)
            self.assertFalse(Path(f"{image}.metadata.txt").exists())

    def test_existing_artifact_with_different_xz_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            self.write_existing_artifact(
                output_dir,
                "3" * 40,
                raw_data=b"raw-image",
                archived_data=b"different-image",
            )

            result = self.run_build_guard(output_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("解壓資料 SHA-256 與原始映像不一致", result.stderr)

    def test_read_only_matrix_verifier_checks_every_image(self) -> None:
        verifier = VERIFY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--partscan --read-only", verifier)
        self.assertIn("mount -o ro,noload", verifier)
        self.assertIn("CONFIG_VIDEO_SUNXI_CEDRUS=y", verifier)
        self.assertIn("CONFIG_SUN50I_H6_PRCM_PPU=y", verifier)
        self.assertIn("CONFIG_DRM_PANFROST=m", verifier)
        self.assertIn("CONFIG_RTW88_8821CU=m", verifier)
        self.assertIn("rtw88_8821cu.ko", verifier)
        self.assertIn("usb:v0BDApC820", verifier)
        self.assertIn("blacklist[[:space:]]+rtw88_8821cu", verifier)
        self.assertIn("gstreamer1.0-plugins-bad", verifier)
        self.assertIn('xz -dc -- "${archive}"', verifier)
        self.assertIn(
            '[[ "${decompressed_sha256}" == "${actual_raw_sha256}" ]]', verifier
        )
        self.assertIn("source_commit", verifier)
        self.assertIn("NF == 9", verifier)
        self.assertIn("M4 Berry H618 十映像矩陣全部通過唯讀驗證", verifier)


if __name__ == "__main__":
    unittest.main()
