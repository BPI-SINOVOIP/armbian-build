#!/usr/bin/env python3
"""執行新版 SPL1 的 AArch64 產物，驗證型別、雜湊及交接；不接觸硬體。"""

import hashlib
from collections import deque
import json
import os
from pathlib import Path
import pty
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tty
import unittest

from unicorn import UC_HOOK_CODE, UcError, UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_PC, UC_ARM64_REG_LR, UC_ARM64_REG_SP
from elftools.elf.elffile import ELFFile
from test_bpi_sram_execution import Machine, frames, package
import test_bpi_sram_execution as legacy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_sram_ddr_package as ddr


class DdrMachine(Machine):
    """只替代 SRAM 負載的 UART／計數器；DDR MMIO 仍未映射，誤啟動會失敗。"""

    def __init__(self, build, payload_build, request=b"", nonce=42, transform=None):
        payload = (payload_build / "spl2-ddr.bin").read_bytes()
        if transform is not None:
            payload = transform(payload)
        data = ddr.build_package(payload, 0x18000)
        stop = b"reason=pmic_geometry_unverified" if request else b"event=ddr-ready"
        super().__init__(build, f"U {nonce} {len(data)}\n".encode(), frames(data),
                         f"R {nonce}\n".encode(), stop)
        self.request = request
        self.ddr_clock = 0
        with (payload_build / "spl2-ddr.elf").open("rb") as stream:
            elf = ELFFile(stream)
            self.ddr_symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols() if s.name}
        for name in ("get_tbclk", "get_ticks", "serial_tstc", "serial_getc"):
            address = self.ddr_symbols[name]
            self.uc.hook_add(UC_HOOK_CODE, self.ddr_service, name, begin=address, end=address)

    def emit(self, data):
        super().emit(data)
        if self.request and self.output.endswith(b"preflight=unverified\r\n"):
            self.rx.extend(self.request)
            self.request = b""

    def ddr_service(self, uc, address, size, name):
        if name == "get_tbclk":
            result = 24000000
        elif name == "get_ticks":
            self.ddr_clock += 24000
            result = self.ddr_clock
        elif name == "serial_tstc":
            result = bool(self.rx)
        else:
            if not self.rx:
                raise AssertionError("負載在 UART 未就緒時讀取")
            result = self.rx.popleft()
        uc.reg_write(UC_ARM64_REG_X0, result)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))


