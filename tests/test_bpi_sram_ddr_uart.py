#!/usr/bin/env python3
"""第二版 DDR UART 主機守門；串口與 sx 全部使用替身，不操作硬體。"""

from contextlib import redirect_stderr, redirect_stdout
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


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/bpi_sram_ddr_uart.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_ddr_uart", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ddr = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(MODULE_PATH.parent), *sys.path]):
    SPEC.loader.exec_module(ddr)

NONCE = 0x1234ABCD
PORT = "/dev/FAKE_TEST_ONLY"
BLOB = ddr.package.build_package(b"\x1a" * 128 + b"\x55" * 385, 1024)
DIGEST = ddr.package.parse_package(BLOB)["hash"]


class Clock:
    def __init__(self, step=0.001):
        self.value = 0
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


def info(nonce=NONCE, abi=2, capabilities=ddr.DdrSupervisorSession.CAPABILITIES):
    return (f"BPI-SUP1 event=info nonce={nonce} abi={abi} board=06180001 "
            f"capabilities={capabilities}\r\n").encode()


def loaded(nonce=NONCE, kind="2", digest=DIGEST, result="0"):
    return (f"BPI-SUP1 event=loaded nonce={nonce} result={result} "
            f"kind={kind} sha256={digest}\r\n").encode()


def ready(nonce=NONCE):
    return (f"BPI-SUP1 event=handoff nonce={nonce} entry=00030000\r\n"
            f"BPI-SPL2 event=ddr-ready nonce_hex={nonce:08x} abi=2 "
            "kind=2 preflight=unverified\r\n").encode()


