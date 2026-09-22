#!/usr/bin/env python3
"""驗證藍牙套件拒絕被更動的來源、架構與跨目錄輸入。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_bpi_cm6_bluetooth", REPO / "tools/package_bpi_cm6_bluetooth.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class BluetoothPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "build"
        (self.root / "bin").mkdir(parents=True)
        self.config = self.base / "config"
        self.config.mkdir()
        source = b"fixed-official-source-fixture"
        self.lock = {"source": {"commit": "1" * 40, "bytes": len(source), "sha256": hashlib.sha256(source).hexdigest()},
                     "patches": [{"path": "fixture.patch", "target": "hciattach.c",
                                  "sha256": hashlib.sha256(b"patch-fixture").hexdigest(),
                                  "after_sha256": hashlib.sha256(b"patched-source").hexdigest()}]}
        (self.root / "patches").mkdir()
        (self.root / "patches/fixture.patch").write_bytes(b"patch-fixture")
        (self.root / "source").mkdir()
        (self.root / "source/hciattach.c").write_bytes(b"patched-source")
        (self.config / "source-lock.json").write_text(json.dumps(self.lock))
        (self.root / "source-lock.json").write_bytes((self.config / "source-lock.json").read_bytes())
        (self.root / "source.tar.gz").write_bytes(source)
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", header, 18, 243)
        (self.root / "bin/rtk_hciattach").write_bytes(header)
        self.record = {"status": "complete", "board": "bpi-cm6", "source": self.lock["source"],
                       "patches": self.lock["patches"],
                       "source_files_after_patch": {"hciattach.c": MOD.digest(self.root / "source/hciattach.c")},
                       "source_lock_sha256": MOD.digest(self.config / "source-lock.json"),
                       "files": {name: {"bytes": (self.root / name).stat().st_size, "sha256": MOD.digest(self.root / name)}
                                 for name in ("source.tar.gz", "source-lock.json", "bin/rtk_hciattach", "patches/fixture.patch", "source/hciattach.c")}}
        self.save()
        patch = mock.patch.object(MOD, "CONFIG", self.config)
        patch.start()
        self.addCleanup(patch.stop)

    def save(self):
        (self.root / "build-manifest.json").write_text(json.dumps(self.record))

    def test_consistent_source_manifest_is_accepted(self):
        self.assertEqual(MOD.verify_build(self.root)["board"], "bpi-cm6")

    def test_changed_payload_is_rejected(self):
        (self.root / "bin/rtk_hciattach").write_bytes(b"changed")
        with self.assertRaises(ValueError): MOD.verify_build(self.root)

    def test_self_consistent_wrong_architecture_is_rejected(self):
        path = self.root / "bin/rtk_hciattach"
        data = bytearray(path.read_bytes());struct.pack_into("<H", data, 18, 183);path.write_bytes(data)
        self.record["files"]["bin/rtk_hciattach"]["sha256"] = MOD.digest(path);self.save()
        with self.assertRaisesRegex(ValueError, "RISC-V"): MOD.verify_build(self.root)

    def test_self_consistent_replaced_source_is_rejected(self):
        path = self.root / "source.tar.gz";path.write_bytes(b"other-source")
        self.record["files"]["source.tar.gz"] = {"bytes": path.stat().st_size, "sha256": MOD.digest(path)};self.save()
        with self.assertRaises(ValueError): MOD.verify_build(self.root)

    def test_manifest_cannot_read_parent_directory(self):
        outside = self.base / "outside";outside.write_bytes(b"outside")
        self.record["files"]["../outside"] = {"bytes": 7, "sha256": MOD.digest(outside)};self.save()
        with self.assertRaises(ValueError): MOD.verify_build(self.root)

    def test_symlink_payload_is_rejected_even_if_hash_matches(self):
        path = self.root / "bin/rtk_hciattach";outside = self.base / "binary"
        path.rename(outside);path.symlink_to(outside)
        with self.assertRaises(ValueError): MOD.verify_build(self.root)

    def test_missing_source_archive_is_rejected(self):
        del self.record["files"]["source.tar.gz"];self.save()
        with self.assertRaises(ValueError): MOD.verify_build(self.root)

    def test_missing_patch_is_rejected(self):
        del self.record["files"]["patches/fixture.patch"];self.save()
        with self.assertRaises(ValueError): MOD.verify_build(self.root)

    def test_self_consistent_changed_patched_source_is_rejected(self):
        path = self.root / "source/hciattach.c";path.write_bytes(b"changed-source")
        self.record["files"]["source/hciattach.c"] = {"bytes": path.stat().st_size, "sha256": MOD.digest(path)}
        self.record["source_files_after_patch"]["hciattach.c"] = MOD.digest(path);self.save()
        with self.assertRaises(ValueError): MOD.verify_build(self.root)


if __name__ == "__main__":
    unittest.main()
