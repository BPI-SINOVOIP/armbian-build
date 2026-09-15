#!/usr/bin/env python3
"""ABI2 smoke 主機守門測試；串口與傳輸皆為替身，不接觸硬體。"""

import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/bpi_sram_v2_uart.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_v2_uart", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
v2 = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(MODULE_PATH.parent), *sys.path]):
    SPEC.loader.exec_module(v2)

NONCE = 0x1234ABCD
PORT = "/dev/FAKE_TEST_ONLY"
TRUSTED_SHA256 = "8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062"
DIGEST = "4efc809a5e5d6980447781c0cf865b6a35b8ee5ca360135143cc1f4b8deec96d"
# build-009/spl2-smoke.sram 的原樣快照，避免測試依賴未追蹤的建置輸出。
BLOB = base64.b64decode("""
QlBJU1JBTTEBAAAAAAIAAAEAGAZHAgAAAIABAAAAAwAAAAAAAQAAAE78gJpeXWmARHeBwM+GW2o1uO5co2ATUUPMH0uN7slt
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAIzyHV3fTwPV8wMAqr9BANWhAQBYPwAAkfIDH6qBAQBYogEAWD8AAutiAABUP4QA+P3//xfgAxOqMQAAlF8gA9X///8X
8H8EAAAAAABQAgMAAAAAAFACAwAAAAAAAUiIUoICgNIAHAAS4QGgcgKgoPJDAEA5gwAoNyEEAHGh//9UwANf1gGgoNIgAAA5
/f//F+QDAKoAAEA5QAAANcADX9b9e7+p/QMAkYQEAJHs//+XgABAOaD//zX9e8GowANf1gUAAJDmAwAqpSAHkf17v6mEA4BS
/QMAkcEkxBohDECShBAAUaBoYTjd//+XnxAAMUH//1T9e8GowANf1uUDAKpAQjjV5wMAkQEQPtWjIEHRAvSP0mNAANF/AALr
KAMAVKMAQLliqopSAiqmcn8AAmuBAgBUogRAuV8EAHEhAgBUoghAuV8AAXHBAQBUowxAuSIAgFICw6ByfwACayEBAFTjAEHR
4v+P0n8AAusAmEz6gQAAVKAAgtI/AADqgAAAVAAAAJAAZAeRwv//F/17v6kAAACQABgIkf0DAJG9//+XoBBAucf//5cAAACQ
AJgIkbj//5fgAwcqwv//l/17wagAAACQAKwIkfD//xcwMTIzNDU2Nzg5YWJjZGVmAEJQSS1TUEwyIGV2ZW50PWhhbHQgcmVz
dWx0PWludmFsaWQtY29udGV4dA0KAEJQSS1TUEwyIGV2ZW50PXNtb2tlIG5vbmNlX2hleD0AIHNwPQAgZWw9MyBkZHI9b2Zm
IHJlc3VsdD1wYXNzDQoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
""")


class Clock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 0.001
        return self.value


def info(nonce=NONCE, abi=2, capabilities=v2.V2SupervisorSession.CAPABILITIES):
    return (f"BPI-SUP1 event=info nonce={nonce} abi={abi} board=06180001 "
            f"capabilities={capabilities}\r\n").encode()


def loaded(nonce=NONCE, kind="1", digest=DIGEST, result="0"):
    return (f"BPI-SUP1 event=loaded nonce={nonce} result={result} "
            f"kind={kind} sha256={digest}\r\n").encode()


def smoke(nonce=NONCE):
    return (f"BPI-SUP1 event=handoff nonce={nonce} entry=00030000\r\n"
            f"BPI-SPL2 event=smoke nonce_hex={nonce:08x} sp=00047ff0 "
            "el=3 ddr=off result=pass\r\n").encode()


