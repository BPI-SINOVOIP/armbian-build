"""重審候選不能沿普通入口改寫佇列，獨立副本流程另有完整回歸。"""

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab as cli
import bpi_lab_queue as queue


class CatalogGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = {"schema": "bpi-lab-catalog-v1", "hardware_validated": False,
                        "root": str(self.root), "entries": [
                            {"image_id": "test-image", "relative_path": "test.img.xz", "board": "bpi-f2p"}]}

    def candidates(self):
        for location in ("top", "entry", "both"):
            for value in (None, {}, {"integration_approved": False}, {"integration_approved": True}):
                data = copy.deepcopy(self.catalog)
                if location in ("top", "both"):
                    data["metadata_review"] = value
                if location in ("entry", "both"):
                    data["entries"][0]["metadata_review"] = value
                yield location, value, data

    def test_plain_catalog_still_imports(self):
        db = queue.connect(self.root / "plain.sqlite3")
        self.addCleanup(db.close)
        self.assertEqual(queue.import_catalog(db, self.catalog), 1)
        self.assertEqual(db.execute("SELECT count(*) FROM images").fetchone()[0], 1)

    def test_review_markers_rejected_before_snapshot_transaction(self):
        db = queue.connect(self.root / "queue.sqlite3")
        self.addCleanup(db.close)
        queue.import_catalog(db, self.catalog)
        before = list(db.iterdump())
        for location, value, data in self.candidates():
            with self.subTest(location=location, value=value):
                with mock.patch.object(queue, "_import_catalog_snapshot") as write:
                    with self.assertRaisesRegex(ValueError, "重審候選"):
                        queue.import_catalog(db, data)
                    write.assert_not_called()
                self.assertEqual(list(db.iterdump()), before)

    def test_cli_rejects_before_database_creation_or_station_output(self):
        for number, (location, value, data) in enumerate(self.candidates()):
            with self.subTest(location=location, value=value):
                path = self.root / f"candidate-{number}.json"
                queue.save_json(path, data)
                database = self.root / f"absent-{number}" / "queue.sqlite3"
                stations = self.root / f"stations-{number}"
                for simulation in (False, True):
                    argv = ["prepare", "--catalog", str(path), "--db", str(database),
                            "--stations-dir", str(stations)]
                    if simulation:
                        argv.append("--simulation")
                    with mock.patch.object(queue, "connect") as connect:
                        with self.assertRaisesRegex(ValueError, "重審候選"):
                            cli.dispatch(cli.parser().parse_args(argv))
                        connect.assert_not_called()
                self.assertFalse(database.parent.exists())
                self.assertFalse(stations.exists())

    def test_cli_existing_database_and_sidecars_unchanged(self):
        database = self.root / "existing.sqlite3"
        db = queue.connect(database)
        queue.import_catalog(db, self.catalog)
        db.close()
        before = database.read_bytes()
        before_sidecars = {s: Path(str(database) + s).exists() for s in ("-wal", "-shm", "-journal")}
        candidate = {**self.catalog, "metadata_review": {"integration_approved": False}}
        path = self.root / "candidate.json"
        queue.save_json(path, candidate)
        args = cli.parser().parse_args(["prepare", "--catalog", str(path), "--db", str(database),
                                       "--stations-dir", str(self.root / "stations")])
        with self.assertRaisesRegex(ValueError, "重審候選"):
            cli.dispatch(args)
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual({s: Path(str(database) + s).exists() for s in before_sidecars}, before_sidecars)

    def test_python_prepare_also_refuses_candidate(self):
        data = {**self.catalog, "metadata_review": {"integration_approved": True}}
        with mock.patch.object(queue, "_import_catalog_snapshot") as write:
            with self.assertRaisesRegex(ValueError, "重審候選"):
                cli.prepare(None, data, self.root / "stations", False)
            write.assert_not_called()
        self.assertFalse((self.root / "stations").exists())

    def test_nonmapping_entries_refused(self):
        for entries in (None, {}, [None], ["metadata_review"]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                queue.validate_catalog_import({**self.catalog, "entries": entries})


if __name__ == "__main__":
    unittest.main()
