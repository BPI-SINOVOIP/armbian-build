"""佇列與 CLI 離線回歸；所有站點均為模擬，不操作實體裝置。"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
import bpi_lab as cli
import bpi_lab_queue as queue
import bpi_lab_station as station_api


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root/"queue.sqlite3"
        self.db = queue.connect(self.db_path)
        self.output = self.root/"evidence"
        self.locks = self.root/"locks"
        self.station = cli.station_template("bpi-test", True)
        queue.register_station(self.db, self.station)
        image = {"image_id": "i1", "relative_path": "bpi-test/test.img.xz", "board": "bpi-test",
                 "artifact_board": "bananapitest", "architecture": "arm64", "family": "test",
                 "release": "bookworm", "variant": "minimal", "branch": "current", "kernel": "6.6.1",
                 "compressed_bytes": 8, "expected_sha256": "a"*64, "identity": {},
                 "issues": [], "source_verified": False}
        self.catalog = {"schema": "bpi-lab-catalog-v1", "root": str(self.root),
                        "entries": [image], "boards": [], "issues": [], "hardware_validated": False}
        queue.import_catalog(self.db, self.catalog)
        queue.schedule(self.db, self.station["station_id"])
        self.key = self.db.execute("SELECT work_key FROM jobs").fetchone()[0]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def run_job(self, **kwargs):
        return queue.run_one(self.db, self.station["station_id"], self.output,
                             lock_root=self.locks, **kwargs)

    def test_five_stages_and_idempotent_schedule(self):
        result = self.run_job()
        self.assertEqual(result["state"], "collected")
        self.assertEqual(queue.schedule(self.db, self.station["station_id"]), 0)
        self.assertIsNone(self.run_job())
        summary = queue.summary(self.db)
        self.assertEqual(summary["reports"], 5)
        self.assertEqual(summary["jobs"]["simulation"], {"collected": 1})
        self.assertEqual(summary["jobs"]["hardware"], {})
        self.assertFalse(summary["all_hardware_tests_passed"])

    def test_database_reopen_does_not_repeat(self):
        self.run_job()
        self.db.close()
        self.db = queue.connect(self.db_path)
        self.assertIsNone(self.run_job())
        self.assertEqual(queue.summary(self.db)["attempts"], 1)

    def failure_runner(self, failure_stage, status="failed", *, needs_recovery=None):
        def runner(station, request, directory):
            report = station_api.run_stage(station, request, directory)
            if request["stage"] == failure_stage:
                report["status"] = status
                if needs_recovery is not None:
                    report["needs_recovery"] = needs_recovery
            return report
        return runner

    def test_smoke_failure_returns_to_rescue(self):
        result = self.run_job(runner=self.failure_runner("smoke"))
        self.assertEqual(result["state"], "failed")
        stages = [row[0] for row in self.db.execute("SELECT stage FROM reports ORDER BY report_id")]
        self.assertEqual(stages, list(queue.STAGES))

    def test_preflight_blocked_does_not_operate_recovery(self):
        result = self.run_job(runner=self.failure_runner("preflight", "blocked"))
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(queue.summary(self.db)["reports"], 1)

    def test_preflight_explicit_false_does_not_operate_recovery(self):
        result = self.run_job(runner=self.failure_runner("preflight", "blocked", needs_recovery=False))
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(queue.summary(self.db)["reports"], 1)
        with queue.reservation_store(self.locks) as store:
            self.assertEqual(store.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_legacy_preflight_failure_does_not_operate_recovery(self):
        result = self.run_job(runner=self.failure_runner("preflight"))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(queue.summary(self.db)["reports"], 1)

    def test_preflight_needs_recovery_keeps_same_attempt_and_failure(self):
        requests = []
        failure = self.failure_runner("preflight", needs_recovery=True)
        def runner(station, request, directory):
            requests.append(request)
            return failure(station, request, directory)
        result = self.run_job(runner=runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual([request["stage"] for request in requests], ["preflight", "recovery"])
        for field in station_api.BINDINGS:
            if field != "stage":
                self.assertEqual(requests[0][field], requests[1][field])
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "ready")
        self.assertFalse(queue.summary(self.db)["all_hardware_tests_passed"])

    def test_preflight_recovery_failure_stays_quarantined_until_queue_recover(self):
        def runner(station, request, directory):
            report = station_api.run_stage(station, request, directory)
            report.update(status="failed", needs_recovery=True)
            return report
        result = self.run_job(runner=runner)
        attempt = result["attempt_id"]
        self.assertEqual(result["state"], "recovery_failed")
        self.db.close()
        self.db = queue.connect(self.db_path)
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "quarantined")
        with queue.reservation_store(self.locks) as store:
            self.assertGreater(store.execute("SELECT count(*) FROM reservations WHERE work_key=?", (self.key,)).fetchone()[0], 0)
        with self.assertRaises(ValueError):
            queue.retry(self.db, self.key, "尚未完成救援，不可重試")
        recovered = queue.recover(self.db, self.key, self.output, lock_root=self.locks)
        self.assertEqual(recovered["state"], "interrupted_recovered")
        self.assertEqual(recovered["attempt_id"], attempt)
        with queue.reservation_store(self.locks) as store:
            self.assertEqual(store.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_preflight_recovery_decision_survives_missing_receipt_and_reopen(self):
        original = queue.save_json
        def interrupt(path, report):
            if Path(path).name == "queue-receipt.json" and report["stage"] == "preflight":
                raise KeyboardInterrupt()
            return original(path, report)
        with mock.patch.object(queue, "save_json", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_job(runner=self.failure_runner("preflight", needs_recovery=True))
        self.db.close()
        self.db = queue.connect(self.db_path)
        calls = []
        def runner(station, request, directory):
            calls.append(request["stage"])
            return station_api.run_stage(station, request, directory)
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(calls, ["recovery"])

    def test_preflight_receipt_io_failure_still_requires_recovery(self):
        original = queue.save_json
        def fail(path, report):
            if Path(path).name == "queue-receipt.json" and report["stage"] == "preflight":
                raise OSError("合成收據發布失敗")
            return original(path, report)
        with mock.patch.object(queue, "save_json", side_effect=fail):
            result = self.run_job(runner=self.failure_runner("preflight", needs_recovery=True))
        self.assertEqual(result["state"], "failed")
        self.assertEqual([row[0] for row in self.db.execute("SELECT stage FROM reports")], ["recovery"])

    def publication_error_runner(self, status, needs_recovery=None):
        def runner(station, request, directory):
            report = station_api.run_stage(station, request, directory)
            if request["stage"] == "preflight":
                report["status"] = status
                if needs_recovery is not None:
                    report["needs_recovery"] = needs_recovery
                raise station_api.StagePublicationError("合成 station 發布失敗", report, adapter_started=True)
            return report
        return runner

    def test_station_publication_error_does_not_lose_recovery_flag(self):
        result = self.run_job(runner=self.publication_error_runner("failed", True))
        self.assertEqual(result["state"], "failed")
        self.assertEqual([row[0] for row in self.db.execute("SELECT stage FROM reports")], ["recovery"])

    def test_station_publication_error_after_passed_new_adapter_recovers(self):
        result = self.run_job(runner=self.publication_error_runner("passed", False))
        self.assertEqual(result["state"], "failed")
        self.assertEqual([row[0] for row in self.db.execute("SELECT stage FROM reports")], ["recovery"])

    def test_station_publication_error_keeps_legacy_behavior_without_flag(self):
        result = self.run_job(runner=self.publication_error_runner("passed"))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(queue.summary(self.db)["reports"], 0)

    def test_station_publication_error_after_pure_blocked_false_never_recovers(self):
        result = self.run_job(runner=self.publication_error_runner("blocked", False))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(queue.summary(self.db)["reports"], 0)

    def test_station_unknown_result_before_spawn_does_not_recover(self):
        runner = mock.Mock(side_effect=station_api.StagePublicationError("合成啟動前發布失敗", None))
        result = self.run_job(runner=runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(runner.call_count, 1)
        with queue.reservation_store(self.locks) as store:
            self.assertEqual(store.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)

    def test_station_unknown_started_result_survives_reopen_before_recovery(self):
        runner = mock.Mock(side_effect=station_api.StageExecutionError(
            "合成啟動後結果未知", None, adapter_started=True))
        with mock.patch.object(queue, "return_to_rescue", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_job(runner=runner)
        attempt = queue.get_job(self.db, self.key)["attempt_id"]
        self.db.close()
        self.db = queue.connect(self.db_path)
        runner = mock.Mock(wraps=station_api.run_stage)
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["attempt_id"], attempt)
        self.assertEqual([call.args[1]["stage"] for call in runner.call_args_list], ["recovery"])

    def test_queue_publication_error_after_passed_new_adapter_recovers(self):
        original = queue.save_json
        def runner(station, request, directory):
            return {**station_api.run_stage(station, request, directory), "needs_recovery": False}
        def fail(path, report):
            if Path(path).name == "queue-receipt.json" and report["stage"] == "preflight":
                raise OSError("合成成功收據發布失敗")
            return original(path, report)
        with mock.patch.object(queue, "save_json", side_effect=fail):
            result = self.run_job(runner=runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual([row[0] for row in self.db.execute("SELECT stage FROM reports")], ["recovery"])

    def test_preflight_recovery_decision_survives_interruption_before_recovery(self):
        with mock.patch.object(queue, "return_to_rescue", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_job(runner=self.failure_runner("preflight", needs_recovery=True))
        result = self.run_job(key=self.key, resume=True)
        self.assertEqual(result["state"], "failed")
        self.assertEqual([row[0] for row in self.db.execute("SELECT stage FROM reports ORDER BY report_id")],
                         ["preflight", "recovery"])

    def test_new_attempt_does_not_inherit_old_recovery_requirement(self):
        self.run_job(runner=self.failure_runner("preflight", needs_recovery=True))
        queue.retry(self.db, self.key, "已完成同次救援，重新核對設定")
        result = self.run_job(runner=self.failure_runner("preflight", "blocked", needs_recovery=False))
        self.assertEqual(result["state"], "blocked")
        rows = self.db.execute("SELECT stage FROM reports WHERE attempt_id=?", (result["attempt_id"],)).fetchall()
        self.assertEqual([row[0] for row in rows], ["preflight"])

    def test_completed_failure_recovery_is_not_reexecuted_after_finish_crash(self):
        original = queue.finish
        def interrupt(db, key, state):
            if state == "failed":
                raise KeyboardInterrupt()
            return original(db, key, state)
        with mock.patch.object(queue, "finish", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_job(runner=self.failure_runner("preflight", needs_recovery=True))
        runner = mock.Mock(side_effect=AssertionError("已完整發布救援，不可重做"))
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "failed")
        runner.assert_not_called()

    def test_failed_deployment_never_boots(self):
        self.run_job(runner=self.failure_runner("deploy"))
        stages = [row[0] for row in self.db.execute("SELECT stage FROM reports ORDER BY report_id")]
        self.assertEqual(stages, ["preflight", "deploy", "recovery"])

    def test_recovery_failure_quarantines_station(self):
        result = self.run_job(runner=self.failure_runner("recovery"))
        self.assertEqual(result["state"], "recovery_failed")
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "quarantined")
        with self.assertRaises(ValueError):
            self.run_job()
        recovered = queue.recover(self.db, self.key, self.output, lock_root=self.locks)
        self.assertEqual(recovered["state"], "interrupted_recovered")
        self.assertEqual(queue.get_station(self.db, self.station["station_id"])[1], "ready")

    def test_retry_preserves_failure_and_creates_attempt(self):
        self.run_job(runner=self.failure_runner("smoke"))
        first = queue.get_job(self.db, self.key)["attempt_id"]
        queue.retry(self.db, self.key, "修正測試條件後限定重試")
        result = self.run_job()
        self.assertEqual(result["state"], "collected")
        self.assertNotEqual(result["attempt_id"], first)
        self.assertEqual(queue.summary(self.db)["attempts"], 2)
        self.assertEqual(queue.summary(self.db)["historical_nonpassing_reports"], 1)

    def test_passed_job_cannot_blindly_retry(self):
        self.run_job()
        with self.assertRaises(ValueError):
            queue.retry(self.db, self.key, "不應重做")

    def interrupt_before_boot(self):
        def runner(station, request, directory):
            if request["stage"] == "boot":
                raise KeyboardInterrupt()
            return station_api.run_stage(station, request, directory)
        with self.assertRaises(KeyboardInterrupt):
            self.run_job(runner=runner)

    def test_resume_revalidates_and_does_not_redeploy(self):
        self.interrupt_before_boot()
        old = queue.get_job(self.db, self.key)
        self.assertEqual(old["state"], "running")
        self.assertEqual(old["cursor"], 2)
        self.assertEqual(old["inflight"], "boot")
        result = self.run_job(key=self.key, resume=True)
        self.assertEqual(result["state"], "collected")
        self.assertEqual(result["attempt_id"], old["attempt_id"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='deploy'").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE is_resume_guard=1").fetchone()[0], 1)

    def test_resume_guard_failure_does_not_boot(self):
        self.interrupt_before_boot()
        result = self.run_job(key=self.key, resume=True, runner=self.failure_runner("preflight", "blocked"))
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='boot'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='recovery'").fetchone()[0], 1)

    def test_resume_guard_false_does_not_suppress_recovery(self):
        self.interrupt_before_boot()
        result = self.run_job(key=self.key, resume=True,
                              runner=self.failure_runner("preflight", "blocked", needs_recovery=False))
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='recovery'").fetchone()[0], 1)

    def test_resume_guard_exception_returns_to_rescue(self):
        self.interrupt_before_boot()
        def runner(station, request, directory):
            if "resume" in request:
                raise OSError("合成續作核對失敗")
            return station_api.run_stage(station, request, directory)
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='recovery'").fetchone()[0], 1)

    def test_failed_resume_guard_receipt_crash_cannot_resume_boot(self):
        self.interrupt_before_boot()
        with mock.patch.object(queue, "record_stage", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_job(key=self.key, resume=True, runner=self.failure_runner("preflight", "blocked", needs_recovery=False))
        result = self.run_job(key=self.key, resume=True)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='boot'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='recovery'").fetchone()[0], 1)

    def test_changed_receipt_refuses_resume(self):
        self.interrupt_before_boot()
        path = Path(self.db.execute("SELECT path FROM reports LIMIT 1").fetchone()[0])
        with path.open("ab") as stream:
            stream.write(b" ")
        result = self.run_job(key=self.key, resume=True)
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(queue.summary(self.db)["reports"], 2)

    def test_busy_job_blocks_another_claim(self):
        self.interrupt_before_boot()
        with self.assertRaises(ValueError):
            self.run_job(key=self.key)

    def test_explicit_recover_never_redeploys(self):
        self.interrupt_before_boot()
        result = queue.recover(self.db, self.key, self.output, lock_root=self.locks)
        self.assertEqual(result["state"], "interrupted_recovered")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='deploy'").fetchone()[0], 1)

    def test_station_change_invalidates_job(self):
        changed = {**self.station, "test_version": "basic-v2"}
        queue.register_station(self.db, changed)
        self.assertEqual(queue.schedule(self.db, self.station["station_id"]), 1)
        with self.assertRaises(ValueError):
            self.run_job(key=self.key)

    def test_busy_station_configuration_cannot_change(self):
        self.interrupt_before_boot()
        with self.assertRaises(ValueError):
            queue.register_station(self.db, {**self.station, "test_version": "basic-v2"})

    def test_resource_locks_conflict_across_station_names(self):
        other = {**self.station, "station_id": "sim-other"}
        with queue.station_locks(self.station, self.locks):
            with self.assertRaises(ValueError):
                with queue.station_locks(other, self.locks):
                    self.fail("重複占用未被拒絕")
        with queue.station_locks(other, self.locks):
            pass

    def test_wrong_work_report_rejected_before_boot(self):
        def wrong(station, request, directory):
            report = station_api.run_stage(station, request, directory)
            report["work_key"] = "0"*64
            return report
        result = self.run_job(runner=wrong)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(queue.summary(self.db)["reports"], 0)

    def test_duplicate_json_rejected(self):
        with self.assertRaises(ValueError):
            queue.decode(b'{"a":1,"a":2}')

    def test_no_overwrite_or_symlink_evidence(self):
        path = self.root/"record.json"
        queue.save_json(path, {"value": 1})
        with self.assertRaises(FileExistsError):
            queue.save_json(path, {"value": 2})
        (self.root/"link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            queue.save_json(self.root/"link/unsafe.json", {})

    def test_manifest_traversal_rejected(self):
        bad = {**self.catalog, "entries": [{**self.catalog["entries"][0], "relative_path": "../other.img.xz"}]}
        with self.assertRaises(ValueError):
            queue.import_catalog(self.db, bad)

    def test_disabled_hardware_templates_never_execute(self):
        station = cli.station_template("bpi-test")
        queue.register_station(self.db, station)
        queue.schedule(self.db, station["station_id"])
        with self.assertRaises(ValueError):
            queue.run_one(self.db, station["station_id"], self.output, lock_root=self.locks)

    def test_444_import_schedule_dedup_and_reopen(self):
        entries = []
        for index in range(444):
            entry = {**self.catalog["entries"][0], "image_id": f"image-{index}",
                     "relative_path": f"bpi-test/image-{index}.img.xz",
                     "expected_sha256": hashlib.sha256(str(index).encode()).hexdigest()}
            entries.append(entry)
        queue.import_catalog(self.db, {**self.catalog, "entries": entries})
        self.assertEqual(queue.schedule(self.db, self.station["station_id"]), 444)
        self.assertEqual(queue.schedule(self.db, self.station["station_id"]), 0)
        self.db.close()
        self.db = queue.connect(self.db_path)
        self.assertEqual(queue.summary(self.db)["inventory_images"], 444)
        self.assertEqual(queue.get_job(self.db, self.key)["state"], "superseded")

    def test_architecture_os_board_priority(self):
        entries = []
        for arch, release in (("riscv64", "bookworm"), ("arm32", "trixie"), ("arm64", "bookworm"), ("arm32", "bookworm")):
            entries.append({**self.catalog["entries"][0], "image_id": arch+release,
                            "relative_path": f"bpi-test/{arch}-{release}.img.xz",
                            "architecture": arch, "release": release})
        queue.import_catalog(self.db, {**self.catalog, "entries": entries})
        queue.schedule(self.db, self.station["station_id"])
        values = [queue.decode(row[0])["image"] for row in self.db.execute("SELECT body FROM jobs ORDER BY priority")]
        pairs = [(v["architecture"], v["release"]) for v in values]
        self.assertEqual(pairs[0], ("arm32", "bookworm"))
        self.assertEqual(pairs[1], ("arm32", "trixie"))
        self.assertEqual(pairs[-1][0], "riscv64")

    def test_transaction_rollback_leaves_no_partial_catalog(self):
        duplicate = {**self.catalog, "entries": self.catalog["entries"]*2}
        before = queue.summary(self.db)["events"]
        with self.assertRaises(ValueError):
            queue.import_catalog(self.db, duplicate)
        self.assertEqual(queue.summary(self.db)["events"], before)

    def test_sqlite_foreign_keys_enabled(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attempts VALUES ('fake','missing',0,NULL,NULL)")

    def test_removed_source_is_not_queued_again(self):
        queue.import_catalog(self.db, {**self.catalog, "entries": []})
        self.assertEqual(queue.summary(self.db)["inventory_images"], 0)
        self.assertEqual(queue.get_job(self.db, self.key)["state"], "superseded")
        self.assertIsNone(self.run_job())

    def test_unknown_database_is_not_adopted(self):
        path = self.root/"unrelated.sqlite3"
        other = sqlite3.connect(path)
        other.execute("CREATE TABLE unrelated(value TEXT)")
        other.close()
        with self.assertRaises(ValueError):
            queue.connect(path)

    def history_fixture(self):
        data = {"schema": "bpi-h618-lab-summary-v1", "historical_only": True,
                "can_skip_current_media": False, "all_tests_passed": False,
                "images": [{"board": "bananapitest", "image": {"compressed": {"sha256": "a"*64}},
                            "stages": {"deployment": {"history": [{"hardware_id": "0845", "status": "verified"}]}}}]}
        path = self.root/"historical.json"
        return queue.save_json(path, data)

    def test_history_holds_hardware_work_without_passing_or_skipping(self):
        station = cli.station_template("bpi-test")
        queue.register_station(self.db, station)
        queue.schedule(self.db, station["station_id"])
        ref = self.history_fixture()
        self.assertEqual(queue.import_history(self.db, ref["path"], ref["sha256"]), 1)
        self.assertEqual(queue.import_history(self.db, ref["path"], ref["sha256"]), 1)
        summary = queue.summary(self.db)
        self.assertEqual(summary["jobs"]["hardware"], {"review_required": 1})
        self.assertEqual(summary["jobs"]["simulation"], {"queued": 1})
        self.assertEqual(summary["historical_images_referenced"], 1)
        key = self.db.execute("SELECT work_key FROM jobs WHERE mode='hardware'").fetchone()[0]
        queue.release_review(self.db, key, "只安排原紀錄尚未涵蓋的測試條件")
        self.assertEqual(queue.get_job(self.db, key)["state"], "queued")
        self.assertFalse(summary["all_hardware_tests_passed"])

    def test_history_wrong_hash_and_pass_claim_refused(self):
        ref = self.history_fixture()
        with self.assertRaises(ValueError):
            queue.import_history(self.db, ref["path"], "0"*64)
        data = queue.read_json(ref["path"])
        data["all_tests_passed"] = True
        bad = queue.save_json(self.root/"bad-history.json", data)
        with self.assertRaises(ValueError):
            queue.import_history(self.db, bad["path"], bad["sha256"])
        self.assertEqual(queue.summary(self.db)["historical_images_referenced"], 0)

    def test_changed_metadata_supersedes_same_image_id(self):
        updated = {**self.catalog["entries"][0], "kernel": "6.6.2"}
        queue.import_catalog(self.db, {**self.catalog, "entries": [updated]})
        self.assertEqual(queue.get_job(self.db, self.key)["state"], "superseded")
        self.assertEqual(queue.schedule(self.db, self.station["station_id"]), 1)

    def test_metadata_issue_stays_visible_and_cannot_execute(self):
        entry = {**self.catalog["entries"][0], "kernel": "0", "issues": [{"code": "unknown_kernel"}]}
        queue.import_catalog(self.db, {**self.catalog, "entries": [entry]})
        queue.schedule(self.db, self.station["station_id"])
        self.assertIsNone(self.run_job())
        self.assertEqual(queue.summary(self.db)["jobs"]["simulation"]["metadata_blocked"], 1)

    def test_metadata_repair_supersedes_old_blocked_work(self):
        entry = {**self.catalog["entries"][0], "kernel": "0", "issues": [{"code": "unknown_kernel"}]}
        queue.import_catalog(self.db, {**self.catalog, "entries": [entry]})
        queue.schedule(self.db, self.station["station_id"])
        old_key = self.db.execute("SELECT work_key FROM jobs WHERE state='metadata_blocked'").fetchone()[0]
        repaired = {**entry, "kernel": "6.6.2", "issues": []}
        queue.import_catalog(self.db, {**self.catalog, "entries": [repaired]})
        queue.schedule(self.db, self.station["station_id"])
        self.assertEqual(queue.get_job(self.db, old_key)["state"], "superseded")
        self.assertEqual(queue.summary(self.db)["jobs"]["simulation"]["queued"], 1)

    def test_failed_deploy_checkpoint_cannot_resume_as_passed(self):
        with mock.patch.object(queue, "return_to_rescue", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job(runner=self.failure_runner("deploy"))
        result = self.run_job(key=self.key, resume=True)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='boot'").fetchone()[0], 0)

    def test_durable_receipt_reconciled_without_redeployment(self):
        original = queue.record_stage
        calls = []
        def before_commit(db, key, request, reference, report):
            if request["stage"] == "deploy":
                raise KeyboardInterrupt()
            return original(db, key, request, reference, report)
        def runner(station, request, directory):
            calls.append(request["stage"])
            return station_api.run_stage(station, request, directory)
        with mock.patch.object(queue, "record_stage", side_effect=before_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job(runner=runner)
        result = self.run_job(key=self.key, resume=True, runner=runner)
        self.assertEqual(result["state"], "collected")
        self.assertEqual(calls.count("deploy"), 1)

    def test_unknown_deploy_requires_recovery_not_reflash(self):
        def interrupted(station, request, directory):
            if request["stage"] == "deploy":
                raise KeyboardInterrupt()
            return station_api.run_stage(station, request, directory)
        with self.assertRaises(KeyboardInterrupt):
            self.run_job(runner=interrupted)
        self.assertEqual(self.run_job(key=self.key, resume=True)["state"], "interrupted")
        self.assertEqual(self.db.execute("SELECT count(*) FROM reports WHERE stage='deploy'").fetchone()[0], 0)

    def test_shared_resource_quarantine_persists_across_databases(self):
        self.run_job(runner=self.failure_runner("recovery"))
        other = queue.connect(self.root/"other.sqlite3")
        try:
            station = {**self.station, "station_id": "another-station"}
            queue.register_station(other, station)
            queue.import_catalog(other, self.catalog)
            queue.schedule(other, station["station_id"])
            with self.assertRaises(ValueError):
                queue.run_one(other, station["station_id"], self.output, lock_root=self.locks)
            self.assertEqual(queue.summary(other)["reports"], 0)
            queue.recover(self.db, self.key, self.output, lock_root=self.locks)
            other_key = other.execute("SELECT work_key FROM jobs").fetchone()[0]
            queue.retry(other, other_key, "原站點已回救援並釋放資源")
            result = queue.run_one(other, station["station_id"], self.output, lock_root=self.locks)
            self.assertEqual(result["state"], "collected")
        finally:
            other.close()

    def test_modified_job_binding_rejected(self):
        body = queue.decode(queue.get_job(self.db, self.key)["body"])
        body["image_sha256"] = body["image"]["expected_sha256"] = "b"*64
        self.db.execute("UPDATE jobs SET body=? WHERE work_key=?", (queue.encode(body).decode(), self.key))
        with self.assertRaises(ValueError):
            self.run_job()
        with self.assertRaises(ValueError):
            queue.summary(self.db)

    def test_modified_mode_cannot_upgrade_simulation(self):
        self.run_job()
        self.db.execute("UPDATE jobs SET mode='hardware' WHERE work_key=?", (self.key,))
        with self.assertRaises(ValueError):
            queue.summary(self.db)

    def test_unlock_only_after_safe_finish(self):
        self.interrupt_before_boot()
        with self.assertRaises(ValueError):
            queue.unlock_completed(self.db, self.key, self.locks)
        self.run_job(key=self.key, resume=True)
        queue.unlock_completed(self.db, self.key, self.locks)

    def test_two_independent_workers_share_one_database(self):
        other = cli.station_template("bpi-second", True)
        other["compatible_boards"] = ["bpi-test"]
        queue.register_station(self.db, other)
        queue.schedule(self.db, other["station_id"])
        def worker(station_id):
            db = queue.connect(self.db_path)
            try:
                return queue.run_one(db, station_id, self.output, lock_root=self.locks)["state"]
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, station_id) for station_id in (self.station["station_id"], other["station_id"])]
            self.assertEqual([future.result(timeout=10) for future in futures], ["collected", "collected"])
        self.assertEqual(queue.summary(self.db)["reports"], 10)


if __name__ == "__main__":
    unittest.main()
