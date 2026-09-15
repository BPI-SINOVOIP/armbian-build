#!/usr/bin/env python3
"""ABI3 主機守門回歸；串口、時間與傳輸均為替身，不接觸實機。"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/bpi_sram_lab_uart.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_lab_uart", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lab = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(MODULE_PATH.parent), *sys.path]):
    SPEC.loader.exec_module(lab)

NONCE = 0x1234ABCD
PORT = "/dev/FAKE_TEST_ONLY"
CID = "0123456789abcdef" * 2
SECTORS = 262144
PARTITION_SECTORS = 245760
BLOBS = {
    1: lab.package.build_package(b"\x11" * 513, 1024),
    2: lab.ddr_package.build_package(b"\x22" * 513, 1024),
    3: lab.lab_package.build_package(b"\x33" * 513, 1024, 3),
    4: lab.lab_package.build_package(b"\x44" * 513, 1024, 4),
}


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 0.001
        return self.value


def event(name, *, spl2=False, nonce=NONCE, **fields):
    prefix = "BPI-SPL2" if spl2 else "BPI-SUP1"
    identity = f"nonce_hex={nonce:08x}" if spl2 else f"nonce={nonce}"
    suffix = "".join(f" {key}={value}" for key, value in fields.items())
    return f"{prefix} event={name} {identity}{suffix}\r\n".encode()


def loaded(kind=3, **changes):
    fields = {"result": 0, "kind": kind, "sha256": lab.parse_package(BLOBS[kind])["hash"]}
    fields.update(changes)
    return event("loaded", **fields)


def media(**changes):
    fields = {"cid": CID, "sectors": SECTORS, "partition_start": 8192,
              "partition_sectors": PARTITION_SECTORS}
    fields.update(changes)
    return event("media", spl2=True, **fields)


class FakeChannel:
    def __init__(self):
        self.buffer = bytearray()
        self.writes = []
        self.overrides = {}
        self.timeout = 0.1
        self.reset_count = 0
        self.kind = 3
        self.updating = False
        self.media_count = 0
        self.slot = 3
        self.size = len(BLOBS[4])
        self.digest = hashlib.sha256(BLOBS[4]).hexdigest()

    def reset_input_buffer(self):
        self.buffer.clear()
        self.reset_count += 1
        self.updating = False
        self.media_count = 0

    def response(self, name, default):
        return self.overrides.get(name, default)

    def write(self, data):
        self.writes.append(data)
        command, raw_nonce, *args = data.decode().strip().split()
        nonce = int(raw_nonce)
        if command == "I" and not self.updating:
            response = self.response("info", event(
                "info", nonce=nonce, abi=3, board="06180001", capabilities=lab.CAPABILITIES))
        elif command == "I":
            self.media_count += 1
            response = self.response("media-before" if self.media_count == 1 else "media-after",
                                     media(nonce=nonce))
        elif command in ("U", "S"):
            response = self.response("loading", event("loading", nonce=nonce, transport=command))
            response += b"C" if command == "U" else self.response("loaded", loaded(self.kind))
        elif command == "R":
            response = self.response("handoff", event("handoff", nonce=nonce, entry="00030000"))
            if self.kind == 1:
                response += self.response("smoke", event(
                    "smoke", spl2=True, nonce=nonce, sp="00047ff0", el=3, ddr="off", result="pass"))
            elif self.kind == 2:
                response += self.response("ddr-ready", event(
                    "ddr-ready", spl2=True, nonce=nonce, abi=2, kind=2, preflight="unverified"))
            elif self.kind == 3:
                self.updating = True
                response += self.response("update-ready", event(
                    "update-ready", spl2=True, nonce=nonce, abi=3, kind=3, ddr="off", write_slots="2,3,4"))
        elif command == "W":
            self.slot, self.size, self.digest = int(args[0]), int(args[1]), args[2]
            response = self.response("update-loading", event(
                "update-loading", spl2=True, nonce=nonce, slot=self.slot, bytes=self.size)) + b"C"
        elif command == "H":
            response = self.response("slot", event("slot", spl2=True, nonce=nonce,
                slot=self.slot, result=0, bytes=self.size, sha256=self.digest))
        else:
            raise AssertionError("不得傳送協定以外的命令")
        self.buffer.extend(response)
        return len(data)

    def read(self, size):
        if size != 1:
            raise AssertionError("不得預讀 XMODEM 或下一個控制事件")
        value = bytes(self.buffer[:1])
        del self.buffer[:1]
        return value


class PackageTests(unittest.TestCase):
    def test_all_versions_have_normalized_kind_and_distinct_hashes(self):
        for kind, blob in BLOBS.items():
            with self.subTest(kind=kind):
                metadata = lab.parse_package(blob)
                self.assertEqual(metadata["kind"], kind)
                self.assertEqual(metadata["version"], min(kind, 3))
                self.assertEqual(metadata["raw_size"], len(blob))
                self.assertNotEqual(metadata["hash"], hashlib.sha256(blob).hexdigest())

    def test_complete_valid_package_required_without_parser_fallback(self):
        variants = [None, {}, b"", BLOBS[3][:512], BLOBS[3][:-1], BLOBS[3] + b"\0",
                    BLOBS[3][:-1] + b"\1"]
        for offset, value in ((8, 4), (36, 2), (16, 0), (28, 0), (32, 1), (72, 1)):
            blob = bytearray(BLOBS[3])
            struct.pack_into("<I", blob, offset, value)
            struct.pack_into("<I", blob, 508, zlib.crc32(blob[:508]))
            variants.append(bytes(blob))
        blob = bytearray(BLOBS[3])
        blob[512] ^= 1
        variants.append(bytes(blob))
        for blob in variants:
            with self.subTest(blob_type=type(blob).__name__), self.assertRaises(lab.package.PackageError):
                lab.parse_package(blob)

    def test_private_snapshot_survives_source_replacement_and_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.spl2"
            source.write_bytes(BLOBS[3])
            with lab.staged_package(source) as (staged, blob):
                source.unlink()
                source.write_bytes(b"\0")
                self.assertNotEqual(staged, source)
                self.assertEqual(staged.read_bytes(), blob)
                self.assertEqual(blob, BLOBS[3])
                self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o400)
                self.assertEqual(stat.S_IMODE(staged.parent.stat().st_mode), 0o700)
            self.assertFalse(staged.exists())

    def test_old_tools_and_parsers_remain_strict(self):
        with mock.patch.object(sys, "path", [str(MODULE_PATH.parent), *sys.path]):
            import bpi_sram_v2_uart as v2
            import bpi_sram_ddr_uart as ddr
        self.assertEqual(lab.base.SupervisorSession.CONTROL_ABI, 1)
        self.assertEqual(v2.V2SupervisorSession.CONTROL_ABI, 2)
        self.assertEqual(ddr.DdrSupervisorSession.CONTROL_ABI, 2)
        self.assertNotIn("update-run", v2.V2SupervisorSession.CAPABILITIES)
        for parser in (lab.package.parse_package, lab.ddr_package.parse_package, v2.validate_smoke):
            with self.assertRaises((lab.base.UartError, lab.package.PackageError)):
                parser(BLOBS[3])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.channel = FakeChannel()
        patcher = mock.patch.object(lab.base.secrets, "randbits", return_value=NONCE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, kind=3):
        self.channel.kind = kind
        return lab.LabSupervisorSession(self.channel, 2, BLOBS[kind], Clock())

    def load(self, session, transport="S"):
        session.probe()
        session.begin_load(transport, len(BLOBS[3]) if transport == "U" else 2)
        if transport == "U":
            self.channel.buffer[:] = loaded()
        session.finish_load()

    def test_probe_only_and_unloaded_sessions_cannot_load_or_run(self):
        session = lab.LabSupervisorSession(self.channel, 2, clock=Clock())
        session.probe()
        for action in (lambda: session.begin_load("S", 0), lambda: session.begin_load("U", 1536),
                       session.finish_load, session.run):
            with self.assertRaises(lab.base.UartError):
                action()
        session = self.session()
        for action in (session.finish_load, session.run, lambda: session.begin_load("S", 0)):
            with self.assertRaises(lab.base.UartError):
                action()
        self.assertEqual([data.split()[0] for data in self.channel.writes], [b"I"])

    def test_reprobe_new_load_and_invalid_load_invalidate_prior_success(self):
        actions = [lambda s: s.probe(), lambda s: s.begin_load("S", 0),
                   lambda s: s.begin_load("U", 1024), lambda s: s.begin_load("S", True),
                   lambda s: s.begin_load("S", 5), lambda s: s.begin_load("W", 2),
                   lambda s: s.finish_load()]
        for action in actions:
            with self.subTest(action=action):
                session = self.session()
                self.load(session)
                try:
                    action(session)
                except lab.base.UartError:
                    pass
                with self.assertRaises(lab.base.UartError):
                    session.run()
        self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_run_requires_unchanged_nonce_kind_digest_and_rejects_smoke_alias(self):
        for name, value in (("nonce", 1), ("_loaded_kind", 1), ("_loaded_digest", "0" * 64)):
            with self.subTest(name=name):
                session = self.session()
                self.load(session)
                setattr(session, name, value)
                with self.assertRaises(lab.base.UartError):
                    session.run()
        session = self.session()
        self.load(session)
        with self.assertRaises(lab.base.UartError):
            session.run_smoke()
        with self.assertRaises(lab.base.UartError):
            session.run()
        self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_handoff_is_permanently_one_shot_even_when_write_or_evidence_fails(self):
        for failure in (None, "handoff", "write"):
            with self.subTest(failure=failure):
                self.channel = FakeChannel()
                session = self.session()
                self.load(session)
                if failure == "handoff":
                    self.channel.overrides["handoff"] = b""
                if failure == "write":
                    self.channel.write = mock.Mock(return_value=0)
                if failure:
                    with self.assertRaises(lab.base.UartError):
                        session.run()
                else:
                    self.assertEqual(session.run()["event"], "update-ready")
                for action in (session.run, session.probe, session.finish_load,
                               lambda: session.begin_load("S", 2)):
                    with self.assertRaises(lab.base.UartError):
                        action()
                if failure == "write":
                    self.channel.write.assert_called_once()
                else:
                    self.assertEqual(sum(data.startswith(b"R ") for data in self.channel.writes), 1)

    def test_update_requires_uart_updater_nonce_and_explicit_optin(self):
        options = dict(slot=3, cid=CID, sectors=SECTORS,
                       partition_sectors=PARTITION_SECTORS, confirm_write=True)
        for state in ("unloaded", "sd", "nonce", "optin"):
            with self.subTest(state=state):
                self.channel = FakeChannel()
                session = self.session()
                if state != "unloaded":
                    self.load(session, "S" if state == "sd" else "U")
                    session.run()
                if state == "nonce":
                    session.nonce = 1
                with self.assertRaises(lab.base.UartError):
                    session.update_slot(Path("/unused"), BLOBS[4], "/fake/sx",
                                        **{**options, "confirm_write": state != "optin"})
                self.assertFalse(any(data.startswith(b"W ") for data in self.channel.writes))

    def test_update_api_rejects_nonprivate_or_different_snapshot_before_w(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.spl2"
            for blob, mode in ((BLOBS[3], 0o400), (BLOBS[4], 0o600)):
                with self.subTest(mode=mode):
                    path.unlink(missing_ok=True)
                    path.write_bytes(blob)
                    path.chmod(mode)
                    self.channel = FakeChannel()
                    session = self.session()
                    self.load(session, "U")
                    session.run()
                    with self.assertRaises(lab.base.UartError):
                        session.update_slot(path, BLOBS[4], "/fake/sx", slot=3, cid=CID,
                            sectors=SECTORS, partition_sectors=PARTITION_SECTORS, confirm_write=True)
                    self.assertFalse(any(data.startswith(b"W ") for data in self.channel.writes))

    def test_update_failure_permanently_prevents_second_write_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidate.spl2"
            source.write_bytes(BLOBS[4])
            with lab.staged_package(source) as (path, blob):
                session = self.session()
                self.load(session, "U")
                session.run()
                with mock.patch.object(lab.base, "send_xmodem", side_effect=lab.base.UartError("替身傳輸失敗")) as sender:
                    for _ in range(2):
                        with self.assertRaises(lab.base.UartError):
                            session.update_slot(path, blob, "/fake/sx", slot=3, cid=CID,
                                sectors=SECTORS, partition_sectors=PARTITION_SECTORS, confirm_write=True)
                    sender.assert_called_once()
                self.assertEqual(sum(data.startswith(b"W ") for data in self.channel.writes), 1)

    def test_nonce_boundaries_bind_handoff_and_spl2_ready(self):
        for nonce in (0, 1, 0xFFFFFFFF):
            with self.subTest(nonce=nonce), mock.patch.object(lab.base.secrets, "randbits", return_value=nonce):
                self.channel = FakeChannel()
                self.channel.overrides["loaded"] = loaded(nonce=nonce)
                session = self.session()
                self.load(session)
                self.assertEqual(session.run()["nonce_hex"], f"{nonce:08x}")
                self.assertEqual(self.channel.writes[-1], f"R {nonce}\n".encode())


class FlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-lab-uart-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "candidate.spl2"
        self.updater = self.root / "updater.spl2"
        self.source.write_bytes(BLOBS[4])
        self.updater.write_bytes(BLOBS[3])
        self.channel = FakeChannel()
        self.serial = self.patch(lab.base, "open_serial")
        self.serial.return_value.__enter__.return_value = self.channel
        self.sender = self.patch(lab.base, "send_xmodem", side_effect=self.transfer)
        self.which = self.patch(lab.shutil, "which", return_value="/fake/sx")
        self.patch(lab.base.secrets, "randbits", return_value=NONCE)
        self.patch(lab.base.time, "monotonic", new=Clock())
        self.paths = []
        self.expected_blobs = [BLOBS[3], BLOBS[4]]

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def transfer(self, channel, path, executable, timeout):
        self.assertIs(channel, self.channel)
        self.assertEqual(channel.buffer, b"C", "控制讀取必須將 C 留給 sx")
        self.assertEqual(channel.reset_count, self.expected_reset_count)
        self.assertNotIn(path, (self.source, self.updater))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(path.read_bytes(), self.expected_blobs[len(self.paths)])
        self.assertEqual((executable, timeout), ("/fake/sx", 120.0))
        self.paths.append(path)
        channel.buffer.clear()
        if channel.updating:
            channel.buffer.extend(channel.response("update-result", event(
                "update-result", spl2=True, slot=channel.slot, result=0,
                committed=1, sha256=channel.digest)))
        else:
            metadata = lab.parse_package(path.read_bytes())
            channel.kind = metadata["kind"]
            channel.buffer.extend(channel.response("loaded", loaded(channel.kind)))
        return 0

    def invoke(self, *args):
        self.channel.writes.clear()
        self.paths.clear()
        self.sender.reset_mock()
        self.serial.reset_mock()
        self.which.reset_mock()
        self.expected_reset_count = self.channel.reset_count + 1
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                status = lab.main(list(map(str, args)))
            except SystemExit as exc:
                status = exc.code
        self.assertTrue(all(not path.exists() for path in self.paths))
        return status, output.getvalue(), error.getvalue()

    def update_args(self, slot=3):
        return ["update-slot", "--port", PORT, "--updater", self.updater,
                "--input", self.source, "--slot", str(slot), "--cid", CID,
                "--sectors", str(SECTORS), "--partition-sectors", str(PARTITION_SECTORS),
                "--confirm-write"]

    def commands(self):
        return [data.split()[0] for data in self.channel.writes]

    def assert_failed(self, outcome, *, no_write=False, no_run=False):
        status, output, error = outcome
        self.assertNotEqual(status, 0)
        self.assertEqual(output, "")
        self.assertIn("錯誤", error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn("PRIVATE_UART", error)
        if no_write:
            self.assertNotIn(b"W", self.commands())
        if no_run:
            self.assertNotIn(b"R", self.commands())

    def test_probe_only_checks_exact_abi_board_nonce_and_capabilities(self):
        status, output, error = self.invoke("probe", "--port", PORT)
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["abi"], 3)
        self.sender.assert_not_called()
        for changes in ({"abi": 2}, {"board": "06180002"}, {"nonce": 1},
                        {"capabilities": lab.CAPABILITIES + ",unknown"},
                        {"capabilities": "uart-ram,sd-read,smoke-run,ddr-run"}):
            with self.subTest(changes=changes):
                fields = dict(abi=3, board="06180001", capabilities=lab.CAPABILITIES)
                self.channel.overrides["info"] = event("info", **{**fields, **changes})
                self.assert_failed(self.invoke(*self.update_args()), no_write=True, no_run=True)
                self.assertEqual(self.commands(), [b"I"])

    def test_upload_and_run_each_kind_reports_only_its_evidence(self):
        for kind, expected in ((1, "smoke"), (2, "ddr-ready"), (3, "update-ready"), (4, "handoff")):
            with self.subTest(kind=kind):
                self.source.write_bytes(BLOBS[kind])
                self.expected_blobs = [BLOBS[kind]]
                status, output, error = self.invoke("upload", "--port", PORT,
                                                  "--input", self.source, "--run")
                self.assertEqual(status, 0, error)
                result = json.loads(output)
                self.assertEqual((result["event"], result["kind"]), (expected, kind))
                self.assertFalse(result["boot_verified"])
                self.assertFalse(result["ddr_result_verified"])
                self.assertTrue(result["ran"])
                self.assertEqual(self.commands(), [b"I", b"U", b"R"])
                if kind == 4:
                    self.assertFalse(result["handoff"]["boot_verified"])

    def test_upload_without_run_and_untrusted_extra_fields_do_not_claim_execution(self):
        self.expected_blobs = [BLOBS[4]]
        self.channel.overrides["loaded"] = loaded(4).replace(b"result=0", b"result=0 private=PRIVATE_UART")
        status, output, error = self.invoke("upload", "--port", PORT, "--input", self.source)
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["event"], "loaded")
        self.assertFalse(json.loads(output)["ran"])
        self.assertNotIn("PRIVATE_UART", output)
        self.assertEqual(self.commands(), [b"I", b"U"])

    def test_load_slot_all_fixed_slots_requires_local_input_without_implicit_run(self):
        self.channel.kind = 4
        for slot in range(5):
            with self.subTest(slot=slot):
                status, output, error = self.invoke("load-slot", "--port", PORT,
                    "--input", self.source, "--slot", slot)
                self.assertEqual(status, 0, error)
                result = json.loads(output)
                self.assertEqual((result["event"], result["slot"]), ("loaded", slot))
                self.assertFalse(result["ran"])
                self.assertEqual(self.commands(), [b"I", b"S"])
                self.sender.assert_not_called()

    def test_loading_and_loaded_mismatch_never_run(self):
        self.expected_blobs = [BLOBS[4]]
        variants = [loaded(4, nonce=1), loaded(4, result=1), loaded(4, result="00"), loaded(3),
                    loaded(4, sha256=hashlib.sha256(BLOBS[4]).hexdigest()),
                    loaded(4, sha256="none"), event("loaded", result=0),
                    loaded(4).replace(b"kind=4", b"kind=04")]
        for response in variants:
            with self.subTest(response=response[:40]):
                self.channel.overrides["loaded"] = response
                self.assert_failed(self.invoke("upload", "--port", PORT,
                    "--input", self.source, "--run"), no_run=True)
        for response in (event("loading", nonce=1, transport="U"),
                         event("loading", transport="S"), b""):
            self.channel.overrides["loading"] = response
            self.assert_failed(self.invoke(*self.update_args()), no_run=True, no_write=True)
            self.sender.assert_not_called()

    def test_handoff_smoke_ddr_and_update_ready_contracts_are_strict(self):
        variants = [
            (1, "handoff", event("handoff", entry="40000000")),
            (1, "smoke", event("smoke", spl2=True, sp="00048000", el=3, ddr="off", result="pass")),
            (1, "smoke", event("smoke", spl2=True, sp="00047ff1", el=3, ddr="off", result="pass")),
            (1, "smoke", event("smoke", spl2=True, sp="00047ff0", el=2, ddr="off", result="pass")),
            (1, "smoke", event("smoke", spl2=True, sp="00047ff0", el=3, ddr="on", result="pass")),
            (2, "ddr-ready", event("ddr-ready", spl2=True, abi=3, kind=2, preflight="unverified")),
            (2, "ddr-ready", event("ddr-ready", spl2=True, abi=2, kind=2, preflight="pass")),
        ]
        for changes in ({"nonce": 1}, {"abi": 2}, {"kind": 4}, {"ddr": "on"},
                        {"write_slots": "0,1,2,3,4"}, {"write_slots": "2,3"}):
            fields = dict(abi=3, kind=3, ddr="off", write_slots="2,3,4")
            variants.append((3, "update-ready", event("update-ready", spl2=True, **{**fields, **changes})))
        for kind, name, response in variants:
            with self.subTest(kind=kind, event=name):
                self.channel.overrides = {name: response}
                self.source.write_bytes(BLOBS[kind])
                self.expected_blobs = [BLOBS[kind]]
                self.assert_failed(self.invoke("upload", "--port", PORT,
                    "--input", self.source, "--run"), no_write=True)
                self.assertEqual(self.commands().count(b"R"), 1)

    def test_update_all_allowed_slots_checks_media_commit_and_readback(self):
        for slot in (2, 3, 4):
            with self.subTest(slot=slot):
                candidate = BLOBS[3 if slot == 2 else 4]
                self.source.write_bytes(candidate)
                self.expected_blobs = [BLOBS[3], candidate]
                status, output, error = self.invoke(*self.update_args(slot))
                self.assertEqual(status, 0, error)
                result = json.loads(output)
                self.assertEqual((result["event"], result["slot"], result["committed"]), ("slot", slot, 1))
                self.assertTrue(result["slot_verified"])
                self.assertFalse(result["boot_verified"])
                self.assertFalse(result["ran"])
                self.assertFalse(result["powercycled"])
                digest = hashlib.sha256(candidate).hexdigest()
                self.assertEqual(result["sha256"], digest)
                self.assertEqual(self.commands(), [b"I", b"U", b"R", b"I", b"W", b"I", b"H"])
                self.assertEqual(self.channel.writes[4], f"W {NONCE} {slot} {len(candidate)} {digest}\n".encode())
                self.assertEqual(self.channel.writes[-1], f"H {NONCE} {slot}\n".encode())
                self.serial.assert_called_once_with(PORT, 115200, 15.0)
                self.assertEqual(self.sender.call_count, 2)

    def test_update_without_optin_rejected_before_files_serial_or_sender(self):
        self.source.unlink()
        self.assert_failed(self.invoke(*self.update_args()[:-1]), no_write=True, no_run=True)
        self.serial.assert_not_called()
        self.sender.assert_not_called()
        self.which.assert_not_called()

    def test_wrong_updater_candidate_or_media_arguments_never_open_serial(self):
        for slot, kind in ((2, 1), (2, 2), (2, 4), (3, 3), (4, 3)):
            self.source.write_bytes(BLOBS[kind])
            self.assert_failed(self.invoke(*self.update_args(slot)), no_write=True)
            self.serial.assert_not_called()
        self.source.write_bytes(BLOBS[4])
        for kind in (1, 2, 4):
            self.updater.write_bytes(BLOBS[kind])
            self.assert_failed(self.invoke(*self.update_args()), no_write=True)
            self.serial.assert_not_called()
        self.updater.write_bytes(BLOBS[3])
        for option, value in (("--cid", "0"), ("--sectors", "131071"),
                              ("--sectors", "4294967296"), ("--partition-sectors", "4095"),
                              ("--partition-sectors", str(SECTORS))):
            args = self.update_args()
            args[args.index(option) + 1] = value
            self.assert_failed(self.invoke(*args), no_write=True)
            self.serial.assert_not_called()

    def test_incomplete_or_nonregular_inputs_are_rejected_before_serial(self):
        for path, original in ((self.source, BLOBS[4]), (self.updater, BLOBS[3])):
            for blob in (b"", original[:512], original[:-1], original + b"\0"):
                path.write_bytes(blob)
                self.assert_failed(self.invoke(*self.update_args()), no_write=True)
                self.serial.assert_not_called()
            path.unlink()
            path.symlink_to(self.root / "missing")
            self.assert_failed(self.invoke(*self.update_args()), no_write=True)
            path.unlink()
            os.mkfifo(path)
            self.assert_failed(self.invoke(*self.update_args()), no_write=True)
            self.serial.assert_not_called()
            path.unlink()
            path.write_bytes(original)

    def test_wrong_media_before_write_never_sends_w(self):
        variants = [media(nonce=1), media(cid="0" * 32), media(sectors=SECTORS + 1),
                    media(partition_start=8193), media(partition_sectors=PARTITION_SECTORS - 1),
                    event("media", spl2=True, cid=CID),
                    event("update-reject", spl2=True, reason="command_or_media")]
        for response in variants:
            with self.subTest(response=response[:60]):
                self.channel.overrides["media-before"] = response
                self.assert_failed(self.invoke(*self.update_args()), no_write=True)
                self.assertEqual(self.sender.call_count, 1)
                self.assertEqual(self.commands(), [b"I", b"U", b"R", b"I"])

    def test_wrong_update_loading_does_not_send_candidate(self):
        variants = [event("update-loading", spl2=True, slot=2, bytes=len(BLOBS[4])),
                    event("update-loading", spl2=True, slot=3, bytes=1024),
                    event("update-loading", spl2=True, nonce=1, slot=3, bytes=len(BLOBS[4])),
                    event("update-result", spl2=True, slot=3, result=0), b""]
        for response in variants:
            self.channel.overrides["update-loading"] = response
            self.assert_failed(self.invoke(*self.update_args()))
            self.assertEqual(self.sender.call_count, 1)
            self.assertEqual(self.commands(), [b"I", b"U", b"R", b"I", b"W"])

    def test_commit_requires_success_one_and_whole_package_sha(self):
        good = dict(slot=3, result=0, committed=1, sha256=hashlib.sha256(BLOBS[4]).hexdigest())
        for changes in ({"slot": 4}, {"result": -1}, {"result": "00"}, {"committed": 0},
                        {"committed": "01"}, {"sha256": lab.parse_package(BLOBS[4])["hash"]},
                        {"sha256": "none"}, {"nonce": 1}):
            self.channel.overrides["update-result"] = event("update-result", spl2=True, **{**good, **changes})
            self.assert_failed(self.invoke(*self.update_args()))
            self.assertEqual(self.commands(), [b"I", b"U", b"R", b"I", b"W"])

    def test_media_change_after_commit_never_claims_success_or_reads_slot(self):
        for changes in ({"cid": "0" * 32}, {"sectors": SECTORS + 1},
                        {"partition_start": 8193}, {"partition_sectors": PARTITION_SECTORS - 1},
                        {"nonce": 1}):
            self.channel.overrides["media-after"] = media(**changes)
            self.assert_failed(self.invoke(*self.update_args()))
            self.assertEqual(self.commands(), [b"I", b"U", b"R", b"I", b"W", b"I"])

    def test_final_slot_requires_result_nonce_slot_size_and_whole_sha(self):
        good = dict(slot=3, result=0, bytes=len(BLOBS[4]), sha256=hashlib.sha256(BLOBS[4]).hexdigest())
        for changes in ({"result": -1}, {"slot": 2}, {"bytes": 1024}, {"nonce": 1},
                        {"sha256": lab.parse_package(BLOBS[4])["hash"]}, {"sha256": "none"}):
            self.channel.overrides["slot"] = event("slot", spl2=True, **{**good, **changes})
            self.assert_failed(self.invoke(*self.update_args()))
            self.assertEqual(self.commands().count(b"W"), 1)
            self.assertEqual(self.commands().count(b"R"), 1)

    def test_both_senders_fail_closed_even_with_queued_success(self):
        for phase in (1, 2):
            for exception in (lab.base.UartError("替身傳輸逾時"), OSError("PRIVATE_UART"),
                              lab.base.subprocess.SubprocessError("PRIVATE_UART"), KeyboardInterrupt()):
                def fail(channel, path, executable, timeout):
                    value = self.transfer(channel, path, executable, timeout)
                    if len(self.paths) == phase:
                        raise exception
                    return value
                self.sender.side_effect = fail
                outcome = self.invoke(*self.update_args())
                self.assert_failed(outcome, no_write=phase == 1, no_run=phase == 1)
                if isinstance(exception, KeyboardInterrupt):
                    self.assertEqual(outcome[0], 130)
                self.assertNotIn(b"H", self.commands())

    def test_source_replacement_during_updater_transfer_cannot_change_candidate(self):
        def replace(channel, path, executable, timeout):
            self.source.write_bytes(b"\0")
            self.updater.write_bytes(b"\0")
            return self.transfer(channel, path, executable, timeout)
        self.sender.side_effect = replace
        status, _, error = self.invoke(*self.update_args())
        self.assertEqual(status, 0, error)

    def test_missing_sender_and_short_control_writes_stop_without_fake_success(self):
        self.which.return_value = None
        self.assert_failed(self.invoke(*self.update_args()), no_run=True, no_write=True)
        self.serial.assert_not_called()
        self.which.return_value = "/fake/sx"
        original = self.channel.write
        for prefix in (b"I", b"U", b"R", b"W", b"H"):
            def short(data):
                if data.split()[0] == prefix:
                    self.channel.writes.append(data)
                    return 0
                return original(data)
            self.channel.write = short
            self.assert_failed(self.invoke(*self.update_args()))

    def test_malformed_control_events_and_timeouts_cannot_be_success_evidence(self):
        variants = [b"PRIVATE_UART " + media(), media().replace(b"BPI-SPL2", b"BPI-SUP1"),
                    media().replace(b"event=media", b"event=media event=media"),
                    media().replace(b"cid=", b"unsafe=$(id) cid="), b"\x18" + media(),
                    b"x" * 513 + b"\n", media()[:-2], b"", b"x\n" * 300]
        with mock.patch.object(lab.base, "MAX_CONTROL_BYTES", 512):
            for response in variants:
                self.channel.overrides["media-before"] = response
                self.assert_failed(self.invoke(*self.update_args()), no_write=True)

    def test_cli_rejects_missing_inputs_unsafe_ports_slots_and_extended_deadlines(self):
        variants = [("load-slot", "--port", PORT, "--slot", "0"),
                    ("upload", "--port", PORT), (*self.update_args(), "--run"),
                    (*self.update_args(), "--powercycle"), (*self.update_args(), "--fall-back")]
        for option, values in (("--port", ("auto", "loop://", "/dev/ttyUSB*", "ttyUSB0")),
                               ("--slot", ("0", "1", "5", "-1"))):
            for value in values:
                args = self.update_args()
                args[args.index(option) + 1] = value
                variants.append(args)
        variants.extend((*self.update_args(), option, value)
                        for option in ("--timeout", "--transfer-timeout")
                        for value in ("0", "nan", "inf", "121"))
        for args in variants:
            self.assert_failed(self.invoke(*args), no_write=True, no_run=True)
            self.serial.assert_not_called()

    def test_help_and_import_are_chinese_and_have_no_hardware_side_effects(self):
        for args in (("--help",), ("upload", "--help"), ("load-slot", "--help"), ("update-slot", "--help")):
            status, output, error = self.invoke(*args)
            self.assertEqual(status, 0, error)
            self.assertIn("用法：", output)
            self.assertNotIn("usage:", output)
            self.serial.assert_not_called()
        with mock.patch.object(lab.base.subprocess, "Popen") as process:
            SPEC.loader.exec_module(importlib.util.module_from_spec(SPEC))
            process.assert_not_called()
        self.sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
