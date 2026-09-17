"""0845 生命週期的離線回歸；替身不構成硬體證據，嚴禁真實傳輸。"""

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_h618_lifecycle as life
from test_bpi_h618_customer_boot import fixtures


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


class FakeConsole:
    """回覆真實 boot 所需的格式；所有傳送皆僅記入記憶體。"""

    def __init__(self, runtime, components):
        self.runtime, self.components = runtime, components
        self.timeout = 0.01
        self.sent, self.shells = [], []
        self.last_command = ""
        self.source = "rescue"
        self.overrides = {}
        self.halt_timeout = False
        self.inventory = {"schema": "bpi-h618-rescue-v1", "devices": [
            {"device/cid": life.rescue.SD_CID, "device/type": "SD"},
            {"device/cid": life.customer.EXPECTED["cid"], "device/type": "MMC"}],
            "mountinfo": "1 0 0:1 / / rw - tmpfs tmpfs rw\n", "swaps": "Filename Type Size Used Priority\n"}

    def read(self, size):
        self.runtime.now += self.timeout
        return b""

    def send(self, wire, timeout=10):
        self.sent.append(wire)
        if isinstance(wire, str) and wire.startswith("if "):
            self.last_command = wire.split("; then echo ")[0][3:]
        return len(wire)

    def expect_literal(self, literal, timeout):
        self.runtime.now += min(0.01, timeout)
        return SimpleNamespace(before=b"", matched=literal, groups=())

    def expect_regex(self, pattern, timeout):
        self.runtime.now += min(0.01, timeout)
        if b"Power down" in pattern and self.halt_timeout:
            self.runtime.now += timeout
            raise TimeoutError("替身關機逾時")
        before, groups = b"", ()
        if b"BPI_RESCUE_READY" in pattern:
            self.source = "rescue"
            groups = (json.dumps({"schema": "bpi-h618-rescue-v1", "kernel": life.rescue.KERNEL}).encode(),)
        elif b"Linux version" in pattern:
            self.source = "customer"
            groups = (self.components["kernel_release"].encode(),)
        elif b" login:" in pattern:
            groups = (b"bananapim4zeroemac",)
        elif b"_(OK|FAIL)" in pattern:
            groups, before = (b"OK",), self.uboot_response().encode()
        return SimpleNamespace(before=before, matched=b"", groups=groups)

    def uboot_response(self):
        command = self.last_command
        if command == "mmc list":
            return "mmc@4020000: 0\nmmc@4022000: 1 (eMMC)\n"
        if command == "mmc info":
            return "Device: mmc@4022000\nMMC version 5.1\n"
        if command == "part uuid mmc 1:1":
            return self.components["partuuid"] + "\n"
        if command.startswith("load "):
            item = self.item_for_address(int(command.split()[3], 16), command.split()[2])
            return f"{item['bytes']} bytes read in 1 ms\n"
        if command.startswith("crc32 "):
            address = int(command.split()[1], 16)
            item = self.item_for_address(address, self.runtime.target_media)
            return f"CRC32 for {address:x} ... {address + item['bytes'] - 1:x} ==> {item['crc32']}\n"
        if command.startswith("fdt print "):
            status = "disabled" if "4020000" in command else "okay"
            return 'status = "' + status + '";\n'
        return ""

    def item_for_address(self, address, media):
        if media == "1:1":
            name = next(name for name, start, _ in life.customer.LOADS if start == address)
            return self.components["files"][name]
        return self.runtime.inputs[life.rescue.ADDRESSES.index(address)]

    def run_shell(self, command, timeout=10):
        self.shells.append(command)
        self.runtime.now += min(0.01, timeout)
        if command in self.overrides:
            return self.overrides[command]
        if command == "uname -r":
            data = (life.rescue.KERNEL if self.source == "rescue" else self.components["kernel_release"]).encode()
        elif command == "sha256sum /etc/bpi-rescue.json":
            data = b"d" * 64 + b"  /etc/bpi-rescue.json\n"
        elif command == "bpi-rescue inventory":
            data = json.dumps(self.inventory).encode()
        elif command.startswith("pgrep "):
            return SimpleNamespace(output=b"", exitcode=1)
        elif command == "cat /sys/class/block/mmcblk*/device/cid":
            data = life.customer.EXPECTED["cid"].encode()
        elif command == "findmnt -n -o SOURCE /":
            data = b"/dev/mmcblk2p1"
        elif command == "findmnt -n -o UUID /":
            data = self.components["root_uuid"].encode()
        elif command == "readlink -f /sys/class/block/mmcblk2":
            data = (life.customer.EXPECTED["controller"] + "/mmc_host/mmc2/mmc2:0001/block/mmcblk2").encode()
        elif command.startswith("python3 -B -c "):
            data = json.dumps(self.runtime.prefix).encode()
        else:
            raise AssertionError("替身收到未核定命令")
        return SimpleNamespace(output=data, exitcode=0)


