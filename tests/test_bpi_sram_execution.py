#!/usr/bin/env python3
"""執行真實 AArch64 產物；UART、時基與 MMC 失敗由模型替代，不模擬 H618 硬體。"""

from collections import deque
import binascii
import importlib.util
import os
import json
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

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_INVALID
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_X0, UC_ARM64_REG_PSTATE


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package", ROOT / "tools/bpi_sram_package.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def frames(data):
    """產生 128 位元組 XMODEM／CRC 封包，最後附 EOT。"""
    result = bytearray()
    for index, start in enumerate(range(0, len(data), 128), 1):
        block = data[start:start + 128]
        if len(block) != 128:
            raise ValueError("模型輸入必須對齊")
        seq = index & 255
        result.extend(bytes((1, seq, 255 - seq)) + block + struct.pack(">H", binascii.crc_hqx(block, 0)))
    return bytes(result) + b"\x04"


class Machine:
    def __init__(self, build, commands, wire=b"", after_load=b"", stop=b"event=info nonce=99"):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.uc.mem_map(0x20000, 0x38000)
        self.uc.mem_map(0x05000000, 0x1000)
        self.symbols = {}
        with (build / "spl1.elf").open("rb") as stream:
            elf = ELFFile(stream)
            for sym in elf.get_section_by_name(".symtab").iter_symbols():
                if sym.name:
                    self.symbols[sym.name] = sym["st_value"]
        # 指令與常數只能來自最終可燒錄檔；ELF 只提供測試定位符號。
        self.uc.mem_write(0x20000, (build / "spl1-egon.bin").read_bytes())
        self.rx, self.output = deque(commands), bytearray()
        self.wire, self.after_load, self.stop = wire, after_load, stop
        self.sent, self.loaded, self.stopped = False, False, False
        self.clock, self.steps, self.min_sp = 0, 0, 0x55e00
        self.entry = self.symbols["bpi_supervisor_run"]
        self.allowed_registers = set()
        self.uc.reg_write(UC_ARM64_REG_PSTATE, 0x3cd)
        self.uc.cpr_write(3, 6, 1, 0, 0, 0)
        self.uc.reg_write(UC_ARM64_REG_SP, 0x55e00)
        for name in ("get_timer", "udelay", "getchar", "tstc", "putc", "puts", "schedule", "mmc_initialize"):
            address = self.symbols[name]
            self.uc.hook_add(UC_HOOK_CODE, self.service, name, begin=address, end=address)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.uart_read, begin=0x05000000, end=0x05000fff)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.memory_write)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self.invalid_memory)

    def invalid_memory(self, uc, access, address, size, value, user):
        raise AssertionError(f"未映射存取 {address:#x}/{size}，PC={uc.reg_read(UC_ARM64_REG_PC):#x}，SP={uc.reg_read(UC_ARM64_REG_SP):#x}")

    def emit(self, data):
        for value in data:
            self.output.append(value)
            if value == 67 and not self.sent and b"event=loading" in self.output:
                self.sent = True
                self.rx.extend(self.wire)
            if value == 10:
                line = bytes(self.output).splitlines()[-1]
                if b"event=loaded" in line and not self.loaded:
                    self.loaded = True
                    self.rx.extend(self.after_load)
                if self.stop in line:
                    self.stopped = True
                    self.uc.emu_stop()

    def service(self, uc, address, size, name):
        arg = uc.reg_read(UC_ARM64_REG_X0)
        self.min_sp = min(self.min_sp, uc.reg_read(UC_ARM64_REG_SP))
        result = 0
        if name == "get_timer":
            self.clock += 1 if not self.rx else 0
            result = self.clock - arg
        elif name == "udelay":
            self.clock += (arg + 999) // 1000
        elif name == "tstc":
            result = bool(self.rx)
        elif name == "getchar":
            if not self.rx:
                raise AssertionError("韌體在未就緒時讀取 UART")
            result = self.rx.popleft()
        elif name == "putc":
            self.emit(bytes((arg & 255,)))
        elif name == "puts":
            text = bytearray()
            for offset in range(4096):
                value = bytes(uc.mem_read(arg + offset, 1))
                if value == b"\0":
                    break
                text.extend(value)
            else:
                raise AssertionError("字串沒有結尾")
            self.emit(text)
        elif name == "mmc_initialize":
            result = (1 << 64) - 1
        uc.reg_write(UC_ARM64_REG_X0, result & ((1 << 64) - 1))
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def uart_read(self, uc, access, address, size, value, user):
        if address != 0x05000014 or size != 1:
            raise AssertionError(f"未建模的 UART 讀取 {address:#x}/{size}")
        uc.mem_write(address, b"\x20")

    def memory_write(self, uc, access, address, size, value, user):
        if address == 0x05000000 and size == 1:
            self.emit(bytes((value & 255,)))
        elif address in self.allowed_registers and size == 4:
            pass
        elif not (0x30000 <= address and address + size <= 0x58000):
            raise AssertionError(f"非預期寫入 {address:#x}/{size}")

    def run(self):
        # 從已完成最低初始化的命令入口開始；不宣稱測到 BootROM、PLL 或實際 MMC。
        self.uc.emu_start(self.entry, 0, timeout=15_000_000, count=12_000_000)
        if not self.stopped:
            raise AssertionError(f"執行未完成，PC={self.uc.reg_read(UC_ARM64_REG_PC):#x}，輸出={bytes(self.output)[-800:]!r}")
        if self.min_sp <= 0x50010:
            raise AssertionError("模型觀察到 SPL1 堆疊超界")
        return bytes(self.output)


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = Path(os.environ["BPI_SRAM_BUILD"])
        cls.payload = (cls.build / "spl2-smoke.bin").read_bytes()
        cls.good = package.build_package(cls.payload, 0x18000)

    def transfer(self, data=None, wire=None, after=b"I 99\n", stop=b"event=info nonce=99"):
        data = self.good if data is None else data
        m = Machine(self.build, f"I 1\nU 42 {len(data)}\n".encode(),
                    frames(data) if wire is None else wire, after, stop)
        return m, m.run()

    def test_info_and_malformed_command_recovery(self):
        output = Machine(self.build, b"X 1\nI 4294967296\nI 99\n").run()
        self.assertEqual(output.count(b"event=reject"), 2)
        self.assertIn(b"board=06180001", output)

    def test_aarch64_reset_allocates_sram_stack_and_clears_bss(self):
        m = Machine(self.build, b"I 99\n")
        m.entry = m.symbols["reset"]
        m.uc.mem_write(m.symbols["__bss_start"], b"\xa5" * (
            m.symbols["__bss_end"] - m.symbols["__bss_start"]))
        # 兩組 GPIO 電源設定寄存器以零值模型取代；不證明真實供電或 pinmux。
        for base in (0x0300b000, 0x07022000):
            m.uc.mem_map(base, 0x1000)
            m.allowed_registers.add(base + 0x340)
        for name in ("clock_init_uart", "timer_init", "sunxi_gpio_set_cfgpin",
                     "sunxi_gpio_set_pull", "preloader_console_init"):
            addr = m.symbols[name]
            m.uc.hook_add(UC_HOOK_CODE, m.service, name, begin=addr, end=addr)
        output = m.run()
        self.assertIn(b"event=info nonce=99", output)
        self.assertGreater(m.min_sp, 0x50010)
        self.assertEqual(m.uc.cpr_read(3, 6, 12, 0, 0), m.symbols["sup_vectors"])
        self.assertEqual(m.uc.reg_read(UC_ARM64_REG_PSTATE) & 0x3c0, 0x3c0)

    def test_sd_initialization_failure_returns_to_uart(self):
        output = Machine(self.build, b"S 4 0\nI 99\n").run()
        self.assertIn(b"event=loaded nonce=4 result=-1", output)

    def test_invalid_slot_never_requires_mmc(self):
        output = Machine(self.build, b"S 4 5\nI 99\n").run()
        self.assertIn(b"event=loaded nonce=4 result=-1", output)

    def test_xmodem_load_and_real_spl2_handoff(self):
        m, output = self.transfer(after=b"R 42\n", stop=b"result=pass")
        self.assertIn(b"event=loaded nonce=42 result=0", output)
        self.assertIn(b"BPI-SPL2 event=smoke nonce_hex=0000002a", output)
        self.assertIn(b"el=3 ddr=off result=pass", output)
        self.assertEqual(bytes(m.uc.mem_read(0x30000, len(self.payload))), self.payload)

    def test_wrong_nonce_cannot_run(self):
        _, output = self.transfer(after=b"R 43\nI 99\n")
        self.assertIn(b"event=reject", output)
        self.assertNotIn(b"event=handoff", output)

    def test_header_crc_failure(self):
        bad = bytearray(self.good)
        bad[508] ^= 1
        _, output = self.transfer(bytes(bad))
        self.assertIn(b"event=loaded nonce=42 result=-1", output)

    def test_payload_hash_failure(self):
        bad = bytearray(self.good)
        bad[512] ^= 1
        _, output = self.transfer(bytes(bad))
        self.assertIn(b"event=loaded nonce=42 result=-1", output)

    def test_padding_failure(self):
        bad = bytearray(self.good)
        bad[-1] = 1
        _, output = self.transfer(bytes(bad))
        self.assertIn(b"event=loaded nonce=42 result=-1", output)

    def test_binary_sub_bytes_are_not_trimmed(self):
        data = package.build_package(b"\x1a" * 384 + b"\x55" * 128, 0x18000)
        m, output = self.transfer(data)
        self.assertIn(b"event=loaded nonce=42 result=0", output)
        self.assertEqual(bytes(m.uc.mem_read(0x30000, 384)), b"\x1a" * 384)

    def test_early_eot_rejected(self):
        _, output = self.transfer(wire=frames(self.good[:512]))
        self.assertIn(b"event=loaded nonce=42 result=-1", output)

    def test_excess_data_after_valid_prefix_is_rejected(self):
        _, output = self.transfer(wire=frames(self.good + b"\0" * 128), after=b"R 42\nI 99\n")
        self.assertIn(b"event=loaded nonce=42 result=-1", output)
        self.assertNotIn(b"event=handoff", output)

    def test_lost_eot_returns_to_uart(self):
        _, output = self.transfer(wire=frames(self.good)[:-1])
        self.assertIn(b"event=loaded nonce=42 result=-1", output)

    def test_maximum_payload_keeps_guard_intact(self):
        data = bytes(range(256)) * 382
        self.assertEqual(len(data), 97792)
        m, output = self.transfer(package.build_package(data, 0x18000))
        self.assertIn(b"event=loaded nonce=42 result=0", output)
        self.assertEqual(bytes(m.uc.mem_read(0x30000, len(data))), data)
        self.assertEqual(bytes(m.uc.mem_read(0x48000, 16)), struct.pack("<I", 0x5352414d) * 4)

    def test_loaded_ram_tamper_prevents_handoff(self):
        class TamperMachine(Machine):
            def emit(machine, data):
                super().emit(data)
                if machine.loaded:
                    machine.uc.mem_write(0x30000, b"\0" * 4)

        m = TamperMachine(self.build, f"U 42 {len(self.good)}\n".encode(),
                          frames(self.good), b"R 42\nI 99\n")
        output = m.run()
        self.assertIn(b"event=reject", output)
        self.assertNotIn(b"event=handoff", output)

    def test_cancel_rejected(self):
        _, output = self.transfer(wire=b"\x18\x18\x18\x18")
        self.assertIn(b"event=loaded nonce=42 result=-1", output)

    def test_real_host_tool_and_sx_over_pseudoterminal(self):
        self.assertIsNotNone(shutil.which("sx"), "此整合測試必須安裝 lrzsz")
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        failures = []

        class TerminalMachine(Machine):
            def emit(machine, data):
                machine.output.extend(data)
                os.write(master, data)
                if machine.output.endswith((b"result=pass\n", b"result=pass\r\n")):
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

        machine = TerminalMachine(self.build, b"")

        def execute():
            try:
                machine.run()
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=execute)
        try:
            with tempfile.TemporaryDirectory(prefix="bpi-sram-model-") as directory:
                path = Path(directory) / "smoke.sram"
                path.write_bytes(self.good)
                worker.start()
                result = subprocess.run([
                    sys.executable, "-B", str(ROOT / "tools/bpi_sram_uart.py"), "upload",
                    "--port", os.ttyname(slave), "--input", str(path), "--run",
                ], capture_output=True, timeout=18, check=False)
                worker.join(timeout=16)
                self.assertFalse(worker.is_alive(), "執行模型未停止")
                self.assertEqual(failures, [])
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(json.loads(result.stdout)["smoke"]["result"], "pass")
        finally:
            machine.uc.emu_stop()
            if worker.ident is not None:
                worker.join(timeout=16)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main(verbosity=2)
