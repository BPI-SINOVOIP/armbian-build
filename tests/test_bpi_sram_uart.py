#!/usr/bin/env python3
"""SPL1 UART 工具測試；串口與 sx 一律使用替身，不接觸硬體。"""

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
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "bpi_sram_uart.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_uart", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
uart = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(MODULE_PATH.parent), *sys.path]):
    SPEC.loader.exec_module(uart)
NONCE = 0x1234ABCD
PORT = "/dev/FAKE_TEST_ONLY"


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


def loaded(nonce=NONCE, result=0):
    return f"BPI-SUP1 event=loaded nonce={nonce} result={result}\r\n".encode()


def smoke(nonce=NONCE):
    return (f"BPI-SUP1 event=handoff nonce={nonce} entry=00030000\r\n"
            f"BPI-SPL2 event=smoke nonce_hex={nonce:08x} sp=00047ff0 "
            "el=3 ddr=off result=pass\r\n").encode()


class FakeSerial:
    def __init__(self):
        self.buffer = bytearray(b"BPI-SUP1 event=loaded nonce=1 result=0\n")
        self.writes = []
        self.overrides = {}
        self.reset_count = 0
        self.timeout = 0.1

    def reset_input_buffer(self):
        self.reset_count += 1
        self.buffer.clear()

    def write(self, data):
        self.writes.append(data)
        command, nonce, *values = data.decode().strip().split()
        nonce = int(nonce)
        if command in self.overrides:
            response = self.overrides[command]
        elif command == "I":
            response = info(nonce)
        elif command in ("U", "S"):
            response = f"BPI-SUP1 event=loading nonce={nonce} transport={command}\r\n".encode()
            response += b"C" if command == "U" else loaded(nonce)
        else:
            response = smoke(nonce)
        self.buffer.extend(response)
        return len(data)

    def read(self, size):
        if size != 1:
            raise AssertionError("控制讀取不得預讀 XMODEM 資料")
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def fileno(self):
        return 4242


class ControlTests(unittest.TestCase):
    def test_strict_line_identity_and_duplicate_fields(self):
        for raw in (
            b"BPI-SUP2 event=info\n", b"BPI-SPL1 event=smoke\n",
            b"BPI-SUP1 event=info event=loaded\n", b"BPI-SUP1 nonce=1\n",
            b"BPI-SUP1 event=info command=$(id)\n", b"BPI-SUP1 event=info\x00\n",
            b"BPI-SUP1 event=info\r", b"x" * 512 + b"\n",
            b"BPI-SUP1  event=info\n", b"\xff\n",
        ):
            with self.subTest(raw=raw[:32]), self.assertRaises(uart.UartError):
                uart.parse_control_line(raw)
        self.assertIsNone(uart.parse_control_line(b"Linux BPI-SUP1 event=info\n"))
        self.assertIsNone(uart.parse_control_line(b"U-Boot\r\n"))
        self.assertEqual(uart.parse_control_line(info())[0], "BPI-SUP1")

    def test_no_run_without_verified_load(self):
        channel = FakeSerial()
        session = uart.SupervisorSession(channel, 1, Clock())
        with self.assertRaises(uart.UartError):
            session.run_smoke()
        with self.assertRaises(uart.UartError):
            session.begin_load("S", 0)
        with self.assertRaises(uart.UartError):
            session.finish_load()
        self.assertEqual(channel.writes, [])

    def test_reprobe_uses_new_nonce_and_invalidates_previous_load(self):
        channel = FakeSerial()
        session = uart.SupervisorSession(channel, 1, Clock())
        with mock.patch.object(uart.secrets, "randbits", side_effect=(1, 2)):
            session.probe()
            session.begin_load("S", 0)
            session.finish_load()
            session.probe()
        self.assertEqual(channel.writes[-1], b"I 2\n")
        with self.assertRaises(uart.UartError):
            session.run_smoke()

    def test_bounded_raw_lines_and_total_log_bytes(self):
        for response, budget in ((b"x" * 513, 65536), (b"x\n" * 100, 32)):
            channel = FakeSerial()
            channel.overrides["I"] = response
            with mock.patch.object(uart, "MAX_CONTROL_BYTES", budget):
                with self.assertRaisesRegex(uart.UartError, "上限"):
                    uart.SupervisorSession(channel, 10, Clock()).probe()

    def test_fresh_decimal_nonce_and_no_ready_requirement(self):
        for nonce in (0, 1, 0xFFFFFFFF):
            with self.subTest(nonce=nonce), mock.patch.object(uart.secrets, "randbits", return_value=nonce):
                channel = FakeSerial()
                result = uart.SupervisorSession(channel, 1, Clock()).probe()
                self.assertEqual(channel.writes, [f"I {nonce}\n".encode()])
                self.assertEqual(result["nonce"], nonce)
                self.assertEqual(channel.reset_count, 1)

    def test_short_write_rejected(self):
        channel = FakeSerial()
        channel.write = mock.Mock(return_value=0)
        with self.assertRaisesRegex(uart.UartError, "未完整寫入"):
            uart.SupervisorSession(channel, 1, Clock()).probe()


class FlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-uart-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "input.spl2"
        self.blob = uart.package.build_package(b"abc\x1a", 16)
        self.source.write_bytes(self.blob)
        self.channel = FakeSerial()
        self.serial = self.enterContext(mock.patch.object(uart, "open_serial"))
        self.serial.return_value.__enter__.return_value = self.channel
        self.sender = self.enterContext(mock.patch.object(uart, "send_xmodem", side_effect=self.transfer))
        self.which = self.enterContext(mock.patch.object(uart.shutil, "which", return_value="/fake/sx"))
        self.enterContext(mock.patch.object(uart.secrets, "randbits", return_value=NONCE))
        self.enterContext(mock.patch.object(uart.time, "monotonic", new=Clock()))
        self.staged_paths = []
        self.loaded_response = loaded()

    def enterContext(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def transfer(self, channel, path, executable, timeout):
        self.assertEqual(channel.buffer, b"C", "XMODEM 的 C 不得被控制讀取吃掉")
        self.assertEqual(channel.reset_count, self.expected_reset_count)
        channel.buffer.clear()
        self.assertNotEqual(path, self.source)
        self.assertEqual(path.read_bytes(), self.blob)
        self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(executable, "/fake/sx")
        self.assertEqual(timeout, 120)
        self.staged_paths.append(path)
        channel.buffer.extend(b"xyzModem\r\n" + self.loaded_response)
        return 0

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        self.expected_reset_count = self.channel.reset_count + 1
        with redirect_stdout(output), redirect_stderr(error):
            try:
                status = uart.main(list(map(str, args)))
            except SystemExit as exc:
                status = exc.code
        return status, output.getvalue(), error.getvalue()

    def upload(self, run=False):
        args = ["upload", "--port", PORT, "--input", str(self.source)]
        if run:
            args.append("--run")
        return self.invoke(*args)

    def assert_failed(self, outcome):
        status, output, error = outcome
        self.assertNotEqual(status, 0)
        self.assertEqual(output, "")
        self.assertIn("錯誤", error)
        self.assertNotIn("Traceback", error)
        self.assertNotIn("PRIVATE_UART", error)

    def test_probe_no_banner_and_clears_old_input(self):
        status, output, error = self.invoke("probe", "--port", PORT)
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["event"], "info")
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode()])
        self.serial.assert_called_once_with(PORT, 115200, 10)
        self.sender.assert_not_called()
        self.which.assert_not_called()

    def test_probe_allows_bounded_boot_logs_and_valid_ready(self):
        self.channel.overrides["I"] = (
            b"U-Boot\nBPI-SUP1 event=ready abi=1 board=06180001 ddr=off sd_write=off\r\n" + info()
        )
        self.assertEqual(self.invoke("probe", "--port", PORT)[0], 0)

    def test_upload_without_run_and_temp_cleanup(self):
        status, output, error = self.upload()
        self.assertEqual(status, 0, error)
        result = json.loads(output)
        self.assertFalse(result["ran"])
        self.assertEqual(result["event"], "loaded")
        self.assertEqual(result["package"], uart.package.parse_package(self.blob))
        self.assertEqual(self.channel.writes, [f"I {NONCE}\n".encode(), f"U {NONCE} 1024\n".encode()])
        self.assertTrue(self.staged_paths)
        self.assertTrue(all(not path.exists() for path in self.staged_paths))

    def test_upload_run_uses_same_nonce_and_requires_smoke(self):
        status, output, error = self.upload(run=True)
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["smoke"]["result"], "pass")
        self.assertTrue(json.loads(output)["ran"])
        self.assertEqual(self.channel.writes[-1], f"R {NONCE}\n".encode())

    def test_load_slots_read_only_without_sender_or_implicit_run(self):
        for slot in range(5):
            with self.subTest(slot=slot):
                self.channel.writes.clear()
                status, output, error = self.invoke("load-slot", "--port", PORT, "--slot", slot)
                self.assertEqual(status, 0, error)
                self.assertEqual(json.loads(output)["slot"], slot)
                self.assertFalse(json.loads(output)["ran"])
                self.assertEqual(self.channel.writes[-1], f"S {NONCE} {slot}\n".encode())
        self.sender.assert_not_called()
        self.which.assert_not_called()

    def test_load_slot_run(self):
        status, output, error = self.invoke("load-slot", "--port", PORT, "--slot", "4", "--run")
        self.assertEqual(status, 0, error)
        self.assertEqual(json.loads(output)["event"], "smoke")
        self.assertEqual(self.channel.writes[-1], f"R {NONCE}\n".encode())
        self.sender.assert_not_called()

    def test_wrong_nonce_board_abi_capabilities_stop_before_transfer(self):
        variants = (
            info(NONCE + 1), info().replace(b"abi=1", b"abi=2"),
            info().replace(b"board=06180001", b"board=06180002"),
            info().replace(b"uart-ram,sd-read,smoke-run", b"uart-ram"),
            info().replace(b"BPI-SUP1", b"BPI-SUP2"),
            b"BPI-SUP1 event=ready abi=2 board=06180001\n" + info(),
        )
        for response in variants:
            with self.subTest(response=response[:40]):
                self.channel.overrides["I"] = response
                self.channel.writes.clear()
                self.assert_failed(self.upload(run=True))
                self.assertEqual(len(self.channel.writes), 1)
                self.sender.assert_not_called()

    def test_wrong_os_log_cannot_fake_probe_success(self):
        self.channel.overrides["I"] = b"PRIVATE_UART Linux " + info()
        self.assert_failed(self.upload(run=True))
        self.sender.assert_not_called()
        self.assertEqual(len(self.channel.writes), 1)

    def test_probe_and_loading_timeouts_do_not_start_sender(self):
        for command in ("I", "U"):
            with self.subTest(command=command):
                self.channel.overrides = {command: b""}
                self.channel.writes.clear()
                self.assert_failed(self.upload(run=True))
                self.sender.assert_not_called()
                self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_wrong_loading_nonce_transport_or_event_prevents_sender(self):
        for response in (
            f"BPI-SUP1 event=loading nonce={NONCE + 1} transport=U\n".encode(),
            f"BPI-SUP1 event=loading nonce={NONCE} transport=S\n".encode(),
            loaded(), b"BPI-SUP1 event=reject reason=command_or_state\n",
        ):
            with self.subTest(response=response):
                self.channel.overrides["U"] = response
                self.assert_failed(self.upload(run=True))
                self.sender.assert_not_called()

    def test_wrong_loaded_or_timeout_never_runs(self):
        for response in (loaded(NONCE + 1), loaded(result=-1), loaded().replace(b"result=0", b"result=pass"), b"", b"PRIVATE_UART " + loaded()):
            with self.subTest(response=response):
                self.loaded_response = response
                self.channel.writes.clear()
                self.assert_failed(self.upload(run=True))
                self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_wrong_handoff_or_smoke_cannot_pass(self):
        for response in (
            smoke().replace(b"entry=00030000", b"entry=00030004"),
            smoke().replace(f"nonce_hex={NONCE:08x}".encode(), b"nonce_hex=00000001"),
            smoke().replace(b"nonce_hex=1234abcd", b"nonce_hex=1234abc"),
            smoke().replace(b"result=pass", b"result=fail"),
            smoke().replace(b"el=3", b"el=2"), smoke().replace(b"ddr=off", b"ddr=on"),
            smoke().replace(b"sp=00047ff0", b"sp=00048000"),
            smoke().replace(b"BPI-SPL2", b"Linux BPI-SPL2"),
            b"BPI-SPL2 event=halt result=invalid-context\n",
            b"PRIVATE_UART result=pass\n",
        ):
            with self.subTest(response=response):
                self.channel.overrides["R"] = response
                self.assert_failed(self.upload(run=True))

    def test_invalid_package_rejected_before_serial_and_sender(self):
        variants = [b"", self.blob[:-1], self.blob + b"\x1a", self.blob[:-1] + b"\x01"]
        for offset, value in ((16, 0), (28, 0), (32, 1), (36, 2), (24, 15)):
            broken = bytearray(self.blob)
            struct.pack_into("<I", broken, offset, value)
            struct.pack_into("<I", broken, 508, zlib.crc32(broken[:508]))
            variants.append(bytes(broken))
        for blob in variants:
            with self.subTest(size=len(blob)):
                self.source.write_bytes(blob)
                self.assert_failed(self.upload(run=True))
                self.serial.assert_not_called()
                self.sender.assert_not_called()
                self.which.assert_not_called()

    def test_nonregular_input_rejected_before_serial(self):
        other = self.root / "original.spl2"
        other.write_bytes(self.blob)
        self.source.unlink()
        self.source.symlink_to(other)
        self.assert_failed(self.upload())
        self.source.unlink()
        os.mkfifo(self.source)
        self.assert_failed(self.upload())
        self.serial.assert_not_called()
        self.sender.assert_not_called()

    def test_verified_snapshot_survives_original_replacement(self):
        def transfer(channel, path, executable, timeout):
            self.source.unlink()
            self.source.write_bytes(b"PRIVATE_UART")
            return self.transfer(channel, path, executable, timeout)
        self.sender.side_effect = transfer
        status, _, error = self.upload()
        self.assertEqual(status, 0, error)

    def test_missing_sx_does_not_open_serial(self):
        self.which.return_value = None
        self.assert_failed(self.upload())
        self.serial.assert_not_called()
        self.sender.assert_not_called()

    def test_transfer_error_or_cancellation_never_runs_and_cleans_temp(self):
        for exception in (
            uart.UartError("傳輸逾時"), OSError("PRIVATE_UART"),
            uart.termios.error("PRIVATE_UART"), KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(exception)):
                paths = []
                def fail(channel, path, executable, timeout):
                    paths.append(path)
                    raise exception
                self.sender.side_effect = fail
                self.channel.writes.clear()
                outcome = self.upload(run=True)
                self.assert_failed(outcome)
                if isinstance(exception, KeyboardInterrupt):
                    self.assertEqual(outcome[0], 130)
                self.assertTrue(all(not path.exists() for path in paths))
                self.assertFalse(any(data.startswith(b"R ") for data in self.channel.writes))

    def test_required_named_port_and_strict_options(self):
        for args in (
            ("probe",), ("probe", "--port", "auto"), ("probe", "--port", "loop://"),
            ("probe", "--port", "/dev/ttyUSB*"), ("probe", "--port", PORT, "--run"),
            ("probe", "--port", PORT, "--timeout", "nan"),
            ("probe", "--port", PORT, "--timeout", "0"),
            ("probe", "--port", PORT, "--timeout", "121"),
            ("load-slot", "--port", PORT, "--slot", "5"),
            ("load-slot", "--port", PORT, "--slot", "-1"),
            ("upload", "--port", PORT),
        ):
            with self.subTest(args=args):
                self.assert_failed(self.invoke(*args))
                self.serial.assert_not_called()
                self.sender.assert_not_called()

    def test_help_does_not_open_serial_and_is_chinese(self):
        for args in (("--help",), ("probe", "--help"), ("upload", "--help"), ("load-slot", "--help")):
            with self.subTest(args=args):
                status, output, _ = self.invoke(*args)
                self.assertEqual(status, 0)
                self.assertIn("用法：", output)
                self.assertNotIn("usage:", output)
        self.serial.assert_not_called()
        self.sender.assert_not_called()


