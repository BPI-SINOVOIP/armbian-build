#!/usr/bin/env python3
"""K3 離線執行回歸；UART／配對觀測為明示模型，不冒充實板資格。"""

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bpi_lab_extlinux import fixture, RELEASE, UUID, legacy
from test_bpi_lab_uboot import Channel, Clock, console
from tools import bpi_lab_k3_runtime as k3


SD = "11112222333344445555666677778888"
EMMC = "9999aaaabbbbccccddddeeeeffff0000"
BOOT_GUID = "430275bd-a58c-4cf3-88b7-ac89c93fab4c"
ROOT_GUID = "16286dbd-ab93-40a2-a282-59c427d769e8"
BUILD = Path(os.environ.get("BPI_K3_FIRMWARE", str(k3.core.ROOT / "output/evidence/bpi-k3-runtime-build-20260918-002/firmware.json")))
REAL = k3.core.ROOT / "output/evidence/bpi-multiboard-integrate-20260917-sm10-002-replay"


def state(c, lcs="00000000"):
    reserved = c["ram"]["reserved"]
    top = c["ram"]["banks"][0]["start"] + c["ram"]["banks"][0]["size"]
    lines = [f"BPI_K3_STATE abi={k3.ABI} boot_mode=sdcard rsa_verify=0 lcs={lcs}",
             f"ram_base = 0x{c['ram']['banks'][0]['start']:x}", f"ram_top = 0x{top:x}"]
    for i, bank in enumerate(c["ram"]["banks"]):
        lines += [f"DRAM bank = 0x{i:x}", f"-> start = 0x{bank['start']:x}", f"-> size = 0x{bank['size']:x}"]
    lines += [f"relocaddr = 0x{top - 0x100000:x}", f"sp start = 0x{top - 0x200000:x}",
              "fdt_blob = 0x0", "new_fdt = 0x0", "fdt_size = 0x0", "video_bottom = 0x0", "video_top = 0x0",
              f"reserved.count = 0x{len(reserved):x}"]
    for i, span in enumerate(sorted(reserved, key=lambda x: x["start"])):
        lines += [f"reserved[{i}] [0x{span['start']:x}-0x{span['start'] + span['size'] - 1:x}], 0x{span['size']:x} bytes, flags: no-map"]
    return "\r\n".join(lines) + "\r\n"


def cid(kind, capacity=1024**3):
    return f"BPI_K3_MMC kind={kind} device={0 if kind == 'sd' else 2} bytes={capacity:x} cid={SD if kind == 'sd' else EMMC}\r\n"


def gpt(rows):
    lines = [f"BPI_K3_PART index={row['index']} name={row['name'].encode().hex()} guid={row['partuuid']} start={row['start']:x} sectors={row['sectors']:x}" for row in rows]
    return "\r\n".join(lines + [f"BPI_K3_GPT device=2 count={len(rows)}", ""])


def rescue_cpio(release=RELEASE, *, changes=None):
    assets = k3.core.ROOT / "tools/bpi_h618_rescue"
    wrapper = b"#!/usr/bin/python3 -B\nimport bpi_rescue_runtime as runtime\nruntime.SCHEMA = 'bpi-lab-rescue-v1'\nif __name__ == '__main__':\n    raise SystemExit(runtime.main())\n"
    files = {"init": (assets / "init").read_bytes(), "usr/sbin/bpi-rescue": wrapper,
             "usr/sbin/bpi_rescue_runtime.py": (assets / "runtime.py").read_bytes(),
             "usr/sbin/bpi-rescue-ssh": (assets / "ssh-start").read_bytes(),
             "usr/sbin/bpi-rescue-udhcpc": (assets / "udhcpc-script").read_bytes(),
             "usr/sbin/bpi_rescue_cli.py": (assets / "bpi_rescue_cli.py").read_bytes(),
             "usr/bin/busybox": b"fixture", "usr/bin/python3": b"fixture",
             "etc/bpi-rescue.json": k3.deploy.encode({"schema": "bpi-lab-rescue-v1", "kernel": release}),
             "usr/lib/modules/" + release + "/kernel/test.ko": b"fixture"}
    files.update(changes or {})
    data = bytearray()
    for name, body in list(files.items()) + [("TRAILER!!!", b"")]:
        name = name.encode() + b"\0"
        fields = [1, 0o100755, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(name), 0]
        data += b"070701" + "".join(f"{x:08x}" for x in fields).encode() + name
        data += bytes(-len(data) % 4) + body
        data += bytes(-len(data) % 4)
    return gzip.compress(bytes(data), mtime=0)