class FakeSerial:
    """單次 smoke 交接後即停止回應 SPL1；不模擬不存在的返回路徑。"""

    def __init__(self):
        self.buffer = bytearray(loaded(nonce=1))
        self.writes = []
        self.overrides = {}
        self.reset_count = 0
        self.timeout = 0.1
        self.handed_off = False

    def reset_input_buffer(self):
        if self.handed_off:
            raise AssertionError("交接後不得再次重設串口以探測 SPL1")
        self.reset_count += 1
        self.buffer.clear()

    def write(self, data):
        if self.handed_off:
            raise AssertionError("單次 smoke 不會返回 SPL1，不得送出後續命令")
        self.writes.append(data)
        command, nonce, *arguments = data.decode().strip().split()
        nonce = int(nonce)
        if command == "R":
            self.handed_off = True
        if command in self.overrides:
            response = self.overrides[command]
        elif command == "I":
            response = info(nonce)
        elif command in ("U", "S"):
            response = f"BPI-SUP1 event=loading nonce={nonce} transport={command}\r\n".encode()
            response += b"C" if command == "U" else loaded(nonce)
        elif command == "R":
            response = smoke(nonce)
        else:
            raise AssertionError("替身拒絕非固定控制命令")
        self.buffer.extend(response)
        return len(data)

    def read(self, size):
        if size != 1:
            raise AssertionError("控制讀取不得預讀傳輸或下一階段資料")
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


class BaseCase(unittest.TestCase):
    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def setUp(self):
        self.serial = self.patch(v2.uart, "open_serial", side_effect=AssertionError("禁止實體串口"))
        self.sender = self.patch(v2.uart, "send_xmodem", side_effect=AssertionError("禁止實體傳輸"))
        self.patch(v2.uart.subprocess, "Popen", side_effect=AssertionError("禁止啟動外部程序"))
        self.patch(v2.uart.secrets, "randbits", return_value=NONCE)


