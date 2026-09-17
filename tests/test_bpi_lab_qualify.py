"""首次循環授權、隔離及人工核定回歸；不接觸任何實板。"""

import copy
import hashlib
import os
from pathlib import Path
import signal
import sys
import time
from unittest import mock
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_qualify as qualify
import test_bpi_lab_backend as data
import test_bpi_lab_linux as linux_data
import test_bpi_lab_session as session_data


class QualifyTests(data.BackendFixture):
    def setUp(self):
        super().setUp()
        self.queue_locks = self.root / "queue-locks"
        mock.patch.object(qualify.queue, "LOCK_ROOT", self.queue_locks).start()
        self.boot_sequence = 10
        self.config_document.pop("qualification")
        self.lifecycle["authorization"]["fault_poweroff"] = True
        self.config_document["lifecycle"] = self.write_json("lifecycle.json", self.lifecycle)
        self.config_ref = self.write_json("first-backend.json", self.config_document)
        self.auth = {"schema": "bpi-lab-first-cycle-authorization-v1", "approved": True,
                     "record": "fixture-first-cycle", "scope_sha256": qualify.backend.scope_digest(self.config_document),
                     "image_sha256": self.request["image_sha256"], "stages": list(qualify.station.STAGES),
                     "allow_failure_recovery": True,
                     "driver_sha256": hashlib.sha256(Path(qualify.__file__).read_bytes()).hexdigest(),
                     "queue_sha256": hashlib.sha256(Path(qualify.queue.__file__).read_bytes()).hexdigest()}
        self.reset_auth()

    def fake_result(self, context, current, output, timeout):
        result = super().fake_result(context, current, output, timeout)
        stage = context[-1]["stage"]
        self.boot_sequence += 1
        boot_id = str(uuid.UUID(int=self.boot_sequence)) if stage in ("boot", "recovery") else (
            current["boot_id"] if current else "11111111-2222-3333-4444-555555555555")
        identity = session_data.SessionTests.identity(self, result["session"]["mode"])
        identity["boot_id"] = boot_id
        hosts = self.root / f"session-hosts-{self.boot_sequence}"
        hosts.write_bytes((self.ssh["host"] + " " + identity["host_key"] + "\n").encode("ascii"))
        result["session"].update(boot_id=boot_id, identity=identity,
                                 ssh={**self.ssh, "known_hosts": self.reference(hosts)})
        if stage == "preflight":
            result.update(self.preflight_proof())
        elif stage in ("boot", "recovery"):
            result.update(schema="bpi-lab-lifecycle-result-v1", action=stage, simulated=False)
            if stage == "recovery":
                result["rescue_proof"] = self.preflight_proof()
        if stage in ("boot", "smoke"):
            collection = self.collection(identity)
            path = output / ("cycle/linux/collection.json" if stage == "boot" else "linux/collection.json")
            path.parent.mkdir(parents=True)
            path.write_bytes(qualify.deploy.encode(collection))
            validation = qualify.backend.linux.validate(collection, self.expected)
            self.assertTrue(validation["ok"], validation)
            if stage == "boot":
                result["linux"] = validation
            else:
                result = {**validation, "session": result["session"]}
            result["linux_collection"] = self.reference(path)
        return result

    def preflight_proof(self):
        proof = {"expected": self.contract["expected"],
                 "source": {key: self.source_record[key] for key in ("raw", "compressed")},
                 "protected_sd": data.data.fixtures.PROTECTED,
                 "rescue_schema": data.data.SCHEMA, "rescue_expected": data.data.RESCUE,
                 "backup_manifest_sha256": self.contract["backup"]["sha256"]}
        return {"schema": "bpi-lab-deploy-preflight-v1", "status": "verified", "request": proof,
                "state": data.data.state(proof)}

    def collection(self, identity):
        collection = linux_data.fixture()
        hosts = (self.ssh["host"] + " " + identity["host_key"] + "\n").encode("ascii")
        collection.update(alias="bpi-lab", known_hosts={"bytes": len(hosts), "sha256": hashlib.sha256(hosts).hexdigest()})
        obs, root = collection["observation"], identity["root"]
        obs["uname"]["value"].update(machine=identity["machine"], release=identity["kernel"])
        obs["dt_compatible"]["value"] = identity["dt_compatible"]
        obs["mounts"]["value"][0].update(major_minor=root["devnum"], fs_type=root["fs"])
        obs["root"]["value"].update(major_minor=root["devnum"], stat_major_minor=root["devnum"],
                                  sysfs_major_minor=root["devnum"], uuid_major_minor=root["devnum"],
                                  sysfs_path=root["sysfs"], uuid_sysfs_path=root["sysfs"],
                                  parent_path=root["parent"], parent_major_minor=root["parent_devnum"],
                                  uuid=root["uuid"], bytes=512)
        obs["media"]["value"] = [
            {"name": item["name"], "sysfs_path": item["sysfs"], "device_path": str(Path(item["sysfs"]).parent.parent),
             "major_minor": item["devnum"], "cid": item["cid"], "type": item["type"], "controller": item["controller"],
             "bytes": item["bytes"], "sectors": item["bytes"] // 512, "slaves": [], "is_partition": False}
            for item in identity["media"]]
        obs["root_after"] = copy.deepcopy(obs["root"])
        obs["media_after"] = copy.deepcopy(obs["media"])
        return collection

    def reset_auth(self):
        self.auth_ref = self.write_json("first-auth.json", self.auth)
        self.request.update(boot_config_sha256=self.config_ref["sha256"], attempt_id=self.auth["record"],
                            work_key=hashlib.sha256(qualify.deploy.encode({"scope": self.auth["scope_sha256"],
                                            "authorization": self.auth_ref["sha256"]})).hexdigest())

    def cycle(self, **kwargs):
        return qualify.first_cycle(self.config_ref, self.auth_ref, self.request, **kwargs)

    def native_fixture(self, **kwargs):
        # 只在測試內替換硬體入口，生產 CLI 沒有此途徑。
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=self.fake_result) as execute:
            result = self.cycle(execute=True, **kwargs)
        return result, execute

    def assert_shared_validation(self, check, result):
        self.assertEqual([call.args[0] for call in check.call_args_list], list(qualify.station.STAGES))
        for index, call in enumerate(check.call_args_list):
            self.assertEqual(call.kwargs["config"], self.config_document)
            current = call.kwargs["current"]
            if index == 0:
                self.assertIsNone(current)
            else:
                prior = result["operations"][index - 1]
                observed = qualify.deploy.load(prior)["session"]
                self.assertEqual(current["operation"], prior)
                self.assertEqual((current["boot_id"], current["phase"]), (observed["boot_id"], observed["mode"]))

    def test_capture_collection_preserves_native_reference(self):
        for stage, relative in (("boot", "cycle/linux/collection.json"), ("smoke", "linux/collection.json")):
            output = self.root / stage
            ref = self.write_json(f"{stage}/{relative}", {})
            result = {"linux_collection": ref}
            with self.subTest(stage=stage):
                self.assertIs(qualify.capture_collection(stage, result, output), result)
                self.assertEqual(result["linux_collection"], ref)

    def test_capture_collection_adds_only_missing_reference(self):
        for stage, relative in (("boot", "cycle/linux/collection.json"), ("smoke", "linux/collection.json")):
            output = self.root / stage
            ref = self.write_json(f"{stage}/{relative}", {})
            with self.subTest(stage=stage):
                self.assertEqual(qualify.capture_collection(stage, {}, output), {"linux_collection": ref})

    def test_capture_collection_rejects_native_reference_mismatch(self):
        for stage, relative in (("boot", "cycle/linux/collection.json"), ("smoke", "linux/collection.json")):
            output = self.root / stage
            ref = self.write_json(f"{stage}/{relative}", {})
            other = self.write_json(f"{stage}/other.json", {})
            for wrong in ({**ref, "sha256": "0" * 64}, other, None, {}):
                result = {"linux_collection": wrong}
                with self.subTest(stage=stage, reference=wrong), self.assertRaisesRegex(ValueError, "採樣參照"):
                    qualify.capture_collection(stage, result, output)
                self.assertEqual(result["linux_collection"], wrong)

    def test_shared_evidence_dependency_is_required_and_pinned(self):
        original = copy.deepcopy(self.config_document["dependencies"])
        name = "bpi_lab_evidence.py"
        self.assertIn(name, qualify.backend.DEPENDENCIES)
        for digest in (None, "0" * 64):
            self.config_document["dependencies"] = copy.deepcopy(original)
            if digest is None:
                self.config_document["dependencies"].pop(name)
            else:
                self.config_document["dependencies"][name] = digest
            self.config_ref = self.write_json("first-backend.json", self.config_document)
            self.auth["scope_sha256"] = qualify.backend.scope_digest(self.config_document)
            self.reset_auth()
            with self.subTest(digest=digest), mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
                with self.assertRaises(ValueError):
                    self.cycle(execute=True)
                execute.assert_not_called()
        self.assertFalse(self.queue_locks.exists())
        self.assertFalse(self.locks.exists())

    def test_check_has_no_hardware_or_state_writes(self):
        with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
            result = self.cycle()
        self.assertEqual(result["status"], "checked")
        execute.assert_not_called()
        self.assertFalse(self.locks.exists())
        self.assertFalse(self.queue_locks.exists())

    def test_queue_still_requires_full_qualification(self):
        with self.assertRaises(ValueError):
            qualify.backend.load_config(self.config_ref)
        self.assertEqual(self.cycle()["status"], "checked")

    def test_wrong_first_authorization_rejected(self):
        original = dict(self.auth)
        for key, value in (("approved", False), ("scope_sha256", "a" * 64),
                           ("image_sha256", "b" * 64), ("stages", ["deploy"]),
                           ("driver_sha256", "0" * 64), ("queue_sha256", "0" * 64), ("allow_failure_recovery", 1)):
            self.auth = {**original, key: value}
            self.reset_auth()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.cycle()
        self.assertFalse(self.locks.exists())
        self.assertFalse(self.queue_locks.exists())

    def test_request_binding_not_borrowable(self):
        for key in ("hardware_id", "station_id", "test_version", "attempt_id", "work_key"):
            previous = self.request[key]
            self.request[key] = "other"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.cycle()
            self.request[key] = previous

    def test_injected_runtime_never_grants_qualification(self):
        fake = mock.Mock()
        fake.execute.side_effect = self.fake_result
        result = self.cycle(execute=True, runtime=fake)
        self.assertEqual(result["status"], "test-only")
        self.assertEqual(fake.execute.call_count, 5)
        self.assertFalse(result["hardware_validated"])
        self.assertFalse(result["qualification_ready_for_review"])
        self.assertFalse(self.locks.exists())
        self.assertFalse(self.queue_locks.exists())
        candidate = self.write_json("candidate.json", result)
        review = self.review(candidate)
        with self.assertRaises(ValueError):
            qualify.approve(candidate, review, self.root / "approved.json")

    def test_complete_cycle_requires_review_and_no_repeat(self):
        with mock.patch.object(qualify.backend, "validate_result", wraps=qualify.backend.validate_result) as check:
            result, execute = self.native_fixture()
        self.assertEqual(result["status"], "review-required")
        self.assert_shared_validation(check, result)
        self.assertEqual(execute.call_count, 5)
        self.assertFalse(result["whole_backend_ready"])
        self.assertEqual(len(result["reports"]), 5)
        with self.assertRaisesRegex(ValueError, "不得重刷"):
            self.native_fixture()

    def review(self, candidate):
        return self.write_json("review.json", {"schema": "bpi-lab-first-cycle-review-v1", "approved": True,
                                "record": "fixture-review", "candidate": candidate,
                                "scope_sha256": self.auth["scope_sha256"]})

    def test_explicit_review_revalidates_five_stage_evidence(self):
        result, _ = self.native_fixture()
        candidate = self.write_json("candidate.json", result)
        with mock.patch.object(qualify.backend, "validate_result", wraps=qualify.backend.validate_result) as check:
            approved = qualify.approve(candidate, self.review(candidate), self.root / "approved.json")
        self.assert_shared_validation(check, result)
        self.assertEqual(approved["approved_stages"], list(qualify.station.STAGES))
        self.assertEqual(len(approved["source_evidence"]), 20)
        config = {**self.config_document, "qualification": self.reference(self.root / "approved.json")}
        qualify.backend.check_qualification(config)

    def test_changed_operation_blocks_review(self):
        result, _ = self.native_fixture()
        Path(result["operations"][2]["path"]).write_bytes(b"{}")
        candidate = self.write_json("candidate.json", result)
        with self.assertRaises(ValueError):
            qualify.approve(candidate, self.review(candidate), self.root / "approved.json")

    def test_changed_configuration_blocks_review(self):
        result, _ = self.native_fixture()
        Path(self.config_ref["path"]).write_bytes(b"{}")
        candidate = self.write_json("candidate.json", result)
        with self.assertRaises(ValueError):
            qualify.approve(candidate, self.review(candidate), self.root / "approved.json")

    def test_failed_stage_quarantines_and_explicit_recovery_only(self):
        def fail(context, current, output, timeout):
            if context[-1]["stage"] == "boot":
                raise TimeoutError("離線模擬開機逾時")
            return self.fake_result(context, current, output, timeout)
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=fail):
            result = self.cycle(execute=True)
        self.assertEqual(result["failed_stage"], "boot")
        self.assertEqual(qualify.backend.StateStore(self.config_document).read()["status"], "failed")
        with self.assertRaises(ValueError):
            self.native_fixture()
        recovered, execute = self.native_fixture(recover=True)
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(execute.call_count, 1)
        self.assertFalse(recovered["qualification_ready_for_review"])

    def test_recovery_without_failed_state_rejected(self):
        with self.assertRaises(ValueError):
            self.native_fixture(recover=True)

    def test_deploy_interruption_cannot_poweroff_unresolved_writer(self):
        def fail(context, current, output, timeout):
            if context[-1]["stage"] == "deploy":
                raise TimeoutError("離線模擬傳輸中斷")
            return self.fake_result(context, current, output, timeout)
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=fail):
            result = self.cycle(execute=True)
        self.assertEqual(result["failed_stage"], "deploy")
        for _ in range(2):
            with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "writer 已停止"):
                    self.cycle(execute=True, recover=True)
            execute.assert_not_called()
        state = qualify.backend.StateStore(self.config_document).read()
        self.assertTrue(state["writer_unresolved"])
        self.assertEqual(state["stage"], "deploy")

    def test_recovery_requires_explicit_authorization(self):
        self.auth["allow_failure_recovery"] = False
        self.reset_auth()
        with self.assertRaises(ValueError):
            self.native_fixture(recover=True)

    def test_interrupt_is_persisted_then_propagated(self):
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.cycle(execute=True)
        self.assertEqual(qualify.backend.StateStore(self.config_document).read()["status"], "failed")

    def reservation_rows(self):
        with qualify.queue.reservation_store(self.queue_locks) as db:
            return db.execute("SELECT resource,owner,work_key FROM reservations ORDER BY resource").fetchall()

    def queue_claim(self, station=None):
        isolation = station or qualify.isolation_station(self.config_document, self.request)
        qualify.queue.safe_directory(self.queue_locks, create=True)
        db = qualify.queue.connect(self.root / "foreign-queue.sqlite3")
        try:
            qualify.queue.reserve_resources(db, isolation, "f" * 64, self.queue_locks)
        finally:
            db.close()

    def test_queue_flock_blocks_first_cycle(self):
        isolation = qualify.isolation_station(self.config_document, self.request)
        with qualify.queue.station_locks(isolation, self.queue_locks):
            with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "占用"):
                    self.cycle(execute=True)
                execute.assert_not_called()

    def test_foreign_queue_reservation_blocks_first_cycle(self):
        self.queue_claim()
        previous = self.reservation_rows()
        with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "其他工作持久占用"):
                self.cycle(execute=True)
            execute.assert_not_called()
        self.assertEqual(self.reservation_rows(), previous)

    def test_shared_uart_reservation_blocks_different_station(self):
        isolation = qualify.isolation_station(self.config_document, self.request)
        isolation.update(station_id="other-station", hardware_id="other-board")
        isolation["resources"] = {**isolation["resources"], "power": "power:other", "media": "cid:" + "9" * 32}
        self.queue_claim(isolation)
        with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "其他工作持久占用"):
                self.cycle(execute=True)
            execute.assert_not_called()

    def test_first_cycle_holds_queue_lock_and_reservation_until_finished(self):
        isolation = qualify.isolation_station(self.config_document, self.request)
        def observed(context, current, output, timeout):
            with self.assertRaisesRegex(ValueError, "占用"):
                with qualify.queue.station_locks(isolation, self.queue_locks):
                    self.fail("首次核定期間不應取得相同資源鎖")
            with self.assertRaisesRegex(ValueError, "持久占用"):
                self.queue_claim()
            self.assertEqual(len(self.reservation_rows()), 5)
            return self.fake_result(context, current, output, timeout)
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=observed):
            self.assertEqual(self.cycle(execute=True)["status"], "review-required")
        self.assertEqual(self.reservation_rows(), [])

    def test_failure_reserves_resources_until_same_work_recovery(self):
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=TimeoutError):
            self.assertEqual(self.cycle(execute=True)["status"], "failed")
        previous = self.reservation_rows()
        self.assertEqual(len(previous), 5)
        with self.assertRaisesRegex(ValueError, "持久占用"):
            self.queue_claim()
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=TimeoutError):
            self.assertEqual(self.cycle(execute=True, recover=True)["status"], "failed")
        self.assertEqual(self.reservation_rows(), previous)
        self.assertEqual(self.native_fixture(recover=True)[0]["status"], "recovered")
        self.assertEqual(self.reservation_rows(), [])
        self.queue_claim()

    def test_missing_queue_pin_rejected(self):
        self.auth.pop("queue_sha256")
        self.reset_auth()
        with self.assertRaises(ValueError):
            self.cycle()

    def test_native_preflight_receipt_matches_validator(self):
        receipt = qualify.deploy.preflight(self.contract, self.source_record, self.root / "native-preflight",
                                           transport=self.readonly)
        context = (self.config_document, self.contract, self.bundle, self.boot, self.expected, self.request)
        receipt["session"] = self.fake_result(context, None, None, 60)["session"]
        qualify.validate_operation("preflight", receipt, self.request, self.contract, self.bundle, self.config_document)

    def test_partial_reservation_does_not_allow_recovery(self):
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=TimeoutError):
            self.cycle(execute=True)
        resource = self.reservation_rows()[0][0]
        with qualify.queue.reservation_store(self.queue_locks) as db:
            db.execute("DELETE FROM reservations WHERE resource=?", (resource,))
        with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "占用不完整"):
                self.cycle(execute=True, recover=True)
            execute.assert_not_called()

    def gap(self, stage, error=OSError):
        original = qualify.deploy.new_directory
        def fail(path):
            if Path(path).name == stage and Path(path).parent.name.startswith("first-cycle-"):
                raise error("離線模擬階段交界中斷")
            return original(path)
        with mock.patch.object(qualify.deploy, "new_directory", side_effect=fail):
            result, execute = self.native_fixture()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], stage)
        self.assertEqual(execute.call_count, qualify.station.STAGES.index(stage))
        self.assertEqual(len(self.reservation_rows()), 5)
        with self.assertRaises(ValueError):
            self.native_fixture()
        recovered, execute = self.native_fixture(recover=True)
        self.assertEqual(recovered["status"], "recovered")
        self.assertFalse(recovered["qualification_ready_for_review"])
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0][-1]["stage"], "recovery")
        self.assertEqual(self.reservation_rows(), [])

    def test_preflight_directory_failure_recovery(self):
        self.gap("preflight")

    def test_gap_after_preflight_recovery(self):
        self.gap("deploy")

    def test_gap_after_verified_deploy_recovery(self):
        self.gap("boot")

    def test_gap_after_boot_recovery(self):
        self.gap("smoke")

    def test_gap_after_smoke_recovery(self):
        self.gap("recovery")

    def test_state_write_failure_preserves_completed_deploy(self):
        original = qualify.backend.StateStore.write
        def fail(store, value):
            if value["stage"] == "boot" and value["status"] == "running":
                raise OSError("離線模擬意圖寫入失敗")
            return original(store, value)
        with mock.patch.object(qualify.backend.StateStore, "write", new=fail):
            result, _ = self.native_fixture()
        self.assertEqual(result["failed_stage"], "boot")
        state = qualify.backend.StateStore(self.config_document).read()
        self.assertEqual((state["status"], state["stage"]), ("verified", "deploy"))
        self.assertEqual(self.native_fixture(recover=True)[0]["status"], "recovered")

    def test_stage_request_write_failure_can_recover(self):
        original = qualify.deploy.save
        def fail(output, name, blob):
            if Path(output).name == "recovery" and name == "request.json":
                raise OSError("離線模擬階段請求寫入失敗")
            return original(output, name, blob)
        with mock.patch.object(qualify.deploy, "save", side_effect=fail):
            self.assertEqual(self.native_fixture()[0]["failed_stage"], "recovery")
        recovered, _ = self.native_fixture(recover=True)
        self.assertEqual(recovered["status"], "recovered", recovered)

    def test_interrupted_gap_can_recover_verified_prefix(self):
        original = qualify.deploy.new_directory
        def fail(path):
            if Path(path).name == "recovery":
                raise KeyboardInterrupt
            return original(path)
        with mock.patch.object(qualify.deploy, "new_directory", side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):
                self.native_fixture()
        state = qualify.backend.StateStore(self.config_document).read()
        self.assertEqual((state["status"], state["stage"]), ("verified", "smoke"))
        self.assertEqual(self.native_fixture(recover=True)[0]["status"], "recovered")

    def test_initial_output_failure_retains_recoverable_reservation(self):
        original = qualify.deploy.new_directory
        def fail(path):
            if Path(path).name == "first-cycle-" + self.auth["record"]:
                raise OSError("離線模擬首輪目錄失敗")
            return original(path)
        with mock.patch.object(qualify.deploy, "new_directory", side_effect=fail):
            with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
                with self.assertRaises(OSError):
                    self.cycle(execute=True)
                execute.assert_not_called()
        self.assertIsNone(qualify.backend.StateStore(self.config_document).read())
        self.assertEqual(len(self.reservation_rows()), 5)
        self.assertEqual(self.native_fixture(recover=True)[0]["status"], "recovered")

    def test_pending_without_state_is_not_a_fresh_cycle(self):
        with qualify.backend.resource_lock(self.config_document):
            store = qualify.backend.StateStore(self.config_document)
            qualify.deploy.save(self.locks, store.pending, qualify.deploy.encode({"stage": "deploy"}))
        with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
            for recover in (False, True):
                with self.assertRaisesRegex(ValueError, "仍有 pending"):
                    self.cycle(execute=True, recover=recover)
            execute.assert_not_called()

    def test_recovery_revalidates_completed_deploy_receipt(self):
        original = qualify.deploy.new_directory
        def fail(path):
            if Path(path).name == "boot":
                raise OSError("離線模擬部署後交界失敗")
            return original(path)
        with mock.patch.object(qualify.deploy, "new_directory", side_effect=fail):
            self.native_fixture()
        state = qualify.backend.StateStore(self.config_document).read()
        Path(state["operation"]["path"]).write_bytes(b"{}")
        with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
            with self.assertRaises(ValueError):
                self.cycle(execute=True, recover=True)
            execute.assert_not_called()
        self.assertEqual(len(self.reservation_rows()), 5)

    def pending_failure(self, stage):
        original = qualify.backend.StateStore.publish
        def fail(store, value, output, report):
            if value["stage"] == stage:
                qualify.deploy.save(qualify.backend.LOCK_ROOT, store.pending, qualify.deploy.encode(value))
                raise OSError("離線模擬發布中斷")
            return original(store, value, output, report)
        with mock.patch.object(qualify.backend.StateStore, "publish", new=fail):
            result, _ = self.native_fixture()
        self.assertEqual(result["failed_stage"], stage)

    def test_pending_boot_can_recover_without_redeploy(self):
        self.pending_failure("boot")
        result, execute = self.native_fixture(recover=True)
        self.assertEqual(result["status"], "recovered")
        execute.assert_called_once()
        store = qualify.backend.StateStore(self.config_document)
        self.assertFalse((self.locks / store.pending).exists())
        self.assertEqual(self.reservation_rows(), [])

    def test_pending_deploy_never_clears_writer(self):
        self.pending_failure("deploy")
        before = self.reservation_rows()
        for _ in range(2):
            with mock.patch.object(qualify.backend.NativeRuntime, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "writer 已停止"):
                    self.cycle(execute=True, recover=True)
                execute.assert_not_called()
        self.assertTrue(qualify.backend.StateStore(self.config_document).read()["writer_unresolved"])
        self.assertEqual(self.reservation_rows(), before)

    def test_completed_recovery_only_republishes_after_summary_failure(self):
        original = qualify.deploy.save
        def fail(output, name, blob):
            if name == "result.json":
                raise OSError("離線模擬摘要發布失敗")
            return original(output, name, blob)
        with mock.patch.object(qualify.deploy, "save", side_effect=fail):
            self.assertEqual(self.native_fixture()[0]["failed_stage"], "publication")
        self.assertEqual(len(self.reservation_rows()), 5)
        result, execute = self.native_fixture(recover=True)
        execute.assert_not_called()
        self.assertEqual(result["status"], "recovered")
        self.assertFalse(result["qualification_ready_for_review"])
        self.assertEqual(self.reservation_rows(), [])
        with self.assertRaises(ValueError):
            self.native_fixture(recover=True)

    def test_release_failure_can_finish_without_new_power_cycle(self):
        with mock.patch.object(qualify, "release_reservation", side_effect=OSError):
            with self.assertRaises(OSError):
                self.native_fixture()
        self.assertEqual(len(self.reservation_rows()), 5)
        result, execute = self.native_fixture(recover=True)
        execute.assert_not_called()
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(self.reservation_rows(), [])

    def test_release_committed_then_exception_does_not_revoke_publication(self):
        original = qualify.release_reservation
        def committed(*args):
            original(*args)
            raise OSError("離線模擬釋放已提交後回傳失敗")
        with mock.patch.object(qualify, "release_reservation", side_effect=committed):
            with self.assertRaises(OSError):
                self.native_fixture()
        self.assertEqual(self.reservation_rows(), [])
        output = Path(self.config_document["output_root"]) / ("first-cycle-" + self.auth["record"])
        candidate = self.reference(output / "result.json")
        result = qualify.deploy.load(candidate)
        qualify.validate_publication(result, self.config_document, self.auth)
        self.assertFalse((output / "publication-failed.json").exists())
        self.assertTrue(qualify.approve(candidate, self.review(candidate), self.root / "approved.json")["hardware_validated"])

    def fork_publication_exit(self, point):
        original_remove, original_release = qualify.remove_publication_file, qualify.release_reservation
        def interrupted_remove(output, name):
            if name == "publication.pending" and point == "before-pending":
                os._exit(88)
            return original_remove(output, name)
        def interrupted_release(*args):
            if point == "before-release":
                os._exit(88)
            original_release(*args)
            if point == "after-release":
                os._exit(88)
        pid = os.fork()
        if pid == 0:
            try:
                with mock.patch.object(qualify, "remove_publication_file", side_effect=interrupted_remove):
                    with mock.patch.object(qualify, "release_reservation", side_effect=interrupted_release):
                        self.native_fixture()
            except BaseException:
                os._exit(89)
            os._exit(90)
        finished = False
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    finished = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 88)
                    break
                time.sleep(0.01)
            self.assertTrue(finished, "離線退出回歸逾時")
        finally:
            if not finished:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        current = qualify.backend.StateStore(self.config_document).read()
        self.assertEqual((current["status"], current["stage"], current["phase"]), ("verified", "recovery", "rescue"))
        self.assertFalse(current["writer_unresolved"])
        output = Path(self.config_document["output_root"]) / ("first-cycle-" + self.auth["record"])
        candidate = self.reference(output / "result.json")
        self.assertEqual(len(qualify.deploy.load(candidate)["operations"]), 5)
        return output, candidate

    def assert_new_authorization_blocked(self):
        # 保留原授權檔，讓同一工作仍可復原；新授權只改記錄及工作鍵。
        auth = {**self.auth, "record": "fixture-second-cycle"}
        auth_ref = self.write_json("second-auth.json", auth)
        request = {**self.request, "attempt_id": auth["record"],
                   "work_key": hashlib.sha256(qualify.deploy.encode({"scope": auth["scope_sha256"],
                                                        "authorization": auth_ref["sha256"]})).hexdigest()}
        self.assertEqual(qualify.first_cycle(self.config_ref, auth_ref, request)["status"], "checked")
        before = self.reservation_rows()
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=self.fake_result) as execute:
            with self.assertRaisesRegex(ValueError, "其他工作持久占用"):
                qualify.first_cycle(self.config_ref, auth_ref, request, execute=True)
            execute.assert_not_called()
        self.assertEqual(self.reservation_rows(), before)

    def test_fork_exit_before_pending_removal_keeps_quarantine(self):
        output, candidate = self.fork_publication_exit("before-pending")
        self.assertTrue((output / "publication.pending").exists())
        self.assertEqual(len(self.reservation_rows()), 5)
        self.assert_new_authorization_blocked()
        review = self.review(candidate)
        with self.assertRaisesRegex(ValueError, "首輪發布仍未完成"):
            qualify.approve(candidate, review, self.root / "approved.json")
        recovered, execute = self.native_fixture(recover=True)
        execute.assert_not_called()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(self.reservation_rows(), [])
        with self.assertRaisesRegex(ValueError, "首輪發布仍未完成"):
            qualify.approve(candidate, review, self.root / "approved.json")

    def test_fork_exit_before_release_keeps_completed_work_isolated(self):
        output, candidate = self.fork_publication_exit("before-release")
        self.assertFalse((output / "publication.pending").exists())
        qualify.validate_publication(qualify.deploy.load(candidate), self.config_document, self.auth)
        self.assertEqual(len(self.reservation_rows()), 5)
        self.assert_new_authorization_blocked()
        recovered, execute = self.native_fixture(recover=True)
        execute.assert_not_called()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(self.reservation_rows(), [])

    def test_fork_exit_after_release_leaves_only_completed_publication(self):
        output, candidate = self.fork_publication_exit("after-release")
        self.assertFalse((output / "publication.pending").exists())
        self.assertFalse((output / "publication-failed.json").exists())
        self.assertEqual(self.reservation_rows(), [])
        self.assertTrue(qualify.approve(candidate, self.review(candidate), self.root / "approved.json")["hardware_validated"])

    def rebind_operations(self, result, operations):
        result = copy.deepcopy(result)
        result["operations"] = [self.write_json(f"review-operation-{index}.json", operation)
                                for index, operation in enumerate(operations)]
        result["reports"] = []
        previous = None
        for index, (stage, operation) in enumerate(zip(qualify.station.STAGES, operations)):
            request = {**self.request, "stage": stage}
            report = qualify.stage_report(request, operation, result["operations"][index], previous, simulated=False)
            previous = self.write_json(f"review-report-{index}.json", report)
            result["reports"].append(previous)
        return result

    def assert_review_rejected(self, result):
        # 負例連發布摘要也重綁，避免僅因外層摘要失配而掩蓋語意漏洞。
        result = copy.deepcopy(result)
        seal_path = Path(result["publication"]["path"])
        result["publication"] = self.write_json(str(seal_path.relative_to(self.root)), qualify.publication_record(result))
        candidate = self.write_json("candidate.json", result)
        with self.assertRaises(ValueError):
            qualify.approve(candidate, self.review(candidate), self.root / "approved.json")
        self.assertFalse((self.root / "approved.json").exists())

    def test_review_cannot_reuse_recovery_as_preflight(self):
        result, _ = self.native_fixture()
        Path(result["operations"][0]["path"]).unlink()
        result["operations"][0] = result["operations"][4]
        self.assert_review_rejected(result)

    def test_review_checks_native_schema_even_with_rebound_reports(self):
        result, _ = self.native_fixture()
        operations = [qualify.deploy.load(item) for item in result["operations"]]
        operations[0] = copy.deepcopy(operations[4])
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_missing_or_wrong_native_schema_and_action(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(item) for item in result["operations"]]
        for index, field, value in ((0, "schema", None), (1, "schema", "unknown"),
                                    (2, "action", "recovery"), (4, "action", "boot"),
                                    (3, "schema", "unknown"), (0, "action", "recovery")):
            operations = copy.deepcopy(original)
            operations[index][field] = value
            with self.subTest(index=index, field=field):
                self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_preflight_other_source_or_media_or_backup(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(item) for item in result["operations"]]
        for field, value in (("expected", {}), ("source", {}), ("protected_sd", {}),
                             ("backup_manifest_sha256", "a" * 64)):
            operations = copy.deepcopy(original)
            operations[0]["request"][field] = value
            with self.subTest(field=field):
                self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_identical_or_discontinuous_boot_ids(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(item) for item in result["operations"]]
        initial = original[0]["session"]["boot_id"]
        for index, boot_id in ((1, str(uuid.UUID(int=99))), (2, initial), (3, initial),
                               (4, initial), (4, original[3]["session"]["boot_id"]), (0, "invalid")):
            operations = copy.deepcopy(original)
            operations[index]["session"]["boot_id"] = boot_id
            operations[index]["session"]["identity"]["boot_id"] = boot_id
            with self.subTest(index=index, boot_id=boot_id):
                self.assert_review_rejected(self.rebind_operations(result, operations))
        operations = copy.deepcopy(original)
        for operation in operations:
            operation["session"]["boot_id"] = initial
            operation["session"]["identity"]["boot_id"] = initial
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_uart_boot_id_mismatch(self):
        result, _ = self.native_fixture()
        operations = [qualify.deploy.load(item) for item in result["operations"]]
        operations[2]["session"]["identity"]["boot_id"] = operations[0]["session"]["boot_id"]
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_unbound_operation_or_previous_report(self):
        result, _ = self.native_fixture()
        for field in ("operation", "previous_report"):
            changed = copy.deepcopy(result)
            report = qualify.deploy.load(changed["reports"][1])
            report[field] = None
            changed["reports"][1] = self.write_json("wrong-report.json", report)
            with self.subTest(field=field):
                self.assert_review_rejected(changed)

    def test_review_rejects_smoke_report_disagreement(self):
        result, _ = self.native_fixture()
        report = qualify.deploy.load(result["reports"][3])
        report["checks"] = {"unobserved": True}
        result["reports"][3] = self.write_json("wrong-smoke.json", report)
        self.assert_review_rejected(result)

    def test_review_rejects_duplicate_smoke_checks(self):
        result, _ = self.native_fixture()
        operations = [qualify.deploy.load(item) for item in result["operations"]]
        operations[3]["checks"] *= 2
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_synthetic_operation_flags(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(item) for item in result["operations"]]
        for flag in ("simulated", "synthetic", "test_only"):
            operations = copy.deepcopy(original)
            operations[0][flag] = True
            with self.subTest(flag=flag):
                self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_runtime_bad_boot_sequence_is_quarantined_before_approval(self):
        def bad(context, current, output, timeout):
            result = self.fake_result(context, current, output, timeout)
            if context[-1]["stage"] == "boot":
                result["session"]["boot_id"] = current["boot_id"]
                result["session"]["identity"]["boot_id"] = current["boot_id"]
            return result
        with mock.patch.object(qualify.backend.NativeRuntime, "execute", side_effect=bad) as execute:
            result = self.cycle(execute=True)
        self.assertEqual(result["failed_stage"], "boot")
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(len(self.reservation_rows()), 5)

    def test_falsey_runtime_never_constructs_native_runtime(self):
        class FalseyRuntime:
            def __bool__(self):
                return False
        runtime = FalseyRuntime()
        runtime.execute = mock.Mock(side_effect=self.fake_result)
        with mock.patch.object(qualify.backend, "NativeRuntime", side_effect=AssertionError("替身不得建立硬體入口")) as native:
            result = self.cycle(execute=True, runtime=runtime)
        native.assert_not_called()
        self.assertEqual(runtime.execute.call_count, 5)
        self.assertEqual(result["status"], "test-only")
        self.assertFalse(result["hardware_validated"])
        self.assertFalse(self.queue_locks.exists())
        self.assertFalse(self.locks.exists())

    def test_invalid_falsey_runtime_does_not_fall_back_to_hardware(self):
        with mock.patch.object(qualify.backend, "NativeRuntime", side_effect=AssertionError("不得建立硬體入口")) as native:
            result = self.cycle(execute=True, runtime=0)
        native.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.queue_locks.exists())

    def test_review_requires_every_identity_field(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(ref) for ref in result["operations"]]
        for field in original[2]["session"]["identity"]:
            operations = copy.deepcopy(original)
            operations[2]["session"]["identity"].pop(field)
            with self.subTest(field=field):
                self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_wrong_identity_root_kernel_dt_or_media(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(ref) for ref in result["operations"]]
        for mutate in (lambda value: value.update(kernel="WRONG"),
                       lambda value: value.update(dt_compatible=["other,board"]),
                       lambda value: value.update(machine="unknown"),
                       lambda value: value.update(nonce="invalid"),
                       lambda value: value.update(uid=1),
                       lambda value: value["root"].update(uuid="WRONG"),
                       lambda value: value["media"][0].update(cid="0" * 32),
                       lambda value: value["media"].pop()):
            operations = copy.deepcopy(original)
            mutate(operations[2]["session"]["identity"])
            self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rejects_changed_root_in_same_boot(self):
        result, _ = self.native_fixture()
        operations = [qualify.deploy.load(ref) for ref in result["operations"]]
        operations[1]["session"]["identity"]["root"]["devnum"] = "0:22"
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_requires_ssh_scope_and_uart_hostkey(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(ref) for ref in result["operations"]]
        for field, value in (("host", "192.0.2.88"), ("port", 2222), ("user", "other"),
                             ("known_hosts", self.ssh["known_hosts"])):
            operations = copy.deepcopy(original)
            operations[2]["session"]["ssh"][field] = value
            with self.subTest(field=field):
                self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_requires_every_smoke_check(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(ref) for ref in result["operations"]]
        for index, item in enumerate(original[3]["checks"]):
            operations = copy.deepcopy(original)
            operations[3]["checks"].pop(index)
            with self.subTest(check=item["check"]):
                self.assert_review_rejected(self.rebind_operations(result, operations))
        operations = copy.deepcopy(original)
        operations[3]["checks"] = [{"check": "root_cid", "status": "passed"}]
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_rederives_linux_results_from_raw_collection(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(ref) for ref in result["operations"]]
        for stage in (2, 3):
            operations = copy.deepcopy(original)
            collection = qualify.deploy.load(operations[stage]["linux_collection"])
            collection["observation"]["failed_services"]["value"]["units"] = ["fixture.service"]
            operations[stage]["linux_collection"] = self.write_json("wrong-collection.json", collection)
            with self.subTest(stage=stage):
                self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_binds_collection_hostkey_and_uart_root(self):
        result, _ = self.native_fixture()
        original = [qualify.deploy.load(ref) for ref in result["operations"]]
        for mutate in (lambda item: item.update(known_hosts={"bytes": 1, "sha256": "a" * 64}),
                       lambda item: item.update(alias="other"),
                       lambda item: item["observation"]["root"]["value"].update(uuid="WRONG")):
            operations = copy.deepcopy(original)
            collection = qualify.deploy.load(operations[3]["linux_collection"])
            mutate(collection)
            operations[3]["linux_collection"] = self.write_json("wrong-collection.json", collection)
            self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_review_requires_rescue_preflight_body(self):
        result, _ = self.native_fixture()
        operations = [qualify.deploy.load(ref) for ref in result["operations"]]
        operations[4].pop("rescue_proof")
        self.assert_review_rejected(self.rebind_operations(result, operations))

    def test_qualification_rechecks_raw_collection_and_hostkey_files(self):
        result, _ = self.native_fixture()
        candidate = self.write_json("candidate.json", result)
        approved_path = self.root / "approved.json"
        qualify.approve(candidate, self.review(candidate), approved_path)
        config = {**self.config_document, "qualification": self.reference(approved_path)}
        smoke = qualify.deploy.load(result["operations"][3])
        for ref in (smoke["linux_collection"], smoke["session"]["ssh"]["known_hosts"]):
            path = Path(ref["path"])
            before = path.read_bytes()
            path.write_bytes(b"{}")
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                qualify.backend.check_qualification(config)
            path.write_bytes(before)

    def test_last_stage_publication_timeout_keeps_quarantine(self):
        clock, original = [0.0], qualify.backend.StateStore.publish
        def delayed(store, value, output, report):
            original(store, value, output, report)
            if value["stage"] == "recovery":
                clock[0] = 601.0
        with mock.patch.object(qualify.time, "monotonic", side_effect=lambda: clock[0]):
            with mock.patch.object(qualify.backend.StateStore, "publish", new=delayed):
                result, _ = self.native_fixture()
        self.assertEqual((result["status"], result["failed_stage"]), ("failed", "recovery"))
        self.assertFalse(result["qualification_ready_for_review"])
        self.assertEqual(len(self.reservation_rows()), 5)
        recovered, execute = self.native_fixture(recover=True)
        self.assertEqual(recovered["status"], "recovered")
        execute.assert_not_called()

    def publication_timeout(self, point):
        clock = [0.0]
        original_save = qualify.deploy.save
        original_remove = qualify.remove_publication_file
        def delayed_save(output, name, blob):
            original_save(output, name, blob)
            if name == point:
                clock[0] = 601.0
        def delayed_remove(output, name):
            original_remove(output, name)
            if point == "pending" and name == "publication.pending":
                clock[0] = 601.0
        with mock.patch.object(qualify.time, "monotonic", side_effect=lambda: clock[0]):
            with mock.patch.object(qualify.deploy, "save", side_effect=delayed_save):
                with mock.patch.object(qualify, "remove_publication_file", side_effect=delayed_remove):
                    result, _ = self.native_fixture()
        self.assertEqual((result["status"], result["failed_stage"]), ("failed", "publication"))
        self.assertFalse(result["hardware_validated"])
        self.assertFalse(result["qualification_ready_for_review"])
        self.assertEqual(len(self.reservation_rows()), 5)
        output = Path(self.config_document["output_root"]) / ("first-cycle-" + self.auth["record"])
        self.assertTrue((output / "publication.pending").exists())
        self.assertFalse((output / "publication.json").exists())
        candidate = self.reference(output / "result.json")
        with self.assertRaises((ValueError, OSError)):
            qualify.approve(candidate, self.review(candidate), self.root / "approved.json")
        recovered, execute = self.native_fixture(recover=True)
        execute.assert_not_called()
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(self.reservation_rows(), [])

    def test_summary_write_timeout_cannot_leave_approvable_candidate(self):
        self.publication_timeout("result.json")

    def test_completion_seal_timeout_cannot_leave_approvable_candidate(self):
        self.publication_timeout("publication.json")

    def test_release_is_last_step_and_cannot_revoke_completed_publication(self):
        clock, released = [0.0], [False]
        original_release, original_check = qualify.release_reservation, qualify.deploy.core.deadline_check
        original_save, original_remove = qualify.deploy.save, qualify.remove_publication_file
        def before_release(function):
            def guarded(*args, **kwargs):
                self.assertFalse(released[0], "釋放後不得再執行發布完成判斷或寫入")
                return function(*args, **kwargs)
            return guarded
        def delayed_release(*args):
            output = Path(self.config_document["output_root"]) / ("first-cycle-" + self.auth["record"])
            candidate = qualify.deploy.load(self.reference(output / "result.json"))
            qualify.validate_publication(candidate, self.config_document, self.auth)
            original_release(*args)
            released[0], clock[0] = True, 601.0
        with mock.patch.object(qualify.time, "monotonic", side_effect=lambda: clock[0]):
            with mock.patch.object(qualify, "release_reservation", side_effect=delayed_release):
                with mock.patch.object(qualify.deploy.core, "deadline_check", side_effect=before_release(original_check)):
                    with mock.patch.object(qualify.deploy, "save", side_effect=before_release(original_save)):
                        with mock.patch.object(qualify, "remove_publication_file", side_effect=before_release(original_remove)):
                            result, _ = self.native_fixture()
        self.assertTrue(released[0])
        self.assertEqual(result["status"], "review-required")
        self.assertEqual(self.reservation_rows(), [])
        qualify.validate_publication(result, self.config_document, self.auth)

    def test_pending_removal_timeout_revokes_completion(self):
        self.publication_timeout("pending")


if __name__ == "__main__":
    unittest.main()
