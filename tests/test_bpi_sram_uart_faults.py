#!/usr/bin/env python3
"""固定故障注入的離線測試；串口、sx 與時鐘皆用替身，不接觸硬體。"""

from contextlib import redirect_stderr, redirect_stdout
from functools import partial
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


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "bpi_sram_uart_faults.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_uart_faults", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
faults = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(MODULE_PATH.parent), *sys.path]):
    SPEC.loader.exec_module(faults)
uart = faults.uart
NONCE = 0x1234ABCD
NEXT_NONCE = 0x2345BCDE
PORT = "/dev/FAKE_TEST_ONLY"
CASES = ("bad-header-crc", "bad-payload-sha256", "truncated-file",
         "cancel-before-payload", "no-payload-timeout")
MODEM_PREFIX = b"C\x15\x18\x08\x06\r\n"
REJECT = b"BPI-SUP1 event=reject reason=command_or_state\r\n"


class Clock:
    def __init__(self, step=0.001):
        self.value = 0
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


def info(nonce=NONCE):
    return (f"BPI-SUP1 event=info nonce={nonce} abi=1 board=06180001 "
            "capabilities=uart-ram,sd-read,smoke-run\r\n").encode()


def loaded(nonce=NONCE, result=-1):
    return f"BPI-SUP1 event=loaded nonce={nonce} result={result}\r\n".encode()


class FakeSerial:
    """沿用 UART 測試的逐位元組緩衝與命令回應，不實作 XMODEM。"""

    def __init__(self, case="bad-header-crc"):
        self.case = case
        self.buffer = bytearray(loaded(nonce=1, result=0))
        self.writes = []
        self.events = []
        self.overrides = {}
        self.loaded_response = loaded()
        self.cancel_count = 3
        self.reset_count = 0
        self.timeout = 0.1

    def reset_input_buffer(self):
        self.reset_count += 1
        self.buffer.clear()

    def read(self, size):
        if size != 1:
            raise AssertionError("控制讀取不得預讀下一階段資料")
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        self.events.append(("rx", value))
        return value

    def write(self, data):
        self.writes.append(data)
        self.events.append(("tx", data))
        if data == b"\x18" * 3:
            if self.cancel_count == 3:
                self.buffer.extend(self.loaded_response)
            return self.cancel_count
        command, raw_nonce, *values = data.decode("ascii").strip().split()
        nonce = int(raw_nonce)
        if command in self.overrides:
            response = self.overrides[command]
            if callable(response):
                response = response(nonce)
        elif command == "I":
            response = info(nonce)
        elif command == "U":
            response = f"BPI-SUP1 event=loading nonce={nonce} transport=U\r\nC".encode()
            if self.case == "no-payload-timeout":
                response += self.loaded_response
        elif command == "R":
            response = REJECT
        else:
            raise AssertionError("替身拒絕非測試範圍的命令")
        self.buffer.extend(response)
        return len(data)


class FaultTestCase(unittest.TestCase):
    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def setUp(self):
        self.serial = self.patch(uart, "open_serial", side_effect=AssertionError("禁止實體串口"))
        self.xmodem = self.patch(uart, "send_xmodem", side_effect=AssertionError("禁止實體傳輸"))
        self.patch(uart.subprocess, "Popen", side_effect=AssertionError("禁止啟動外部程序"))
        self.blob = faults.package.build_package(bytes(range(256)) * 3, 768)
        # 只替換受信任雜湊常數；SHA-256 計算與封包解析仍使用真實實作。
        self.trusted_hash = faults.PACKAGE_SHA256
        self.patch(faults, "PACKAGE_SHA256", new=hashlib.sha256(self.blob).hexdigest())