class PackageTests(BaseCase):
    def test_real_build009_fixture_and_distinct_digest_domains(self):
        self.assertEqual(len(BLOB), 1536)
        self.assertEqual(hashlib.sha256(BLOB).hexdigest(), TRUSTED_SHA256)
        self.assertEqual(v2.PACKAGE_SHA256, TRUSTED_SHA256)
        metadata = v2.validate_smoke(BLOB)
        self.assertEqual(metadata["hash"], DIGEST)
        self.assertEqual(metadata["kind"], "smoke")
        self.assertNotEqual(metadata["hash"], v2.PACKAGE_SHA256)

    def test_nonfixed_packages_types_and_sizes_rejected(self):
        variants = (None, "smoke", {"hash": DIGEST}, b"", BLOB[:-1], BLOB + b"\0",
                    v2.package.build_package(b"a" * 583, 0x18000))
        for blob in variants:
            with self.subTest(kind=type(blob).__name__), self.assertRaises(v2.uart.UartError):
                v2.validate_smoke(blob)
        self.serial.assert_not_called()

    def test_mutation_in_header_payload_or_padding_rejected(self):
        for offset in (0, 508, 512, len(BLOB) - 1):
            blob = bytearray(BLOB)
            blob[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(v2.uart.UartError):
                v2.validate_smoke(blob)

    def test_fixed_hash_still_requires_full_parser(self):
        with mock.patch.object(v2.package, "parse_package", wraps=v2.package.parse_package) as parser:
            v2.validate_smoke(BLOB)
        parser.assert_called_once_with(BLOB)

    def test_session_rejects_metadata_instead_of_complete_package(self):
        with self.assertRaises(v2.uart.UartError):
            v2.V2SupervisorSession(FakeSerial(), 2, {"hash": DIGEST})


class SessionTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.fresh()

    def fresh(self, blob=BLOB):
        self.channel = FakeSerial()
        self.session = v2.V2SupervisorSession(self.channel, 2, blob, Clock())

    def load(self, transport="S"):
        self.session.probe()
        self.session.begin_load(transport, len(BLOB) if transport == "U" else 0)
        if transport == "U":
            self.assertEqual(self.channel.buffer, b"C")
            self.channel.buffer[:] = loaded(self.session.nonce)
        self.session.finish_load()

    def assert_no_run(self):
        with self.assertRaises(v2.uart.UartError):
            self.session.run_smoke()
        self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def assert_terminal(self):
        before = list(self.channel.writes)
        resets = self.channel.reset_count
        for action in (self.session.probe, self.session.finish_load, self.session.run_smoke,
                       lambda: self.session.begin_load("S", 0),
                       lambda: self.session.begin_load("U", len(BLOB))):
            with self.assertRaises(v2.uart.UartError):
                action()
        self.assertEqual(self.channel.writes, before)
        self.assertEqual(self.channel.reset_count, resets)
        self.assertFalse(self.session.probed)
        self.assertIsNone(self.session.loaded_nonce)

    def test_probe_without_package_cannot_load_or_run(self):
        self.fresh(blob=None)
        self.assertEqual(self.session.probe()["abi"], 2)
        for transport, value in (("S", 0), ("U", len(BLOB))):
            with self.assertRaises(v2.uart.UartError):
                self.session.begin_load(transport, value)
        self.assert_no_run()

    def test_no_load_before_probe_or_result_before_loading(self):
        with self.assertRaises(v2.uart.UartError):
            self.session.begin_load("S", 0)
        with self.assertRaises(v2.uart.UartError):
            self.session.finish_load()
        self.assert_no_run()
        self.session.probe()
        self.channel.buffer[:] = loaded()
        with self.assertRaises(v2.uart.UartError):
            self.session.finish_load()
        self.assert_no_run()

    def test_wrong_info_abi_caps_nonce_board_rejected(self):
        for response in (info(abi=1), info(abi=3), info(NONCE + 1),
                         info().replace(b"06180001", b"06180002"),
                         info(capabilities=v2.uart.CAPABILITIES), info(capabilities="ddr-run"),
                         info(capabilities=v2.V2SupervisorSession.CAPABILITIES + ",unknown")):
            with self.subTest(response=response[:80]):
                self.fresh()
                self.channel.overrides["I"] = response
                with self.assertRaises(v2.uart.UartError):
                    self.session.probe()
                self.assert_no_run()
                self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode()])

    def test_v2_ready_accepted_but_wrong_ready_abi_or_board_rejected(self):
        ready = b"BPI-SUP1 event=ready abi=2 board=06180001 ddr=off sd_write=off\n"
        self.channel.overrides["I"] = ready + info()
        self.assertEqual(self.session.probe()["abi"], 2)
        for response in (ready.replace(b"abi=2", b"abi=1"),
                         ready.replace(b"06180001", b"06180002")):
            self.fresh()
            self.channel.overrides["I"] = response + info()
            with self.assertRaises(v2.uart.UartError):
                self.session.probe()
            self.assert_no_run()

    def test_loaded_requires_nonce_kind_payload_hash_and_success(self):
        variants = (loaded(NONCE + 1), loaded(kind="2"), loaded(kind="0"),
                    loaded(result="-1"), loaded(digest="0" * 64), loaded(digest=TRUSTED_SHA256),
                    loaded(digest=DIGEST.upper()), loaded().replace(b" kind=1", b""),
                    loaded().replace(f" sha256={DIGEST}".encode(), b""),
                    loaded().replace(b"result=0", b"result=0 result=0"),
                    loaded().replace(b"nonce=", b"nonce=0", 1), b"")
        for response in variants:
            with self.subTest(response=response[:80]):
                self.fresh()
                self.session.probe()
                self.session.begin_load("S", 0)
                self.channel.buffer[:] = response
                with self.assertRaises(v2.uart.UartError):
                    self.session.finish_load()
                self.assert_no_run()

    def test_reprobe_invalidates_even_if_nonce_is_reused(self):
        for nonce in (NONCE, 99):
            self.fresh()
            self.load()
            with mock.patch.object(v2.uart.secrets, "randbits", return_value=nonce):
                self.session.probe()
            self.assert_no_run()

    def test_failed_reprobe_invalidates_prior_load(self):
        self.load()
        self.channel.overrides["I"] = info(abi=1)
        with self.assertRaises(v2.uart.UartError):
            self.session.probe()
        self.assert_no_run()

    def test_new_or_invalid_load_clears_prior_authorization(self):
        for transport, value in (("S", 0), ("U", len(BLOB)), ("U", 1024), ("U", True),
                                 ("S", True), ("S", 5), ("S", -1), ("S", 0.0), ("X", 0)):
            with self.subTest(transport=transport, value=value):
                self.fresh()
                self.load()
                if type(value) is int and (transport, value) in (("S", 0), ("U", len(BLOB))):
                    self.session.begin_load(transport, value)
                else:
                    with self.assertRaises(v2.uart.UartError):
                        self.session.begin_load(transport, value)
                self.assert_no_run()

    def test_second_finish_cannot_replay_loaded_success(self):
        self.load()
        self.channel.buffer[:] = loaded()
        with self.assertRaises(v2.uart.UartError):
            self.session.finish_load()
        self.assert_no_run()

    def test_incomplete_or_modified_authorization_cannot_run(self):
        self.session.probe()
        self.session.loaded_nonce = NONCE
        self.assert_no_run()
        for attribute, value in (("nonce", 99), ("_loaded_digest", "0" * 64),
                                 ("_loaded_kind", 2), ("probed", False), ("loading", True)):
            self.fresh()
            self.load()
            setattr(self.session, attribute, value)
            self.assert_no_run()

    def test_valid_uart_and_slot_loads_allow_one_terminal_smoke(self):
        for transport in ("U", "S"):
            self.fresh()
            self.load(transport)
            result = self.session.run_smoke()
            self.assertEqual(result, {"nonce_hex": f"{NONCE:08x}", "sp": "00047ff0",
                                      "el": 3, "ddr": "off", "result": "pass"})
            self.assertTrue(self.channel.handed_off)
            self.assert_terminal()

    def test_nonce_boundaries_across_load_and_smoke(self):
        for nonce in (0, 1, 0xFFFFFFFF):
            with self.subTest(nonce=nonce), mock.patch.object(v2.uart.secrets, "randbits", return_value=nonce):
                self.fresh()
                self.load()
                self.assertEqual(self.session.run_smoke()["nonce_hex"], f"{nonce:08x}")
                self.assert_terminal()

    def test_handoff_and_smoke_nonce_or_entry_failure_is_terminal(self):
        variants = (smoke().replace(f"nonce={NONCE}".encode(), b"nonce=1"),
                    smoke().replace(f"nonce_hex={NONCE:08x}".encode(), b"nonce_hex=00000001"),
                    smoke().replace(b"entry=00030000", b"entry=00030010"), b"")
        for response in variants:
            self.fresh()
            self.load()
            self.channel.overrides["R"] = response
            with self.assertRaises(v2.uart.UartError):
                self.session.run_smoke()
            self.assert_terminal()

    def test_smoke_preserves_el3_ddr_off_result_and_stack_checks(self):
        variants = [(b"el=3", b"el=2"), (b"ddr=off", b"ddr=on"), (b"result=pass", b"result=fail")]
        variants += [(b"sp=00047ff0", f"sp={value}".encode())
                     for value in ("0003fff0", "00048000", "00047ff8", "00047FF0", "47ff0", "invalid")]
        for old, new in variants:
            with self.subTest(field=new):
                self.fresh()
                self.load()
                self.channel.overrides["R"] = smoke().replace(old, new)
                with self.assertRaises(v2.uart.UartError):
                    self.session.run_smoke()
                self.assert_terminal()

    def test_stack_lower_boundary_is_accepted(self):
        self.load()
        self.channel.overrides["R"] = smoke().replace(b"sp=00047ff0", b"sp=00040000")
        self.assertEqual(self.session.run_smoke()["sp"], "00040000")
        self.assert_terminal()

    def test_short_or_failed_run_write_also_permanently_invalidates(self):
        for effect in (None, OSError("模擬寫入失敗")):
            self.fresh()
            self.load()
            self.channel.write = mock.Mock(return_value=0, side_effect=effect)
            with self.assertRaises((v2.uart.UartError, OSError)):
                self.session.run_smoke()
            self.assert_terminal()
            self.channel.write.assert_called_once_with(f"R {NONCE}\n".encode())

    def test_legacy_abi1_session_stays_strict(self):
        self.assertEqual(v2.uart.SupervisorSession.CONTROL_ABI, 1)
        self.assertEqual(v2.uart.SupervisorSession.CAPABILITIES, "uart-ram,sd-read,smoke-run")
        legacy = v2.uart.SupervisorSession(self.channel, 2, Clock())
        with self.assertRaises(v2.uart.UartError):
            legacy.probe()


