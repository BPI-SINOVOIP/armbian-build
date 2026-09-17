"""救援 Python 擷取的來源固定、排除敏感內容與相依邊界回歸。"""

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_external_bundle as bundle
from tools import bpi_lab_external_guard as guard
from test_bpi_lab_external_guard import INIT, FUNCTIONS, elf


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.original, self.rescue = {}, {}
        self.args = {"architecture": "arm64", "python": "/usr/bin/python3", "stdlib": "/usr/lib/python3.11",
                     "library_dirs": ["/usr/lib"]}
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(guard.INIT_PROFILES, {(hashlib.sha256(INIT).hexdigest(), hashlib.sha256(FUNCTIONS).hexdigest()):
                                            "離線測試入口"}).start()
        for name, value in (("init", guard.entry(INIT, 0o100755)),
                            ("scripts/functions", guard.entry(FUNCTIONS)),
                            ("scripts/init-bottom/ORDER", guard.entry(b"")),
                            ("bin/sh", guard.entry(elf(), 0o100755)),
                            ("usr/sbin/blkid", guard.entry(elf(), 0o100755))):
            guard._put(self.original, name, value)
        for name, value in (("usr/bin/python3", guard.entry(b"python3.11", 0o120777)),
                            ("usr/bin/python3.11", guard.entry(elf(), 0o100755)),
                            ("usr/lib/python3.11/os.py", guard.entry(b"pass\n")),
                            ("usr/lib/python3.11/lib-dynload/example.so", guard.entry(elf())),
                            ("usr/lib/example.so", guard.entry(elf())),
                            ("usr/lib/not-a-library", guard.entry(b"private")),
                            ("init", guard.entry(b"private")),
                            ("etc/shadow", guard.entry(b"private")),
                            ("root/.ssh/id_ed25519", guard.entry(b"private")),
                            ("usr/lib/modules/unused.ko", guard.entry(elf()))):
            guard._put(self.rescue, name, value)

    def test_selects_target_runtime_without_accounts_keys_init_or_modules(self):
        before = copy.deepcopy(self.original)
        blob, metadata, deps = bundle.select(self.original, self.rescue, **self.args)
        entries, _ = guard.parse_archive(blob)
        self.assertEqual(self.original, before)
        self.assertIn("usr/bin/python3", entries)
        for path in ("init", "etc/shadow", "root/.ssh/id_ed25519", "usr/lib/modules/unused.ko", "usr/lib/not-a-library"):
            self.assertNotIn(path, entries)
        self.assertTrue(deps)
        self.assertEqual(metadata["architecture"], "arm64")

    def test_preserves_original_libraries_and_directory_metadata(self):
        self.original["usr"]["mtime"] = 123
        guard._put(self.original, "usr/lib/example.so", guard.entry(elf() + b"original"))
        blob, _, deps = bundle.select(self.original, self.rescue, **self.args)
        entries, _ = guard.parse_archive(blob)
        self.assertEqual(entries["usr"]["mtime"], 123)
        self.assertNotIn("usr/lib/example.so", entries)
        self.assertTrue(all(row["path"] != "/usr/lib/example.so" for row in deps))

    def test_conflicting_python_rejected(self):
        guard._put(self.original, "usr/bin/python3.11", guard.entry(elf() + b"different", 0o100755))
        with self.assertRaisesRegex(ValueError, "Python 內容衝突"):
            bundle.select(self.original, self.rescue, **self.args)

    def test_wrong_architecture_or_missing_blkid_rejected(self):
        with self.assertRaises(ValueError):
            bundle.select(self.original, self.rescue, **{**self.args, "architecture": "riscv64"})
        del self.original["usr/sbin/blkid"]
        with self.assertRaises(ValueError):
            bundle.select(self.original, self.rescue, **self.args)

    def test_scope_or_version_mismatch_rejected(self):
        for changes in ({"library_dirs": ["/etc"]}, {"stdlib": "/root"}, {"python": "/bin/sh"},
                        {"stdlib": "/usr/lib/python3.12"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                bundle.select(self.original, self.rescue, **{**self.args, **changes})

    def test_symlink_parent_and_escape_rejected(self):
        self.original["usr/bin"] = guard.entry(b"/other", 0o120777)
        with self.assertRaises(ValueError):
            bundle.select(self.original, self.rescue, **self.args)
        self.rescue["usr/bin/python3"] = guard.entry(b"../../../../host", 0o120777)
        with self.assertRaises(ValueError):
            bundle.select({}, self.rescue, **self.args)

    def test_riscv_target_uses_actual_machine_header(self):
        for entries in (self.original, self.rescue):
            for item in entries.values():
                if item["data"].startswith(b"\x7fELF"):
                    item["data"] = elf(243)
        _, result, _ = bundle.select(self.original, self.rescue, **{**self.args, "architecture": "riscv64"})
        self.assertEqual(result["architecture"], "riscv64")

    def test_build_pins_sources_and_does_not_overwrite_or_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            refs = []
            for name, entries in (("original", self.original), ("rescue", self.rescue)):
                path = root / name
                blob = guard.archive(entries)
                path.write_bytes(blob)
                refs.append({"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()})
            with mock.patch.object(guard, "_probe", side_effect=AssertionError("封裝入口不執行目標程式")):
                result = bundle.build(*refs, root / "result", **self.args)
                self.assertFalse(result["runtime_executed"])
                self.assertFalse(result["hardware_validated"])
                self.assertEqual(hashlib.sha256(Path(result["bundle"]["path"]).read_bytes()).hexdigest(), result["bundle"]["sha256"])
                with self.assertRaises((OSError, ValueError)):
                    bundle.build(*refs, root / "result", **self.args)
                bad = {**refs[0], "sha256": "0" * 64}
                with self.assertRaises(ValueError):
                    bundle.build(bad, refs[1], root / "bad", **self.args)
                self.assertFalse((root / "bad").exists())

    def test_probe_rebuilds_sources_before_target_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root, refs = Path(directory), []
            for name, entries in (("original", self.original), ("rescue", self.rescue)):
                path = root / name
                blob = guard.archive(entries)
                path.write_bytes(blob)
                refs.append({"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()})
            report = bundle.build(*refs, root / "result", **self.args)

            def execute(entries, metadata, emulator, timeout):
                self.assertEqual(entries["init"], self.original["init"])
                self.assertIn(guard.PREFIX + "/bpi_lab_external_guard.py", entries)
                self.assertNotIn("etc/shadow", entries)
                return {"schema": "bpi-lab-external-runtime-probe-v1", "hardware_validated": False,
                        "architecture": "arm64", "python": {"python_major": 3, "machine": "aarch64", "imports": "complete"},
                        "blkid_stdout_sha256": "a" * 64, "emulator": None,
                        "runtime_archive_sha256": metadata["archive"]["sha256"]}

            with mock.patch.object(guard, "_probe", side_effect=execute) as call:
                result = bundle.probe(report, None)
                self.assertTrue(result["runtime_executed"])
                self.assertFalse(result["hardware_validated"])
                call.assert_called_once()
            for change in ({"builder_sha256": "0" * 64}, {"dependencies": []}, {"archive_bytes": 1},
                           {"retained_original_dependencies": []}):
                with self.subTest(change=change), mock.patch.object(guard, "_probe") as call:
                    with self.assertRaises(ValueError):
                        bundle.probe({**report, **change}, None)
                    call.assert_not_called()
            for timeout in (True, 0, -1, float("inf"), float("nan"), 601):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    bundle.probe(report, None, timeout=timeout)


if __name__ == "__main__":
    unittest.main()
