"""六板原配解析與離線配置的正負例；不需要映像解壓或硬體。"""

import contextlib
import copy
import gzip
import io
import json
import lzma
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zlib

from tools import bpi_lab_special as special


UUID = "12345678-1234-1234-1234-123456789abc"
VERSIONS = {"sunplus": "5.4.35-legacy-sunplus-sp7021-bpi", "renesas": "6.1.102-legacy-bpi-rzv2n",
            "synaptics": "5.4.0-legacy-vs680", "bpi-m4": "4.9.119-legacy-realtek-rtd139x-bpi",
            "bpi-w2": "4.9.119-legacy-realtek-rtd129x-bpi"}


def release(board):
    return VERSIONS.get(board, VERSIONS.get(special.PROFILES[board]["group"]))


def legacy(payload, *, kind=3, arch=22, compression=1, version=""):
    address = 0x300000 if kind == 2 else 0
    name = ("Linux-" + version).encode() if kind == 2 else b"test"
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), address, address,
                         zlib.crc32(payload), 5, arch, kind, compression, name)
    return header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:] + payload


def image(version):
    data = bytearray(4096)
    struct.pack_into("<3Q", data, 8, 0x80000, 8192, 8)
    data[56:60] = b"ARM\x64"
    banner = f"Linux version {version} (test)\0".encode()
    data[128:128 + len(banner)] = banner
    return bytes(data)


def zimage(version):
    data = bytearray(256)
    data.extend(gzip.compress(f"Linux version {version} (test)\0".encode(), mtime=0))
    data[36:40] = bytes.fromhex("18286f01")
    struct.pack_into("<II", data, 40, 0, len(data))
    return bytes(data)


def cpio(items):
    data = bytearray()
    for index, (name, content) in enumerate([*items, ("TRAILER!!!", b"")]):
        name = name.encode() + b"\0"
        fields = [index, 0o100755 if name.rstrip(b"\0") in (b"init", b"./init") else 0o100644,
                  0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name), 0]
        data.extend(b"070701" + "".join(f"{value:08x}" for value in fields).encode() + name)
        data.extend(bytes(-len(data) % 4))
        data.extend(content)
        data.extend(bytes(-len(data) % 4))
    return bytes(data)


def initrd(version, arch=22, *, compression="gzip", extra=()):
    data = cpio([("init", b"test"), (f"lib/modules/{version}/modules.dep", b"test.ko:\n"), *extra])
    if compression == "gzip":
        data = gzip.compress(data, mtime=0)
    elif compression == "xz":
        data = lzma.compress(data)
    return legacy(data, arch=arch, compression=0 if compression == "none" else 1)


def dtb(board, *, reserve="", extra="", memory_size=0x80000000):
    profile = special.PROFILES[board]
    compatible = ", ".join(json.dumps(value) for value in profile["compatible"])
    if board == "bpi-ai2n":
        extra += ('reserved-memory { #address-cells = <2>; #size-cells = <2>; ranges; '
                  'opencva@a8000000 { reg = <0 0xa8000000 0 0x7cff000>; }; '
                  'codec@afd00000 { reg = <0 0xafd00000 0 0x300000>; }; };')
    if board in ("bpi-m4", "bpi-w2"):
        extra += f'memory@0 {{ device_type = "memory"; reg = <0 0 0 0x{memory_size:x}>; }};'
    source = (f'/dts-v1/; {reserve} / {{ #address-cells = <2>; #size-cells = <2>; '
              f'compatible = {compatible}; model = {json.dumps(profile["model"])}; {extra} }};')
    return subprocess.run(["/usr/bin/dtc", "-q", "-I", "dts", "-O", "dtb"], input=source.encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10).stdout


def env_bytes(values):
    return "".join(key + "=" + value + "\n" for key, value in values.items()).encode()


