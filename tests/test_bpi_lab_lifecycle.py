#!/usr/bin/env python3
"""非 H618 冷循環離線測試；使用真 U-Boot 執行器與模擬 UART 傳輸。"""

from contextlib import contextmanager
import re
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_lifecycle as life
import test_bpi_lab_backend as data
import test_bpi_lab_uboot as boot_data

BEFORE = "11111111-2222-3333-4444-555555555555"
AFTER = "66666666-7777-8888-9999-000000000000"


class Channel(boot_data.Channel):
    def __init__(self, config, blobs, clock):
        super().__init__(config, blobs, clock)
        self.queue.clear()
        self.kernel_response = ("\r\n[ 0.0] Linux version " + config["kernel_release"] +
                                " (fixture)\r\nroot@fixture:~# ").encode()

    def write(self, wire):
        if wire == b"\n":
            self.queue.append(b"\r\nroot@fixture:~# ")
        elif wire in (b"systemctl poweroff\n", b"/bin/busybox poweroff -f\n"):
            self.queue.append(b"\r\n[ 3.14] reboot: Power down\r\n")
        elif wire == b" ":
            self.queue.append(b"\r\n" + self.prompt)
        else:
            return super().write(wire)
        self.writes.append(wire)
        return len(wire)


class Runtime:
    def __init__(self, config, blobs):
        self.clock = boot_data.Clock()
        self.channel = Channel(config, blobs, self.clock)
        self.actions, self.on, self.bad_identity = [], True, False

    def sleep(self, seconds):
        self.clock.value += seconds

    @contextmanager
    def console(self, config, pairing, output, deadline):
        with life.console_api.ConsoleSession(self.channel, log_path=output / "uart.bin", monotonic=self.clock) as console:
            yield life.BoundedConsole(console, deadline)

    def drain(self, console, deadline):
        life.NativeRuntime.drain(self, console, deadline)

    def power(self, config, pairing, action, deadline):
        self.actions.append(action)
        if action in ("off", "on"):
            self.on = action == "on"
        if action == "on":
            self.channel.queue.append(config["autoboot"]["stop_text"].encode())
        device = {key: pairing["power"][key] for key in ("name", "ip", "mac")}
        if self.bad_identity:
            device["mac"] = "00:00:00:00:00:00"
        return {"ok": True, "verified": True, "device": {**device, "on": self.on, "identity_verified": True}}


