#!/usr/bin/env python3
"""開機交接後的原始串口紀錄守門；不接觸實體串口。"""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_sram_lab_uart as uart
from tools import bpi_sram_lab_package as package


class CaptureTests(unittest.TestCase):
    def test_raw_bytes_are_preserved_without_claiming_boot_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boot.bin"
            channel = mock.Mock()
            data = b"\xff\0\r\nLinux version 6.6.75\r\n"
            channel.read.side_effect = [data, b"", b"", b""]
            with uart.new_capture(path) as fd:
                result = uart.capture_boot(channel, fd, 1, clock=iter([0, 0.1, 0.3, 0.5, 0.8, 1]).__next__)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
            self.assertIs(result["boot_verified"], False)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            channel.write.assert_not_called()

    def test_short_writes_are_completed(self):
        channel = mock.Mock()
        channel.read.return_value = b"abcdef"
        with mock.patch.object(uart.os, "write", side_effect=[2, 4]) as write, mock.patch.object(uart.os, "fsync"):
            result = uart.capture_boot(channel, 123, 1, clock=iter([0, 0.1, 1]).__next__)
        self.assertEqual([call.args for call in write.call_args_list], [(123, b"abcdef"), (123, b"cdef")])
        self.assertEqual(result["bytes"], 6)

    def test_existing_files_and_symlinks_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "existing"
            original.write_bytes(b"unchanged")
            link = Path(directory) / "link"
            link.symlink_to(original)
            for path in (original, link):
                with self.assertRaises((OSError, package.base.PackageError)):
                    with uart.new_capture(path):
                        self.fail("不可開啟既有紀錄")
            self.assertEqual(original.read_bytes(), b"unchanged")

    def test_invalid_duration_and_oversize_read_are_rejected(self):
        channel = mock.Mock()
        for seconds in (0, -1, 301, True, 1.5):
            with self.assertRaises(uart.base.UartError):
                uart.capture_boot(channel, 123, seconds)
        channel.read.assert_not_called()
        channel.read.return_value = bytes(4097)
        with self.assertRaises(uart.base.UartError):
            uart.capture_boot(channel, 123, 1, clock=iter([0, 0.1]).__next__)

    def test_capture_requires_boot_type_before_serial_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.pkg"
            path.write_bytes(package.build_package(bytes(512), 0x18000, 3))
            args = uart.build_parser().parse_args([
                "upload", "--port", "/dev/ttyUSB0", "--input", str(path), "--run",
                "--capture-seconds", "1", "--capture-log", str(Path(directory) / "log"),
            ])
            with mock.patch.object(uart.base, "open_serial") as serial, mock.patch.object(uart.shutil, "which", return_value="/usr/bin/sx"):
                with self.assertRaises(uart.base.UartError):
                    uart.execute(args)
                serial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