def realtek_env(board):
    env = {"bpi": "bananapi", "board": board, "chip": "RTD1395" if board == "bpi-m4" else "RTD1296",
           "service": "linux", "kernel": "uImage", "kernel_loadaddr": "0x03000000",
           "audio_loadaddr": "0x0f900000", "blue_logo_loadaddr": "0x30000000", "fdt_loadaddr": "0x02100000",
           "rootfs_loadaddr": "0x31400000", "root": "LABEL=BPI-ROOT rw rootfstype=ext4 rootwait",
           "abootargs": "setenv bootargs board=${board} console=${console} root=${root} fsck.mode=force fsck.repair=yes service=${service} sdmmc_on=${sdmmc_on} ${bootopts}",
           "ahello": "echo Banana Pi ${board} chip: $chip Service: $service",
           "aload_kernel": "fatload $device $partition ${kernel_loadaddr} ${bpi}/${board}/${service}/${kernel}"}
    if board == "bpi-m4":
        env.update(rootfs="uInitrd", audio="bluecore.audio", aboot="go all;", bootopts="loglevel=8 initcall_debug=0",
                   console="earlycon=uart8250,mmio32,0x98007800 console=tty1 fbcon=map:0 console=ttyS0,115200",
                   aload_dtb="if test $dram_size = 1GB; then setenv dtb rtd-1395-bananapi-m4-1GB.dtb; fi; if test $dram_size = 2GB; then setenv dtb rtd-1395-bananapi-m4-2GB.dtb; fi; fatload $device $partition ${fdt_loadaddr} ${bpi}/${board}/${service}/${dtb}",
                   aload_rootfs="fatload $device $partition ${rootfs_loadaddr} ${bpi}/${board}/${service}/${rootfs}",
                   aload_audio="fatload $device $partition ${audio_loadaddr} ${bpi}/${board}/${service}/${audio}",
                   uenvcmd="run ahello abootargs aload_dtb aload_kernel aload_rootfs aload_audio aboot",
                   usercmd="run ahello abootargs")
    else:
        env.update(sd_boot_dtb="bananapi/bpi-w2/linux/rtd-1296-bananapi-w2-2GB.dtb",
                   sd_boot_rootfs="bananapi/bpi-w2/linux/uInitrd", sd_vmlinux="bananapi/bpi-w2/linux/uImage",
                   sd_audio="bananapi/bpi-w2/linux/bluecore.audio", aboot="gosd;", bootopts="loglevel=7 initcall_debug=0",
                   console="earlycon=uart8250,mmio32,0x98007800 fbcon=map:0 console=ttyS0,115200",
                   uenvcmd="run ahello abootargs aboot")
    return env


def fixture(board):
    p, version = special.PROFILES[board], release(board)
    vendor = p["group"] in ("sunplus", "realtek")
    base = f"/boot/bananapi/{board}/linux/" if vendor else "/boot/"
    kernel = legacy(zimage(version), kind=2, arch=2, compression=0, version=version) if p["arch"] == "arm32" else image(version)
    values = {"BOARD": p["artifact"], "BOARDFAMILY": p["family"], "KERNEL_VERSION": version}
    files = {"/etc/armbian-release": env_bytes(values), base + ("uImage" if vendor else "Image"): kernel,
             base + "uInitrd": initrd(version, 2 if p["arch"] == "arm32" else 22)}
    for name in p["dtbs"]:
        files[base + ("" if vendor else "dtb/") + name] = dtb(board, memory_size=0x40000000 if name.endswith("-1GB.dtb") else 0x80000000)
    if p["group"] == "sunplus":
        files["/boot/uEnv.txt"] = env_bytes(special._sunplus_env(board, UUID))
    elif p["group"] == "realtek":
        files["/boot/uEnv.txt"] = files[base + "uEnv.txt"] = env_bytes(realtek_env(board))
        files[base + "bluecore.audio"] = b"\x01" * 256
        files["/etc/fstab"] = ("UUID=" + UUID + " / ext4 defaults 0 1\n").encode()
    else:
        env = {"rootdev": "UUID=" + UUID, "fdtfile": p["dtbs"][0]}
        if board == "bpi-ai2n":
            env.update(board=board, debug_uart="ttySC0")
            files.update({"/boot/OpenCV_Bin.bin": b"\x01" * 128, "/boot/Codec_Bin.bin": b"\x02" * 128})
        files["/boot/armbianEnv.txt"] = env_bytes(env)
        script = (special.ROOT / "config/bootscripts" / p["script"]).read_bytes()
        files["/boot/boot.cmd"] = script
        files["/boot/boot.scr"] = legacy(struct.pack(">II", len(script), 0) + script, kind=6, arch=2, compression=0)
    return files


