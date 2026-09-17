"""佇列複審回歸；僅使用臨時資料庫、模擬器及被攔截的外部入口。"""

import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab as cli
import bpi_lab_queue as queue
import bpi_lab_station as station_api
from test_bpi_lab_station import _ADAPTER


class QueueReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db_path = self.root / "queue.sqlite3"
        self.db = queue.connect(self.db_path)
        self.addCleanup(self.db.close)
        self.output = self.root / "evidence"
        self.locks = self.root / "locks"
        self.station = cli.station_template("bpi-review", simulation=True)
        image = {
            "image_id": "review-image-1", "board": "bpi-review",
            "artifact_board": "bananapireview", "architecture": "arm64",
            "release": "bookworm", "variant": "minimal", "kernel": "6.6.1",
            "relative_path": "bpi-review/test.img.xz", "expected_sha256": "a" * 64,
            "identity": {}, "issues": [], "source_verified": False,
        }
        self.catalog = {
            "schema": "bpi-lab-catalog-v1", "root": str(self.root),
            "entries": [image], "boards": [], "issues": [], "hardware_validated": False,
        }
        queue.register_station(self.db, self.station)
        queue.import_catalog(self.db, self.catalog)
        queue.schedule(self.db, self.station["station_id"])
        self.key = self.db.execute("SELECT work_key FROM jobs").fetchone()[0]

    def run_job(self, **kwargs):
        return queue.run_one(self.db, self.station["station_id"], self.output,
                             lock_root=self.locks, **kwargs)

    def interrupt_deploy(self, durable_receipt=False):
        if durable_receipt:
            original = queue.record_stage

            def record(db, key, request, reference, report):
                if request["stage"] == "deploy":
                    raise KeyboardInterrupt()
                return original(db, key, request, reference, report)

            with mock.patch.object(queue, "record_stage", side_effect=record):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_job()
        else:
            def runner(station, request, directory):
                if request["stage"] == "deploy":
                    raise KeyboardInterrupt()
                return station_api.run_stage(station, request, directory)

            with self.assertRaises(KeyboardInterrupt):
                self.run_job(runner=runner)

    def interrupt_recovery(self, durable_receipt=False):
        if durable_receipt:
            with mock.patch.object(queue, "record_stage", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                queue.recover(self.db, self.key, self.output, lock_root=self.locks)
        else:
            with self.assertRaises(KeyboardInterrupt):
                queue.recover(self.db, self.key, self.output, lock_root=self.locks,
                              runner=mock.Mock(side_effect=KeyboardInterrupt))

    def test_stdout_failure_after_adapter_start_recovers_same_attempt(self):
        self.check_stdout_failure(recovery_status="passed")

    def test_stdout_failure_then_blocked_recovery_keeps_reservation(self):
        self.check_stdout_failure(recovery_status="blocked")

    def check_stdout_failure(self, recovery_status):
        adapter = self.root / "adapter.py"
        adapter.write_text("#!" + str(Path(sys.executable).resolve()) + "\n" + _ADAPTER
                           + "out.update(status='failed', needs_recovery=True)\nprint(json.dumps(out))\n",
                           encoding="utf-8")
        adapter.chmod(0o700)
        external = {"kind": "external-v1", "argv": [str(adapter)],
                    "sha256": hashlib.sha256(adapter.read_bytes()).hexdigest()}
        original = station_api._reserve_evidence
        calls, errors = [], []
        def reserve(directory):
            fd, files = original(directory)
            wrapper = mock.Mock(wraps=files["stdout.log"])
            wrapper.write.side_effect = OSError("合成 stdout 持久寫入失敗")
            files["stdout.log"] = wrapper
            return fd, files
        def runner(station, request, directory):
            calls.append(request)
            if request["stage"] == "preflight":
                with mock.patch.object(station_api, "_reserve_evidence", side_effect=reserve):
                    try:
                        return station_api.run_stage({**station, "adapter": external}, request, directory)
                    except station_api.StagePublicationError as exc:
                        errors.append(exc)
                        raise
            report = station_api.run_stage(station, request, directory)
            report["status"] = recovery_status
            return report
        result = self.run_job(runner=runner)
        self.assertEqual(len(errors), 1)
        self.assertIsNone(errors[0].report)
        self.assertIs(errors[0].adapter_started, True)
        self.assertEqual([item["stage"] for item in calls], ["preflight", "recovery"])
        self.assertEqual(calls[0]["attempt_id"], calls[1]["attempt_id"])
        recovered = recovery_status == "passed"
        self.assertEqual(result["state"], "failed" if recovered else "recovery_failed")
        reopened = queue.connect(self.db_path)
        self.addCleanup(reopened.close)
        self.assertEqual(queue.get_station(reopened, self.station["station_id"])[1],
                         "ready" if recovered else "quarantined")
        with queue.reservation_store(self.locks) as store:
            self.assertEqual(bool(store.execute("SELECT count(*) FROM reservations").fetchone()[0]), not recovered)

    def test_explicit_recovery_receipt_resume_does_not_repeat_recovery(self):
        self.check_recovery_receipt(recover_again=False)

    def test_explicit_recovery_receipt_recover_does_not_repeat_recovery(self):
        self.check_recovery_receipt(recover_again=True)

    def check_recovery_receipt(self, recover_again):
        self.interrupt_deploy()
        self.interrupt_recovery(durable_receipt=True)
        attempt = queue.get_job(self.db, self.key)["attempt_id"]
        runner = mock.Mock(side_effect=AssertionError("已有完整同次救援收據，不得再次操作"))
        reopened = queue.connect(self.db_path)
        self.addCleanup(reopened.close)
        if recover_again:
            result = queue.recover(reopened, self.key, self.output, lock_root=self.locks, runner=runner)
        else:
            result = queue.run_one(reopened, self.station["station_id"], self.output, key=self.key,
                                   resume=True, lock_root=self.locks, runner=runner)
        runner.assert_not_called()
        self.assertEqual(result["state"], "interrupted_recovered")
        self.assertEqual(result["attempt_id"], attempt)
        self.assertEqual(reopened.execute("SELECT count(*) FROM reports WHERE stage='recovery'").fetchone()[0], 1)
        intents = [queue.decode(row[0]) for row in reopened.execute("SELECT body FROM events WHERE kind='stage_started'")]
        self.assertEqual([item["stage"] for item in intents], ["preflight", "deploy", "recovery"])
        with queue.reservation_store(self.locks) as store:
            self.assertEqual(store.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_wrong_attempt_recovery_receipt_keeps_isolation(self):
        self.interrupt_deploy()
        self.interrupt_recovery(durable_receipt=True)
        intent = queue.decode(self.db.execute(
            "SELECT body FROM events WHERE kind='stage_started' ORDER BY event_id DESC LIMIT 1").fetchone()[0])
        path = Path(intent["evidence_directory"]) / "queue-receipt.json"
        report = queue.read_json(path)
        report["attempt_id"] = "other-attempt"
        path.write_bytes(queue.encode(report))
        runner = mock.Mock(side_effect=AssertionError("錯誤收據不得觸發任何操作"))
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "interrupted")
        result = queue.recover(self.db, self.key, self.output, lock_root=self.locks, runner=runner)
        self.assertEqual(result["state"], "recovery_failed")
        runner.assert_not_called()
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "quarantined")
        with queue.reservation_store(self.locks) as store:
            self.assertGreater(store.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def assert_resume_does_not_redeploy(self):
        calls = []

        def runner(station, request, directory):
            calls.append(request["stage"])
            return station_api.run_stage(station, request, directory)

        try:
            self.run_job(key=self.key, resume=True, runner=runner)
        except ValueError:
            pass
        self.assertNotIn("deploy", calls, "救援尚未完成，不得由續作重新部署")

    def test_unknown_deploy_then_interrupted_recovery_never_redeploys(self):
        """救援中斷不能消除未知部署必須先救援、再明示重試的限制。"""
        self.interrupt_deploy()
        self.assertEqual(self.run_job(key=self.key, resume=True)["state"], "interrupted")
        self.interrupt_recovery()
        self.assert_resume_does_not_redeploy()

    def test_durable_deploy_then_interrupted_recovery_never_redeploys(self):
        """部署已有落盤收據，救援中斷後仍不能重刷已完成的部署。"""
        self.interrupt_deploy(durable_receipt=True)
        self.interrupt_recovery()
        self.assert_resume_does_not_redeploy()

    def test_simulate_rechecks_adapter_when_claiming_work(self):
        """命令前置檢查與領取之間的設定變更不能啟動外部適配器。"""
        original = queue.run_one
        changed = False

        def race(db, station_id, output, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                station = {**self.station, "adapter": {
                    "kind": "external-v1", "argv": ["/never-executed"], "sha256": "d" * 64,
                }}
                queue.register_station(db, station)
                queue.schedule(db, station_id)
            kwargs["lock_root"] = self.locks
            return original(db, station_id, output, **kwargs)

        args = SimpleNamespace(command="simulate", db=str(self.db_path), evidence=str(self.output))
        with mock.patch.object(queue, "run_one", side_effect=race), mock.patch.object(
            station_api, "_external",
            side_effect=station_api.StationError("測試已攔截，禁止執行外部程式"),
        ) as external:
            try:
                cli.dispatch(args)
            except ValueError:
                pass
            self.assertTrue(changed, "測試必須確實插入設定變更")
            self.assertEqual(external.call_count, 0, "simulate 不得抵達外部執行入口")

    def test_wrong_attempt_receipt_is_not_reconciled(self):
        """未入庫收據若屬於其他嘗試，必須在任何後續階段前拒絕。"""
        self.interrupt_deploy()
        row = self.db.execute(
            "SELECT body FROM events WHERE work_key=? AND kind='stage_started' "
            "ORDER BY event_id DESC LIMIT 1", (self.key,),
        ).fetchone()
        intent = queue.decode(row[0])
        report = {
            "schema": "bpi-lab-stage-v1",
            **{field: intent["request"][field] for field in station_api.BINDINGS},
            "status": "passed", "hardware_validated": False, "synthetic": True,
        }
        report["attempt_id"] = "different-attempt"
        queue.save_json(Path(intent["evidence_directory"]) / "queue-receipt.json", report)
        runner = mock.Mock(side_effect=AssertionError("拒絕收據前不得執行後續階段"))
        try:
            self.run_job(key=self.key, resume=True, runner=runner)
        except ValueError:
            pass
        runner.assert_not_called()
        self.assertNotEqual(queue.get_job(self.db, self.key)["state"], "collected")

    def test_all_receipts_resume_without_reexecuting_stages(self):
        """所有必要收據已入庫、僅終態未提交時，續作不重跑階段。"""
        original = queue.finish

        def finish(db, key, state):
            if state == "collected":
                raise KeyboardInterrupt()
            return original(db, key, state)

        with mock.patch.object(queue, "finish", side_effect=finish):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job()
        runner = mock.Mock(side_effect=AssertionError("完整收據不應再次操作階段"))
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "collected")
        runner.assert_not_called()

    def test_interrupted_recovery_keeps_cross_database_reservation(self):
        """未知部署及救援中斷期間，共用資源仍須封鎖其他資料庫。"""
        self.interrupt_deploy()
        self.interrupt_recovery()
        other = queue.connect(self.root / "other.sqlite3")
        self.addCleanup(other.close)
        station = {**self.station, "station_id": "sim-other-review"}
        queue.register_station(other, station)
        queue.import_catalog(other, self.catalog)
        queue.schedule(other, station["station_id"])
        runner = mock.Mock(side_effect=AssertionError("隔離期間不得操作共用資源"))
        with self.assertRaises(ValueError):
            queue.run_one(other, station["station_id"], self.output,
                          lock_root=self.locks, runner=runner)
        runner.assert_not_called()
        queue.recover(self.db, self.key, self.output, lock_root=self.locks)
        other_key = other.execute("SELECT work_key FROM jobs").fetchone()[0]
        queue.retry(other, other_key, "原工作已明確完成救援")
        result = queue.run_one(other, station["station_id"], self.output, lock_root=self.locks)
        self.assertEqual(result["state"], "collected")


if __name__ == "__main__":
    unittest.main()
