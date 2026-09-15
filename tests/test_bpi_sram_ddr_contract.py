#!/usr/bin/env python3
"""以真實 C 核心驗證新舊封包型別邊界；不接觸硬體。"""

import ctypes
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
import build_bpi_sram_supervisor as builder

ROOT = Path(__file__).resolve().parents[1]


class Image(ctypes.Structure):
    _fields_ = [("image_bytes", ctypes.c_uint32), ("runtime_bytes", ctypes.c_uint32),
                ("package_bytes", ctypes.c_uint32), ("kind", ctypes.c_uint32),
                ("digest", ctypes.c_uint8 * 32)]


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.libs = []
        for v2 in (False, True):
            out = Path(cls.temp.name) / ("v2.so" if v2 else "v1.so")
            source = ROOT / "patch/lab/u-boot/bananapim4zero/sram-supervisor"
            cmd = ["cc", "-shared", "-fPIC", "-std=c11", "-Wall", "-Wextra", "-Werror",
                   "-DSUP_HOST_TEST", "-I", str(source), str(source / "supervisor_core.c"),
                   str(ROOT / "tests/test_bpi_sram_core.c"), "-lz", "-o", str(out)]
            if v2:
                cmd.insert(1, "-DCONFIG_BPI_SRAM_DDR_V2=1")
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            lib = ctypes.CDLL(str(out))
            lib.sup_header.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(Image)]
            lib.sup_header.restype = ctypes.c_int
            cls.libs.append(lib)

    def accepts(self, blob, v2):
        data = (ctypes.c_uint8 * 512).from_buffer_copy(blob[:512])
        result = Image()
        code = self.libs[v2].sup_header(data, ctypes.byref(result))
        return code, result

    def test_v2_requires_explicit_build_switch(self):
        args = ["--source-git", "/source", "--output", "/new"]
        self.assertFalse(builder.build_parser().parse_args(args).ddr_v2)
        self.assertTrue(builder.build_parser().parse_args(args + ["--ddr-v2"]).ddr_v2)

    def test_legacy_accepts_smoke_but_rejects_ddr(self):
        self.assertEqual(self.accepts(base.build_package(b"a", 16), False)[0], 0)
        self.assertEqual(self.accepts(ddr.build_package(b"a", 16), False)[0], -1)

    def test_v2_accepts_both_matching_type_pairs(self):
        for kind, blob in ((1, base.build_package(b"a", 16)), (2, ddr.build_package(b"a", 16))):
            code, out = self.accepts(blob, True)
            self.assertEqual(code, 0)
            self.assertEqual(out.kind, kind)

    def test_version_kind_cross_pairs_rejected(self):
        for version, kind in ((1, 2), (2, 1), (0, 0), (3, 3), (2, 3)):
            blob = bytearray(ddr.build_package(b"a", 16))
            struct.pack_into("<I", blob, 8, version)
            struct.pack_into("<I", blob, 36, kind)
            struct.pack_into("<I", blob, 508, zlib.crc32(blob[:508]))
            self.assertEqual(self.accepts(blob, True)[0], -1)
            with self.assertRaises(base.PackageError):
                ddr.parse_package(blob)

    def test_new_parser_rejects_old_and_old_rejects_new(self):
        with self.assertRaises(base.PackageError):
            ddr.parse_package(base.build_package(b"abc", 16))
        with self.assertRaises(base.PackageError):
            base.parse_package(ddr.build_package(b"abc", 16))

    def test_roundtrip_boundaries(self):
        for size in (1, 511, 512, 513, base.MAX_IMAGE_BYTES):
            payload = bytes([0x1a]) * size
            blob = ddr.build_package(payload, base.MAX_RUNTIME_BYTES)
            self.assertEqual(ddr.parse_package(blob)["image_size"], size)
            self.assertEqual(self.accepts(blob, True)[0], 0)

    def test_byte_corruption_rejected(self):
        blob = ddr.build_package(b"a" * 513, 1024)
        for index in range(len(blob)):
            broken = bytearray(blob)
            broken[index] ^= 1
            with self.subTest(index=index), self.assertRaises(base.PackageError):
                ddr.parse_package(broken)

    def test_crc_recomputed_cannot_relax_sram_or_header(self):
        for offset, value in ((12, 0), (16, 1), (20, 0), (20, 98304), (24, 98320),
                              (24, 15), (24, 0), (28, 0x30004), (32, 1), (72, 1)):
            blob = bytearray(ddr.build_package(b"a", 16))
            struct.pack_into("<I", blob, offset, value)
            struct.pack_into("<I", blob, 508, zlib.crc32(blob[:508]))
            self.assertEqual(self.accepts(blob, True)[0], -1)
            with self.assertRaises(base.PackageError):
                ddr.parse_package(blob)

    def test_bad_sizes_and_non_bytes(self):
        for payload, runtime in ((b"", 16), (b"a", 0), (b"a", 17), (b"a", True),
                                 (b"a" * 32, 16), (b"a", 98320), ("a", 16)):
            with self.assertRaises(base.PackageError):
                ddr.build_package(payload, runtime)
        for blob in (b"", b"a" * 512, b"a" * 100000, "x"):
            with self.assertRaises(base.PackageError):
                ddr.parse_package(blob)

    def test_truncated_and_appended_data_rejected(self):
        blob = ddr.build_package(b"a" * 513, 1024)
        for broken in (blob[:-1], blob[:1024], blob + b"\x00", blob + bytes(512)):
            with self.assertRaises(base.PackageError):
                ddr.parse_package(broken)


if __name__ == "__main__":
    unittest.main()