def template(board):
    result = {"board": board, "kernel_release": release(board),
              "source": {"type": "mmc", "device": 0, "partition": 1, "partuuid": "12345678-01"},
              "ram": {"banks": [{"start": 0, "size": 0xc0000000}],
                      "reserved": [{"start": 0x100000, "size": 0x100000}],
                      "kernel_work": {"start": 0x300000, "size": 0x1000000}},
              "addresses": {"kernel": 0x300000, "initrd": 0x3000000, "dtb": 0x4000000},
              "bindings": {"board": board, "sdmmc_on": "1"}}
    if board == "bpi-m6":
        result["addresses"] = {"kernel": 0x04a80000, "initrd": 0x0ca00000, "dtb": 0x15a00000}
        result["ram"]["kernel_work"] = {"start": 0x04a80000, "size": 0x1000000}
        result["bindings"] = {}
    elif board == "bpi-ai2n":
        result["addresses"].update(opencva=0xa8000000, codec=0xafd00000)
        result["bindings"] = {"ethaddr": "02:00:00:00:00:01", "eth1addr": "02:00:00:00:00:02",
                              "serial": "1234", "chipid": "12345678123412341234123456789abc",
                              "ocaaddr": "0xa8000000", "codaddr": "0xafd00000",
                              "ocabin": "OpenCV_Bin.bin", "codbin": "Codec_Bin.bin"}
    elif board in ("bpi-m4", "bpi-w2"):
        result["addresses"] = {"kernel": 0x03000000, "initrd": 0x31400000, "dtb": 0x02100000, "audio": 0x0f900000}
        result["ram"]["banks"] = [{"start": 0, "size": 0x80000000}]
        result["ram"]["kernel_work"] = {"start": 0x80000, "size": 0x1000000}
        result["ram"]["reserved"] = [{"start": 0, "size": 0x80000}]
        result["source"] = {"type": "vendor-fat", "device": "sd", "partition": "0:1", "partuuid": "12345678-01",
                            "filesystem": "vfat", "identity_sha256": "a" * 64}
        result["vendor"] = {"dram_size": "2GB", "secure_mode": "non-secure", "hyp_loadaddr": "",
                            "root_identity": {"uuid": UUID, "label": "BPI-ROOT", "filesystem": "ext4", "evidence_sha256": "b" * 64}}
    return result


def desktop_bootargs(board, *, bootlogo="false", console="both", verbosity="1", bindings=None):
    """依兩板固定腳本列出期望參數，不呼叫待測解析器產生答案。"""
    if board == "bpi-ai2n":
        args = ["root=UUID=" + UUID, "rootwait", "rootfstype=ext4"]
        args += ["splash", "plymouth.ignore-serial-consoles"] if bootlogo == "true" else ["splash=verbose"]
        args += ["console=ttySC0,115200"]
        if console in ("both", "display"):
            args += ["console=tty1"]
        args += ["consoleblank=0", "loglevel=" + verbosity, "fsck.mode=force", "fsck.repair=yes",
                 "net.ifnames=0", "board=bpi-ai2n", "ethaddr=${ethaddr}", "eth1addr=${eth1addr}",
                 "serialno=${serial}", "systemd.machine_id=${chipid}"]
    elif board == "bpi-m6":
        args = ["console=ttyS0,115200n8", "console=tty1", "rootfstype=ext4", "root=UUID=" + UUID,
                "rw", "rootwait", "board=bpi-m6", "loglevel=" + verbosity, "tz_enable", "vppta",
                "chipid=43111a82aee08964", "cma=343932928@1509949440"]
    else:
        raise ValueError("桌面測資只涵蓋 AI2N 與 M6")
    for key, value in (bindings or {}).items():
        args = [arg.replace("${" + key + "}", value) for arg in args]
    return args


