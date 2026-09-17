#!/usr/bin/env python3
"""原入口一次性 UART 執行器回歸；所有資格與傳輸均為合成測資。"""

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bpi_lab_extlinux import fixture, core, RELEASE, UUID, legacy, overlay
from test_bpi_lab_uboot import Channel, Clock, console
from tools import bpi_lab_original_entry as entry


SD_CID = "11112222333344445555666677778888"
EMMC_CID = "9999aaaabbbbccccddddeeeeffff0000"


def cid_output(cid):
    return "\r\n".join(f"CID[{i}]: 0x{cid[i * 8:i * 8 + 8]}" for i in range(4)) + "\r\n"


def sd_cid_output(cid, device=0):
    return f"{entry.SD_CID_MARKER} device={device}\r\n" + cid_output(cid)


def bdinfo(config, loaded=(), *, flags="none"):
    lines = []
    for index, region in enumerate(config["ram"]["banks"]):
        lines += [f"DRAM bank = 0x{index:x}", f"-> start = 0x{region['start']:x}", f"-> size = 0x{region['size']:x}"]
    lines += ["relocaddr = 0x1ff00000", "sp start = 0x1fe00000", f"reserved.count = 0x{len(config['ram']['reserved']) + len(loaded):x}"]
    for i, region in enumerate(config["ram"]["reserved"]):
        lines.append(f"reserved[{i}] [0x{region['start']:x}-0x{region['start'] + region['size'] - 1:x}], 0x{region['size']:x} bytes, flags: no-overwrite")
    for i, item in enumerate(loaded, len(config["ram"]["reserved"])):
        lines.append(f"reserved[{i}] [0x{item['address']:x}-0x{item['address'] + item['bytes'] - 1:x}], 0x{item['bytes']:x} bytes, flags: {flags}")
    return "\r\n".join(lines) + "\r\n"


class OriginalChannel(Channel):
    def __init__(self, config, files, clock):
        super().__init__(config, {}, clock)
        self.files = files
        self.device = None
        self.kernel_response = ("\r\nLinux version " + RELEASE + " (BPI)\r\n").encode()

    def write(self, wire):
        if wire.startswith((b"source ", b"sysboot ")):
            self.writes.append(wire)
            self.commands.append(wire.decode().strip())
            self.clock.value += 0.001
            self.queue.append(wire.replace(b"\n", b"\r\n") + self.kernel_response)
            return len(wire)
        return super().write(wire)

    def response(self, command):
        if command in self.overrides:
            return super().response(command)
        parts = command.split()
        if command == "bdinfo":
            return bdinfo(self.config, [{"address": address, "bytes": len(data)} for address, data in self.memory.items()]).encode()
        if parts[0] == "bpi_lab_sd_cid":
            if int(parts[1]) != self.config["mmc"]["sd"]:
                self.failures.add(command)
                return b""
            return sd_cid_output(SD_CID, int(parts[1])).encode()
        if parts[:2] == ["mmc", "dev"]:
            self.device = int(parts[2])
        elif parts[:4] == ["mmc", "reg", "read", "cid"]:
            if self.device == self.config["mmc"]["sd"]:
                self.failures.add(command)
                return b"SD registers are not supported\r\n"
            return cid_output(EMMC_CID).splitlines()[int(parts[4])].encode() + b"\r\n"
        elif parts[:2] == ["part", "uuid"]:
            target = int(parts[3].split(":")[1], 16) if ":" in parts[3] else 1
            return (self.config["root_source"]["partuuid"] if target == self.config["root_source"]["partition"] else self.config["source"]["partuuid"]).encode() + b"\r\n"
        elif parts[0] == "fsuuid":
            return (self.config["root_uuid"] + "\r\n").encode()
        elif parts[0] == "load":
            self.assert_target(parts[2])
            address, path, length = int(parts[3], 16), parts[4], int(parts[5], 16)
            data = self.files[path][:length]
            self.memory[address] = data
            self.env["filesize"] = f"{len(data):x}"
            return f"{len(data)} bytes read in 1 ms\r\n".encode()
        elif command.startswith("if test -e mmc "):
            if parts[5].removesuffix(";") in self.files:
                self.failures.add(command)
        else:
            return super().response(command)
        return b""

    def assert_target(self, value):
        expected = f"{self.config['source']['device']:x}:{self.config['source']['partition']:x}"
        if value != expected:
            raise AssertionError("載入錯誤 MMC 分割區")


class OriginalEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0
        self.boot_counter = 0

    def save(self, name, value):
        path = self.root / name
        data = value if isinstance(value, bytes) else entry.deploy.encode(value)
        path.write_bytes(data)
        return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}

    def qualify(self, config):
        c = entry.validate_config(config)
        proof = {"schema": "bpi-lab-original-entry-observation-v1", "hardware_id": c["hardware_id"],
                 "firmware_sha256": self.firmware["binary"]["sha256"], "boot_origin": "sd", "reset": "cold",
                 "version_output": c["uboot"]["version"] + "\r\n", "bdinfo_output": bdinfo(c),
                 "sd_cid_output": sd_cid_output(SD_CID, c["mmc"]["sd"]), "emmc_cid_output": cid_output(EMMC_CID)}
        self.proof = proof
        q = {"schema": "bpi-lab-original-entry-qualification-v1", "abi": entry.ABI, "approved": True,
             "record": "fixture-qualification", "hardware_id": c["hardware_id"], "scope_sha256": entry.scope_digest(c),
             "pairing_sha256": c["pairing"]["sha256"], "firmware": self.firmware,
             "observations": self.save("observations.json", proof), "review": {k: True for k in entry.REVIEWS}}
        self.q = q
        ref = self.save("qualification.json", q)
        c["qualification"] = ref
        c["uboot"]["qualification_sha256"] = ref["sha256"]
        return c

    def make(self, board="bpi-r3", *, split=False, compressed=False, fdtdir=False, with_overlay=False, fit_kernel=False):
        self.counter += 1
        files, module, policy = fixture(board)
        if fit_kernel:
            from test_bpi_lab_spacemit import fit
            blob = fit(files["/boot/Image"])
            files["/boot/Image"] = files["/boot/vmlinuz-" + RELEASE] = blob
        if board == "bpi-m7":
            cmd = (core.ROOT / "config/bootscripts/boot-rockchip64.cmd").read_bytes()
            files["/boot/boot.cmd"], files["/boot/boot.scr"] = cmd, legacy(cmd, script=True)
            files["/boot/armbianEnv.txt"] += b"extraargs=cma=256M\n"
        if compressed:
            raw = bytearray(files["/boot/Image"])
            raw[1024:] = random.Random(7).randbytes(len(raw) - 1024)
            blob = gzip.compress(raw, mtime=0)
            files["/boot/Image"] = files["/boot/vmlinuz-" + RELEASE] = blob
        if fdtdir:
            files["/boot/extlinux/extlinux.conf"] = files["/boot/extlinux/extlinux.conf"].replace(
                ("FDT /boot/dtb/" + policy["dtb"]).encode(), b"FDTDIR /boot/dtb/")
        if with_overlay:
            files["/boot/extlinux/extlinux.conf"] += b" FDTOVERLAYS /boot/test.dtbo\n"
            files["/boot/test.dtbo"] = overlay()
        if split:
            files["/boot/extlinux/extlinux.conf"] = files["/boot/extlinux/extlinux.conf"].replace(b"/boot/", b"/")
        out = self.root / ("components-" + str(self.counter))
        def read(name):
            if name not in files:
                raise FileNotFoundError(name)
            return files[name]
        m = module.prepare(read, board=board, kernel_release=RELEASE, output=out)
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.manifest = m
        self.output = out
        bootpart = {"index": 1, "partuuid": "1234abcd-01"}
        rootpart = {"index": 2, "partuuid": "1234abcd-02"} if split else bootpart
        extracted = {"schema": "bpi-lab-image-v1", "ok": True, "hardware_validated": False, "source_verified": True,
                     "source": "/fixture/image.img", "source_digest": core.digest(b"image"),
                     "raw": {"bytes": 64 * 1024**2, "sha256": "1" * 64}, "partition": rootpart,
                     "boot_partition": bootpart, "filesystem_uuid": UUID, "queries": [], "files": {}}
        for index, (name, blob) in enumerate(files.items()):
            ref = self.save(f"file-{self.counter}-{index}.bin", blob)
            extracted["files"][name] = {"file": Path(ref["path"]).name, "digest": core.digest(blob),
                                        "volume_index": bootpart["index"] if name.startswith("/boot/") else rootpart["index"]}
        for index, row in enumerate(m["reads"]):
            if row["status"] == "missing":
                blob = (row["path"] + ": File not found by ext2_lookup\n").encode()
                ref = self.save(f"missing-{self.counter}-{index}.txt", blob)
                extracted["queries"].append({"command": "stat " + row["path"], "returncode": 0,
                                             "stderr_file": Path(ref["path"]).name, "stderr": core.digest(blob)})
        self.extracted = extracted
        self.pairing = {"schema": "bpi-lab-pairing-v1", "approved": True, "record": "fixture-pairing", "hardware_id": "fixture-board",
                        "resources": {"uart": "/dev/serial/by-id/fixture", "power": "power:fixture", "media": "cid:" + EMMC_CID},
                        "uart": {"stable_path": "/dev/serial/by-id/fixture", "baud": 115200}, "power": {}, "rescue": {},
                        "emmc": {"cid": EMMC_CID, "bytes": 1024**3, "controller": "fixture-emmc"},
                        "protected_sd": {"cid": SD_CID, "bytes": 1024**3, "controller": "fixture-sd"}}
        pairing_ref = self.save("pairing.json", self.pairing)
        kind = m["entry"]["kind"]
        role = "boot_scr" if kind == "script" else "extlinux"
        component_files = {}
        for name, address, capacity, fmt in (("kernel", 0x80000 if m["arch"] == "arm64" else 0x200000, 0x400000, m["checks"]["kernel"]["format"]),
                                            ("initrd", 0x2000000, 0x100000, m["initrd_format"]), ("dtb", 0x3000000, 0x100000, "dtb")):
            record = m["files"][name]
            component_files[name] = {k: record[k] for k in ("bytes", "sha256")}
            component_files[name].update(path=record["path"][5:] if split else record["path"], address=address, capacity=capacity, format=fmt)
        component_files["kernel"]["entry"] = component_files["kernel"]["address"]
        config = {"schema": entry.SCHEMA, "board": board, "hardware_id": "fixture-board", "root_uuid": UUID,
                  "arch": m["arch"], "kernel_release": RELEASE, "files": component_files,
                  "source": {"type": "mmc", "device": 1, "partition": bootpart["index"], "partuuid": bootpart["partuuid"]},
                  "root_source": {"partition": rootpart["index"], "partuuid": rootpart["partuuid"], "uuid": UUID}, "mmc": {"sd": 0, "emmc": 1},
                  "uboot": {"prompt": "BPI=> ", "version": "U-Boot 2025.01 (BPI)", "address_bits": 64, "line_limit": 2048,
                            "pairing_sha256": pairing_ref["sha256"], "qualification_sha256": "b" * 64, "abi": "mainline-v2025.01"},
                  "ram": {"banks": [{"start": 0, "size": 0x20000000}], "reserved": [{"start": 0x1f000000, "size": 0x1000000}],
                          "boot": {"start": 0, "size": 0x1f000000}, "kernel_work": {"start": 0x80000, "size": 0x800000}},
                  "fdt_extra": 0x65536, "bootargs": [a.replace("${partuuid}", bootpart["partuuid"]) for a in m["bootargs_template"]],
                  "entry": {"kind": kind, "path": m["entry"]["path"][5:] if split else m["entry"]["path"],
                            "address": 0x8000000, "capacity": 0x100000, **{k: m["files"][role][k] for k in ("bytes", "sha256")}},
                  "work": {"address": 0x9000000, "capacity": 0x100000},
                  "decompression": {"address": 0xa000000, "capacity": 0x100000} if compressed else None,
                  "components": {"manifest": {"path": str(out / "manifest.json"), "sha256": hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()},
                                 "artifact_root": str(out), "extraction": self.save("extraction.json", extracted)},
                  "pairing": pairing_ref, "qualification": {"path": str(self.root / "qualification.json"), "sha256": "b" * 64},
                  "authorization": {"record": "fixture-authorization", "one_shot": True, "customer_boot_may_write_emmc": True}}
        if m.get("script_profile") == "rk3576":
            config["work"]["address"] = 0x48000000
            config["ram"]["banks"][0]["size"] = 0x80000000
            config["ram"]["boot"]["size"] = 0x70000000
        if m["arch"] == "arm32":
            config["uboot"]["address_bits"] = 32
            config["ram"]["kernel_work"] = {"start": 0x8000, "size": 0x1800000}
            config["files"]["kernel"]["entry"] = 0x8000
        if board == "bpi-forge1":
            config["work"]["address"] = 0x2000000
            config["files"]["initrd"]["address"] = 0x2800000
        if fit_kernel:
            config["files"]["kernel"]["address"] = 0x1000000
            config["ram"]["kernel_work"] = {"start": 0x200000, "size": 0x1400000}
        flags = entry.REQUIRED_CONFIG + ["CONFIG_CMD_SOURCE", "CONFIG_CMD_IMPORTENV", "CONFIG_CMD_SYSBOOT", "CONFIG_GZIP",
                                         "CONFIG_CMD_BOOTI", "CONFIG_CMD_BOOTZ", "CONFIG_CMD_EXT4", "CONFIG_CMD_BOOTM", "CONFIG_FIT"]
        self.firmware = {"binary": self.save("uboot.bin", b"fixture-uboot\0bpi_lab_sd_cid\0" + entry.SD_CID_MARKER.encode()),
                         "sd_cid_source": self.save("bpi_lab_sd_cid.c", entry.SD_CID_SOURCE),
                         "config": self.save("uboot.config", ("\n".join(k + "=y" for k in flags)
                             + "\nCONFIG_SYS_CBSIZE=2048\nCONFIG_SYS_BOOTM_LEN=0x4000000\n").encode())}
        self.media = {name[5:] if split else name: blob for name, blob in files.items() if name.startswith("/boot/")}
        self.config = self.qualify(config)
        return self.config

    def boot(self, config=None, *, mutate=None, timeout=30):
        c = config or self.config
        clock = Clock()
        channel = OriginalChannel(c, self.media, clock)
        if mutate:
            mutate(channel)
        self.channel = channel
        self.records = []
        self.boot_counter += 1
        with console.ConsoleSession(channel, log_path=self.root / f"uart-{self.boot_counter}.bin", monotonic=clock) as session:
            return entry.boot(session, c, self.records, timeout=timeout, monotonic=clock)

    def test_extlinux_script_riscv_and_split_positive(self):
        for board, split, compressed in (("bpi-r3", False, False), ("bpi-m7", False, False),
                                         ("bpi-f3", False, True), ("bpi-r3", True, False)):
            with self.subTest(board=board, split=split):
                c = self.make(board, split=split, compressed=compressed)
                result = self.boot()
                self.assertEqual(result["status"], "kernel-marker-observed")
                self.assertFalse(result["hardware_validated"])
                self.assertFalse(result["root_verified"])
                final = self.channel.commands[-1]
                self.assertTrue(final.startswith("source " if board == "bpi-m7" else "sysboot mmc 1:1 any "))
                self.assertEqual(sum(cmd.startswith(("source ", "sysboot ")) for cmd in self.channel.commands), 1)
                self.assertNotRegex("\n".join(self.channel.commands), r"saveenv|env save|mmc (?:write|erase|partconf)|reset")
                self.assertEqual(c, entry.validate_config(c))
                if board == "bpi-m7":
                    self.assertIn("cma=256M", c["bootargs"])

    def test_fdtdir_binding_and_render(self):
        c = self.make(fdtdir=True)
        rendered = entry.render(c)
        self.assertFalse(rendered["executed"])
        self.assertTrue(any(s["command"].startswith("setenv fdtfile mediatek/") for s in rendered["steps"]))
        self.assertEqual(entry.build_uboot_config(c, artifact_root=self.output), c)
        self.boot()

    def test_qualification_digest_without_document_is_not_authority(self):
        self.make()
        (self.root / "qualification.json").unlink()
        with self.assertRaises((ValueError, OSError)):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_qualification_content_and_scope(self):
        for field, value in (("approved", False), ("scope_sha256", "0" * 64), ("abi", "vendor"), ("hardware_id", "wrong")):
            self.make()
            self.q[field] = value
            ref = self.save("qualification.json", self.q)
            self.config["qualification"] = ref
            self.config["uboot"]["qualification_sha256"] = ref["sha256"]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.boot()
            self.assertEqual(self.channel.writes, [])

    def test_actual_observations_required(self):
        self.make()
        self.proof["sd_cid_output"] = cid_output(EMMC_CID)
        self.q["observations"] = self.save("observations.json", self.proof)
        ref = self.save("qualification.json", self.q)
        self.config["qualification"] = ref
        self.config["uboot"]["qualification_sha256"] = ref["sha256"]
        with self.assertRaisesRegex(ValueError, "CID"):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_external_summary_cannot_replace_actual_append(self):
        self.make()
        self.manifest["bootargs_template"].append("quiet")
        data = core.binary._json(self.manifest)
        (self.output / "manifest.json").write_bytes(data)
        self.config["components"]["manifest"]["sha256"] = hashlib.sha256(data).hexdigest()
        self.config["bootargs"].append("quiet")
        self.config = self.qualify(self.config)
        with self.assertRaisesRegex(ValueError, "重新解析"):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_config_changes_cannot_retarget_media_or_paths(self):
        for mutation in (lambda c: c["source"].update(device=0), lambda c: c["files"]["kernel"].update(path="/Image"),
                         lambda c: c["source"].update(partition=2), lambda c: c["root_source"].update(partuuid="87654321-01")):
            self.make()
            mutation(self.config)
            with self.assertRaises(ValueError):
                self.config = self.qualify(self.config)
                self.boot()

    def test_k3_blocked_before_uart(self):
        self.make()
        self.config["board"] = "bpi-sm10"
        self.config["entry"]["kind"] = "vendor-env"
        with self.assertRaisesRegex(ValueError, "K3.*vendor runtime"):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_static_overlap_and_dynamic_lmb(self):
        self.make()
        self.config["entry"]["address"] = self.config["work"]["address"]
        with self.assertRaisesRegex(ValueError, "重疊"):
            entry.validate_config(self.config)
        self.make()
        def mutate(ch):
            text = bdinfo(ch.config).replace("reserved.count = 0x1", "reserved.count = 0x2")
            text += "reserved[1] [0x8000000-0x8000fff], 0x1000 bytes, flags: no-overwrite\r\n"
            ch.overrides["bdinfo"] = text.encode()
        with self.assertRaisesRegex(ValueError, "LMB"):
            self.boot(mutate=mutate)
        self.assertFalse(any(cmd.startswith("load ") for cmd in self.channel.commands))

    def test_cid_partuuid_rootuuid_length_hash_mismatch_stops(self):
        self.make()
        overrides = [("mmc reg read cid 0", b"CID[0]: 0xdeadbeef\r\n"),
                     ("part uuid mmc 1:1", b"1234abcd-02\r\n"), ("fsuuid mmc 1:1", b"wrong\r\n"),
                     ("printenv filesize", b"filesize=ffff\r\n")]
        item = self.config["files"]["kernel"]
        overrides.append((f"hash sha256 {item['address']:x} {item['bytes']:x}", b"sha256 for 00080000 ... 00080fff ==> " + b"0" * 64 + b"\r\n"))
        for command, output in overrides:
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.boot(mutate=lambda ch: ch.overrides.update({command: output}))
            self.assertEqual(self.records[-1]["status"], "failed")
            self.assertFalse(any(x.startswith(("source ", "sysboot ")) for x in self.channel.commands))

    def test_script_unexpected_fixup_blocks(self):
        self.make("bpi-m7")
        self.media["/boot/fixup.scr"] = b"unexpected"
        with self.assertRaises(ValueError):
            self.boot()
        self.assertFalse(any(x.startswith("source ") for x in self.channel.commands))

    def test_failed_extract_or_changed_kernel_never_sends(self):
        self.make()
        self.extracted["ok"] = False
        self.config["components"]["extraction"] = self.save("extraction.json", self.extracted)
        self.config = self.qualify(self.config)
        with self.assertRaises(ValueError):
            self.boot()
        self.assertEqual(self.channel.writes, [])
        self.make()
        kernel = self.output / self.manifest["files"]["kernel"]["evidence_path"]
        kernel.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_wrong_kernel_return_prompt_echo_only_and_deadline(self):
        self.make()
        mutations = [lambda ch: setattr(ch, "kernel_response", b"\r\nLinux version wrong (BPI)\r\n"),
                     lambda ch: setattr(ch, "kernel_response", b"\r\n" + ch.prompt),
                     lambda ch: setattr(ch, "echo_only", True)]
        for mutate in mutations:
            with self.assertRaises(ValueError):
                self.boot(mutate=mutate, timeout=2)
        with self.assertRaises(ValueError):
            self.boot(timeout=0.001)

    def test_partial_send_and_fragmented_receive(self):
        self.make()
        self.boot(mutate=lambda ch: setattr(ch, "fragment", True))
        with mock.patch.object(console.ConsoleSession, "send", return_value=1), self.assertRaises(ValueError):
            self.boot()

    def test_overlay_not_silently_ignored(self):
        self.make(with_overlay=True)
        with self.assertRaisesRegex(ValueError, "overlay"):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_firmware_config_content_not_only_hash(self):
        for raw in (b"CONFIG_HUSH_PARSER=y\n", b"CONFIG_HUSH_PARSER=y\n# CONFIG_HUSH_PARSER is not set\n"):
            self.make()
            self.firmware["config"] = self.save("uboot.config", raw)
            self.config = self.qualify(self.config)
            with self.assertRaisesRegex(ValueError, "config"):
                self.boot()
            self.assertEqual(self.channel.writes, [])

    def test_script_resize_uses_actual_hex_semantics(self):
        self.make("bpi-m7")
        self.config["fdt_extra"] = 65536
        self.config = self.qualify(self.config)
        with self.assertRaisesRegex(ValueError, "十六進位"):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_absent_probe_io_error_is_not_absence(self):
        self.make("bpi-m7")
        command = "if test -e mmc 1:1 /boot/fixup.scr; then false; else true; fi"
        with self.assertRaisesRegex(ValueError, "缺檔探測"):
            self.boot(mutate=lambda ch: ch.overrides.update({command: b"I/O error\r\n"}))

    def test_dtb_static_disabled_and_dynamic_are_distinct(self):
        c = self.make()
        parent = {"#address-cells": "0 0 0 2", "#size-cells": "0 0 0 2", "ranges": ""}
        def cells(start, size):
            return (start.to_bytes(8, "big") + size.to_bytes(8, "big")).hex(" ")
        checked = {"memreserve": [], "reserved_memory": {"/reserved-memory": parent,
                    "/reserved-memory/fixed": {"reg": cells(0x1f000000, 0x1000)},
                    "/reserved-memory/off": {"status": b"disabled\0".hex(" "), "reg": cells(0x80000, 0x1000)},
                    "/reserved-memory/cma": {"size": (0x100000).to_bytes(8, "big").hex(" ")}}}
        entry._dtb_memory(checked, c)
        checked["reserved_memory"]["/reserved-memory/fixed"]["reg"] = cells(0x80000, 0x1000)
        with self.assertRaisesRegex(ValueError, "ram.reserved"):
            entry._dtb_memory(checked, c)

    def test_deadline_includes_offline_gate(self):
        self.make()
        c = self.config
        clock = Clock()
        channel = OriginalChannel(c, self.media, clock)
        validated = entry.validate_artifacts(c)
        def slow(_):
            clock.value += 3
            return validated
        with mock.patch.object(entry, "validate_artifacts", side_effect=slow), self.assertRaisesRegex(ValueError, "本機核對已逾時"):
            entry.boot(channel, c, timeout=2, monotonic=clock)
        self.assertEqual(channel.writes, [])

    def test_command_limit_matches_firmware_not_only_approval(self):
        self.make()
        config_file = Path(self.firmware["config"]["path"])
        raw = config_file.read_bytes().replace(b"CONFIG_SYS_CBSIZE=2048", b"CONFIG_SYS_CBSIZE=256")
        self.firmware["config"] = self.save("uboot.config", raw)
        self.config = self.qualify(self.config)
        with self.assertRaisesRegex(ValueError, "CONFIG_SYS_CBSIZE"):
            self.boot()
        self.assertEqual(self.channel.writes, [])

    def test_rk3576_uses_real_environment_reload_address(self):
        for board in ("bpi-m5pro", "bpi-cm5pro"):
            with self.subTest(board=board):
                self.make(board)
                self.boot()
                self.assertTrue(any(command.startswith("load mmc 1:1 48000000 /boot/armbianEnv.txt ") for command in self.channel.commands))
                self.config["work"]["address"] = 0x9000000
                self.config["ram"]["reserved"].append({"start": 0x48000000, "size": 0x100000})
                self.config = self.qualify(self.config)
                with self.assertRaisesRegex(ValueError, "load_addr"):
                    self.boot()
                self.assertEqual(self.channel.writes, [])

    def test_arm32_original_bootz_scripts(self):
        from tools import bpi_lab_backend as backend
        from test_bpi_lab_extlinux import template
        for board in ("bpi-forge1", "bpi-r2"):
            with self.subTest(board=board):
                self.make(board)
                t = template(self.manifest)
                t.update(pairing_sha256=self.config["pairing"]["sha256"], firmware_review_sha256=self.config["qualification"]["sha256"])
                selected = backend.render_family(self.manifest, t, self.output, runtime_config=self.config)
                bundle = {"board": board, "uboot_qualification": self.config["qualification"],
                          "transport": {"kind": "mmc-original", "image_paths": {role: self.manifest["files"][role]["path"] for role in self.config["files"]}}}
                selected = backend.bind_runtime(bundle, selected, self.extracted, self.config["components"]["extraction"], {"pairing": self.config["pairing"]})
                self.assertIs(backend.life.boot_driver(selected), entry)
                result = self.boot(selected)
                self.assertEqual(result["status"], "kernel-marker-observed")
                self.assertIn("help bootz", self.channel.commands)
                self.assertNotIn("help booti", self.channel.commands)
                self.assertTrue(self.channel.commands[-1].startswith("source "))
                self.assertFalse(any(command.startswith("bootz ") for command in self.channel.commands))
                if board == "bpi-r2":
                    self.assertIn("setenv mmcpart 1", self.channel.commands)
                    self.assertIn("help ext4load", self.channel.commands)
                else:
                    self.assertIn("part uuid mmc 1", self.channel.commands)
                    self.assertIn("setenv ramdisk_addr_r 2800000", self.channel.commands)

    def test_arm32_fixed_addresses_expanded_kernel_and_commands(self):
        self.make("bpi-forge1")
        self.config["files"]["initrd"]["address"] = 0x2400000
        self.config = self.qualify(self.config)
        with self.assertRaisesRegex(ValueError, "ramdisk_addr_r"):
            self.boot()
        self.assertEqual(self.channel.writes, [])
        self.make("bpi-r2")
        region = self.config["ram"]["kernel_work"]
        self.config["files"]["kernel"]["entry"] = region["start"] + region["size"] - 4
        self.config = self.qualify(self.config)
        with self.assertRaisesRegex(ValueError, "展開核心"):
            self.boot()
        self.assertEqual(self.channel.writes, [])
        for flag in ("CONFIG_CMD_BOOTZ", "CONFIG_CMD_EXT4"):
            self.make("bpi-r2")
            blob = Path(self.firmware["config"]["path"]).read_bytes().replace((flag + "=y").encode(), (flag + "=n").encode())
            self.firmware["config"] = self.save("uboot.config", blob)
            self.config = self.qualify(self.config)
            with self.assertRaisesRegex(ValueError, "config"):
                self.boot()
            self.assertEqual(self.channel.writes, [])

    def test_forge_implicit_partition_must_match_explicit_target(self):
        self.make("bpi-forge1")
        with self.assertRaisesRegex(ValueError, "UUID"):
            self.boot(mutate=lambda channel: channel.overrides.update({"part uuid mmc 1": b"1234abcd-02\r\n"}))
        self.assertFalse(any(command.startswith("load ") for command in self.channel.commands))

    def test_cm6_fit_keeps_sysboot_bootm_external_initrd_dtb(self):
        c = self.make("bpi-cm6", fit_kernel=True)
        view = entry.lifecycle_view(c)
        self.assertEqual(view["files"]["kernel"]["format"], "Image")
        self.assertEqual(c["files"]["kernel"]["format"], "FIT")
        result = self.boot()
        self.assertEqual(result["status"], "kernel-marker-observed")
        self.assertIn("help bootm", self.channel.commands)
        self.assertNotIn("help booti", self.channel.commands)
        self.assertIn("setenv verify yes", self.channel.commands)
        self.assertTrue(self.channel.commands[-1].startswith("sysboot mmc 1:1 "))
        self.assertFalse(any(cmd.startswith("bootm ") for cmd in self.channel.commands))
        for role in ("kernel", "initrd", "dtb"):
            self.assertEqual(c["files"][role]["sha256"], self.manifest["files"][role]["sha256"])

    def test_fit_rejects_destination_overlap_and_missing_bootm(self):
        for defect in ("overlap", "entry", "config", "bootm-limit"):
            self.make("bpi-cm6", fit_kernel=True)
            if defect == "overlap":
                self.config["files"]["kernel"]["address"] = 0x200000
            elif defect == "entry":
                self.config["files"]["kernel"]["entry"] = 0x200004
            elif defect == "config":
                self.firmware["config"] = self.save("uboot.config", Path(self.firmware["config"]["path"]).read_bytes()
                                                    .replace(b"CONFIG_CMD_BOOTM=y", b"CONFIG_CMD_BOOTM=n"))
            else:
                self.firmware["config"] = self.save("uboot.config", Path(self.firmware["config"]["path"]).read_bytes()
                                                    .replace(b"CONFIG_SYS_BOOTM_LEN=0x4000000", b"CONFIG_SYS_BOOTM_LEN=1"))
            self.config = self.qualify(self.config)
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                self.boot()
            self.assertEqual(self.channel.writes, [])

    def test_mainline_sd_reg_failure_is_not_simulated_success(self):
        self.make()
        channel = OriginalChannel(self.config, self.media, Clock())
        channel.response("mmc dev 0 0")
        self.assertIn(b"SD registers are not supported", channel.response("mmc reg read cid 0"))
        self.assertIn("mmc reg read cid 0", channel.failures)
        self.boot()
        self.assertIn("bpi_lab_sd_cid 0", self.channel.commands)
        self.assertNotIn("mmc dev 0 0", self.channel.commands)

    def test_stock_or_changed_sd_helper_rejected_before_uart(self):
        for key, blob in (("binary", b"fixture-uboot"), ("sd_cid_source", b"changed")):
            with self.subTest(key=key):
                self.make()
                self.firmware[key] = self.save("changed-" + key, blob)
                self.config = self.qualify(self.config)
                with self.assertRaises(ValueError):
                    self.boot()
                self.assertEqual(self.channel.writes, [])

    def test_sd_cid_abi_device_or_identity_mismatch_stops(self):
        self.make()
        for output in (cid_output(SD_CID), sd_cid_output(SD_CID, 1), sd_cid_output(EMMC_CID),
                       sd_cid_output(SD_CID).replace("CID[2]", "CID[1]")):
            with self.subTest(output=output[:40]), self.assertRaises(ValueError):
                self.boot(mutate=lambda channel: channel.overrides.update({"bpi_lab_sd_cid 0": output.encode()}))
            self.assertFalse(any(command.startswith("load ") for command in self.channel.commands))

    def test_loaded_lmb_requires_exact_verified_span_and_writable_flags(self):
        c = self.make("bpi-m7")
        records = list(c["files"].values()) + [c["entry"], {**c["work"], "bytes": self.manifest["files"]["env"]["bytes"]}]
        entry._memory(bdinfo(c, records).encode(), c, records)
        for record in records:
            for changed in ({**record, "bytes": record["bytes"] + 1},
                            {**record, "address": record["address"] + 1, "bytes": record["bytes"] - 1}):
                with self.subTest(address=record["address"], changed=changed["bytes"]), self.assertRaises(ValueError):
                    entry._memory(bdinfo(c, [changed]).encode(), c, records)
            with self.assertRaises(ValueError):
                entry._memory(bdinfo(c, [record], flags="no-overwrite").encode(), c, records)
            with self.assertRaises(ValueError):
                entry._memory(bdinfo(c, [record]).encode(), c)

    def test_second_bdinfo_tracks_files_but_rejects_unknown_lmb(self):
        self.make()
        self.boot()
        seen = [row for row in self.records if row["check"] == "entry-memory"]
        self.assertEqual(len(seen), 2)
        self.assertIn("reserved.count = 0x5", seen[-1]["output"])
        def mutate(channel):
            def response():
                loaded = [{"address": address, "bytes": len(blob)} for address, blob in channel.memory.items()]
                if loaded:
                    loaded.append({"address": channel.config["work"]["address"] + 4096, "bytes": 1024})
                return bdinfo(channel.config, loaded).encode()
            channel.overrides["bdinfo"] = response
        with self.assertRaisesRegex(ValueError, "LMB"):
            self.boot(mutate=mutate)
        self.assertFalse(any(command.startswith("sysboot ") for command in self.channel.commands))

    def test_lmb_remaining_indices_are_rebuilt_and_full_guard_is_used(self):
        c = self.make()
        item = c["files"]["kernel"]
        text = bdinfo(c, [item])
        lines = text.splitlines()
        a = next(i for i, line in enumerate(lines) if line.startswith("reserved[0]"))
        b = next(i for i, line in enumerate(lines) if line.startswith("reserved[1]"))
        lines[a], lines[b] = lines[b].replace("reserved[1]", "reserved[0]"), lines[a].replace("reserved[0]", "reserved[1]")
        with mock.patch.object(entry.uboot, "_memory", wraps=entry.uboot._memory) as guard:
            entry._memory("\n".join(lines).encode(), c, [item])
            guard.assert_called_once()
            observed = guard.call_args.args[0]
            self.assertIn(b"reserved.count = 0x1", observed)
            self.assertIn(b"reserved[0]", observed)
            self.assertNotIn(b"reserved[1]", observed)
        bad = "\n".join(lines).replace("reserved[1]", "reserved[2]").encode()
        with self.assertRaises(ValueError):
            entry._memory(bad, c, [item])
        intruder = {"address": c["files"]["dtb"]["address"] + 4096, "bytes": 4096}
        with self.assertRaises(ValueError):
            entry._memory(bdinfo(c, [item, intruder]).encode(), c, [item])

    def test_sd_cid_source_compiles_and_checks_device_type(self):
        output = self.root / "sd-helper"
        report = entry.build_rescue_sd_cid(output=output)
        self.assertFalse(report["hardware_validated"])
        self.assertEqual((output / "bpi_lab_sd_cid.c").read_bytes(), entry.SD_CID_SOURCE)
        (output / "command.h").write_text('#include <stdio.h>\nstruct cmd_tbl { int unused; };\n'
            '#define CMD_RET_SUCCESS 0\n#define CMD_RET_FAILURE 1\n#define CMD_RET_USAGE 2\n#define U_BOOT_CMD(...)\n')
        (output / "mmc.h").write_text('#include <stdint.h>\nstruct mmc { int sd; uint32_t cid[4]; };\n'
            '#define IS_SD(m) ((m)->sd)\nstruct mmc *find_mmc_device(int);\nint mmc_init(struct mmc *);\n')
        (output / "test.c").write_text('#include <string.h>\n#include "bpi_lab_sd_cid.c"\n'
            'static struct mmc card = { 1, {0x11112222, 0x33334444, 0x55556666, 0x77778888} };\n'
            'static int error;\nstruct mmc *find_mmc_device(int n) { return n == 0 ? &card : NULL; }\n'
            'int mmc_init(struct mmc *m) { return error; }\n'
            'int main(int argc, char **argv) { char *args[] = {"bpi_lab_sd_cid", argv[2], NULL};\n'
            'if (argc != 3) return 9; card.sd = strcmp(argv[1], "emmc"); error = !strcmp(argv[1], "error");\n'
            'return do_bpi_lab_sd_cid(NULL, 0, 2, args); }\n')
        binary = output / "test"
        subprocess.run(["cc", "-I", str(output), str(output / "test.c"), "-o", str(binary)], check=True, capture_output=True, timeout=30)
        result = subprocess.run([str(binary), "sd", "0"], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(entry._sd_cid(result.stdout, 0), SD_CID)
        for mode, device in (("emmc", "0"), ("error", "0"), ("sd", "1"), ("sd", "-1"),
                             ("sd", "256"), ("sd", "999999999999"), ("sd", ""), ("sd", "0x0")):
            result = subprocess.run([str(binary), mode, device], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_k3_audit_distinguishes_software_from_qualification(self):
        report = entry.audit_k3_abi()
        self.assertEqual(report["status"], "software-blocked")
        self.assertFalse(report["hardware_validated"])
        self.assertFalse(report["capabilities"]["hash_command"])
        self.assertFalse(report["capabilities"]["full_mmc_cid_command"])
        self.assertEqual({x["code"] for x in report["software_blockers"]}, {
            "k3_hash_command", "k3_mmc_cid_command", "k3_vendor_runtime", "k3_console_memory_abi"})
        self.assertTrue(report["qualification_requirements"])

    def test_k3_source_change_is_not_old_abi(self):
        with mock.patch.dict(entry.K3_ABI_SOURCES, {"cmd/mmc.c": "0" * 64}), self.assertRaises(ValueError):
            entry.audit_k3_abi()


REAL_SAMPLES = {
    "bpi-cm6": ("bpi-c3-cm6-fit-replay-20260918-001", "fcf34e9c0550251f048ad47712508fce37820140c8301536336b65c3b30de96e"),
    "bpi-m7": ("m7-002", "e364a6c9db115c6aa03309e9612b786a5a9a9027946a67c1b2635ac29186beb0"),
    "bpi-r3": ("r3-001", "01ee91bca56aa5408b03c1ddf0e6880bb100425e6a88cbdbb69b4437f91f991a"),
    "bpi-f3": ("f3-002-replay", "e4ec4ba57465230e8892df59d91fbd359f919126f7b8710ef00bf406fd19250e"),
    "bpi-sm10": ("sm10-002-replay", "90c9b1ac05c83a988849190b1d8fbeff3f0bde47ede9b902c8b6797dab0bcb59"),
}


@unittest.skipUnless(os.environ.get("BPI_LAB_REAL_C3") == "1", "真組件測試須明示 BPI_LAB_REAL_C3=1；仍不操作硬體")
class RealImageEntryTests(unittest.TestCase):
    """真組件配離線 RAM／UART 模型；不保留合成核定為可供實板使用的配置。"""

    def sample(self, board):
        name, expected = REAL_SAMPLES[board]
        root = core.ROOT / "output/evidence" / (name if name.startswith("bpi-c3-") else "bpi-multiboard-integrate-20260917-" + name)
        prepared = json.loads((root / "preparation.json").read_bytes())
        self.assertEqual(prepared["status"], "prepared")
        self.assertTrue(prepared["root_uuid_verified"])
        for key in ("components", "extraction"):
            item = prepared[key]
            blob = entry.deploy.checked_bytes({"path": str(root / item["path"]), "sha256": item["sha256"]}, 4 * 1024**2)
            self.assertEqual(len(blob), item["bytes"])
        self.assertEqual(prepared["extraction"]["sha256"], expected)
        manifest_path = root / "components/manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        self.assertEqual(manifest, json.loads((root / prepared["components"]["path"]).read_bytes()))
        self.assertFalse(manifest["hardware_validated"])
        return root, prepared, manifest

    def model_config(self, helper, root, prepared, manifest):
        is_fit = manifest["checks"]["kernel"]["format"] == "FIT"
        c = copy.deepcopy(helper.make(manifest["board"], fit_kernel=is_fit))
        ref = {"path": str(root / prepared["extraction"]["path"]), "sha256": prepared["extraction"]["sha256"]}
        with entry.image.SnapshotReader(ref["path"], ref["sha256"], helper.root / "real-extraction") as reader:
            extraction = copy.deepcopy(reader.original)
            data = {p: reader.read_file(p) for p in extraction["files"]}
        boot = extraction.get("boot_partition", extraction["partition"])
        c.update(kernel_release=manifest["kernel_release"], root_uuid=manifest["root_uuid"])
        c["root_source"] = {"partition": extraction["partition"]["index"], "partuuid": extraction["partition"]["partuuid"], "uuid": manifest["root_uuid"]}
        c["source"].update(partition=boot["index"], partuuid=boot["partuuid"])
        c["bootargs"] = [arg.replace("${partuuid}", boot["partuuid"]) for arg in manifest["bootargs_template"]]
        c["components"] = {"manifest": {"path": str(root / "components/manifest.json"),
                                          "sha256": hashlib.sha256((root / "components/manifest.json").read_bytes()).hexdigest()},
                           "artifact_root": str(root / "components"), "extraction": ref}
        mount = manifest["runtime_requirements"]["boot_mount"]
        helper.media = {entry._media_path(p, mount): b for p, b in data.items() if p.startswith("/boot/")}
        k = manifest["checks"]["kernel"]
        payload = k["payload"] if is_fit else k
        # 這些位址只供模型檢查，並非任何板型的 RAM 建議或實板核定。
        bank = {"start": 0x200000, "size": 0x80000000}
        c["ram"]["banks"] = [bank]
        c["ram"]["boot"] = dict(bank)
        for role, address in (("kernel", 0x4000000 if is_fit else bank["start"] + k["text_offset"]), ("initrd", 0x10000000), ("dtb", 0x18000000)):
            record = manifest["files"][role]
            c["files"][role].update({key: record[key] for key in ("bytes", "sha256")})
            size = max(record["bytes"] + 1, payload["image_size"] if role == "kernel" else record["bytes"] + c["fdt_extra"] + 4096)
            capacity = (size + 0x1fffff) // 0x200000 * 0x200000
            c["files"][role].update(path=entry._media_path(record["path"], mount), address=address, capacity=capacity)
        c["files"]["kernel"]["entry"] = c["files"]["kernel"]["address"]
        c["files"]["initrd"]["format"] = manifest["initrd_format"]
        c["ram"]["kernel_work"] = entry.uboot._slot(c["files"]["kernel"])
        if is_fit:
            c["files"]["kernel"]["entry"] = k["fit"]["entry"]
            c["ram"]["kernel_work"] = {"start": k["fit"]["load"], "size": 0x7000000 - k["fit"]["load"]}
        if k.get("compression"):
            c["decompression"] = {"address": 0x20000000, "capacity": c["files"]["kernel"]["bytes"] * 10}
        role = "boot_scr" if manifest["entry"]["kind"] == "script" else "extlinux"
        c["entry"].update({key: manifest["files"][role][key] for key in ("bytes", "sha256")})
        c["entry"].update(path=entry._media_path(manifest["entry"]["path"], mount), address=0x1a000000)
        reserved = list(manifest["checks"]["dtb"]["memreserve"]) + c["ram"]["reserved"]
        nodes = manifest["checks"]["dtb"]["reserved_memory"]
        for node, props in nodes.items():
            if "reg" not in props or props.get("status") == "64 69 73 61 62 6c 65 64 0":
                continue
            self.assertEqual(nodes["/reserved-memory"]["#address-cells"], "0 0 0 2")
            self.assertEqual(nodes["/reserved-memory"]["#size-cells"], "0 0 0 2")
            blob = bytes(int(x, 16) for x in props["reg"].split())
            self.assertEqual(len(blob) % 16, 0, node)
            for i in range(0, len(blob), 16):
                reserved.append({"start": int.from_bytes(blob[i:i + 8], "big"), "size": int.from_bytes(blob[i + 8:i + 16], "big")})
        spans = []
        for region in sorted(reserved, key=lambda x: x["start"]):
            left, right = max(region["start"], bank["start"]), min(region["start"] + region["size"], bank["start"] + bank["size"])
            if left >= right:
                continue
            if spans and left <= spans[-1]["start"] + spans[-1]["size"]:
                spans[-1]["size"] = max(spans[-1]["size"], right - spans[-1]["start"])
            else:
                spans.append({"start": left, "size": right - left})
        c["ram"]["reserved"] = spans
        return helper.qualify(c), extraction

    def test_real_cm6_fit_original_runner(self):
        root, prepared, manifest = self.sample("bpi-cm6")
        helper = OriginalEntryTests()
        helper.setUp()
        try:
            config, _ = self.model_config(helper, root, prepared, manifest)
            selected = entry.build_uboot_config(config, artifact_root=root / "components")
            self.assertEqual(entry.lifecycle_view(selected)["kernel_release"], manifest["kernel_release"])
            result = helper.boot(selected, mutate=lambda ch: setattr(ch, "kernel_response",
                ("\r\nLinux version " + manifest["kernel_release"] + " (BPI)\r\n").encode()))
            self.assertEqual(result["status"], "kernel-marker-observed")
            self.assertIn("help bootm", helper.channel.commands)
            output = os.environ.get("BPI_LAB_REAL_CM6_REPORT")
            if output:
                target = entry.image.create_directory(output)
                entry.image.save_json(target, "cm6-fit-model.json", {
                    "schema": "bpi-lab-original-entry-fit-model-v1", "status": "model-passed", "simulated": True,
                    "hardware_validated": False, "source_reread": False, "source": prepared["source"],
                    "components": config["components"], "kernel_release": config["kernel_release"],
                    "root_uuid": config["root_uuid"], "files": config["files"], "fit": manifest["checks"]["kernel"]["fit"],
                    "entry": config["entry"], "result": result, "steps": helper.records,
                    "limits": "真 FIT／initrd／DTB 與原 extlinux；RAM／核定／CID／UART 為模型，未在 U-Boot 或硬體執行。"})
        finally:
            helper.doCleanups()

    def test_real_samples_through_backend_and_original_runner(self):
        from tools import bpi_lab_backend as backend
        output = os.environ.get("BPI_LAB_REAL_C3_REPORT")
        if output:
            entry.deploy.new_directory(Path(output))
        reports = []
        for board in ("bpi-m7", "bpi-r3", "bpi-f3"):
            with self.subTest(board=board):
                root, prepared, manifest = self.sample(board)
                helper = OriginalEntryTests()
                helper.setUp()
                try:
                    config, extraction = self.model_config(helper, root, prepared, manifest)
                    template = {k: manifest[k] for k in ("board", "kernel_release", "root_uuid", "entry", "runtime_requirements", "bootargs_template")}
                    template.update(schema=core.TEMPLATE_SCHEMA, pairing_sha256=config["pairing"]["sha256"],
                                    firmware_review_sha256=config["qualification"]["sha256"],
                                    dtb_checks={k: manifest["checks"][k] for k in ("dtb", "overlay_application")})
                    selected = backend.render_family(manifest, template, root / "components", runtime_config=config)
                    bundle = {"board": board, "uboot_qualification": config["qualification"],
                              "transport": {"kind": "mmc-original", "image_paths": {r: manifest["files"][r]["path"] for r in config["files"]}}}
                    selected = backend.bind_runtime(bundle, selected, extraction, config["components"]["extraction"], {"pairing": config["pairing"]})
                    self.assertIs(backend.life.boot_driver(selected), entry)
                    self.assertEqual(backend.life.customer_view(selected)["kernel_release"], manifest["kernel_release"])
                    result = helper.boot(selected, mutate=lambda ch: setattr(ch, "kernel_response", ("\r\nLinux version " + manifest["kernel_release"] + " (BPI)\r\n").encode()))
                    self.assertEqual(result["status"], "kernel-marker-observed")
                    row = {"board": board, "status": "model-passed", "simulated": True, "hardware_validated": False,
                           "source": prepared["source"], "extraction": config["components"]["extraction"],
                           "components": config["components"]["manifest"], "root_uuid": manifest["root_uuid"],
                           "kernel_release": manifest["kernel_release"], "entry": manifest["entry"],
                           "observed_steps": len(helper.records), "result": result,
                           "limits": "使用真組件，RAM／CID／核定／UART 核心標記均為模型；未執行原腳本或 sysboot 的 U-Boot 程式，不是硬體證據。"}
                    reports.append(row)
                    if output:
                        entry.image.save_json(Path(output), board + "-model.json", {**row, "steps": helper.records})
                finally:
                    helper.doCleanups()
        root, prepared, manifest = self.sample("bpi-sm10")
        audit = entry.audit_k3_abi()
        self.assertEqual(manifest["entry"]["kind"], "vendor-env")
        self.assertEqual(audit["status"], "software-blocked")
        if output:
            entry.image.save_json(Path(output), "sm10-software-audit.json", {**audit, "preparation": str(root / "preparation.json"),
                                                                            "extraction_sha256": prepared["extraction"]["sha256"]})
            entry.image.save_json(Path(output), "summary.json", {"schema": "bpi-lab-original-entry-real-components-model-v1",
                "simulated": True, "hardware_validated": False, "source_reread": False, "production_authorization_created": False,
                "samples": reports, "sm10_status": audit["status"], "sm10_software_blockers": audit["software_blockers"]})


if __name__ == "__main__":
    unittest.main()