class FakeRuntime:
    def __init__(self, root, components, manifest):
        self.lock_root, self.now = root / "locks", 0.0
        self.inputs, self.prefix = manifest["inputs"], manifest["sd_prefix"]
        self.raw = FakeConsole(self, components)
        self.calls, self.opened, self.closed = [], 0, 0
        self.on = True
        self.bad_power, self.fail_action = None, None
        self.target_media = "0:1"

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def validate_uart(self, uart):
        self.calls.append("uart-pairing")
        return 1

    @contextmanager
    def console(self, uart, output, deadline):
        self.opened += 1
        try:
            yield self.raw
        finally:
            self.closed += 1

    def power(self, action, deadline):
        self.calls.append(action)
        deadline.remaining()
        if action == self.fail_action:
            raise RuntimeError("測試用診斷不得外洩")
        if action in ("on", "off"):
            self.on = action == "on"
        device = {**life.POWER, "identity_verified": True, "on": self.on}
        if self.bad_power:
            device.update(self.bad_power)
        return {"ok": True, "verified": True, "device": device, "ignored_secret": "測試用診斷不得外洩"}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.components, self.receipt = fixtures()
        self.manifest = {"inputs": [{"path": path, "bytes": 1024, "sha256": "a" * 64, "crc32": "12345678"}
                                    for path in life.rescue.PATHS],
                         "sd_prefix": self.receipt["remote_state"]["sd_after"]["prefix"]}
        self.runtime = FakeRuntime(self.root, self.components, self.manifest)
        self.config = {"schema": "bpi-lab-h618-lifecycle-v1", "station_id": "offline-0845",
            "hardware_id": life.HARDWARE, "power": copy.deepcopy(life.POWER),
            "uart": {"stable_path": "/dev/serial/by-id/usb-offline-0845", "device": "/dev/ttyUSB0", "baud": 115200},
            "pairing": {}, "authorization": {"record": "offline-only", "normal_shutdown": True,
                "fault_poweroff": True, "customer_boot_may_write_emmc": True},
            "bridge": self.reference("bridge.bin", b"offline-bridge"),
            "rescue_inputs": self.reference("rescue.json", self.manifest),
            "rescue_identity_sha256": "d" * 64, "components": self.reference("components.json", self.components),
            "deploy_receipt": self.reference("receipt.json", self.receipt), "timeout_seconds": 600,
            "off_seconds": 10, "ssh_policy": copy.deepcopy(life.SSH_POLICY),
            "dependencies": {name: sha((Path(life.__file__).parent / name).read_bytes()) for name in life.DEPENDENCIES}}
        self.config["pairing"] = {"approved": True, "record": "offline-only", "hardware_id": life.HARDWARE,
            "uart": copy.deepcopy(self.config["uart"]), "power": copy.deepcopy(life.POWER),
            "emmc": copy.deepcopy(life.customer.EXPECTED), "sd": copy.deepcopy(life.customer.PROTECTED_SD)}
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.no_serial = stack.enter_context(patch.object(life.rescue, "open_serial", side_effect=AssertionError("禁止真 UART")))
        self.no_process = stack.enter_context(patch.object(life.customer.deploy.backup.subprocess, "Popen",
                                                         side_effect=AssertionError("禁止真 SSH／電源")))
        stack.enter_context(patch.object(life.rescue, "BRIDGE_SHA", sha(b"offline-bridge")))
        self.supervisor = stack.enter_context(patch.object(life.rescue, "LabSupervisorSession"))
        self.supervisor.return_value.probe.return_value = {"abi": 3}
        self.supervisor.return_value.run.return_value = {"ran": True}
        self.number = 0

    def tearDown(self):
        self.no_serial.assert_not_called()
        self.no_process.assert_not_called()

    def reference(self, name, value):
        blob = value if isinstance(value, bytes) else json.dumps(value).encode()
        path = self.root / name
        path.write_bytes(blob)
        return {"path": str(path), "sha256": sha(blob)}

    def start(self, action="cold-cycle", **kwargs):
        self.number += 1
        reference = self.reference("config.json", self.config)
        self.output = self.root / f"run-{self.number}"
        self.runtime.target_media = "1:1" if action == "boot-customer" else "0:1"
        return life.run(config_path=reference["path"], config_sha256=reference["sha256"],
                        action=action, session_id=kwargs.pop("session_id", "session-1"), output=self.output,
                        runtime=self.runtime, simulated=True, **kwargs)

    def failed_reference(self):
        path = self.output / "report.json"
        return {"path": str(path), "sha256": sha(path.read_bytes())}

    def state(self):
        return json.loads((self.runtime.lock_root / life.STATE_FILE).read_text())

    def test_cold_cycle_calls_real_rescue_boot_and_is_not_hardware_qualification(self):
        result = self.start()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "ram_rescue_verified")
        self.assertTrue(result["normal_shutdown_verified"])
        self.assertTrue(result["sd_prefix_unchanged"])
        self.assertGreaterEqual(result["off_seconds"], 10)
        self.assertEqual(self.runtime.calls, ["uart-pairing", "status", "off", "status", "on"])
        self.supervisor.return_value.begin_load.assert_called_once_with("S", 3)
        for key in ("hardware_validated", "whole_adapter_ready", "strict_ssh_verified", "recovery_verified",
                    "first_login_initialized", "system_verified", "block_write_commands_sent"):
            self.assertIs(result[key], False)
        self.assertEqual(self.state()["status"], "rescue")

    def test_customer_boot_calls_real_boot_but_stops_at_login(self):
        result = self.start("boot-customer")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["login_observed"])
        self.assertFalse(result["first_login_initialized"])
        self.assertFalse(result["system_verified"])
        self.assertEqual(self.state()["status"], "customer")
        self.assertIn(b"booti 40080000 4ff00000 4fa00000\n", [x.encode() if isinstance(x, str) else x for x in self.runtime.raw.sent])
        self.assertNotIn("root\n", self.runtime.raw.sent)

    def test_normal_return_uses_current_customer_identity(self):
        self.assertTrue(self.start("boot-customer")["ok"])
        result = self.start("recover-normal")
        self.assertTrue(result["ok"], result)
        self.assertIn("systemctl poweroff\n", self.runtime.raw.sent)
        self.assertIn("findmnt -n -o UUID /", self.runtime.raw.shells)
        self.assertEqual(self.state()["status"], "rescue")

    def test_success_trace_is_private_durable_and_contains_no_write_commands(self):
        self.assertTrue(self.start()["ok"])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        trace = json.loads((self.output / "boot-trace.json").read_text())
        commands = [item["command"] for item in trace["commands"]]
        self.assertTrue(any(item.startswith("load mmc 0:1 ") for item in commands))
        self.assertFalse(any(re.search(r"mmc (?:write|erase)|saveenv|^source |^reset", item) for item in commands))
        events = [json.loads(p.read_text()) for p in sorted(self.output.glob("event-*.json"))]
        self.assertEqual(events[0]["step"], "preflight")
        self.assertEqual(events[-1]["step"], "complete")

    def test_configuration_rejections_have_zero_transport(self):
        baseline = copy.deepcopy(self.config)
        mutations = (lambda c: c.update(hardware_id="bpi-m4zero-0438"),
            lambda c: c["uart"].update(device="/dev/ttyUSB1"),
            lambda c: c["uart"].update(stable_path="/dev/ttyUSB0"),
            lambda c: c["uart"].update(baud=True),
            lambda c: c["power"].update(ip="192.168.50.244"),
            lambda c: c["power"].update(mac="00:00:00:00:00:00"),
            lambda c: c["pairing"].update(approved=False),
            lambda c: c["pairing"]["emmc"].update(cid="0" * 32),
            lambda c: c["authorization"].update(normal_shutdown=False),
            lambda c: c["authorization"].update(normal_shutdown=1),
            lambda c: c["ssh_policy"].update(reuse_previous_session=True),
            lambda c: c["ssh_policy"].update(strict_host_key_checking=False),
            lambda c: c.update(customer_ssh={"config": "old-session"}),
            lambda c: c.update(timeout_seconds=0), lambda c: c.update(off_seconds=9),
            lambda c: c["dependencies"].update(bpi_lab_console="0" * 64))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.config = copy.deepcopy(baseline)
                mutate(self.config)
                result = self.start()
                self.assertFalse(result["ok"])
                self.assertTrue((self.output / "failure.json").exists())
                self.assertEqual(self.runtime.calls, [])

    def test_all_pinned_hashes_and_dependency_drift_rejected(self):
        for key in ("bridge", "rescue_inputs", "components", "deploy_receipt"):
            old = self.config[key]["sha256"]
            self.config[key]["sha256"] = "0" * 64
            self.assertFalse(self.start()["ok"])
            self.config[key]["sha256"] = old
        self.config["dependencies"]["bpi_lab_console.py"] = "0" * 64
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_rescue_manifest_and_receipt_must_share_sd_prefix(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["sd_prefix"]["sha256"] = "f" * 64
        self.config["rescue_inputs"] = self.reference("wrong-rescue.json", manifest)
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_rescue_duplicate_extra_and_injection_rejected_before_transport(self):
        for mutate in (lambda d: d["inputs"].append(d["inputs"][0]),
                       lambda d: d["inputs"][0].update(path="/boot/evil;reset"),
                       lambda d: d["inputs"][0].update(bytes=True)):
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            self.config["rescue_inputs"] = self.reference("wrong-rescue.json", manifest)
            self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_symlink_input_and_output_cannot_be_used(self):
        path = Path(self.config["bridge"]["path"])
        path.rename(self.root / "original.bin")
        path.symlink_to(self.root / "original.bin")
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])
        reference = self.reference("config.json", self.config)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            life.run(config_path=reference["path"], config_sha256=reference["sha256"], action="cold-cycle",
                     session_id="session-1", output=alias / "out", runtime=self.runtime, simulated=True)

    def test_duplicate_json_and_device_input_rejected_without_reading_device(self):
        for blob in (b'{"schema":1,"schema":2}', b" " * 65537):
            ref = self.reference("invalid.json", blob)
            with self.assertRaises(ValueError):
                life.load_config(ref["path"], ref["sha256"])
        with self.assertRaises(ValueError):
            life.checked_bytes({"path": "/dev/null", "sha256": "0" * 64})

    def test_customer_boot_requires_emmc_side_effect_authorization(self):
        self.config["authorization"]["customer_boot_may_write_emmc"] = False
        self.assertFalse(self.start("boot-customer")["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_fault_requires_separate_authorization_and_current_failure(self):
        self.config["authorization"]["fault_poweroff"] = False
        self.assertFalse(self.start("recover-fault")["ok"])
        self.config["authorization"]["fault_poweroff"] = True
        self.assertFalse(self.start("recover-fault")["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_power_identity_mismatch_never_shuts_down_or_cuts_power(self):
        self.runtime.bad_power = {"mac": "00:00:00:00:00:00"}
        result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(self.runtime.calls, ["uart-pairing", "status"])
        self.assertEqual(self.runtime.raw.sent, [])
        self.assertEqual(self.runtime.closed, 1)
        self.assertEqual(self.state()["status"], "failed")

    def test_shutdown_timeout_never_falls_back_to_fault(self):
        self.runtime.raw.halt_timeout = True
        result = self.start()
        self.assertFalse(result["ok"])
        self.assertNotIn("off", self.runtime.calls)
        self.assertFalse(result["forced_poweroff"])
        self.assertTrue((self.output / "report.json").exists())

    def test_active_stress_blocks_shutdown(self):
        self.runtime.raw.overrides["pgrep -x stress-ng"] = SimpleNamespace(exitcode=0, output=b"12")
        self.assertFalse(self.start()["ok"])
        self.assertNotIn("off", self.runtime.calls)
        self.assertEqual(self.runtime.raw.sent, [])

    def test_customer_root_mismatch_blocks_normal_return(self):
        self.runtime.raw.source = "customer"
        self.runtime.raw.overrides["findmnt -n -o UUID /"] = SimpleNamespace(exitcode=0, output=b"wrong")
        self.assertFalse(self.start("recover-normal")["ok"])
        self.assertNotIn("off", self.runtime.calls)

    def test_rescue_mount_swap_or_identity_mismatch_blocks_shutdown(self):
        self.runtime.raw.inventory["swaps"] += "/dev/mmcblk2p2 partition 1 0 0\n"
        self.assertFalse(self.start()["ok"])
        self.assertNotIn("off", self.runtime.calls)

    def test_mmc_mount_under_uuid_alias_blocks_direct_shutdown(self):
        self.runtime.raw.inventory["mountinfo"] += "2 1 179:17 / /mnt rw - ext4 /dev/disk/by-uuid/offline rw\n"
        self.assertFalse(self.start()["ok"])
        self.assertNotIn("off", self.runtime.calls)
        self.assertEqual(self.runtime.raw.sent, [])

    def test_post_recovery_sd_prefix_mismatch_keeps_station_failed(self):
        self.runtime.prefix = {"bytes": 4194304, "sha256": "0" * 64}
        result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(self.state()["status"], "failed")
        self.assertEqual(self.runtime.closed, 1)

    def test_fault_recovery_uses_current_failure_even_when_already_off(self):
        self.runtime.fail_action = "on"
        self.assertFalse(self.start("boot-customer")["ok"])
        reference = self.failed_reference()
        self.assertFalse(self.runtime.on)
        self.runtime.fail_action = None
        before = len(self.runtime.raw.shells)
        result = self.start("recover-fault", fault_evidence=reference)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["forced_poweroff"])
        self.assertFalse(result["normal_shutdown_verified"])
        self.assertFalse(result["filesystem_integrity_after_powerloss_verified"])
        self.assertNotIn("pgrep -x stress-ng", self.runtime.raw.shells[before:])

    def test_old_or_cross_session_failure_cannot_authorize_power(self):
        self.runtime.fail_action = "on"
        self.start()
        reference = self.failed_reference()
        self.runtime.fail_action = None
        before = list(self.runtime.calls)
        self.assertFalse(self.start("recover-fault", session_id="session-2", fault_evidence=reference)["ok"])
        wrong = {**reference, "sha256": "0" * 64}
        self.assertFalse(self.start("recover-fault", fault_evidence=wrong)["ok"])
        self.assertEqual(self.runtime.calls, before)
        self.assertTrue(self.start("recover-fault", fault_evidence=reference)["ok"])
        before = list(self.runtime.calls)
        self.assertFalse(self.start("recover-fault", fault_evidence=reference)["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_persistent_customer_and_failure_states_block_next_job(self):
        self.assertTrue(self.start("boot-customer")["ok"])
        before = list(self.runtime.calls)
        self.assertFalse(self.start("boot-customer", session_id="other-job")["ok"])
        self.assertFalse(self.start("cold-cycle")["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_incomplete_running_state_cannot_be_retried(self):
        with life.resource_lock(self.config, self.runtime.lock_root):
            life._write_state(self.runtime.lock_root, {"session_id": "session-1", "status": "running",
                "config_sha256": self.reference("config.json", self.config)["sha256"], "simulated": True})
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_lock_is_shared_across_station_names_and_released(self):
        other = copy.deepcopy(self.config)
        other["station_id"] = "other-station"
        with life.resource_lock(other, self.runtime.lock_root):
            self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])
        self.assertTrue(self.start()["ok"])

    def test_lock_symlink_and_world_readable_directory_rejected(self):
        self.runtime.lock_root.mkdir(mode=0o755)
        self.assertFalse(self.start()["ok"])
        self.runtime.lock_root.chmod(0o700)
        lock = self.runtime.lock_root / (sha(("hardware:" + life.HARDWARE).encode()) + ".lock")
        lock.symlink_to(self.root / "outside")
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_deadline_covers_all_steps_and_does_not_restart_per_io(self):
        self.config["timeout_seconds"] = 60
        self.runtime.sleep = lambda seconds: setattr(self.runtime, "now", self.runtime.now + 100)
        result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertNotIn("on", self.runtime.calls)
        self.assertEqual(self.runtime.closed, 1)

    def test_insufficient_off_time_budget_never_turns_power_on(self):
        self.config.update(timeout_seconds=60, off_seconds=60)
        result = self.start()
        self.assertFalse(result["ok"])
        self.assertNotIn("on", self.runtime.calls)

    def test_keyboard_interrupt_is_persisted_and_not_printed(self):
        with patch.object(self.runtime, "power", side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()) as out:
            result = self.start()
        self.assertEqual(result["error_code"], "interrupted")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.state()["status"], "failed")

    def test_error_and_extra_power_fields_never_leak_to_reports(self):
        self.runtime.fail_action = "on"
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            self.assertFalse(self.start()["ok"])
        saved = "".join(path.read_text() for path in self.output.glob("*.json"))
        self.assertNotIn("測試用診斷不得外洩", saved + out.getvalue() + err.getvalue())

    def test_journal_failure_stops_before_hardware_action(self):
        original = life._save
        def fail(directory, name, value):
            if value.get("step") == "power-off" and value.get("state") == "started":
                raise OSError("注入同步失敗")
            return original(directory, name, value)
        with patch.object(life, "_save", side_effect=fail):
            self.assertFalse(self.start()["ok"])
        self.assertNotIn("off", self.runtime.calls)
        self.assertEqual(self.state()["status"], "failed")

    def test_report_failure_keeps_running_lease_and_cannot_resume(self):
        original = life._save
        def fail(directory, name, value):
            if name == "report.json":
                raise OSError("注入報告同步失敗")
            return original(directory, name, value)
        with patch.object(life, "_save", side_effect=fail):
            self.assertFalse(self.start()["ok"])
        self.assertEqual(self.state()["status"], "running")
        before = list(self.runtime.calls)
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_final_lease_failure_overrides_saved_success_and_blocks_followup(self):
        original = life._write_state
        def fail(root, state):
            if state["status"] == "rescue":
                raise OSError("注入狀態同步失敗")
            return original(root, state)
        with patch.object(life, "_write_state", side_effect=fail):
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertTrue(json.loads((self.output / "report.json").read_text())["ok"])
        self.assertFalse(json.loads((self.output / "failure.json").read_text())["ok"])
        self.assertEqual(self.state()["status"], "running")
        before = list(self.runtime.calls)
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_published_rescue_with_directory_fsync_failure_blocks_other_session(self):
        original_replace, original_fsync = life.os.replace, life.os.fsync
        armed, fired = False, False
        def replace(*args, **kwargs):
            nonlocal armed
            result = original_replace(*args, **kwargs)
            if self.state()["status"] == "rescue":
                armed = True
            return result
        def fsync(fd):
            nonlocal armed, fired
            if armed and stat.S_ISDIR(life.os.fstat(fd).st_mode):
                armed, fired = False, True
                raise OSError("注入已發布 rescue 後的目錄同步失敗")
            return original_fsync(fd)
        with patch.object(life.os, "replace", side_effect=replace), patch.object(life.os, "fsync", side_effect=fsync):
            result = self.start()
        self.assertTrue(fired)
        self.assertFalse(result["ok"])
        self.assertEqual(self.state()["status"], "rescue")
        self.assertTrue((self.runtime.lock_root / life.PUBLICATION_FILE).is_file())
        with self.assertRaises(ValueError):
            life._read_state(self.runtime.lock_root)
        before = list(self.runtime.calls)
        self.assertFalse(self.start(session_id="different-session")["ok"])
        self.assertEqual(self.runtime.calls, before)

    def assert_publication_blocked(self):
        self.assertTrue((self.runtime.lock_root / life.PUBLICATION_FILE).is_file())
        with self.assertRaises(ValueError):
            life._read_state(self.runtime.lock_root)
        before = list(self.runtime.calls)
        self.assertFalse(self.start(session_id="different-session")["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_slow_report_persistence_cannot_publish_success_state(self):
        original = life._save
        def slow(directory, name, value):
            result = original(directory, name, value)
            if name == "report.json":
                self.runtime.now += 600
            return result
        with patch.object(life, "_save", side_effect=slow):
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertEqual(self.state()["status"], "running")
        self.assert_publication_blocked()

    def test_slow_published_rescue_state_cannot_release_other_session(self):
        original = life._write_state
        def slow(root, state):
            result = original(root, state)
            if state["status"] == "rescue":
                self.runtime.now += 600
            return result
        with patch.object(life, "_write_state", side_effect=slow):
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertEqual(self.state()["status"], "rescue")
        self.assert_publication_blocked()

    def test_slow_publication_guard_does_not_start_success_report(self):
        original = life._save
        def slow(directory, name, value):
            result = original(directory, name, value)
            if name == life.PUBLICATION_FILE:
                self.runtime.now += 600
            return result
        with patch.object(life, "_save", side_effect=slow):
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertFalse((self.output / "report.json").exists())
        self.assertEqual(self.state()["status"], "running")
        self.assert_publication_blocked()

    def test_slow_report_fingerprint_does_not_publish_success_state(self):
        original = life._report_sha
        def slow(output):
            result = original(output)
            self.runtime.now += 600
            return result
        with patch.object(life, "_report_sha", side_effect=slow):
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertEqual(self.state()["status"], "running")
        self.assert_publication_blocked()

    def test_uncertain_state_blocks_even_when_failure_report_cannot_be_saved(self):
        original_state, original_save = life._write_state, life._save
        def fail_state(root, state):
            original_state(root, state)
            if state["status"] == "rescue":
                raise OSError("注入最終狀態發布不確定")
        def fail_report(directory, name, value):
            if name == "failure.json":
                raise OSError("注入失敗報告也無法同步")
            return original_save(directory, name, value)
        with patch.object(life, "_write_state", side_effect=fail_state), \
                patch.object(life, "_save", side_effect=fail_report), self.assertRaises(OSError):
            self.start()
        self.assertEqual(self.state()["status"], "rescue")
        self.assertFalse((self.output / "failure.json").exists())
        self.assert_publication_blocked()

    def test_guard_unlink_failure_keeps_published_rescue_blocked(self):
        original = life.os.unlink
        def fail(path, *args, **kwargs):
            if path == life.PUBLICATION_FILE:
                raise OSError("注入阻擋標記移除失敗")
            return original(path, *args, **kwargs)
        with patch.object(life.os, "unlink", side_effect=fail):
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(self.state()["status"], "rescue")
        self.assert_publication_blocked()

    def test_guard_removal_is_last_publication_operation_and_success_can_release(self):
        original_unlink, original_fsync = life.os.unlink, life.os.fsync
        removed = False
        def unlink(path, *args, **kwargs):
            nonlocal removed
            result = original_unlink(path, *args, **kwargs)
            if path == life.PUBLICATION_FILE:
                removed = True
            return result
        def fsync(fd):
            if removed:
                raise OSError("阻擋標記移除後不得再有發布同步")
            return original_fsync(fd)
        with patch.object(life.os, "unlink", side_effect=unlink), patch.object(life.os, "fsync", side_effect=fsync):
            result = self.start()
        self.assertTrue(result["ok"], result)
        self.assertTrue(removed)
        self.assertFalse((self.runtime.lock_root / life.PUBLICATION_FILE).exists())
        self.assertTrue(self.start(session_id="different-session")["ok"])

    def test_guard_reappearing_after_crash_blocks_visible_rescue(self):
        self.assertTrue(self.start()["ok"])
        life._save(self.runtime.lock_root, life.PUBLICATION_FILE, {"session_id": "session-1"})
        self.assertEqual(self.state()["status"], "rescue")
        self.assert_publication_blocked()

    def test_publication_guard_symlink_blocks_without_reading_target(self):
        self.assertTrue(self.start()["ok"])
        (self.runtime.lock_root / life.PUBLICATION_FILE).symlink_to(self.root / "never-read")
        before = list(self.runtime.calls)
        self.assertFalse(self.start(session_id="different-session")["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_complete_event_time_is_included_in_total_deadline(self):
        original = life._save
        def slow(directory, name, value):
            result = original(directory, name, value)
            if value.get("step") == "complete":
                self.runtime.now += 600
            return result
        with patch.object(life, "_save", side_effect=slow):
            result = self.start()
        self.assertGreater(self.runtime.now, 600)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertNotEqual(self.state()["status"], "rescue")
        before = list(self.runtime.calls)
        self.assertFalse(self.start(session_id="different-session")["ok"])
        self.assertEqual(self.runtime.calls, before)

    def test_power_off_readback_failure_never_turns_power_on(self):
        original = self.runtime.power
        def wrong(action, deadline):
            record = original(action, deadline)
            if action == "off":
                record["device"]["on"] = True
            return record
        with patch.object(self.runtime, "power", side_effect=wrong):
            self.assertFalse(self.start()["ok"])
        self.assertNotIn("on", self.runtime.calls)

    def test_uart_pairing_failure_sends_no_power_command(self):
        with patch.object(self.runtime, "validate_uart", side_effect=ValueError("配對不符")):
            self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.runtime.opened, 0)

    def test_total_deadline_includes_preflight_not_only_boot(self):
        original = life.load_config
        def slow(*args, **kwargs):
            result = original(*args, **kwargs)
            self.runtime.now += self.config["timeout_seconds"] + 1
            return result
        with patch.object(life, "load_config", side_effect=slow):
            result = self.start()
        self.assertEqual(result["error_code"], "deadline")
        self.assertEqual(self.runtime.calls, [])

    def test_existing_output_and_partial_deploy_receipt_are_rejected(self):
        self.config["deploy_receipt"] = self.reference("receipt.json.partial", self.receipt)
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])
        self.number -= 1
        with self.assertRaises(FileExistsError):
            self.start()

    def test_normal_action_cannot_accept_fault_proof(self):
        self.assertFalse(self.start(fault_evidence={"path": "/never-read", "sha256": "0" * 64})["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_input_snapshot_is_used_after_original_manifest_changes(self):
        original = self.runtime.validate_uart
        def changed(uart):
            Path(self.config["rescue_inputs"]["path"]).write_bytes(b"changed-after-validation")
            return original(uart)
        with patch.object(self.runtime, "validate_uart", side_effect=changed):
            self.assertTrue(self.start()["ok"])
        self.assertEqual(json.loads((self.output / "rescue-inputs.json").read_text()), self.manifest)

    def test_boot_failure_keeps_partial_trace_and_does_not_retry(self):
        with patch.object(life.rescue, "boot", side_effect=TimeoutError("替身引導逾時")) as boot:
            result = self.start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "deadline")
        self.assertEqual(self.runtime.calls.count("on"), 1)
        boot.assert_called_once()
        self.assertFalse(json.loads((self.output / "boot-trace.json").read_text())["completed"])

    def test_simulation_requires_explicit_fake_and_isolated_lock_root(self):
        with self.assertRaises(ValueError):
            life.run(config_path="/never-read", config_sha256="a" * 64, action="cold-cycle", session_id="x",
                     output=self.root / "unused", runtime=self.runtime)
        self.runtime.lock_root = life.LOCK_ROOT
        self.assertFalse(self.start()["ok"])
        self.assertEqual(self.runtime.calls, [])

    def test_falsey_fake_never_falls_back_to_native_runtime(self):
        with patch.object(FakeRuntime, "__bool__", return_value=False, create=True):
            self.assertTrue(self.start()["ok"])
        self.assertEqual(self.runtime.opened, 1)

    def test_simulation_explicitly_rejects_native_runtime(self):
        with self.assertRaises(ValueError):
            life.run(config_path="/never-read", config_sha256="a" * 64, action="cold-cycle", session_id="x",
                     output=self.root / "unused", runtime=life.NativeRuntime(), simulated=True)

    def test_native_power_uses_bounded_stream_without_logging_raw_diagnostics(self):
        response = json.dumps({"ok": True, "verified": True, "device": {**life.POWER, "on": True}}).encode()
        events = iter((("stdout", response), ("exit", 0)))
        def stream(*args):
            yield from events
        with patch.object(life.customer.deploy.backup, "ssh_stream", side_effect=stream) as mock:
            result = life.NativeRuntime().power("status", life.Deadline(30, self.runtime.clock, self.runtime.sleep))
        self.assertTrue(result["ok"])
        self.assertEqual(mock.call_args.args[0], ["bpi-pw", "--device", "bpi-pw-1", "status"])
        self.assertEqual(mock.call_args.args[1], 30)

    def test_native_power_overflow_and_nonzero_exit_rejected(self):
        for events in ((("stdout", b"x" * 65537),), (("stderr", b"secret-placeholder"), ("exit", 1))):
            def stream(*args):
                yield from events
            with patch.object(life.customer.deploy.backup, "ssh_stream", side_effect=stream), self.assertRaises(ValueError):
                life.NativeRuntime().power("status", life.Deadline(60))

    def test_native_uart_rejects_stable_path_resolving_to_usb1(self):
        with patch.object(Path, "resolve", return_value=Path("/dev/ttyUSB1")), self.assertRaises(ValueError):
            life.NativeRuntime().validate_uart(self.config["uart"])

    def test_bad_inventory_fails_without_rescue_retry_sleep(self):
        self.runtime.raw.overrides["bpi-rescue inventory"] = SimpleNamespace(exitcode=0, output=b"{broken}")
        with patch.object(life.rescue.time, "sleep", side_effect=AssertionError("不應進入舊重試")):
            self.assertFalse(self.start()["ok"])
        self.assertNotIn("off", self.runtime.calls)

    def test_public_wrappers_select_only_their_named_action(self):
        for wrapper, action in ((life.cold_cycle, "cold-cycle"), (life.boot_customer, "boot-customer"),
                                (life.recover_normal, "recover-normal"), (life.recover_fault, "recover-fault")):
            with patch.object(life, "run", return_value={"ok": False}) as run:
                wrapper(session_id="unused")
                run.assert_called_once_with(action=action, session_id="unused")


class ParentLockTests(unittest.TestCase):
    """以真實本機 flock 與子程序驗證借鎖；只存取暫存檔與 /proc。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {"station_id": "offline", "hardware_id": life.HARDWARE,
                       "uart": {"stable_path": "/dev/serial/by-id/offline"}}
        self.binding = {"work_key": "f" * 64, "attempt_id": "attempt-1", "station_id": "offline",
                        "hardware_id": life.HARDWARE, "image_sha256": "a" * 64,
                        "boot_config_sha256": "b" * 64, "test_version": "offline", "mode": "hardware"}
        self.keys = ["hardware:" + life.HARDWARE, "station:offline"]
        owner = self.root / "queue.sqlite3"
        with sqlite3.connect(owner) as db:
            db.execute("CREATE TABLE jobs (work_key TEXT, attempt_id TEXT, station_id TEXT, state TEXT, body TEXT)")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (self.binding["work_key"], "attempt-1", "offline",
                                                              "running", json.dumps(self.binding)))
        with sqlite3.connect(self.root / "reservations.sqlite3") as db:
            db.execute("CREATE TABLE reservations (resource TEXT, owner TEXT, work_key TEXT)")
            for key in self.keys:
                db.execute("INSERT INTO reservations VALUES (?,?,?)", (key, str(owner), self.binding["work_key"]))
        self.descriptors = []
        self.addCleanup(self.close_locks)

    def close_locks(self):
        for fd in self.descriptors:
            os.close(fd)
        self.descriptors.clear()

    def hold_parent_locks(self, execution=False):
        for key in self.keys + (["h618:execution"] if execution else []):
            fd = os.open(self.root / (sha(key.encode()) + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.descriptors.append(fd)

    def child_lock(self, binding):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            for fd in self.descriptors:
                os.close(fd)
            try:
                with life.resource_lock(self.config, self.root, binding) as guard:
                    guard.check(self.config, self.root, binding)
                result = b"ok"
            except Exception:
                result = b"blocked"
            os.write(write_fd, result)
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        try:
            result = os.read(read_fd, 64)
        finally:
            os.close(read_fd)
            _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0)
        return result

    def test_verified_parent_queue_locks_are_borrowed_without_reentrant_flock(self):
        self.hold_parent_locks()
        self.assertEqual(self.child_lock(self.binding), b"ok")

    def test_parent_lock_without_matching_work_or_attempt_is_rejected(self):
        self.hold_parent_locks()
        for field, value in (("work_key", "0" * 64), ("attempt_id", "attempt-2"),
                             ("image_sha256", "c" * 64), ("boot_config_sha256", "d" * 64)):
            with self.subTest(field=field):
                self.assertEqual(self.child_lock({**self.binding, field: value}), b"blocked")

    def test_execution_mutex_is_never_borrowed_even_from_parent(self):
        self.hold_parent_locks(execution=True)
        self.assertEqual(self.child_lock(self.binding), b"blocked")

    def test_expired_scope_cannot_be_reused(self):
        with life.resource_lock(self.config, self.root, self.binding) as guard:
            guard.check(self.config, self.root, self.binding)
        with self.assertRaises(ValueError):
            guard.check(self.config, self.root, self.binding)

    def test_persistent_reservations_survive_released_flock(self):
        with self.assertRaises(ValueError), life.resource_lock(self.config, self.root):
            self.fail("獨立 runtime 不可繞過佇列租約")
        with self.assertRaises(ValueError), life.resource_lock(
                self.config, self.root, {**self.binding, "attempt_id": "attempt-2"}):
            self.fail("相同工作鍵仍須核對 attempt")


if __name__ == "__main__":
    unittest.main()
