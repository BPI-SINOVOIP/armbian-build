"""進度查詢不初始化資料庫，且不忽略即時 WAL 的離線回歸。"""

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab as cli
import bpi_lab_queue as queue


class ReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "queue.sqlite3"

    def test_missing_database_or_parent_is_not_created(self):
        for path in (self.path, self.root / "absent" / "queue.sqlite3"):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                queue.connect(path, readonly=True)
            self.assertFalse(path.exists())
        self.assertFalse((self.root / "absent").exists())

    def test_unknown_and_uninitialized_versions_are_unchanged(self):
        for version in (0, 2):
            path = self.root / f"version-{version}.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute(f"PRAGMA user_version={version}")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                queue.connect(path, readonly=True)
            self.assertEqual(path.read_bytes(), before)

    def test_readonly_refuses_writes_even_after_query_only_disabled(self):
        queue.connect(self.path).close()
        before = self.path.read_bytes()
        db = queue.connect(self.path, readonly=True)
        try:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)
            db.execute("PRAGMA query_only=OFF")
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("INSERT INTO events(kind,body,created) VALUES ('x','{}',0)")
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("PRAGMA user_version=2")
        finally:
            db.close()
        self.assertEqual(self.path.read_bytes(), before)

    def test_live_wal_is_visible_without_initialization(self):
        writer = queue.connect(self.path)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        queue.event(writer, None, "readonly_test", {"hardware_validated": False})
        self.assertGreater(Path(str(self.path) + "-wal").stat().st_size, 0)
        reader = queue.connect(self.path, readonly=True)
        try:
            self.assertEqual(reader.execute("SELECT kind FROM events").fetchone()[0], "readonly_test")
            queue.event(writer, None, "second_test", {})
            self.assertEqual(reader.execute("SELECT count(*) FROM events").fetchone()[0], 2)
        finally:
            reader.close()
            writer.close()

    def test_cli_status_and_jobs_use_readonly_branch(self):
        queue.connect(self.path).close()
        before = self.path.read_bytes()
        for command in ("status", "jobs"):
            with self.subTest(command=command), mock.patch.object(queue, "connect", wraps=queue.connect) as call:
                args = cli.parser().parse_args([command, "--db", str(self.path)])
                cli.dispatch(args)
                call.assert_called_once_with(str(self.path), readonly=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_symlink_database_and_invalid_flag_rejected(self):
        queue.connect(self.path).close()
        alias = self.root / "link.sqlite3"
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            queue.connect(alias, readonly=True)
        with self.assertRaises(ValueError):
            queue.connect(self.path, readonly="true")


if __name__ == "__main__":
    unittest.main()