class SerialOpenTests(unittest.TestCase):
    def test_import_has_no_serial_or_process_side_effect(self):
        serial = mock.Mock()
        module = importlib.util.module_from_spec(SPEC)
        with mock.patch.dict(sys.modules, {"serial": serial}), mock.patch.object(uart.subprocess, "Popen") as process:
            SPEC.loader.exec_module(module)
        serial.Serial.assert_not_called()
        process.assert_not_called()

    def test_only_named_port_opened_exclusively_and_closed(self):
        serial = mock.Mock()
        channel = serial.Serial.return_value
        with mock.patch.dict(sys.modules, {"serial": serial}):
            with uart.open_serial(PORT, 115200, 5) as opened:
                self.assertIs(opened, channel)
                self.assertEqual(channel.port, PORT)
                self.assertFalse(channel.dtr)
                self.assertFalse(channel.rts)
                channel.open.assert_called_once_with()
        serial.Serial.assert_called_once_with(
            port=None, baudrate=115200, timeout=0.1, write_timeout=5,
            exclusive=True, xonxoff=False, rtscts=False, dsrdtr=False,
        )
        channel.close.assert_called_once_with()

    def test_serial_open_failure_closes_without_retry(self):
        serial = mock.Mock()
        serial.Serial.return_value.open.side_effect = OSError("PRIVATE_UART")
        with mock.patch.dict(sys.modules, {"serial": serial}), self.assertRaises(OSError):
            with uart.open_serial(PORT, 115200, 5):
                self.fail("開啟失敗不應進入操作")
        serial.Serial.return_value.open.assert_called_once()
        serial.Serial.return_value.close.assert_called_once()


