#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import lzma
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/compress-bananapi-image-queue.sh"
SOURCE_COMMIT = "a" * 40
BUILD_CONTEXT = "b" * 64


class BananaPiImageCompressorTests(unittest.TestCase):
    def test_ready_image_is_compressed_verified_and_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            state = root / "state"
            stage = release / ".staging-bpi-demo-aaaaaaaaaaaa"
            raw_dir = state / "raw-images" / "build-uuid"
            marker_dir = state / "raw-items"
            log_dir = state / "logs"
            framework_dir = state / "framework-logs"
            for directory in (stage, raw_dir, marker_dir, log_dir, framework_dir):
                directory.mkdir(parents=True)

            payload = (b"Banana Pi raw image\0" * 8192) + os.urandom(4096)
            raw = raw_dir / "Armbian-test_Bananapidemonstration_trixie_current_minimal.img"
            raw.write_bytes(payload)
            raw_digest = hashlib.sha256(payload).hexdigest()
            Path(f"{raw}.sha").write_text(
                f"{raw_digest}  {raw.name}\n", encoding="utf-8"
            )
            log = log_dir / "build.log"
            log.write_text("建置完成\n", encoding="utf-8")
            framework_log = framework_dir / "framework.log"
            framework_log.write_text("內部建置完成\n", encoding="utf-8")
            archive_name = f"{raw.name}.xz"
            target = stage / archive_name
            marker = marker_dir / "bpi-demo-trixie-minimal.ready"
            marker.write_text(
                f"source_commit={SOURCE_COMMIT}\n"
                "bsp_base_commit=" + "c" * 40 + "\n"
                "matrix_sha256=" + "d" * 64 + "\n"
                "userpatches_sha256=" + "e" * 64 + "\n"
                f"build_context_sha256={BUILD_CONTEXT}\n"
                "folder=bpi-demo\n"
                "board=bananapidemonstration\n"
                "branch=current\n"
                "release=trixie\n"
                "profile=minimal\n"
                f"archive={archive_name}\n"
                f"target_archive={target}\n"
                f"raw_image={raw}\n"
                f"raw_sha256={raw_digest}\n"
                "fresh_artifacts=yes\n"
                "build_uuid=build-uuid\n"
                f"log={log}\n"
                f"log_sha256={hashlib.sha256(log.read_bytes()).hexdigest()}\n"
                f"framework_log={framework_log}\n"
                "framework_log_sha256="
                f"{hashlib.sha256(framework_log.read_bytes()).hexdigest()}\n"
                "status=ready\n",
                encoding="utf-8",
            )
            done = state / "producer.done"
            done.touch()
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=ROOT,
                env={
                    **os.environ,
                    "CANDIDATE_RELEASE": str(release),
                    "CANDIDATE_STATE": str(state),
                    "SOURCE_COMMIT": SOURCE_COMMIT,
                    "BUILD_CONTEXT_SHA256": BUILD_CONTEXT,
                    "COMPRESSION_DONE_SIGNAL": str(done),
                    "XZ_THREADS": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(lzma.decompress(target.read_bytes()), payload)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(
                Path(f"{target}.sha").read_text(encoding="utf-8"),
                f"{digest}  {archive_name}\n",
            )
            completed = state / "items" / "bpi-demo-trixie-minimal.complete"
            self.assertTrue(completed.is_file())
            self.assertIn(f"sha256={digest}\n", completed.read_text(encoding="utf-8"))
            self.assertFalse(raw.exists())
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