class PayloadTests(FaultTestCase):
    def test_fixed_case_allowlist(self):
        self.assertEqual(faults.CASES, CASES)
        self.assertEqual(len(self.blob), 1536)

    def test_bad_header_crc_changes_only_crc_bit(self):
        payload = faults.fault_payload(self.blob, "bad-header-crc")
        expected = bytearray(self.blob)
        expected[508] ^= 1
        self.assertEqual(payload, bytes(expected))
        self.assertIsInstance(payload, bytes)
        with self.assertRaisesRegex(faults.package.PackageError, "標頭 CRC"):
            faults.package.parse_package(payload)

    def test_bad_payload_sha256_changes_only_payload_bit(self):
        payload = faults.fault_payload(self.blob, "bad-payload-sha256")
        expected = bytearray(self.blob)
        expected[512] ^= 1
        self.assertEqual(payload, bytes(expected))
        self.assertIsInstance(payload, bytes)
        with self.assertRaisesRegex(faults.package.PackageError, "負載 SHA-256"):
            faults.package.parse_package(payload)

    def test_truncated_file_keeps_only_first_1024_bytes(self):
        payload = faults.fault_payload(self.blob, "truncated-file")
        self.assertEqual(payload, self.blob[:1024])
        with self.assertRaisesRegex(faults.package.PackageError, "封包總長不符"):
            faults.package.parse_package(payload)

    def test_cancel_before_payload_has_no_payload(self):
        self.assertIsNone(faults.fault_payload(self.blob, "cancel-before-payload"))

    def test_no_payload_timeout_has_no_payload(self):
        self.assertIsNone(faults.fault_payload(self.blob, "no-payload-timeout"))

    def test_all_variants_preserve_mutable_source_and_validate_before_mutation(self):
        for case in CASES:
            with self.subTest(case=case):
                source = bytearray(self.blob)
                with mock.patch.object(faults.package, "parse_package",
                                       wraps=faults.package.parse_package) as parser:
                    faults.fault_payload(source, case)
                parser.assert_called_once_with(self.blob)
                self.assertEqual(source, self.blob)

    def test_unknown_case_rejected_before_hash_or_parser(self):
        with mock.patch.object(faults.package, "parse_package") as parser:
            with self.assertRaisesRegex(uart.UartError, "固定清單"):
                faults.fault_payload(None, "unknown-case")
        parser.assert_not_called()

    def test_wrong_length_rejected_for_every_case_before_parser(self):
        for case in CASES:
            for blob in (b"", self.blob[:1024], self.blob[:-1], self.blob + b"\0"):
                with self.subTest(case=case, size=len(blob)):
                    with mock.patch.object(faults.package, "parse_package") as parser:
                        with self.assertRaisesRegex(uart.UartError, "固定 smoke 封包"):
                            faults.fault_payload(blob, case)
                    parser.assert_not_called()

    def test_wrong_hash_rejected_for_every_case_before_parser(self):
        other = faults.package.build_package(b"\x55" * 768, 768)
        self.assertEqual(len(other), 1536)
        for case in CASES:
            with self.subTest(case=case), mock.patch.object(faults.package, "parse_package") as parser:
                with self.assertRaisesRegex(uart.UartError, "固定 smoke 封包"):
                    faults.fault_payload(other, case)
                parser.assert_not_called()

    def test_synthetic_package_is_not_accepted_by_production_hash(self):
        with mock.patch.object(faults, "PACKAGE_SHA256", self.trusted_hash):
            with self.assertRaisesRegex(uart.UartError, "固定 smoke 封包"):
                faults.fault_payload(self.blob, "bad-header-crc")

    def test_trusted_hash_does_not_bypass_parser_for_any_case(self):
        broken = bytearray(self.blob)
        broken[508] ^= 1
        with mock.patch.object(faults, "PACKAGE_SHA256", hashlib.sha256(broken).hexdigest()):
            for case in CASES:
                with self.subTest(case=case), self.assertRaisesRegex(faults.package.PackageError, "標頭 CRC"):
                    faults.fault_payload(broken, case)


