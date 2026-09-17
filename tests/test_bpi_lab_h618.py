"""階段接線的離線回歸；禁止實際 SSH、UART、電源及媒體操作。"""

import copy
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_h618 as adapter


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.component = {
            "schema": "bpi-h618-customer-components-v1", "board": "bananapim4zeroemac",
            "os": "noble", "desktop": "minimal", "kernel_release": "6.18.49-current-sunxi64",
            "root_uuid": "62cd58de-498d-4325-8528-96a15da7d902",
            "preflight": {"source_verified": True},
            "image": {"path": str(self.root / "source.img.xz"),
                      "compressed": {"bytes": 128, "sha256": "a" * 64},
                      "raw": {"bytes": 4096, "sha256": "b" * 64}},
        }
        self.config = {
            "schema": "bpi-lab-h618-v1", "station_id": "offline-0845", "hardware_id": "bpi-m4zero-0845",
            "test_version": "basic-v1", "dependencies": {
                name: sha((Path(adapter.__file__).parent / name).read_bytes()) for name in adapter.DEPENDENCIES},
            "backup": self.reference("manifest.json", b"{}"),
            "authorization": {"userarea_write": True, "hardware_id": "bpi-m4zero-0845",
                "media_identity": "cid:" + adapter.boot.EXPECTED["cid"], "record": "純離線替身，無硬體授權"},
            "sd_prefix": {"bytes": 4194304, "sha256": "c" * 64},
            "rescue_identity_sha256": "d" * 64,
            "rescue_ssh": {"config": self.reference("rescue-ssh", b"Host offline\n"), "alias": "offline"},
            "customer_ssh": {"config": self.reference("customer-ssh", b"Host offline\n"), "alias": "offline"},
            "components": {"a" * 64: self.reference("components.json", json.dumps(self.component).encode())},
            "output_root": str(self.root), "timeout_seconds": 120,
        }
        self.request = {"schema": "bpi-lab-request-v1", "work_key": "f" * 64, "attempt_id": "attempt-1",
                        "stage": "preflight", "station_id": "offline-0845", "hardware_id": "bpi-m4zero-0845",
                        "image_sha256": "a" * 64, "boot_config_sha256": "e" * 64, "test_version": "basic-v1",
                        "mode": "hardware", "image_root": str(self.root), "image": {
                            "board": "bpi-m4z-emac", "relative_path": "source.img.xz", "compressed_bytes": 128,
                            "release": "noble", "variant": "minimal", "expected_sha256": "a" * 64}}
        self.no_process = mock.patch.object(adapter.backup.subprocess, "Popen", side_effect=AssertionError("不得啟動程序"))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def reference(self, name, blob):
        path = self.root / name
        path.write_bytes(blob)
        return {"path": str(path), "sha256": sha(blob)}

    def run_stage(self, stage, **kwargs):
        self.request["stage"] = stage
        with mock.patch.object(adapter, "load_config", return_value=self.config):
            return adapter.run_stage(self.root / "config.json", "e" * 64, self.request, **kwargs)

    def test_config_dependencies_and_small_references(self):
        self.assertIs(adapter.check_config(self.config), self.config)

    def test_config_rejects_other_board_and_authorization(self):
        for change in ("hardware", "auth", "dependency", "prefix", "backup", "unknown"):
            with self.subTest(change=change):
                config = copy.deepcopy(self.config)
                if change == "hardware":
                    config["hardware_id"] = "bpi-m4berry-other"
                elif change == "auth":
                    config["authorization"]["userarea_write"] = False
                elif change == "dependency":
                    config["dependencies"]["bpi_lab_h618.py"] = "0" * 64
                elif change == "prefix":
                    config["sd_prefix"]["bytes"] = 512
                elif change == "backup":
                    config["backup"]["sha256"] = "0" * 64
                else:
                    config["unknown"] = True
                with self.assertRaises(ValueError):
                    adapter.check_config(config)

    def test_selected_component_is_bound_to_path_hash_release_role(self):
        self.assertEqual(adapter.selected_components(self.config, self.request)[0], self.component)
        for key, value in (("board", "bpi-m4b"), ("relative_path", "../source.img.xz"),
                           ("release", "jammy"), ("variant", "xfce_desktop"), ("compressed_bytes", 127)):
            request = copy.deepcopy(self.request)
            request["image"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                adapter.selected_components(self.config, request)

    def test_unimplemented_and_resume_are_blocked_without_transport(self):
        for stage in ("boot", "recovery"):
            result = self.run_stage(stage)
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["hardware_validated"])
        self.request["resume"] = {"next_stage": "smoke", "previous_reports": []}
        self.assertEqual(self.run_stage("preflight")["status"], "blocked")

    def test_simulation_cannot_invoke_hardware(self):
        self.request["mode"] = "simulation"
        self.assertEqual(self.run_stage("deploy")["status"], "blocked")

    def test_binding_mismatch_before_any_transport(self):
        self.request["boot_config_sha256"] = "0" * 64
        self.assertEqual(self.run_stage("deploy")["status"], "blocked")

    def test_preflight_and_repeated_attempt_does_not_overwrite(self):
        with mock.patch.object(adapter.deploy, "validate_backup", return_value=self.config["backup"]), \
                mock.patch.object(adapter, "readonly_preflight", return_value={"remote": {}}):
            result = self.run_stage("preflight")
            self.assertEqual(result["status"], "passed")
            path = Path(result["evidence_path"]) / "stage.json"
            original = path.read_bytes()
            self.assertEqual(self.run_stage("preflight")["status"], "blocked")
            self.assertEqual(path.read_bytes(), original)

    def test_preflight_failure_prevents_deploy(self):
        with mock.patch.object(adapter.deploy, "validate_backup", return_value=self.config["backup"]), \
                mock.patch.object(adapter, "readonly_preflight", side_effect=ValueError("媒體不符")), \
                mock.patch.object(adapter.deploy, "deploy") as writer:
            result = self.run_stage("deploy")
        self.assertEqual(result["status"], "failed")
        writer.assert_not_called()
        self.assertFalse(result["hardware_validated"])
        self.assertTrue((Path(result["evidence_path"]) / "stage.json").exists())

    def test_real_deploy_api_arguments_and_success_receipt_validation(self):
        receipt = {"request": {"backup_manifest_sha256": self.config["backup"]["sha256"]},
                   "remote_state": {"sd_before": {"prefix": self.config["sd_prefix"]}}}
        with mock.patch.object(adapter.deploy, "validate_backup", return_value=self.config["backup"]), \
                mock.patch.object(adapter, "readonly_preflight", return_value={"remote": {}}), \
                mock.patch.object(adapter.deploy, "deploy", return_value=receipt) as writer, \
                mock.patch.object(adapter.boot, "validate_metadata") as validator:
            result = self.run_stage("deploy")
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["full_readback_verified"])
        self.assertEqual(writer.call_args.kwargs["expected_cid"], adapter.boot.EXPECTED["cid"])
        self.assertEqual(writer.call_args.kwargs["source"], self.component["image"]["path"])
        pins = writer.call_args.kwargs["pinned_preflight"]
        self.assertEqual(pins["ssh_config_sha256"], self.config["rescue_ssh"]["config"]["sha256"])
        self.assertEqual(pins["sd_prefix"], self.config["sd_prefix"])
        self.assertEqual(pins["rescue"]["identity_sha256"], self.config["rescue_identity_sha256"])
        validator.assert_called_once_with(self.component, receipt)

    def test_smoke_failure_is_not_passed(self):
        with mock.patch.object(adapter.smoke, "smoke", return_value={"ok": False}):
            result = self.run_stage("smoke")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("checks", result)

    def test_readonly_program_never_calls_deploy_entrypoint(self):
        program = adapter.readonly_program()
        self.assertTrue(program.startswith("__name__ = '_bpi_readonly_preflight'"))
        compile(program, "<唯讀預檢>", "exec")
        namespace = {}
        # 在 request 載入時停止；若誤呼叫部署 main，便會讀 stdin 或存取裝置。
        with mock.patch.object(sys, "argv", ["probe"]), \
                mock.patch("os.open", side_effect=AssertionError("不得存取設備")):
            with self.assertRaises(IndexError):
                exec(program, namespace)
        self.assertIn("rescue_identity", namespace)

    def test_readonly_response_requires_nonce_and_pinned_identity(self):
        def transport(argv, deadline, clock):
            request = json.loads(shlex.split(argv[-1])[-1])
            yield "stdout", json.dumps({"nonce": request["nonce"], "state": {}}).encode()
            yield "exit", 0
        out = self.root / "readonly"
        out.mkdir()
        with self.assertRaises(ValueError):
            adapter.readonly_preflight(self.config, self.component, out, lambda: 30, transport)
        self.assertTrue((out / "ssh-stdout.log").exists())

    def state(self):
        return {
            "range": {"start": 0, "end_exclusive": 4096}, "bootable": False,
            "boot_selected": False, "boot_verified": False, "bytes_written": 0, "attempted_end": 0,
            "identity": {**adapter.boot.EXPECTED, "type": "MMC", "device": "/dev/mmcblk8",
                         "devnum": "179:64", "mounted": False, "swap": False, "holders": False},
            "rescue": {"schema": "bpi-h618-rescue-v1", "kernel": adapter.boot.rescue.KERNEL,
                       "root_ram": True, "root_fs": "tmpfs", "identity_sha256": "d" * 64},
            "sd_before": {"identity": {**adapter.boot.PROTECTED_SD, "type": "SD", "devnum": "179:0"},
                          "prefix": self.config["sd_prefix"]},
        }

    def test_readonly_success_and_wrong_pin_nonce_kernel_or_media(self):
        for case in ("valid", "nonce", "kernel", "sd", "cid", "mounted", "ram", "code", "missing_exit"):
            with self.subTest(case=case):
                state = self.state()
                if case == "kernel":
                    state["rescue"]["kernel"] = "wrong"
                elif case == "sd":
                    state["sd_before"]["prefix"] = {"bytes": 4194304, "sha256": "0" * 64}
                elif case == "cid":
                    state["identity"]["cid"] = "0" * 32
                elif case == "mounted":
                    state["identity"]["mounted"] = True
                elif case == "ram":
                    state["rescue"]["root_fs"] = "ext4"
                def transport(argv, deadline, clock):
                    request = json.loads(shlex.split(argv[-1])[-1])
                    nonce = "wrong" if case == "nonce" else request["nonce"]
                    yield "stdout", json.dumps({"nonce": nonce, "state": state}).encode()
                    if case != "missing_exit":
                        yield "exit", 1 if case == "code" else 0
                out = self.root / case
                out.mkdir()
                if case == "valid":
                    result = adapter.readonly_preflight(self.config, self.component, out, lambda: 30, transport)
                    self.assertEqual(result["remote"]["state"], state)
                else:
                    with self.assertRaises(ValueError):
                        adapter.readonly_preflight(self.config, self.component, out, lambda: 30, transport)

    def test_interrupted_transport_keeps_partial_evidence(self):
        def transport(argv, deadline, clock):
            yield "stdout", b'{"nonce":'
            yield "stderr", "連線中斷前的診斷".encode()
            raise OSError("測試中斷")
        out = self.root / "interrupted"
        out.mkdir()
        with self.assertRaises(OSError):
            adapter.readonly_preflight(self.config, self.component, out, lambda: 30, transport)
        self.assertEqual((out / "ssh-stdout.log").read_bytes(), b'{"nonce":')
        self.assertEqual((out / "ssh-stderr.log").read_text(), "連線中斷前的診斷")

    def test_oversized_transport_is_bounded_and_not_passed(self):
        def transport(argv, deadline, clock):
            yield "stdout", b"x" * 70000
            yield "exit", 0
        out = self.root / "oversized"
        out.mkdir()
        with self.assertRaises(ValueError):
            adapter.readonly_preflight(self.config, self.component, out, lambda: 30, transport)
        self.assertEqual((out / "ssh-stdout.log").stat().st_size, 65536)

    def test_symlink_and_changed_manifest_rejected(self):
        ref = self.config["backup"]
        original = Path(ref["path"])
        alias = self.root / "symlink.json"
        alias.symlink_to(original)
        with self.assertRaises((ValueError, OSError)):
            adapter.checked_file({**ref, "path": str(alias)})
        original.write_bytes(b"{ }")
        with self.assertRaises(ValueError):
            adapter.checked_file(ref)


if __name__ == "__main__":
    unittest.main()
