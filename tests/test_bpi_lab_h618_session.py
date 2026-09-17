"""登入與同次 SSH 的合成回歸；不連線、不讀取真實憑證，也不構成實板資格。"""

import base64
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_h618_session as session


KEY = b"ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + b"x" * 32)
BOOT_ID = "11111111-2222-3333-4444-555555555555"


class Console:
    def __init__(self, prompts=()):
        self.prompts, self.sent, self.commands = list(prompts), [], []

    def send(self, value):
        self.sent.append(value)

    def expect_regex(self, pattern, timeout):
        if not self.prompts:
            raise TimeoutError("替身逾時")
        value = self.prompts.pop(0)
        before, matched = value if isinstance(value, tuple) else (b"", value)
        return SimpleNamespace(before=before, matched=matched)

    def run_shell(self, command, timeout=10):
        self.commands.append(command)
        return SimpleNamespace(exitcode=0, output=b"0\n" if command == "id -u" else b"")


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = self.file("old-secret", b"synthetic-old\n")
        self.new = self.file("new-secret", b"synthetic-new\n")
        self.identity = self.file("identity", b"synthetic-private-key")
        public = self.file("identity.pub", KEY)
        self.config = {"schema": "bpi-lab-h618-session-v1", "login": {
            "username": "root", "password_file": str(self.old), "new_password_file": str(self.new),
            "initialize": True, "skip_user_creation": True}, "peer_ipv4": "192.0.2.2",
            "identity_file": str(self.identity), "public_key": session.reference(public),
            "rescue_network": {"mode": "existing"}, "customer_network": {"mode": "existing"},
            "dt_compatible": {"rescue": ["sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"],
                "customer": ["sinovoip,bpi-m4-zero-emac", "sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"]}}
        self.request = {key: key for key in session.BINDINGS}
        self.components = {"kernel_release": "6.18.49-current-sunxi64", "root_uuid": BOOT_ID}
        self.observed = {"nonce": "", "boot_id": BOOT_ID, "kernel": self.components["kernel_release"],
            "architecture": "aarch64", "root_dev": "179:17", "dt_compatible": self.config["dt_compatible"]["customer"],
            **session.life.customer.EXPECTED, "uuid": BOOT_ID}
        self.deadline = session.life.Deadline(120)
        self.no_process = patch.object(session.life.customer.deploy.backup.subprocess, "Popen",
                                       side_effect=AssertionError("禁止真實程序"))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def file(self, name, blob):
        path = self.root / name
        path.write_bytes(blob)
        path.chmod(0o600)
        return path

    def test_inspection_does_not_read_secrets(self):
        with patch.object(session, "private_file", side_effect=AssertionError("不應讀取認證")):
            self.assertIs(session.check_config(self.config), self.config)

    def test_login_initializes_only_from_explicit_secret_files(self):
        console = Console([b"Password:", b"Create root password:", b"Repeat root password:",
                           (b"1) bash\r\n", b"2) zsh\r\n"), b"Please provide a username: ", b"root@board:~# "])
        self.assertTrue(session.login(console, self.config["login"]))
        self.assertEqual(console.sent, ["root\n", b"synthetic-old\n", b"synthetic-new\n",
                                        b"synthetic-new\n", "1\n", b"\x03"])
        self.assertEqual(console.commands, ["id -u"])

    def test_existing_login_does_not_read_new_password(self):
        console = Console([b"Password:", b"root@board:~# "])
        self.new.unlink()
        self.assertFalse(session.login(console, self.config["login"]))

    def test_forced_password_expiry_supported(self):
        console = Console([b"Password:", b"(current) UNIX password:", b"New UNIX password:",
                           b"Retype new UNIX password:", b"root@board:~# "])
        self.assertTrue(session.login(console, self.config["login"]))
        self.assertEqual(console.sent.count(b"synthetic-old\n"), 2)

    def test_unknown_or_repeated_prompt_never_retries(self):
        for prompts in ([b"Password:", b"Password:"], [b"Repeat root password:"],
                        [b"Password:", b"Login incorrect"], [b"Create root password:", b"Create root password:"]):
            with self.subTest(prompts=prompts), self.assertRaises(ValueError):
                session.login(Console(prompts), self.config["login"])

    def test_unapproved_initialization_does_not_transmit_secret(self):
        self.config["login"]["initialize"] = False
        console = Console([b"Create root password:"])
        with self.assertRaises(ValueError):
            session.login(console, self.config["login"])
        self.assertEqual(console.sent, ["root\n"])

    def test_login_timeout_is_not_retried(self):
        console = Console()
        with self.assertRaises(TimeoutError):
            session.login(console, self.config["login"])
        self.assertEqual(console.sent, ["root\n"])

    def test_private_file_rejects_permissions_links_devices_and_multiline_password(self):
        self.old.chmod(0o644)
        with self.assertRaises(ValueError):
            session.private_file(self.old)
        alias = self.root / "alias"
        alias.symlink_to(self.new)
        with self.assertRaises((OSError, ValueError)):
            session.private_file(alias)
        with self.assertRaises(ValueError):
            session.private_file("/dev/null")
        invalid = self.file("invalid", b"line1\nline2")
        with self.assertRaises(ValueError):
            session._password(str(invalid))

    def test_public_key_checks_wire_structure(self):
        self.assertEqual(session.public_key(KEY + b" comment"), KEY.decode())
        for value in (b"ssh-ed25519 AAAA", b"ssh-ed25519 %%%%", b"ssh-rsa AAAA"):
            with self.assertRaises(ValueError):
                session.public_key(value)

    def test_identity_command_fits_single_uart_shell_line(self):
        command = session._identity_command("customer", "a" * 64)
        self.assertNotIn("\n", command)
        self.assertLess(len(shlex.quote(command)) + 250, 4095)
        compile(shlex.split(command)[3], "<身分探測>", "exec")
        compile(session.IDENTITY, "<唯讀程式>", "exec")

    def test_identity_rejects_kernel_root_cid_capacity_dt_and_architecture(self):
        session.validate_identity(self.observed, "customer", self.components, self.config, "d" * 64)
        for field, value in (("kernel", "wrong"), ("cid", "0" * 32), ("uuid", "wrong"),
                             ("bytes", 512), ("controller", "/wrong"), ("architecture", "armv7l"),
                             ("dt_compatible", []), ("boot_id", "wrong")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                session.validate_identity({**self.observed, field: value}, "customer", self.components,
                                          self.config, "d" * 64)

    def rescue_identity(self):
        return {"nonce": "", "boot_id": BOOT_ID, "architecture": "aarch64", "root_dev": "0:1",
                "dt_compatible": self.config["dt_compatible"]["rescue"], "kernel": session.life.rescue.KERNEL,
                "identity_sha256": "d" * 64, "inventory": {"schema": "bpi-h618-rescue-v1", "devices": [
                    {"device/cid": session.life.rescue.SD_CID, "device/type": "SD"},
                    {"device/cid": session.life.customer.EXPECTED["cid"], "device/type": "MMC"}],
                    "mountinfo": "1 0 0:1 / / rw - tmpfs tmpfs rw\n",
                    "swaps": "Filename Type Size Used Priority\n"}}

    def test_mode_specific_dt_requires_complete_ordered_identity(self):
        identities = {"rescue": self.rescue_identity(), "customer": self.observed}
        for mode, identity in identities.items():
            session.validate_identity(identity, mode, self.components, self.config, "d" * 64)
            other = "customer" if mode == "rescue" else "rescue"
            for wrong in (self.config["dt_compatible"][other], list(reversed(identity["dt_compatible"])),
                          identity["dt_compatible"][1:]):
                with self.subTest(mode=mode, wrong=wrong), self.assertRaisesRegex(ValueError, "DT 身分不符"):
                    session.validate_identity({**identity, "dt_compatible": wrong}, mode,
                                              self.components, self.config, "d" * 64)

    def test_dt_configuration_rejects_shared_list_missing_mode_and_invalid_lists(self):
        wanted = self.config["dt_compatible"]
        invalid = [wanted["customer"], {}, {"customer": wanted["customer"]}, {"rescue": wanted["rescue"]},
                   {**wanted, "other": wanted["rescue"]}]
        for mode in ("rescue", "customer"):
            invalid.extend({**wanted, mode: value} for value in ([], "allwinner,sun50i-h618", None,
                           ["duplicate", "duplicate"], ["wrong\nvalue"], [1], ["x"] * 33))
        for compatible in invalid:
            with self.subTest(compatible=compatible), self.assertRaises(ValueError):
                session.check_config({**self.config, "dt_compatible": compatible})

    def establish(self, *, mismatch=False, expected_boot_id=None, validation=None, mode="customer", observed=None):
        console = Console()
        identity = self.observed if observed is None else observed
        def shell(console, command, **kwargs):
            if command == "id -u":
                return b"0\n"
            if command.startswith("python3 -B -c ") and "exec(" in command:
                return json.dumps({**identity, "nonce": shlex.split(command)[-1]}).encode()
            if command == "ip -j address":
                return json.dumps([{"ifname": "end1", "flags": ["UP", "LOWER_UP"], "addr_info": [
                    {"family": "inet", "scope": "global", "local": "192.0.2.3", "prefixlen": 24}]}]).encode()
            if command.startswith(("cat /etc/ssh/", "cat /run/ssh/")):
                return KEY
            return b""
        def run_shell(command, timeout=10):
            if command.startswith("ip -j route"):
                return SimpleNamespace(exitcode=0, output=json.dumps([
                    {"dev": "end1", "prefsrc": "192.0.2.3", "dst": "192.0.2.2"}]).encode())
            return SimpleNamespace(exitcode=0, output=b"")
        def ssh(ssh, command, deadline):
            return {**identity, "nonce": shlex.split(command)[-1],
                    "boot_id": BOOT_ID if not mismatch else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        console.run_shell = run_shell
        with ExitStack() as stack:
            stack.enter_context(patch.object(session.life, "_shell", side_effect=shell))
            stack.enter_context(patch.object(session, "_ssh_identity", side_effect=ssh))
            self.linux_collect = stack.enter_context(patch.object(session.linux, "collect", return_value={"synthetic": True}))
            self.linux_validate = stack.enter_context(patch.object(session.linux, "validate", return_value=validation or
                {"checks": [{"check": "root_cid", "status": "passed"}]}))
            return session.establish(console, mode, self.root, self.deadline, False,
                config=self.config, components=self.components, request=self.request,
                rescue_sha="d" * 64, expected_boot_id=expected_boot_id)

    def test_rescue_session_accepts_m4_zero_dt_without_borrowing_customer_dt(self):
        result = self.establish(mode="rescue", observed=self.rescue_identity())
        self.assertTrue(result["strict_ssh_verified"])
        self.assertEqual(result["identity"]["dt_compatible"], self.config["dt_compatible"]["rescue"])
        self.linux_collect.assert_not_called()
        self.linux_validate.assert_not_called()

    def test_customer_session_passes_only_emac_dt_to_linux_validation(self):
        result = self.establish()
        self.assertTrue(result["strict_ssh_verified"])
        self.assertEqual(result["identity"]["dt_compatible"], self.config["dt_compatible"]["customer"])
        self.assertEqual(self.linux_validate.call_args.args[1]["dt_compatible"], self.config["dt_compatible"]["customer"])

    def test_unknown_mode_rejected_before_login_or_output(self):
        console = Console()
        with self.assertRaises(ValueError):
            session.establish(console, "unknown", self.root, self.deadline, True,
                config=self.config, components=self.components, request=self.request, rescue_sha="d" * 64)
        self.assertEqual(console.sent, [])
        self.assertFalse((self.root / "session").exists())

    def test_establish_pins_uart_key_and_same_boot_without_lowering_ssh(self):
        result = self.establish()
        self.assertTrue(result["strict_ssh_verified"])
        self.assertFalse(result["hardware_validated"])
        self.assertEqual(result["binding"], session.binding(self.request))
        ssh = Path(result["ssh"]["config"]["path"]).read_text()
        for option in ("StrictHostKeyChecking yes", "ControlPath none", "ProxyCommand none",
                       "KnownHostsCommand none", "PasswordAuthentication no", "VerifyHostKeyDNS no"):
            self.assertIn(option, ssh)
        self.assertEqual((self.root / "session" / "known_hosts").read_text(), "192.0.2.3 " + KEY.decode() + "\n")
        evidence = (self.root / "session" / "session.json").read_text()
        self.assertNotIn("synthetic-old", evidence)
        self.assertNotIn("synthetic-private-key", evidence)

    def test_uart_ssh_boot_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.establish(mismatch=True)
        self.assertFalse((self.root / "session" / "session.json").exists())

    def test_resume_unobserved_reboot_rejected(self):
        with self.assertRaises(ValueError):
            self.establish(expected_boot_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    def test_linux_identity_failure_blocks_but_services_are_preserved_for_smoke(self):
        with self.assertRaises(ValueError):
            self.establish(validation={"checks": [{"check": "root_cid", "status": "failed"}]})

    def test_failed_services_are_not_misreported_as_boot_identity_failure(self):
        result = self.establish(validation={"checks": [{"check": "root_cid", "status": "passed"},
                                                       {"check": "failed_services", "status": "failed"}]})
        self.assertTrue(result["strict_ssh_verified"])
        self.assertIn('"failed"', (self.root / "session" / "linux-validation.json").read_text())

    def test_recheck_rejects_cross_attempt_before_transport(self):
        record = {"binding": {**session.binding(self.request), "attempt_id": "other"}}
        with patch.object(session, "_ssh_identity") as ssh, self.assertRaises(ValueError):
            session.recheck(record, self.request, self.deadline)
        ssh.assert_not_called()

    def test_ssh_stream_requires_nonce_payload_exit_and_no_diagnostics(self):
        ssh = session.write_ssh_config(self.root, address="192.0.2.3", username="root",
                                       identity_file=str(self.identity), host_key=KEY)
        for events in ([('stdout', b'{}')], [('stdout', b'{}'), ('exit', 1)],
                       [('stderr', b'sensitive'), ('exit', 0)], [('stdout', b'x' * 65537), ('exit', 0)],
                       [('exit', 0), ('stdout', b'{}')]):
            with self.subTest(events=len(events)), patch.object(session.life.customer.deploy.backup,
                    "ssh_stream", return_value=(event for event in events)), self.assertRaises(ValueError):
                session._ssh_identity(ssh, ":", self.deadline)

    def test_reusable_ssh_json_validates_pins_before_and_after_transport(self):
        ssh = session.write_ssh_config(self.root, address="192.0.2.3", username="root",
                                       identity_file=str(self.identity), host_key=KEY, alias="other-board")
        def events(*args):
            yield "stdout", b'{"nonce":"test"}'
            yield "exit", 0
        with patch.object(session.life.customer.deploy.backup, "ssh_stream", side_effect=events):
            self.assertEqual(session.ssh_json(ssh, ":", self.deadline), {"nonce": "test"})
        Path(ssh["known_hosts"]["path"]).write_bytes(b"changed")
        with patch.object(session.life.customer.deploy.backup, "ssh_stream") as transport:
            with self.assertRaises(ValueError):
                session.ssh_json(ssh, ":", self.deadline)
            transport.assert_not_called()

    def test_reusable_writer_never_overwrites_previous_session(self):
        kwargs = {"address": "192.0.2.3", "username": "root", "identity_file": str(self.identity), "host_key": KEY}
        session.write_ssh_config(self.root, **kwargs)
        old = (self.root / "known_hosts").read_bytes()
        with self.assertRaises(FileExistsError):
            session.write_ssh_config(self.root, **kwargs)
        self.assertEqual((self.root / "known_hosts").read_bytes(), old)

    def test_wifi_secret_is_not_part_of_shell_command_and_waits_for_echo_off(self):
        secret = self.file("wifi-secret", b"synthetic-wifi-secret")
        console = Console()
        item = {"mode": "wifi", "interface": "wlan9", "ssid": "offline-network", "secret_file": str(secret)}
        with self.assertRaises(TimeoutError):
            session.connect_wifi(console, item, "rescue", self.deadline)
        self.assertEqual(len(console.sent), 1)
        self.assertNotIn("synthetic-wifi-secret", console.sent[0])
        self.assertIn("ECHONL", console.sent[0])
        self.assertIn("finally:", console.sent[0])

    def test_configuration_rejects_command_injection_and_implicit_credentials(self):
        for field, value in (("identity_file", "/tmp/key%h"), ("peer_ipv4", "127.0.0.1"),
                             ("dt_compatible", [])):
            config = copy.deepcopy(self.config)
            config[field] = value
            with self.assertRaises(ValueError):
                session.check_config(config)


if __name__ == "__main__":
    unittest.main()
