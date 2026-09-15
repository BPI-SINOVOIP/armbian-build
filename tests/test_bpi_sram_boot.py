#!/usr/bin/env python3
"""固定 FIT 橋接的主機回歸；執行到原 SPL 入口即停，不接觸硬體。"""

from contextlib import redirect_stderr
import importlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X18, UC_ARM64_REG_PSTATE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
builder = importlib.import_module("tools.build_bpi_sram_boot")


def altered(blob, offset, value, checksum=True):
    data = bytearray(blob)
    data[offset:offset + len(value)] = value
    if checksum:
        struct.pack_into("<I", data, 12, 0x5f0a6c39)
        struct.pack_into("<I", data, 12, sum(struct.unpack("<10240I", data)) & 0xffffffff)
    return bytes(data)


class Machine:
    """只映射 SRAM；SPL1 區域在開始複製時可撤除，檢查隱藏相依。"""

    def __init__(self, raw, symbols, context=None, pointer=0x49000, state=0x3cd,
                 sctlr=0, revoke=True):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.uc.mem_map(0x20000, 0x38000)
        self.uc.mem_write(0x20000, b"\xa5" * 0x38000)
        self.uc.mem_write(0x30000, raw)
        self.context = context or struct.pack("<8I", 0x31505553, 4, 64, 0x06180001,
                                             0x12345678, 0xffffffff, len(raw), 0x18000) + bytes.fromhex(builder.digest(raw))
        self.uc.mem_write(0x49000, self.context)
        self.uc.reg_write(UC_ARM64_REG_PSTATE, state)
        self.sctlr = sctlr
        self.uc.cpr_write(3, 6, 1, 0, 0, sctlr & ~1)
        self.uc.cpr_write(3, 6, 12, 0, 0, 0x20800)
        self.uc.reg_write(UC_ARM64_REG_SP, 0xdead0000)
        self.uc.reg_write(UC_ARM64_REG_X18, 0xbad00000)
        self.uc.reg_write(UC_ARM64_REG_X0, pointer)
        self.symbols, self.revoke = symbols, revoke
        self.copying = self.arrived = self.stopped = False
        self.writes, self.reads = [], []
        self.uc.hook_add(UC_HOOK_CODE, self.code)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.read)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write)

    def code(self, uc, address, size, user):
        if address == 0x20060:
            self.arrived = True
            uc.emu_stop()
            return
        if not (0x30000 <= address < 0x32000 or 0x40000 <= address < 0x40800):
            raise AssertionError(f"執行位置依賴 SPL1 或資料區：{address:#x}")
        # MMU 反例僅替代 SCTLR 的讀值；不建立頁表，也不宣稱測過位址轉譯。
        if self.sctlr & 1 and bytes(uc.mem_read(address, 4)) == struct.pack("<I", 0xd53e1001):
            uc.reg_write(UC_ARM64_REG_X1, self.sctlr)
            uc.reg_write(UC_ARM64_REG_PC, address + 4)
        if address == self.symbols["bridge_stop"]:
            self.stopped = True
            uc.emu_stop()
        if address == self.symbols["bridge_copy_start"]:
            self.copying = True
            if self.revoke:
                uc.mem_unmap(0x48000, 0x10000)

    def read(self, uc, access, address, size, value, user):
        self.reads.append((address, size, self.copying))
        if 0x30000 <= address and address + size <= 0x48000:
            return
        if not self.copying and 0x48010 <= address and address + size <= 0x4fff0:
            return
        raise AssertionError(f"不允許的讀取：{address:#x}/{size}")

    def write(self, uc, access, address, size, value, user):
        self.writes.append((address, size))
        if self.copying and 0x20000 <= address and address + size <= 0x2a000:
            return
        if not self.copying and 0x3f000 <= address and address + size <= 0x3f040:
            return
        raise AssertionError(f"不允許的寫入：{address:#x}/{size}")

    def run(self, entry=0x30000):
        self.uc.emu_start(entry, 0, timeout=3_000_000, count=100_000)
        if not (self.arrived or self.stopped):
            raise AssertionError(f"模型未到終點：PC={self.uc.reg_read(UC_ARM64_REG_PC):#x}")
        return self


class BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup = builder.base.read_regular_file(builder.N2_BACKUP, 4 * 1024 * 1024)
        if builder.digest(cls.backup) != builder.N2_BACKUP_SHA256:
            raise AssertionError("指定 N2 備份 SHA 不符；不改用合成 SPL 充數")
        cls.spl = cls.backup[8192:49152]
        cls.temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-boot-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.input = cls.directory / "original.bin"
        builder.base.write_new_regular_file(cls.input, cls.spl)
        cls.output = cls.directory / "build"
        cls.report = builder.build(cls.input, builder.N2_SPL_SHA256, cls.output)
        cls.raw = (cls.output / "bridge.bin").read_bytes()
        cls.symbols = cls.report["symbols"]

    @classmethod
    def tearDownClass(cls):
        if builder.base.read_regular_file(builder.N2_BACKUP, 4 * 1024 * 1024) != cls.backup:
            raise AssertionError("原始備份在測試期間變動")

    def reject(self, offset, value, message, checksum=True):
        data = altered(self.spl, offset, value, checksum)
        with self.assertRaisesRegex(builder.BuildError, message):
            builder.validate_spl(data, builder.digest(data))

    def test_01_real_backup_and_fixed_fit(self):
        data = builder.validate_spl(self.spl, builder.N2_SPL_SHA256)
        self.assertTrue(data["known_diagnostic"])
        self.assertEqual(data["bytes"], 40960)
        self.assertEqual(self.backup[49152:49156], b"\xd0\x0d\xfe\xed")
        self.assertIn("D1", self.report["limitation"])

    def test_02_exact_length_required(self):
        for blob in (b"", self.spl[:-1], self.spl + b"\0", self.spl[:32768]):
            with self.subTest(size=len(blob)), self.assertRaisesRegex(builder.BuildError, "40 KiB"):
                builder.validate_spl(blob, builder.digest(blob))

    def test_03_explicit_hash_required(self):
        for sha in (None, "", "abc", "g" * 64, "0" * 64):
            with self.subTest(sha=sha), self.assertRaisesRegex(builder.BuildError, "SHA-256"):
                builder.validate_spl(self.spl, sha)

    def test_04_magic_rejected(self):
        self.reject(4, b"eGON.BT1", "magic")

    def test_05_header_branch_rejected(self):
        self.reject(0, struct.pack("<I", 0xea000000), "96 位元組")

    def test_06_header_length_rejected(self):
        self.reject(16, struct.pack("<I", 32768), "length")

    def test_07_header_fields_rejected(self):
        for offset, value in ((20, b"SPL\x03"), (24, b"\1"), (28, b"\1"),
                              (32, b"\0"), (36, b"\1"), (40, b"\x10"),
                              (44, b"X"), (95, b"\1")):
            with self.subTest(offset=offset):
                self.reject(offset, value, "標頭|欄位|板型|字串")

    def test_08_checksum_rejected(self):
        self.reject(40959, bytes((self.spl[-1] ^ 1,)), "checksum", checksum=False)

    def test_09_entry_and_reset_rejected(self):
        for offset, word in ((96, 0), (100, 0), (100, 0x17ffffff), (100, 0x14010000)):
            with self.subTest(offset=offset, word=word):
                self.reject(offset, struct.pack("<I", word), "入口|分支")

    def test_10_cli_requires_sha_and_new_output(self):
        self.assertIn("用法：", builder.build_parser().format_help())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            builder.build_parser().parse_args(["--spl", str(self.input), "--output", "new"])
        with self.assertRaisesRegex(builder.BuildError, "已存在"):
            builder.build(self.input, builder.N2_SPL_SHA256, self.output)

    def test_11_symlink_and_device_rejected(self):
        link = self.directory / "input-link"
        link.symlink_to(self.input)
        with self.assertRaisesRegex(builder.BuildError, "符號連結"):
            builder.build(link, builder.N2_SPL_SHA256, self.directory / "unused")
        with self.assertRaises(builder.base.PackageError):
            builder.base.read_regular_file(Path("/dev/null"), builder.SPL_BYTES)
        self.assertFalse((self.directory / "unused").exists())

    def test_12_elf_raw_and_package_contract(self):
        packaged = (self.output / "bridge-package.bin").read_bytes()
        parsed = builder.package.parse_package(packaged)
        self.assertEqual((parsed["version"], parsed["kind"], parsed["runtime_size"]), (3, 4, 0x18000))
        self.assertEqual(packaged[512:512 + len(self.raw)], self.raw)
        self.assertEqual(self.raw[0x2000:0xc000], self.spl)
        self.assertTrue(self.report["inputs_unchanged"])
        self.assertEqual(json.loads((self.output / "build-report.json").read_text()), self.report)

    def test_13_copy_exact_range_and_handoff(self):
        machine = Machine(self.raw, self.symbols).run()
        self.assertTrue(machine.arrived)
        self.assertEqual(bytes(machine.uc.mem_read(0x20000, 40960)), self.spl)
        self.assertEqual(bytes(machine.uc.mem_read(0x2a000, 0x6000)), b"\xa5" * 0x6000)
        writes = [(address, size) for address, size in machine.writes if address < 0x30000]
        self.assertEqual(sum(size for _, size in writes), 40960)
        self.assertEqual((writes[0][0], writes[-1][0] + writes[-1][1]), (0x20000, 0x2a000))

    def test_14_owns_context_stack_vectors_without_spl1(self):
        machine = Machine(self.raw, self.symbols).run()
        self.assertTrue(machine.arrived)
        self.assertEqual(bytes(machine.uc.mem_read(0x3f000, 64)), machine.context)
        self.assertEqual(machine.uc.reg_read(UC_ARM64_REG_SP), 0x47ff0)
        self.assertEqual(machine.uc.reg_read(UC_ARM64_REG_X18), 0)
        self.assertEqual(machine.uc.reg_read(UC_ARM64_REG_X0), 0)
        self.assertEqual(machine.uc.cpr_read(3, 6, 12, 0, 0), 0x40000)
        self.assertTrue(all(address >= 0x30000 for address, _, copying in machine.reads if copying))

    def test_15_invalid_context_pointer_and_fields_stop_before_copy(self):
        for pointer in (0, 0x48008, 0x49001, 0x4ffb8, 0x40000000):
            with self.subTest(pointer=pointer):
                machine = Machine(self.raw, self.symbols, pointer=pointer).run()
                self.assertTrue(machine.stopped)
                self.assertFalse(machine.writes)
        context = Machine(self.raw, self.symbols).context
        for offset, value in ((0, 0), (4, 1), (8, 63), (12, 0), (20, 5), (24, 1), (28, 0x17000)):
            with self.subTest(offset=offset):
                bad = bytearray(context)
                struct.pack_into("<I", bad, offset, value)
                machine = Machine(self.raw, self.symbols, context=bytes(bad)).run()
                self.assertTrue(machine.stopped)
                self.assertFalse(machine.writes)

    def test_16_invalid_el_or_cache_state_stops(self):
        for state, sctlr in ((0x3c5, 0), (0x3c9, 0), (0x3cd, 1), (0x3cd, 4), (0x3cd, 0x1000)):
            with self.subTest(state=state, sctlr=sctlr):
                machine = Machine(self.raw, self.symbols, state=state, sctlr=sctlr).run()
                self.assertTrue(machine.stopped)
                self.assertFalse(machine.writes)

    def test_17_runtime_spl_corruption_stops_before_overwrite(self):
        raw = bytearray(self.raw)
        raw[0xbfff] ^= 1
        machine = Machine(bytes(raw), self.symbols).run()
        self.assertTrue(machine.stopped)
        self.assertFalse(machine.copying)
        self.assertEqual(bytes(machine.uc.mem_read(0x20000, 40960)), b"\xa5" * 40960)

    def test_18_all_vectors_stop_without_old_stack(self):
        for index in range(16):
            with self.subTest(vector=index):
                machine = Machine(self.raw, self.symbols).run(0x40000 + index * 128)
                self.assertTrue(machine.stopped)
                self.assertEqual(machine.uc.reg_read(UC_ARM64_REG_SP), 0x47ff0)
                self.assertFalse(machine.writes)


if __name__ == "__main__":
    unittest.main()
