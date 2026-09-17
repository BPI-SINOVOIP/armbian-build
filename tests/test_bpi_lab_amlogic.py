#!/usr/bin/env python3
"""Amlogic 小型原配資料回歸；真實 dtc／fdtoverlay，但不接觸硬體。"""

import copy
import gzip
import hashlib
import json
import lzma
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_amlogic as amlogic
from tools import bpi_lab_uboot as uboot


RELEASE = "6.18.1-current-meson64"
UUID = "01234567-89ab-cdef-0123-456789abcdef"
BOARD = "bananapim5"


def legacy(payload, *, kind=3, arch=22, compression=1):
    header = struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), 0, 0,
                         zlib.crc32(payload), 5, arch, kind, compression, b"fixture")
    return header[:4] + struct.pack(">I", zlib.crc32(header)) + header[8:] + payload


def script(payload):
    return legacy(struct.pack(">II", len(payload), 0) + payload, kind=6, arch=2, compression=0)


def cpio(items, *, crc=False):
    result = bytearray()
    for index, (name, data) in enumerate([*items, ("TRAILER!!!", b"")]):
        name = name.encode() + b"\0"
        fields = [index, 0o100644, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), sum(data) if crc else 0]
        result.extend(b"070702" if crc else b"070701")
        result.extend("".join(f"{value:08x}" for value in fields).encode())
        result.extend(name)
        result.extend(bytes(-len(result) % 4))
        result.extend(data)
        result.extend(bytes(-len(result) % 4))
    return bytes(result)


def initrd(release=RELEASE, *, compression="gzip"):
    data = cpio([("init", b"fixture"), (f"lib/modules/{release}/modules.dep", b"fixture.ko:\n")])
    if compression == "gzip":
        data = gzip.compress(data, mtime=0)
    elif compression == "xz":
        data = lzma.compress(data)
    return legacy(data)


def image(release=RELEASE):
    data = bytearray(4096)
    struct.pack_into("<3Q", data, 8, 0x80000, 8192, 8)
    data[56:60] = b"ARM\x64"
    banner = f"Linux version {release} (fixture)\0".encode()
    data[128:128 + len(banner)] = banner
    return bytes(data)


def compile_dts(source):
    return subprocess.run(["/usr/bin/dtc", "-q", "-@", "-I", "dts", "-O", "dtb"],
                          input=source.encode(), capture_output=True, check=True, timeout=10).stdout


def dtb(board=BOARD, *, extra="", reserve=""):
    compatible = ", ".join(json.dumps(value) for value in amlogic.PROFILES[board]["compatible"])
    return compile_dts(f'/dts-v1/; {reserve} / {{ compatible = {compatible}; '
                       f'model = "fixture"; node: node {{ status = "disabled"; }}; {extra} }};')


def overlay(value="okay", *, target="&node"):
    return compile_dts(f'/dts-v1/; /plugin/; {target} {{ status = "{value}"; }};')


def cma_dtb(*, size=0x200000, alignment=0x100000, ranges="", extra="", compatible="shared-dma-pool"):
    return dtb(extra='reserved-memory { #address-cells = <2>; #size-cells = <2>; ranges; '
               'secmon@43000000 { reg = <0 0x43000000 0 0x1000>; no-map; }; '
               f'linux,cma {{ compatible = "{compatible}"; reusable; size = <0 {size}>; '
               f'alignment = <0 {alignment}>; linux,cma-default; {ranges} {extra} }}; }};')


def fixture(board=BOARD):
    profile = amlogic.PROFILES[board]
    cmd = (amlogic.ROOT / amlogic.SCRIPT).read_bytes()
    return {
        "/etc/armbian-release": (f'BOARD={board}\nBOARD_NAME="fixture"\nBOARDFAMILY={profile["family"]}\n'
                                 'LINUXFAMILY=meson64\nARCH=arm64\nINITRD_ARCH=arm64\n').encode(),
        "/boot/armbianEnv.txt": (f'rootdev=UUID={UUID}\nfdtfile={profile["dtb"]}\noverlay_prefix=meson\n'
                                 'verbosity=1\nconsole=both\n').encode(),
        "/boot/boot.cmd": cmd, "/boot/boot.scr": script(cmd),
        "/boot/Image": image(), "/boot/uInitrd": initrd(),
        "/boot/dtb/" + profile["dtb"]: dtb(board),
        "/boot/dtb/amlogic/overlay/meson-fixup.scr": script((amlogic.ROOT / amlogic.NOOP_FIXUP).read_bytes()),
    }


