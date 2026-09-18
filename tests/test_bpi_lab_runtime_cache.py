"""快取來源固定、受限擷取、連結與容量拒絕回歸。"""

import hashlib
import io
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_runtime_cache as cache
from test_bpi_lab_external_guard import elf
import test_bpi_lab_external_bundle as bundle_tests


def pack(rows):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, kind, data in rows:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o755 if kind == tarfile.DIRTYPE or name.endswith("python3.11") else 0o644
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = data.decode()
                member.mode = 0o777
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.args = {"architecture": "arm64", "python": "/usr/bin/python3",
                     "stdlib": "/usr/lib/python3.11", "library_dirs": ["/usr/lib"]}
        self.rows = [(name, tarfile.DIRTYPE, b"") for name in
                     ("usr", "usr/bin", "usr/lib", "usr/lib/python3.11")]
        self.rows += [("usr/bin/python3", tarfile.SYMTYPE, b"python3.11"),
                      ("usr/bin/python3.11", tarfile.REGTYPE, elf()),
                      ("usr/lib/python3.11/os.py", tarfile.REGTYPE, b"pass\n"),
                      ("usr/lib/python3.11/_example.so", tarfile.REGTYPE, elf()),
                      ("usr/lib/python3.11/__pycache__/os.pyc", tarfile.REGTYPE, b"unused"),
                      ("usr/lib/python3.11/sitecustomize.py", tarfile.SYMTYPE, b"/etc/python3.11/sitecustomize.py"),
                      ("usr/lib/python3.11/usercustomize.py", tarfile.REGTYPE, b"raise RuntimeError()"),
                      ("usr/lib/python3.11/config-3.11-arm-linux-gnueabihf/libpython.so", tarfile.SYMTYPE, b"../../unused.so"),
                      ("usr/lib/unused.so", tarfile.REGTYPE, elf()),
                      ("etc/shadow", tarfile.REGTYPE, b"secret"),
                      ("root/.ssh/id_ed25519", tarfile.REGTYPE, b"secret"),
                      ("init", tarfile.REGTYPE, b"unused")]

    def extract(self, rows=None, **changes):
        with cache.CacheTar.open(fileobj=io.BytesIO(pack(self.rows if rows is None else rows)), mode="r:") as archive:
            return cache.extract(archive, **{**self.args, **changes})

    def test_selects_only_python_and_actual_dependencies(self):
        blob, dependencies = self.extract()
        entries, _ = cache.guard.parse_archive(blob)
        self.assertEqual(len(dependencies), 2)
        self.assertIn("usr/lib/python3.11/os.py", entries)
        for name in ("etc/shadow", "root/.ssh/id_ed25519", "init", "usr/lib/unused.so",
                     "usr/lib/python3.11/__pycache__/os.pyc", "usr/lib/python3.11/sitecustomize.py",
                     "usr/lib/python3.11/usercustomize.py",
                     "usr/lib/python3.11/config-3.11-arm-linux-gnueabihf/libpython.so"):
            self.assertNotIn(name, entries)
        self.assertEqual(self.extract()[0], blob)

    def test_duplicate_or_traversal_paths_rejected(self):
        for row in (self.rows[0], ("../outside", tarfile.REGTYPE, b""),
                    ("/absolute", tarfile.REGTYPE, b"")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.extract(self.rows + [row])

    def test_symlink_escape_scope_and_cycles_rejected(self):
        for target in (b"../../../../host", b"/etc/shadow", b"python3", b"missing/../python3.11"):
            rows = [(name, kind, target if name == "usr/bin/python3" else data)
                    for name, kind, data in self.rows]
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.extract(rows)

    def test_parent_symlink_inside_root_preserved(self):
        blob, _ = self.extract(self.rows + [("lib", tarfile.SYMTYPE, b"usr/lib")], library_dirs=["/lib"])
        entries, _ = cache.guard.parse_archive(blob)
        self.assertTrue(stat.S_ISLNK(entries["lib"]["mode"]))
        self.assertEqual(cache.guard.resolve_entry(entries, "/lib")[0], "usr/lib")

    def test_stdlib_cross_scope_link_rejected(self):
        rows = self.rows + [("usr/lib/shared.py", tarfile.REGTYPE, b"pass"),
                            ("usr/lib/python3.11/shared.py", tarfile.SYMTYPE, b"/usr/lib/shared.py")]
        with self.assertRaisesRegex(ValueError, "封裝選入範圍"):
            self.extract(rows)
        rows = self.rows + [("usr/lib/mid.py", tarfile.SYMTYPE, b"/usr/lib/python3.11/os.py"),
                            ("usr/lib/python3.11/alias.py", tarfile.SYMTYPE, b"/usr/lib/mid.py")]
        with self.assertRaisesRegex(ValueError, "中間連結"):
            self.extract(rows)

    def test_extended_headers_and_repeated_root_are_bounded(self):
        for kind in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                     tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            header = tarfile.TarInfo("extended")
            header.type, header.size = kind, cache.MAX_EXTENDED_HEADER + 1
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "延伸標頭超界"):
                cache.CacheTar.open(fileobj=io.BytesIO(header.tobuf()), mode="r:")
        with mock.patch.object(cache, "MAX_MEMBERS", 3), self.assertRaises(ValueError):
            self.extract([(".", tarfile.DIRTYPE, b"")] * 4)
        with self.assertRaisesRegex(ValueError, "標頭期限"):
            cache.CacheTar.open(fileobj=io.BytesIO(pack(self.rows)), mode="r:",
                                check=mock.Mock(side_effect=ValueError("標頭期限")))
        header = tarfile.TarInfo("extended")
        header.type = tarfile.XHDTYPE
        with self.assertRaisesRegex(ValueError, "巢狀過深"):
            cache.CacheTar.open(fileobj=io.BytesIO(header.tobuf() * 9 + pack(self.rows)), mode="r:")

    def test_pax_sparse_mapping_rejected_before_processing(self):
        for headers in ({"GNU.sparse.major": "1", "GNU.sparse.minor": "0"},
                        {"GNU.sparse.map": "0,100"}, {"GNU.sparse.size": "100"}):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
                member = tarfile.TarInfo("sparse")
                member.pax_headers = headers
                archive.addfile(member)
            with self.subTest(headers=headers), self.assertRaisesRegex(ValueError, "PAX 稀疏"):
                cache.CacheTar.open(fileobj=io.BytesIO(output.getvalue()), mode="r:")

    def test_cache_to_bundle_keeps_resolvable_links(self):
        fixture = bundle_tests.BundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source, _ = self.extract()
        rescue, _ = cache.guard.parse_archive(source)
        blob, _, _ = cache.bundle.select(fixture.original, rescue, **self.args)
        additions, _ = cache.guard.parse_archive(blob)
        merged = {**fixture.original, **additions}
        for name, item in additions.items():
            if stat.S_ISLNK(item["mode"]):
                cache.guard.resolve_entry(merged, name)

    def test_special_node_and_hardlink_rejected(self):
        for kind in (tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.extract(self.rows + [("usr/lib/python3.11/bad", kind, b"usr/bin/python3.11")])

    def test_wrong_architecture_and_missing_python_rejected(self):
        with self.assertRaises(ValueError):
            self.extract(architecture="riscv64")
        with self.assertRaises(ValueError):
            self.extract([row for row in self.rows if row[0] != "usr/bin/python3.11"])

    def test_invalid_scope_rejected(self):
        for change in ({"library_dirs": ["/etc"]}, {"library_dirs": ["/usr/lib", "/usr/lib"]},
                       {"python": "/bin/sh"}, {"stdlib": "/root"}, {"architecture": "x86"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.extract(**change)

    def test_limits_and_deadline(self):
        with mock.patch.object(cache, "MAX_MEMBERS", 2), self.assertRaises(ValueError):
            self.extract()
        with mock.patch.object(cache.guard, "MAX_ARCHIVE", 16), self.assertRaises(ValueError):
            self.extract()
        with self.assertRaisesRegex(ValueError, "測試期限"):
            self.extract(check=mock.Mock(side_effect=ValueError("測試期限")))

    def test_build_replays_real_zstd_without_target_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blob = subprocess.run(["/usr/bin/zstd", "-c", "--quiet"], input=pack(self.rows),
                                  stdout=subprocess.PIPE, check=True).stdout
            source = root / "rootfs.tar.zst"
            source.write_bytes(blob)
            ref = {"path": str(source), "sha256": hashlib.sha256(blob).hexdigest()}
            report = cache.build(ref, root / "output", **self.args)
            self.assertFalse(report["runtime_executed"])
            self.assertFalse(report["bootable_rescue"])
            self.assertFalse(report["hardware_validated"])
            self.assertEqual(Path(report["archive"]["path"]).read_bytes(), self.extract()[0])
            with self.assertRaises((ValueError, OSError)):
                cache.build(ref, root / "output", **self.args)
            with self.assertRaises(ValueError):
                cache.build({**ref, "sha256": "0" * 64}, root / "bad", **self.args)
            self.assertFalse((root / "bad").exists())
            for bad_blob in (blob[:10], b"not-zstd"):
                source.write_bytes(bad_blob)
                with self.assertRaises(ValueError):
                    cache.build({**ref, "sha256": hashlib.sha256(bad_blob).hexdigest()}, root / "bad", **self.args)
                self.assertFalse((root / "bad").exists())

    def test_bad_timeout_or_source_never_decompresses(self):
        for timeout in (True, 0, -1, float("nan"), float("inf"), 3601):
            with self.subTest(timeout=timeout), mock.patch.object(cache, "decompress") as call:
                with self.assertRaises(ValueError):
                    cache.build({}, Path("/unused"), timeout=timeout, **self.args)
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
