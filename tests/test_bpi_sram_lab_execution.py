#!/usr/bin/env python3
"""執行第三版固定入口，驗證型別交接與槽位；不模擬實體 SD 或 DDR。"""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from unicorn import UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X1

import test_bpi_sram_core as core
from test_bpi_sram_execution import Machine, frames, package

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_sram_ddr_package as ddr
from tools import bpi_sram_lab_package as lab


class LabCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="bpi-lab-core-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.compiler = ["cc"]
        cls.environment = dict(os.environ)
        output = cls.directory / "lab.so"
        core.CoreTests.compile.__func__(cls, output, [
            "-shared", "-fPIC", "-DCONFIG_BPI_SRAM_LAB_V3", "-DCONFIG_BPI_SRAM_DDR_V2",
        ])
        cls.lib = ctypes.CDLL(str(output))
        cls.lib.sup_header.argtypes = [ctypes.POINTER(core.U8), ctypes.POINTER(core.Image)]
        cls.lib.sup_slot.argtypes = [core.U32, ctypes.POINTER(core.U32)]

    def test_version_and_kind_pairs_are_not_interchangeable(self):
        for version in range(5):
            for kind in range(6):
                with self.subTest(版本=version, 型別=kind):
                    header = core.header_for()
                    struct.pack_into("<I", header, 8, version)
                    struct.pack_into("<I", header, 36, kind)
                    core.seal(header)
                    out = core.Image()
                    result = self.lib.sup_header((core.U8 * 512).from_buffer_copy(header), ctypes.byref(out))
                    self.assertEqual(result == 0, (version, kind) in ((1, 1), (2, 2), (3, 3), (3, 4)))
                    if result == 0:
                        self.assertEqual(out.kind, kind)

    def test_all_slots_fit_before_original_partition(self):
        for slot, expected in enumerate((6144, 6400, 6656, 6912, 7168)):
            address = core.U32()
            self.assertEqual(self.lib.sup_slot(slot, ctypes.byref(address)), 0)
            self.assertEqual(address.value, expected)
            self.assertLessEqual(address.value + 256, 8192)
        self.assertEqual(self.lib.sup_slot(5, ctypes.byref(core.U32())), -1)


class LabExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = Path(os.environ["BPI_SRAM_BUILD"])
        if "CONFIG_BPI_SRAM_LAB_V3=y" not in (cls.build / "spl1.config").read_text().splitlines():
            raise RuntimeError("必須指定第三版實驗入口產物")

    def test_exact_capabilities(self):
        output = Machine(self.build, b"I 99\n").run()
        self.assertIn(b"abi=3", output)
        self.assertIn(b"capabilities=uart-ram,sd-read,smoke-run,ddr-run,update-run,boot-run", output)

    def test_smoke_compatibility_executes(self):
        data = package.build_package((self.build / "spl2-smoke.bin").read_bytes(), 0x18000)
        output = Machine(self.build, f"U 42 {len(data)}\n".encode(), frames(data), b"R 42\n", b"result=pass").run()
        self.assertIn(b"result=0 kind=1", output)
        self.assertIn(b"el=3 ddr=off result=pass", output)

    def test_all_new_context_kinds_and_hashes_before_execution(self):
        for kind in (2, 3, 4):
            with self.subTest(型別=kind):
                payload = b"\x5a" * 512
                data = ddr.build_package(payload, 0x18000) if kind == 2 else lab.build_package(payload, 0x18000, kind)
                machine = Machine(self.build, f"U 42 {len(data)}\n".encode(), frames(data), b"R 42\n")
                observed = []

                def capture(uc, address, size, user):
                    observed.append(bytes(uc.mem_read(uc.reg_read(UC_ARM64_REG_X1), 64)))
                    machine.stopped = True
                    uc.emu_stop()

                entry = machine.symbols["sup_enter"]
                machine.uc.hook_add(UC_HOOK_CODE, capture, begin=entry, end=entry)
                output = machine.run()
                self.assertEqual(len(observed), 1)
                values = struct.unpack("<8I32s", observed[0])
                self.assertEqual(values[:8], (0x31505553, kind, 64, 0x06180001, 42, 0xffffffff, 512, 0x18000))
                self.assertEqual(values[-1], hashlib.sha256(payload).digest())
                self.assertIn(f"result=0 kind={kind}".encode(), output)

    def test_corrupt_new_payload_never_hands_off(self):
        for kind in (3, 4):
            with self.subTest(型別=kind):
                data = bytearray(lab.build_package(b"\x5a" * 512, 0x18000, kind))
                data[512] ^= 1
                output = Machine(self.build, f"U 42 {len(data)}\n".encode(), frames(data), b"R 42\nI 99\n").run()
                self.assertIn(b"result=-1 kind=0 sha256=none", output)
                self.assertNotIn(b"event=handoff", output)

    def test_final_boot_package_moves_verified_spl_and_reaches_entry(self):
        build = Path(os.environ["BPI_BOOT_BUILD"])
        data = (build / "bridge-package.bin").read_bytes()
        metadata = lab.parse_package(data)
        self.assertEqual(metadata["kind"], 4)
        report = json.loads((build / "build-report.json").read_text())
        symbols = report["symbols"]
        spl = (build / "inputs/spl.bin").read_bytes()

        class BootMachine(Machine):
            def memory_write(machine, uc, access, address, size, value, user):
                if 0x20000 <= address and address + size <= 0x2a000:
                    self.assertTrue(symbols["bridge_copy_start"] <= uc.reg_read(UC_ARM64_REG_PC) < symbols["bridge_handoff"])
                else:
                    super().memory_write(uc, access, address, size, value, user)

        machine = BootMachine(self.build, f"U 42 {len(data)}\n".encode(), frames(data), b"R 42\n")

        def arrive(uc, address, size, user):
            machine.stopped = True
            uc.emu_stop()

        machine.uc.hook_add(UC_HOOK_CODE, arrive, begin=0x20060, end=0x20060)
        output = machine.run()
        self.assertIn(b"event=handoff nonce=42", output)
        self.assertEqual(bytes(machine.uc.mem_read(0x20000, len(spl))), spl)
        self.assertEqual(machine.uc.reg_read(UC_ARM64_REG_PC), 0x20060)


if __name__ == "__main__":
    unittest.main()