class LifecycleTests(data.BackendFixture):
    def run_cycle(self, action="boot", runtime=None, interrupted=None):
        runtime = runtime or Runtime(self.boot if action == "boot" else self.rescue, self.blobs)
        request = {**self.request, "stage": action}
        current = {"schema": "bpi-lab-session-v1", "boot_id": AFTER, "identity": {"root": {"devnum": "179:58"}},
                   "ssh": self.ssh, "binding": {key: request[key] for key in life.session.BINDINGS},
                   "mode": "customer" if action == "boot" else "rescue"}
        samples = ([{"boot_id": BEFORE}] if interrupted is None else []) + [
            {"boot_id": AFTER, "root": current["identity"]["root"]}]
        with mock.patch.object(life.session, "uart_identity", side_effect=samples), \
                mock.patch.object(life.session, "check_boot_root", return_value={"matches": ["fixture-emmc"]}), \
                mock.patch.object(life.session, "establish", return_value=current), \
                mock.patch.object(life.linux, "collect", return_value={}), \
                mock.patch.object(life.linux, "validate", return_value={"ok": True}), \
                mock.patch.object(life.deploy, "preflight", return_value={"status": "verified"}) as rescue_check:
            result = life.cycle(self.lifecycle, self.pairing, self.contract, self.source_record, self.boot,
                                self.expected, self.ssh, self.root / (action + "-cycle"), request,
                                action=action, timeout=600, previous_boot_id=BEFORE, interrupted=interrupted, runtime=runtime)
        return result, runtime, rescue_check

    def test_customer_boot_runs_real_uboot_protocol_and_power_order(self):
        result, runtime, rescue_check = self.run_cycle()
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["simulated"])
        self.assertTrue(result["customer_kernel_verified"])
        self.assertEqual(runtime.actions, ["status", "off", "status", "on"])
        self.assertIn("version", runtime.channel.commands)
        self.assertTrue(any(command.startswith("booti ") for command in runtime.channel.commands))
        self.assertTrue(any(command.startswith("hash sha256") for command in runtime.channel.commands))
        self.assertGreaterEqual(runtime.clock.value, self.lifecycle["off_seconds"])
        rescue_check.assert_not_called()

    def test_recovery_uses_sd_config_then_ram_and_sd_preflight(self):
        result, runtime, rescue_check = self.run_cycle("recovery")
        self.assertTrue(result["rescue_verified"])
        self.assertIn("mmc dev 2", runtime.channel.commands)
        rescue_check.assert_called_once()
        self.assertEqual(rescue_check.call_args.args[0]["rescue"], self.contract["rescue"])

    def test_wrong_power_asset_stops_before_off(self):
        runtime = Runtime(self.boot, self.blobs)
        runtime.bad_identity = True
        with self.assertRaises(ValueError):
            self.run_cycle(runtime=runtime)
        self.assertEqual(runtime.actions, ["status"])
        self.assertFalse(runtime.channel.commands)

    def test_duplicate_label_stops_before_shutdown_or_power(self):
        self.boot["bootargs"] = ["root=LABEL=BPI-ROOT", "console=ttyS0,115200"]
        runtime = Runtime(self.boot, self.blobs)
        uuid = self.expected["root"]["uuid"]
        parent = self.contract["expected"]["controller"] + "/mmc_host/mmc7/mmc7:0001/block/mmcblk7"
        binding = {"method": "label", "label": "BPI-ROOT", "uuid": uuid,
                   "unique_in_image": True, "unique_on_hardware": False}
        row = {"name": "mmcblk7p2", "parent": parent, "devnum": "179:226", "uuid": uuid, "label": "BPI-ROOT"}
        record = {"nonce": "a" * 64, "matches": [row], "inventory": ["mmcblk7", "sda"],
                  "label_matches": [row, {**row, "name": "sda1", "parent": "/sys/devices/usb/block/sda",
                                           "devnum": "8:1", "uuid": "00000000-1111-2222-3333-444444444444"}]}
        before = {"boot_id": BEFORE, "media": [{"cid": self.contract["expected"]["cid"], "sysfs": parent}]}
        response = SimpleNamespace(exitcode=0, output=life.deploy.encode(record))
        with mock.patch.object(life.session.secrets, "token_hex", return_value="a" * 64), \
                mock.patch.object(life.session, "uart_identity", return_value=before), \
                mock.patch.object(life.BoundedConsole, "run_shell", return_value=response) as scan:
            with self.assertRaisesRegex(ValueError, "LABEL 在可見媒體不唯一"):
                life.cycle(self.lifecycle, self.pairing, self.contract, self.source_record, self.boot,
                           self.expected, self.ssh, self.root / "duplicate-label", {**self.request, "stage": "boot"},
                           action="boot", timeout=600, previous_boot_id=BEFORE, runtime=runtime, root_binding=binding)
        scan.assert_called_once()
        self.assertEqual(runtime.actions, [])
        self.assertFalse(any(b"poweroff" in wire for wire in runtime.channel.writes))

    def test_label_boot_cannot_omit_root_binding(self):
        self.boot["bootargs"] = ["root=LABEL=BPI-ROOT", "console=ttyS0,115200"]
        runtime = Runtime(self.boot, self.blobs)
        with self.assertRaisesRegex(ValueError, "LABEL 引導缺少原配根綁定"):
            self.run_cycle(runtime=runtime)
        self.assertEqual(runtime.actions, [])

    def test_uboot_version_mismatch_stops_before_kernel(self):
        runtime = Runtime(self.boot, self.blobs)
        runtime.channel.overrides["version"] = b"U-Boot WRONG\r\n"
        with self.assertRaises(ValueError):
            self.run_cycle(runtime=runtime)
        self.assertFalse(any(command.startswith("booti ") for command in runtime.channel.commands))

    def test_no_fault_poweroff_without_separate_authorization(self):
        request = {**self.request, "stage": "recovery"}
        intent = {"status": "failed", "stage": "boot", "binding": {key: request[key] for key in life.session.BINDINGS}}
        runtime = Runtime(self.rescue, self.blobs)
        with self.assertRaises(ValueError):
            self.run_cycle("recovery", runtime=runtime, interrupted=intent)
        self.assertEqual(runtime.actions, [])

    def test_authorized_fault_recovery_requires_same_work(self):
        self.lifecycle["authorization"]["fault_poweroff"] = True
        request = {**self.request, "stage": "recovery"}
        intent = {"status": "running", "stage": "boot", "binding": {key: request[key] for key in life.session.BINDINGS}}
        result, runtime, _ = self.run_cycle("recovery", interrupted=intent)
        self.assertTrue(result["forced_poweroff"])
        self.assertFalse(any(wire == b"systemctl poweroff\n" for wire in runtime.channel.writes))

    def test_deploy_writer_isolation_cannot_be_overridden_by_power_authorization(self):
        self.lifecycle["authorization"]["fault_poweroff"] = True
        request = {**self.request, "stage": "recovery"}
        for stage, status, unresolved in (("deploy", "running", False), ("deploy", "failed", False),
                                           ("recovery", "failed", True)):
            runtime = Runtime(self.rescue, self.blobs)
            intent = {"status": status, "stage": stage, "writer_unresolved": unresolved,
                      "binding": {key: request[key] for key in life.session.BINDINGS}}
            with self.subTest(stage=stage, status=status), self.assertRaises(ValueError):
                self.run_cycle("recovery", runtime=runtime, interrupted=intent)
            self.assertEqual(runtime.actions, [])

    def test_rescue_may_not_boot_persistent_root(self):
        self.rescue["bootargs"] = ["root=/dev/mmcblk7p1"]
        self.lifecycle["rescue_uboot"] = self.write_json("rescue-bad.json", self.rescue)
        with self.assertRaises(ValueError):
            life.validate_config(self.lifecycle, self.pairing, self.config_document["pairing"]["sha256"], self.contract, self.boot)

    def test_uart_open_checks_are_not_called_by_config_validation(self):
        with mock.patch.object(life.console_api.uart, "open_serial", side_effect=AssertionError("禁止真 UART")) as port:
            life.validate_config(self.lifecycle, self.pairing, self.config_document["pairing"]["sha256"], self.contract, self.boot)
        port.assert_not_called()


