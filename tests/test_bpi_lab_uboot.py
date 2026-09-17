#!/usr/bin/env python3
"""跨架構引導離線回歸；協定原文僅作解析測資，不接觸 UART 或電源。"""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_console as console
from tools import bpi_lab_uboot as uboot


def legacy(payload, arch, kind, load=0, entry=0, compression=0):
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), load, entry,
                         zlib.crc32(payload), 5, {"arm32": 2, "arm64": 22, "riscv64": 26}[arch],
                         kind, compression, b"BPI")
    return header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:] + payload


def fixture(arch="arm64", fmt=None, initrd="raw", source="mmc"):
    fmt = fmt or ("zImage" if arch == "arm32" else "Image")
    address = 0x40280000 if arch == "arm64" else 0x40200000
    entry = address
    kernel = bytearray(4096)
    if fmt == "zImage":
        struct.pack_into("<3I", kernel, 36, 0x016f2818, 0, len(kernel))
        entry = 0x40400000
    elif fmt == "Image":
        struct.pack_into("<3Q", kernel, 8, 0x80000 if arch == "arm64" else 0x200000, 8192, 8 if arch == "arm64" else 0)
        kernel[56:60] = b"ARM\x64" if arch == "arm64" else b"RSC\x05"
        if arch == "riscv64":
            kernel[48:56] = b"RISCV\0\0\0"
    else:
        entry = 0x40400000
        kernel = legacy(bytes(kernel), arch, 2, load=entry, entry=entry)
    dtb = bytearray(128)
    struct.pack_into(">10I", dtb, 0, 0xd00dfeed, 128, 56, 72, 40, 17, 16, 0, 0, 16)
    struct.pack_into(">4I", dtb, 56, 1, 0, 2, 9)
    ramdisk = b"070701" + bytes(506)
    if initrd == "legacy":
        ramdisk = legacy(ramdisk, arch, 3, compression=1)
    blobs = {"kernel": bytes(kernel), "initrd": ramdisk, "dtb": bytes(dtb)}
    files = {}
    for name, addr, capacity, kind, path in (
            ("kernel", address, 0x40600000 - address, fmt, "/boot/" + fmt),
            ("initrd", 0x41000000, 0x10000, initrd, "/boot/initrd"),
            ("dtb", 0x42000000, 0x20000, "dtb", "/boot/board.dtb")):
        files[name] = {"address": addr, "capacity": capacity, "format": kind, "path": path,
                       "bytes": len(blobs[name]), "sha256": hashlib.sha256(blobs[name]).hexdigest()}
    files["kernel"]["entry"] = entry
    if initrd is None:
        files["initrd"] = None
        blobs.pop("initrd")
    config = {"schema": uboot.SCHEMA, "arch": arch,
              "uboot": {"prompt": "BPI=> ", "version": "U-Boot 2025.01 (BPI)",
                        "address_bits": 32 if arch == "arm32" else 64, "line_limit": 1024,
                        "pairing_sha256": "a" * 64, "qualification_sha256": "b" * 64,
                        "abi": "mainline-v2025.01"},
              "ram": {"banks": [{"start": 0x40000000, "size": 0x4000000}],
                      "reserved": [{"start": 0x43000000, "size": 0x1000000}],
                      "kernel_work": {"start": 0x40200000, "size": 0x400000},
                      "boot": {"start": 0x40000000, "size": 0x3000000}},
              "source": {"type": "mmc", "device": 1, "partition": 2, "partuuid": "1234abcd-02"}, "files": files,
              "bootargs": ["console=ttyS0,115200", "root=UUID=01234567-89ab-cdef-0123-456789abcdef", "ro"],
              "kernel_release": "6.6.1-test", "fdt_extra": 4096}
    if source == "tftp":
        config["source"] = {"type": "tftp", "ipaddr": "192.0.2.2", "serverip": "192.0.2.1",
                            "netmask": "255.255.255.0", "gatewayip": "0.0.0.0", "ethact": "ethernet@1c30000",
                            "protection": "lmb-no-overwrite-v1"}
        config["ram"]["reserved"].extend({"start": item["address"] + item["capacity"], "size": 65536}
                                         for item in files.values() if item is not None)
    return config, blobs


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class Channel:
    """模擬傳輸與 U-Boot 協定回應；正式 runner／ConsoleSession 均不替換。"""

    def __init__(self, config, blobs, clock):
        self.config, self.blobs, self.clock = config, blobs, clock
        self.timeout, self.write_timeout = 0.1, 0.1
        self.prompt = config["uboot"]["prompt"].encode()
        self.queue = deque([self.prompt])
        self.commands, self.writes, self.memory, self.env = [], [], {}, {}
        self.overrides, self.failures = {}, set()
        self.echo_only, self.fragment, self.closed = False, False, False
        self.kernel_response = b"\r\nStarting kernel ...\r\n[ 0.000000] Linux version 6.6.1-test (test)\r\nlogin: "

    def read(self, size):
        self.clock.value += 0.001 if self.queue else self.timeout
        return self.queue.popleft() if self.queue else b""

    def write(self, wire):
        self.writes.append(wire)
        self.clock.value += 0.001
        echo = wire.replace(b"\n", b"\r\n")
        if wire.startswith((b"bootz ", b"booti ", b"bootm ")):
            self.commands.append(wire.decode().strip())
            response = echo + self.kernel_response
        else:
            match = re.fullmatch(rb"echo BPI_'([0-9a-f]{16})'_BEGIN; if (.*); then echo BPI_'\1'_OK; else echo BPI_'\1'_FAIL; fi\n", wire)
            if match is None:
                raise AssertionError("收到不在固定協定中的傳送")
            command = match[2].decode()
            self.commands.append(command)
            output = self.response(command)
            status = b"FAIL" if command in self.failures else b"OK"
            prefix = b"BPI_" + match[1]
            response = (echo + prefix + b"_BEGIN\r\n" + output + b"\r\n" + prefix + b"_" + status
                        + b"\r\n" + self.prompt)
        if self.echo_only:
            response = echo + self.prompt
        if self.fragment:
            self.queue.extend(response[index:index + 7] for index in range(0, len(response), 7))
        else:
            self.queue.append(response)
        return len(wire)

    def response(self, command):
        if command in self.overrides:
            value = self.overrides[command]
            return value() if callable(value) else value
        parts = command.split()
        if command == "version":
            return self.config["uboot"]["version"].encode() + b"\r\n"
        if command == "bdinfo":
            lines = []
            for index, span in enumerate(self.config["ram"]["banks"]):
                lines.extend([f"DRAM bank   = 0x{index:x}", f"-> start    = 0x{span['start']:x}", f"-> size     = 0x{span['size']:x}"])
            lines.extend(["relocaddr = 0x43f00000", "sp start = 0x43e00000", "lmb_dump_all:",
                          f" reserved.count = 0x{len(self.config['ram']['reserved']):x}"])
            for index, span in enumerate(self.config["ram"]["reserved"]):
                lines.append(f" reserved[{index}] [0x{span['start']:x}-0x{span['start'] + span['size'] - 1:x}], "
                             f"0x{span['size']:x} bytes, flags: no-overwrite")
            return "\r\n".join(lines).encode() + b"\r\n"
        if parts[0] == "setenv":
            if len(parts) == 2:
                self.env.pop(parts[1], None)
            else:
                self.env[parts[1]] = " ".join(parts[2:])
        elif parts[0] == "printenv":
            return f"{parts[1]}={self.env.get(parts[1], '')}\r\n".encode()
        elif command == "base 0":
            return b"Base Address: 0x00000000\r\n"
        elif parts[:2] == ["part", "uuid"]:
            return self.config["source"]["partuuid"].encode() + b"\r\n"
        elif parts[0] in ("load", "tftpboot"):
            address = int(parts[3] if parts[0] == "load" else parts[1], 16)
            name = next(name for name, item in self.config["files"].items() if item and item["address"] == address)
            data = self.blobs[name]
            if parts[0] == "load":
                data = data[:int(parts[5], 16)]
            self.memory[address] = data
            self.env["filesize"] = f"{len(data):x}"
            return (f"{len(data)} bytes read in 10 ms (1 MiB/s)\r\n" if parts[0] == "load"
                    else f"Bytes transferred = {len(data)} ({len(data):x} hex)\r\n").encode()
        elif parts[:2] == ["hash", "sha256"]:
            address, count = int(parts[2], 16), int(parts[3], 16)
            digest = hashlib.sha256(self.memory.get(address, b"")[:count]).hexdigest()
            return f"sha256 for {address:08x} ... {address + count - 1:08x} ==> {digest}\r\n".encode()
        elif parts[0] == "md.b":
            address = int(parts[1], 16)
            data = self.memory[address][:64]
            return b"".join(f"{address + offset:08x}: {data[offset:offset + 16].hex(' ')}  ................\r\n".encode()
                            for offset in range(0, 64, 16))
        elif command == "fdt list /":
            return b"/ {\r\n};\r\n"
        elif command == "fdt rsvmem print":
            return b"index           start               size\r\n------------------------------------------------\r\n"
        elif parts[:2] == ["fdt", "resize"]:
            address = self.config["files"]["dtb"]["address"]
            blob = bytearray(self.memory[address])
            struct.pack_into(">I", blob, 4, len(blob) + self.config["fdt_extra"])
            self.memory[address] = bytes(blob)
        return b""

    def close(self):
        self.closed = True


class UBootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.counter = 0

    def session(self, config, blobs):
        self.counter += 1
        clock = Clock()
        channel = Channel(config, blobs, clock)
        log = self.directory / f"rx-{self.counter}.bin"
        session = console.ConsoleSession(channel, log_path=log, monotonic=clock)
        self.addCleanup(session.close)
        return session, channel, clock

    def run_fixture(self, config, blobs, prepare=None, timeout=300):
        session, channel, clock = self.session(config, blobs)
        if prepare:
            prepare(channel)
        records = []
        result = uboot.boot(session, config, records, timeout=timeout, monotonic=clock)
        return result, records, channel, session

    def assert_stopped(self, config, blobs, prepare):
        session, channel, clock = self.session(config, blobs)
        prepare(channel)
        records = []
        with self.assertRaises((uboot.UBootError, console.ConsoleTimeout)):
            uboot.boot(session, config, records, timeout=3, monotonic=clock)
        self.assertFalse(any(command.startswith(("booti ", "bootz ", "bootm ")) for command in channel.commands))
        return channel, records

    def test_architecture_format_initrd_matrix_executes_real_runner(self):
        for arch in ("arm32", "arm64", "riscv64"):
            for fmt in (None, "uImage"):
                for initrd in ("raw", "legacy", None):
                    with self.subTest(arch=arch, fmt=fmt, initrd=initrd):
                        config, blobs = fixture(arch, fmt, initrd)
                        result, records, channel, session = self.run_fixture(config, blobs)
                        expected = "bootm" if fmt else "bootz" if arch == "arm32" else "booti"
                        self.assertTrue(channel.commands[-1].startswith(expected + " "))
                        ramdisk = channel.commands[-1].split()[2]
                        self.assertEqual(ramdisk, "-" if initrd is None else
                                         f"41000000:{len(blobs['initrd']):x}" if initrd == "raw" else "41000000")
                        self.assertEqual(result["status"], "kernel-marker-observed")
                        self.assertFalse(result["root_verified"] or result["smoke_verified"])
                        self.assertNotIn("ok", result)
                        self.assertEqual(session.buffered, b"login: ")
                        self.assertFalse(channel.closed)
                        self.assertTrue(all(record["status"] == "verified" for record in records[:-1]))
                        self.assertLess(max(i for i, cmd in enumerate(channel.commands) if cmd.startswith("load ")),
                                        min(i for i, cmd in enumerate(channel.commands) if cmd.startswith("hash sha256") and not cmd.endswith(" 0")))

    def test_fragmented_rx_and_nondefault_prompt(self):
        config, blobs = fixture()
        result, _, _, _ = self.run_fixture(config, blobs, lambda channel: setattr(channel, "fragment", True))
        self.assertEqual(result["status"], "kernel-marker-observed")

    def test_tftp_requires_actual_guard_and_explicit_static_network(self):
        config, blobs = fixture(source="tftp")
        _, _, channel, _ = self.run_fixture(config, blobs)
        self.assertEqual(sum(cmd.startswith("tftpboot ") for cmd in channel.commands), 3)
        self.assertIn("setenv ethact ethernet@1c30000", channel.commands)
        self.assertIn("setenv netretry no", channel.commands)
        self.assertIn("setenv autostart no", channel.commands)
        self.assertFalse(any("dhcp" in cmd for cmd in channel.commands))

    def test_tftp_no_overwrite_flag_is_required(self):
        config, blobs = fixture(source="tftp")
        def prepare(channel):
            response = channel.response("bdinfo").replace(b"no-overwrite", b"none")
            channel.overrides["bdinfo"] = response
        channel, _ = self.assert_stopped(config, blobs, prepare)
        self.assertFalse(any(cmd.startswith("tftpboot ") for cmd in channel.commands))

    def test_tftp_missing_guard_bad_static_network_and_unknown_protection(self):
        changes = [lambda c: c["ram"]["reserved"].pop(),
                   lambda c: c["source"].update(protection="tsize"),
                   lambda c: c["source"].update(ipaddr=123),
                   lambda c: c["source"].update(serverip="192.0.2.2"),
                   lambda c: c["source"].update(serverip="192.0.3.1"),
                   lambda c: c["source"].update(netmask="255.0.255.0"),
                   lambda c: c["source"].update(ethact="eth0;reset")]
        for mutate in changes:
            config, _ = fixture(source="tftp")
            mutate(config)
            with self.subTest(mutate=mutate), self.assertRaises(uboot.UBootError):
                uboot.validate_config(config)

    def test_tftp_rechecks_guard_before_each_load_and_rejects_wrong_counts(self):
        config, blobs = fixture(source="tftp")
        def prepare(channel):
            original = channel.response("bdinfo")
            def reply():
                if any(cmd.startswith("tftpboot ") for cmd in channel.commands):
                    return original.replace(b"reserved.count = 0x4", b"reserved.count = 0x5")
                return original
            channel.overrides["bdinfo"] = reply
        channel, _ = self.assert_stopped(config, blobs, prepare)
        self.assertEqual(sum(cmd.startswith("tftpboot ") for cmd in channel.commands), 1)

    def test_tftp_allows_only_previous_loaded_nonprotected_lmb_spans(self):
        config, blobs = fixture(source="tftp")
        def prepare(channel):
            original = channel.response("bdinfo")
            def reply():
                count, lines = len(config["ram"]["reserved"]), []
                for address, data in channel.memory.items():
                    lines.append(f" reserved[{count}] [0x{address:x}-0x{address + len(data) - 1:x}], 0x{len(data):x} bytes, flags: none\r\n")
                    count += 1
                return original.replace(b"reserved.count = 0x4", f"reserved.count = 0x{count:x}".encode()) + "".join(lines).encode()
            channel.overrides["bdinfo"] = reply
        result, _, _, _ = self.run_fixture(config, blobs, prepare)
        self.assertEqual(result["status"], "kernel-marker-observed")

    def test_decimal_mmc_dev_and_hex_partition_rendering(self):
        config, _ = fixture()
        config["source"].update(device=10, partition=15)
        commands = [step["command"] for step in uboot.render(config)["steps"]]
        self.assertIn("mmc dev 10", commands)
        self.assertIn("part uuid mmc a:f", commands)
        self.assertTrue(any(cmd.startswith("load mmc a:f ") for cmd in commands))

    def test_mmc_partition_identity_is_independent_and_precedes_loads(self):
        config, blobs = fixture()
        for response in (b"1234abcd-01\r\n", b"", b"1234abcd-02\r\n1234abcd-02\r\n"):
            channel, _ = self.assert_stopped(config, blobs, lambda ch: ch.overrides.update({"part uuid mmc 1:2": response}))
            self.assertFalse(any(cmd.startswith("load ") for cmd in channel.commands))
        config["source"].pop("partuuid")
        with self.assertRaises(uboot.UBootError):
            uboot.validate_config(config)

    def test_validate_is_pure_and_hex_addresses_normalize(self):
        config, _ = fixture()
        config["files"]["kernel"]["address"] = "0x40280000"
        previous = copy.deepcopy(config)
        result = uboot.validate_config(config)
        self.assertEqual(config, previous)
        self.assertEqual(result["files"]["kernel"]["address"], 0x40280000)

    def test_reject_configuration_without_any_console_tx(self):
        changes = [lambda c: c.update(unknown=True), lambda c: c.update(arch="mips"),
                   lambda c: c["uboot"].update(prompt=""), lambda c: c["uboot"].update(pairing_sha256=""),
                   lambda c: c["uboot"].update(abi="vendor"), lambda c: c["uboot"].update(address_bits=True),
                   lambda c: c["files"]["kernel"].update(format="FIT"),
                   lambda c: c["files"]["kernel"].update(format="zImage"),
                   lambda c: c["files"]["initrd"].update(format="auto"),
                   lambda c: c["files"]["dtb"].update(bytes=True),
                   lambda c: c["files"]["dtb"].update(sha256="f" * 63),
                   lambda c: c["files"]["kernel"].update(path="/boot/../Image"),
                   lambda c: c["files"].update(kernel=None), lambda c: c["files"].update(dtb=None),
                   lambda c: c["ram"].update(reserved=[]), lambda c: c["ram"].update(banks=[]),
                   lambda c: c["source"].update(device=True), lambda c: c["source"].update(partition=0)]
        for mutate in changes:
            config, blobs = fixture()
            session, channel, clock = self.session(config, blobs)
            mutate(config)
            with self.subTest(mutate=mutate), self.assertRaises(uboot.UBootError):
                uboot.boot(session, config, monotonic=clock)
            self.assertEqual(channel.writes, [])

    def test_reject_shell_injection_in_all_wire_inputs(self):
        for value in ("x;saveenv", "x\nreset", "${bootcmd}", "$(reset)", "`reset`", "x&&reset", "x|reset", "x'", 'x"', "x\\y", "x\x00"):
            for field in ("path", "bootargs"):
                config, _ = fixture()
                if field == "path":
                    config["files"]["kernel"][field] = value
                else:
                    config[field] = [value]
                with self.subTest(value=value, field=field), self.assertRaises(uboot.UBootError):
                    uboot.validate_config(config)

    def test_reject_overlap_overflow_and_insufficient_dtb_padding(self):
        changes = [lambda c: c["files"]["initrd"].update(address=0x40200000),
                   lambda c: c["files"]["initrd"].update(address=0x43000000),
                   lambda c: c["files"]["initrd"].update(address=2**64 - 32),
                   lambda c: c["files"]["dtb"].update(capacity=200),
                   lambda c: c["ram"]["banks"].append(copy.deepcopy(c["ram"]["banks"][0])),
                   lambda c: c["ram"]["kernel_work"].update(size=0x2000000),
                   lambda c: c["ram"]["reserved"][0].update(size=2**64)]
        for mutate in changes:
            config, _ = fixture()
            mutate(config)
            with self.subTest(mutate=mutate), self.assertRaises(uboot.UBootError):
                uboot.validate_config(config)

    def test_command_line_limit_is_enforced_before_tx(self):
        config, _ = fixture()
        config["uboot"]["line_limit"] = 256
        config["bootargs"] = ["x=" + "a" * 250]
        with self.assertRaises(uboot.UBootError):
            uboot.validate_config(config)

    def test_bdinfo_missing_or_mismatching_stops_before_load(self):
        for response in (b"RAM: 1 GiB\r\n", b"", b"DRAM bank = 0x0\r\n-> start = 0x40000000\r\n-> size = 0x8000000\r\n"):
            config, blobs = fixture()
            channel, _ = self.assert_stopped(config, blobs, lambda ch: ch.overrides.update(bdinfo=response))
            self.assertFalse(any(cmd.startswith("load ") for cmd in channel.commands))

    def test_bdinfo_undeclared_stack_or_dynamic_reservation_stops(self):
        for change in (lambda b: b.replace(b"0x43e00000", b"0x40200000"),
                       lambda b: b + b" reserved[1] [0x40280000-0x40280fff], 0x1000 bytes, flags: none\r\n"):
            config, blobs = fixture()
            def prepare(channel):
                channel.overrides["bdinfo"] = change(channel.response("bdinfo"))
            self.assert_stopped(config, blobs, prepare)

    def test_sha256_unavailable_no_crc_fallback(self):
        config, blobs = fixture()
        def prepare(channel):
            command = f"hash sha256 {config['files']['kernel']['address']:x} 0"
            channel.failures.add(command)
            channel.overrides[command] = b"Unknown hash algorithm 'sha256'\r\n"
        channel, records = self.assert_stopped(config, blobs, prepare)
        self.assertFalse(any(cmd.startswith(("load ", "crc32 ")) for cmd in channel.commands))
        self.assertEqual(records[-1]["status"], "failed")

    def test_hash_digest_range_and_duplicate_are_checked(self):
        for output in (b"sha256 for 40280000 ... 40280fff ==> " + b"0" * 64 + b"\r\n",
                       b"sha256 for 40280001 ... 40281000 ==> {digest}\r\n",
                       b"sha256 for 40280000 ... 40280fff ==> {digest}\r\n" * 2):
            config, blobs = fixture()
            digest = config["files"]["kernel"]["sha256"].encode()
            response = output.replace(b"{digest}", digest)
            self.assert_stopped(config, blobs, lambda ch: ch.overrides.update({"hash sha256 40280000 1000": response}))

    def test_length_short_extra_and_declared_output_lie_stop(self):
        for change in (-1, 1, 100000):
            config, blobs = fixture()
            blobs["kernel"] = blobs["kernel"][:change] if change == -1 else blobs["kernel"] + b"x" * change
            channel, _ = self.assert_stopped(config, blobs, lambda ch: None)
            self.assertEqual(len(channel.memory[0x40280000]), 4095 if change == -1 else 4097)
        config, blobs = fixture()
        self.assert_stopped(config, blobs, lambda ch: ch.overrides.update({"printenv filesize": b"filesize=dead\r\n"}))

    def test_kernel_header_type_and_relocation_are_checked_after_hash(self):
        for replacement in (b"\x27\x05\x19\x56", b"\xd0\x0d\xfe\xed", bytes(4)):
            config, blobs = fixture()
            data = bytearray(blobs["kernel"])
            data[56:60] = replacement
            blobs["kernel"] = bytes(data)
            config["files"]["kernel"]["sha256"] = hashlib.sha256(data).hexdigest()
            self.assert_stopped(config, blobs, lambda ch: None)
        config, blobs = fixture()
        config["files"]["kernel"]["entry"] += 4096
        self.assert_stopped(config, blobs, lambda ch: None)

    def test_raw_legacy_initrd_cannot_be_interchanged(self):
        for declared, actual in (("raw", "legacy"), ("legacy", "raw")):
            config, blobs = fixture(initrd=actual)
            config["files"]["initrd"]["format"] = declared
            self.assert_stopped(config, blobs, lambda ch: None)

    def test_compressed_legacy_kernel_and_bad_arch_are_rejected(self):
        for arch, compression in (("arm64", 1), ("arm32", 0)):
            config, blobs = fixture(fmt="uImage")
            blobs["kernel"] = legacy(bytes(4096), arch, 2, load=0x40400000, entry=0x40400000, compression=compression)
            config["files"]["kernel"]["sha256"] = hashlib.sha256(blobs["kernel"]).hexdigest()
            self.assert_stopped(config, blobs, lambda ch: None)

    def test_fit_dtb_root_and_md_address_spoof_stop(self):
        config, blobs = fixture()
        self.assert_stopped(config, blobs, lambda ch: ch.overrides.update({"fdt list /": b"/ {\r\n images {\r\n };\r\n};\r\n"}))
        self.assert_stopped(config, blobs, lambda ch: ch.overrides.update({"md.b 40280000 40": b"40280001: " + b"00 " * 15 + b"00\r\n"}))

    def test_live_uboot_dtb_and_loaded_dtb_reservations_are_checked(self):
        config, blobs = fixture()
        def prepare(channel):
            channel.overrides["bdinfo"] = channel.response("bdinfo") + b"fdt_blob = 0x40280000\r\nfdt_size = 0x1000\r\n"
        self.assert_stopped(config, blobs, prepare)
        response = b"index           start               size\r\n------------------------------------------------\r\n    0 0000000040280000 0000000000001000\r\n"
        self.assert_stopped(config, blobs, lambda ch: ch.overrides.update({"fdt rsvmem print": response}))

    def test_image_size_zero_source_overrun_and_address_width_overflow(self):
        for size in (0, 2**63):
            config, blobs = fixture()
            data = bytearray(blobs["kernel"])
            struct.pack_into("<Q", data, 16, size)
            blobs["kernel"] = bytes(data)
            config["files"]["kernel"]["sha256"] = hashlib.sha256(data).hexdigest()
            self.assert_stopped(config, blobs, lambda ch: None)
        config, _ = fixture("arm32")
        config["files"]["kernel"]["address"] = 2**32
        with self.assertRaises(uboot.UBootError):
            uboot.validate_config(config)

    def test_nonce_echo_never_contains_complete_response_marker(self):
        wire = uboot._wire("version", "0123456789abcdef")
        self.assertNotIn("BPI_0123456789abcdef_BEGIN", wire)
        self.assertNotIn("BPI_0123456789abcdef_OK", wire)

    def test_version_prompt_and_echo_do_not_fake_success(self):
        config, blobs = fixture()
        self.assert_stopped(config, blobs, lambda ch: ch.overrides.update(version=b"U-Boot other\r\n"))
        self.assert_stopped(config, blobs, lambda ch: setattr(ch, "echo_only", True))
        self.assert_stopped(config, blobs, lambda ch: setattr(ch, "queue", deque([b"=> "])))

    def test_command_failure_never_continues(self):
        config, blobs = fixture()
        command = next(step["command"] for step in uboot.render(config)["steps"] if step["check"] == "length")
        channel, records = self.assert_stopped(config, blobs, lambda ch: ch.failures.add(command))
        self.assertEqual(channel.commands[-1], command)
        self.assertEqual(records[-1]["status"], "failed")

    def test_no_kernel_marker_wrong_release_and_returned_prompt_fail_once(self):
        for response in (b"Starting kernel ...\r\n", b"Linux version 9.9.9 (test)\r\n", b"Bad Image\r\nBPI=> "):
            config, blobs = fixture()
            session, channel, clock = self.session(config, blobs)
            channel.kernel_response = response
            records = []
            with self.assertRaises(uboot.UBootError):
                uboot.boot(session, config, records, timeout=2, monotonic=clock)
            self.assertEqual(sum(cmd.startswith("booti ") for cmd in channel.commands), 1)
            self.assertEqual(records[-1]["status"], "failed")
            self.assertFalse(channel.closed)

    def test_total_deadline_and_invalid_timeouts(self):
        config, blobs = fixture()
        session, channel, clock = self.session(config, blobs)
        with self.assertRaises(uboot.UBootError):
            uboot.boot(session, config, timeout=0.02, monotonic=clock)
        self.assertLess(clock.value, 0.1)
        self.assertFalse(any(cmd.startswith("booti ") for cmd in channel.commands))
        for timeout in (True, 0, -1, float("nan"), float("inf"), 1801):
            with self.assertRaises(uboot.UBootError):
                uboot.boot(session, config, timeout=timeout)

    def test_render_contains_no_arbitrary_or_persistent_commands(self):
        config, _ = fixture()
        result = uboot.render(config)
        self.assertFalse(result["executed"])
        commands = [step["command"] for step in result["steps"]]
        for forbidden in ("saveenv", "erase", "mmc write", "source ", "run ", "env import", "reset", "bootcmd"):
            self.assertFalse(any(forbidden in command for command in commands))
        self.assertTrue(all(";" not in command and "\n" not in command for command in commands))

    def write_inputs(self, config, blobs):
        path = self.directory / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        for name, blob in blobs.items():
            target = self.directory / config["files"][name]["path"].lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        return path

    def test_cli_validate_render_and_optional_artifact_hashes_are_offline(self):
        config, blobs = fixture()
        path = self.write_inputs(config, blobs)
        for action in ("validate", "render"):
            output = io.StringIO()
            with redirect_stdout(output), mock.patch.object(uboot, "boot", side_effect=AssertionError("CLI 不得執行引導")):
                code = uboot.main([action, "--config", str(path), "--artifact-root", str(self.directory)])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(result["artifacts_verified"])
            self.assertFalse(result["hardware_verified"] or result["executed"])

    def test_cli_metadata_only_does_not_claim_artifact_verification(self):
        config, blobs = fixture()
        path = self.write_inputs(config, blobs)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(uboot.main(["validate", "--config", str(path)]), 0)
        self.assertFalse(json.loads(output.getvalue())["artifacts_verified"])

    def test_artifact_hash_size_symlink_and_duplicate_json_are_rejected(self):
        config, blobs = fixture()
        path = self.write_inputs(config, blobs)
        (self.directory / "boot/Image").write_bytes(b"x" * len(blobs["kernel"]))
        with self.assertRaises(uboot.UBootError):
            uboot.validate_artifacts(config, self.directory)
        path.write_text('{"schema": 1, "schema": 2}', encoding="utf-8")
        with self.assertRaises(uboot.UBootError):
            uboot.load_config(path)
        link = self.directory / "linked.json"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            uboot.load_config(link)
        with self.assertRaises(uboot.UBootError):
            uboot.load_config("/dev/null")

    def test_cli_errors_and_help_are_traditional_chinese(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(uboot.main(["validate", "--config", str(self.directory / "missing")]), 2)
        self.assertIn("離線核對失敗", errors.getvalue())
        with redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            uboot.main(["--help"])
        self.assertEqual(exited.exception.code, 0)
        self.assertIn("用法:", output.getvalue())
        self.assertNotIn("options:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