class PhaseLineTests(FaultTestCase):
    def channel(self, data):
        channel = FakeSerial()
        channel.buffer[:] = data
        return channel

    def test_plain_control_line_does_not_consume_next_phase(self):
        channel = self.channel(loaded() + b"C")
        self.assertEqual(faults.phase_line(channel, clock=Clock()), uart.parse_control_line(loaded()))
        self.assertEqual(channel.buffer, b"C")

    def test_only_exact_modem_prefix_allowlist_is_skipped(self):
        self.assertEqual(faults.MODEM_PREFIX, frozenset(MODEM_PREFIX))
        for prefix in [bytes([byte]) for byte in MODEM_PREFIX] + [MODEM_PREFIX * 2]:
            with self.subTest(prefix=prefix):
                channel = self.channel(prefix + loaded() + b"C")
                self.assertEqual(faults.phase_line(channel, modem=True, clock=Clock()),
                                 uart.parse_control_line(loaded()))
                self.assertEqual(channel.buffer, b"C")

    def test_every_unlisted_prefix_byte_is_rejected(self):
        for byte in set(range(256)) - set(MODEM_PREFIX):
            with self.subTest(byte=byte), self.assertRaises(uart.UartError):
                faults.phase_line(self.channel(bytes([byte]) + loaded()), modem=True, clock=Clock())

    def test_modem_prefix_is_not_allowed_without_modem_mode(self):
        for byte in MODEM_PREFIX:
            with self.subTest(byte=byte), self.assertRaises(uart.UartError):
                faults.phase_line(self.channel(bytes([byte]) + loaded()), clock=Clock())

    def test_modem_control_bytes_are_not_skipped_inside_a_line(self):
        for byte in MODEM_PREFIX:
            raw = loaded().replace(b"BPI-SUP1", b"BPI-" + bytes([byte]) + b"SUP1")
            with self.subTest(byte=byte), self.assertRaises(uart.UartError):
                faults.phase_line(self.channel(raw), modem=True, clock=Clock())

    def test_modem_prefix_4096_boundary(self):
        channel = self.channel(b"C" * 4096 + loaded())
        self.assertEqual(faults.phase_line(channel, modem=True, clock=Clock()),
                         uart.parse_control_line(loaded()))
        channel = self.channel(b"C" * 4097 + loaded())
        with self.assertRaisesRegex(uart.UartError, "控制前綴超過上限"):
            faults.phase_line(channel, modem=True, clock=Clock())
        self.assertEqual(channel.buffer, loaded())

    def test_line_byte_limit_includes_newline(self):
        start = b"BPI-SUP1 event=loaded padding="
        raw = start + b"x" * (uart.MAX_LINE_BYTES - len(start) - 1) + b"\n"
        self.assertEqual(len(raw), uart.MAX_LINE_BYTES)
        self.assertEqual(faults.phase_line(self.channel(raw), clock=Clock()),
                         uart.parse_control_line(raw))
        with self.assertRaisesRegex(uart.UartError, "控制行超過上限"):
            faults.phase_line(self.channel(raw[:-1] + b"x\n"), clock=Clock())

    def test_oversized_read_is_rejected(self):
        channel = mock.Mock()
        channel.read.return_value = b"CC"
        with self.assertRaisesRegex(uart.UartError, "超過單位元組"):
            faults.phase_line(channel, modem=True, clock=Clock())
        channel.read.assert_called_once_with(1)

    def test_timeout_bounds_empty_partial_and_continuous_prefix_input(self):
        for data in (b"", b"BPI-SUP1 event=loaded", b"C" * 100):
            with self.subTest(data=data):
                channel = self.channel(data)
                with self.assertRaisesRegex(uart.UartError, "觀測期限"):
                    faults.phase_line(channel, timeout=4, modem=True, clock=Clock(step=1))
                self.assertEqual(len(channel.events), 3)

    def test_default_timeout_is_40_seconds(self):
        channel = self.channel(b"")
        with self.assertRaisesRegex(uart.UartError, "觀測期限"):
            faults.phase_line(channel, clock=Clock(step=10))
        self.assertEqual(len(channel.events), 3)

    def test_boot_logs_and_malformed_control_lines_are_not_skipped(self):
        for raw in (b"U-Boot\r\n", b"Linux " + loaded(),
                    b"BPI-SUP2 event=loaded\n", b"BPI-SUP1 event=loaded event=info\n",
                    b"BPI-SUP1 nonce=1\n", b"BPI-SUP1 event=loaded\x00\n"):
            with self.subTest(raw=raw), self.assertRaises(uart.UartError):
                faults.phase_line(self.channel(raw + loaded()), modem=True, clock=Clock())