class FakeSerial:
    def __init__(self):
        self.buffer = bytearray(loaded(1))
        self.writes = []
        self.overrides = {}
        self.reset_count = 0
        self.timeout = 0.1

    def reset_input_buffer(self):
        self.reset_count += 1
        self.buffer.clear()

    def write(self, data):
        self.writes.append(data)
        command, nonce, *arguments = data.decode().strip().split()
        nonce = int(nonce)
        if command in self.overrides:
            response = self.overrides[command]
        elif command == "I":
            response = info(nonce)
        elif command == "U":
            response = f"BPI-SUP1 event=loading nonce={nonce} transport=U\r\n".encode() + b"C"
        elif command == "R":
            response = ready(nonce)
        else:
            raise AssertionError("DDR 工具不得送出 SD 或其他命令")
        self.buffer.extend(response)
        return len(data)

    def read(self, size):
        if size != 1:
            raise AssertionError("不得預讀 XMODEM 或下一個控制事件")
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.channel = FakeSerial()
        self.session = ddr.DdrSupervisorSession(self.channel, 2, BLOB, Clock())
        patcher = mock.patch.object(ddr.uart.secrets, "randbits", return_value=NONCE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self):
        self.session.probe()
        self.session.begin_load("U", len(BLOB))
        self.assertEqual(self.channel.buffer, b"C")
        self.channel.buffer[:] = loaded()
        self.session.finish_load()

    def assert_no_run(self):
        with self.assertRaises(ddr.uart.UartError):
            self.session.run_ddr()
        self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_no_run_without_loaded_kind_and_digest(self):
        self.assert_no_run()
        self.session.probe()
        self.session.loaded_nonce = NONCE
        self.assert_no_run()

    def test_no_load_before_probe_or_result_before_loading(self):
        with self.assertRaises(ddr.uart.UartError):
            self.session.begin_load("U", len(BLOB))
        with self.assertRaises(ddr.uart.UartError):
            self.session.finish_load()
        self.session.probe()
        with self.assertRaises(ddr.uart.UartError):
            self.session.finish_load()
        self.assert_no_run()

    def test_reprobe_invalidates_loaded_type_and_digest(self):
        self.load()
        with mock.patch.object(ddr.uart.secrets, "randbits", return_value=99):
            self.session.probe()
        self.assertEqual(self.session.nonce, 99)
        self.assert_no_run()

    def test_new_or_invalid_load_invalidates_prior_success(self):
        for transport, value in (("U", len(BLOB)), ("U", 1024), ("S", 0), ("U", True)):
            with self.subTest(transport=transport, value=value):
                self.load()
                if (transport, value) == ("U", len(BLOB)):
                    self.session.begin_load(transport, value)
                else:
                    with self.assertRaises(ddr.uart.UartError):
                        self.session.begin_load(transport, value)
                self.assert_no_run()

    def test_no_sd_or_inherited_smoke_execution(self):
        self.load()
        with self.assertRaises(ddr.uart.UartError):
            self.session.run_smoke()
        self.assert_no_run()
        with self.assertRaises(ddr.uart.UartError):
            self.session._send("S", 0)
        self.assertFalse(any(data.startswith(b"S ") for data in self.channel.writes))

    def test_failed_finish_does_not_preserve_prior_success(self):
        self.load()
        self.session.begin_load("U", len(BLOB))
        self.channel.buffer[:] = loaded(kind="1")
        with self.assertRaises(ddr.uart.UartError):
            self.session.finish_load()
        self.assert_no_run()

    def test_nonce_or_digest_change_before_run_is_rejected(self):
        for attribute, value in (("nonce", 99), ("_loaded_digest", "0" * 64), ("_loaded_kind", 1)):
            with self.subTest(attribute=attribute):
                self.load()
                setattr(self.session, attribute, value)
                self.assert_no_run()

    def test_run_is_one_shot_even_after_handoff_failure(self):
        for response in (ready(), b"BPI-SUP1 event=handoff nonce=1 entry=00030000\n", b""):
            with self.subTest(response=response[:32]):
                self.channel.writes.clear()
                self.load()
                self.channel.overrides["R"] = response
                if response == ready():
                    self.assertEqual(self.session.run_ddr()["abi"], 2)
                else:
                    with self.assertRaises(ddr.uart.UartError):
                        self.session.run_ddr()
                with self.assertRaises(ddr.uart.UartError):
                    self.session.run_ddr()
                self.assertEqual(self.channel.writes.count(f"R {NONCE}\n".encode()), 1)

    def test_full_package_required_by_session_not_just_metadata(self):
        for blob in (BLOB[:-1], ddr.base.build_package(b"abc", 16), {"kind": "ddr", "hash": DIGEST}):
            with self.subTest(kind=type(blob).__name__), self.assertRaises(ddr.base.PackageError):
                ddr.DdrSupervisorSession(self.channel, 1, blob)
        self.assertEqual(self.channel.writes, [])

    def test_legacy_parent_stays_strict(self):
        self.assertEqual(ddr.uart.SupervisorSession.CONTROL_ABI, 1)
        self.assertEqual(ddr.uart.SupervisorSession.CAPABILITIES, "uart-ram,sd-read,smoke-run")
        legacy = ddr.uart.SupervisorSession(self.channel, 2, Clock())
        with self.assertRaises(ddr.uart.UartError):
            legacy.probe()
        with self.assertRaises(ddr.base.PackageError):
            ddr.base.parse_package(BLOB)

    def test_nonce_boundaries_bind_info_load_handoff_and_ready(self):
        for nonce in (0, 1, 0xFFFFFFFF):
            with self.subTest(nonce=nonce), mock.patch.object(ddr.uart.secrets, "randbits", return_value=nonce):
                channel = FakeSerial()
                session = ddr.DdrSupervisorSession(channel, 2, BLOB, Clock())
                session.probe()
                session.begin_load("U", len(BLOB))
                channel.buffer[:] = loaded(nonce)
                session.finish_load()
                self.assertEqual(session.run_ddr()["nonce_hex"], f"{nonce:08x}")
                self.assertEqual(channel.writes[-1], f"R {nonce}\n".encode())


class FlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-ddr-uart-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "input.spl2"
        self.source.write_bytes(BLOB)
        self.channel = FakeSerial()
        self.serial = self.patch(ddr.uart, "open_serial")
        self.serial.return_value.__enter__.return_value = self.channel
        self.sender = self.patch(ddr.uart, "send_xmodem", side_effect=self.transfer)
        self.which = self.patch(ddr.shutil, "which", return_value="/fake/sx")
        self.patch(ddr.uart.secrets, "randbits", return_value=NONCE)
        self.patch(ddr.uart.time, "monotonic", new=Clock())
        self.staged_paths = []
        self.loaded_response = loaded()

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def transfer(self, channel, path, executable, timeout):
        self.assertEqual(channel.buffer, b"C", "loading 後的 C 必須留給 sx")
        self.assertEqual(channel.reset_count, self.expected_reset_count)
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
        self.expected_reset_count = self.channel.reset_count + 1
        with redirect_stdout(output), redirect_stderr(error):
            try:
                status = ddr.main(list(map(str, args)))
            except SystemExit as exc:
                status = exc.code
        return status, output.getvalue(), error.getvalue()

    def upload(self, run=True):
        args = ["upload", "--port", PORT, "--input", self.source]
        if run:
            args.append("--run")
        return self.invoke(*args)

    def assert_failed(self, outcome, *, before_run=True):
        status, output, error = outcome
        self.assertNotEqual(status, 0)
        self.assertEqual(output, "")
        self.assertIn("錯誤", error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn("PRIVATE_UART", error)
        if before_run:
            self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_upload_without_run_verifies_kind_hash_and_cleans_snapshot(self):
        status, output, error = self.upload(run=False)
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertEqual((result["event"], result["abi"], result["kind"]), ("loaded", 2, 2))
        self.assertFalse(result["ran"])
        self.assertFalse(result["ddr_result_verified"])
        self.assertEqual(result["package"], ddr.package.parse_package(BLOB))
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode(), f"U {NONCE} {len(BLOB)}\n".encode()])
        self.serial.assert_called_once_with(PORT, 115200, 15)
        self.assertTrue(self.staged_paths)
        self.assertTrue(all(not path.exists() for path in self.staged_paths))

    def test_explicit_run_only_reports_ddr_ready_not_ddr_test_pass(self):
        status, output, error = self.upload()
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertEqual(result["event"], "ddr-ready")
        self.assertEqual(result["ddr_ready"], {"event": "ddr-ready", "abi": 2,
                                               "nonce_hex": f"{NONCE:08x}", "kind": 2,
                                               "preflight": "unverified"})
        self.assertTrue(result["ran"])
        self.assertFalse(result["ddr_result_verified"])
        self.assertNotIn("smoke", result)
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode(),
                                              f"U {NONCE} {len(BLOB)}\n".encode(),
                                              f"R {NONCE}\n".encode()])

    def test_wrong_probe_abi_board_nonce_or_capabilities_never_loads(self):
        variants = (info(abi=1), info(NONCE + 1), info().replace(b"06180001", b"06180002"),
                    info(capabilities=ddr.uart.CAPABILITIES), info(capabilities="ddr-run"),
                    info(capabilities=ddr.DdrSupervisorSession.CAPABILITIES + ",unknown"),
                    b"BPI-SUP1 event=ready abi=1 board=06180001\n" + info())
        for response in variants:
            with self.subTest(response=response[:40]):
                self.channel.writes.clear()
                self.channel.overrides["I"] = response
                self.assert_failed(self.upload())
                self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode()])
                self.sender.assert_not_called()

    def test_valid_v2_ready_before_info(self):
        self.channel.overrides["I"] = b"BPI-SUP1 event=ready abi=2 board=06180001 ddr=off sd_write=off\n" + info()
        self.assertEqual(self.upload(run=False)[0], 0)

    def test_bad_loading_does_not_start_sender(self):
        for response in (loaded(), b"", b"BPI-SUP1 event=loading nonce=1 transport=U\n",
                         f"BPI-SUP1 event=loading nonce={NONCE} transport=S\n".encode()):
            with self.subTest(response=response[:40]):
                self.channel.writes.clear()
                self.channel.overrides["U"] = response
                self.assert_failed(self.upload())
                self.sender.assert_not_called()

    def test_loaded_requires_success_same_nonce_ddr_type_and_exact_local_hash(self):
        variants = [loaded(NONCE + 1), loaded(result="-1", kind="0", digest="none"),
                    loaded(result="00"), loaded(result="1"),
                    f"BPI-SUP1 event=loaded nonce={NONCE} result=0\n".encode(),
                    loaded().replace(b" kind=2", b""), loaded().replace(b" sha256=" + DIGEST.encode(), b""),
                    loaded().replace(b" nonce=" + str(NONCE).encode(), b"")]
        variants.extend(loaded(kind=kind) for kind in ("1", "0", "02", "ddr", "-2"))
        variants.extend(loaded(digest=digest) for digest in ("none", "0" * 64, DIGEST.upper(), DIGEST[:-1], "g" * 64))
        for response in variants:
            with self.subTest(response=response[:70]):
                self.channel.writes.clear()
                self.loaded_response = response
                self.assert_failed(self.upload())

    def test_malformed_or_untrusted_loaded_lines_never_run(self):
        variants = (b"PRIVATE_UART " + loaded(), loaded().replace(b"BPI-SUP1", b"BPI-SPL2"),
                    loaded().replace(b"event=loaded", b"event=smoke"),
                    loaded().replace(b"kind=2", b"kind=2 kind=2"),
                    loaded().replace(b"result=0", b"result=0 command=$(id)"),
                    b"\x18" * 4 + loaded(), loaded().replace(b"kind=2", b"kind=2\x00"),
                    b"x" * 513 + b"\n", loaded()[:-2], b"", b"x\n" * 400)
        with mock.patch.object(ddr.uart, "MAX_CONTROL_BYTES", 512):
            for response in variants:
                with self.subTest(response=response[:32]):
                    self.channel.writes.clear()
                    self.loaded_response = response
                    self.assert_failed(self.upload())

    def test_wrong_handoff_or_ready_never_reports_success_or_retries(self):
        variants = (ready(NONCE + 1), ready().replace(b"entry=00030000", b"entry=40000000"),
                    ready().replace(b"abi=2", b"abi=1"), ready().replace(b" abi=2", b""),
                    ready().replace(b" kind=2", b""), ready().replace(b"kind=2", b"kind=1"),
                    ready().replace(b" preflight=unverified", b""),
                    ready().replace(b"preflight=unverified", b"preflight=pass"),
                    ready().replace(f"nonce_hex={NONCE:08x}".encode(), b"nonce_hex=00000000"),
                    ready().replace(b"event=ddr-ready", b"event=smoke"),
                    ready().replace(b"BPI-SPL2", b"BPI-SUP1"),
                    ready().replace(b"abi=2", b"abi=2 abi=2"),
                    ready().replace(b"BPI-SPL2", b"PRIVATE_UART BPI-SPL2"),
                    ready().split(b"\n", 1)[0] + b"\n", b"")
        for response in variants:
            with self.subTest(response=response[:50]):
                self.channel.writes.clear()
                self.channel.overrides["R"] = response
                self.assert_failed(self.upload(), before_run=False)
                self.assertEqual(self.channel.writes.count(f"R {NONCE}\n".encode()), 1)

    def test_extra_untrusted_ready_fields_not_copied_into_result(self):
        self.channel.overrides["R"] = ready().replace(b"abi=2", b"abi=2 private=PRIVATE_UART")
        status, output, error = self.upload()
        self.assertEqual(status, 0, error)
        self.assertNotIn("PRIVATE_UART", output)

    def test_invalid_full_package_rejected_before_serial(self):
        variants = [b"", BLOB[:-1], BLOB + b"\0", BLOB[:-1] + b"\x01",
                    ddr.base.build_package(b"abc", 16)]
        corrupt = bytearray(BLOB)
        corrupt[512] ^= 1
        variants.append(bytes(corrupt))
        for offset, value in ((8, 1), (36, 1), (16, 0), (28, 0), (32, 1), (24, 15), (72, 1)):
            changed = bytearray(BLOB)
            struct.pack_into("<I", changed, offset, value)
            struct.pack_into("<I", changed, 508, zlib.crc32(changed[:508]))
            variants.append(bytes(changed))
        for blob in variants:
            with self.subTest(size=len(blob)):
                self.source.write_bytes(blob)
                self.assert_failed(self.upload())
                self.serial.assert_not_called()
                self.sender.assert_not_called()
                self.which.assert_not_called()

    def test_nonregular_input_rejected_without_opening_serial(self):
        other = self.root / "other.spl2"
        other.write_bytes(BLOB)
        self.source.unlink()
        self.source.symlink_to(other)
        self.assert_failed(self.upload())
        self.source.unlink()
        os.mkfifo(self.source)
        self.assert_failed(self.upload())
        self.serial.assert_not_called()
        self.sender.assert_not_called()

    def test_private_snapshot_survives_original_replacement(self):
        def transfer(channel, path, executable, timeout):
            self.source.unlink()
            self.source.write_bytes(b"PRIVATE_UART")
            return self.transfer(channel, path, executable, timeout)
        self.sender.side_effect = transfer
        status, _, error = self.upload()
        self.assertEqual(status, 0, error)

    def test_missing_sx_prevents_serial_open(self):
        self.which.return_value = None
        self.assert_failed(self.upload())
        self.serial.assert_not_called()
        self.sender.assert_not_called()

    def test_sender_failure_cancellation_or_timeout_never_runs(self):
        for exception in (ddr.uart.UartError("傳輸逾時"), OSError("PRIVATE_UART"),
                          ddr.uart.termios.error("PRIVATE_UART"),
                          ddr.uart.subprocess.SubprocessError("PRIVATE_UART"), KeyboardInterrupt()):
            with self.subTest(exception=type(exception).__name__):
                paths = []
                def fail(channel, path, executable, timeout):
                    self.assertEqual(channel.buffer, b"C")
                    paths.append(path)
                    channel.buffer[:] = loaded()
                    raise exception
                self.sender.side_effect = fail
                self.channel.writes.clear()
                outcome = self.upload()
                self.assert_failed(outcome)
                if isinstance(exception, KeyboardInterrupt):
                    self.assertEqual(outcome[0], 130)
                self.assertTrue(paths)
                self.assertTrue(all(not path.exists() for path in paths))

    def test_probe_timeout_does_not_send_upload(self):
        self.channel.overrides["I"] = b""
        self.assert_failed(self.upload())
        self.sender.assert_not_called()
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode()])

    def test_short_control_write_stops_flow(self):
        self.channel.write = mock.Mock(return_value=0)
        self.assert_failed(self.upload())
        self.sender.assert_not_called()

    def test_only_explicit_upload_options_are_available(self):
        prefix = ("upload", "--port", PORT, "--input", self.source)
        variants = [("probe", "--port", PORT), ("load-slot", "--port", PORT, "--slot", "0"),
                    ("upload", "--input", self.source), ("upload", "--port", PORT),
                    (*prefix, "--slot", "0"), (*prefix, "--power"), (*prefix, "--ddr-clock", "480"),
                    (*prefix, "--timeout", "nan"), (*prefix, "--timeout", "0"),
                    (*prefix, "--transfer-timeout", "121"), (*prefix, "--fall-back")]
        variants.extend(("upload", "--port", port, "--input", self.source)
                        for port in ("auto", "loop://", "/dev/ttyUSB*", "ttyUSB0"))
        for args in variants:
            with self.subTest(args=args):
                self.assert_failed(self.invoke(*args))
                self.serial.assert_not_called()
                self.sender.assert_not_called()

    def test_help_is_chinese_and_never_opens_serial(self):
        for args in (("--help",), ("upload", "--help")):
            status, output, error = self.invoke(*args)
            self.assertEqual(status, 0, error)
            self.assertIn("用法：", output)
            self.assertNotIn("usage:", output)
        self.serial.assert_not_called()
        self.sender.assert_not_called()

    def test_import_never_opens_serial_or_starts_process(self):
        module = importlib.util.module_from_spec(SPEC)
        with mock.patch.object(ddr.uart.subprocess, "Popen") as process:
            SPEC.loader.exec_module(module)
        self.serial.assert_not_called()
        self.sender.assert_not_called()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