def template(manifest):
    """位址僅屬合成測資，不能作為任何實板的預設值。"""
    return {
        "schema": uboot.SCHEMA, "arch": "arm64", "kernel_release": RELEASE, "fdt_extra": 65536,
        "uboot": {"prompt": "BPI=> ", "version": "U-Boot 2025.01 (fixture)", "address_bits": 64,
                  "line_limit": 1024, "pairing_sha256": "a" * 64, "qualification_sha256": "b" * 64,
                  "abi": "mainline-v2025.01"},
        "ram": {"banks": [{"start": 0x40000000, "size": 0x4000000}],
                "reserved": [{"start": 0x43000000, "size": 0x1000000}],
                "kernel_work": {"start": 0x40200000, "size": 0x400000},
                "boot": {"start": 0x40000000, "size": 0x3000000}},
        "source": {"type": "mmc", "device": 1, "partition": 2, "partuuid": "1234abcd-02"},
        "files": {"kernel": {"address": 0x40280000, "entry": 0x40280000, "capacity": 0x380000},
                  "initrd": {"address": 0x41000000, "capacity": 0x100000},
                  "dtb": {"address": 0x42000000, "capacity": 0x20000}},
        "bootargs": [arg.replace("{partuuid}", "1234abcd-02") for arg in manifest["bootargs_pattern"]],
    }