class V2ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = Path(os.environ["BPI_SRAM_BUILD"])
        config = (cls.build / "spl1.config").read_text().splitlines()
        if "CONFIG_BPI_SRAM_DDR_V2=y" not in config:
            raise RuntimeError("此測試必須指定明確啟用 DDR V2 的建置，不可用舊產物代替")
        cls.smoke = (cls.build / "spl2-smoke.bin").read_bytes()

    def transfer(self, data, after=b"I 99\n", stop=b"event=info nonce=99"):
        return Machine(self.build, f"U 42 {len(data)}\n".encode(), frames(data), after, stop)

    def test_control_abi_and_capabilities(self):
        output = Machine(self.build, b"I 99\n").run()
        self.assertIn(b"abi=2", output)
        self.assertIn(b"capabilities=uart-ram,sd-read,smoke-run,ddr-run", output)

    def test_legacy_smoke_still_executes_with_abi_one(self):
        data = package.build_package(self.smoke, 0x18000)
        machine = self.transfer(data, b"R 42\n", b"result=pass")
        output = machine.run()
        digest = hashlib.sha256(self.smoke).hexdigest().encode()
        self.assertIn(b"result=0 kind=1 sha256=" + digest, output)
        self.assertIn(b"BPI-SPL2 event=smoke nonce_hex=0000002a", output)
        self.assertIn(b"el=3 ddr=off result=pass", output)

    def test_ddr_context_type_and_digest_before_jump(self):
        # 只測載入契約，於 sup_enter 前停止；合成內容絕不被當成 DDR 演算法執行。
        image = b"\x5a" * 512
        data = ddr.build_package(image, 0x18000)
        machine = self.transfer(data, b"R 42\n")
        observed = []

        def capture(uc, address, size, user):
            context = uc.reg_read(UC_ARM64_REG_X1)
            observed.append(bytes(uc.mem_read(context, 64)))
            machine.stopped = True
            uc.emu_stop()

        entry = machine.symbols["sup_enter"]
        machine.uc.hook_add(UC_HOOK_CODE, capture, begin=entry, end=entry)
        output = machine.run()
        self.assertEqual(len(observed), 1)
        values = struct.unpack("<8I32s", observed[0])
        self.assertEqual(values[:8], (0x31505553, 2, 64, 0x06180001, 42, 0xffffffff, 512, 0x18000))
        self.assertEqual(values[-1], hashlib.sha256(image).digest())
        self.assertIn(b"result=0 kind=2 sha256=" + hashlib.sha256(image).hexdigest().encode(), output)
        self.assertIn(b"event=handoff nonce=42", output)

    def test_corruption_clears_type_and_prevents_run(self):
        data = bytearray(ddr.build_package(b"\x5a" * 512, 0x18000))
        data[512] ^= 1
        output = self.transfer(bytes(data), b"R 42\nI 99\n").run()
        self.assertIn(b"result=-1 kind=0 sha256=none", output)
        self.assertNotIn(b"event=handoff", output)

    def test_no_payload_returns_by_total_deadline(self):
        machine = Machine(self.build, b"U 42 1536\n", b"", b"R 42\nI 99\n")
        output = machine.run()
        self.assertIn(b"result=-1 kind=0 sha256=none", output)
        self.assertNotIn(b"event=handoff", output)
        self.assertLess(machine.clock, 15000)

    def test_real_ddr_entry_reaches_ready_without_dram(self):
        payload = Path(os.environ["BPI_DDR_BUILD_DIR"])
        machine = DdrMachine(self.build, payload)
        output = machine.run()
        self.assertIn(b"BPI-SPL2 event=ddr-ready nonce_hex=0000002a abi=2", output)
        self.assertIn(b"kind=2 preflight=unverified", output)
        sp = machine.uc.reg_read(UC_ARM64_REG_SP)
        self.assertTrue(0x40010 <= sp < 0x47800 and sp % 16 == 0)
        self.assertEqual(machine.uc.cpr_read(3, 6, 12, 0, 0), machine.ddr_symbols["ddr_vectors"])

    def test_real_parameter_request_blocks_before_unverified_pmic(self):
        payload = Path(os.environ["BPI_DDR_BUILD_DIR"])
        request = (b"R nonce_hex=0000002a id=1 clk=480 dx_odt=0x07070707 dx_dri=0x0e0e0e0e "
                   b"ca_dri=0x0d0d odt_en=0xaaaaeeee tpr0=0 tpr2=0 tpr6=0x3a808080 "
                   b"tpr10=0x402f6663 tpr11=0x25252523 tpr12=0x110f0f10 level=0 passes=1 window=1\n")
        output = DdrMachine(self.build, payload, request).run()
        self.assertIn(b"event=ddr-params nonce_hex=0000002a", output)
        self.assertIn(b"result=blocked reason=pmic_geometry_unverified ddr=off tested_bytes=0", output)
        self.assertNotIn(b"event=ddr-start", output)

    def test_zero_nonce_reaches_real_ddr_ready(self):
        payload = Path(os.environ["BPI_DDR_BUILD_DIR"])
        output = DdrMachine(self.build, payload, nonce=0).run()
        self.assertIn(b"BPI-SPL2 event=ddr-ready nonce_hex=00000000 abi=2", output)

    def test_gate_bypass_mutation_reaches_forbidden_memory_and_fails(self):
        payload = Path(os.environ["BPI_DDR_BUILD_DIR"])
        with (payload / "spl2-ddr.elf").open("rb") as stream:
            symbols = {s.name: s["st_value"] for s in ELFFile(stream).get_section_by_name(".symtab").iter_symbols()}

        def bypass(data):
            result = bytearray(data)
            changed = 0
            for offset in range(0, len(result) - 3, 4):
                word = struct.unpack_from("<I", result, offset)[0]
                if word & 0xfc000000 != 0x94000000:
                    continue
                immediate = word & 0x03ffffff
                if immediate & 0x02000000:
                    immediate -= 0x04000000
                address = 0x30000 + offset
                if address + 4 * immediate != symbols["ddr_preflight"]:
                    continue
                delta = symbols["mctl_core_init"] - address
                self.assertEqual(delta % 4, 0)
                struct.pack_into("<I", result, offset, 0x94000000 | ((delta // 4) & 0x03ffffff))
                changed += 1
            self.assertEqual(changed, 1, "必須找到唯一真實前置檢查呼叫，不能略過變異測試")
            return bytes(result)

        request = (b"R nonce_hex=0000002a id=1 clk=480 dx_odt=0x07070707 dx_dri=0x0e0e0e0e "
                   b"ca_dri=0x0d0d odt_en=0xaaaaeeee tpr0=0 tpr2=0 tpr6=0x3a808080 "
                   b"tpr10=0x402f6663 tpr11=0x25252523 tpr12=0x110f0f10 level=0 passes=1 window=1\n")
        # 重新計算封包雜湊，確保不是被傳輸校驗擋下；必須由實際存取守門發現繞過。
        class MutationMachine(DdrMachine):
            def invalid_memory(machine, uc, access, address, size, value, user):
                machine.refused_accesses.append((address, size))
                return False

        machine = MutationMachine(self.build, payload, request, transform=bypass)
        machine.refused_accesses = []
        with self.assertRaises(UcError) as caught:
            machine.run()
        self.assertIn(caught.exception.errno, (UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED))
        self.assertTrue(any(0x03000000 <= address < 0x08000000 for address, size in machine.refused_accesses),
                        machine.refused_accesses)
        self.assertNotIn(b"reason=pmic_geometry_unverified", machine.output)


class V2LegacyRegressionTests(legacy.ExecutionTests):
    """重跑既有真實接收案例，只將主機整合測試換成新版 DDR 協定。"""

    def test_real_host_tool_and_sx_over_pseudoterminal(self):
        self.assertIsNotNone(shutil.which("sx"), "整合測試必須有 lrzsz")
        payload_build = Path(os.environ["BPI_DDR_BUILD_DIR"])
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        errors = []

        class TerminalDdrMachine(DdrMachine):
            def emit(machine, data):
                machine.output.extend(data)
                os.write(master, data)
                if machine.output.endswith(b"preflight=unverified\r\n"):
                    machine.stopped = True
                    machine.uc.emu_stop()

            def service(machine, uc, address, size, name):
                if name == "tstc":
                    try:
                        machine.rx.extend(os.read(master, 4096))
                    except BlockingIOError:
                        time.sleep(0.0001)
                if name == "get_timer":
                    arg = uc.reg_read(UC_ARM64_REG_X0)
                    uc.reg_write(UC_ARM64_REG_X0, int(time.monotonic() * 1000) - arg)
                    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
                    return
                if name == "udelay":
                    time.sleep(uc.reg_read(UC_ARM64_REG_X0) / 1_000_000)
                super().service(uc, address, size, name)

        machine = TerminalDdrMachine(self.build, payload_build)
        machine.rx = deque()
        machine.wire = machine.after_load = b""

        def run():
            try:
                machine.run()
            except BaseException as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=run)
        try:
            with tempfile.TemporaryDirectory(prefix="bpi-ddr-pty-") as directory:
                packaged = Path(directory) / "ddr.sram"
                packaged.write_bytes(ddr.build_package((payload_build / "spl2-ddr.bin").read_bytes(), 0x18000))
                worker.start()
                result = subprocess.run([
                    sys.executable, "-B", str(legacy.ROOT / "tools/bpi_sram_ddr_uart.py"), "upload",
                    "--port", os.ttyname(slave), "--input", str(packaged), "--run",
                ], capture_output=True, timeout=20)
                worker.join(timeout=16)
                self.assertFalse(worker.is_alive(), "新版執行模型未停止")
                self.assertEqual(errors, [])
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                data = json.loads(result.stdout)
                self.assertEqual(data["event"], "ddr-ready")
                self.assertFalse(data["ddr_result_verified"])
        finally:
            machine.uc.emu_stop()
            if worker.ident is not None:
                worker.join(timeout=16)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