class FlowTests(BaseCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-v2-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "smoke.sram"
        self.source.write_bytes(BLOB)
        self.channel = FakeSerial()
        self.serial.side_effect = None
        self.serial.return_value.__enter__.return_value = self.channel
        self.sender.side_effect = self.transfer
        self.which = self.patch(v2.shutil, "which", return_value="/fake/sx")
        self.patch(v2.uart.time, "monotonic", new=Clock())
        self.staged_paths = []
        self.loaded_response = loaded()

    def transfer(self, channel, path, executable, timeout):
        self.assertEqual(channel.buffer, b"C")
        self.assertEqual(channel.reset_count, 1)
        channel.buffer.clear()
        self.assertNotEqual(path, self.source)
        self.assertEqual(path.read_bytes(), BLOB)
        self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual((executable, timeout), ("/fake/sx", 120))
        self.staged_paths.append(path)
        channel.buffer.extend(self.loaded_response)
        return 0

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                status = v2.main(list(map(str, args)))
            except SystemExit as exc:
                status = exc.code
        return status, output.getvalue(), error.getvalue()

    def upload(self, run=False):
        args = ["upload", "--port", PORT, "--input", self.source]
        if run:
            args.append("--run")
        return self.invoke(*args)

    def assert_failed_without_run(self, outcome):
        status, output, error = outcome
        self.assertNotEqual(status, 0)
        self.assertEqual(output, "")
        self.assertIn("錯誤", error)
        self.assertNotIn("Traceback", error)
        self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_probe_does_not_require_input_or_sender(self):
        status, output, error = self.invoke("probe", "--port", PORT)
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertEqual((result["abi"], result["event"], result["ran"]), (2, "info", False))
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode()])
        self.sender.assert_not_called()
        self.which.assert_not_called()

    def test_upload_default_only_loads_and_removes_private_snapshot(self):
        status, output, error = self.upload()
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertEqual((result["abi"], result["kind"], result["event"], result["ran"]), (2, 1, "loaded", False))
        self.assertEqual(result["sha256"], DIGEST)
        self.assertEqual(result["package_sha256"], TRUSTED_SHA256)
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode(), f"U {NONCE} 1536\n".encode()])
        self.serial.assert_called_once_with(PORT, 115200, 15)
        self.assertTrue(self.staged_paths)
        self.assertTrue(all(not path.exists() for path in self.staged_paths))

    def test_explicit_upload_run_reports_smoke_not_ddr(self):
        status, output, error = self.upload(run=True)
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertTrue(result["ran"])
        self.assertEqual(result["smoke"]["ddr"], "off")
        self.assertNotIn("ddr_ready", result)
        self.assertTrue(self.channel.handed_off)
        self.assertEqual(self.channel.writes[-1], f"R {NONCE}\n".encode())

    def test_load_slot_compares_local_payload_and_does_not_send_xmodem(self):
        status, output, error = self.invoke("load-slot", "--port", PORT, "--slot", 0, "--input", self.source)
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertEqual((result["slot"], result["sha256"], result["ran"]), (0, DIGEST, False))
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode(), f"S {NONCE} 0\n".encode()])
        self.sender.assert_not_called()
        self.which.assert_not_called()

    def test_load_slot_run_uses_single_handoff(self):
        status, output, error = self.invoke("load-slot", "--port", PORT, "--slot", 4,
                                            "--input", self.source, "--run")
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["event"], "smoke")
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode(), f"S {NONCE} 4\n".encode(),
                                              f"R {NONCE}\n".encode()])
        self.assertTrue(self.channel.handed_off)

    def test_load_slot_wrong_digest_never_runs(self):
        self.channel.overrides["S"] = (f"BPI-SUP1 event=loading nonce={NONCE} transport=S\n".encode()
                                        + loaded(digest="0" * 64))
        self.assert_failed_without_run(self.invoke("load-slot", "--port", PORT, "--slot", 0,
                                                   "--input", self.source, "--run"))

    def test_wrong_local_package_rejected_before_open_for_both_transports(self):
        self.source.write_bytes(v2.package.build_package(b"x" * 583, 0x18000))
        self.assert_failed_without_run(self.upload(run=True))
        self.assert_failed_without_run(self.invoke("load-slot", "--port", PORT, "--slot", 0,
                                                   "--input", self.source, "--run"))
        self.serial.assert_not_called()

    def test_symlink_fifo_or_directory_input_never_opens_uart(self):
        self.source.unlink()
        for kind in ("symlink", "fifo", "directory"):
            if kind == "symlink":
                self.source.symlink_to(self.root / "missing.sram")
            elif kind == "fifo":
                os.mkfifo(self.source)
            else:
                self.source.mkdir()
            self.assert_failed_without_run(self.upload(run=True))
            self.source.rmdir() if kind == "directory" else self.source.unlink()
        self.serial.assert_not_called()

    def test_missing_sx_never_opens_uart(self):
        self.which.return_value = None
        self.assert_failed_without_run(self.upload(run=True))
        self.serial.assert_not_called()

    def test_wrong_probe_never_starts_load(self):
        self.channel.overrides["I"] = info(abi=1)
        self.assert_failed_without_run(self.upload(run=True))
        self.sender.assert_not_called()
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode()])

    def test_bad_loading_nonce_or_transport_never_starts_sender(self):
        for response in (f"BPI-SUP1 event=loading nonce={NONCE} transport=S\n".encode(),
                         b"BPI-SUP1 event=loading nonce=1 transport=U\n"):
            self.channel.overrides["U"] = response
            self.assert_failed_without_run(self.upload(run=True))
        self.sender.assert_not_called()

    def test_failed_transfer_never_waits_for_loaded_or_runs(self):
        self.sender.side_effect = v2.uart.UartError("模擬 sx 失敗")
        with mock.patch.object(v2.V2SupervisorSession, "finish_load") as finish:
            self.assert_failed_without_run(self.upload(run=True))
        finish.assert_not_called()

    def test_loaded_kind_failure_never_runs(self):
        self.loaded_response = loaded(kind="2")
        self.assert_failed_without_run(self.upload(run=True))

    def test_snapshot_is_unchanged_when_original_is_replaced(self):
        original_transfer = self.sender.side_effect

        def replace_source(*args):
            self.source.unlink()
            self.source.write_bytes("已替換".encode())
            return original_transfer(*args)

        self.sender.side_effect = replace_source
        self.assertEqual(self.upload()[0], 0)

    def test_invalid_cli_never_opens_uart(self):
        variants = (["probe", "--port", PORT, "--run"],
                    ["upload", "--port", PORT], ["load-slot", "--port", PORT, "--slot", "0"],
                    ["probe", "--port", "loop://"], ["probe", "--port", "ttyUSB0"],
                    ["probe", "--port", "/dev/ttyUSB*"], ["probe", "--port", PORT, "--timeout", "nan"],
                    ["load-slot", "--port", PORT, "--slot", "5", "--input", str(self.source)])
        for args in variants:
            with self.subTest(args=args):
                self.assert_failed_without_run(self.invoke(*args))
        self.serial.assert_not_called()

    def test_help_never_opens_uart(self):
        for args in (["--help"], ["probe", "--help"], ["upload", "--help"], ["load-slot", "--help"]):
            status, output, error = self.invoke(*args)
            self.assertEqual(status, 0, error)
            self.assertIn("用法", output)
        self.serial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
