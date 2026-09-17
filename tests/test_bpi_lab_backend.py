#!/usr/bin/env python3
"""共用後端的完整五階段離線契約與中斷回歸；硬體入口全部封鎖。"""

import copy
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_backend as backend
import test_bpi_lab_deploy as data
import test_bpi_lab_uboot as boot_data
from bpi_lab_evidence_fixture import EvidenceFixture


class BackendFixture(data.DeployFixture):
    def write_json(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(backend.deploy.encode(value))
        return self.reference(target)

    def setUp(self):
        super().setUp()
        self.locks = self.root / "locks"
        mock.patch.object(backend, "LOCK_ROOT", self.locks).start()
        self.resources = {"uart": "/dev/serial/by-id/fixture-uart", "power": "power:fixture-power",
                          "media": "cid:" + data.fixtures.CID}
        self.pairing = {"schema": "bpi-lab-pairing-v1", "approved": True, "record": "fixture-pairing",
                        "hardware_id": self.contract["hardware_id"], "resources": self.resources,
                        "uart": {"stable_path": self.resources["uart"], "baud": 115200},
                        "power": {"driver": "bpi-pw", "name": "fixture-power", "ip": "192.0.2.9", "mac": "02:00:00:00:00:09"},
                        "emmc": self.contract["expected"], "protected_sd": self.contract["protected_sd"], "rescue": self.contract["rescue"]}
        pairing_ref = self.write_json("pairing.json", self.pairing)
        build_ref = self.write_json("uboot-qualification.json", {"record": "離線建置測試"})
        boot, blobs = boot_data.fixture(initrd="legacy")
        boot["uboot"].update(pairing_sha256=pairing_ref["sha256"], qualification_sha256=build_ref["sha256"])
        boot["bootargs"].append("ubootpart=1234abcd-02")
        self.boot, self.blobs = boot, blobs
        self.family_builder = mock.patch.object(backend.allwinner, "build_uboot_config",
                                                side_effect=lambda *args, **kwargs: copy.deepcopy(self.boot)).start()
        self.artifacts = self.root / "artifacts"
        for role, item in boot["files"].items():
            target = self.artifacts / item["path"].lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blobs[role])
        self.expected = {"schema": "bpi-lab-linux-expected-v1", "architecture": "arm64",
                         "kernel_release": boot["kernel_release"], "dt_compatible": ["fixture,board", "fixture,soc"],
                         "root": {**self.contract["expected"], "uuid": "01234567-89ab-cdef-0123-456789abcdef", "media_type": "MMC"}}
        family = {"schema": "bpi-lab-allwinner-components-v1", "status": "prepared", "hardware_validated": False,
                  "root_uuid": self.expected["root"]["uuid"], "kernel_release": boot["kernel_release"], "blockers": [],
                  "bootargs_template": [arg if not arg.startswith("ubootpart=") else "ubootpart=${partuuid}" for arg in boot["bootargs"]],
                  "files": {("effective_dtb" if role == "dtb" else role): {key: item[key] for key in ("bytes", "sha256")}
                            for role, item in boot["files"].items()}}
        extraction = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
                      "source_digest": self.source_record["compressed"], "raw": self.source_record["raw"],
                      "filesystem_uuid": self.expected["root"]["uuid"],
                      "partition": {"index": 2, "partuuid": "1234abcd-02"},
                      "files": {item["path"]: {"resolved": item["path"],
                                                "digest": {key: item[key] for key in ("bytes", "sha256")}}
                                for item in boot["files"].values()}}
        def child(name, value):
            ref = self.write_json("prepare/" + name, value)
            return {"path": name, "bytes": len((self.root / "prepare" / name).read_bytes()), "sha256": ref["sha256"]}
        prepared = {"schema": "bpi-lab-prepare-v1", "status": "prepared", "hardware_validated": False,
                    "root_uuid_verified": True, "board": "bpi-fixture", "kernel_release": boot["kernel_release"],
                    "source": self.source_record["compressed"], "raw": self.source_record["raw"],
                    "root_uuid": self.expected["root"]["uuid"], "components": child("family-result.json", family),
                    "extraction": child("extraction.json", extraction)}
        self.bundle = {"schema": "bpi-lab-backend-image-v1", "board": "bpi-fixture", "image": self.source_record,
                       "preparation": self.write_json("prepare/preparation.json", prepared),
                       "uboot": self.write_json("customer-uboot.json", boot), "uboot_qualification": build_ref,
                       "uboot_template": self.write_json("uboot-template.json", boot),
                       "linux_expected": self.write_json("linux.json", self.expected),
                       "artifact_root": str(self.artifacts), "customer_ssh": self.ssh,
                       "transport": {"kind": "mmc-original", "image_paths": {role: item["path"] for role, item in boot["files"].items()}}}
        rescue = copy.deepcopy(boot)
        rescue["source"]["device"] = 2
        rescue["kernel_release"] = self.contract["rescue"]["kernel"]
        rescue["bootargs"] = ["console=ttyS0,115200", "root=/dev/ram0"]
        self.rescue = rescue
        power = self.root / "bpi-pw"
        power.write_text("#!/bin/sh\nexit 1\n")
        power.chmod(0o700)
        self.lifecycle = {
            "schema": "bpi-lab-lifecycle-v1", "abi": backend.life.ABI, "hardware_id": self.contract["hardware_id"],
            "pairing_sha256": pairing_ref["sha256"], "uart_device": "/dev/ttyUSB7", "power_program": self.reference(power),
            "power_dependencies": [], "autoboot": {"stop_text": "Hit any key to stop autoboot:", "stop_key_hex": "20"},
            "login": {kind: {"kind": "root-shell", "shell_prompt": "root@fixture:~# "} for kind in ("customer", "rescue")},
            "shutdown_marker": "reboot: Power down", "off_seconds": 10,
            "authorization": {"record": "fixture-life-authorization", "normal_shutdown": True, "cold_cycle": True,
                              "customer_boot_may_write_emmc": True, "fault_poweroff": False,
                              "firstboot_account_changes": False, "install_test_ssh_key": False},
            "mmc": {"emmc": 1, "sd": 2}, "rescue_uboot": self.write_json("rescue-uboot.json", rescue),
            "rescue_qualification": build_ref, "rescue_artifact_root": str(self.artifacts),
            "rescue_expected": {key: self.expected[key] for key in ("architecture", "dt_compatible")},
            "ssh_setup": {mode: {"host_key_path": "/etc/ssh/ssh_host_ed25519_key.pub", "install_key": False,
                                  "public_key": None, "authorized_keys": "/root/.ssh/authorized_keys",
                                  "peer_ipv4": "192.0.2.100", "wait_for": "existing"} for mode in ("customer", "rescue")}}
        out = self.root / "evidence"
        out.mkdir()
        self.config_document = {
            "schema": "bpi-lab-backend-v1", "station_id": "fixture-station", "hardware_id": self.contract["hardware_id"],
            "test_version": "fixture-v1", "resources": self.resources, "pairing": pairing_ref,
            "deploy": self.write_json("deploy.json", self.contract), "output_root": str(out), "timeout_seconds": 600,
            "images": {self.source_record["compressed"]["sha256"]: self.write_json("bundle.json", self.bundle)},
            "lifecycle": self.write_json("lifecycle.json", self.lifecycle),
            "dependencies": {name: hashlib.sha256((Path(backend.__file__).parent / name).read_bytes()).hexdigest()
                             for name in backend.DEPENDENCIES}}
        self.sync_config()
        self.request = {"schema": "bpi-lab-request-v1", "work_key": "f" * 64, "attempt_id": "fixture-attempt",
                        "stage": "preflight", "station_id": "fixture-station", "hardware_id": self.contract["hardware_id"],
                        "image_sha256": self.source_record["compressed"]["sha256"], "boot_config_sha256": self.config_ref["sha256"],
                        "test_version": "fixture-v1", "mode": "hardware", "image_root": str(self.root),
                        "image": {"board": "bpi-fixture", "relative_path": self.source.name,
                                  "compressed_bytes": len(self.xz)}}

    def sync_config(self):
        evidence = self.write_json("qualification-source.json", {"record": "離線核定替身，不是實板證據"})
        qualification = {"schema": "bpi-lab-backend-qualification-v1", "scope_sha256": backend.scope_digest(self.config_document),
                         "approved_stages": list(backend.station.STAGES), "hardware_validated": True,
                         "rescue_verified": True, "single_image_cycle_verified": True, "source_evidence": [evidence]}
        self.config_document["qualification"] = self.write_json("qualification.json", qualification)
        self.config_ref = self.write_json("backend.json", self.config_document)

    def fake_result(self, context, current, output, timeout):
        request = context[-1]
        stage = request["stage"]
        mode = current["phase"] if "resume" in request else "customer" if stage in ("boot", "smoke") else "rescue"
        observed = {"schema": "bpi-lab-session-v1", "binding": {key: request[key] for key in backend.session.BINDINGS},
                    "mode": mode, "boot_id": "11111111-2222-3333-4444-555555555555",
                    "uart_verified": True, "ssh_verified": True, "hostkey_source": "same-session-uart"}
        if stage == "deploy":
            remote_request = {"expected": self.contract["expected"], "protected_sd": data.fixtures.PROTECTED,
                              "rescue_schema": data.SCHEMA, "rescue_expected": data.RESCUE,
                              "backup_manifest_sha256": self.contract["backup"]["sha256"],
                              "confirm_overwrite": True, "backup_verified": True,
                              "source": {key: self.source_record[key] for key in ("raw", "compressed")}}
            return {"schema": "bpi-h618-emmc-deploy-v1", "status": "verified", "ok": True,
                    "request": remote_request, "remote_state": data.state(remote_request, True), "session": observed}
        if stage == "smoke":
            return {"status": "passed", "ok": True, "checks": [{"check": "root_cid", "status": "passed"}], "session": observed}
        return {"status": "verified", "customer_kernel_verified": stage == "boot",
                "rescue_verified": stage == "recovery", "session": observed}

    def run_stage(self, stage, **extra):
        request = {**self.request, "stage": stage, **extra}
        with mock.patch.object(backend.NativeRuntime, "execute", side_effect=self.fake_result) as execute:
            result = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], request)
        return result, execute