class _FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="amlogic-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = fixture()
        self.reads = []
        self.serial = 0

    def reader(self, path):
        self.assertTrue(path.startswith("/"))
        self.assertNotIn("..", path.split("/"))
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        value = self.files[path]
        if isinstance(value, Exception):
            raise value
        return value

    def prepare(self, board=BOARD, reader=None):
        self.serial += 1
        self.output = self.root / str(self.serial)
        result = amlogic.prepare(reader or self.reader, board=board, kernel_release=RELEASE, output=self.output)
        self.assertEqual(result, json.loads((self.output / "manifest.json").read_text()))
        self.assertIs(result["hardware_validated"], False)
        self.assertIs(result["boot_config_validated"], False)
        self.assertEqual(result["components_available"], result["status"] == "prepared")
        for record in result["files"]:
            data = (self.output / record["path"]).read_bytes()
            self.assertEqual(len(data), record["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
        return result

    def blocked(self, result, text=None):
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["blockers"])
        if text:
            self.assertIn(text, json.dumps(result["blockers"], ensure_ascii=False))

    def add_env(self, data):
        self.files["/boot/armbianEnv.txt"] += data.encode()


class PrepareTests(_FixtureCase):
    def test_standalone_import_without_project_root_on_module_path(self):
        tools_path = str(amlogic.ROOT / "tools")
        result = subprocess.run([sys.executable, "-I", "-B", "-c",
                                 f"import sys; sys.path.insert(0, {tools_path!r}); import bpi_lab_amlogic"],
                                cwd=self.root, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_module_can_run_as_direct_script(self):
        result = subprocess.run([sys.executable, "-B", str(amlogic.ROOT / "tools/bpi_lab_amlogic.py")],
                                cwd=self.root, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_four_boards_preserve_original_components(self):
        for board in amlogic.PROFILES:
            with self.subTest(board=board):
                self.files = fixture(board)
                result = self.prepare(board)
                self.assertEqual(result["status"], "prepared", result["blockers"])
                self.assertEqual(result["root_uuid"], UUID)
                self.assertEqual(result["components"]["kernel"]["kernel_release"], RELEASE)
                self.assertEqual(result["components"]["initrd"]["module_releases"], [RELEASE])
                self.assertEqual(result["fixups"][0]["status"], "verified-noop")
                self.assertEqual(result["branch"], "modern")

    def test_callback_reads_each_absolute_path_only_once(self):
        self.prepare()
        self.assertEqual(len(self.reads), len(set(self.reads)))
        self.assertTrue(all(path.startswith(("/boot", "/etc/armbian", "/extlinux", "/armbian", "/uEnv", "/aml_", "/s905_"))
                            for path in self.reads))

    def test_missing_each_required_file_preserves_other_evidence(self):
        for path in ("/etc/armbian-release", "/boot/armbianEnv.txt", "/boot/boot.cmd", "/boot/boot.scr",
                     "/boot/Image", "/boot/uInitrd", "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]):
            with self.subTest(path=path):
                saved = self.files.pop(path)
                result = self.prepare()
                self.blocked(result, "不存在")
                self.assertGreater(len(result["files"]), 7)
                self.files[path] = saved

    def test_empty_required_and_alternate_files_are_not_absence(self):
        for path in ("/boot/boot.scr", "/boot/Image", "/boot/boot.ini"):
            with self.subTest(path=path):
                original = fixture()
                self.files = {**original, path: b""}
                self.blocked(self.prepare())
                self.assertTrue((self.output / ("files" + path)).exists())

    def test_alternate_boot_entries_block_and_are_captured(self):
        for path in amlogic.ALTERNATE_ENTRIES:
            with self.subTest(path=path):
                self.files[path] = b"fixture"
                self.blocked(self.prepare(), "替代或自訂")
                self.assertEqual((self.output / ("files" + path)).read_bytes(), b"fixture")
                del self.files[path]

    def test_release_mismatches_block(self):
        for old, new in ((b"BOARD=bananapim5", b"BOARD=bananapim2pro"), (b"ARCH=arm64", b"ARCH=armhf"),
                         (b"BOARDFAMILY=meson-sm1", b"BOARDFAMILY=meson-g12b"),
                         (b"LINUXFAMILY=meson64", b"LINUXFAMILY=meson")):
            with self.subTest(new=new):
                self.files = fixture()
                self.files["/etc/armbian-release"] = self.files["/etc/armbian-release"].replace(old, new)
                self.blocked(self.prepare(), "板型不符")

    def test_release_kernel_metadata_checked_when_present(self):
        self.files["/etc/armbian-release"] += b"KERNEL_VERSION=0.0\n"
        self.blocked(self.prepare(), "核心版本")

    def test_config_is_not_shell_and_rejects_duplicates(self):
        for value in ("param_spidev_spi_bus=0\n", "customscript=anything\n", "rootdev=UUID=other\n",
                      "fdtfile=../../etc/shadow\n", "setenv rootdev evil\n", "extraargs=$(touch /tmp/never)\n"):
            with self.subTest(value=value):
                self.files = fixture()
                self.add_env(value)
                self.blocked(self.prepare())
                self.assertFalse(any("shadow" in path or "never" in path for path in self.reads))

    def test_release_shell_expansion_never_executed(self):
        self.files["/etc/armbian-release"] += b"UNTRUSTED=$(false)\n"
        result = self.prepare()
        self.blocked(result, "指令語法")
        self.assertEqual(result["release"]["BOARD"], BOARD)
        self.assertFalse(any("板型不符" in blocker["reason"] for blocker in result["blockers"]))

    def test_real_release_quoted_color_and_literal_punctuation(self):
        self.files["/etc/armbian-release"] += (
            b'VENDORCOLOR="247;16;0"\nVENDOR="Armbian-unofficial"\n'
            b'VENDORDOCS="https://docs.armbian.com/"\nEMPTY=\nLITERAL="a;<>|&b"\n'
            b"SINGLE='literal;$value'\n")
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        self.assertEqual(result["release"]["VENDORCOLOR"], "247;16;0")
        self.assertEqual(result["release"]["LITERAL"], "a;<>|&b")
        self.assertEqual(result["release"]["SINGLE"], "literal;$value")

    def test_shell_commands_and_expansion_outside_literal_remain_blocked(self):
        base = self.files["/etc/armbian-release"]
        for value in ('x;false', '"x";false', '"$(false)"', '"$HOME"', '"`false`"', 'a | false', '"unterminated'):
            with self.subTest(value=value):
                self.files["/etc/armbian-release"] = base + f"UNTRUSTED={value}\n".encode()
                result = self.prepare()
                self.blocked(result, "指令語法")
                self.assertEqual(result["release"]["BOARD"], BOARD)

    def test_invalid_utf8_and_nul_are_blockers(self):
        for data in (b"\xff", b"rootdev=UUID=x\0", b"rootdev=UUID=x\r\n"):
            self.files["/boot/armbianEnv.txt"] = data
            self.blocked(self.prepare())

    def test_missing_rootdev_never_uses_script_mmc_default(self):
        self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(f"rootdev=UUID={UUID}\n".encode(), b"")
        result = self.prepare()
        self.blocked(result, "rootdev")
        self.assertIsNone(result["root_uuid"])

    def test_partuuid_is_explicit_but_does_not_invent_root_uuid(self):
        self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(f"UUID={UUID}".encode(), b"PARTUUID=1234abcd-02")
        result = self.prepare()
        self.assertEqual(result["status"], "prepared")
        self.assertIsNone(result["root_uuid"])

    def test_kernel_version_magic_endianness_and_size(self):
        original = image()
        variants = [image("6.18.2-current-meson64"), original.replace(b"Linux version", b"Other version"),
                    original[:56] + b"RSC\x05" + original[60:]]
        for offset, value in ((16, 0), (16, 4095), (24, 1)):
            changed = bytearray(original)
            struct.pack_into("<Q", changed, offset, value)
            variants.append(bytes(changed))
        for data in variants:
            with self.subTest(header=data[:64]):
                self.files["/boot/Image"] = data
                self.blocked(self.prepare(), "kernel")

    def test_ambiguous_kernel_version_rejected(self):
        self.files["/boot/Image"] += b"Linux version 1.0 (fixture)\0"
        self.blocked(self.prepare(), "內嵌版本")

    def test_legacy_branch_is_detected_by_presence_even_empty(self):
        for data in (b"", gzip.compress(image())):
            self.files["/boot/zImage"] = data
            result = self.prepare()
            self.blocked(result, "legacy unzip")
            self.assertEqual(result["branch"], "legacy")
            self.assertIn("kernel", result["components"])

    def test_boot_script_crc_and_length_table(self):
        original = self.files["/boot/boot.scr"]
        variants = []
        for index in (4, 100):
            changed = bytearray(original)
            changed[index] ^= 1
            variants.append(bytes(changed))
        variants += [original[:-1], legacy(b"\0" * 8, kind=6, arch=2, compression=0),
                     legacy(struct.pack(">II", 1, 2) + b"x", kind=6, arch=2, compression=0)]
        for data in variants:
            self.files["/boot/boot.scr"] = data
            self.blocked(self.prepare(), "boot.scr")

    def test_changed_script_and_stale_compiled_script_block(self):
        self.files["/boot/boot.cmd"] += b"setenv injected yes\n"
        self.blocked(self.prepare(), "不一致")
        self.files["/boot/boot.scr"] = script(self.files["/boot/boot.cmd"])
        self.blocked(self.prepare(), "來源")

    def test_initrd_raw_gzip_and_xz_payloads(self):
        for compression in ("gzip", "xz", "raw"):
            self.files["/boot/uInitrd"] = initrd(compression=compression)
            result = self.prepare()
            self.assertEqual(result["status"], "prepared", result["blockers"])

    def test_initrd_early_archive_and_concatenated_gzip(self):
        early = cpio([("early", b"fixture")])
        main = cpio([(f"usr/lib/modules/{RELEASE}/modules.dep", b"")])
        self.files["/boot/uInitrd"] = legacy(early + bytes(12) + gzip.compress(main, mtime=0))
        self.assertEqual(self.prepare()["status"], "prepared")
        self.files["/boot/uInitrd"] = legacy(gzip.compress(early, mtime=0) + gzip.compress(main, mtime=0))
        self.assertEqual(self.prepare()["status"], "prepared")

    def test_initrd_crc_architecture_truncation_and_version(self):
        original = initrd()
        changed = bytearray(original)
        changed[-1] ^= 1
        for data in (bytes(changed), original[:-1], legacy(original[64:], arch=2), initrd("9.0"),
                     legacy(b"070701"), legacy(gzip.compress(b"not-cpio")),
                     legacy(gzip.compress(cpio([("init", b"")]))), legacy(b"\x28\xb5\x2f\xfd")):
            with self.subTest(data=data[:32]):
                self.files["/boot/uInitrd"] = data
                self.blocked(self.prepare(), "initrd")

    def test_cpio_crc_and_mixed_versions(self):
        data = cpio([(f"lib/modules/{RELEASE}/modules.dep", b"abc")], crc=True)
        self.files["/boot/uInitrd"] = legacy(gzip.compress(data, mtime=0))
        self.assertEqual(self.prepare()["status"], "prepared")
        self.files["/boot/uInitrd"] = legacy(gzip.compress(data.replace(b"abc", b"abd"), mtime=0))
        self.blocked(self.prepare(), "校驗")
        self.files["/boot/uInitrd"] = legacy(cpio([(f"lib/modules/{RELEASE}/x", b""), ("lib/modules/other/x", b"")]))
        self.blocked(self.prepare(), "混用")

    def test_initrd_traversal_never_extracted(self):
        self.files["/boot/uInitrd"] = legacy(cpio([("../escape", b"fixture")]))
        self.blocked(self.prepare(), "不安全路徑")
        self.assertFalse((self.root / "escape").exists())

    def test_initrd_decompression_limit(self):
        with mock.patch.object(amlogic, "MAX_UNPACKED", 100):
            self.blocked(self.prepare(), "超限")

    def test_wrong_board_dtb_and_fit_are_rejected(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        for data in (dtb("bananapim2pro"), dtb(extra="images {}; configurations {};"), b"\0" * 128):
            self.files[path] = data
            self.blocked(self.prepare(), "dtb")

    def test_m2s_s922x_is_not_a311d(self):
        self.files = fixture("bananapim2s")
        path = "/boot/dtb/" + amlogic.PROFILES["bananapim2s"]["dtb"]
        self.files[path] = self.files[path].replace(b"amlogic,a311d", b"amlogic,s922x")
        self.blocked(self.prepare("bananapim2s"), "SoC 不符")

    def test_fdt_header_range_truncation_and_structure(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        original = self.files[path]
        variants = [original[:-1], original + b"\0"]
        for offset, value in ((8, 0xfffffff0), (12, 40), (16, 41), (20, 16)):
            data = bytearray(original)
            struct.pack_into(">I", data, offset, value)
            variants.append(bytes(data))
        for data in variants:
            self.files[path] = data
            self.blocked(self.prepare(), "FDT")

    def test_real_overlay_merges_kernel_then_user_order(self):
        self.add_env("overlays=first\nuser_overlays=second\n")
        self.files["/boot/dtb/amlogic/overlay/meson-first.dtbo"] = overlay("okay")
        self.files["/boot/overlay-user/second.dtbo"] = overlay("disabled")
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        self.assertIs(result["overlay_applied"], True)
        local = self.output / result["components"]["dtb"]["path"]
        self.assertEqual(amlogic._fdtget(local, "/node", "status", mode="s"), ["disabled"])
        self.assertNotEqual(result["components"]["dtb"]["sha256"], hashlib.sha256(dtb()).hexdigest())

    def test_missing_overlay_does_not_silently_use_base_dtb(self):
        self.add_env("overlays=missing\n")
        result = self.prepare()
        self.blocked(result, "不存在")
        self.assertNotIn("overlay_applied", result)

    def test_overlay_missing_target_is_blocked(self):
        self.add_env("overlays=bad\n")
        self.files["/boot/dtb/amlogic/overlay/meson-bad.dtbo"] = overlay(target="&missing")
        self.blocked(self.prepare(), "fdtoverlay")

    def test_overlay_cannot_replace_board_identity(self):
        self.add_env("user_overlays=bad\n")
        self.files["/boot/overlay-user/bad.dtbo"] = compile_dts('/dts-v1/; /plugin/; &{/} { compatible = "wrong,board"; };')
        self.blocked(self.prepare(), "板型")

    def test_overlay_names_and_prefix_are_bounded(self):
        for value in ("overlays=../escape\n", "overlays=x x\n", "overlays=" + " ".join(f"x{n}" for n in range(65)) + "\n",
                      "user_overlays=x;false\n", "overlay_prefix=../../etc\n"):
            self.files = fixture()
            self.add_env(value)
            self.blocked(self.prepare(), "overlay")

    def test_unknown_fixup_preserves_decoded_script_without_execution(self):
        path = "/boot/dtb/amlogic/overlay/meson-fixup.scr"
        self.files[path] = script(b"fdt set /node status okay\n")
        self.blocked(self.prepare(), "fixup")
        self.assertEqual((self.output / ("decoded" + path + ".cmd")).read_bytes(), b"fdt set /node status okay\n")

    def test_user_fixup_is_not_silently_skipped(self):
        self.files["/boot/fixup.scr"] = script(b"true\n")
        self.blocked(self.prepare(), "fixup")

    def test_broken_noop_fixup_crc_is_rejected(self):
        path = "/boot/dtb/amlogic/overlay/meson-fixup.scr"
        self.files[path] = self.files[path][:-1] + b"!"
        self.blocked(self.prepare(), "CRC")

    def test_no_overlay_execution_when_fixup_or_parameters_unknown(self):
        self.add_env("overlays=first\nparam_x=1\n")
        self.files["/boot/dtb/amlogic/overlay/meson-first.dtbo"] = overlay()
        with mock.patch.object(amlogic, "_run", wraps=amlogic._run) as run:
            self.blocked(self.prepare(), "param_x")
        self.assertFalse(any("fdtoverlay" in call.args[0][0] for call in run.call_args_list))

    def test_tool_missing_or_timeout_preserves_evidence(self):
        with mock.patch.object(amlogic.subprocess, "run", side_effect=FileNotFoundError):
            result = self.prepare()
        self.blocked(result, "工具不存在")
        self.assertTrue((self.output / "files/boot/Image").is_file())

    def test_provider_error_is_not_absence(self):
        for error in (PermissionError(), ValueError(), "not-bytes"):
            self.files["/boot/fixup.scr"] = error
            self.blocked(self.prepare(), "read_file" if isinstance(error, str) else "提供者")

    def test_oversized_evidence_keeps_hash_not_blob(self):
        self.files["/boot/armbianEnv.txt"] = b"x" * 65537
        result = self.prepare()
        self.blocked(result, "超限")
        record = next(record for record in result["reads"] if record["image_path"] == "/boot/armbianEnv.txt")
        self.assertEqual(record["status"], "over-limit")
        self.assertIn("sha256", record)
        self.assertFalse((self.output / "files/boot/armbianEnv.txt").exists())

    def test_existing_output_and_symlink_output_never_overwritten(self):
        existing = self.root / "existing"
        existing.mkdir()
        link = self.root / "link"
        link.symlink_to(existing)
        for path in (existing, link):
            with self.assertRaises(FileExistsError):
                amlogic.prepare(self.reader, board=BOARD, kernel_release=RELEASE, output=path)
        self.assertEqual(self.reads, [])

    def test_invalid_api_rejected_before_reading(self):
        for board, release in (("m5", RELEASE), ("bananapim4zero", RELEASE), (BOARD, "../release")):
            with self.assertRaises(amlogic.AmlogicError):
                amlogic.prepare(self.reader, board=board, kernel_release=release, output=self.root / "no")
        self.assertEqual(self.reads, [])

    def test_changed_trusted_sources_block(self):
        with mock.patch.dict(amlogic.SOURCE_HASHES, {amlogic.SCRIPT: "0" * 64}):
            self.blocked(self.prepare(), "來源已變更")

    def test_unexpected_provider_exception_leaves_blocked_manifest(self):
        def reader(path):
            if path == "/boot/Image":
                raise RuntimeError("測試中斷")
            return self.reader(path)
        with self.assertRaises(RuntimeError):
            self.prepare(reader=reader)
        result = json.loads((self.output / "manifest.json").read_text())
        self.blocked(result, "未完成")

    def test_no_hardware_or_shell_process_is_spawned(self):
        with mock.patch.object(amlogic.subprocess, "run", wraps=subprocess.run) as run:
            self.prepare()
        self.assertTrue(run.called)
        for call in run.call_args_list:
            self.assertIn(call.args[0][0], ("/usr/bin/dtc", "/usr/bin/fdtget", "/usr/bin/fdtoverlay"))
            self.assertFalse(call.kwargs.get("shell", False))
            self.assertEqual(call.kwargs["timeout"], 30)


class ConfigTests(_FixtureCase):
    def test_validate_uses_both_shared_validators_and_preserves_template(self):
        result = self.prepare()
        config = template(result)
        before = copy.deepcopy(config)
        with mock.patch.object(uboot, "validate_config", wraps=uboot.validate_config) as validate, \
                mock.patch.object(uboot, "validate_artifacts", wraps=uboot.validate_artifacts) as artifacts:
            checked = amlogic.validate_boot_config(self.output, template=config)
        self.assertTrue(validate.called)
        self.assertEqual(artifacts.call_count, 1)
        self.assertIs(checked["hardware_validated"], False)
        self.assertIs(checked["executed"], False)
        self.assertTrue(checked["artifacts_verified"])
        self.assertEqual(config, before)

    def test_template_memory_version_media_and_bootargs_mismatches(self):
        result = self.prepare()
        for modify in (lambda c: c["ram"]["reserved"].append({"start": 0x40200000, "size": 0x100000}),
                       lambda c: c["files"]["kernel"].update(entry=0x40200000),
                       lambda c: c.update(kernel_release="other"),
                       lambda c: c["source"].update(partuuid="1234abcd-03"),
                       lambda c: c["bootargs"].append("init=/bin/sh"),
                       lambda c: c["uboot"].update(abi="vendor"),
                       lambda c: c["files"]["dtb"].update(capacity=128)):
            config = template(result)
            modify(config)
            with self.assertRaises((amlogic.AmlogicError, uboot.UBootError)):
                amlogic.validate_boot_config(self.output, template=config)

    def test_tampered_component_or_evidence_symlink_is_rejected(self):
        result = self.prepare()
        path = self.output / "files/boot/Image"
        path.write_bytes(b"x" * 4096)
        with self.assertRaises(amlogic.AmlogicError):
            amlogic.validate_boot_config(self.output, template=template(result))
        path.unlink()
        path.symlink_to(self.output / "files/boot/uInitrd")
        with self.assertRaises((amlogic.AmlogicError, OSError)):
            amlogic.validate_boot_config(self.output, template=template(result))

    def test_cannot_unblock_by_editing_manifest_status(self):
        self.add_env("customscript=x\n")
        result = self.prepare()
        result.update(status="prepared", blockers=[], components_available=True)
        (self.output / "manifest.json").write_text(json.dumps(result))
        with self.assertRaises(amlogic.AmlogicError):
            amlogic.validate_boot_config(self.output, template=template(result))

    def test_dtb_memreserve_and_reserved_memory_require_external_approval(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        variants = [dtb(reserve="/memreserve/ 0x40800000 0x1000;"),
                    dtb(extra="reserved-memory { #address-cells = <2>; #size-cells = <2>; ranges; "
                              "area@40800000 { reg = <0 0x40800000 0 0x1000>; }; };")]
        for data in variants:
            self.files[path] = data
            result = self.prepare()
            self.assertEqual(result["status"], "prepared", result["blockers"])
            config = template(result)
            with self.assertRaisesRegex(amlogic.AmlogicError, "保留區"):
                amlogic.validate_boot_config(self.output, template=config)
            config["ram"]["reserved"].append({"start": 0x40800000, "size": 0x1000})
            self.assertTrue(amlogic.validate_boot_config(self.output, template=config)["artifacts_verified"])

    def test_dynamic_reserved_memory_is_blocked(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        self.files[path] = dtb(extra="reserved-memory { #address-cells = <2>; #size-cells = <2>; ranges; "
                                    "area { size = <0 0x1000>; }; };")
        self.blocked(self.prepare(), "動態")

    def test_standard_dynamic_cma_separated_from_firmware_reg(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        self.files[path] = cma_dtb(size=0x10000000, alignment=0x400000)
        result = self.prepare()
        self.assertEqual(result["status"], "prepared", result["blockers"])
        component = result["components"]["dtb"]
        self.assertEqual(component["reservations"], [{"start": 0x43000000, "size": 0x1000}])
        self.assertEqual(component["dynamic_cma"], [{"node": "/reserved-memory/linux,cma", "kind": "linux-cma-default",
                                                   "size": 0x10000000, "alignment": 0x400000, "alloc_ranges": []}])

    def test_dynamic_cma_unknown_types_flags_and_bad_cells_block(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        variants = [cma_dtb(extra="no-map;"), cma_dtb(extra="linux,dma-default;"),
                    cma_dtb(extra='status = "disabled";'), cma_dtb(size=0), cma_dtb(alignment=3),
                    cma_dtb(ranges="alloc-ranges = <0 0x40000000 0>;"),
                    cma_dtb(ranges="alloc-ranges = <0xffffffff 0xfffff000 0 0x2000>;"),
                    cma_dtb(compatible="vendor,unknown")]
        for data in variants:
            self.files[path] = data
            self.blocked(self.prepare())

    def test_cma_requires_exact_external_approval_with_evidence(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        self.files[path] = cma_dtb()
        result = self.prepare()
        config = template(result)
        with self.assertRaisesRegex(amlogic.AmlogicError, "逐項明確核定"):
            amlogic.validate_boot_config(self.output, template=config)
        approval = {"requirements": result["components"]["dtb"]["dynamic_cma"], "qualification_sha256": "c" * 64}
        config["amlogic_cma"] = copy.deepcopy(approval)
        checked = amlogic.validate_boot_config(self.output, template=config)
        self.assertTrue(checked["artifacts_verified"])
        self.assertEqual(checked["amlogic_cma"], approval)
        self.assertNotIn("amlogic_cma", checked["config"])
        for mutate in (lambda a: a.update(qualification_sha256=""),
                       lambda a: a["requirements"][0].update(size=0x400000)):
            config["amlogic_cma"] = copy.deepcopy(approval)
            mutate(config["amlogic_cma"])
            with self.assertRaisesRegex(amlogic.AmlogicError, "逐項明確核定"):
                amlogic.validate_boot_config(self.output, template=config)

    def test_cma_alloc_ranges_are_constraints_not_static_reservations(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        self.files[path] = cma_dtb(ranges="alloc-ranges = <0 0x40000000 0 0x3000000>;")
        result = self.prepare()
        config = template(result)
        config["amlogic_cma"] = {"requirements": result["components"]["dtb"]["dynamic_cma"], "qualification_sha256": "c" * 64}
        self.assertTrue(amlogic.validate_boot_config(self.output, template=config)["artifacts_verified"])
        self.assertEqual(len(result["components"]["dtb"]["reservations"]), 1)

    def test_cma_approval_does_not_bypass_firmware_reserved_regions(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        self.files[path] = cma_dtb()
        result = self.prepare()
        config = template(result)
        config["amlogic_cma"] = {"requirements": result["components"]["dtb"]["dynamic_cma"], "qualification_sha256": "c" * 64}
        config["ram"]["reserved"] = [{"start": 0x43f00000, "size": 0x100000}]
        with self.assertRaisesRegex(amlogic.AmlogicError, "DTB 保留區"):
            amlogic.validate_boot_config(self.output, template=config)

    def test_cma_capacity_and_ranges_respect_occupied_memory(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        for data in (cma_dtb(size=0x10000000, alignment=0x400000),
                     cma_dtb(ranges="alloc-ranges = <0 0x43000000 0 0x1000000>;"),
                     cma_dtb(ranges="alloc-ranges = <0 0x40200000 0 0x400000>;")):
            self.files[path] = data
            result = self.prepare()
            config = template(result)
            config["amlogic_cma"] = {"requirements": result["components"]["dtb"]["dynamic_cma"], "qualification_sha256": "c" * 64}
            with self.assertRaisesRegex(amlogic.AmlogicError, "不足以容納 CMA"):
                amlogic.validate_boot_config(self.output, template=config)

    def test_cma_bootargs_override_is_not_silently_accepted(self):
        path = "/boot/dtb/" + amlogic.PROFILES[BOARD]["dtb"]
        self.files[path] = cma_dtb()
        self.add_env("extraargs=cma=64M\n")
        result = self.prepare()
        config = template(result)
        config["amlogic_cma"] = {"requirements": result["components"]["dtb"]["dynamic_cma"], "qualification_sha256": "c" * 64}
        with self.assertRaisesRegex(amlogic.AmlogicError, "核心參數"):
            amlogic.validate_boot_config(self.output, template=config)

    def test_evidence_replay_requires_explicit_absence_records(self):
        result = self.prepare()
        result["reads"] = [record for record in result["reads"] if record["image_path"] != "/boot/fixup.scr"]
        (self.output / "manifest.json").write_text(json.dumps(result))
        with self.assertRaisesRegex(amlogic.AmlogicError, "證據重播遭阻擋"):
            amlogic.validate_boot_config(self.output, template=template(result))

    def test_overlay_output_is_revalidated_not_base_dtb(self):
        self.add_env("overlays=first\n")
        self.files["/boot/dtb/amlogic/overlay/meson-first.dtbo"] = overlay()
        result = self.prepare()
        checked = amlogic.validate_boot_config(self.output, template=template(result))
        self.assertEqual(checked["config"]["files"]["dtb"]["path"], "derived/board.dtb")
        (self.output / "derived/board.dtb").write_bytes(dtb())
        with self.assertRaises(uboot.UBootError):
            amlogic.validate_boot_config(self.output, template=template(result))


if __name__ == "__main__":
    unittest.main()
