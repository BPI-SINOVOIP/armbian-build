#!/usr/bin/env python3
"""執行更新器 AArch64 產物；只替代 UART、時基及 MMC 區塊操作，不接觸硬體。"""

import hashlib
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

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
    UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_LR,
)

from test_bpi_sram_execution import Machine, frames

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_sram_lab_package as package


def structure_fields(elf, name):
    for unit in elf.get_dwarf_info().iter_CUs():
        for die in unit.iter_DIEs():
            attr = die.attributes
            if die.tag != "DW_TAG_structure_type" or "DW_AT_byte_size" not in attr:
                continue
            if attr.get("DW_AT_name") is None or attr["DW_AT_name"].value != name.encode():
                continue
            return {child.attributes["DW_AT_name"].value.decode():
                    child.attributes["DW_AT_data_member_location"].value
                    for child in die.iter_children() if child.tag == "DW_TAG_member"
                    and "DW_AT_name" in child.attributes and "DW_AT_data_member_location" in child.attributes}
    raise AssertionError(f"缺少實際編譯結構：{name}")


class UpdateMachine(Machine):
    CARD, DESC = 0x42000, 0x42800
    EXECUTION_US, INSTRUCTIONS = 15_000_000, 12_000_000

    def __init__(self, loader, updater, commands, data=b"", after=b"", stop=b"event=slot",
                 source=0xffffffff, fail_write=None, corrupt_read=False, mutate_media=False):
        super().__init__(loader, commands, stop=stop)
        self.uc.mem_write(0x30000, (updater / "spl2-update.bin").read_bytes())
        with (updater / "spl2-update.elf").open("rb") as stream:
            elf = ELFFile(stream)
            self.update_symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols() if s.name}
            self.card_fields = structure_fields(elf, "mmc")
            self.desc_fields = structure_fields(elf, "blk_desc")
        self.entry = self.update_symbols["bpi_update_run"]
        self.uc.reg_write(UC_ARM64_REG_SP, 0x47b00)
        self.uc.reg_write(UC_ARM64_REG_X0, 0x48010)
        self.uc.mem_write(0x48010, struct.pack("<8I32s", 0x31505553, 3, 64, 0x06180001,
                                             42, source, 40960, 0x18000, bytes(32)))
        self.field(self.CARD, self.card_fields, "version", "I", 0x80020000)
        self.cid = "03534453523634478697bc8c07018401"
        self.uc.mem_write(self.CARD + self.card_fields["cid"], struct.pack("<4I", *(
            int(self.cid[i:i + 8], 16) for i in range(0, 32, 8))))
        self.field(self.DESC, self.desc_fields, "blksz", "Q", 512)
        self.field(self.DESC, self.desc_fields, "lba", "Q", 124735488)
        self.field(self.DESC, self.desc_fields, "block_read", "Q", self.update_symbols["mmc_bread"])
        self.field(self.DESC, self.desc_fields, "block_write", "Q", self.update_symbols["mmc_bwrite"])
        self.fake_card = bytes(self.uc.mem_read(self.CARD, 0x1000))
        self.blocks, self.writes, self.reads = {}, [], []
        mbr = bytearray(512)
        mbr[450] = 0x83
        struct.pack_into("<II", mbr, 454, 8192, 123461632)
        mbr[510:] = b"\x55\xaa"
        self.blocks[0] = bytes(mbr)
        self.data, self.after_update = data, after
        self.update_sent = False
        self.fail_write, self.corrupt_read, self.mutate_media = fail_write, corrupt_read, mutate_media
        self.initializations = 0
        self.min_sp = 0x47b00
        for name in ("get_timer", "udelay", "getchar", "tstc", "putc", "puts", "schedule",
                     "mmc_initialize", "find_mmc_device", "mmc_init", "mmc_get_blk_desc", "mmc_bread", "mmc_bwrite"):
            address = self.update_symbols[name]
            self.uc.hook_add(UC_HOOK_CODE, self.service, name, begin=address, end=address)

    def field(self, address, fields, name, fmt, value):
        self.uc.mem_write(address + fields[name], struct.pack("<" + fmt, value))

    def emit(self, data):
        for value in data:
            super().emit(bytes((value,)))
            if value == 67 and not self.update_sent and b"event=update-loading" in self.output:
                self.update_sent = True
                self.rx.extend(frames(self.data) if self.data else b"")
            if value == 10 and b"event=update-result" in bytes(self.output).splitlines()[-1]:
                self.rx.extend(self.after_update)
                self.after_update = b""

    def service(self, uc, address, size, name):
        self.min_sp = min(self.min_sp, uc.reg_read(UC_ARM64_REG_SP))
        if name == "find_mmc_device":
            uc.mem_write(self.CARD, self.fake_card)
            result = self.CARD
        elif name == "mmc_get_blk_desc":
            result = self.DESC
        elif name == "mmc_initialize":
            result = 0
        elif name == "mmc_init":
            self.initializations += 1
            if self.mutate_media and self.initializations == 2:
                self.uc.mem_write(self.CARD + self.card_fields["cid"], bytes(16))
            result = 0
        elif name in ("mmc_bread", "mmc_bwrite"):
            lba, count, buffer = (uc.reg_read(r) for r in (UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3))
            if not 0 < count <= 2:
                raise AssertionError(f"非預期 I/O 長度：{count}")
            if name == "mmc_bread":
                self.reads.append((lba, count))
                blob = b"".join(self.blocks.get(i, bytes([0xa5]) * 512) for i in range(lba, lba + count))
                if self.corrupt_read and self.writes and lba > 6656:
                    blob = bytes((blob[0] ^ 1,)) + blob[1:]
                uc.mem_write(buffer, blob)
                result = count
            else:
                if not 6656 <= lba < lba + count <= 7424:
                    raise AssertionError(f"寫入超出明確實驗槽：{lba}/{count}")
                blob = bytes(uc.mem_read(buffer, count * 512))
                self.writes.append((lba, count, blob))
                if self.fail_write == len(self.writes):
                    result = 0
                else:
                    for i in range(count):
                        self.blocks[lba + i] = blob[i * 512:(i + 1) * 512]
                    result = count
        else:
            return super().service(uc, address, size, name)
        uc.reg_write(UC_ARM64_REG_X0, result)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def run(self):
        self.uc.emu_start(self.entry, 0, timeout=self.EXECUTION_US, count=self.INSTRUCTIONS)
        if not self.stopped:
            raise AssertionError(f"更新器未完成：{bytes(self.output)[-1200:]!r}")
        if not 0x44000 < self.min_sp < 0x48000:
            raise AssertionError(f"更新器堆疊超界：{self.min_sp:#x}")
        return bytes(self.output)


class UpdateExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = Path(os.environ["BPI_SRAM_BUILD"])
        cls.updater = Path(os.environ["BPI_UPDATE_BUILD"])

    def machine(self, commands, **kwargs):
        return UpdateMachine(self.loader, self.updater, commands, **kwargs)

    def write(self, *, slot=3, payload=513, kind=4, data=None, digest=None, after=None, **kwargs):
        data = package.build_package(bytes([0x5a]) * payload, 0x18000, kind) if data is None else data
        digest = hashlib.sha256(data).hexdigest() if digest is None else digest
        commands = f"I 42\nW 42 {slot} {len(data)} {digest}\n".encode()
        after = f"I 42\nH 42 {slot}\n".encode() if after is None else after
        return self.machine(commands, data=data, after=after, **kwargs), data

    def test_media_identity_is_reported_from_actual_structure(self):
        m = self.machine(b"I 42\n", stop=b"event=media")
        output = m.run()
        self.assertIn(b"cid=03534453523634478697bc8c07018401 sectors=124735488", output)
        self.assertIn(b"partition_start=8192 partition_sectors=123461632", output)
        self.assertEqual(m.writes, [])

    def test_real_entry_initializes_its_own_sram_context(self):
        m = self.machine(b"", stop=b"event=update-ready")
        m.entry = 0x30000
        m.uc.reg_write(UC_ARM64_REG_SP, 0x55e00)
        for address in (0x0300b000, 0x07022000):
            m.uc.mem_map(address, 0x1000)
            m.allowed_registers.add(address + 0x340)
        for name in ("clock_init_uart", "timer_init", "sunxi_gpio_set_cfgpin",
                     "sunxi_gpio_set_pull", "preloader_console_init"):
            address = m.update_symbols[name]
            m.uc.hook_add(UC_HOOK_CODE, m.service, name, begin=address, end=address)
        self.assertIn(b"abi=3 kind=3 ddr=off write_slots=2,3,4", m.run())
        self.assertEqual(m.uc.cpr_read(3, 6, 12, 0, 0), m.update_symbols["sup_vectors"])
        self.assertEqual(m.writes, [])

    def test_complete_write_commits_header_last_and_rechecks(self):
        for slot, kind, size in ((2, 3, 512), (3, 4, 513), (4, 4, 8192)):
            with self.subTest(槽=slot, 長度=size):
                m, data = self.write(slot=slot, kind=kind, payload=size)
                output = m.run()
                lba = 6144 + slot * 256
                self.assertIn(b"result=0 committed=1", output)
                self.assertIn(f"bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}".encode(), output)
                self.assertEqual(m.writes[0], (lba, 1, bytes(512)))
                self.assertEqual(m.writes[-1], (lba, 1, data[:512]))
                self.assertEqual(b"".join(m.blocks[i] for i in range(lba, lba + len(data) // 512)), data)

    def test_wrong_hash_leaves_header_invalid(self):
        m, _ = self.write(digest="0" * 64)
        self.assertIn(b"result=-1 committed=0", m.run())
        self.assertEqual(m.blocks[6912], bytes(512))

    def test_bad_payload_digest_leaves_header_invalid(self):
        data = bytearray(package.build_package(bytes(513), 0x18000, 4))
        data[512] = 1
        m, _ = self.write(data=bytes(data))
        self.assertIn(b"result=-1 committed=0", m.run())
        self.assertEqual(m.blocks[6912], bytes(512))

    def test_partial_write_never_commits(self):
        m, _ = self.write(fail_write=2)
        self.assertIn(b"result=-1 committed=0", m.run())
        self.assertEqual(m.blocks[6912], bytes(512))

    def test_readback_corruption_never_commits(self):
        m, _ = self.write(corrupt_read=True)
        self.assertIn(b"result=-1 committed=0", m.run())
        self.assertEqual(m.blocks[6912], bytes(512))

    def test_protected_slots_and_own_source_are_rejected_before_transfer(self):
        for slot, source in ((0, 0xffffffff), (1, 0xffffffff), (2, 2), (3, 3), (4, 4), (5, 0xffffffff)):
            with self.subTest(槽=slot, 來源=source):
                m, _ = self.write(slot=slot, source=source, stop=b"event=update-reject")
                self.assertIn(b"event=update-reject", m.run())
                self.assertNotIn(b"event=update-loading", m.output)
                self.assertEqual(m.writes, [])

    def test_kind_mismatch_cannot_invalidate_target(self):
        for slot, kind in ((2, 4), (3, 3), (4, 3)):
            m, _ = self.write(slot=slot, kind=kind)
            self.assertIn(b"result=-1 committed=0", m.run())
            self.assertEqual(m.writes, [])

    def test_changed_cid_rejected_before_first_write(self):
        m, _ = self.write(mutate_media=True, stop=b"event=update-reject")
        self.assertIn(b"event=update-reject", m.run())
        self.assertEqual(m.writes, [])

    def test_no_media_or_wrong_nonce_cannot_write(self):
        for commands in (b"W 42 3 1536 " + b"0" * 64 + b"\n", b"I 43\n"):
            m = self.machine(commands, stop=b"event=update-reject")
            self.assertIn(b"event=update-reject", m.run())
            self.assertEqual(m.writes, [])

    def test_real_host_sx_loader_and_update_roundtrip(self):
        self.assertIsNotNone(shutil.which("sx"))
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        errors = []

        class TerminalMachine(UpdateMachine):
            EXECUTION_US, INSTRUCTIONS = 60_000_000, 60_000_000

            def emit(machine, data):
                machine.output.extend(data)
                os.write(master, data)
                if machine.output.endswith(b"\n") and b"event=slot" in bytes(machine.output).splitlines()[-1]:
                    machine.stopped = True
                    machine.uc.emu_stop()

            def service(machine, uc, address, size, name):
                if name == "tstc":
                    try:
                        machine.rx.extend(os.read(master, 4096))
                    except BlockingIOError:
                        time.sleep(0.00005)
                if name == "get_timer":
                    value = int(time.monotonic() * 1000) - uc.reg_read(UC_ARM64_REG_X0)
                    uc.reg_write(UC_ARM64_REG_X0, value)
                    uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
                    return
                if name == "udelay":
                    time.sleep(uc.reg_read(UC_ARM64_REG_X0) / 1_000_000)
                super().service(uc, address, size, name)

        machine = TerminalMachine(self.loader, self.updater, b"")
        machine.entry = machine.symbols["bpi_supervisor_run"]
        machine.uc.reg_write(UC_ARM64_REG_SP, 0x55e00)
        for address in (0x0300b000, 0x07022000):
            machine.uc.mem_map(address, 0x1000)
            machine.allowed_registers.add(address + 0x340)
        for name in ("clock_init_uart", "timer_init", "sunxi_gpio_set_cfgpin",
                     "sunxi_gpio_set_pull", "preloader_console_init"):
            address = machine.update_symbols[name]
            machine.uc.hook_add(UC_HOOK_CODE, machine.service, name, begin=address, end=address)

        def run():
            try:
                machine.run()
            except BaseException as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=run)
        try:
            with tempfile.TemporaryDirectory(prefix="bpi-update-pty-") as directory:
                root = Path(directory)
                updater = root / "update.pkg"
                updater.write_bytes(package.build_package((self.updater / "spl2-update.bin").read_bytes(), 0x18000, 3))
                candidate = root / "boot.pkg"
                data = package.build_package(bytes([0x5a]) * 513, 0x18000, 4)
                candidate.write_bytes(data)
                worker.start()
                result = subprocess.run([
                    sys.executable, "-B", "tools/bpi_sram_lab_uart.py", "update-slot",
                    "--port", os.ttyname(slave), "--updater", str(updater), "--input", str(candidate),
                    "--slot", "3", "--cid", machine.cid, "--sectors", "124735488",
                    "--partition-sectors", "123461632", "--confirm-write",
                ], capture_output=True, timeout=90)
                worker.join(timeout=16)
                self.assertFalse(worker.is_alive(), "更新執行模型未停止")
                self.assertEqual(errors, [], bytes(machine.output)[-1200:])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(b'"slot_verified": true', result.stdout)
                self.assertIn(b'"boot_verified": false', result.stdout)
                self.assertEqual(machine.blocks[6912], data[:512])
        finally:
            if worker.is_alive():
                machine.uc.emu_stop()
                worker.join(timeout=16)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