class FakeProcess:
    def __init__(self, running=False, code=0):
        self.running = running
        self.returncode = None if running else code
        self.stderr = mock.Mock()
        self.stderr.fileno.return_value = 9000
        self.kill = mock.Mock(side_effect=self.stop)
        self.wait = mock.Mock(side_effect=lambda timeout: self.returncode)

    def stop(self):
        self.running = False
        self.returncode = -9

    def poll(self):
        return None if self.running else self.returncode


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.channel = FakeSerial()
        self.process = FakeProcess()
        self.chunks = iter([b"PRIVATE_UART", b""])
        self.read = self.patch(uart.os, "read", side_effect=lambda fd, count: next(self.chunks, b""))
        self.get_attributes = self.patch(uart.termios, "tcgetattr", return_value=["saved"])
        self.set_attributes = self.patch(uart.termios, "tcsetattr")
        self.patch(uart.os, "get_blocking", return_value=False)
        self.set_blocking = self.patch(uart.os, "set_blocking")
        self.popen = self.patch(uart.subprocess, "Popen", return_value=self.process)
        self.selector = self.patch(uart.selectors, "DefaultSelector").return_value.__enter__.return_value
        self.registered = True
        self.selector.unregister.side_effect = lambda file: setattr(self, "registered", False)
        self.selector.select.side_effect = lambda timeout: [(SimpleNamespace(fileobj=self.process.stderr), 1)] if self.registered else []
        self.patch(uart.time, "monotonic", new=Clock(0.01))

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def send(self, timeout=1):
        return uart.send_xmodem(self.channel, Path("/private/package.spl2"), "/fake/sx", timeout)

    def assert_restored(self):
        self.set_attributes.assert_called_with(4242, uart.termios.TCSANOW, ["saved"])
        self.set_blocking.assert_called_with(4242, False)

    def test_xmodem_binary_same_fd_and_bounded_private_capture(self):
        self.assertEqual(self.send(), len(b"PRIVATE_UART"))
        self.popen.assert_called_once_with(
            ["/fake/sx", "-b", "-X", "-q", "--", "/private/package.spl2"],
            stdin=4242, stdout=4242, stderr=uart.subprocess.PIPE,
            close_fds=True, shell=False,
        )
        self.process.kill.assert_not_called()
        self.process.wait.assert_called_once_with(timeout=2)
        self.process.stderr.close.assert_called_once()
        self.assert_restored()

    def test_timeout_kills_and_reaps_sender(self):
        self.process.running = True
        self.process.returncode = None
        with self.assertRaisesRegex(uart.UartError, "逾時"):
            self.send(timeout=0.05)
        self.process.kill.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=2)
        self.assert_restored()

    def test_stderr_flood_is_capped_and_sender_killed(self):
        self.process.running = True
        self.process.returncode = None
        self.chunks = iter([b"x" * 4096] * 17)
        with self.assertRaisesRegex(uart.UartError, "上限"):
            self.send()
        self.process.kill.assert_called_once()
        self.assert_restored()

    def test_failure_does_not_include_stderr(self):
        self.process.returncode = 3
        with self.assertRaisesRegex(uart.UartError, "exit_code=3") as caught:
            self.send()
        self.assertNotIn("PRIVATE_UART", str(caught.exception))
        self.assert_restored()

    def test_cancellation_kills_and_reaps_sender(self):
        self.process.running = True
        self.process.returncode = None
        self.selector.select.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.send()
        self.process.kill.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=2)
        self.assert_restored()

    def test_spawn_failure_restores_serial_settings(self):
        self.popen.side_effect = OSError("PRIVATE_UART")
        with self.assertRaises(OSError):
            self.send()
        self.assert_restored()


if __name__ == "__main__":
    unittest.main()
