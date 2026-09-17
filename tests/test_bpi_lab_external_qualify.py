"""外部首次核定的授權、持久隔離、證據重播與 Q1..Q5 離線回歸。"""

import copy
import hashlib
import os
from pathlib import Path
import signal
import sys
import time
from unittest import mock
import unittest

from tools import bpi_lab_external_qualify as qualify
from test_bpi_lab_external_backend import Fixture, fingerprint, lab


class QualifyTests(Fixture):
    def setUp(self):
        super().setUp()
        self.queue_locks = self.root / "queue-locks"
        mock.patch.object(qualify.queue, "LOCK_ROOT", self.queue_locks).start()
        self.auth = {"schema": "bpi-lab-external-first-authorization-v1", "approved": True, "record": "fixture-first",
                     "scope_sha256": lab.scope_digest(self.config), "image_sha256": self.request["image_sha256"],
                     "stages": list(lab.STAGES), "allow_failure_recovery": True,
                     "driver_sha256": hashlib.sha256(Path(qualify.__file__).read_bytes()).hexdigest(),
                     "queue_sha256": hashlib.sha256(Path(qualify.queue.__file__).read_bytes()).hexdigest()}
        self.auth_ref = self.write("authorization.json", self.auth)
        self.request.update(attempt_id=self.auth["record"], work_key=lab.media.digest(
            {"scope": self.auth["scope_sha256"], "authorization": self.auth_ref["sha256"]}))

    def run_first(self, *, recover=False):
        with mock.patch.object(lab.NativeRuntime, "execute", side_effect=self.fake_result) as execute:
            result = qualify.first_cycle(self.config_ref, self.auth_ref, self.request, execute=True, recover=recover)
        return result, execute

    def reservations(self):
        with qualify.queue.reservation_store(self.queue_locks) as db:
            return db.execute("SELECT count(*) FROM reservations").fetchone()[0]

    def review(self, candidate, images=None):
        return self.write("review.json", {"schema": "bpi-lab-external-first-review-v1", "approved": True,
            "record": "fixture-review", "candidate": candidate, "scope_sha256": lab.scope_digest(self.config),
            "approved_images": [self.request["image_sha256"]] if images is None else images})

    def authorize_images(self, variants):
        self.select_variant(variants[0], "fixture-first")
        self.auth.update(scope_sha256=lab.scope_digest(self.config), image_sha256=self.request["image_sha256"])
        self.auth_ref = self.write("two-images-authorization.json", self.auth)
        self.request.update(attempt_id=self.auth["record"], work_key=lab.media.digest(
            {"scope": self.auth["scope_sha256"], "authorization": self.auth_ref["sha256"]}))

    def test_check_is_read_only_and_does_not_require_existing_qualification(self):
        with mock.patch.object(lab, "NativeRuntime", side_effect=AssertionError("不得操作硬體")):
            result = qualify.first_cycle(self.config_ref, self.auth_ref, self.request)
        self.assertEqual(result["status"], "checked")
        self.assertIs(result["hardware_validated"], False)
        self.assertFalse(self.queue_locks.exists())
        self.assertFalse(self.locks.exists())

    def test_five_stages_review_replays_and_batch_is_separate(self):
        result, execute = self.run_first()
        self.assertEqual(result["status"], "review-required", result)
        self.assertEqual(execute.call_count, 5)
        self.assertEqual(self.reservations(), 0)
        self.assertNotIn("qualification", self.config)
        candidate = self.write("candidate.json", result)
        qualified = qualify.approve(candidate, self.review(candidate), self.root / "qualification.json")
        self.assertEqual(qualified["approved_stages"], list(lab.STAGES))
        fixed = {**self.config, "qualification": self.ref(self.root / "qualification.json")}
        self.assertEqual(qualify.check_qualification(fixed), qualified)
        with self.assertRaises(ValueError):
            self.run_first()

    def test_external_queue_larger_then_smaller_reuses_one_backup_and_sd(self):
        variants = self.reusable_images()
        self.authorize_images(variants)
        initial_backup = copy.deepcopy(self.contract["backup"])
        backup_bytes = Path(initial_backup["path"]).read_bytes()
        source_bytes = {row["bundle"]["image"]["path"]: row["compressed"] for row in variants}
        sd_before = bytes(self.d2.ops.data[str(self.d2.dev / "mmcblk0")])
        with self.native_edges():
            first = qualify.first_cycle(self.config_ref, self.auth_ref, self.request, execute=True)
        self.assertEqual(first["status"], "review-required", first)
        candidate = self.write("candidate.json", first)
        qualify.approve(candidate, self.review(candidate, list(self.config["images"])), self.root / "qualification.json")
        self.config = {**self.config, "qualification": self.ref(self.root / "qualification.json")}
        self.config_ref = self.write("qualified-config.json", self.config)
        fixed = qualify.export_station(self.config_ref, self.root / "station", Path(sys.executable).resolve(), enabled=True)
        disabled = qualify.export_station(self.config_ref, self.root / "disabled-station", Path(sys.executable).resolve())
        self.assertIs(disabled["enabled"], False)
        queue = qualify.queue
        db = queue.connect(self.root / "queue.sqlite3")
        self.addCleanup(db.close)
        queue.register_station(db, fixed)
        entries = [{"image_id": str(index), "board": row["bundle"]["board"],
                    "relative_path": Path(row["bundle"]["image"]["path"]).name,
                    "compressed_bytes": row["bundle"]["image"]["compressed"]["bytes"],
                    "expected_sha256": row["bundle"]["image"]["compressed"]["sha256"], "issues": []}
                   for index, row in enumerate(variants)]
        queue.import_catalog(db, {"schema": "bpi-lab-catalog-v1", "hardware_validated": False,
                                  "root": str(self.root), "entries": entries})
        self.assertEqual(queue.schedule(db, fixed["station_id"]), 2)
        calls, receipts = [], []
        def adapter(argv, request_bytes, timeout, *, capture, on_started):
            self.assertEqual(argv, fixed["adapter"])
            on_started()
            req = lab.station._json_loads(request_bytes)
            calls.append((req["image_sha256"], req["stage"]))
            report = lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], req)
            if req["stage"] == "deploy" and report["status"] == "passed":
                receipts.append(lab.deploy.load(lab.deploy.load(report["operation"])["media_receipt"]))
            stdout = lab.deploy.encode(report)
            capture("stdout", stdout)
            return stdout, b"", None
        for index, variant in enumerate(variants):
            self.select_variant(variant, "fixture-batch-" + str(index))
            # 客戶根資料及原映像外尾端都已改寫；不是重置回初始備份後才測第二套。
            disk = self.d2.ops.data[str(self.d2.dev / "sda")]
            disk[20000:20016], disk[-16:] = b"customer-changes", b"tail-not-zero!!!"
            self.assertNotEqual(fingerprint(bytes(disk)), lab.deploy.load(initial_backup)["artifact"]["raw"])
            key = next(row["work_key"] for row in db.execute("SELECT work_key,body FROM jobs")
                       if queue.decode(row["body"])["image_sha256"] == self.request["image_sha256"])
            with self.native_edges(), mock.patch.object(queue.station_api, "_external", side_effect=adapter):
                result = queue.run_one(db, fixed["station_id"], self.root / "queue-evidence", key=key, lock_root=self.queue_locks)
            self.assertEqual(result["state"], "collected", result)
            receipt = receipts[-1]
            lab.external.validate_result(receipt, receipt["contract"], variant["bundle"]["image"])
            self.assertEqual(receipt["remote"]["readback"], variant["bundle"]["image"]["raw"])
            self.assertEqual(receipt["remote"]["tail_readback"], lab.external.zero_digest(len(disk) - len(variant["raw"])))
            self.assertEqual(bytes(disk), variant["raw"] + bytes(len(disk) - len(variant["raw"])))
            self.assertEqual(self.contract["backup"], initial_backup)
            self.assertEqual(self.reservations(), 0)
        self.assertEqual([stage for _, stage in calls], list(lab.STAGES) * 2)
        self.assertGreater(receipts[0]["source"]["raw"]["bytes"], receipts[1]["source"]["raw"]["bytes"])
        self.assertEqual(Path(initial_backup["path"]).read_bytes(), backup_bytes)
        self.assertEqual(bytes(self.d2.ops.data[str(self.d2.dev / "mmcblk0")]), sd_before)
        for path, blob in source_bytes.items():
            self.assertEqual(Path(path).read_bytes(), blob)
        operations = [lab.station._json_loads(lab.shlex.split(argv[-1])[-1])["operation"] for argv in self.d2.calls]
        self.assertEqual(operations.count("backup"), 1)
        self.assertEqual(queue.summary(db)["reports"], 10)

    def test_falsey_runtime_is_synthetic_and_cannot_approve(self):
        class Falsey:
            def __bool__(self):
                return False
        runtime = Falsey()
        runtime.execute = self.fake_result
        with mock.patch.object(lab, "NativeRuntime", side_effect=AssertionError("不得建立原生入口")):
            result = qualify.first_cycle(self.config_ref, self.auth_ref, self.request, execute=True, runtime=runtime)
        self.assertEqual(result["status"], "test-only", result)
        self.assertIs(result["hardware_validated"], False)
        self.assertFalse(self.locks.exists())
        candidate = self.write("candidate.json", result)
        with self.assertRaises(ValueError):
            qualify.approve(candidate, self.review(candidate), self.root / "qualification.json")

    def test_phase_directory_crash_keeps_same_work_recoverable(self):
        original = lab.deploy.new_directory
        def failed(path):
            if Path(path).name == "boot":
                raise OSError("離線目錄故障")
            return original(path)
        with mock.patch.object(lab.deploy, "new_directory", side_effect=failed):
            result, _ = self.run_first()
        self.assertEqual(result["failed_stage"], "boot")
        self.assertEqual(self.reservations(), 5)
        state = lab.StateStore(self.config, self.contract, self.d2.source).read()
        self.assertEqual((state["stage"], state["status"], state["writer_unresolved"]), ("deploy", "verified", False))
        recovered, execute = self.run_first(recover=True)
        self.assertEqual(recovered["status"], "recovered", recovered)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(self.reservations(), 0)

    def test_output_creation_crash_retains_lease_before_first_intent(self):
        original = lab.deploy.new_directory
        def failed(path):
            if Path(path).name == "first-cycle-fixture-first":
                raise OSError("離線首個目錄故障")
            return original(path)
        with mock.patch.object(lab.deploy, "new_directory", side_effect=failed), self.assertRaises(OSError):
            self.run_first()
        self.assertEqual(self.reservations(), 5)
        recovered, execute = self.run_first(recover=True)
        self.assertEqual(recovered["status"], "recovered", recovered)
        self.assertEqual(execute.call_count, 1)

    def test_deploy_interrupt_recovery_does_not_replace_intent(self):
        def stopped(context, *args):
            if context[-1]["stage"] == "deploy":
                raise TimeoutError("離線 writer 未確認")
            return self.fake_result(context, *args)
        with mock.patch.object(lab.NativeRuntime, "execute", side_effect=stopped):
            result = qualify.first_cycle(self.config_ref, self.auth_ref, self.request, execute=True)
        self.assertEqual(result["failed_stage"], "deploy")
        store = lab.StateStore(self.config, self.contract, self.d2.source)
        before = (self.locks / store.name).read_bytes()
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.run_first(recover=True)
            self.assertEqual((self.locks / store.name).read_bytes(), before)
        self.assertEqual(self.reservations(), 5)

    def test_failed_summary_only_republishes_without_power_cycle(self):
        original = lab.deploy.save
        def failed(directory, name, value):
            if name == "result.json" and Path(directory).name == "first-cycle-fixture-first":
                raise OSError("離線摘要故障")
            return original(directory, name, value)
        with mock.patch.object(lab.deploy, "save", side_effect=failed):
            result, _ = self.run_first()
        self.assertEqual(result["failed_stage"], "publication")
        self.assertEqual(self.reservations(), 5)
        recovered, execute = self.run_first(recover=True)
        self.assertEqual(recovered["status"], "recovered")
        execute.assert_not_called()

    def test_approve_rechecks_nested_collection_and_contract(self):
        result, _ = self.run_first()
        candidate = self.write("candidate.json", result)
        review = self.review(candidate)
        smoke = lab.deploy.load(result["operations"][3])
        path = Path(smoke["session"]["ssh_observation"]["path"])
        path.write_bytes(b"{}")
        with self.assertRaises(ValueError):
            qualify.approve(candidate, review, self.root / "qualification.json")

    def test_approve_requires_d2_original_publication_completion(self):
        result, _ = self.run_first()
        self.assertEqual(result["status"], "review-required", result)
        candidate = self.write("candidate.json", result)
        review = self.review(candidate)
        operation = lab.deploy.load(result["operations"][1])
        receipt = lab.deploy.load(operation["media_receipt"])
        complete = Path(self.contract["isolation_dir"]) / lab.external._completion_name(receipt)
        complete.unlink()
        with mock.patch.object(lab.external, "validate_result", wraps=lab.external.validate_result) as validator:
            with self.assertRaises((ValueError, OSError)):
                qualify.approve(candidate, review, self.root / "qualification.json")
        self.assertGreater(validator.call_count, 0)
        self.assertFalse((self.root / "qualification.json").exists())

    def test_approve_and_batch_replay_reject_new_media_pending(self):
        result, _ = self.run_first()
        self.assertEqual(result["status"], "review-required", result)
        candidate = self.write("candidate.json", result)
        review = self.review(candidate)
        qualify.approve(candidate, review, self.root / "qualification.json")
        fixed = {**self.config, "qualification": self.ref(self.root / "qualification.json")}
        qualify.check_qualification(fixed)
        pending = Path(self.contract["isolation_dir"]) / (lab.media.media_identity(self.contract["target"]).split(":", 1)[1]
                                                         + ".pending.json")
        pending.write_bytes(lab.deploy.encode({"reason": "合成後續 writer 未確認停止"}))
        before = pending.read_bytes()
        with self.assertRaises(ValueError):
            qualify.approve(candidate, review, self.root / "forbidden-qualification.json")
        with self.assertRaises(ValueError):
            qualify.check_qualification(fixed)
        self.assertEqual(pending.read_bytes(), before)
        self.assertFalse((self.root / "forbidden-qualification.json").exists())

    def test_approve_rejects_rebound_action_bootid_and_shallow_checks(self):
        result, _ = self.run_first()
        original = [lab.deploy.load(item) for item in result["operations"]]
        for index, mutate in ((2, lambda row: row.update(action="recovery")),
                              (3, lambda row: row.update(validation={"checks": [{"check": "root", "status": "passed"}]})),
                              (2, lambda row: row["session"].update(boot_id=original[1]["session"]["boot_id"]))):
            operation = copy.deepcopy(original[index])
            mutate(operation)
            changed = copy.deepcopy(result)
            changed["operations"][index] = self.write("rebound-operation.json", operation)
            candidate = self.write("rebound-candidate.json", changed)
            with self.assertRaises(ValueError):
                qualify.approve(candidate, self.review(candidate), self.root / "qualification.json")

    def test_queue_lock_and_reservations_span_all_operations(self):
        def checked(context, *args):
            self.assertEqual(self.reservations(), 5)
            isolation = qualify.common.isolation_station(self.config, self.request)
            with self.assertRaises(ValueError):
                with qualify.queue.station_locks(isolation, self.queue_locks):
                    self.fail("不可重入父 queue 鎖")
            return self.fake_result(context, *args)
        with mock.patch.object(lab.NativeRuntime, "execute", side_effect=checked):
            result = qualify.first_cycle(self.config_ref, self.auth_ref, self.request, execute=True)
        self.assertEqual(result["status"], "review-required", result)

    def test_fork_exit_before_pending_removal_keeps_five_reservations(self):
        original = qualify.common.remove_publication_file
        pid = os.fork()
        if pid == 0:
            def stopped(output, name):
                if name == "publication.pending":
                    os._exit(88)
                return original(output, name)
            try:
                with mock.patch.object(qualify.common, "remove_publication_file", side_effect=stopped):
                    self.run_first()
            except BaseException:
                os._exit(89)
            os._exit(90)
        finished = False
        try:
            end = time.monotonic() + 30
            while time.monotonic() < end:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    finished = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 88)
                    break
                time.sleep(0.01)
            self.assertTrue(finished)
        finally:
            if not finished:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        self.assertEqual(self.reservations(), 5)
        output = self.root / "evidence/first-cycle-fixture-first"
        self.assertTrue((output / "publication.pending").exists())
        candidate = self.ref(output / "result.json")
        with self.assertRaises(ValueError):
            qualify.approve(candidate, self.review(candidate), self.root / "qualification.json")
        recovered, execute = self.run_first(recover=True)
        self.assertEqual(recovered["status"], "recovered", recovered)
        execute.assert_not_called()
        self.assertEqual(self.reservations(), 0)


if __name__ == "__main__":
    unittest.main()
