"""五階段的離線整合；以替身執行真實 lifecycle 接線，不產生實板資格。"""

from contextlib import ExitStack
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_h618 as adapter
from test_bpi_h618_customer_boot import fixtures
from test_bpi_lab_h618_lifecycle import FakeRuntime
from test_bpi_lab_h618_session import KEY

life, session = adapter.life, adapter.session


class StageContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.components, self.receipt = fixtures()
        self.components["image"]["path"] = str(self.root / "source.img.xz")
        self.receipt["source"]["path"] = self.components["image"]["path"]
        self.manifest = {"inputs": [{"path": path, "bytes": 1024, "sha256": "a" * 64, "crc32": "12345678"}
                                    for path in life.rescue.PATHS],
                         "sd_prefix": self.receipt["remote_state"]["sd_after"]["prefix"]}
        self.runtime = FakeRuntime(self.root, self.components, self.manifest)
        self.lifecycle = {"schema": "bpi-lab-h618-lifecycle-v2", "station_id": "offline-0845",
            "hardware_id": life.HARDWARE, "power": copy.deepcopy(life.POWER),
            "uart": {"stable_path": "/dev/serial/by-id/usb-offline-0845", "device": "/dev/ttyUSB0", "baud": 115200},
            "authorization": {"record": "offline-only", "normal_shutdown": True, "fault_poweroff": True,
                              "customer_boot_may_write_emmc": True},
            "bridge": self.ref("bridge.bin", b"offline-bridge"),
            "rescue_inputs": self.ref("rescue.json", self.manifest), "rescue_identity_sha256": "d" * 64,
            "timeout_seconds": 600, "off_seconds": 10, "ssh_policy": copy.deepcopy(life.SSH_POLICY),
            "dependencies": self.dependencies(life.DEPENDENCIES)}
        self.lifecycle["pairing"] = {"approved": True, "record": "offline-only", "hardware_id": life.HARDWARE,
            "uart": copy.deepcopy(self.lifecycle["uart"]), "power": copy.deepcopy(life.POWER),
            "emmc": copy.deepcopy(life.customer.EXPECTED), "sd": copy.deepcopy(life.customer.PROTECTED_SD)}
        self.login = {"schema": "bpi-lab-h618-session-v1", "login": {"username": "root", "password_file": None,
            "new_password_file": None, "initialize": False, "skip_user_creation": False},
            "peer_ipv4": "192.0.2.2", "identity_file": str(self.root / "private-key"),
            "public_key": self.ref("key.pub", KEY), "rescue_network": {"mode": "existing"},
            "customer_network": {"mode": "existing"}, "dt_compatible": {
                "rescue": ["sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"],
                "customer": ["sinovoip,bpi-m4-zero-emac", "sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"]}}
        self.config = {"schema": "bpi-lab-h618-v2", "station_id": "offline-0845", "hardware_id": life.HARDWARE,
            "test_version": "offline-v2", "dependencies": self.dependencies(adapter.DEPENDENCIES),
            "backup": self.ref("manifest.json", {}), "authorization": {"userarea_write": True,
                "hardware_id": life.HARDWARE, "media_identity": "cid:" + life.customer.EXPECTED["cid"],
                "record": "純離線替身，不是實物授權"}, "sd_prefix": self.manifest["sd_prefix"],
            "rescue_identity_sha256": "d" * 64, "lifecycle": self.ref("lifecycle.json", self.lifecycle),
            "session": self.ref("login.json", self.login),
            "components": {"a" * 64: self.ref("components.json", self.components)},
            "output_root": str(self.root), "timeout_seconds": 1800}
        self.receipt["request"]["backup_manifest_sha256"] = self.config["backup"]["sha256"]
        self.config_ref = self.ref("config.json", self.config)
        self.request = {"schema": "bpi-lab-request-v1", "work_key": "f" * 64, "attempt_id": "attempt-1",
            "stage": "preflight", "station_id": "offline-0845", "hardware_id": life.HARDWARE,
            "image_sha256": "a" * 64, "boot_config_sha256": self.config_ref["sha256"], "test_version": "offline-v2",
            "mode": "hardware", "image_root": str(self.root), "image": {"board": "bpi-m4z-emac",
                "relative_path": "source.img.xz", "compressed_bytes": 512, "release": "bookworm",
                "variant": "minimal", "expected_sha256": "a" * 64}}
        self.ssh = {"config": self.ref("synthetic-ssh", b"Host offline\n"), "alias": "offline",
                    "known_hosts": self.ref("synthetic-hosts", KEY)}
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.no_serial = stack.enter_context(patch.object(life.rescue, "open_serial", side_effect=AssertionError("禁止真 UART")))
        self.no_process = stack.enter_context(patch.object(adapter.backup.subprocess, "Popen",
                                                          side_effect=AssertionError("禁止真 SSH／電源")))
        stack.enter_context(patch.object(life, "LOCK_ROOT", self.runtime.lock_root))
        stack.enter_context(patch.object(life, "NativeRuntime", return_value=self.runtime))
        stack.enter_context(patch.object(life.rescue, "BRIDGE_SHA", hashlib.sha256(b"offline-bridge").hexdigest()))
        supervisor = stack.enter_context(patch.object(life.rescue, "LabSupervisorSession"))
        supervisor.return_value.probe.return_value = {"abi": 3}
        supervisor.return_value.run.return_value = {"ran": True}
        self.establish = stack.enter_context(patch.object(session, "establish", side_effect=self.fake_session))
        self.recheck = stack.enter_context(patch.object(session, "recheck"))
        stack.enter_context(patch.object(adapter.deploy, "validate_backup", return_value=self.config["backup"]))
        self.readonly = stack.enter_context(patch.object(adapter, "readonly_preflight", return_value={"remote": {}}))
        self.writer = stack.enter_context(patch.object(adapter.deploy, "deploy", side_effect=self.fake_deploy))
        self.smoke = stack.enter_context(patch.object(adapter.smoke, "smoke", return_value={"ok": True, "remote": {}, "request": {}}))
        stack.enter_context(patch.object(adapter.smoke, "validate_result"))

    def tearDown(self):
        self.no_serial.assert_not_called()
        self.no_process.assert_not_called()

    def dependencies(self, names):
        return {name: hashlib.sha256((Path(adapter.__file__).parent / name).read_bytes()).hexdigest() for name in names}

    def ref(self, name, value):
        path = self.root / name
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        path.chmod(0o600)
        return session.reference(path)

    def fake_session(self, console, mode, output, deadline, at_login, **kwargs):
        deadline.remaining()
        return {"schema": "bpi-lab-h618-session-result-v1", "binding": session.binding(kwargs["request"]),
                "mode": mode, "boot_id": mode + "-offline-boot", "ssh": self.ssh,
                "strict_ssh_verified": True, "hardware_validated": False, "first_login_initialized": at_login}

    def fake_deploy(self, **kwargs):
        output = life._new_directory(kwargs["output_dir"])
        life._save(output, "receipt.json", self.receipt)
        return copy.deepcopy(self.receipt)

    def run_stage(self, stage, resume=None):
        self.request["stage"] = stage
        self.request.pop("resume", None)
        if resume is not None:
            self.request["resume"] = resume
        self.runtime.target_media = "1:1" if stage == "boot" else "0:1"
        return adapter.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request)

    def through(self, last):
        reports = []
        for stage in adapter.IMPLEMENTED:
            result = self.run_stage(stage)
            self.assertEqual(result["status"], "passed", result)
            reports.append(result)
            if stage == last:
                return reports

    def state(self):
        return adapter._read_state(self.runtime.lock_root)

    def resume(self, wanted):
        return {"next_stage": wanted, "previous_reports": [
            {"stage": stage, **ref} for stage, ref in self.state()["completed"].items()]}

    def test_all_five_stages_call_real_lifecycle_and_keep_qualification_pending(self):
        reports = self.through("recovery")
        self.assertEqual(self.writer.call_count, 1)
        self.assertEqual(self.smoke.call_count, 1)
        self.assertTrue(reports[2]["customer_kernel_verified"])
        self.assertTrue(reports[-1]["rescue_verified"])
        self.assertIsNone(self.state()["next_stage"])
        self.assertEqual(life._read_state(self.runtime.lock_root)["status"], "rescue")
        for report in reports:
            self.assertFalse(report["whole_adapter_ready"])
            self.assertFalse(report["needs_recovery"])
            self.assertEqual(report["hardware_qualification"], "awaiting_hardware")
            self.assertFalse(report["session"]["hardware_validated"])
        self.assertEqual(self.runtime.calls.count("off"), 2)
        self.assertEqual(self.runtime.opened, self.runtime.closed)

    def test_pure_preflight_blocked_has_no_recovery_requirement(self):
        with patch.object(adapter, "load_config", side_effect=ValueError("合成本機設定不符")):
            result = self.run_stage("preflight")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["needs_recovery"])
        self.assertEqual(self.runtime.opened, 0)
        self.assertFalse((self.runtime.lock_root / adapter.STATE_FILE).exists())

    def test_initial_state_write_failure_without_state_does_not_require_recovery(self):
        with patch.object(adapter, "_write_state", side_effect=OSError("合成狀態寫入尚未開始")):
            result = self.run_stage("preflight")
        self.assertFalse(result["needs_recovery"])
        self.assertEqual(self.runtime.opened, 0)
        self.assertIsNone(self.state())

    def test_initial_state_fsync_failure_requires_recovery_without_uart_access(self):
        original = adapter._write_state
        def fail(root, state):
            original(root, state)
            raise OSError("合成狀態已替換但同步失敗")
        with patch.object(adapter, "_write_state", side_effect=fail):
            result = self.run_stage("preflight")
        self.assertTrue(result["needs_recovery"])
        self.assertEqual(self.runtime.opened, 0)
        self.assertEqual(self.state()["binding"], session.binding(self.request))
        self.assertEqual(self.run_stage("recovery")["status"], "passed")

    def test_preflight_operation_failure_requires_same_attempt_recovery(self):
        self.recheck.side_effect = ValueError("合成同次核對失敗")
        result = self.run_stage("preflight")
        self.assertTrue(result["needs_recovery"])
        self.assertEqual(self.state()["status"], "failed")
        self.recheck.side_effect = None
        recovered = self.run_stage("recovery")
        self.assertEqual(recovered["status"], "passed")
        self.assertFalse(recovered["needs_recovery"])
        self.assertEqual(session.binding(result), session.binding(recovered))

    def test_repeated_preflight_keeps_original_evidence_and_lease(self):
        first = self.through("preflight")[0]
        path = Path(first["evidence_path"]) / "stage.json"
        blob = path.read_bytes()
        state = self.state()
        self.assertNotEqual(self.run_stage("preflight")["status"], "passed")
        self.assertEqual(path.read_bytes(), blob)
        self.assertEqual(self.state(), state)

    def test_cross_attempt_stage_rejected_before_uart(self):
        self.through("preflight")
        self.request["attempt_id"] = "attempt-2"
        count = self.runtime.opened
        result = self.run_stage("deploy")
        self.assertNotEqual(result["status"], "passed")
        self.assertFalse(result["needs_recovery"])
        self.assertEqual(self.runtime.opened, count)
        self.writer.assert_not_called()

    def test_boot_requires_same_attempt_receipt_not_preconfigured_receipt(self):
        self.through("deploy")
        state = self.state()
        ref = state["completed"]["deploy"]
        document = json.loads(Path(ref["path"]).read_text())
        document["attempt_id"] = "other"
        Path(ref["path"]).write_text(json.dumps(document))
        self.assertNotEqual(self.run_stage("boot")["status"], "passed")
        self.assertNotIn("off", self.runtime.calls)

    def test_login_failure_persists_lifecycle_failure_and_fault_recovery(self):
        self.through("deploy")
        original = self.fake_session
        def fail(console, mode, *args, **kwargs):
            if mode == "customer":
                raise ValueError("合成登入失敗")
            return original(console, mode, *args, **kwargs)
        self.establish.side_effect = fail
        self.assertEqual(self.run_stage("boot")["status"], "failed")
        state = life._read_state(self.runtime.lock_root)
        self.assertEqual(state["status"], "failed")
        result = self.run_stage("recovery")
        self.assertEqual(result["status"], "passed", result)
        lifecycle = json.loads(Path(result["lifecycle"]["path"]).read_text())
        self.assertTrue(lifecycle["forced_poweroff"])
        self.assertFalse(lifecycle["filesystem_integrity_after_powerloss_verified"])

    def test_boot_cannot_pass_on_login_prompt_without_session(self):
        self.through("deploy")
        self.establish.side_effect = lambda *args, **kwargs: {"strict_ssh_verified": False}
        result = self.run_stage("boot")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("customer_kernel_verified", result)

    def test_smoke_failure_keeps_failure_and_normal_recovery(self):
        self.through("boot")
        self.smoke.return_value = {"ok": False}
        result = self.run_stage("smoke")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("checks", result)
        self.assertEqual(self.run_stage("recovery")["status"], "passed")

    def test_recovery_requires_final_readonly_media_proof_after_lifecycle(self):
        self.through("smoke")
        self.readonly.side_effect = ValueError("合成救援媒體錯配")
        result = self.run_stage("recovery")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("rescue_verified", result)
        self.assertEqual(life._read_state(self.runtime.lock_root)["status"], "rescue")
        self.assertEqual(self.state()["status"], "failed")

    def test_resume_rechecks_boot_after_backup_and_media_validation(self):
        self.through("deploy")
        self.recheck.side_effect = ValueError("合成期間重啟")
        result = self.run_stage("preflight", self.resume("boot"))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["needs_recovery"])
        self.assertNotIn("resume_state_verified", result)

    def test_resume_boot_rechecks_full_deployed_range_and_preserves_next_stage(self):
        self.through("deploy")
        result = self.run_stage("preflight", self.resume("boot"))
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["resume_state_verified"])
        self.assertTrue(self.readonly.call_args.kwargs["verify_deployed"])
        self.assertEqual(self.state()["next_stage"], "boot")
        self.assertEqual(self.run_stage("boot")["status"], "passed")

    def test_resume_smoke_rebinds_current_customer_and_keeps_boot_identity(self):
        self.through("boot")
        result = self.run_stage("preflight", self.resume("smoke"))
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(self.establish.call_args.kwargs["expected_boot_id"], "customer-offline-boot")
        self.assertEqual(self.establish.call_args.args[1], "customer")

    def test_resume_rejects_missing_cross_attempt_modified_and_future_reports(self):
        self.through("deploy")
        resume = self.resume("boot")
        for value in ({"next_stage": "boot", "previous_reports": []},
                      {**resume, "next_stage": "smoke"},
                      {**resume, "previous_reports": [resume["previous_reports"][0]]}):
            count = self.runtime.opened
            result = self.run_stage("preflight", value)
            self.assertNotEqual(result["status"], "passed")
            self.assertEqual(self.runtime.opened, count)

    def test_uncatchable_deploy_interruption_stays_quarantined_without_power(self):
        self.through("preflight")
        state = {**self.state(), "status": "running", "inflight": "deploy"}
        adapter._write_state(self.runtime.lock_root, state)
        count = self.runtime.opened
        result = self.run_stage("recovery")
        self.assertNotEqual(result["status"], "passed")
        self.assertEqual(self.state(), state)
        self.assertEqual(self.runtime.opened, count)

    def test_interrupted_lifecycle_is_durably_reconciled_then_fault_recovered(self):
        self.through("deploy")
        bound = session.binding(self.request)
        prior = self.root / "killed-lifecycle"
        prior.mkdir()
        state = {"session_id": hashlib.sha256(adapter.station._json_bytes(bound)).hexdigest(),
                 "config_sha256": self.config["lifecycle"]["sha256"], "simulated": False,
                 "binding": bound, "status": "running", "action": "boot-customer",
                 "report_path": str(prior / "report.json")}
        life._write_state(self.runtime.lock_root, state)
        result = self.run_stage("recovery")
        self.assertEqual(result["status"], "passed", result)
        interruption = Path(result["evidence_path"]) / "interruption" / "report.json"
        proof = json.loads(interruption.read_text())
        self.assertTrue(proof["hardware_state_unknown"])
        self.assertFalse(proof["hardware_access_started"])
        self.assertEqual(proof["error_code"], "interrupted")

    def test_lifecycle_success_before_stage_publication_can_only_recover(self):
        self.through("deploy")
        before = self.state()
        self.assertEqual(self.run_stage("boot")["status"], "passed")
        adapter._write_state(self.runtime.lock_root, {**before, "status": "running", "inflight": "boot"})
        self.assertNotEqual(self.run_stage("smoke")["status"], "passed")
        result = self.run_stage("recovery")
        self.assertEqual(result["status"], "passed", result)
        self.assertNotIn("boot", self.state()["completed"])
        self.assertIn("systemctl poweroff\n", self.runtime.raw.sent)

    def test_recovery_lifecycle_success_before_stage_publication_reprobes_rescue(self):
        self.through("smoke")
        before = self.state()
        self.assertEqual(self.run_stage("recovery")["status"], "passed")
        adapter._write_state(self.runtime.lock_root, {**before, "status": "running", "inflight": "recovery"})
        off_count = self.runtime.calls.count("off")
        result = self.run_stage("recovery")
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(self.runtime.calls.count("off"), off_count)

    def test_stage_lost_publication_never_allows_recovery_or_new_work(self):
        self.through("preflight")
        life._save(self.runtime.lock_root, adapter.PUBLICATION_FILE, {"unknown": True})
        previous = (self.runtime.lock_root / adapter.STATE_FILE).read_bytes()
        count = self.runtime.opened
        for stage in ("deploy", "recovery", "preflight"):
            report = self.run_stage(stage)
            self.assertNotEqual(report["status"], "passed")
            self.assertTrue(report["needs_recovery"])
        self.assertEqual(self.runtime.opened, count)
        self.assertEqual((self.runtime.lock_root / adapter.STATE_FILE).read_bytes(), previous)
        self.assertTrue((self.runtime.lock_root / adapter.PUBLICATION_FILE).exists())

    def test_lifecycle_lost_publication_also_blocks_data_stage(self):
        self.through("preflight")
        life._save(self.runtime.lock_root, life.PUBLICATION_FILE, {"unknown": True})
        report = self.run_stage("deploy")
        self.assertNotEqual(report["status"], "passed")
        self.assertTrue(report["needs_recovery"])
        self.writer.assert_not_called()

    def test_pending_without_stage_state_still_requires_recovery_without_touching_marker(self):
        life._new_directory(self.runtime.lock_root)
        marker = self.runtime.lock_root / adapter.PUBLICATION_FILE
        marker.symlink_to(self.root / "must-not-open")
        report = self.run_stage("preflight")
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["needs_recovery"])
        self.assertEqual(self.runtime.opened, 0)
        self.assertTrue(marker.is_symlink())
        self.assertFalse((self.runtime.lock_root / adapter.STATE_FILE).exists())

    def test_pending_cannot_clear_interrupted_deploy_writer_state(self):
        self.through("preflight")
        adapter._write_state(self.runtime.lock_root, {**self.state(), "status": "running", "inflight": "deploy"})
        previous = (self.runtime.lock_root / adapter.STATE_FILE).read_bytes()
        life._save(self.runtime.lock_root, adapter.PUBLICATION_FILE, {"unknown": True})
        count = self.runtime.opened
        for _ in range(2):
            result = self.run_stage("recovery")
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["needs_recovery"])
            self.assertEqual((self.runtime.lock_root / adapter.STATE_FILE).read_bytes(), previous)
        self.assertEqual(self.runtime.opened, count)
        self.assertNotIn("off", self.runtime.calls)

    def test_same_attempt_lifecycle_operation_without_stage_state_requires_recovery(self):
        life._new_directory(self.runtime.lock_root)
        state = {"binding": session.binding(self.request), "status": "running"}
        life._save(self.runtime.lock_root, life.STATE_FILE, state)
        report = self.run_stage("preflight")
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["needs_recovery"])
        self.assertEqual(self.runtime.opened, 0)
        self.assertEqual(life._read_state(self.runtime.lock_root), state)

    def test_stage_state_fsync_failure_preserves_publication_barrier(self):
        original = adapter._write_state
        def fail(root, state):
            original(root, state)
            if state.get("status") == "passed":
                raise OSError("合成最終同步失敗")
        with patch.object(adapter, "_write_state", side_effect=fail):
            result = self.run_stage("preflight")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["needs_recovery"])
        self.assertTrue((self.runtime.lock_root / adapter.PUBLICATION_FILE).exists())
        self.assertNotEqual(self.run_stage("recovery")["status"], "passed")

    def test_lost_publication_cannot_hide_recovery_flag_when_failure_save_also_fails(self):
        original = adapter.save_json
        def fail(root, name, value):
            if name in ("stage.json", "failure.json"):
                raise OSError("合成發布媒體無法寫入")
            return original(root, name, value)
        with patch.object(adapter, "save_json", side_effect=fail):
            result = self.run_stage("preflight")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["needs_recovery"])
        self.assertTrue(result["failure_publication_failed"])
        self.assertTrue((self.runtime.lock_root / adapter.PUBLICATION_FILE).exists())
        self.assertNotEqual(self.run_stage("recovery")["status"], "passed")

    def test_keyboard_interrupt_is_failed_not_passed_and_secrets_not_exposed(self):
        self.establish.side_effect = KeyboardInterrupt("不可公開的診斷")
        result = self.run_stage("preflight")
        self.assertEqual(result["error_code"], "interrupted")
        self.assertTrue(result["needs_recovery"])
        self.assertEqual(self.state()["status"], "failed")
        self.assertNotIn("不可公開", json.dumps(result))

    def test_same_work_new_attempt_requires_completed_recovery(self):
        self.through("recovery")
        self.request["attempt_id"] = "attempt-2"
        self.assertEqual(self.run_stage("preflight")["status"], "passed")
        self.assertEqual(self.state()["binding"]["attempt_id"], "attempt-2")


if __name__ == "__main__":
    unittest.main()