class KernelTests(unittest.TestCase):
    def test_uimage_entry_must_be_within_loaded_payload_and_aligned(self):
        version = release("bpi-f2s")
        original = legacy(zimage(version), kind=2, arch=2, compression=0, version=version)
        for entry in (0x800000, 0x300000 + len(original) - 64, 0x2ffffc, 0x300001):
            changed = bytearray(original)
            struct.pack_into(">I", changed, 20, entry)
            struct.pack_into(">I", changed, 4, 0)
            struct.pack_into(">I", changed, 4, zlib.crc32(changed[:64]))
            with self.subTest(entry=entry), self.assertRaisesRegex(special.SpecialError, "入口"):
                special.validate_kernel(bytes(changed), arch="arm32")

    def test_initramfs_requires_executable_regular_init_across_segments(self):
        version = "5.4.35"
        modules = cpio([(f"lib/modules/{version}/modules.dep", b"test.ko:\n")])
        for payload in (modules, cpio([("init", b"")]) + modules):
            with self.assertRaisesRegex(special.SpecialError, "/init"):
                special.validate_initrd(legacy(gzip.compress(payload, mtime=0), arch=2), arch="arm32", kernel_release=version)
        for mode in (0o100644, 0o040755, 0o120777):
            archive = bytearray(cpio([("init", b"/bin/sh")]))
            archive[14:22] = f"{mode:08x}".encode()
            for payload in (bytes(archive) + modules, cpio([("init", b"valid")]) + bytes(archive) + modules):
                with self.assertRaisesRegex(special.SpecialError, "/init"):
                    special.validate_initrd(legacy(gzip.compress(payload, mtime=0), arch=2), arch="arm32", kernel_release=version)
        payload = modules + gzip.compress(cpio([("./init", b"#!/bin/sh\n")]), mtime=0)
        self.assertEqual(special.validate_initrd(legacy(payload, arch=2, compression=0), arch="arm32", kernel_release=version)["module_releases"], [version])
    def test_realtek_named_uimage_is_raw_image(self):
        for board in ("bpi-m4", "bpi-w2"):
            result = special.validate_kernel(image(release(board)), arch="arm64")
            self.assertEqual(result["format"], "Image")
            self.assertEqual(result["kernel_release"], release(board))

    def test_sunplus_embedded_release_and_legacy(self):
        version = release("bpi-f2s")
        blob = legacy(zimage(version), kind=2, arch=2, compression=0, version=version)
        result = special.validate_kernel(blob, arch="arm32")
        self.assertEqual(result["kernel_release"], version)
        self.assertEqual(result["payload_format"], "zImage")
        self.assertTrue(result["data_crc_verified"])

    def test_crc_and_truncation_rejected(self):
        good = legacy(zimage(release("bpi-f2s")), kind=2, arch=2, compression=0, version=release("bpi-f2s"))
        for value in (good[:32], good[:-1], good[:4] + b"bad!" + good[8:], good[:-1] + bytes([good[-1] ^ 1])):
            with self.subTest(value=value[:8]), self.assertRaises(special.SpecialError):
                special.validate_kernel(value, arch="arm32")

    def test_unknown_vendor_container_and_wrong_arch_rejected(self):
        for data, arch in ((b"uImage" * 100, "arm64"), (image("6.1.1"), "arm32"), (zimage("5.4.35"), "arm64")):
            with self.subTest(arch=arch), self.assertRaises(special.SpecialError):
                special.validate_kernel(data, arch=arch)

    def test_banner_missing_or_ambiguous(self):
        for data in (image("0"), image("6.1.1") + b"Linux version 6.1.2 (test)", zimage("0")):
            with self.assertRaises(special.SpecialError):
                special.validate_kernel(data, arch="arm32" if data[36:40] == bytes.fromhex("18286f01") else "arm64")

    def test_image_size_and_endianness(self):
        for size, flags in ((1, 8), (8192, 1), (special.MAX_EXPANDED + 1, 8)):
            data = bytearray(image("6.1.1"))
            struct.pack_into("<QQ", data, 16, size, flags)
            with self.assertRaises(special.SpecialError):
                special.validate_kernel(bytes(data), arch="arm64")

    def test_initrd_all_supported_compressions(self):
        for compression in ("gzip", "xz", "none"):
            for arch in ("arm32", "arm64"):
                with self.subTest(compression=compression, arch=arch):
                    result = special.validate_initrd(initrd("5.4.35", 2 if arch == "arm32" else 22, compression=compression),
                                                     arch=arch, kernel_release="5.4.35")
                    self.assertEqual(result["module_releases"], ["5.4.35"])

    def test_initrd_version_mixed_path_escape_and_wrong_arch(self):
        for data in (initrd("5.4.36"), initrd("5.4.35", extra=[("lib/modules/6.1.1/test.ko", b"x")]),
                     initrd("5.4.35", extra=[("../outside", b"x")]), initrd("5.4.35", arch=2)):
            with self.assertRaises(ValueError):
                special.validate_initrd(data, arch="arm64", kernel_release="5.4.35")

    def test_initrd_expansion_limit(self):
        with mock.patch.object(special, "MAX_EXPANDED", 100), self.assertRaises(ValueError):
            special.validate_initrd(initrd("5.4.35"), arch="arm64", kernel_release="5.4.35")


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bpi-special-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.number = 0

    def prepare(self, board="bpi-f2s", *, files=None, requested=None):
        self.number += 1
        self.output = self.root / str(self.number)
        files = fixture(board) if files is None else files
        def read(path):
            if path not in files:
                raise FileNotFoundError(path)
            return files[path]
        return special.prepare(read, board=board, kernel_release=release(board) if requested is None else requested, output=self.output)

    def blocked(self, manifest):
        self.assertEqual(manifest["status"], "blocked", manifest)
        self.assertTrue(manifest["blockers"])
        self.assertFalse(manifest["hardware_validated"])

    def assert_desktop_environment(self, board, environment):
        files = fixture(board)
        expected_env = {"rootdev": "UUID=" + UUID, "fdtfile": special.PROFILES[board]["dtbs"][0]}
        if board == "bpi-ai2n":
            expected_env.update(board=board, debug_uart="ttySC0")
        expected_env.update(environment)
        files["/boot/armbianEnv.txt"] += env_bytes(environment)
        original = files["/boot/armbianEnv.txt"]
        result = self.prepare(board, files=files)
        self.assertEqual(result["environment"], expected_env)
        self.assertEqual((self.output / "files/boot/armbianEnv.txt").read_bytes(), original)
        self.assertEqual(result["status"], "prepared", result["blockers"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["bootargs_template"], desktop_bootargs(board, **environment))
        self.assertEqual(result["root_uuid"], UUID)
        self.assertEqual(special.validate(self.output), result)
        t = template(board)
        before = copy.deepcopy(t)
        config = special.bootconfig(self.output, template=t)
        self.assertEqual(config["bootargs"], desktop_bootargs(board, **environment, bindings=t["bindings"]))
        self.assertEqual(config["boot_command"], "booti " + " ".join(
            f"{t['addresses'][role]:x}" for role in ("kernel", "initrd", "dtb")))
        self.assertEqual(t, before)
        self.assertEqual(config["status"], "validated_offline")
        self.assertTrue(config["boot_config_validated"])
        self.assertTrue(result["qualification_blockers"])
        self.assertEqual(config["qualification_blockers"], result["qualification_blockers"])
        self.assertFalse(result["hardware_validated"] or result["execution_ready"])
        self.assertFalse(config["hardware_validated"] or config["execution_ready"] or config["executed"])
        self.assertEqual(result["environment"], expected_env)
        self.assertEqual(files["/boot/armbianEnv.txt"], original)
        self.assertEqual((self.output / "files/boot/armbianEnv.txt").read_bytes(), original)

    def test_desktop_twelve_combinations_preserve_environment_and_exact_bootargs(self):
        for board in ("bpi-ai2n", "bpi-m6"):
            for bootlogo in ("false", "true"):
                for console in ("serial", "both", "display"):
                    with self.subTest(board=board, bootlogo=bootlogo, console=console):
                        self.assert_desktop_environment(board, {
                            "bootlogo": bootlogo, "console": console, "verbosity": "8"})

    def test_desktop_defaults_preserve_absent_environment_keys(self):
        for board in ("bpi-ai2n", "bpi-m6"):
            for environment in ({}, {"bootlogo": "false"}, {"bootlogo": "true"},
                                {"console": "serial"}, {"console": "both"}, {"console": "display"}):
                with self.subTest(board=board, environment=environment):
                    self.assert_desktop_environment(board, environment)

    def test_desktop_invalid_values_rejected(self):
        invalid = {"bootlogo": ("", "on", "1", "True", "FALSE", "false; reset", "$(reset)"),
                   "console": ("", "none", "tty1", "Both", "DISPLAY", "both; reset", "${console}")}
        for board in ("bpi-ai2n", "bpi-m6"):
            for key, values in invalid.items():
                for value in values:
                    with self.subTest(board=board, key=key, value=value):
                        files = fixture(board)
                        files["/boot/armbianEnv.txt"] += env_bytes({"bootlogo": "false", "console": "both", key: value})
                        result = self.prepare(board, files=files)
                        self.blocked(result)
                        self.assertEqual([item["stage"] for item in result["blockers"]], ["environment"])

    def test_desktop_duplicate_keys_rejected(self):
        for board in ("bpi-ai2n", "bpi-m6"):
            for key, first, second in (("bootlogo", "false", "false"), ("bootlogo", "false", "true"),
                                       ("console", "both", "both"), ("console", "both", "serial")):
                with self.subTest(board=board, key=key, second=second):
                    files = fixture(board)
                    files["/boot/armbianEnv.txt"] += env_bytes({key: first}) + env_bytes({key: second})
                    result = self.prepare(board, files=files)
                    self.blocked(result)
                    self.assertEqual(result["blockers"], [{"stage": "environment", "reason": "設定鍵重複：" + key}])

    def test_all_six_boards_and_replay(self):
        for board in special.PROFILES:
            with self.subTest(board=board):
                result = self.prepare(board)
                self.assertEqual(result["status"], "prepared", result["blockers"])
                self.assertFalse(result["execution_ready"])
                self.assertFalse(result["hardware_validated"])
                self.assertTrue(result["qualification_blockers"])
                self.assertEqual(special.validate(self.output), result)
                self.assertEqual(result["root_uuid"], None if board in ("bpi-m4", "bpi-w2") else UUID)

    def test_sunplus_zero_resolved_independently(self):
        for board in ("bpi-f2p", "bpi-f2s"):
            files = fixture(board)
            rel = special.assignments(files["/etc/armbian-release"])
            rel["KERNEL_VERSION"] = "0"
            files["/etc/armbian-release"] = env_bytes(rel)
            result = self.prepare(board, files=files, requested="0")
            self.assertEqual(result["status"], "prepared", result["blockers"])
            self.assertEqual(result["kernel_release"], release(board))
            self.assertTrue(result["release_evidence"]["zero_resolved"])

    def test_zero_with_no_binary_version_stays_blocked(self):
        files = fixture("bpi-f2s")
        files["/boot/bananapi/bpi-f2s/linux/uImage"] = legacy(zimage("0"), kind=2, arch=2, compression=0, version="5.4.35")
        result = self.prepare(files=files, requested="0")
        self.blocked(result)
        self.assertIsNone(result["kernel_release"])

    def test_header_and_requested_release_conflicts(self):
        files = fixture("bpi-f2s")
        files[f'/usr/src/linux-headers-{release("bpi-f2s")}/include/generated/utsrelease.h'] = b'#define UTS_RELEASE "5.4.1"\n'
        self.blocked(self.prepare(files=files))
        self.blocked(self.prepare(requested="5.4.1"))

    def test_release_board_mismatch_and_duplicate(self):
        for raw in (b"BOARD=bananapif2p\nBOARDFAMILY=sunplus-sp7021-bpi\n", b"BOARD=a\nBOARD=b\n"):
            files = fixture("bpi-f2s")
            files["/etc/armbian-release"] = raw
            self.blocked(self.prepare(files=files))

    def test_image_relocation_source_and_destination_must_fit_ram_work(self):
        self.prepare("bpi-ai2n")
        good = template("bpi-ai2n")
        result = special.bootconfig(self.output, template=good)
        self.assertEqual(result["kernel_relocation"], {"start": 0x480000, "size": 8192})
        short = template("bpi-ai2n")
        short["ram"]["kernel_work"]["size"] = 8192
        with self.assertRaisesRegex(special.SpecialError, "搬移"):
            special.bootconfig(self.output, template=short)
        for offset, flags in ((0x400000, 8), (2**64 - 1, 0), (0x80000, 0)):
            files = fixture("bpi-ai2n")
            kernel = bytearray(files["/boot/Image"])
            struct.pack_into("<Q", kernel, 8, offset)
            struct.pack_into("<Q", kernel, 24, flags)
            files["/boot/Image"] = bytes(kernel)
            self.prepare("bpi-ai2n", files=files)
            with self.subTest(offset=offset, flags=flags), self.assertRaises(ValueError):
                special.bootconfig(self.output, template=good)

    def test_root_uuid_remains_bound_to_original_env_and_vendor_identity(self):
        for board in ("bpi-f2p", "bpi-ai2n", "bpi-m6"):
            self.prepare(board)
            result = special.bootconfig(self.output, template=template(board))
            self.assertEqual(result["root_uuid"], UUID)
            self.assertEqual([arg for arg in result["bootargs"] if arg.startswith("root=")], ["root=UUID=" + UUID])
        self.prepare("bpi-w2")
        changed = template("bpi-w2")
        changed["vendor"]["root_identity"]["uuid"] = "00000000-0000-0000-0000-000000000001"
        with self.assertRaisesRegex(special.SpecialError, "根身分"):
            special.bootconfig(self.output, template=changed)

    def test_release_arch_aliases(self):
        for board, aliases in (("bpi-f2p", ("arm", "armhf")), ("bpi-f2s", ("arm", "armhf")),
                               ("bpi-ai2n", ("arm64",))):
            for arch in aliases:
                with self.subTest(board=board, arch=arch):
                    files = fixture(board)
                    rel = special.assignments(files["/etc/armbian-release"])
                    rel.update(ARCH=arch, INITRD_ARCH="arm" if arch != "arm64" else "arm64")
                    files["/etc/armbian-release"] = env_bytes(rel)
                    result = self.prepare(board, files=files, requested="0" if board.startswith("bpi-f2") else None)
                    self.assertEqual(result["status"], "prepared", result["blockers"])
                    self.assertEqual(result["release"]["ARCH"], arch)
                    self.assertEqual(result["kernel_release"], release(board))
                    self.assertEqual(special.validate(self.output), result)

    def test_release_arch_mismatch_and_alias_does_not_override_binary(self):
        for board, arch in (("bpi-f2p", "arm64"), ("bpi-ai2n", "arm"),
                            ("bpi-ai2n", "armhf"), ("bpi-f2p", "aarch64"),
                            ("bpi-f2p", "arm32"), ("bpi-f2p", "")):
            files = fixture(board)
            rel = special.assignments(files["/etc/armbian-release"])
            rel["ARCH"] = arch
            files["/etc/armbian-release"] = env_bytes(rel)
            self.blocked(self.prepare(board, files=files))
        files = fixture("bpi-f2p")
        files["/etc/armbian-release"] += b"ARCH=arm\nINITRD_ARCH=arm\n"
        files["/boot/bananapi/bpi-f2p/linux/uImage"] = image(release("bpi-f2p"))
        self.blocked(self.prepare("bpi-f2p", files=files, requested="0"))

    def test_sunplus_zimage_fallback_is_not_bootm(self):
        files = fixture("bpi-f2s")
        files["/boot/bananapi/bpi-f2s/linux/uImage"] = zimage(release("bpi-f2s"))
        self.blocked(self.prepare(files=files))

    def test_uenv_injection_and_duplicate_keys(self):
        for suffix in (b"uenvcmd=run unexpected\n", b"unexpected=reset\n", b"root=UUID=" + UUID.encode() + b"; reset\n"):
            files = fixture("bpi-f2s")
            files["/boot/uEnv.txt"] += suffix
            self.blocked(self.prepare(files=files))

    def test_realtek_uuid_and_vendor_command_refusal(self):
        files = fixture("bpi-m4")
        env = realtek_env("bpi-m4")
        env["root"] = "UUID=" + UUID + " rw rootfstype=ext4 rootwait"
        files["/boot/uEnv.txt"] = files["/boot/bananapi/bpi-m4/linux/uEnv.txt"] = env_bytes(env)
        result = self.prepare("bpi-m4", files=files)
        self.assertEqual(result["status"], "prepared", result["blockers"])
        self.assertEqual(result["root_uuid"], UUID)
        with self.assertRaises(special.SpecialError):
            special.bootconfig(self.output, template={})
        env["aboot"] = "bootm ${kernel_loadaddr}"
        files["/boot/uEnv.txt"] = env_bytes(env)
        self.blocked(self.prepare("bpi-m4", files=files))

    def test_realtek_unknown_magic_does_not_pass_filename(self):
        files = fixture("bpi-w2")
        files["/boot/bananapi/bpi-w2/linux/uImage"] = b"\0" * 4096
        self.blocked(self.prepare("bpi-w2", files=files))

    def test_realtek_variant_and_audio_required(self):
        for path in ("rtd-1395-bananapi-m4-1GB.dtb", "bluecore.audio"):
            files = fixture("bpi-m4")
            del files["/boot/bananapi/bpi-m4/linux/" + path]
            self.blocked(self.prepare("bpi-m4", files=files))

    def test_dtb_wrong_board_corruption_and_fit(self):
        for data in (dtb("bpi-f2p"), dtb("bpi-f2s")[:-1], dtb("bpi-f2s", extra="images {}; configurations {};")):
            files = fixture("bpi-f2s")
            files["/boot/bananapi/bpi-f2s/linux/sp7021-bpi-f2s.dtb"] = data
            self.blocked(self.prepare(files=files))

    def test_script_payload_and_crc(self):
        for index in (4, 70):
            files = fixture("bpi-m6")
            data = bytearray(files["/boot/boot.scr"])
            data[index] ^= 1
            files["/boot/boot.scr"] = bytes(data)
            self.blocked(self.prepare("bpi-m6", files=files))

    def test_nonempty_overlay_extraargs_and_override_rejected(self):
        for suffix in (b"overlays=test\n", b"extraargs=init=/bin/sh\n", b"kernel_addr_r=0x1000\n"):
            files = fixture("bpi-ai2n")
            files["/boot/armbianEnv.txt"] += suffix
            self.blocked(self.prepare("bpi-ai2n", files=files))

    def test_renesas_extra_payload_required(self):
        files = fixture("bpi-ai2n")
        del files["/boot/Codec_Bin.bin"]
        self.blocked(self.prepare("bpi-ai2n", files=files))

    def test_alternative_entry_even_empty_blocks(self):
        files = fixture("bpi-f2s")
        files["/boot/extlinux/extlinux.conf"] = b""
        self.blocked(self.prepare(files=files))

    def test_callback_non_bytes_and_permission_error(self):
        files = fixture("bpi-f2s")
        files["/boot/uEnv.txt"] = "不是位元組"
        self.blocked(self.prepare(files=files))
        with mock.patch.object(special.Capture, "read_file", create=True):
            def read(_):
                raise PermissionError()
            result = special.prepare(read, board="bpi-f2s", kernel_release="0", output=self.root / "denied")
            self.blocked(result)
            self.assertEqual(result["reads"][0]["status"], "error")

    def test_output_existing_and_symlink_parent_rejected(self):
        self.prepare()
        with self.assertRaises(FileExistsError):
            special.prepare(lambda _: b"", board="bpi-f2s", kernel_release="0", output=self.output)
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            special.prepare(lambda _: b"", board="bpi-f2s", kernel_release="0", output=self.root / "link" / "escape")

    def test_manifest_tampering_and_derived_artifact_tampering(self):
        self.prepare()
        path = self.output / "manifest.json"
        value = json.loads(path.read_bytes())
        value["root_uuid"] = "00000000-0000-0000-0000-000000000000"
        path.write_bytes(special.encoded(value))
        with self.assertRaises(special.SpecialError):
            special.validate(self.output)
        self.prepare()
        (self.output / "artifacts/kernel").write_bytes(b"bad")
        with self.assertRaises(special.SpecialError):
            special.validate(self.output)

    def test_forged_prepared_manifest_does_not_override_replay(self):
        files = fixture("bpi-f2s")
        del files["/boot/uEnv.txt"]
        result = self.prepare(files=files)
        result.update(status="prepared", blockers=[], components_available=True)
        (self.output / "manifest.json").write_bytes(special.encoded(result))
        with self.assertRaises(special.SpecialError):
            special.validate(self.output)

    def test_saved_evidence_symlink_rejected(self):
        self.prepare()
        path = self.output / "files/boot/uEnv.txt"
        target = self.root / "outside"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            special.validate(self.output)

    def test_cli_validate_and_bootconfig(self):
        self.prepare()
        config = self.root / "template.json"
        config.write_bytes(special.encoded(template("bpi-f2s")))
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(special.main(["validate", "--output", str(self.output)]), 0)
        self.assertEqual(json.loads(stream.getvalue())["status"], "prepared")
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(special.main(["bootconfig", "--output", str(self.output), "--template", str(config)]), 0)
        self.assertEqual(json.loads(stream.getvalue())["status"], "validated_offline")

    def test_bootconfig_all_supported_boards(self):
        for board in ("bpi-f2p", "bpi-f2s", "bpi-ai2n", "bpi-m6"):
            self.prepare(board)
            t = template(board)
            before = copy.deepcopy(t)
            config = special.bootconfig(self.output, template=t)
            self.assertEqual(t, before)
            self.assertTrue(config["boot_config_validated"])
            self.assertFalse(config["hardware_validated"])
            self.assertFalse(config["execution_ready"])
            self.assertIn("root=UUID=" + UUID, config["bootargs"])
            self.assertTrue(config["boot_command"].startswith("bootm" if "f2" in board else "booti"))

    def test_bootconfig_overlap_and_binding_rejected(self):
        self.prepare()
        variants = []
        t = template("bpi-f2s")
        t["addresses"]["dtb"] = t["addresses"]["kernel"]
        variants.append(t)
        t = template("bpi-f2s")
        t["ram"]["reserved"].append({"start": 0x300000, "size": 4096})
        variants.append(t)
        t = template("bpi-f2s")
        t["bindings"]["sdmmc_on"] = "1;reset"
        variants.append(t)
        t = template("bpi-f2s")
        t["source"]["partuuid"] = ""
        variants.append(t)
        for t in variants:
            with self.assertRaises(ValueError):
                special.bootconfig(self.output, template=t)

    def test_m6_fixed_address_and_cma_conflicts(self):
        self.prepare("bpi-m6")
        t = template("bpi-m6")
        t["addresses"]["dtb"] += 0x10000
        with self.assertRaisesRegex(special.SpecialError, "固定"):
            special.bootconfig(self.output, template=t)
        t = template("bpi-m6")
        t["ram"]["kernel_work"] = {"start": 0x04a80000, "size": 0x60000000}
        with self.assertRaises(ValueError):
            special.bootconfig(self.output, template=t)

    def test_renesas_extra_binding_mismatch(self):
        self.prepare("bpi-ai2n")
        t = template("bpi-ai2n")
        t["bindings"]["ocabin"] = "Other.bin"
        with self.assertRaisesRegex(special.SpecialError, "額外載荷"):
            special.bootconfig(self.output, template=t)

    def test_realtek_software_recipe_and_relocation(self):
        for board, command in (("bpi-m4", "go all"), ("bpi-w2", "gosd")):
            self.prepare(board)
            t = template(board)
            result = special.bootconfig(self.output, template=t)
            self.assertEqual(result["boot_command"], command)
            self.assertEqual(result["kernel_relocation"], {"start": 0x80000, "size": 8192})
            self.assertEqual(result["vendor_steps"][-1], command)
            self.assertEqual(len(result["loads"]), 4)
            self.assertFalse(result["hardware_validated"])
            self.assertFalse(result["source_identity_verified"])
            t["ram"]["kernel_work"] = {"start": 0x03000000, "size": 0x1000000}
            with self.assertRaises(ValueError):
                special.bootconfig(self.output, template=t)

    def test_realtek_secure_dram_and_media_rejected(self):
        self.prepare("bpi-m4")
        for section, key, value in (("vendor", "secure_mode", "secure"), ("vendor", "hyp_loadaddr", "0x900000"),
                                    ("vendor", "dram_size", "4GB"), ("source", "partition", "1:1"),
                                    ("source", "identity_sha256", "")):
            t = template("bpi-m4")
            t[section][key] = value
            with self.assertRaises(ValueError):
                special.bootconfig(self.output, template=t)


if __name__ == "__main__":
    unittest.main()