class FlowTestCase(FaultTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.patch(uart.time, "monotonic", new=self.clock)
        self.nonces = self.patch(uart.secrets, "randbits")
        self.new_flow()

    def new_flow(self, case="bad-header-crc"):
        self.channel = FakeSerial(case)
        self.sender = mock.Mock(side_effect=self.transfer)
        self.nonces.side_effect = (NONCE, NEXT_NONCE)
        self.nonces.reset_mock()

    def transfer(self, channel, payload):
        self.assertEqual(channel.buffer, b"C", "載入交握不得吃掉 XMODEM 前綴")
        self.assertEqual(channel.reset_count, 1)
        self.assertEqual(channel.writes, [f"I {NONCE}\n".encode(), f"U {NONCE} 1536\n".encode()])
        channel.buffer.clear()
        channel.buffer.extend(MODEM_PREFIX + channel.loaded_response)

    def run_fault(self, *, clock=None):
        return faults.run_case(self.channel, self.blob, self.channel.case, self.sender,
                               clock=clock or self.clock)

    def assert_no_run(self):
        self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def assert_recovery(self, result):
        expected = [f"I {NONCE}\n".encode(), f"U {NONCE} 1536\n".encode()]
        if self.channel.case == "cancel-before-payload":
            expected.append(b"\x18" * 3)
        expected += [f"R {NONCE}\n".encode(), f"I {NEXT_NONCE}\n".encode()]
        self.assertEqual(self.channel.writes, expected)
        self.assertEqual(self.channel.reset_count, 2)
        self.assertEqual(self.nonces.call_args_list, [mock.call(32), mock.call(32)])
        r_index = self.channel.events.index(("tx", f"R {NONCE}\n".encode()))
        before_run = b"".join(value for direction, value in self.channel.events[:r_index] if direction == "rx")
        self.assertTrue(before_run.endswith(loaded()), "讀完同一 nonce 的失敗行後才可發送 R")
        probe_index = self.channel.events.index(("tx", f"I {NEXT_NONCE}\n".encode()))
        before_probe = b"".join(value for direction, value in self.channel.events[r_index:probe_index]
                                if direction == "rx")
        self.assertEqual(before_probe, REJECT)
        self.assertEqual(result["case"], self.channel.case)
        self.assertEqual(result["loaded_nonce"], NONCE)
        self.assertEqual(result["loaded_result"], -1)
        self.assertIs(result["run_rejected"], True)
        self.assertEqual(result["reprobe"], {"nonce": NEXT_NONCE, "abi": 1, "board": "06180001",
                                             "capabilities": ["uart-ram", "sd-read", "smoke-run"]})


class RunCaseTests(FlowTestCase):
    def test_all_five_variants_require_reject_then_reprobe_with_new_nonce(self):
        for case in CASES:
            with self.subTest(case=case):
                self.new_flow(case)
                source = bytearray(self.blob)
                result = faults.run_case(self.channel, source, case, self.sender, clock=self.clock)
                self.assert_recovery(result)
                self.assertEqual(source, self.blob)
                self.assertIsNone(result["sender_error"])
                self.assertIs(result["return_within_15_seconds"], True)
                payload = faults.fault_payload(self.blob, case)
                if payload is None:
                    self.sender.assert_not_called()
                    self.assertIsNone(result["fault_payload_sha256"])
                else:
                    self.sender.assert_called_once_with(self.channel, payload)
                    self.assertEqual(result["fault_payload_sha256"], hashlib.sha256(payload).hexdigest())

    def test_loaded_zero_never_sends_run_even_after_stale_failure(self):
        self.channel.buffer[:] = loaded()
        self.channel.loaded_response = loaded(result=0)
        with self.assertRaisesRegex(uart.UartError, "禁止發送執行命令"):
            self.run_fault()
        self.assert_no_run()
        self.assertEqual(self.channel.reset_count, 1)
        self.nonces.assert_called_once_with(32)

    def test_wrong_or_missing_nonce_never_sends_run(self):
        for response in (loaded(NONCE + 1), loaded().replace(f" nonce={NONCE}".encode(), b"")):
            with self.subTest(response=response):
                self.new_flow()
                self.channel.loaded_response = response
                with self.assertRaisesRegex(uart.UartError, "禁止發送執行命令"):
                    self.run_fault()
                self.assert_no_run()

    def test_unexpected_identity_event_or_result_never_sends_run(self):
        responses = [loaded().replace(b"BPI-SUP1", b"BPI-SPL2"),
                     loaded().replace(b"event=loaded", b"event=handoff"),
                     loaded().replace(b"event=loaded", b"event=loading"), REJECT,
                     loaded().replace(b" result=-1", b"")]
        responses += [loaded(result=result) for result in (1, -2, "-01", "pass")]
        for response in responses:
            with self.subTest(response=response):
                self.new_flow()
                self.channel.loaded_response = response
                with self.assertRaisesRegex(uart.UartError, "禁止發送執行命令"):
                    self.run_fault()
                self.assert_no_run()
                self.nonces.assert_called_once_with(32)

    def test_sender_error_can_recover_only_with_firmware_failure_evidence(self):
        def fail(channel, payload):
            self.transfer(channel, payload)
            raise uart.UartError("替身傳輸失敗的內部診斷")
        self.sender.side_effect = fail
        result = self.run_fault()
        self.assert_recovery(result)
        self.assertIn("sx", result["sender_error"])
        self.assertNotIn("內部診斷", result["sender_error"])

    def test_sender_error_without_failure_evidence_never_sends_run(self):
        self.sender.side_effect = uart.UartError("替身傳輸失敗")
        with self.assertRaisesRegex(uart.UartError, "觀測期限"):
            self.run_fault()
        self.assert_no_run()
        self.nonces.assert_called_once_with(32)

    def test_timeout_case_without_loaded_evidence_never_sends_run(self):
        self.new_flow("no-payload-timeout")
        self.channel.loaded_response = b""
        with self.assertRaisesRegex(uart.UartError, "觀測期限"):
            self.run_fault()
        self.sender.assert_not_called()
        self.assert_no_run()

    def test_incomplete_cancel_stops_without_run_or_reprobe(self):
        self.new_flow("cancel-before-payload")
        self.channel.cancel_count = 2
        with self.assertRaisesRegex(uart.UartError, "取消命令未完整送出"):
            self.run_fault()
        self.sender.assert_not_called()
        self.assert_no_run()
        self.nonces.assert_called_once_with(32)
        self.assertEqual(self.channel.writes[-1], b"\x18" * 3)

    def test_probe_or_loading_mismatch_prevents_sender_and_run(self):
        for command, response in (("I", info(NONCE + 1)), ("U", loaded()),
                                  ("U", f"BPI-SUP1 event=loading nonce={NONCE} transport=S\n".encode())):
            with self.subTest(command=command, response=response):
                self.new_flow()
                self.channel.overrides[command] = response
                with self.assertRaises(uart.UartError):
                    self.run_fault()
                self.sender.assert_not_called()
                self.assert_no_run()

    def test_unknown_case_or_wrong_source_stops_before_probe(self):
        for blob, case in ((self.blob, "unknown-case"), (self.blob[:-1], CASES[0]),
                           (b"\0" * 1536, CASES[0])):
            with self.subTest(case=case, size=len(blob)), self.assertRaises(uart.UartError):
                faults.run_case(self.channel, blob, case, self.sender, clock=self.clock)
            self.assertEqual(self.channel.writes, [])
            self.sender.assert_not_called()
            self.nonces.assert_not_called()

    def test_only_exact_run_rejection_allows_reprobe(self):
        for response in (loaded(), REJECT.replace(b"command_or_state", b"other"),
                         REJECT.replace(b"BPI-SUP1", b"BPI-SPL2"),
                         REJECT.replace(b"\r\n", b" nonce=1\r\n"), b"C" + REJECT, b""):
            with self.subTest(response=response):
                self.new_flow()
                self.channel.overrides["R"] = response
                with self.assertRaises(uart.UartError):
                    self.run_fault()
                self.assertEqual(self.channel.writes[-1], f"R {NONCE}\n".encode())
                self.assertEqual(self.channel.reset_count, 1)
                self.nonces.assert_called_once_with(32)

    def test_reprobe_rejects_old_nonce_and_does_not_retry_run(self):
        self.channel.overrides["I"] = info()
        with self.assertRaisesRegex(uart.UartError, "nonce 不符"):
            self.run_fault()
        self.assertEqual(self.channel.writes[-2:], [f"R {NONCE}\n".encode(), f"I {NEXT_NONCE}\n".encode()])
        self.assertEqual(self.channel.reset_count, 2)

    def test_15_second_threshold_uses_unrounded_elapsed_and_includes_boundary(self):
        for elapsed in (14.999, 15, 15.0004, 16):
            with self.subTest(elapsed=elapsed):
                self.new_flow()
                clock = mock.Mock(side_effect=(0, elapsed))
                with mock.patch.object(faults, "phase_line", side_effect=(
                        uart.parse_control_line(loaded()), uart.parse_control_line(REJECT))):
                    result = self.run_fault(clock=clock)
                self.assertEqual(result["elapsed_seconds"], round(elapsed, 3))
                self.assertIs(result["return_within_15_seconds"], elapsed <= 15)


class MainTests(FlowTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-fault-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "input.sram"
        self.source.write_bytes(self.blob)
        self.source.chmod(0o400)
        self.output = self.root / "evidence"
        self.serial.side_effect = None
        self.serial.return_value.__enter__.return_value = self.channel
        self.xmodem.side_effect = self.transfer_file
        self.which = self.patch(faults.shutil, "which", return_value="/fake/sx")
        self.reader = self.patch(faults.package, "read_regular_file", wraps=faults.package.read_regular_file)
        self.runner = self.patch(faults, "run_case", side_effect=partial(faults.run_case, clock=self.clock))
        self.staged_paths = []

    def transfer_file(self, channel, path, executable, timeout):
        self.assertIsInstance(channel, faults.TraceChannel)
        self.assertIs(channel.channel, self.channel)
        self.assertNotEqual(path, self.source)
        self.assertEqual(path.read_bytes(), faults.fault_payload(self.blob, self.channel.case))
        self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(executable, "/fake/sx")
        self.assertEqual(timeout, 35)
        self.staged_paths.append(path)
        self.transfer(channel, path.read_bytes())

    def invoke(self, *extra, confirm=True, case=CASES[0], source=None, output=None, port=PORT):
        args = ["--port", port, "--input", str(source or self.source),
                "--output", str(output or self.output), "--case", case]
        if confirm:
            args.append("--confirm-fault-injection")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = faults.main(args + list(extra))
            except SystemExit as exc:
                status = exc.code
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_preflight_failure(self, outcome):
        status, output, error = outcome
        self.assertNotEqual(status, 0)
        self.assertEqual(output, "")
        self.assertIn("錯誤", error)
        self.assertNotIn("Traceback", error)
        self.serial.assert_not_called()
        self.xmodem.assert_not_called()
        self.runner.assert_not_called()
        self.assertEqual(self.channel.writes, [])

    def test_missing_confirmation_stops_before_read_sx_evidence_or_serial(self):
        self.assert_preflight_failure(self.invoke(confirm=False))
        self.reader.assert_not_called()
        self.which.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_abbreviated_confirmation_is_not_accepted(self):
        self.assert_preflight_failure(self.invoke("--confirm-fault", confirm=False))
        self.reader.assert_not_called()
        self.which.assert_not_called()

    def test_unknown_case_is_rejected_before_source_or_serial(self):
        self.assert_preflight_failure(self.invoke(case="unknown-case"))
        self.reader.assert_not_called()
        self.which.assert_not_called()

    def test_nonlocal_or_ambiguous_ports_are_rejected(self):
        for port in ("auto", "loop://", "/dev/ttyUSB*", "/dev/ttyUSB?", "/dev/ttyUSB[0]", "/dev/fake\n"):
            with self.subTest(port=port):
                self.assert_preflight_failure(self.invoke(port=port))
        self.reader.assert_not_called()
        self.which.assert_not_called()

    def test_missing_sx_rejected_for_all_cases_before_evidence_or_serial(self):
        self.which.return_value = None
        for case in CASES:
            with self.subTest(case=case):
                outcome = self.invoke(case=case)
                self.assert_preflight_failure(outcome)
                self.assertIn("缺少 sx", outcome[2])
                self.assertFalse(self.output.exists())
        self.assertEqual(self.which.call_args_list, [mock.call("sx")] * len(CASES))

    def test_wrong_source_hash_rejected_before_sx_or_serial(self):
        source = self.root / "other.sram"
        source.write_bytes(faults.package.build_package(b"\x55" * 768, 768))
        outcome = self.invoke(source=source)
        self.assert_preflight_failure(outcome)
        self.assertIn("固定 smoke 封包", outcome[2])
        self.which.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_wrong_source_sizes_rejected_before_sx_or_serial(self):
        for blob in (b"", self.blob[:1024], self.blob[:-1], self.blob + b"\0"):
            with self.subTest(size=len(blob)):
                source = self.root / f"size-{len(blob)}.sram"
                source.write_bytes(blob)
                self.assert_preflight_failure(self.invoke(source=source))
                self.assertEqual(source.read_bytes(), blob)
        self.which.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_parser_rejection_with_matching_hash_still_prevents_serial(self):
        broken = bytearray(self.blob)
        broken[508] ^= 1
        source = self.root / "broken.sram"
        source.write_bytes(broken)
        with mock.patch.object(faults, "PACKAGE_SHA256", hashlib.sha256(broken).hexdigest()):
            self.assert_preflight_failure(self.invoke(source=source))
        self.which.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_or_nonregular_source_rejected_without_serial(self):
        link = self.root / "linked.sram"
        link.symlink_to(self.source)
        fifo = self.root / "fifo.sram"
        os.mkfifo(fifo)
        for source in (self.root / "missing.sram", self.root, link, fifo):
            with self.subTest(source=source.name):
                self.assert_preflight_failure(self.invoke(source=source))
        self.which.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_evidence_directory_is_not_overwritten_or_used(self):
        self.output.mkdir()
        result = self.output / "result.json"
        original = b'{"preserve": true}\n'
        result.write_bytes(original)
        self.assert_preflight_failure(self.invoke())
        self.assertEqual(result.read_bytes(), original)
        self.assertEqual(list(self.output.iterdir()), [result])

    def test_existing_evidence_file_or_symlink_is_rejected(self):
        regular = self.root / "existing"
        regular.write_bytes(b"\x12\x34")
        link = self.root / "linked"
        link.symlink_to(regular)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "absent")
        for output in (regular, link, dangling):
            with self.subTest(output=output.name):
                self.assert_preflight_failure(self.invoke(output=output))
        self.assertEqual(regular.read_bytes(), b"\x12\x34")
        self.assertTrue(link.is_symlink())
        self.assertTrue(dangling.is_symlink())

    def test_output_symlink_ancestor_is_rejected(self):
        parent = self.root / "linked-parent"
        parent.symlink_to(self.root, target_is_directory=True)
        self.assert_preflight_failure(self.invoke(output=parent / "evidence"))
        self.assertFalse(self.output.exists())

    def test_unsafe_evidence_name_is_rejected(self):
        output = self.root / ".evidence"
        self.assert_preflight_failure(self.invoke(output=output))
        self.assertFalse(output.exists())

    def test_success_preserves_source_cleans_staged_payload_and_writes_evidence(self):
        before = self.source.stat()
        status, stdout, stderr = self.invoke()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertIs(report["recovery_observed"], True)
        self.assertIs(report["media_written"], False)
        self.assertIs(report["power_control"], False)
        self.assert_recovery(report["result"])
        self.serial.assert_called_once_with(PORT, 115200, 15)
        self.xmodem.assert_called_once()
        self.reader.assert_called_once_with(self.source, 1536)
        self.assertEqual(self.source.read_bytes(), self.blob)
        after = self.source.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode),
                         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode))
        self.assertTrue(self.staged_paths)
        self.assertTrue(all(not path.parent.exists() for path in self.staged_paths))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual({path.name for path in self.output.iterdir()}, {"control.json", "result.json"})
        self.assertEqual(json.loads((self.output / "result.json").read_text()), report)
        trace = json.loads((self.output / "control.json").read_text())
        self.assertEqual([bytes.fromhex(item["hex"]) for item in trace if item["direction"] == "tx"],
                         self.channel.writes)
        self.assertEqual(b"".join(bytes.fromhex(item["hex"]) for item in trace if item["direction"] == "rx"),
                         b"".join(value for direction, value in self.channel.events if direction == "rx"))
        self.assertIn("sx", report["trace_scope"])

    def test_loaded_zero_writes_failure_evidence_without_run(self):
        self.channel.loaded_response = loaded(result=0)
        status, stdout, stderr = self.invoke()
        self.assertEqual(status, 1, stderr)
        report = json.loads(stdout)
        self.assertIs(report["recovery_observed"], False)
        self.assertIn("禁止發送執行命令", report["error"])
        self.assertNotIn("result", report)
        self.assert_no_run()
        self.assertEqual(json.loads((self.output / "result.json").read_text()), report)
        self.assertTrue(all(not path.parent.exists() for path in self.staged_paths))

    def test_serial_open_failure_is_recorded_without_sender(self):
        self.serial.side_effect = OSError("替身串口開啟失敗")
        status, stdout, stderr = self.invoke()
        self.assertEqual(status, 1, stderr)
        report = json.loads(stdout)
        self.assertIs(report["recovery_observed"], False)
        self.assertIn("替身串口開啟失敗", report["error"])
        self.assertEqual(json.loads((self.output / "result.json").read_text()), report)
        self.assertEqual(json.loads((self.output / "control.json").read_text()), [])
        self.xmodem.assert_not_called()
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