class K3Channel(Channel):
    """新命令模型自行算載入內容 SHA；不使用命令中的預期摘要充當回應。"""
    def __init__(self, c, files, rows, clock):
        super().__init__(c, {}, clock)
        self.files, self.rows, self.roles = files, rows, {}
        self.pairing = k3.deploy.load(c["pairing"])
        self.prefix = bytes(4 * 1024**2)
        self.env = k3.default_environment()
        self.env.update({"bootdelay": "-1"})
        for key in k3.FIXUP_KEYS:
            self.env.pop(key, None)
        self.kernel_response = ("\r\nLinux version " + c["kernel_release"] + " (BPI)\r\n").encode()

    def write(self, wire):
        if wire.startswith(b"bpi_k3 boot "):
            self.commands.append(wire.decode().strip())
            self.writes.append(wire)
            self.queue.append(wire.replace(b"\n", b"\r\n") + self.kernel_response)
            return len(wire)
        return super().write(wire)

    def hash(self, role):
        blob = self.roles[role]
        return f"BPI_K3_HASH role={role} bytes={len(blob):x} sha256={hashlib.sha256(blob).hexdigest()}\r\n".encode()

    def response(self, command):
        if command in self.overrides:
            return super().response(command)
        p = command.split()
        if p[:2] == ["bpi_k3", "state"]:
            return state(self.config).encode()
        if p[:2] == ["bpi_k3", "mmc"]:
            return cid(p[2], self.pairing["protected_sd" if p[2] == "sd" else "emmc"]["bytes"]).encode()
        if p[:2] == ["bpi_k3", "gpt"]:
            return gpt(self.rows).encode()
        if p[:2] == ["bpi_k3", "envsha"]:
            return f"BPI_K3_ENV_STORAGE device=2 offset=a0000 bytes=4000 sha256={k3.spacemit.DEFAULT_ENV_SHA256}\r\n".encode()
        if p[:2] == ["bpi_k3", "prefix"]:
            return f"BPI_K3_PREFIX device=0 bytes=400000 sha256={hashlib.sha256(self.prefix).hexdigest()}\r\n".encode()
        if p[:2] == ["bpi_k3", "env"]:
            value = self.env.get(p[2])
            data = (value or "").encode()
            return f"BPI_K3_ENV key={p[2]} present={int(value is not None)} bytes={len(data):x} sha256={hashlib.sha256(data).hexdigest()}\r\n".encode()
        if p[:2] == ["bpi_k3", "load"]:
            if int(p[3]) != self.config["source"]["device"] or int(p[4]) != self.config["source"]["partition"]:
                raise AssertionError("載入錯誤的 MMC 分割區")
            self.roles[p[2]] = self.files[p[9]][:int(p[6], 16) + 1]
            return self.hash(p[2])
        if p[:2] == ["bpi_k3", "hash"]:
            return self.hash(p[2])
        if p[:2] == ["bpi_k3", "import"]:
            self.env.update(k3.core.binary._env(self.roles["env"]))
            return self.hash("env")
        if p[0] == "part":
            guid = self.config["source"]["partuuid"] if int(p[3].split(":")[1], 16) == self.config["source"]["partition"] else self.config["root_source"]["partuuid"]
            return (guid + "\r\n").encode()
        if p[0] == "fsuuid":
            return (self.config["root_uuid"] + "\r\n").encode()
        if command.startswith("if test -e mmc "):
            if p[5].removesuffix(";") in self.files:
                self.failures.add(command)
            return b""
        if p[0] == "run":
            if p[1] == "commonargs":
                self.env["bootargs"] = "earlycon=sbi earlyprintk plymouth.ignore-serial-consoles plymouth.prefer-fbcon splash clk_ignore_unused random.trust_bootloader=1"
            elif p[1] == "add_bootarg":
                self.env["bootargs"] += " console=" + self.env["console"] + " loglevel=" + self.env["loglevel"]
            elif p[1] == "set_mmc_args":
                self.env["bootargs"] += " rootwait rootfstype=ext4"
            elif p[1] == "set_root_arg":
                root, boot = self.config["root_source"], self.config["source"]
                self.env.update(rootfs_part=f"0x{root['partition']:x}", bootfs_part=f"0x{boot['partition']:x}",
                                rootfs_guid=root["partuuid"], bootfs_guid=boot["partuuid"])
                self.env["bootargs"] += f" root=PARTUUID={root['partuuid']} bootfs=PARTUUID={boot['partuuid']}"
            elif p[1] != "detect_dtb":
                raise AssertionError("未預期的原環境命令")
            return b""
        return super().response(command)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0
        self.firmware = {key: self.save(key + ".bin", b"model-only") for key in ("binary", "elf", "config", "source", "patch", "environment", "dtb", "build")}
        self.firmware_mock = mock.patch.object(k3, "_firmware")
        self.firmware_mock.start()
        self.addCleanup(self.firmware_mock.stop)

    def save(self, name, value):
        blob = value if isinstance(value, bytes) else k3.deploy.encode(value)
        path = self.root / name
        path.write_bytes(blob)
        return {"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()}

    def qualify(self, c):
        c = k3.validate_config(c)
        pairing = k3.deploy.load(c["pairing"])
        security = {"schema": "bpi-lab-k3-security-v1", "approved": True, "record": "model-security", "hardware_id": c["hardware_id"],
                    "firmware_sha256": self.firmware["binary"]["sha256"], "lcs": "00000000", "external_kernel_authorized": True,
                    "chain_evidence": {key: self.save("chain-" + key + ".bin", b"model-only") for key in ("fsbl", "esos", "sbi", "lifecycle")}}
        self.security = security
        proof = {"schema": "bpi-lab-k3-observation-v1", "hardware_id": c["hardware_id"], "firmware_sha256": self.firmware["binary"]["sha256"],
                 "boot_origin": "sd", "reset": "cold", "version_output": c["uboot"]["version"] + "\r\n", "state_output": state(c),
                 "sd_cid_output": cid("sd", pairing["protected_sd"]["bytes"]),
                 "emmc_cid_output": cid("emmc", pairing["emmc"]["bytes"]) if c["purpose"] == "original" else None}
        self.q = {"schema": k3.QUALIFICATION_SCHEMA, "abi": k3.ABI, "approved": True, "record": "model-qualification",
                  "hardware_id": c["hardware_id"], "purpose": c["purpose"], "scope_sha256": k3.scope_digest(c),
                  "pairing_sha256": c["pairing"]["sha256"], "firmware": self.firmware,
                  "observations": self.save("proof.json", proof), "review": {key: True for key in k3.REVIEWS},
                  "security": self.save("security.json", security), "gpt": self.rows if c["purpose"] == "original" else None,
                  "fixups": {key: None for key in k3.FIXUP_KEYS}}
        c["qualification"] = self.save("qualification.json", self.q)
        c["uboot"]["qualification_sha256"] = c["qualification"]["sha256"]
        return c

    def make(self, purpose="original", *, compressed=False):
        files, module, _ = fixture("bpi-sm10")
        if compressed:
            raw = bytearray(files["/boot/Image"])
            raw += bytes(range(256)) * 4096
            struct.pack_into("<Q", raw, 16, len(raw) + 4096)
            files["/boot/Image"] = files["/boot/vmlinuz-" + RELEASE] = gzip.compress(raw, mtime=0)
        if purpose == "sd-rescue":
            files = {k3.RESCUE_PATHS[key]: files[old] for key, old in
                     (("kernel", "/boot/Image"), ("dtb", "/boot/dtb/spacemit/k3-bananapi-sm10.dtb"), ("kernel_config", "/boot/config-" + RELEASE))}
            files[k3.RESCUE_PATHS["initrd"]] = legacy(rescue_cpio(), "riscv64")
        out = self.root / "components"
        def read(path):
            if path not in files:
                raise FileNotFoundError(path)
            return files[path]
        self.m = (module.prepare(read, board="bpi-sm10", kernel_release=RELEASE, output=out) if purpose == "original" else
                  k3.prepare_rescue(read, kernel_release=RELEASE, root_uuid=UUID, sd_prefix=bytes(4 * 1024**2), output=out))
        self.assertEqual(self.m["status"], "prepared", self.m["blockers"])
        self.rows = [{"index": 1, "name": "bootfs", "partuuid": BOOT_GUID, "start": 24576, "sectors": 524288},
                     {"index": 2, "name": "rootfs", "partuuid": ROOT_GUID, "start": 548864, "sectors": 1048576}]
        bootpart = {key: self.rows[0][key] for key in ("index", "partuuid", "start", "sectors")}
        rootpart = {key: self.rows[1 if purpose == "original" else 0][key] for key in bootpart}
        extraction = {"schema": "bpi-lab-image-v1", "ok": True, "hardware_validated": False, "source_verified": True,
                      "source": "/model/source.img", "source_digest": k3.core.digest(b"model"), "raw": {"bytes": 1024**3, "sha256": "1" * 64},
                      "partition": rootpart, "boot_partition": bootpart, "filesystem_uuid": UUID, "queries": [], "files": {}}
        for i, (path, data) in enumerate(files.items()):
            ref = self.save(f"file-{i}.bin", data)
            extraction["files"][path] = {"file": Path(ref["path"]).name, "digest": k3.core.digest(data),
                                         "volume_index": 1 if path.startswith("/boot/") else rootpart["index"]}
        for i, row in enumerate(self.m["reads"]):
            if row["status"] == "missing":
                blob = (row["path"] + ": File not found by ext2_lookup\n").encode()
                ref = self.save(f"missing-{i}.txt", blob)
                extraction["queries"].append({"command": "stat " + row["path"], "returncode": 0, "stderr_file": Path(ref["path"]).name,
                                             "stderr": k3.core.digest(blob)})
        self.extraction = extraction
        pairing = {"schema": "bpi-lab-pairing-v1", "approved": True, "record": "model-pairing", "hardware_id": "model-k3",
                   "resources": {}, "uart": {}, "power": {}, "rescue": {},
                   "emmc": {"cid": EMMC, "bytes": 1024**3, "controller": "model-emmc"},
                   "protected_sd": {"cid": SD, "bytes": 1024**3, "controller": "model-sd"}}
        ref = self.save("pairing.json", pairing)
        components = {}
        for role, address in (("kernel", 0x140000000), ("initrd", 0x130000000), ("dtb", 0x138000000)):
            item = self.m["files"][role]
            components[role] = {key: item[key] for key in ("bytes", "sha256")}
            components[role].update(path=item["path"][5:], address=address, capacity=0x800000,
                                    format="Image" if role == "kernel" else self.m["initrd_format"] if role == "initrd" else "dtb")
        components["kernel"]["entry"] = 0x102200000
        args = ["earlycon=sbi", "console=ttyS0,115200", "root=/dev/ram0", "rdinit=/init", "ro", "boot_mode=sdcard"]
        if purpose == "original":
            args = [a.replace("${rootfs_guid}", ROOT_GUID).replace("${bootfs_guid}", BOOT_GUID).replace("${boot_mode}", "sdcard") for a in self.m["bootargs_template"]]
        c = {"schema": k3.SCHEMA, "board": "bpi-sm10", "purpose": purpose, "hardware_id": "model-k3", "root_uuid": UUID,
             "arch": "riscv64", "kernel_release": RELEASE, "files": components, "bootargs": args, "fdt_extra": 0x10000,
             "source": {"type": "mmc", "device": 2 if purpose == "original" else 0, "partition": 1, "partuuid": BOOT_GUID},
             "root_source": {"partition": rootpart["index"], "partuuid": rootpart["partuuid"], "uuid": UUID}, "mmc": {"sd": 0, "emmc": 2},
             "uboot": {"prompt": "=> ", "version": "U-Boot 2022.10 (K3)", "address_bits": 64, "line_limit": 2048,
                       "pairing_sha256": ref["sha256"], "qualification_sha256": "b" * 64, "abi": k3.ABI},
             "ram": {"banks": [{"start": k3.RAM_BASE, "size": 0x7e000000}], "reserved": [{"start": 0x170000000, "size": 0x10000000}],
                     "boot": {"start": k3.RAM_BASE, "size": 0x6e000000}, "kernel_work": {"start": 0x102200000, "size": 0x8000000}},
             "entry": {"kind": "vendor-env", "path": "/env_k3.txt", "address": 0x110000000, "capacity": 0x10000,
                       **{key: self.m["files"]["vendor_env"][key] for key in ("bytes", "sha256")}} if purpose == "original" else None,
             "work": None, "decompression": {"address": 0x150000000, "capacity": 0x10000000} if compressed else None,
             "pairing": ref, "qualification": {"path": str(self.root / "qualification.json"), "sha256": "b" * 64},
             "components": {"manifest": {"path": str(out / "manifest.json"), "sha256": hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()},
                            "artifact_root": str(out), "extraction": self.save("extraction.json", extraction)},
             "authorization": {"record": "model-once", "one_shot": True, "customer_boot_may_write_emmc": purpose == "original"}}
        self.media = {path[5:]: blob for path, blob in files.items() if path.startswith("/boot/")}
        self.c = self.qualify(c)
        return self.c

    def boot(self, mutate=None):
        self.count += 1
        clock = Clock()
        channel = K3Channel(self.c, self.media, self.rows, clock)
        self.channel, self.records = channel, []
        if mutate:
            mutate(channel)
        with console.ConsoleSession(channel, log_path=self.root / f"uart-{self.count}.bin", monotonic=clock) as session:
            return k3.boot(session, self.c, self.records, timeout=30, monotonic=clock)


class RuntimeTests(FixtureCase):
    def test_original_actual_components_and_once(self):
        self.make()
        result = self.boot()
        self.assertEqual(result["status"], "kernel-marker-observed")
        self.assertFalse(result["hardware_validated"])
        self.assertEqual(self.channel.commands[-1], "bpi_k3 boot original")
        self.assertTrue(any(cmd == "run set_root_arg" for cmd in self.channel.commands))
        self.assertFalse(any(re.search(r"\b(saveenv|write|erase|sysboot|source)\b", cmd) for cmd in self.channel.commands))

    def test_fixed_sd_rescue_does_not_read_emmc(self):
        self.make("sd-rescue")
        self.assertEqual(self.boot()["purpose"], "sd-rescue")
        self.assertFalse(any("emmc" in cmd or "envsha" in cmd or cmd == "bpi_k3 import" for cmd in self.channel.commands))
        self.assertEqual(self.channel.commands[-1], "bpi_k3 boot sd-rescue")
        self.assertEqual(k3.lifecycle_view(self.c)["uboot"]["abi"], k3.ABI)

    def test_sd_prefix_content_not_claim(self):
        self.make("sd-rescue")
        with self.assertRaisesRegex(ValueError, "SD 保護前綴"):
            self.boot(lambda channel: setattr(channel, "prefix", b"x" + channel.prefix[1:]))
        self.assertFalse(any(cmd.startswith("bpi_k3 load") for cmd in self.channel.commands))

    def test_legacy_rescue_schema_rejected(self):
        identity = k3.deploy.encode({"schema": "bpi-h618-rescue-v1", "kernel": RELEASE})
        with self.assertRaisesRegex(ValueError, "拒絕舊 H618"):
            k3._rescue_archive(rescue_cpio(changes={"etc/bpi-rescue.json": identity}), RELEASE)

    def test_rescue_wrapper_and_module_are_checked(self):
        for changes in ({"usr/sbin/bpi_rescue_runtime.py": b"pass\n"},
                        {"usr/sbin/bpi-rescue": b"#!/usr/bin/python3 -B\nimport os; os.system('mount /dev/mmcblk2 /mnt')\n"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                k3._rescue_archive(rescue_cpio(changes=changes), RELEASE)

    def test_actual_main_builder_wrapper_with_chinese_docstring(self):
        from tools import build_bpi_h618_rescue as builder
        seed = self.root / "builder-seed"
        profile = {"schema": "bpi-lab-rescue-build-profile-v1", "board": "bpi-sm10", "architecture": "riscv64",
                   "multiarch": "riscv64-linux-gnu", "dt_compatible": ["spacemit,k3"],
                   "modules": ["virtio_mmio"], "firmware": [], "wireless": False}
        builder.prepare_runtime(seed, RELEASE, profile=profile)
        names = ("etc/bpi-rescue.json", "usr/sbin/bpi-rescue", "usr/sbin/bpi_rescue_runtime.py")
        changes = {name: (seed / name).read_bytes() for name in names}
        self.assertIn("使用獨立板級救援識別".encode(), changes["usr/sbin/bpi-rescue"])
        actual = k3._rescue_archive(rescue_cpio(changes=changes), RELEASE)
        self.assertEqual(actual, {"schema": "bpi-lab-rescue-v1", "kernel": RELEASE,
                                  "identity_sha256": hashlib.sha256(changes["etc/bpi-rescue.json"]).hexdigest()})

    def test_bss_source_and_decompression_region(self):
        self.make()
        c = copy.deepcopy(self.c)
        c["ram"]["banks"][0]["start"] = 0x100000000
        c["ram"]["banks"][0]["size"] += 0x2000000
        with self.assertRaisesRegex(ValueError, "ESOS"):
            k3.validate_config(c)

    def test_external_manifest_claims_not_trusted(self):
        self.make()
        self.m["bootargs_template"] += ["init=/bin/sh"]
        self.c["components"]["manifest"] = self.save("components/manifest.json", self.m)
        self.c = self.qualify(self.c)
        with self.assertRaisesRegex(ValueError, "宣告與內容不同"):
            self.boot()
        self.assertEqual(self.channel.commands, [])

    def test_no_mainline_or_spl_mmc_fallback(self):
        self.make()
        for key, value in (("mmc", {"sd": 0, "emmc": 1}), ("board", "bpi-f3"), ("purpose", "fallback")):
            c = {**self.c, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                k3.validate_config(c)

    def test_changed_file_hash_stops_before_boot(self):
        self.make()
        self.media["/Image"] = self.media["/Image"][:-1] + b"x"
        with self.assertRaisesRegex(ValueError, "SHA-256 不符"):
            self.boot()
        self.assertFalse(any(cmd.startswith("bpi_k3 boot") for cmd in self.channel.commands))

    def test_load_overlength_not_accepted(self):
        self.make()
        self.media["/Image"] += b"x"
        with self.assertRaises(ValueError):
            self.boot()

    def test_full_cid_mismatch(self):
        self.make()
        for kind in ("sd", "emmc"):
            command = "bpi_k3 mmc " + kind + (" 0" if kind == "sd" else " 2")
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "完整 CID"):
                self.boot(lambda channel: channel.overrides.update({command: cid(kind).replace(SD if kind == "sd" else EMMC, "0" * 32).encode()}))

    def test_gpt_name_guid_or_count_mismatch(self):
        self.make()
        for raw in (gpt(self.rows).replace("626f6f746673", "626f6f74"), gpt(self.rows).replace("count=2", "count=3"), gpt(self.rows).replace(BOOT_GUID, UUID)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.boot(lambda channel: channel.overrides.update({"bpi_k3 gpt 2": raw.encode()}))

    def test_persistent_environment_content_not_digest_claim(self):
        self.make()
        with self.assertRaisesRegex(ValueError, "持久環境"):
            self.boot(lambda channel: channel.overrides.update({"bpi_k3 envsha 2": b"BPI_K3_ENV_STORAGE device=2 offset=a0000 bytes=4000 sha256=" + b"0" * 64 + b"\r\n"}))

    def test_env_command_and_fixup_mutation(self):
        self.make()
        for key in ("commonargs", "wifi_addr", "part#"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "實際環境"):
                self.boot(lambda channel: channel.env.update({key: "changed"}))

    def test_secure_state_no_zero_inference(self):
        self.make()
        with self.assertRaisesRegex(ValueError, "生命週期"):
            self.boot(lambda channel: channel.overrides.update({"bpi_k3 state": state(self.c, "00000001").encode()}))
        self.security["external_kernel_authorized"] = False
        self.q["security"] = self.save("security.json", self.security)
        self.c["qualification"] = self.save("qualification.json", self.q)
        self.c["uboot"]["qualification_sha256"] = self.c["qualification"]["sha256"]
        with self.assertRaisesRegex(ValueError, "安全鏈"):
            self.boot()
        self.assertEqual(self.channel.commands, [])

    def test_lmb_count_and_staging_overlap(self):
        self.make()
        for output in (state(self.c).replace("reserved.count = 0x1", "reserved.count = 0x2"),
                       state(self.c).replace("reserved.count = 0x1", "reserved.count = 0x2").replace("reserved[0]", "reserved[1]") +
                       "reserved[0] [0x140000000-0x140000fff], 0x1000 bytes, flags: none\r\n"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                k3._memory(output.encode(), self.c, "00000000")

    def test_framebuffer_must_be_reserved(self):
        self.make()
        output = state(self.c).replace("video_bottom = 0x0", "video_bottom = 0x140000000").replace("video_top = 0x0", "video_top = 0x140001000")
        with self.assertRaisesRegex(ValueError, "framebuffer"):
            k3._memory(output.encode(), self.c, "00000000")

    def test_esp_grub_not_silently_skipped(self):
        self.make()
        self.media["/EFI/BOOT/BOOTRISCV64.EFI"] = b"model-grub"
        with self.assertRaises(ValueError):
            self.boot()
        self.assertFalse(any(cmd.startswith("bpi_k3 boot") for cmd in self.channel.commands))

    def test_rescue_content_must_include_fixed_init(self):
        for changes in ({"init": b"#!/bin/sh\nmount /dev/mmcblk2 /mnt\n"}, {"etc/bpi-rescue.json": b"{}"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                k3._rescue_archive(rescue_cpio(changes=changes), RELEASE)

    def test_rescue_second_archive_rejected(self):
        with self.assertRaises(ValueError):
            k3._rescue_archive(rescue_cpio() + rescue_cpio(), RELEASE)

    def test_kernel_return_prompt_is_not_success(self):
        self.make()
        with self.assertRaisesRegex(ValueError, "返回 U-Boot"):
            self.boot(lambda channel: setattr(channel, "kernel_response", b"\r\n=> "))

    def test_qualification_scope_and_api(self):
        self.make()
        self.assertEqual(k3.build_uboot_config(self.c, artifact_root=self.c["components"]["artifact_root"]), k3.validate_config(self.c))
        self.assertTrue(k3.backend_binding(self.c)["requires_runtime_dispatch"])
        self.c["authorization"]["record"] = "different"
        with self.assertRaisesRegex(ValueError, "資格未綁定"):
            k3.validate_artifacts(self.c)


@unittest.skipUnless(BUILD.is_file(), "本機未提供真 K3 BSP 建置；模型測試不代替編譯")
class BuiltBspTests(FixtureCase):
    def real_firmware(self):
        self.firmware_mock.stop()
        self.firmware = json.loads(BUILD.read_text())
        blob = k3.deploy.checked_bytes(self.firmware["binary"], 32 * 1024**2)
        self.c["uboot"]["version"] = re.search(rb"U-Boot 2022\.10[^\0\r\n]*", blob)[0].decode()
        self.c = self.qualify(self.c)

    def test_real_bsp_elf_link_and_original_model(self):
        self.make()
        self.real_firmware()
        self.assertEqual(self.boot()["status"], "kernel-marker-observed")

    def test_real_bsp_sd_rescue_model(self):
        self.make("sd-rescue")
        self.real_firmware()
        self.assertEqual(self.boot()["purpose"], "sd-rescue")

    def test_forged_binary_cannot_self_certify(self):
        self.make()
        self.real_firmware()
        self.firmware["binary"] = self.save("forged.bin", b"bpi_k3 spacemit-k3-lab-v1")
        with self.assertRaisesRegex(ValueError, "固定建置"):
            k3._firmware(self.firmware, self.c)

    @unittest.skipUnless((REAL / "extraction/extraction.json").is_file(), "本機沒有已成功擷取的真 SM10 證據")
    def test_real_sm10_snapshot_with_real_bsp_runner(self):
        self.make()
        m = json.loads((REAL / "components/manifest.json").read_bytes())
        ref = {"path": str(REAL / "extraction/extraction.json"), "sha256": "90c9b1ac05c83a988849190b1d8fbeff3f0bde47ede9b902c8b6797dab0bcb59"}
        require_blob = k3.deploy.checked_bytes(ref)
        extracted = json.loads(require_blob)
        self.c["components"] = {"manifest": {"path": str(REAL / "components/manifest.json"),
                                               "sha256": hashlib.sha256((REAL / "components/manifest.json").read_bytes()).hexdigest()},
                                 "artifact_root": str(REAL / "components"), "extraction": ref}
        self.c["kernel_release"] = m["kernel_release"]
        self.c["root_uuid"] = self.c["root_source"]["uuid"] = m["root_uuid"]
        self.c["bootargs"] = [a.replace("${rootfs_guid}", ROOT_GUID).replace("${bootfs_guid}", BOOT_GUID).replace("${boot_mode}", "sdcard") for a in m["bootargs_template"]]
        self.c["decompression"] = {"address": 0x150000000, "capacity": 0x10000000}
        for role, item in self.c["files"].items():
            item.update({key: m["files"][role][key] for key in ("bytes", "sha256")})
            item["capacity"] = 0x4000000 if role == "kernel" else 0x2000000 if role == "initrd" else 0x800000
        self.c["entry"].update({key: m["files"]["vendor_env"][key] for key in ("bytes", "sha256")})
        pairing = k3.deploy.load(self.c["pairing"])
        pairing["emmc"]["bytes"] = 4 * 1024**3
        self.c["pairing"] = self.save("pairing.json", pairing)
        self.c["uboot"]["pairing_sha256"] = self.c["pairing"]["sha256"]
        for row, part in zip(self.rows, (extracted["boot_partition"], extracted["partition"])):
            row.update(start=part["start_lba"], sectors=part["sectors"])
        self.media = {item["path"][5:]: (REAL / "components" / item["evidence_path"]).read_bytes()
                      for item in m["files"].values() if item.get("path", "").startswith("/boot/")}
        self.real_firmware()
        result = self.boot()
        self.assertEqual(result["status"], "kernel-marker-observed")
        self.assertEqual(result["kernel_release"], "6.18.3-current-spacemit-k3-bpi")
        if os.environ.get("BPI_K3_EVIDENCE"):
            out = Path(os.environ["BPI_K3_EVIDENCE"]).absolute()
            out.mkdir(parents=True, exist_ok=False)
            k3.image.save_json(out, "result.json", {**result, "model": True, "source_extraction": ref,
                                                   "source_components": self.c["components"]["manifest"],
                                                   "firmware": self.firmware, "commands": len(self.records)})
            k3.image.save_json(out, "records.json", self.records)


def make_fixture(root, *, purpose="original", firmware=BUILD):
    """供共用後端整合測試：使用真 ELF 守門，只有媒體／UART／核定觀測是模型。"""
    case = FixtureCase()
    case.root, case.count = Path(root), 0
    case.root.mkdir(parents=True, exist_ok=True)
    case.firmware = json.loads(Path(firmware).read_bytes())
    case.make(purpose)
    blob = k3.deploy.checked_bytes(case.firmware["binary"], 32 * 1024**2)
    case.c["uboot"]["version"] = re.search(rb"U-Boot 2022\.10[^\0\r\n]*", blob)[0].decode()
    case.c = case.qualify(case.c)
    k3.validate_artifacts(case.c)
    return case


if __name__ == "__main__":
    unittest.main()
