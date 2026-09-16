#!/usr/bin/env python3
"""主控台離線回歸；僅使用替身與本機 sh，不接觸 UART 或媒體。"""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_console as console


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeChannel:
    def __init__(self, clock, chunks=(), respond=None):
        self.clock = clock
        self.chunks = deque(chunks)
        self.respond = respond
        self.timeout = 0.5
        self.write_timeout = 0.5
        self.writes = []
        self.read_timeouts = []
        self.closed = False

    def read(self, size):
        self.read_timeouts.append(self.timeout)
        self.clock.value += min(0.01, self.timeout) if self.chunks else self.timeout
        # 刻意一次回傳多個 prompt，甚至超過 size，以檢驗未消耗尾端。
        return self.chunks.popleft() if self.chunks else b""

    def write(self, data):
        self.clock.value += 0.001
        self.writes.append(data)
        if self.respond:
            self.chunks.extend(self.respond(data))
        return len(data)

    def close(self):
        self.closed = True


def markers(wire):
    nonce = re.search(rb"'([0-9a-f]{64})__'", wire)[1]
    return (b"__BPI_CONSOLE_BEGIN_" + nonce + b"__",
            b"__BPI_CONSOLE_END_" + nonce + b"__")


def shell_response(wire, output=b"\xe6\xb8\xac\xe8\xa9\xa6\r\n", status=b"7"):
    begin, end = markers(wire)
    return [wire.replace(b"\n", b"\r\n") + b"\r\n" + begin + b"\r\n"
            + output + b"\r\n" + end + b":" + status + b"\r\nroot# "]


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.log = self.directory / "rx.bin"
        self.clock = Clock()

    def session(self, chunks=(), respond=None):
        channel = FakeChannel(self.clock, chunks, respond)
        session = console.ConsoleSession(channel, log_path=self.log, monotonic=self.clock)
        self.addCleanup(session.close)
        return session, channel

    def test_literal_regex_preserve_overread_tail_and_raw_log(self):
        raw = b"\xff\0\r\nlogin: Password: root# tail"
        session, channel = self.session([raw])
        result = session.expect_literal("login: ", timeout=1)
        self.assertEqual(result.before, b"\xff\0\r\n")
        self.assertEqual(result.matched, b"login: ")
        self.assertEqual(session.expect_literal(b"Password: ", 1).before, b"")
        result = session.expect_regex(rb"(root)# ", 1)
        self.assertEqual(result.groups, (b"root",))
        self.assertEqual(session.buffered, b"tail")
        self.assertEqual(session.read(2), b"ta")
        self.assertEqual(session.read(9), b"il")
        self.assertEqual(len(channel.read_timeouts), 1)
        self.assertEqual(channel.timeout, 0.5)
        self.assertEqual(self.log.read_bytes(), raw)
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)

    def test_fragmented_literal_and_regex(self):
        session, _ = self.session([b"lo", b"gin:", b" ", b"code=", b"12\r", b"\nnext"])
        self.assertEqual(session.expect_literal(b"login: ", 1).matched, b"login: ")
        result = session.expect_regex(re.compile(rb"code=(\d+)\r?\n"), 1)
        self.assertEqual(result.groups, (b"12",))
        self.assertEqual(session.buffered, b"next")

    def test_timeout_is_absolute_despite_continuous_input(self):
        session, channel = self.session([b"x"] * 100)
        with self.assertRaises(console.ConsoleTimeout):
            session.expect_literal(b"login:", deadline=0.055)
        self.assertAlmostEqual(self.clock.value, 0.055)
        self.assertEqual(session.buffered, self.log.read_bytes())
        self.assertGreater(len(session.buffered), 0)
        self.assertEqual(channel.timeout, 0.5)

    def test_timeout_does_not_consume_and_can_resume(self):
        session, channel = self.session([b"log"])
        with self.assertRaises(console.ConsoleTimeout):
            session.expect_literal(b"login:", 0.05)
        channel.chunks.append(b"in:tail")
        self.assertEqual(session.expect_literal(b"login:", 1).matched, b"login:")
        self.assertEqual(session.buffered, b"tail")
        self.assertEqual(self.log.read_bytes(), b"login:tail")

    def test_late_read_is_logged_but_not_accepted(self):
        session, channel = self.session()

        def late_read(size):
            self.clock.value += 2
            return b"login:"

        channel.read = late_read
        with self.assertRaises(console.ConsoleTimeout):
            session.expect_literal(b"login:", 1)
        self.assertEqual(self.log.read_bytes(), b"login:")
        self.assertEqual(session.buffered, b"login:")

    def test_send_password_never_logs_tx_or_adds_newline(self):
        session, channel = self.session()
        self.assertEqual(session.send("測試密碼\n", secret=True), len("測試密碼\n".encode()))
        session.write(b"I 123\n")
        self.assertEqual(channel.writes, ["測試密碼\n".encode(), b"I 123\n"])
        self.assertEqual(self.log.read_bytes(), b"")
        self.assertEqual(channel.write_timeout, 0.5)

    def test_short_tx_writes_complete_without_resending_prefix(self):
        session, channel = self.session()
        with mock.patch.object(channel, "write", side_effect=[2, 3]) as write:
            self.assertEqual(session.send(b"abcde"), 5)
        self.assertEqual(write.call_args_list, [mock.call(b"abcde"), mock.call(b"cde")])

    def test_zero_and_late_tx_fail_without_retry(self):
        session, channel = self.session()
        with mock.patch.object(channel, "write", return_value=0) as write:
            with self.assertRaises(console.ConsoleError):
                session.send(b"x")
            self.assertEqual(write.call_count, 1)

        def late_write(data):
            self.clock.value += 2
            return len(data)

        with mock.patch.object(channel, "write", side_effect=late_write):
            with self.assertRaises(console.ConsoleTimeout):
                session.send(b"x", timeout=1)
        self.assertEqual(channel.write_timeout, 0.5)

    def test_shell_ignores_echo_preserves_output_status_and_next_prompt(self):
        session, channel = self.session(respond=shell_response)
        result = session.run_shell("id", 1)
        self.assertEqual(result, console.ShellResult("測試\r\n".encode(), 7))
        self.assertEqual(session.expect_literal(b"root# ", 1).matched, b"root# ")
        self.assertEqual(len(channel.writes), 1)
        begin, end = markers(channel.writes[0])
        self.assertNotIn(begin, channel.writes[0])
        self.assertNotIn(end, channel.writes[0])
        self.assertIn(channel.writes[0].replace(b"\n", b"\r\n"), self.log.read_bytes())

    def test_shell_echo_alone_never_counts_as_result(self):
        session, _ = self.session(respond=lambda wire: [wire + b"login: "])
        with self.assertRaises(console.ConsoleTimeout):
            session.run_shell(":", 0.1)

    def test_ansi_bracketed_paste_and_long_echo_stay_outside_result(self):
        def respond(wire):
            begin, end = markers(wire)
            echo = b"\x1b[?2004hroot# " + wire.replace(b"\n", b"\r\n") + b"\x1b[?2004l\r"
            response = (echo + b"\r\n" + begin + b"\r\n" + b"uid=0\r\n"
                        + b"\r\n" + end + b":0\r\n\x1b[?2004hroot# ")
            return [response[:900], response[900:-1], response[-1:]]

        session, _ = self.session(respond=respond)
        self.assertEqual(session.run_shell("printf '%s' '" + "x" * 1500 + "'", 1),
                         (b"uid=0\r\n", 0))
        self.assertIn(b"\x1b[?2004l", self.log.read_bytes())
        self.assertEqual(session.expect_literal(b"root# ", 1).before, b"\x1b[?2004h")

    def test_exact_nonce_without_complete_marker_line_is_not_success(self):
        def respond(wire):
            begin, end = markers(wire)
            return [b"\n" + begin + b"\n\n" + end + b":0"]

        session, _ = self.session(respond=respond)
        with self.assertRaises(console.ConsoleTimeout):
            session.run_shell(":", 0.1)

    def test_shell_no_end_marker_times_out_retaining_output(self):
        def respond(wire):
            begin, _ = markers(wire)
            return [b"\r\n" + begin + b"\r\n" + "尚未結束".encode()]

        session, _ = self.session(respond=respond)
        with self.assertRaises(console.ConsoleTimeout):
            session.run_shell(":", 0.1)
        self.assertEqual(session.buffered, "尚未結束".encode())

    def test_shell_rejects_spoofs_wrong_nonce_incomplete_and_embedded_lines(self):
        observed = []

        def respond(wire):
            begin, end = markers(wire)
            spoof = (end.replace(b"END_", b"END_0") + b":0\n"
                     + b"prefix" + end + b":0\n" + end + b":0suffix\n"
                     + end + b":256\n" + end + b":00\n")
            observed.append(spoof)
            return [b"prefix" + begin + b"\n" + begin + b"suffix\n"
                    + b"\n" + begin + b"\n" + spoof + b"\n" + end + b":3\nnext"]

        session, _ = self.session(respond=respond)
        self.assertEqual(session.run_shell(":", 1), (observed[0], 3))
        self.assertEqual(session.buffered, b"next")

    def test_shell_shared_deadline_includes_send_and_both_markers(self):
        session, channel = self.session()
        counter = 0

        def respond(wire):
            begin, end = markers(wire)
            return [b"\n" + begin + b"\n", b"\n" + end + b":0\n"]

        channel.respond = respond
        original = channel.read

        def slow_read(size):
            nonlocal counter
            counter += 1
            self.clock.value += 0.06
            return original(size)

        channel.read = slow_read
        with self.assertRaises(console.ConsoleTimeout):
            session.run_shell(":", 0.1)
        self.assertEqual(counter, 2)
        self.assertIn(b":0\n", self.log.read_bytes())

    def test_shell_random_nonce_changes_each_command(self):
        session, channel = self.session(respond=shell_response)
        session.run_shell(":", 1)
        session.run_shell(":", 1)
        self.assertNotEqual(markers(channel.writes[0]), markers(channel.writes[1]))

    def test_shell_wrapper_executes_with_local_posix_shell(self):
        def respond(wire):
            completed = subprocess.run(["/bin/sh"], input=wire, capture_output=True,
                                       timeout=2, check=True)
            self.assertEqual(completed.stderr, b"")
            return [wire + completed.stdout + b"prompt# "]

        session, _ = self.session(respond=respond)
        self.assertEqual(session.run_shell("printf '%s' '測試'; exit 23", 1),
                         ("測試".encode(), 23))
        self.assertEqual(session.run_shell(":", 1), (b"", 0))
        self.assertEqual(session.run_shell("printf 'a\\nb\\n'", 1), (b"a\nb\n", 0))
        self.assertEqual(session.run_shell("read value", 1), (b"", 1))

    def test_capture_only_records_rx_never_executes_received_text(self):
        raw = b"$(reboot)\r\npasswd root\n\xff\0"
        session, channel = self.session([raw])
        self.assertEqual(session.capture(0.05), len(raw))
        self.assertEqual(self.log.read_bytes(), raw)
        self.assertEqual(session.buffered, raw)
        self.assertEqual(channel.writes, [])

    def test_close_retains_borrowed_connection_and_is_idempotent(self):
        session, channel = self.session()
        session.close()
        session.close()
        self.assertFalse(channel.closed)
        with self.assertRaises(console.ConsoleError):
            session.send(b"x")
        with self.assertRaises(console.ConsoleError):
            session.read()

    def test_rejects_existing_file_and_all_symlink_components(self):
        original = self.directory / "original"
        original.write_bytes("不可覆寫".encode())
        link = self.directory / "link"
        link.symlink_to(original)
        dangling = self.directory / "dangling"
        dangling.symlink_to(self.directory / "missing")
        parent = self.directory / "parent"
        parent.symlink_to(self.directory, target_is_directory=True)
        for path in (original, link, dangling, parent / "new"):
            with self.subTest(path=path), self.assertRaises(console.ConsoleError):
                console.ConsoleSession(None, log_path=path)
        self.assertEqual(original.read_bytes(), "不可覆寫".encode())
        self.assertFalse((self.directory / "new").exists())
        self.assertFalse((self.directory / "missing").exists())

    def test_partial_log_writes_preserve_all_bytes(self):
        session, _ = self.session([b"abcdef"])
        real_write = os.write
        with mock.patch.object(console.os, "write", side_effect=lambda fd, data: real_write(fd, data[:2])):
            self.assertEqual(session.read(6), b"abcdef")
        self.assertEqual(self.log.read_bytes(), b"abcdef")

    def test_log_failure_prevents_later_success_and_tx(self):
        session, channel = self.session([b"login:"])
        with mock.patch.object(console.os, "write", return_value=0):
            with self.assertRaisesRegex(console.ConsoleError, "日誌未完整"):
                session.expect_literal(b"login:", 1)
        with self.assertRaises(console.ConsoleError):
            session.send(b"root\n")
        self.assertEqual(channel.writes, [])

    def test_transport_errors_are_chinese_without_sensitive_data(self):
        session, channel = self.session()
        with mock.patch.object(channel, "read", side_effect=OSError("不應公開的 RX")):
            with self.assertRaisesRegex(console.ConsoleError, "^主控台讀取失敗") as error:
                session.expect_literal(b"x", 1)
        self.assertNotIn("不應公開", str(error.exception))
        with mock.patch.object(channel, "write", side_effect=OSError("不應公開的 TX")):
            with self.assertRaisesRegex(console.ConsoleError, "^主控台傳送失敗") as error:
                session.send("密碼\n", secret=True)
        self.assertNotIn("不應公開", str(error.exception))

    def test_read_write_only_nonblocking_fake_can_be_injected(self):
        class Channel:
            def read(self, size):
                return b"login:"

            def write(self, data):
                return len(data)

        with console.ConsoleSession(Channel(), log_path=self.log, monotonic=self.clock) as session:
            self.assertEqual(session.send(b"root\n"), 5)
            self.assertEqual(session.expect_literal(b"login:", 1).matched, b"login:")

    def test_invalid_input_is_rejected_before_tx(self):
        session, channel = self.session()
        for timeout in (0, -1, float("inf"), float("nan"), True):
            with self.subTest(timeout=timeout), self.assertRaises(console.ConsoleError):
                session.expect_literal(b"x", timeout)
        for command in ("", "\n", "id\rreboot", "x\0", "x" * 4096):
            with self.subTest(command_length=len(command)), self.assertRaises(console.ConsoleError):
                session.run_shell(command, 1)
        for pattern in (b"", b".*", b"[", re.compile("x")):
            with self.assertRaises(console.ConsoleError):
                session.expect_regex(pattern, 1)
        self.assertEqual(channel.writes, [])

    def test_cli_bad_log_does_not_open_serial(self):
        self.log.write_bytes("既有日誌".encode())
        with mock.patch.object(console.uart, "open_serial") as opened, redirect_stderr(io.StringIO()):
            self.assertEqual(console.main(["capture", "--port", "/dev/FAKE_TEST_ONLY",
                                           "--log", str(self.log)]), 1)
        opened.assert_not_called()

    def test_cli_reuses_existing_exclusive_serial_opener(self):
        channel = FakeChannel(self.clock)
        with mock.patch.object(console.uart, "open_serial") as opened, \
                mock.patch.object(console.ConsoleSession, "capture", return_value=12) as capture, \
                redirect_stdout(io.StringIO()):
            opened.return_value.__enter__.return_value = channel
            self.assertEqual(console.main(["capture", "--port", "/dev/FAKE_TEST_ONLY",
                                           "--log", str(self.log)]), 0)
        opened.assert_called_once_with("/dev/FAKE_TEST_ONLY", 115200, 10.0)
        capture.assert_called_once_with(10.0)
        self.assertEqual(channel.writes, [])

    def test_buffer_overflow_logs_full_chunk_and_never_reports_success(self):
        for operation in ("expect", "capture"):
            with self.subTest(operation=operation):
                path = self.directory / operation
                channel = FakeChannel(self.clock, [b"abcd", b"efghlogin:"])
                with console.ConsoleSession(channel, log_path=path, monotonic=self.clock,
                                            max_buffer_bytes=6) as session:
                    with self.assertRaisesRegex(console.ConsoleError, "緩衝上限"):
                        if operation == "expect":
                            session.expect_literal(b"login:", 1)
                        else:
                            session.capture(1)
                    self.assertEqual(session.buffered, b"abcdef")
                    self.assertEqual(path.read_bytes(), b"abcdefghlogin:")
                    with self.assertRaises(console.ConsoleError):
                        session.expect_literal(b"abc", 1)
                    with self.assertRaises(console.ConsoleError):
                        session.send(b"x")
                self.assertEqual(channel.writes, [])

    def test_invalid_buffer_limit_does_not_create_log(self):
        for limit in (0, -1, True, 1.5):
            with self.assertRaises(console.ConsoleError):
                console.ConsoleSession(None, log_path=self.log, max_buffer_bytes=limit)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
