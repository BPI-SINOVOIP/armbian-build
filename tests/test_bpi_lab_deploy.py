#!/usr/bin/env python3
"""共用部署離線回歸；不開啟硬體，不啟動 SSH 或其他子程序。"""

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_deploy as lab
import test_bpi_h618_emmc_deploy as fixtures

core = lab.core
SCHEMA = "bpi-lab-fixture-rescue-v1"
RESCUE = {"kernel": "TEST_KERNEL", "identity_sha256": fixtures.sha(b"identity")}


def state(request, final=False):
    value = fixtures.remote_state(request, final)
    value["rescue"].update(schema=request.get("rescue_schema", "bpi-h618-rescue-v1"),
                           **request.get("rescue_expected", RESCUE))
    return value


class Upload:
    def __init__(self, change=None):
        self.change = change or (lambda value: None)
        self.calls = []

    def __call__(self, argv, chunks, deadline, clock):
        self.calls.append(argv)
        request = json.loads(shlex.split(argv[-1])[-1])
        for stage, final in (("ready", False), ("verified", True)):
            value = state(request, final)
            self.change(value)
            yield "stdout", core.PREFIX + json.dumps({"schema": 1, "nonce": request["nonce"],
                                                       "event": stage, "state": value}).encode() + b"\n"
            if not final:
                for chunk in chunks:
                    yield "sent", len(chunk)
        yield "exit", 0


