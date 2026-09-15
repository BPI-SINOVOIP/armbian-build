#!/usr/bin/env python3
"""第三版實驗封包的格式、版本隔離與離線 CLI 安全限制測試。"""

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_sram_package as base
import bpi_sram_ddr_package as ddr
import bpi_sram_lab_package as lab


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "bpi_sram_lab_package.py"


def refresh_crc(blob: bytearray) -> bytes:
    struct.pack_into("<I", blob, 508, zlib.crc32(blob[:508]))
    return bytes(blob)


def replace_u32(blob: bytes, offset: int, value: int) -> bytes:
    modified = bytearray(blob)
    struct.pack_into("<I", modified, offset, value)
    return refresh_crc(modified)


class PackageFormatTests(unittest.TestCase):
    def setUp(self):
        self.payload = bytes(range(256)) * 2 + b"\x1a"
        self.blob = lab.build_package(self.payload, 528, lab.KIND_UPDATE)

    def test_exact_header_and_integer_metadata_for_both_kinds(self):
        self.assertEqual((lab.VERSION, lab.KIND_UPDATE, lab.KIND_BOOT), (3, 3, 4))
        for kind in (3, 4):
            with self.subTest(kind=kind):
                header = (
                    b"BPISRAM1"
                    + struct.pack("<8I", 3, 512, 0x06180001, 3, 16, 0x30000, 0, kind)
                    + hashlib.sha256(b"abc").digest() + bytes(436)
                )
                expected = header + struct.pack("<I", zlib.crc32(header)) + b"abc" + bytes(509)
                blob = lab.build_package(b"abc", 16, kind)
                self.assertEqual(blob, expected)
                info = lab.parse_package(blob)
                self.assertEqual(info, {
                    "version": 3, "kind": kind, "image_size": 3,
                    "runtime_size": 16, "raw_size": 1024,
                    "hash": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                })
                self.assertIs(type(info["version"]), int)
                self.assertIs(type(info["kind"]), int)

    def test_size_boundaries_and_exact_padding(self):
        for size in (1, 511, 512, 513, 0x18000 - 512):
            with self.subTest(size=size):
                payload = b"\x1a" * size
                blob = lab.build_package(payload, 0x18000, lab.KIND_BOOT)
                info = lab.parse_package(blob)
                self.assertEqual(info["image_size"], size)
                self.assertEqual(info["runtime_size"], 0x18000)
                self.assertEqual(info["raw_size"], 512 + (size // 512 + 1) * 512)
                self.assertEqual(blob[512:512 + size], payload)
                self.assertEqual(blob[512 + size:], bytes(512 - size % 512))
                self.assertEqual(info["hash"], hashlib.sha256(payload).hexdigest())

    def test_api_byte_types(self):
        self.assertEqual(lab.build_package(bytearray(self.payload), 528, 3), self.blob)
        self.assertEqual(lab.parse_package(bytearray(self.blob)), lab.parse_package(self.blob))
        for value in (None, "abc", 3, [1, 2, 3]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(base.PackageError, "位元組"):
                    lab.build_package(value, 16, 3)
                with self.assertRaisesRegex(base.PackageError, "位元組"):
                    lab.parse_package(value)

    def test_builder_rejects_invalid_kind_values_and_types(self):
        for kind in (0, 1, 2, 5, -1, True, 3.0, "update", None):
            with self.subTest(kind=kind), self.assertRaisesRegex(base.PackageError, "kind"):
                lab.build_package(b"abc", 16, kind)

    def test_parser_rejects_other_versions_with_valid_crc(self):
        for version in (0, 1, 2, 4):
            with self.subTest(version=version), self.assertRaisesRegex(base.PackageError, "版本"):
                lab.parse_package(replace_u32(self.blob, 8, version))

    def test_parser_rejects_other_kinds_with_valid_crc(self):
        for kind in (0, 1, 2, 5):
            with self.subTest(kind=kind), self.assertRaisesRegex(base.PackageError, "kind"):
                lab.parse_package(replace_u32(self.blob, 36, kind))

    def test_v1_v2_and_v3_are_mutually_rejected(self):
        for legacy in (base, ddr):
            with self.subTest(version=legacy.VERSION):
                with self.assertRaises(base.PackageError):
                    lab.parse_package(legacy.build_package(b"abc", 16))
                for kind in (3, 4):
                    with self.subTest(kind=kind), self.assertRaises(base.PackageError):
                        legacy.parse_package(lab.build_package(b"abc", 16, kind))

    def test_fixed_header_fields_cannot_be_relaxed_by_crc(self):
        for offset, value in ((0, 0), (12, 1024), (16, 0x06180002), (28, 0x30004), (32, 1)):
            with self.subTest(offset=offset), self.assertRaisesRegex(base.PackageError, "識別"):
                lab.parse_package(replace_u32(self.blob, offset, value))

    def test_builder_rejects_empty_and_oversize_payloads(self):
        for size in (0, 0x18000 - 511):
            with self.subTest(size=size), self.assertRaisesRegex(base.PackageError, "映像大小"):
                lab.build_package(bytes(size), 0x18000, 3)

    def test_builder_rejects_invalid_runtime_sizes_and_types(self):
        for runtime in (-16, 0, 16, 33, 0x18010, True, 32.0, "32", None):
            with self.subTest(runtime=runtime), self.assertRaisesRegex(base.PackageError, "執行期"):
                lab.build_package(bytes(32), runtime, 3)

    def test_parser_revalidates_image_and_runtime_sizes(self):
        for offset, value in ((20, 0), (20, 0x18000 - 511), (24, 512), (24, 529), (24, 0x18010)):
            with self.subTest(offset=offset, value=value), self.assertRaises(base.PackageError):
                lab.parse_package(replace_u32(self.blob, offset, value))

    def test_crc_covers_header_and_digest(self):
        for offset in (40, 508):
            broken = bytearray(self.blob)
            broken[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(base.PackageError, "CRC"):
                lab.parse_package(broken)

    def test_sha_detects_payload_and_digest_corruption(self):
        for offset in (40, 512):
            broken = bytearray(self.blob)
            broken[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaisesRegex(base.PackageError, "SHA-256"):
                lab.parse_package(refresh_crc(broken))

    def test_reserved_header_bytes_must_be_zero_even_with_valid_crc(self):
        for offset in (72, 507):
            broken = bytearray(self.blob)
            broken[offset] = 1
            with self.subTest(offset=offset), self.assertRaisesRegex(base.PackageError, "保留區"):
                lab.parse_package(refresh_crc(broken))

    def test_padding_is_zero_and_aligned_payload_keeps_full_block(self):
        for offset in (512 + len(self.payload), len(self.blob) - 1):
            broken = bytearray(self.blob)
            broken[offset] = 0x1a
            with self.subTest(offset=offset), self.assertRaisesRegex(base.PackageError, "填補"):
                lab.parse_package(broken)
        aligned = lab.build_package(bytes(512), 512, 4)
        self.assertEqual(len(aligned), 1536)
        self.assertEqual(aligned[-512:], bytes(512))
        with self.assertRaisesRegex(base.PackageError, "長度"):
            lab.parse_package(aligned[:-512])

    def test_truncation_extra_data_and_total_size_limits(self):
        for broken in (
            b"", self.blob[:511], self.blob[:512], self.blob[:1024], self.blob[:-1],
            self.blob + b"\x00", self.blob + bytes(512), bytes(base.MAX_PACKAGE_BYTES + 1),
        ):
            with self.subTest(size=len(broken)), self.assertRaises(base.PackageError):
                lab.parse_package(broken)


class FileAndCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-lab-package-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input = self.root / "payload.bin"
        self.output = self.root / "lab.spl2"
        self.payload = b"abc\x1a"
        self.input.write_bytes(self.payload)

    def invoke(self, *args, module=False):
        command = ["-m", "tools.bpi_sram_lab_package"] if module else [str(MODULE_PATH)]
        return subprocess.run(
            [sys.executable, "-B", *command, *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
            cwd=ROOT if module else self.root,
        )

    def pack_args(self, kind="update", runtime="0x10", output=None):
        return [
            "pack", "--input", str(self.input), "--output", str(output or self.output),
            "--runtime-size", runtime, "--kind", kind,
        ]

    def assert_failure(self, result, code=1):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("錯誤", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_pack_inspect_both_kinds_and_import_modes(self):
        for name, kind, runtime, module in (("update", 3, "0x10", False), ("boot", 4, "16", True)):
            with self.subTest(kind=name):
                output = self.root / f"{name}.spl2"
                result = self.invoke(*self.pack_args(name, runtime, output), module=module)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(output.read_bytes(), lab.build_package(self.payload, 16, kind))
                info = json.loads(result.stdout)
                self.assertEqual(info["kind"], kind)
                inspected = self.invoke("inspect", "--input", output, module=module)
                self.assertEqual(inspected.returncode, 0, inspected.stderr)
                self.assertEqual(inspected.stderr, "")
                self.assertEqual(json.loads(inspected.stdout), info)
                self.assertEqual(info, lab.parse_package(output.read_bytes()))

    def test_cli_requires_explicit_supported_kind(self):
        for args in (self.pack_args()[:-2], self.pack_args("ddr"), self.pack_args("3")):
            with self.subTest(args=args):
                self.assert_failure(self.invoke(*args), code=2)
                self.assertFalse(self.output.exists())

    def test_invalid_payload_or_runtime_does_not_create_output(self):
        for payload, runtime, code in (
            (b"", "16", 1), (bytes(base.MAX_IMAGE_BYTES + 1), "0x18000", 1),
            (self.payload, "17", 1), (self.payload, "0x18010", 1), (self.payload, "nan", 2),
        ):
            with self.subTest(size=len(payload), runtime=runtime):
                self.input.write_bytes(payload)
                self.assert_failure(self.invoke(*self.pack_args(runtime=runtime)), code=code)
                self.assertFalse(self.output.exists())

    def test_existing_output_same_input_and_hardlink_are_not_overwritten(self):
        self.output.write_bytes(b"\x55\xaa")
        self.assert_failure(self.invoke(*self.pack_args()))
        self.assertEqual(self.output.read_bytes(), b"\x55\xaa")
        self.assert_failure(self.invoke(*self.pack_args(output=self.input)))
        hardlink = self.root / "hardlink.spl2"
        os.link(self.input, hardlink)
        self.assert_failure(self.invoke(*self.pack_args(output=hardlink)))
        self.assertEqual(hardlink.read_bytes(), self.payload)
        self.assertEqual(self.input.read_bytes(), self.payload)

    def test_output_symlinks_are_not_overwritten(self):
        missing = self.root / "missing.bin"
        for target in (self.input, missing):
            with self.subTest(target=target):
                self.output.symlink_to(target)
                try:
                    self.assert_failure(self.invoke(*self.pack_args()))
                    self.assertTrue(self.output.is_symlink())
                    self.assertEqual(self.output.readlink(), target)
                    self.assertEqual(self.input.read_bytes(), self.payload)
                    self.assertFalse(missing.exists())
                finally:
                    self.output.unlink()

    def test_inspect_rejects_old_versions_and_corruption_without_json(self):
        blob = lab.build_package(self.payload, 16, 3)
        for invalid in (base.build_package(self.payload, 16), ddr.build_package(self.payload, 16),
                        blob[:-1], blob[:-1] + b"\x01"):
            with self.subTest(header=invalid[:40], size=len(invalid)):
                self.output.write_bytes(invalid)
                self.assert_failure(self.invoke("inspect", "--input", self.output))
                self.assertEqual(self.output.read_bytes(), invalid)

    def test_nonregular_inputs_are_rejected_without_output(self):
        link = self.root / "input-link"
        link.symlink_to(self.input)
        fifo = self.root / "input-fifo"
        os.mkfifo(fifo)
        for source in (link, fifo, self.root):
            with self.subTest(source=source):
                args = self.pack_args()
                args[2] = str(source)
                self.assert_failure(self.invoke(*args))
                self.assert_failure(self.invoke("inspect", "--input", source))
                self.assertFalse(self.output.exists())

    def test_cli_help_and_filesystem_errors_use_chinese(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("用法：", result.stdout)
        self.assertIn("不執行或操作硬體", result.stdout)
        self.assertNotIn("usage:", result.stdout)
        result = self.invoke("inspect", "--input", self.root / "missing.bin")
        self.assert_failure(result)
        self.assertIn("一般檔案操作失敗", result.stderr)
        self.assertIn("errno=2", result.stderr)
        self.assertNotIn("No such file", result.stderr)


if __name__ == "__main__":
    unittest.main()