class BackendTests(EvidenceFixture, BackendFixture):
    def test_strict_boot_rejects_forged_identity_action_and_simulation(self):
        request = {**self.request, "stage": "boot"}
        current = {"phase": "rescue", "status": "verified", "stage": "deploy",
                   "boot_id": "11111111-2222-3333-4444-555555555555"}
        result = self.fake_result((self.config_document, self.contract, self.bundle, self.boot, self.expected, request),
                                  current, self.root / "strict-boot", 600)
        backend.validate_result("boot", result, request, self.contract, self.bundle,
                                config=self.config_document, current=current)
        mutations = [lambda row: row.update(action="recovery"), lambda row: row.update(test_only=True),
                     lambda row: row.update(synthetic=True), lambda row: row["session"].pop("identity"),
                     lambda row: row["session"]["identity"].update(dt_compatible=["wrong,board"]),
                     lambda row: row["session"]["identity"]["root"].update(uuid="wrong")]
        for mutate in mutations:
            changed = copy.deepcopy(result)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                backend.validate_result("boot", changed, request, self.contract, self.bundle,
                                        config=self.config_document, current=current)

    def test_strict_smoke_requires_full_collection_and_unique_checks(self):
        request = {**self.request, "stage": "smoke"}
        current = {"phase": "customer", "boot_id": "11111111-2222-3333-4444-555555555555"}
        result = self.fake_result((self.config_document, self.contract, self.bundle, self.boot, self.expected, request),
                                  current, self.root / "strict-smoke", 600)
        for change in ({"checks": [{"check": "root_cid", "status": "passed"}]},
                       {"checks": [result["checks"][0], result["checks"][0]]}, {"linux_collection": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                backend.validate_result("smoke", {**result, **change}, request, self.contract, self.bundle,
                                        config=self.config_document, current=current)

    def test_resume_cannot_return_customer_when_next_stage_needs_rescue(self):
        current = {"phase": "rescue", "boot_id": "11111111-2222-3333-4444-555555555555"}
        request = {**self.request, "resume": {"next_stage": "boot"}}
        result = {**self.media_proof(), "session": self.observed("customer", request, current["boot_id"])}
        with self.assertRaises(ValueError):
            backend.validate_result("preflight", result, request, self.contract, self.bundle,
                                    config=self.config_document, current=current)

    def test_receipt_cannot_only_agree_with_its_own_wrong_contract(self):
        request = {**self.request, "stage": "deploy"}
        result = {**self.media_proof(final=True), "session": self.observed(
            "rescue", request, "11111111-2222-3333-4444-555555555555")}
        result["request"] = copy.deepcopy(result["request"])
        result["request"]["expected"]["cid"] = "0" * 32
        result["remote_state"]["identity"]["cid"] = "0" * 32
        with self.assertRaises(ValueError):
            backend.validate_result("deploy", result, request, self.contract, self.bundle, config=self.config_document)

    def test_orphan_pending_blocks_new_preflight_and_is_not_deleted(self):
        with backend.resource_lock(self.config_document):
            store = backend.StateStore(self.config_document)
            backend.deploy.save(self.locks, store.pending, backend.deploy.encode({"status": "uncertain"}))
        report, execute = self.run_stage("preflight")
        self.assertEqual(report["status"], "blocked", report)
        execute.assert_not_called()
        self.assertTrue((self.locks / store.pending).exists())

    def test_state_receipt_hook_preserves_default_validation_and_writer_isolation(self):
        receipt = self.media_proof(final=True)
        reference = self.write_json("receipt.json", receipt)
        value = {"schema": "bpi-lab-backend-state-v1", "stage": "deploy", "status": "running"}
        with backend.resource_lock(self.config_document):
            store = backend.StateStore(self.config_document)
            store.write(dict(value))
            completed = {**value, "status": "verified", "operation": reference}
            with mock.patch.object(store, "validate_deploy_receipt", side_effect=ValueError("離線嚴格收據拒絕")) as check:
                with self.assertRaises(ValueError):
                    store.write(dict(completed))
            check.assert_called_once_with(receipt)
            self.assertIs(store.read()["writer_unresolved"], True)
            for bad in ({**receipt, "ok": False}, {**receipt, "status": "failed"},
                        {**receipt, "remote_state": {**receipt["remote_state"], "readback": {}}}):
                with self.assertRaises(ValueError):
                    store.validate_deploy_receipt(bad)
            with mock.patch.object(store, "validate_deploy_receipt", wraps=store.validate_deploy_receipt) as check:
                store.write(completed)
            check.assert_called_once_with(receipt)
            self.assertIs(store.read()["writer_unresolved"], False)

    def test_same_attempt_can_explicitly_retry_failed_recovery_without_overwriting(self):
        report, _ = self.run_stage("preflight")
        self.assertEqual(report["status"], "passed", report)
        request = {**self.request, "stage": "recovery"}
        with mock.patch.object(backend.NativeRuntime, "execute", side_effect=TimeoutError("離線救援失敗")):
            first = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], request)
        self.assertIs(first["needs_recovery"], True)
        second, _ = self.run_stage("recovery")
        self.assertEqual(second["status"], "passed", second)
        self.assertNotEqual(first["evidence_path"], second["evidence_path"])

    def test_publication_crossing_deadline_is_failed_and_isolated(self):
        now = 0
        publish = backend.StateStore.publish
        def late(store, *args):
            nonlocal now
            publish(store, *args)
            now = 1000
        with mock.patch.object(backend.time, "monotonic", side_effect=lambda: now), \
                mock.patch.object(backend.StateStore, "publish", autospec=True, side_effect=late):
            report, _ = self.run_stage("preflight")
        self.assertEqual(report["status"], "failed", report)
        self.assertIs(report["needs_recovery"], True)
        self.assertIs(report["hardware_validated"], False)
        self.assertEqual(backend.StateStore(self.config_document).read()["status"], "failed")

    def test_all_five_stages_publish_bound_reports(self):
        for stage in backend.station.STAGES:
            with self.subTest(stage=stage):
                report, execute = self.run_stage(stage)
                self.assertEqual(report["status"], "passed", report)
                self.assertIs(report["needs_recovery"], False)
                backend.station.validate_report(report, {**self.request, "stage": stage})
                execute.assert_called_once()
        state = backend.StateStore(self.config_document).read()
        self.assertEqual((state["status"], state["stage"], state["phase"]), ("verified", "recovery", "rescue"))

    def test_no_qualification_never_calls_runtime(self):
        Path(self.config_document["qualification"]["path"]).unlink()
        report, execute = self.run_stage("preflight")
        self.assertEqual(report["status"], "blocked")
        self.assertIs(report["needs_recovery"], False)
        execute.assert_not_called()

    def test_changed_dependency_never_calls_runtime(self):
        self.config_document["dependencies"]["bpi_lab_session.py"] = "0" * 64
        self.sync_config()
        self.request["boot_config_sha256"] = self.config_ref["sha256"]
        report, execute = self.run_stage("preflight")
        self.assertEqual(report["status"], "blocked")
        execute.assert_not_called()

    def test_k3_python_c_and_patch_are_exact_pinned_dependencies(self):
        names = ("bpi_lab_k3_runtime.py", "bpi_lab_spacemit.py",
                 "bpi_lab_k3/bpi_lab_k3.c", "bpi_lab_k3/readonly-sdk.patch",
                 "bpi_h618_rescue/init", "bpi_h618_rescue/runtime.py", "bpi_h618_rescue/ssh-start", "bpi_h618_rescue/udhcpc-script")
        for name in names:
            self.assertIn(name, backend.DEPENDENCIES)
            changed = {**self.config_document["dependencies"], name: "0" * 64}
            with self.subTest(name=name), self.assertRaises(ValueError):
                backend.check_dependencies(changed)
        for name in ("bpi_lab_k3/other.c", "../bpi_lab_k3_runtime.py", "bpi_lab_k3/../bpi_lab_backend.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                backend.check_dependencies({**self.config_document["dependencies"], name: "a" * 64})

    def test_incomplete_k3_runtime_is_explicitly_blocked(self):
        self.assertIs(backend.life.boot_driver({"schema": backend.life.k3_runtime.SCHEMA}), backend.life.k3_runtime)
        with mock.patch.object(backend.life.k3_runtime, "boot", None, create=True):
            with self.assertRaisesRegex(ValueError, "API 尚未完整落盤"):
                backend.life.require_k3_api()

    def test_no_skipping_or_duplicate_deploy(self):
        report, execute = self.run_stage("deploy")
        self.assertEqual(report["status"], "blocked")
        execute.assert_not_called()
        self.assertEqual(self.run_stage("preflight")[0]["status"], "passed")
        self.assertEqual(self.run_stage("deploy")[0]["status"], "passed")
        report, execute = self.run_stage("deploy")
        self.assertEqual(report["status"], "blocked")
        execute.assert_not_called()

    def test_resume_rechecks_state_without_redeploy(self):
        prior = []
        for stage in ("preflight", "deploy"):
            report, _ = self.run_stage(stage)
            self.assertEqual(report["status"], "passed", report)
            prior.append({"stage": stage, **self.reference(Path(report["evidence_path"]) / "report.json")})
        report, execute = self.run_stage("preflight", resume={"next_stage": "boot", "previous_reports": prior})
        self.assertEqual(report["status"], "passed", report)
        self.assertTrue(report["resume_state_verified"])
        self.assertEqual(backend.StateStore(self.config_document).read()["stage"], "deploy")
        self.assertEqual(self.run_stage("boot")[0]["status"], "passed")

    def test_tampered_resume_history_is_blocked(self):
        report, _ = self.run_stage("preflight")
        ref = self.reference(Path(report["evidence_path"]) / "report.json")
        ref["sha256"] = "0" * 64
        result, execute = self.run_stage("preflight", resume={"next_stage": "deploy", "previous_reports": [{"stage": "preflight", **ref}]})
        self.assertEqual(result["status"], "blocked")
        execute.assert_not_called()

    def test_interruption_blocks_resume_and_retains_intent(self):
        self.run_stage("preflight")
        request = {**self.request, "stage": "deploy"}
        with mock.patch.object(backend.NativeRuntime, "execute", side_effect=TimeoutError("離線中斷")):
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], request)
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["needs_recovery"], True)
        self.assertEqual(backend.StateStore(self.config_document).read()["status"], "failed")
        result, execute = self.run_stage("boot")
        self.assertEqual(result["status"], "blocked")
        execute.assert_not_called()
        result, execute = self.run_stage("recovery")
        self.assertEqual(result["status"], "blocked")
        execute.assert_not_called()
        store = backend.StateStore(self.config_document)
        self.assertTrue(store.read()["writer_unresolved"])
        # 首次核定驅動器即使寫下新的失敗 recovery，也不能清除既有未知 writer。
        value = store.read()
        value.update(stage="recovery", status="failed", writer_unresolved=False)
        store.write(value)
        self.assertTrue(store.read()["writer_unresolved"])
        with self.assertRaises(ValueError):
            backend.require_recovery_safe(store.read())

    def test_injected_runtime_cannot_publish_hardware_success(self):
        runtime = mock.Mock()
        runtime.execute.side_effect = self.fake_result
        report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request, runtime=runtime)
        self.assertEqual(report["status"], "blocked", report)
        self.assertFalse(report["hardware_validated"])
        self.assertIsNone(backend.StateStore(self.config_document).read())

    def test_falsey_runtime_cannot_construct_native_runtime(self):
        class FalseyRuntime:
            def __bool__(self):
                return False
            def execute(inner, *args):
                return self.fake_result(*args)
        with mock.patch.object(backend, "NativeRuntime", side_effect=AssertionError("禁止建立真 runtime")) as native:
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request,
                                       runtime=FalseyRuntime())
        native.assert_not_called()
        self.assertIs(report["hardware_validated"], False)
        self.assertIs(report["needs_recovery"], False)
        self.assertIsNone(backend.StateStore(self.config_document).read())

    def test_native_recovery_rejects_unknown_writer_before_lifecycle(self):
        request = {**self.request, "stage": "recovery"}
        context = (self.config_document, self.contract, self.bundle, self.boot, self.expected, request)
        output = self.root / "blocked-recovery"
        for stage, status, unresolved in (("deploy", "running", False), ("deploy", "failed", False),
                                           ("recovery", "failed", True)):
            current = {"stage": stage, "status": status, "writer_unresolved": unresolved}
            with self.subTest(stage=stage, status=status), mock.patch.object(backend.life, "cycle") as cycle:
                with self.assertRaisesRegex(ValueError, "writer"):
                    backend.NativeRuntime().execute(context, current, output, 600)
                cycle.assert_not_called()
                self.assertFalse(output.exists())

    def test_preflight_failure_requests_recovery_only_after_persisted_intent(self):
        with mock.patch.object(backend.NativeRuntime, "execute", side_effect=TimeoutError("離線預檢中斷")):
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["needs_recovery"], True)
        self.assertEqual(backend.StateStore(self.config_document).read()["stage"], "preflight")

    def test_state_publication_failure_before_runtime_does_not_request_recovery(self):
        with mock.patch.object(backend.StateStore, "write", side_effect=OSError("離線狀態寫入失敗")), \
                mock.patch.object(backend.NativeRuntime, "execute") as execute:
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)
        self.assertEqual(report["status"], "blocked")
        self.assertIs(report["needs_recovery"], False)
        execute.assert_not_called()

    def test_failed_report_disk_error_preserves_recovery_signal(self):
        save = backend.deploy.save
        def disk_error(directory, name, blob):
            if name in ("report.json", "report.failure.json"):
                raise OSError("離線報告磁碟故障")
            return save(directory, name, blob)
        with mock.patch.object(backend.deploy, "save", side_effect=disk_error), \
                mock.patch.object(backend.NativeRuntime, "execute", side_effect=TimeoutError("離線操作中斷")):
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["needs_recovery"], True)
        self.assertIs(report["report_persistence_failed"], True)
        self.assertEqual(backend.StateStore(self.config_document).read()["status"], "failed")
        backend.station.validate_report(report, self.request)

    def test_intent_rename_followed_by_sync_failure_requires_recovery(self):
        replace, fsync = os.replace, os.fsync
        renamed = False
        def publish(*args, **kwargs):
            nonlocal renamed
            result = replace(*args, **kwargs)
            renamed = True
            return result
        def sync(fd):
            if renamed:
                raise OSError("離線模擬 intent rename 後同步失敗")
            return fsync(fd)
        with mock.patch.object(os, "replace", side_effect=publish), mock.patch.object(os, "fsync", side_effect=sync), \
                mock.patch.object(backend.NativeRuntime, "execute") as execute:
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)
        self.assertTrue(renamed)
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["needs_recovery"], True)
        execute.assert_not_called()
        self.assertEqual(backend.StateStore(self.config_document).read()["status"], "running")

    def test_unreadable_intent_after_failed_write_is_conservatively_isolated(self):
        with mock.patch.object(backend.StateStore, "write", side_effect=OSError("離線同步失敗")), \
                mock.patch.object(backend.StateStore, "read", side_effect=[None, OSError("無法判定 intent")]), \
                mock.patch.object(backend.NativeRuntime, "execute") as execute:
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["needs_recovery"], True)
        execute.assert_not_called()

    def test_failed_intent_is_inspected_before_releasing_resource_locks(self):
        locked = False
        acquire = backend.resource_lock
        @contextmanager
        def lock(config):
            nonlocal locked
            with acquire(config):
                locked = True
                try:
                    yield
                finally:
                    locked = False
        def read():
            self.assertTrue(locked, "intent 的不確定狀態須在同一把資源鎖內判讀")
            return None
        with mock.patch.object(backend, "resource_lock", side_effect=lock), \
                mock.patch.object(backend.StateStore, "read", side_effect=read) as inspect, \
                mock.patch.object(backend.StateStore, "write", side_effect=OSError("離線意圖寫入失敗")), \
                mock.patch.object(backend.NativeRuntime, "execute") as execute:
            report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)
        self.assertEqual(inspect.call_count, 2)
        self.assertIs(report["needs_recovery"], False)
        execute.assert_not_called()

    def test_persisted_deploy_intent_error_isolates_writer_before_runtime_starts(self):
        report, _ = self.run_stage("preflight")
        self.assertEqual(report["status"], "passed", report)
        write = backend.StateStore.write
        def write_then_fail(store, value):
            write(store, value)
            raise OSError("離線模擬 deploy intent 發布後同步錯誤")
        with mock.patch.object(backend.StateStore, "write", autospec=True, side_effect=write_then_fail):
            report, execute = self.run_stage("deploy")
        execute.assert_not_called()
        self.assertEqual(report["status"], "failed", report)
        self.assertIs(report["needs_recovery"], True)
        current = backend.StateStore(self.config_document).read()
        self.assertEqual((current["stage"], current["status"]), ("deploy", "running"))
        self.assertIs(current["writer_unresolved"], True)
        recovery, execute = self.run_stage("recovery")
        self.assertEqual(recovery["status"], "blocked", recovery)
        self.assertIs(recovery["needs_recovery"], False)
        execute.assert_not_called()

    def test_successful_operation_with_unpublished_report_remains_failed(self):
        with mock.patch.object(backend.StateStore, "publish", side_effect=OSError("離線發布故障")):
            report, execute = self.run_stage("preflight")
        execute.assert_called_once()
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["needs_recovery"], True)
        self.assertIs(report["hardware_validated"], False)
        self.assertEqual(backend.StateStore(self.config_document).read()["status"], "failed")

    def test_original_component_drift_is_blocked(self):
        location = self.artifacts / self.boot["files"]["kernel"]["path"].lstrip("/")
        location.write_bytes(b"x" * self.boot["files"]["kernel"]["bytes"])
        report, execute = self.run_stage("preflight")
        self.assertEqual(report["status"], "blocked")
        execute.assert_not_called()

    def test_mainline_hex_addresses_keep_existing_normalization(self):
        boot = copy.deepcopy(self.boot)
        boot["files"]["kernel"]["address"] = hex(boot["files"]["kernel"]["address"])
        boot["files"]["kernel"]["entry"] = hex(boot["files"]["kernel"]["entry"])
        self.bundle["uboot"] = self.write_json("customer-hex.json", boot)
        self.config_document["images"][self.request["image_sha256"]] = self.write_json("bundle.json", self.bundle)
        _, selected, _ = backend.selected_image(self.config_document, self.contract, self.request)
        self.assertEqual(selected, self.boot)

    def test_wrong_request_binding_is_blocked(self):
        report, execute = self.run_stage("preflight", hardware_id="bpi-m4zero-0845")
        self.assertEqual(report["status"], "blocked")
        execute.assert_not_called()

    def test_h618_legacy_aliases_are_rejected_before_runtime(self):
        for board in ("bpi-m4zero", "bpi-m4berry"):
            self.bundle["board"] = board
            self.config_document["images"][self.request["image_sha256"]] = self.write_json("bundle.json", self.bundle)
            self.sync_config()
            self.request["boot_config_sha256"] = self.config_ref["sha256"]
            self.request["image"]["board"] = board
            with self.subTest(board=board), mock.patch.object(backend.life, "customer_view") as view:
                report, execute = self.run_stage("preflight")
                self.assertEqual(report["status"], "blocked")
                self.assertIn("H618", report["reason"])
                self.assertIs(report["needs_recovery"], False)
                view.assert_not_called()
                execute.assert_not_called()

    def test_h618_formal_names_cannot_borrow_another_family_manifest(self):
        for board in sorted(backend.H618_BOARDS):
            self.bundle["board"] = board
            preparation = backend.deploy.load(self.bundle["preparation"])
            preparation["board"] = board
            self.bundle["preparation"] = self.write_json("prepare/preparation.json", preparation)
            self.config_document["images"][self.request["image_sha256"]] = self.write_json("bundle.json", self.bundle)
            self.request["image"]["board"] = board
            with self.subTest(board=board), self.assertRaisesRegex(ValueError, "正式板名的 Allwinner"):
                backend.selected_image(self.config_document, self.contract, self.request)

    def test_0845_scope_and_sram_identity_cannot_enter_shared_backend(self):
        for changed in ({**self.contract, "hardware_id": "bpi-m4zero-0845"},
                        {**self.contract, "authorization": {**self.contract["authorization"], "hardware_id": "bpi-m4zero-0845"}},
                        {**self.contract, "rescue": {**self.contract["rescue"], "schema": "bpi-h618-rescue-v1"}},
                        *({**self.contract, role: {**self.contract[role], "cid": cid}}
                          for role in ("expected", "protected_sd") for cid in backend.SRAM_PROTOTYPE_CIDS)):
            with self.subTest(contract=changed), self.assertRaisesRegex(ValueError, "0845"):
                backend.selected_image(self.config_document, changed, self.request)

    def test_bootstrap_inputs_do_not_require_fabricated_cycle_qualification(self):
        del self.config_document["qualification"]
        ref = self.write_json("bootstrap.json", self.config_document)
        config, contract = backend.load_inputs(ref)
        self.assertEqual(contract["hardware_id"], self.contract["hardware_id"])
        self.assertNotIn("qualification", config)
        with self.assertRaises(ValueError):
            backend.load_config(ref)

    def test_family_cma_rejection_cannot_be_bypassed_by_rendered_config(self):
        self.family_builder.side_effect = ValueError("CMA 未核定")
        report, execute = self.run_stage("preflight")
        self.assertEqual(report["status"], "blocked")
        self.family_builder.assert_called_once()
        execute.assert_not_called()

    def test_offline_family_recipes_are_not_execution_configs(self):
        with mock.patch.object(backend.extlinux, "validate_template", return_value={"execution_ready": False}) as check:
            with self.assertRaises(ValueError):
                backend.render_family({"schema": backend.extlinux.SCHEMA}, {}, self.artifacts)
        check.assert_called_once()
        with mock.patch.object(backend.special, "bootconfig", return_value={"vendor": {"abi": "fixture"}}) as check:
            with self.assertRaises(ValueError):
                backend.render_family({"schema": backend.special.SCHEMA}, {}, self.artifacts)
        check.assert_called_once()

    def test_mmc_rejects_derived_dtb_not_in_original_image(self):
        prepared = copy.deepcopy(self.boot)
        extraction = {"partition": {"index": 2, "partuuid": "1234abcd-02"},
                      "files": {item["path"]: {"resolved": item["path"], "digest": {key: item[key] for key in ("bytes", "sha256")}}
                                for item in prepared["files"].values()}}
        prepared["files"]["dtb"]["sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            backend.bind_transport(self.bundle, prepared, extraction)

    def test_tftp_publishes_real_prepared_bytes_and_never_overwrites(self):
        prepared, blobs = boot_data.fixture(initrd="legacy", source="tftp")
        for role, item in prepared["files"].items():
            path = self.artifacts / item["path"].lstrip("/")
            path.write_bytes(blobs[role])
        root = self.root / "tftp"
        root.mkdir()
        bundle = {**self.bundle, "_prepared_boot": prepared,
                  "transport": {"kind": "tftp-published", "root": str(root), "serverip": prepared["source"]["serverip"]}}
        config = backend.bind_transport(bundle, prepared, {})
        backend.publish_artifacts(bundle, config)
        backend.publish_artifacts(bundle, config)
        for role, item in config["files"].items():
            published = root / item["path"]
            self.assertEqual(published.read_bytes(), blobs[role])
            self.assertEqual(published.stat().st_mode & 0o777, 0o444)
        damaged = root / config["files"]["dtb"]["path"]
        damaged.chmod(0o600)
        damaged.write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            backend.publish_artifacts(bundle, config)
        self.assertEqual(damaged.read_bytes(), b"damaged")


if __name__ == "__main__":
    unittest.main()
