"""佇列至 H618 的離線整合；真實父子鎖與暫存租約，所有硬體入口均攔截。"""

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bpi_lab_h618_stages as stages
import test_bpi_lab_station as stations
import bpi_lab_queue as queue


class QueueH618RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = stages.StageContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.locks = self.fixture.runtime.lock_root
        self.db = queue.connect(self.root / "queue.sqlite3")
        self.addCleanup(self.db.close)
        self.output = self.root / "queue-evidence"
        self.station = stations.hardware()
        config = self.fixture.config
        self.station.update(station_id=config["station_id"], hardware_id=config["hardware_id"],
                            test_version=config["test_version"], boot_config_sha256=self.fixture.config_ref["sha256"],
                            compatible_boards=["bpi-m4z-emac"])
        self.station["resources"] = {"uart": self.fixture.lifecycle["uart"]["stable_path"],
                                     "power": "power:bpi-pw-1", "media": "cid:" + stages.life.customer.EXPECTED["cid"]}
        self.station["authorization"].update(hardware_id=config["hardware_id"],
                                             media_identity=self.station["resources"]["media"])
        self.catalog = {"schema": "bpi-lab-catalog-v1", "root": str(self.root), "hardware_validated": False,
                        "entries": [{**self.fixture.request["image"], "image_id": "offline-h618",
                                     "artifact_board": "bananapim4zero", "architecture": "arm64", "issues": []}]}
        queue.register_station(self.db, self.station)
        queue.import_catalog(self.db, self.catalog)
        queue.schedule(self.db, self.station["station_id"])
        self.key = self.db.execute("SELECT work_key FROM jobs").fetchone()[0]
        self.calls = []

    def runner(self, station, request, directory):
        self.calls.append(request["stage"])
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                result = stages.adapter.run_stage(self.fixture.config_ref["path"],
                                                  self.fixture.config_ref["sha256"], request)
                code = 0
            except BaseException as exc:
                result, code = {"error_type": type(exc).__name__}, 1
            with os.fdopen(write_fd, "wb") as stream:
                stream.write(queue.encode(result))
            os._exit(code)
        os.close(write_fd)
        try:
            with os.fdopen(read_fd, "rb") as stream:
                blob = stream.read(queue.MAX_JSON + 1)
        finally:
            _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0, blob)
        return queue.decode(blob)

    def run_job(self):
        return queue.run_one(self.db, self.station["station_id"], self.output,
                             runner=self.runner, lock_root=self.locks)

    def test_started_preflight_failure_recovers_through_queue_with_parent_locks(self):
        def fail_preflight(*args, **kwargs):
            if kwargs["request"]["stage"] == "preflight":
                raise ValueError("合成首次同次連線失敗")
            return self.fixture.fake_session(*args, **kwargs)
        self.fixture.establish.side_effect = fail_preflight
        result = self.run_job()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.calls, ["preflight", "recovery"])
        state = self.fixture.state()
        self.assertEqual(state["binding"]["work_key"], self.key)
        self.assertEqual(state["binding"]["attempt_id"], result["attempt_id"])
        self.assertIsNone(state["next_stage"])
        reports = [queue.read_json(row[0]) for row in self.db.execute("SELECT path FROM reports ORDER BY report_id")]
        self.assertTrue(reports[0]["needs_recovery"])
        self.assertFalse(reports[1]["needs_recovery"])
        self.assertEqual(reports[1]["hardware_qualification"], "awaiting_hardware")
        with queue.reservation_store(self.locks) as store:
            self.assertEqual(store.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_pure_preflight_blocked_never_starts_h618_recovery(self):
        with mock.patch.object(stages.adapter, "load_config", side_effect=ValueError("合成本機設定拒絕")):
            result = self.run_job()
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(self.calls, ["preflight"])
        self.assertFalse((self.locks / stages.adapter.STATE_FILE).exists())
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "ready")

    def test_existing_stage_pending_preflight_cannot_release_queue_reservation(self):
        before = None
        def runner(station, request, directory):
            nonlocal before
            if request["stage"] == "preflight":
                prior = self.runner(station, request, directory)
                self.assertEqual(prior["status"], "passed")
                before = (self.locks / stages.adapter.STATE_FILE).read_bytes()
                stages.life._save(self.locks, stages.adapter.PUBLICATION_FILE, {"unknown": True})
            report = self.runner(station, request, directory)
            self.assertTrue(report["needs_recovery"])
            return report
        result = queue.run_one(self.db, self.station["station_id"], self.output,
                               runner=runner, lock_root=self.locks)
        self.assertEqual(result["state"], "recovery_failed")
        self.assertEqual(self.calls, ["preflight", "preflight", "recovery"])
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "quarantined")
        self.assertEqual((self.locks / stages.adapter.STATE_FILE).read_bytes(), before)
        self.assertTrue((self.locks / stages.adapter.PUBLICATION_FILE).exists())
        with queue.reservation_store(self.locks) as store:
            self.assertGreater(store.execute("SELECT count(*) FROM reservations WHERE work_key=?", (self.key,)).fetchone()[0], 0)

    def test_lost_publication_quarantines_and_holds_cross_database_reservation(self):
        original = stages.adapter.save_json
        def fail(root, name, report):
            if name in ("stage.json", "failure.json"):
                raise OSError("合成發布失敗")
            return original(root, name, report)
        with mock.patch.object(stages.adapter, "save_json", side_effect=fail):
            result = self.run_job()
        self.assertEqual(result["state"], "recovery_failed")
        self.assertEqual(self.calls, ["preflight", "recovery"])
        self.assertTrue((self.locks / stages.adapter.PUBLICATION_FILE).exists())
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "quarantined")
        other = queue.connect(self.root / "other.sqlite3")
        self.addCleanup(other.close)
        station = {**self.station, "station_id": "offline-other-queue"}
        queue.register_station(other, station)
        queue.import_catalog(other, self.catalog)
        queue.schedule(other, station["station_id"])
        runner = mock.Mock(side_effect=AssertionError("持久隔離期間不得操作另一工作"))
        with self.assertRaises(ValueError):
            queue.run_one(other, station["station_id"], self.output, lock_root=self.locks, runner=runner)
        runner.assert_not_called()
        with queue.reservation_store(self.locks) as store:
            self.assertGreater(store.execute("SELECT count(*) FROM reservations WHERE work_key=?", (self.key,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