class DeployFixture(unittest.TestCase):
    def setUp(self):
        fixtures.HostTests.setUp(self)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(core.subprocess, "Popen", side_effect=AssertionError("不可啟動真實子程序")).start()
        key = self.root / "identity"
        hosts = self.root / "known_hosts"
        key.write_bytes(b"TEST_PRIVATE_KEY")
        hosts.write_bytes(b"192.0.2.1 ssh-ed25519 TEST_HOST_KEY\n")
        self.ssh = {"host": "192.0.2.1", "port": 22, "user": "root",
                    "identity": self.reference(key), "known_hosts": self.reference(hosts)}
        self.contract = {
            "schema": "bpi-lab-deploy-v1", "hardware_id": "fixture-board-01",
            "expected": fixtures.EXPECTED.copy(),
            "protected_sd": {**fixtures.PROTECTED, "bytes": 8 * core.CHUNK},
            "sd_prefix": {"bytes": 4 * core.CHUNK, "sha256": fixtures.sha(b"SD")},
            "rescue": {"schema": SCHEMA, **RESCUE}, "backup": self.reference(self.backup_path),
            "authorization": {"userarea_write": True, "hardware_id": "fixture-board-01",
                              "media_identity": "cid:" + fixtures.CID,
                              "backup_sha256": fixtures.sha(self.backup_path.read_bytes()), "record": "fixture-authorization"},
            "ssh": self.ssh}
        self.source_record = {"path": str(self.source), **fixtures.source_summary(fixtures.RAW, self.xz)}

    @staticmethod
    def reference(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def readonly(self, argv, deadline, clock):
        request = json.loads(shlex.split(argv[-1])[-1])
        yield "stdout", json.dumps({"nonce": request["nonce"], "state": state(request)}).encode()
        yield "exit", 0

    run_deploy = fixtures.HostTests.run_deploy
    pins = fixtures.HostTests.pins


class RescueParameters(DeployFixture):
    def test_default_request_remains_compatible(self):
        result = self.run_deploy()
        self.assertNotIn("rescue_schema", result["request"])
        self.assertNotIn("rescue_expected", result["request"])

    def test_custom_schema_verified_without_h618_identity(self):
        result = self.run_deploy(transport=Upload(), rescue_schema=SCHEMA, rescue_expected=RESCUE,
                                 pinned_preflight=self.pins())
        self.assertTrue(result["ok"])
        self.assertEqual(result["remote_state"]["rescue"]["schema"], SCHEMA)
        self.assertEqual(result["request"]["rescue_expected"], RESCUE)
        self.assertFalse(result["boot_verified"])

    def test_custom_schema_requires_pinned_identity_before_transport(self):
        for bad in (None, {}, {**RESCUE, "extra": True}, {**RESCUE, "kernel": "x;reboot"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_deploy(rescue_schema=SCHEMA, rescue_expected=bad)
        self.assertEqual(self.fake.calls, [])

    def test_schema_is_not_a_shell_or_regex_fragment(self):
        for bad in ("", "*", "bpi-.*", "fixture;reboot", [SCHEMA]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_deploy(rescue_schema=bad, rescue_expected=RESCUE)
        self.assertEqual(self.fake.calls, [])

    def test_conflicting_pins_rejected_before_transport(self):
        with self.assertRaises(ValueError):
            self.run_deploy(rescue_schema=SCHEMA, rescue_expected={**RESCUE, "kernel": "OTHER"},
                            pinned_preflight=self.pins())
        self.assertEqual(self.fake.calls, [])

    def test_remote_h618_schema_cannot_satisfy_other_board(self):
        upload = Upload(lambda value: value["rescue"].update(schema="bpi-h618-rescue-v1"))
        with self.assertRaises(ValueError):
            self.run_deploy(transport=upload, rescue_schema=SCHEMA, rescue_expected=RESCUE)
        self.assertFalse((self.output / "receipt.json").exists())

    def test_remote_identity_must_equal_expected_without_pins(self):
        upload = Upload(lambda value: value["rescue"].update(identity_sha256="f" * 64))
        with self.assertRaises(ValueError):
            self.run_deploy(transport=upload, rescue_schema=SCHEMA, rescue_expected=RESCUE)
        self.assertFalse((self.output / "receipt.json").exists())

    def test_real_rescue_parser_rejects_overlay_and_wrong_identity(self):
        identity = self.root / "rescue.json"
        identity.write_text(json.dumps({"schema": SCHEMA, "kernel": RESCUE["kernel"]}))
        mounts = self.root / "mountinfo"
        mounts.write_text("10 1 0:21 / / rw - tmpfs tmpfs rw\n")
        expected = {**RESCUE, "identity_sha256": fixtures.sha(identity.read_bytes())}
        inspect = core.remote_namespace()["rescue_identity"]
        original_stat = os.stat
        def root_stat(value, *args, **kwargs):
            return SimpleNamespace(st_dev=os.makedev(0, 21)) if value == "/" else original_stat(value, *args, **kwargs)
        with mock.patch.object(os, "stat", side_effect=root_stat), mock.patch.object(
                os, "uname", return_value=SimpleNamespace(release=RESCUE["kernel"])):
            result = inspect(identity, mounts, expected_schema=SCHEMA, expected_identity=expected)
            self.assertEqual(result["schema"], SCHEMA)
            with self.assertRaises(ValueError):
                inspect(identity, mounts, expected_schema=SCHEMA, expected_identity=RESCUE)
            mounts.write_text("10 1 0:21 / / rw - overlay overlay rw\n")
            with self.assertRaises(ValueError):
                inspect(identity, mounts, expected_schema=SCHEMA, expected_identity=expected)

    def test_default_identity_parser_does_not_accept_custom_schema(self):
        identity = self.root / "rescue.json"
        identity.write_text(json.dumps({"schema": SCHEMA, "kernel": os.uname().release}))
        with self.assertRaises(ValueError):
            core.remote_namespace()["rescue_identity"](identity)


class SharedDeploy(DeployFixture):
    def test_full_backup_and_source_are_redecoded(self):
        result = lab.verify_inputs(self.contract, self.source_record)
        self.assertEqual(result["backup"]["raw"]["bytes"], fixtures.CAPACITY)
        self.assertEqual(result["source"]["raw"], self.source_record["raw"])
        self.assertFalse(result["hardware_validated"])

    def test_false_backup_raw_digest_is_rejected(self):
        self.backup_record["host"]["raw"]["sha256"] = "a" * 64
        self.backup_path.write_text(json.dumps(self.backup_record))
        self.contract["backup"] = self.reference(self.backup_path)
        self.contract["authorization"]["backup_sha256"] = self.contract["backup"]["sha256"]
        with self.assertRaises(ValueError):
            lab.verify_inputs(self.contract, self.source_record)

    def test_corrupt_source_never_opens_transport(self):
        self.source.write_bytes(self.xz[:-4])
        transport = mock.Mock(side_effect=AssertionError("損壞來源不得啟動 SSH"))
        with self.assertRaises(ValueError):
            lab.preflight(self.contract, self.source_record, self.output, transport=transport)
        transport.assert_not_called()

    def test_wrong_authorization_cannot_borrow_other_board(self):
        for field, value in (("hardware_id", "bpi-m4zero-0845"), ("media_identity", "cid:" + fixtures.SD_CID),
                             ("backup_sha256", "a" * 64), ("userarea_write", False)):
            contract = copy.deepcopy(self.contract)
            contract["authorization"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                lab.verify_inputs(contract, self.source_record)

    def test_pinned_readonly_preflight(self):
        result = lab.preflight(self.contract, self.source_record, self.output, transport=self.readonly)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["state"]["rescue"]["schema"], SCHEMA)
        self.assertEqual(result["state"]["bytes_written"], 0)
        self.assertTrue((self.output / "preflight.json").is_file())
        self.assertEqual((self.output / "identity").stat().st_mode & 0o777, 0o400)

    def test_changed_source_during_preflight_fails(self):
        def changed(argv, deadline, clock):
            self.source.write_bytes(self.xz)
            yield from self.readonly(argv, deadline, clock)
        with self.assertRaises(ValueError):
            lab.preflight(self.contract, self.source_record, self.output, transport=changed)
        self.assertFalse((self.output / "preflight.json").exists())

    def test_wrong_sd_prefix_and_capacity_rejected(self):
        for change in (lambda v: v["sd_before"]["prefix"].update(sha256="a" * 64),
                       lambda v: v["sd_before"]["identity"].update(bytes=512),
                       lambda v: v.update(bytes_written=1, attempted_end=1)):
            output = self.root / ("try-" + str(id(change)))
            def wrong(argv, deadline, clock):
                request = json.loads(shlex.split(argv[-1])[-1])
                value = state(request)
                change(value)
                yield "stdout", json.dumps({"nonce": request["nonce"], "state": value}).encode()
                yield "exit", 0
            with self.assertRaises(ValueError):
                lab.preflight(self.contract, self.source_record, output, transport=wrong)

    def test_deploy_runs_full_preflight_and_core_write_protocol(self):
        result = lab.deploy(self.contract, self.source_record, self.output, confirm_overwrite=True,
                            preflight_transport=self.readonly, transport=Upload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["remote_state"]["readback"], self.source_record["raw"])
        self.assertTrue((self.output / "write/receipt.json").exists())

    def test_missing_confirmation_does_not_create_output_or_transport(self):
        with self.assertRaises(ValueError):
            lab.deploy(self.contract, self.source_record, self.output)
        self.assertFalse(self.output.exists())

    def test_ssh_rejects_commands_and_symlink_keys(self):
        with self.assertRaises(ValueError):
            lab.validate_ssh({**self.ssh, "ProxyCommand": "reboot"})
        link = self.root / "key-link"
        link.symlink_to(self.ssh["identity"]["path"])
        self.ssh["identity"]["path"] = str(link)
        with self.assertRaises((ValueError, OSError)):
            lab.validate_ssh(self.ssh)

    def test_generated_remote_program_is_valid_python(self):
        compile(lab.readonly_program(), "<離線預檢>", "exec")


if __name__ == "__main__":
    unittest.main()
