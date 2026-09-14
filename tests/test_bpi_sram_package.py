#!/usr/bin/env python3
"""SPL1 第一版封包 ABI 與離線檔案限制的確定性回歸測試。"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "bpi_sram_package.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_package", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def refresh_crc(blob: bytearray) -> bytes:
    struct.pack_into("<I", blob, 508, zlib.crc32(blob[:508]))
    return bytes(blob)


def replace_u32(blob: bytes, offset: int, value: int) -> bytes:
    modified = bytearray(blob)
    struct.pack_into("<I", modified, offset, value)
    return refresh_crc(modified)


class PackageFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = bytes(range(256)) * 2 + b"\x1a"
        self.blob = package.build_package(self.payload, 528)

    def test_exact_header_layout_and_json_metadata(self) -> None:
        blob = package.build_package(b"abc", 16)
        digest = hashlib.sha256(b"abc").digest()
        header = (
            b"BPISRAM1"
            + struct.pack("<8I", 1, 512, 0x06180001, 3, 16, 0x30000, 0, 1)
            + digest + bytes(436)
        )
        crc = zlib.crc32(header)
        self.assertEqual(blob, header + struct.pack("<I", crc) + b"abc" + bytes(509))
        self.assertEqual(package.parse_package(blob), {
            "raw_size": 1024,
            "image_size": 3,
            "runtime_size": 16,
            "kind": "smoke",
            "hash": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "header_crc": crc,
        })

    def test_round_trip_boundaries_and_exact_padding(self) -> None:
        for size in (1, 15, 16, 17, 511, 512, 513, 1023, 1024, 97791, 97792):
            with self.subTest(size=size):
                payload = (bytes(range(256)) * (size // 256 + 1))[:size]
                runtime_size = (size + 15) // 16 * 16
                blob = package.build_package(payload, runtime_size)
                info = package.parse_package(blob)
                self.assertEqual(info["image_size"], size)
                self.assertEqual(info["runtime_size"], runtime_size)
                self.assertEqual(info["raw_size"], 512 + (size // 512 + 1) * 512)
                self.assertEqual(blob[512:512 + size], payload)
                self.assertEqual(blob[512 + size:], bytes(512 - size % 512))
                self.assertEqual(info["hash"], hashlib.sha256(payload).hexdigest())

    def test_maximum_image_and_runtime(self) -> None:
        blob = package.build_package(bytes(0x18000 - 512), 0x18000)
        self.assertEqual(len(blob), 0x18200)
        self.assertEqual(package.parse_package(blob)["runtime_size"], 0x18000)
        self.assertEqual(blob[-512:], bytes(512))
        package.parse_package(package.build_package(b"x", 0x18000))

    def test_invalid_image_sizes(self) -> None:
        for size in (0, 97793, 98304):
            with self.subTest(size=size), self.assertRaisesRegex(package.PackageError, "映像大小"):
                package.build_package(bytes(size), 0x18000)

    def test_invalid_runtime_sizes(self) -> None:
        for runtime_size in (-16, 0, 16, 31, 33, 0x18001, 0x18010, 2 ** 32):
            with self.subTest(runtime_size=runtime_size), self.assertRaises(package.PackageError):
                package.build_package(bytes(32), runtime_size)
        for runtime_size in (True, False, 16.0, "16", None):
            with self.subTest(runtime_size=runtime_size), self.assertRaisesRegex(
                package.PackageError, "整數",
            ):
                package.build_package(b"x", runtime_size)

    def test_invalid_api_input_types(self) -> None:
        for value in (None, "abc", 3, [1, 2, 3]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(package.PackageError, "位元組"):
                    package.build_package(value, 16)
                with self.assertRaisesRegex(package.PackageError, "位元組"):
                    package.parse_package(value)
        self.assertEqual(package.build_package(bytearray(b"abc"), 16), package.build_package(b"abc", 16))
        self.assertEqual(package.parse_package(bytearray(self.blob)), package.parse_package(self.blob))

    def test_rejects_magic_and_egon(self) -> None:
        for magic in (b"BPISRAM2", b"eGON.BT0", bytes(8)):
            modified = bytearray(self.blob)
            modified[:8] = magic
            with self.subTest(magic=magic), self.assertRaisesRegex(package.PackageError, "識別碼"):
                package.parse_package(refresh_crc(modified))

    def test_rejects_header_fields_even_with_valid_crc(self) -> None:
        fields = (
            (8, "version", (0, 2, 0xFFFFFFFF)),
            (12, "header_bytes", (0, 511, 1024)),
            (16, "board_id", (0, 0x06180002)),
            (28, "entry", (0, 0x30004, 0x48000)),
            (32, "flags", (1, 0x80000000, 0xFFFFFFFF)),
            (36, "kind", (0, 2, 0xFFFFFFFF)),
        )
        for offset, field, values in fields:
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                    package.PackageError, field,
                ):
                    package.parse_package(replace_u32(self.blob, offset, value))

    def test_rejects_big_endian_header(self) -> None:
        modified = bytearray(self.blob)
        struct.pack_into(
            ">8I", modified, 8, 1, 512, 0x06180001, 513, 528, 0x30000, 0, 1,
        )
        with self.assertRaisesRegex(package.PackageError, "version"):
            package.parse_package(refresh_crc(modified))

    def test_rejects_invalid_header_sizes_even_with_valid_crc(self) -> None:
        for offset, values in (
            (20, (0, 97793, 0xFFFFFFFF)),
            (24, (0, 512, 527, 529, 0x18001, 0x18010, 0xFFFFFFFF)),
        ):
            for value in values:
                with self.subTest(offset=offset, value=value), self.assertRaises(package.PackageError):
                    package.parse_package(replace_u32(self.blob, offset, value))

    def test_rejects_every_reserved_byte_even_with_valid_crc(self) -> None:
        for offset in range(72, 508):
            modified = bytearray(self.blob)
            modified[offset] = 1
            with self.subTest(offset=offset), self.assertRaisesRegex(package.PackageError, "保留欄位"):
                package.parse_package(refresh_crc(modified))

    def test_rejects_crc_and_header_corruption(self) -> None:
        for offset in (8, 20, 40, 72, 507, 508, 509, 510, 511):
            modified = bytearray(self.blob)
            modified[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(package.PackageError, "CRC"):
                package.parse_package(modified)

    def test_rejects_every_digest_byte_with_valid_crc(self) -> None:
        for offset in range(40, 72):
            modified = bytearray(self.blob)
            modified[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(package.PackageError, "SHA-256"):
                package.parse_package(refresh_crc(modified))

    def test_rejects_every_payload_byte_corruption(self) -> None:
        for offset in range(512, 512 + len(self.payload)):
            modified = bytearray(self.blob)
            modified[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(package.PackageError, "SHA-256"):
                package.parse_package(modified)

    def test_rejects_every_nonzero_padding_byte(self) -> None:
        for offset in range(512 + len(self.payload), len(self.blob)):
            modified = bytearray(self.blob)
            modified[offset] = 0x1A
            with self.subTest(offset=offset), self.assertRaisesRegex(package.PackageError, "填補"):
                package.parse_package(modified)

    def test_rejects_all_truncated_prefixes(self) -> None:
        for end in range(len(self.blob)):
            with self.subTest(end=end), self.assertRaises(package.PackageError):
                package.parse_package(self.blob[:end])

    def test_rejects_appended_bytes_or_transfer_padding(self) -> None:
        for tail in (b"\x00", b"\x1a", bytes(512), b"\x1a" * 128, self.blob):
            with self.subTest(size=len(tail)), self.assertRaisesRegex(package.PackageError, "總長"):
                package.parse_package(self.blob + tail)
        with self.assertRaisesRegex(package.PackageError, "總長"):
            package.parse_package(bytes(0x18201))

    def test_aligned_payload_requires_extra_block(self) -> None:
        blob = package.build_package(bytes(512), 512)
        self.assertEqual(len(blob), 1536)
        with self.assertRaisesRegex(package.PackageError, "總長"):
            package.parse_package(blob[:-512])

    def test_preserves_trailing_sub_and_zero_bytes_without_truncation(self) -> None:
        for size in (1, 511, 512, 513, 97792):
            for tail in (b"\x1a", b"\x00"):
                with self.subTest(size=size, tail=tail):
                    payload = tail * size
                    blob = package.build_package(payload, (size + 15) // 16 * 16)
                    self.assertEqual(blob.rstrip(b"\x1a"), blob)
                    self.assertEqual(package.parse_package(blob)["image_size"], size)
                    self.assertEqual(blob[512:512 + size], payload)
                    with self.assertRaises(package.PackageError):
                        package.parse_package(blob.rstrip(b"\x00"))


class FileAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-package-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / "payload.bin"
        self.output = self.root / "smoke.spl2"
        self.input.write_bytes(b"abc\x1a")

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(MODULE_PATH), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
            cwd=self.root,
        )

    def pack_args(self, input_path: Path | None = None, output_path: Path | None = None) -> list[str]:
        return [
            "pack", "--input", str(input_path or self.input),
            "--output", str(output_path or self.output), "--runtime-size", "0x10",
        ]

    def assert_cli_failure(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("錯誤", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_pack_and_inspect_json(self) -> None:
        packed = self.invoke(*self.pack_args())
        self.assertEqual(packed.returncode, 0, packed.stderr)
        self.assertEqual(packed.stdout, "")
        inspected = self.invoke("inspect", "--input", self.output)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(inspected.stderr, "")
        self.assertEqual(json.loads(inspected.stdout), package.parse_package(self.output.read_bytes()))
        self.assertTrue(stat.S_ISREG(self.output.lstat().st_mode))

    def test_cli_accepts_decimal_hex_and_relative_paths(self) -> None:
        for index, value in enumerate(("16", "0x10", "0X10", "0016")):
            with self.subTest(value=value):
                name = f"smoke-{index}.spl2"
                result = self.invoke(
                    "pack", "--input", "payload.bin", "--output", name,
                    "--runtime-size", value,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(package.parse_package((self.root / name).read_bytes())["runtime_size"], 16)

    def test_cli_maximum_size(self) -> None:
        self.input.write_bytes(bytes(97792))
        result = self.invoke(
            "pack", "--input", self.input, "--output", self.output,
            "--runtime-size", "0x18000",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.stat().st_size, 98816)
        self.assertEqual(self.invoke("inspect", "--input", self.output).returncode, 0)

    def test_invalid_inputs_do_not_create_output(self) -> None:
        for payload in (b"", bytes(97793)):
            with self.subTest(size=len(payload)):
                self.input.write_bytes(payload)
                self.assert_cli_failure(self.invoke(*self.pack_args()))
                self.assertFalse(self.output.exists())

    def test_invalid_runtime_does_not_create_output(self) -> None:
        for value in ("0", "3", "17", "0x18010", "-16", "nan"):
            with self.subTest(value=value):
                args = self.pack_args()
                args[-1] = value
                self.assert_cli_failure(self.invoke(*args))
                self.assertFalse(self.output.exists())

    def test_inspect_rejects_corruption_and_oversize_without_json(self) -> None:
        blob = package.build_package(b"abc", 16)
        for broken in (b"", blob[:-1], blob + b"\x1a", blob[:-1] + b"\x01", bytes(98817)):
            with self.subTest(size=len(broken)):
                self.output.write_bytes(broken)
                self.assert_cli_failure(self.invoke("inspect", "--input", self.output))

    def test_rejects_existing_output_and_same_input(self) -> None:
        self.output.write_bytes(b"\x55\xaa")
        self.assert_cli_failure(self.invoke(*self.pack_args()))
        self.assertEqual(self.output.read_bytes(), b"\x55\xaa")
        self.assert_cli_failure(self.invoke(*self.pack_args(output_path=self.input)))
        self.assertEqual(self.input.read_bytes(), b"abc\x1a")

    def test_rejects_existing_hardlink_output(self) -> None:
        os.link(self.input, self.output)
        self.assert_cli_failure(self.invoke(*self.pack_args()))
        self.assertEqual(self.input.read_bytes(), b"abc\x1a")

    def test_rejects_input_symlink_and_dangling_symlink(self) -> None:
        for target in (self.input, self.root / "missing.bin"):
            with self.subTest(target=target):
                link = self.root / "input-link"
                link.symlink_to(target)
                try:
                    self.assert_cli_failure(self.invoke(*self.pack_args(input_path=link)))
                    self.assert_cli_failure(self.invoke("inspect", "--input", link))
                    self.assertFalse(self.output.exists())
                finally:
                    link.unlink()

    def test_rejects_output_symlink_and_dangling_symlink(self) -> None:
        for target in (self.input, self.root / "missing.bin"):
            with self.subTest(target=target):
                self.output.symlink_to(target)
                try:
                    self.assert_cli_failure(self.invoke(*self.pack_args()))
                    self.assertTrue(self.output.is_symlink())
                    self.assertEqual(self.input.read_bytes(), b"abc\x1a")
                    self.assertFalse((self.root / "missing.bin").exists())
                finally:
                    self.output.unlink()

    def test_rejects_symlink_in_parent_path_including_dotdot(self) -> None:
        directory = self.root / "real"
        directory.mkdir()
        link = self.root / "linked"
        link.symlink_to(directory, target_is_directory=True)
        (directory / "payload.bin").write_bytes(b"abc")
        for input_path in (link / "payload.bin", link / ".." / "payload.bin"):
            with self.subTest(input_path=input_path):
                self.assert_cli_failure(self.invoke(*self.pack_args(input_path=input_path)))
                self.assert_cli_failure(self.invoke("inspect", "--input", input_path))
        self.assert_cli_failure(self.invoke(*self.pack_args(output_path=link / "smoke.spl2")))
        self.assertFalse((directory / "smoke.spl2").exists())

    def test_rejects_directory_and_fifo_without_blocking(self) -> None:
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (self.root, fifo):
            with self.subTest(path=path):
                self.assert_cli_failure(self.invoke(*self.pack_args(input_path=path)))
                self.assert_cli_failure(self.invoke("inspect", "--input", path))
                self.assert_cli_failure(self.invoke(*self.pack_args(output_path=path)))
                self.assertFalse(self.output.exists())
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_missing_input_and_output_parent(self) -> None:
        missing = self.root / "missing" / "file"
        self.assert_cli_failure(self.invoke(*self.pack_args(input_path=missing)))
        self.assert_cli_failure(self.invoke("inspect", "--input", missing))
        self.assert_cli_failure(self.invoke(*self.pack_args(output_path=missing)))
        self.assertFalse(missing.parent.exists())

    def test_special_input_modes_rejected_before_open(self) -> None:
        # 裝置類型以中介資料模擬，測試不建立或開啟任何硬體裝置。
        for mode in (stat.S_IFBLK, stat.S_IFCHR, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFLNK, stat.S_IFDIR):
            with self.subTest(mode=mode), mock.patch.object(
                package, "_parent_directory",
            ) as parent, mock.patch.object(package.os, "stat") as metadata, mock.patch.object(
                package.os, "open",
            ) as opened:
                parent.return_value.__enter__.return_value = (123, "payload.bin")
                metadata.return_value.st_mode = mode | 0o600
                with self.assertRaisesRegex(package.PackageError, "一般檔案"):
                    package.read_regular_file(self.input, 97792)
                opened.assert_not_called()
                metadata.assert_called_once_with("payload.bin", dir_fd=123, follow_symlinks=False)

    def test_input_descriptor_rechecked_before_read(self) -> None:
        original = self.input.stat()
        for mode, inode in ((stat.S_IFIFO, original.st_ino), (stat.S_IFREG, original.st_ino + 1)):
            changed = SimpleNamespace(st_mode=mode, st_dev=original.st_dev, st_ino=inode)
            with self.subTest(mode=mode), mock.patch.object(
                package.os, "fstat", return_value=changed,
            ), mock.patch.object(package.os, "fdopen") as read:
                with self.assertRaises(package.PackageError):
                    package.read_regular_file(self.input, 97792)
                read.assert_not_called()

    def test_input_symlink_swap_is_not_followed(self) -> None:
        real_open = os.open
        target = self.root / "target.bin"
        target.write_bytes(b"\xaa")

        def swapped_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
            if path == self.input.name:
                self.input.unlink()
                self.input.symlink_to(target)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(package.os, "open", side_effect=swapped_open):
            with self.assertRaisesRegex(package.PackageError, "一般檔案"):
                package.read_regular_file(self.input, 97792)
        self.assertEqual(target.read_bytes(), b"\xaa")

    def test_input_fifo_swap_rejected_before_data_open(self) -> None:
        real_open = os.open

        def swapped_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
            self.assertFalse(str(path).startswith("/proc/self/fd/"))
            if path == self.input.name:
                self.assertTrue(flags & os.O_PATH)
                self.input.unlink()
                os.mkfifo(self.input)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(package.os, "open", side_effect=swapped_open):
            with self.assertRaisesRegex(package.PackageError, "一般檔案"):
                package.read_regular_file(self.input, 97792)

    def test_input_growth_and_truncation_during_read(self) -> None:
        opened = self.input.stat()
        changed = SimpleNamespace(
            st_size=opened.st_size, st_mtime_ns=opened.st_mtime_ns + 1,
            st_ctime_ns=opened.st_ctime_ns,
        )
        for after in (changed, opened):
            with self.subTest(after=after), mock.patch.object(
                package.os, "fstat", side_effect=(opened, after),
            ), mock.patch.object(package.os, "fdopen") as source:
                source.return_value.__enter__.return_value.read.return_value = b"abc" if after is opened else b"abc\x1a"
                with self.assertRaisesRegex(package.PackageError, "變動"):
                    package.read_regular_file(self.input, 97792)

    def test_read_limit_is_enforced_even_if_file_grows(self) -> None:
        with mock.patch.object(package.os, "fdopen") as source:
            reader = source.return_value.__enter__.return_value
            reader.read.return_value = bytes(17)
            with self.assertRaisesRegex(package.PackageError, "上限"):
                package.read_regular_file(self.input, 16)
            reader.read.assert_called_once_with(17)

    def test_output_creation_is_exclusive_even_during_race(self) -> None:
        real_open = os.open

        def competing_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
            if path == self.output.name:
                self.assertTrue(flags & os.O_EXCL)
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertFalse(flags & os.O_TRUNC)
                self.output.write_bytes(b"\xaa")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(package.os, "open", side_effect=competing_open):
            with self.assertRaisesRegex(package.PackageError, "禁止覆寫"):
                package.write_new_regular_file(self.output, b"\xbb")
        self.assertEqual(self.output.read_bytes(), b"\xaa")

    def test_output_descriptor_must_be_regular_before_write(self) -> None:
        with mock.patch.object(package.os, "fstat") as metadata, mock.patch.object(
            package.os, "fdopen",
        ) as destination:
            metadata.return_value.st_mode = stat.S_IFBLK
            with self.assertRaisesRegex(package.PackageError, "一般檔案"):
                package.write_new_regular_file(self.output, b"abc")
            destination.assert_not_called()

    def test_help_and_argument_errors_are_chinese(self) -> None:
        for args in (("--help",), ("pack", "--help"), ("inspect", "--help")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 0)
                self.assertIn("用法：", result.stdout)
                self.assertIn("選項", result.stdout)
                self.assertNotIn("usage:", result.stdout)
                self.assertNotIn("show this help", result.stdout)
        for args in ((), ("unknown",), ("pack",), ("inspect", "--input"), ("inspect", "--in", "x")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assert_cli_failure(result)
                self.assertEqual(result.returncode, 2)
                self.assertIn("參數錯誤", result.stderr)
                self.assertNotIn("required", result.stderr)
                self.assertNotIn("invalid choice", result.stderr)

    def test_main_returns_status_without_traceback_on_io_error(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(package, "read_regular_file", side_effect=OSError(13, "")):
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(package.main(self.pack_args()), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("errno=13", err.getvalue())
        self.assertIn("一般檔案操作失敗", err.getvalue())


if __name__ == "__main__":
    unittest.main()