class PromptConsole:
    def __init__(self, prompts):
        self.prompts, self.sent = list(prompts), []

    def send(self, data, **kwargs):
        self.sent.append((data.encode() if isinstance(data, str) else data, kwargs.get("secret", False)))
        return len(data)

    def expect_literal(self, value, **kwargs):
        return SimpleNamespace(matched=value.encode(), before=b"")

    def expect_regex(self, pattern, **kwargs):
        value = self.prompts.pop(0)
        before = b"1) bash\r\n" if value.startswith(b"2)") else b""
        match = re.search(pattern, b"\r\n" + value)
        if match is None:
            raise TimeoutError("未知登入流程")
        return SimpleNamespace(matched=match[0], before=before)


class FirstLoginTests(data.BackendFixture):
    def login_config(self):
        old, new = self.root / "old-password", self.root / "new-password"
        old.write_bytes(b"FIXTURE_OLD")
        new.write_bytes(b"FIXTURE_NEW")
        old.chmod(0o600)
        new.chmod(0o600)
        return {"kind": "initial-setup", "shell_prompt": "root@fixture:~# ", "login_prompt": "login: ",
                "password_prompt": "Password:", "username": "root", "password": self.reference(old),
                "new_password": self.reference(new), "skip_user_creation": True}

    def test_forced_password_change_and_armbian_setup_are_bounded(self):
        prompts = [b"Password:", b"(current) UNIX password:", b"New UNIX password:",
                   b"Retype new UNIX password:", b"2) zsh\r\n", b"Please provide a username: ", b"root@fixture:~# "]
        console = PromptConsole(prompts)
        result = life.login(console, self.login_config(), life.Deadline(600), initialize_authorized=True)
        self.assertTrue(result["initialized"])
        self.assertTrue(result["user_creation_skipped"])
        self.assertEqual([value for value, secret in console.sent if secret],
                         [b"FIXTURE_OLD\n", b"FIXTURE_OLD\n", b"FIXTURE_NEW\n", b"FIXTURE_NEW\n"])
        self.assertNotIn("FIXTURE", str(result))

    def test_initial_setup_never_runs_without_account_authorization(self):
        console = PromptConsole([])
        with self.assertRaises(ValueError):
            life.login(console, self.login_config(), life.Deadline(600))
        self.assertEqual(console.sent, [])

    def test_repeated_password_prompt_does_not_guess(self):
        console = PromptConsole([b"Password:", b"Password:"])
        with self.assertRaises(ValueError):
            life.login(console, self.login_config(), life.Deadline(600), initialize_authorized=True)
        self.assertEqual(sum(secret for _, secret in console.sent), 1)

    def test_early_shell_does_not_claim_firstboot_complete(self):
        console = PromptConsole([b"Password:", b"root@fixture:~# "])
        with self.assertRaises(ValueError):
            life.login(console, self.login_config(), life.Deadline(600), initialize_authorized=True)

    def test_user_creation_skip_requires_separate_setting(self):
        config = self.login_config()
        config["skip_user_creation"] = False
        console = PromptConsole([b"New password:", b"Retype new password:", b"Please provide a username: "])
        with self.assertRaises(ValueError):
            life.login(console, config, life.Deadline(600), initialize_authorized=True)
        self.assertFalse(any(value == b"\x03" for value, _ in console.sent))


if __name__ == "__main__":
    unittest.main()
