"""CM6 唯讀蒐證工具的失敗邊界與完整性回歸；不蒐集建置主機證據。"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TOOL = Path(__file__).resolve().parents[1] / "tools/collect_bpi_cm6_connectivity.py"
SPEC = importlib.util.spec_from_file_location("cm6_connectivity", TOOL)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class ConnectivityTests(unittest.TestCase):
    def test_missing_command_is_explicit(self):
        with mock.patch.object(collector.shutil, "which", return_value=None):
            result = collector.run_command(("nmcli", "device", "status"))
        self.assertEqual(result["status"], "missing_command")

    def test_command_permission_denied_is_explicit(self):
        with mock.patch.object(collector.shutil, "which", return_value="/usr/bin/nmcli"), \
                mock.patch.object(collector.subprocess, "Popen", side_effect=PermissionError):
            result = collector.run_command(("nmcli", "device", "status"))
        self.assertEqual(result["status"], "permission_denied")

    def test_timeout_keeps_partial_output_and_stops_process(self):
        result = collector.run_command((sys.executable, "-I", "-B", "-c",
                                        "import time; print('開始', flush=True); time.sleep(5)"), timeout=0.3)
        self.assertEqual(result["status"], "timeout")
        self.assertIn("開始", result["stdout"])
        self.assertLess(result["elapsed_seconds"], 2)

    def test_output_limit_stops_unbounded_producer(self):
        result = collector.run_command((sys.executable, "-I", "-B", "-c",
                                        "import os;\nwhile True: os.write(1, b'x'*8192)"),
                                       timeout=2, byte_limit=1024)
        self.assertEqual(result["status"], "output_limit")
        self.assertEqual(result["captured_bytes"], 1024)
        self.assertTrue(result["truncated"])

    def test_file_limit_fifo_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowed = root / "allowed"
            allowed.mkdir()
            (allowed / "large").write_bytes(b"x" * 20)
            os.mkfifo(allowed / "fifo")
            (root / "outside").write_text("不得蒐集")
            (allowed / "escape").symlink_to(root / "outside")
            result = collector.read_small(allowed / "large", (allowed,), limit=4)
            self.assertEqual(result["status"], "truncated")
            self.assertEqual(result["captured_bytes"], 4)
            self.assertEqual(collector.read_small(allowed / "fifo", (allowed,))["status"], "unsupported_file_type")
            self.assertEqual(collector.read_small(allowed / "escape", (allowed,))["status"], "outside_allowed_roots")

    def test_file_permission_denied_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state"
            path.write_text("1")
            with mock.patch.object(collector.os, "open", side_effect=PermissionError):
                result = collector.read_small(path, (Path(tmp),))
            self.assertEqual(result["status"], "permission_denied")

    def test_directory_limit_and_sysfs_snapshot_use_only_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rfkill = root / "sys/class/rfkill"
            rfkill.mkdir(parents=True)
            for index in range(collector.DIRECTORY_ENTRIES + 1):
                (rfkill / f"rfkill{index}").mkdir()
            record, entries = collector.list_small(rfkill, r"rfkill\d+")
            self.assertEqual(record["status"], "truncated")
            self.assertEqual(len(entries), collector.DIRECTORY_ENTRIES)
            model = root / "proc/device-tree/model"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"BananaPi BPI-CM6\0")
            snapshot = collector.collect_files(root)
            self.assertTrue(any(item.get("text") == "BananaPi BPI-CM6\0" for item in snapshot["files"]))
            self.assertFalse(any("/dev/" in item["path"] for item in snapshot["files"]))

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(collector, "run_command") as command:
            with self.assertRaises(FileExistsError):
                collector.collect(Path(tmp))
            command.assert_not_called()

    def test_manifest_hashes_and_missing_commands_do_not_grant_pass(self):
        def fake_command(argv, **kwargs):
            if argv[-1] == "--_snapshot":
                return {"status": "completed", "stdout": '{"files":[]}', "stderr": ""}
            return {"status": "missing_command", "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(collector, "COMMANDS", (("nm", ("nmcli", "device", "status")),)), \
                mock.patch.object(collector, "run_command", side_effect=fake_command):
            output = Path(tmp) / "new"
            result = collector.collect(output)
            self.assertEqual(result["status"], "collected_unverified")
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertFalse(manifest["hardware_verified"])
            self.assertEqual(manifest["commands"][0]["status"], "missing_command")
            for line in (output / "SHA256SUMS").read_text().splitlines():
                expected, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), expected)

    def test_fixed_commands_have_no_state_changing_operations(self):
        for _, argv in collector.COMMANDS:
            self.assertNotIn("--show-secrets", argv)
            self.assertFalse(set(argv) & {"connect", "disconnect", "set", "restart", "start", "stop", "pair", "scan", "unblock", "block"})
            if argv[0] == "ethtool":
                self.assertTrue(len(argv) == 2 or argv[1] in ("-i", "-S"))
            if argv[0] == "journalctl":
                self.assertIn("--lines=200", argv)
                self.assertIn("--no-pager", argv)

    def test_filesystem_timeout_preserves_partial_collection(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(collector, "COMMANDS", ()), \
                mock.patch.object(collector, "run_command", return_value={
                    "status": "timeout", "stdout": "未完成的快照", "stderr": ""}):
            output = Path(tmp) / "partial"
            self.assertEqual(collector.collect(output)["status"], "collected_unverified")
            result = json.loads((output / "filesystem.json").read_text())
            self.assertEqual(result["collection"]["status"], "timeout")
            self.assertEqual(result["partial_output"], "未完成的快照")


if __name__ == "__main__":
    unittest.main()
