#!/usr/bin/env python3
"""藍牙成品紀錄只保留穩定工作根；不下載、交叉編譯或操作硬體。"""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cm6_bt_public_manifest", ROOT / "tools/build_bpi_cm6_bluetooth.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class PublicManifestPaths(unittest.TestCase):
    def record(self, output):
        source = output / "source"
        return {
            "schema_version": 1,
            "source": {"commit": "1" * 40, "sha256": "2" * 64},
            "source_lock_sha256": "3" * 64,
            "patches": [{"path": "fix.patch", "sha256": "4" * 64}],
            "files": {"bin/rtk_hciattach": {"bytes": 100, "sha256": "5" * 64}},
            "command": ["/usr/bin/riscv64-linux-gnu-gcc-13", "-O2",
                        f"-ffile-prefix-map={source}=/usr/src/bpi-cm6-bluetooth",
                        *[str(source / name) for name in BUILD.SOURCES], "-o", str(output / "bin/rtk_hciattach")],
            "cwd": str(source),
        }

    def test_path_tokens_cover_sources_output_and_prefix_map(self):
        output = Path("/tmp/cm6-build-a")
        result = BUILD.public_manifest(self.record(output), output / "source", output)
        self.assertEqual(result["command"][2], "-ffile-prefix-map=${SOURCE_ROOT}=/usr/src/bpi-cm6-bluetooth")
        self.assertEqual(result["command"][3:7], ["${SOURCE_ROOT}/" + name for name in BUILD.SOURCES])
        self.assertEqual(result["command"][-1], "${BUILD_ROOT}/bin/rtk_hciattach")
        self.assertEqual(result["cwd"], "${SOURCE_ROOT}")
        self.assertEqual(result["command_roots"], {"SOURCE_ROOT": "source", "BUILD_ROOT": "."})
        self.assertFalse(any(str(output) in arg for arg in result["command"]))

    def test_different_workspaces_have_same_public_record(self):
        first, second = Path("/tmp/cm6-build-a"), Path("/tmp/another workspace/build-b")
        self.assertEqual(BUILD.public_manifest(self.record(first), first / "source", first),
                         BUILD.public_manifest(self.record(second), second / "source", second))

    def test_original_command_and_provenance_are_unchanged(self):
        output = Path("/tmp/cm6-build-a")
        original = self.record(output)
        before = copy.deepcopy(original)
        result = BUILD.public_manifest(original, output / "source", output)
        self.assertEqual(original, before)
        for field in ("source", "source_lock_sha256", "patches", "files"):
            self.assertEqual(result[field], before[field])
        self.assertEqual(result["builder_sha256"], hashlib.sha256(Path(BUILD.__file__).read_bytes()).hexdigest())
        self.assertEqual(result["command"][0], before["command"][0])

    def test_similar_directory_prefix_is_not_rewritten(self):
        output = Path("/tmp/cm6-build")
        original = self.record(output)
        original["command"].append("/tmp/cm6-build-other/file.c")
        result = BUILD.public_manifest(original, output / "source", output)
        self.assertEqual(result["command"][-1], original["command"][-1])


if __name__ == "__main__":
    unittest.main()
