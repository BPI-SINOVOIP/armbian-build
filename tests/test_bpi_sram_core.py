#!/usr/bin/env python3
"""直接編譯 SRAM C 核心，驗證固定 ABI 與記憶體安全；不存取設備。"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "patch/lab/u-boot/bananapim4zero/sram-supervisor"
HARNESS = ROOT / "tests/test_bpi_sram_core.c"
UINT32_MAX = (1 << 32) - 1
IMAGE_MAX = 97792
RUNTIME_MAX = 98304
U8 = ctypes.c_uint8
U32 = ctypes.c_uint32


class Image(ctypes.Structure):
    _fields_ = [
        ("image_bytes", U32), ("runtime_bytes", U32),
        ("package_bytes", U32), ("kind", U32), ("digest", U8 * 32),
    ]


class GuardedImage(ctypes.Structure):
    _fields_ = [("before", U8 * 16), ("image", Image), ("after", U8 * 16)]


def seal(header: bytearray) -> bytearray:
    struct.pack_into("<I", header, 508, zlib.crc32(header[:508]))
    return header


def header_for(image: int = 513, runtime: int = 1024) -> bytearray:
    header = bytearray(512)
    struct.pack_into(
        "<8s8I", header, 0, b"BPISRAM1", 1, 512, 0x06180001,
        image, runtime, 0x30000, 0, 1,
    )
    header[40:72] = bytes(range(32))
    return seal(header)


def expected_header(header: bytes | bytearray) -> bool:
    """用 Python 任意精度整數判定固定 ABI，不導入主機封包工具。"""
    fields = struct.unpack_from("<8s8I", header)
    magic, version, size, board, image, runtime, entry, flags, kind = fields
    return (
        (magic, version, size, board, entry, flags, kind)
        == (b"BPISRAM1", 1, 512, 0x06180001, 0x30000, 0, 1)
        and not any(header[72:508])
        and zlib.crc32(header[:508]) == struct.unpack_from("<I", header, 508)[0]
        and 1 <= image <= IMAGE_MAX
        and image <= runtime <= RUNTIME_MAX
        and runtime % 16 == 0
    )


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-core-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
        cls.environment = dict(os.environ, TMPDIR=str(cls.directory))
        cls.library_path = cls.directory / "libbpi_sram_core.so"
        cls.compile(cls.library_path, ["-shared", "-fPIC"])
        cls.lib = ctypes.CDLL(str(cls.library_path))
        cls.lib.sup_header.argtypes = [ctypes.POINTER(U8), ctypes.POINTER(Image)]
        cls.lib.sup_header.restype = ctypes.c_int
        cls.lib.sup_padding.argtypes = [ctypes.POINTER(U8), U32]
        cls.lib.sup_padding.restype = ctypes.c_int
        cls.lib.sup_decimal.argtypes = [ctypes.c_char_p, U32, ctypes.POINTER(U32)]
        cls.lib.sup_decimal.restype = ctypes.c_int
        cls.lib.sup_slot.argtypes = [U32, ctypes.POINTER(U32)]
        cls.lib.sup_slot.restype = ctypes.c_int
        cls.lib.sup_crc32.argtypes = [ctypes.POINTER(U8), U32]
        cls.lib.sup_crc32.restype = U32
        cls.lib.sup_test_image_size.argtypes = []
        cls.lib.sup_test_image_size.restype = ctypes.c_size_t

    @classmethod
    def compile(cls, output: Path, flags: list[str]) -> None:
        command = cls.compiler + [
            "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-DSUP_HOST_TEST", "-I", str(CORE), *flags,
            str(CORE / "supervisor_core.c"), str(HARNESS), "-lz", "-o", str(output),
        ]
        try:
            result = subprocess.run(
                command, cwd=cls.directory, env=cls.environment,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=90, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("無法編譯 C 測試；請確認主機編譯器與 zlib 開發檔案") from exc
        if result.returncode:
            raise RuntimeError(f"C 測試編譯失敗：\n{result.stdout}{result.stderr}")

    def check_header(self, header: bytes | bytearray, valid: bool) -> Image:
        self.assertEqual(len(header), 512, "核心呼叫端必須提供完整 512-byte 標頭")
        source = (U8 * 512).from_buffer_copy(header)
        out = GuardedImage()
        ctypes.memset(ctypes.byref(out), 0xA5, ctypes.sizeof(out))
        original = bytes(out)
        result = self.lib.sup_header(source, ctypes.byref(out.image))
        self.assertEqual(result, 0 if valid else -1, "C 核心標頭判定不符")
        self.assertEqual(bytes(source), bytes(header), "核心改寫了輸入標頭")
        self.assertEqual(bytes(out.before), bytes([0xA5]) * 16, "輸出前哨兵遭改寫")
        self.assertEqual(bytes(out.after), bytes([0xA5]) * 16, "輸出後哨兵遭改寫")
        if not valid:
            self.assertEqual(bytes(out), original, "拒絕標頭時輸出結構必須完全不變")
        else:
            image, runtime = struct.unpack_from("<2I", header, 20)
            self.assertEqual(out.image.image_bytes, image, "映像長度不符")
            self.assertEqual(out.image.runtime_bytes, runtime, "執行期長度不符")
            self.assertEqual(
                out.image.package_bytes, 512 + image + (512 - image % 512),
                "封包必須包含標頭及至少一個零填補位元組",
            )
            self.assertEqual(out.image.kind, 1, "負載種類不符")
            self.assertEqual(bytes(out.image.digest), bytes(header[40:72]), "摘要複製不符")
        return out.image

    def test_abi_and_crc(self) -> None:
        """核對 ctypes 結構大小與 zlib 標準 CRC 向量。"""
        self.assertEqual(ctypes.sizeof(Image), 48, "測試結構 ABI 大小不符")
        self.assertEqual(self.lib.sup_test_image_size(), 48, "C 結構 ABI 大小不符")
        vector = (U8 * 9).from_buffer_copy(b"123456789")
        self.assertEqual(self.lib.sup_crc32(vector, 9), 0xCBF43926, "CRC 實作不符標準向量")
        self.assertEqual(self.lib.sup_crc32(vector, 0), 0, "空 CRC 不符")

    def test_valid_header_and_package_padding(self) -> None:
        """合法標頭與已對齊映像仍保留完整填補區塊。"""
        for image, runtime in [(1, 16), (16, 16), (511, 512), (512, 512),
                               (513, 528), (IMAGE_MAX, IMAGE_MAX), (IMAGE_MAX, RUNTIME_MAX)]:
            with self.subTest(映像=image, 執行期=runtime):
                out = self.check_header(header_for(image, runtime), True)
                self.assertLessEqual(out.package_bytes, 98816, "封包超出 ABI 容量")

    def test_each_bit_without_recomputed_crc(self) -> None:
        """逐一翻轉全部 4096 位元，損壞標頭一律拒絕。"""
        baseline = header_for()
        for offset in range(512):
            for bit in range(8):
                with self.subTest(位移=offset, 位元=bit):
                    header = baseline.copy()
                    header[offset] ^= 1 << bit
                    self.check_header(header, False)

    def test_each_bit_with_recomputed_crc(self) -> None:
        """重算 CRC 後覆蓋識別碼、各欄位及全部保留區守門。"""
        baseline = header_for()
        for offset in range(508):
            for bit in range(8):
                with self.subTest(位移=offset, 位元=bit):
                    header = baseline.copy()
                    header[offset] ^= 1 << bit
                    seal(header)
                    self.check_header(header, expected_header(header))

    def test_fixed_fields_reject_wrong_values(self) -> None:
        """固定欄位即使 CRC 正確也拒絕零、錯誤版本與最大整數。"""
        for offset, correct in [(8, 1), (12, 512), (16, 0x06180001),
                                (28, 0x30000), (32, 0), (36, 1)]:
            for value in {0, 1, 2, correct - 1 if correct else 1, correct + 1, UINT32_MAX}:
                if value == correct:
                    continue
                with self.subTest(位移=offset, 值=value):
                    header = header_for()
                    struct.pack_into("<I", header, offset, value)
                    self.check_header(seal(header), False)

    def test_image_runtime_boundaries(self) -> None:
        """映像與執行期交叉邊界，包含 UINT32_MAX 與全部對齊餘數。"""
        values = [0, 1, 15, 16, 17, 511, 512, 513, IMAGE_MAX - 1, IMAGE_MAX,
                  IMAGE_MAX + 1, RUNTIME_MAX - 16, RUNTIME_MAX - 1, RUNTIME_MAX,
                  RUNTIME_MAX + 1, UINT32_MAX - 15, UINT32_MAX]
        for image in values:
            for runtime in values:
                with self.subTest(映像=image, 執行期=runtime):
                    valid = 1 <= image <= IMAGE_MAX and image <= runtime <= RUNTIME_MAX and runtime % 16 == 0
                    self.check_header(header_for(image, runtime), valid)
        for remainder in range(16):
            with self.subTest(對齊餘數=remainder):
                self.check_header(header_for(1, 512 + remainder), remainder == 0)

    def test_digest_is_copied_not_authenticated(self) -> None:
        """標頭核心只複製摘要；不假稱已驗證負載或來源。"""
        for digest in [bytes(32), bytes([255]) * 32, bytes(range(31, -1, -1))]:
            header = header_for()
            header[40:72] = digest
            self.check_header(seal(header), True)

    def test_unaligned_header(self) -> None:
        """標頭可從未對齊的位址解析。"""
        baseline = header_for()
        for offset in range(1, 8):
            with self.subTest(位移=offset):
                storage = (U8 * (512 + offset))()
                ctypes.memmove(ctypes.byref(storage, offset), bytes(baseline), 512)
                pointer = ctypes.cast(ctypes.byref(storage, offset), ctypes.POINTER(U8))
                out = Image()
                self.assertEqual(self.lib.sup_header(pointer, ctypes.byref(out)), 0, "未對齊標頭解析失敗")
                self.assertEqual(out.image_bytes, 513, "未對齊讀取結果不符")

    def test_padding_boundaries_and_mutations(self) -> None:
        """零長與所有填補長度，並檢查每個位置的非零拒絕。"""
        for size in range(513):
            data = (U8 * 513)()
            data[size] = 0xA5
            original = bytes(data)
            self.assertEqual(self.lib.sup_padding(data, size), 0, "讀取超出填補長度")
            self.assertEqual(bytes(data), original, "填補檢查改寫輸入")
        for offset in range(512):
            for value in (1, 0x1A, 0x80, 0xFF):
                data = (U8 * 512)()
                data[offset] = value
                self.assertEqual(self.lib.sup_padding(data, 512), -1, "非零填補未被拒絕")
                self.assertEqual(data[offset], value, "填補檢查改寫輸入")

    def check_decimal(self, text: bytes | None, limit: int, expected: int | None) -> None:
        out = (U32 * 3)(0x12345678, 0xA5A5A5A5, 0x87654321)
        pointer = ctypes.cast(ctypes.byref(out, ctypes.sizeof(U32)), ctypes.POINTER(U32))
        result = self.lib.sup_decimal(text, limit, pointer)
        self.assertEqual(result, -1 if expected is None else 0, "十進位判定不符")
        self.assertEqual(out[1], 0xA5A5A5A5 if expected is None else expected, "十進位輸出不符或失敗時遭改寫")
        self.assertEqual((out[0], out[2]), (0x12345678, 0x87654321), "十進位輸出哨兵遭改寫")

    def test_decimal_limits(self) -> None:
        """十進位限制包含零、逐位進位、前導零與 32 位元溢位。"""
        for limit in [0, 1, 4, 5, 9, 10, 99, 100, 65535, UINT32_MAX - 1, UINT32_MAX]:
            for value in sorted({0, 1, max(0, limit - 1), limit, limit + 1, UINT32_MAX, UINT32_MAX + 1}):
                for prefix in (b"", b"000"):
                    with self.subTest(上限=limit, 值=value, 前導零=bool(prefix)):
                        self.check_decimal(prefix + str(value).encode("ascii"), limit, value if value <= limit else None)
        self.check_decimal(b"0" * 4096, 0, 0)
        self.check_decimal(b"9" * 4096, UINT32_MAX, None)

    def test_decimal_invalid_characters(self) -> None:
        """空指標、空字串、符號、空白及非 ASCII 數字全部拒絕。"""
        invalid = [None, b"", b" ", b"+1", b"-1", b" 1", b"1 ", b"1\n", b"1\r",
                   b"1\t", b"0x1", b"1.0", b"1e2", "１２".encode("utf-8")]
        for text in invalid:
            with self.subTest(輸入=text):
                self.check_decimal(text, UINT32_MAX, None)
        for value in range(1, 256):
            if not ord("0") <= value <= ord("9"):
                self.check_decimal(b"12" + bytes([value]) + b"3", UINT32_MAX, None)

    def test_slots_and_decimal_overflow(self) -> None:
        """五個固定槽、非法索引與十進位超大索引不可繞回合法槽。"""
        for index in [*range(7), 65535, 1 << 31, UINT32_MAX]:
            with self.subTest(槽=index):
                out = U32(0xA5A5A5A5)
                self.assertEqual(self.lib.sup_slot(index, ctypes.byref(out)), 0 if index < 5 else -1, "槽索引判定不符")
                self.assertEqual(out.value, 6144 + 2048 * index if index < 5 else 0xA5A5A5A5, "槽 LBA 不符或拒絕時遭改寫")
        for text in [b"5", b"4294967295", b"4294967296", b"4294967300", b"18446744073709551616"]:
            self.check_decimal(text, 4, None)

    def test_c_stress_with_asan_ubsan(self) -> None:
        """ASan／UBSan 獨立 C 程式執行全位元組變異及固定種子壓力測試。"""
        executable = self.directory / "sram_core_sanitized"
        self.compile(executable, [
            "-DSUP_TEST_MAIN", "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer", "-fno-sanitize-recover=all",
        ])
        environment = dict(
            self.environment, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
            UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
        )
        try:
            result = subprocess.run(
                [str(executable)], cwd=self.directory, env=environment,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.fail(f"無法完成 C 壓力測試：{exc}")
        self.assertEqual(result.returncode, 0, f"C 壓力或消毒器測試失敗：\n{result.stdout}{result.stderr}")
        self.assertEqual(result.stderr, "", "消毒器產生非預期診斷")
        self.assertIn("固定種子 0x20260915，隨機標頭 20000 筆", result.stdout, "壓力測試未完整執行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
