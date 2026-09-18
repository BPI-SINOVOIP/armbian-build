#!/usr/bin/env python3
"""K1 根系統準備工具的來源、分區與套件隔離回歸；不執行掛載。"""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_bpi_k1_vendor_rootfs", REPO / "tools/prepare_bpi_k1_vendor_rootfs.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class PrepareRootfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source.img"
        self.source.write_bytes("來源固定內容".encode())
        self.cache = self.base / "cache"
        self.cache.mkdir()

    def partition_table(self):
        with self.source.open("wb") as stream:
            stream.truncate((8192 + 32) * 512)
        return {"partitiontable": {"label": "dos", "unit": "sectors", "sectorsize": 512,
                "partitions": [{"start": 8192, "size": 32, "type": "83"}]}}

    def check_partition(self, table):
        with mock.patch.object(MOD.subprocess, "check_output", return_value=json.dumps(table).encode()):
            return MOD.partition(self.source)

    def package_lock(self):
        first = self.cache / "gpu-old.deb"
        second = self.cache / "gpu-new.deb"
        first.write_bytes(b"old-pvr")
        second.write_bytes(b"new-pvr")
        return {
            "profiles": {"bpi-cm6": {"packages": ["gpu=23"]}, "bpi-f3": {"packages": ["gpu=24"]}},
            "packages": {
                key: {"Filename": "pool/main/g/" + path.name, "Size": path.stat().st_size,
                      "SHA256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for key, path in (("gpu=23", first), ("gpu=24", second))
            },
        }

    def test_mbr_partition_ending_exactly_at_eof_is_valid(self):
        table = self.partition_table()
        self.assertEqual(self.check_partition(table)["size"], 32)

    def test_mbr_partition_exceeding_eof_is_rejected(self):
        table = self.partition_table()
        table["partitiontable"]["partitions"][0]["size"] += 1
        with self.assertRaises(ValueError):
            self.check_partition(table)

    def test_gpt_source_is_not_mistaken_for_old_armbian(self):
        table = self.partition_table()
        table["partitiontable"]["label"] = "gpt"
        with self.assertRaises(ValueError):
            self.check_partition(table)

    def test_wrong_offset_is_rejected(self):
        table = self.partition_table()
        table["partitiontable"]["partitions"][0]["start"] = 2048
        with self.assertRaises(ValueError):
            self.check_partition(table)

    def test_multiple_partitions_are_rejected(self):
        table = self.partition_table()
        table["partitiontable"]["partitions"].append({"start": 1, "size": 10, "type": "83"})
        with self.assertRaises(ValueError):
            self.check_partition(table)

    def test_wrong_filesystem_partition_type_is_rejected(self):
        table = self.partition_table()
        table["partitiontable"]["partitions"][0]["type"] = "c"
        with self.assertRaises(ValueError):
            self.check_partition(table)

    def test_copy_preserves_source_and_exact_partition_bytes(self):
        self.source.write_bytes(b"headerPAYLOADtail")
        destination = self.base / "rootfs.ext4"
        MOD.copy_partition(self.source, destination, 6, 7)
        self.assertEqual(destination.read_bytes(), b"PAYLOAD")
        self.assertEqual(self.source.read_bytes(), b"headerPAYLOADtail")

    def test_short_source_cannot_be_silently_padded(self):
        self.source.write_bytes(b"1234")
        with self.assertRaises(ValueError):
            MOD.copy_partition(self.source, self.base / "rootfs.ext4", 0, 5)

    def test_existing_rootfs_is_not_overwritten_by_copy(self):
        destination = self.base / "rootfs.ext4"
        destination.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            MOD.copy_partition(self.source, destination, 0, 1)
        self.assertEqual(destination.read_bytes(), b"existing")

    def test_package_selection_keeps_board_specific_ddk(self):
        lock = self.package_lock()
        self.assertEqual([p.name for p in MOD.package_selection(lock, "bpi-cm6", self.cache)], ["gpu-old.deb"])
        self.assertEqual([p.name for p in MOD.package_selection(lock, "bpi-f3", self.cache)], ["gpu-new.deb"])

    def test_modified_package_with_same_size_is_rejected(self):
        lock = self.package_lock()
        (self.cache / "gpu-old.deb").write_bytes(b"bad-pvr")
        with self.assertRaises(ValueError):
            MOD.package_selection(lock, "bpi-cm6", self.cache)

    def test_truncated_package_is_rejected(self):
        lock = self.package_lock()
        (self.cache / "gpu-old.deb").write_bytes(b"old")
        with self.assertRaises(ValueError):
            MOD.package_selection(lock, "bpi-cm6", self.cache)

    def test_symlink_package_is_rejected_even_when_hash_matches(self):
        lock = self.package_lock()
        package = self.cache / "gpu-old.deb"
        target = self.base / "outside.deb"
        package.rename(target)
        package.symlink_to(target)
        with self.assertRaises(ValueError):
            MOD.package_selection(lock, "bpi-cm6", self.cache)

    def test_unknown_board_cannot_use_another_profile(self):
        lock = self.package_lock()
        with self.assertRaises((KeyError, ValueError)):
            MOD.package_selection(lock, "bpi-other", self.cache)

    def test_non_regular_source_is_rejected(self):
        with self.assertRaises(ValueError):
            MOD.regular(Path("/dev/null"))
        linked = self.base / "source-link.img"
        linked.symlink_to(self.source)
        with self.assertRaises(ValueError):
            MOD.regular(linked)

    def resume_fixture(self):
        lock = REPO / "config/spacemit-k1-acceleration/noble.lock.json"
        output = self.base / "prepared"
        output.mkdir()
        identity = {"board": "bpi-cm6", "source_sha256": MOD.sha256(self.source),
                    "acceleration_lock_sha256": MOD.sha256(lock), "schema_version": 1}
        marker = output / "preparation.json"
        marker.write_text(json.dumps({"identity": identity, "status": "failed"}))
        argv = [str(SPEC.origin), "--inside", "--resume", "--board", "bpi-cm6",
                "--image", str(self.source), "--output", str(output),
                "--deb-cache", str(self.cache), "--lock", str(lock)]
        return marker, identity, argv

    def assert_resume_rejected_without_mount(self, argv):
        with mock.patch.object(MOD.sys, "argv", argv), \
             mock.patch.object(MOD.os, "geteuid", return_value=0), \
             mock.patch.object(MOD, "package_selection", return_value=[]), \
             mock.patch.object(MOD, "run", side_effect=AssertionError("來源拒絕前不得執行命令")) as command, \
             mock.patch.object(MOD.subprocess, "check_output", side_effect=AssertionError("來源拒絕前不得讀取分區")):
            with self.assertRaises(ValueError):
                MOD.main()
        command.assert_not_called()

    def test_resume_source_mutation_is_rejected_before_mount(self):
        marker, _, argv = self.resume_fixture()
        old_marker = marker.read_bytes()
        self.source.write_bytes(b"changed-source")
        self.assert_resume_rejected_without_mount(argv)
        self.assertEqual(marker.read_bytes(), old_marker)

    def test_resume_different_board_is_rejected_before_mount(self):
        marker, identity, argv = self.resume_fixture()
        identity["board"] = "bpi-f3"
        marker.write_text(json.dumps({"identity": identity, "status": "failed"}))
        self.assert_resume_rejected_without_mount(argv)

    def test_resume_changed_package_lock_is_rejected_before_mount(self):
        marker, identity, argv = self.resume_fixture()
        identity["acceleration_lock_sha256"] = "0" * 64
        marker.write_text(json.dumps({"identity": identity, "status": "failed"}))
        self.assert_resume_rejected_without_mount(argv)

    def test_completed_candidate_is_not_rebuilt(self):
        marker, identity, argv = self.resume_fixture()
        marker.write_text(json.dumps({"identity": identity, "status": "complete"}))
        self.assert_resume_rejected_without_mount(argv)


if __name__ == "__main__":
    unittest.main()
