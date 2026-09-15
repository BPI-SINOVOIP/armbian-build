#!/usr/bin/env python3
"""DDR SPL2 離線來源、ELF 與 AArch64 純邏輯回歸；不執行硬體程式。"""

from contextlib import nullcontext, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
SPEC = importlib.util.spec_from_file_location("tools.build_bpi_sram_ddr", REPO / "tools/build_bpi_sram_ddr.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
PROFILE = {
    "id": "1", "clk": "480", "dx_odt": "0x07070707", "dx_dri": "0x0e0e0e0e",
    "ca_dri": "0x0d0d", "odt_en": "0xaaaaeeee", "tpr0": "0", "tpr2": "0",
    "tpr6": "0x3a808080", "tpr10": "0x402f6663", "tpr11": "0x25252523",
    "tpr12": "0x110f0f10", "level": "0", "passes": "1", "window": "1",
}


def command(fields=None, nonce="12345678"):
    values = PROFILE if fields is None else fields
    return f"R nonce_hex={nonce} " + " ".join(f"{key}={value}" for key, value in values.items())


class BuilderTests(unittest.TestCase):
    def test_manifest_identity_and_sources(self):
        raw = builder.read_regular(builder.INPUT / "source-manifest.json")
        self.assertEqual(builder.digest(raw), builder.MANIFEST_SHA256)
        manifest = json.loads(raw)
        self.assertEqual(manifest["base_commit"], "127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3")
        self.assertIn("Licenses/gpl-2.0.txt", manifest["files"])
        self.assertIn("Licenses/lgpl-2.1.txt", manifest["files"])
        self.assertEqual(manifest["files"]["arch/arm/mach-sunxi/dram_sun50i_h616_lab.c"],
                         "63271c36f8f6ca67ae43de06184c9913b1e580a0b6d46878c9c24e0452ede587")

    def test_chinese_cli_and_no_bypass(self):
        parser = builder.build_parser()
        self.assertIn("用法：", parser.format_help())
        self.assertNotIn("usage:", parser.format_help())
        for extra in ("--enable-training", "--allow-unknown-pmic", "--dry-run", "--assume-4g"):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(["--source", "/source", "--output", "/output", extra])

    def test_output_is_new_and_does_not_touch_build009(self):
        source = Path("/source")
        for path in (REPO / "output", Path("/tmp/new"),
                     REPO / "output/evidence/bpi-sram-supervisor/build-009/new"):
            with self.subTest(path=path), self.assertRaises(builder.BuildError):
                builder.new_output(path, source)

    def test_symlinks_and_traversal_rejected(self):
        with self.assertRaises(builder.BuildError):
            builder.plain_path(Path("/x/../y"))
        with mock.patch.object(Path, "is_symlink", return_value=True), self.assertRaises(builder.BuildError):
            builder.plain_path(Path("/source/file"))

    def test_namespace_and_direct_script_imports(self):
        self.assertEqual(builder.package.__name__, "tools.bpi_sram_package")
        result = subprocess.run([sys.executable, "-B", str(REPO / "tools/build_bpi_sram_ddr.py"), "--help"],
                                cwd="/", stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=5, check=True)
        self.assertIn("用法：", result.stdout.decode())

    def test_read_regular_delegates_limit_and_preserves_error(self):
        path = REPO / "tools/build_bpi_sram_ddr.py"
        with mock.patch.object(builder.package, "read_regular_file", return_value=b"source") as reader:
            self.assertEqual(builder.read_regular(path), b"source")
        reader.assert_called_once_with(path, 2 * 1024 * 1024)
        error = builder.package.PackageError("輸入超過上限")
        with mock.patch.object(builder.package, "read_regular_file", side_effect=error) as reader:
            with self.assertRaises(builder.BuildError) as raised:
                builder.read_regular(path)
        reader.assert_called_once_with(path, 2 * 1024 * 1024)
        self.assertIs(raised.exception.__cause__, error)

    def test_read_regular_devices_and_fifo_rejected_before_open(self):
        path = Path("/source/payload.bin")
        for mode in (stat.S_IFBLK, stat.S_IFCHR, stat.S_IFIFO):
            with (self.subTest(mode=mode),
                  mock.patch.object(builder, "plain_path", return_value=path),
                  mock.patch.object(builder.package, "_parent_directory",
                                    return_value=nullcontext((123, path.name))),
                  mock.patch.object(builder.package.os, "stat", return_value=mock.Mock(st_mode=mode)),
                  mock.patch.object(builder.package.os, "open") as opened,
                  self.assertRaises(builder.BuildError)):
                builder.read_regular(path)
            opened.assert_not_called()

    def test_read_regular_fifo_swap_never_opens_data_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"original")
            real_open = os.open
            swapped = False

            def swap(name, flags, *args, **kwargs):
                nonlocal swapped
                self.assertFalse(str(name).startswith("/proc/self/fd/"))
                if name == path.name:
                    self.assertTrue(flags & os.O_PATH)
                    self.assertIn("dir_fd", kwargs)
                    path.unlink()
                    os.mkfifo(path)
                    swapped = True
                return real_open(name, flags, *args, **kwargs)

            with mock.patch.object(builder.package.os, "open", side_effect=swap):
                with self.assertRaises(builder.BuildError):
                    builder.read_regular(path)
            self.assertTrue(swapped)

    def test_read_regular_parent_swap_before_walk_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, replacement = root / "parent", root / "replacement"
            parent.mkdir()
            replacement.mkdir()
            (parent / "payload.bin").write_bytes(b"original")
            (replacement / "payload.bin").write_bytes(b"replacement")
            real_open = os.open
            swapped = False

            def swap(name, flags, *args, **kwargs):
                nonlocal swapped
                self.assertNotEqual(name, "payload.bin")
                if name == parent.name:
                    self.assertTrue(flags & os.O_NOFOLLOW)
                    self.assertTrue(flags & os.O_DIRECTORY)
                    parent.rename(root / "held")
                    parent.symlink_to(replacement, target_is_directory=True)
                    swapped = True
                return real_open(name, flags, *args, **kwargs)

            with mock.patch.object(builder.package.os, "open", side_effect=swap):
                with self.assertRaises(OSError):
                    builder.read_regular(parent / "payload.bin")
            self.assertTrue(swapped)

    def test_read_regular_parent_swap_after_walk_keeps_original_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, replacement = root / "parent", root / "replacement"
            parent.mkdir()
            replacement.mkdir()
            (parent / "payload.bin").write_bytes(b"original")
            (replacement / "payload.bin").write_bytes(b"replacement")
            real_open = os.open
            swapped = False

            def swap(name, flags, *args, **kwargs):
                nonlocal swapped
                if name == "payload.bin":
                    self.assertTrue(flags & os.O_PATH)
                    self.assertIn("dir_fd", kwargs)
                    parent.rename(root / "held")
                    parent.symlink_to(replacement, target_is_directory=True)
                    swapped = True
                return real_open(name, flags, *args, **kwargs)

            with mock.patch.object(builder.package.os, "open", side_effect=swap):
                self.assertEqual(builder.read_regular(parent / "payload.bin"), b"original")
            self.assertTrue(swapped)

    def test_environment_does_not_inherit_compiler_and_git_overrides(self):
        with mock.patch.dict(os.environ, {"CFLAGS": "-O0", "GIT_DIR": "/wrong", "LD_PRELOAD": "/bad",
                                         "CPATH": "/wrong", "GIT_CONFIG_COUNT": "5"}):
            env = builder.environment(Path("/output/new"))
        for key in ("CFLAGS", "GIT_DIR", "LD_PRELOAD", "CPATH", "GIT_CONFIG_COUNT"):
            self.assertNotIn(key, env)
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(env["GIT_CEILING_DIRECTORIES"], "/output/new")

    def test_elf_rejects_truncation_and_non_elf(self):
        for data in (b"", b"\x7fELF", b"\0" * 64):
            with self.subTest(data=data), self.assertRaises(builder.BuildError):
                builder.audit_elf(data, b"")


class PayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("BPI_DDR_BUILD_DIR")
        if not directory:
            raise unittest.SkipTest("設定 BPI_DDR_BUILD_DIR 才執行真實 ELF 與 QEMU 純邏輯回歸")
        cls.directory = Path(directory).resolve()
        cls.elf = (cls.directory / "spl2-ddr.elf").read_bytes()
        cls.raw = (cls.directory / "spl2-ddr.bin").read_bytes()
        cls.report = json.loads((cls.directory / "build-report.json").read_text())
        cls.harness_path = cls.directory / "parser-harness"
        cls.qemu = shutil.which("qemu-aarch64") or shutil.which("qemu-aarch64-static")
        if not cls.qemu:
            raise RuntimeError("缺少 QEMU AArch64 使用者模式工具")
        if not cls.report.get("inputs_unchanged"):
            raise RuntimeError("指定建置尚未完成來源核對")
        for name in ("spl2-ddr.elf", "spl2-ddr.bin", "parser-harness"):
            if builder.digest((cls.directory / name).read_bytes()) != cls.report["artifacts"][name]["sha256"]:
                raise RuntimeError("產物雜湊不符建置報告")
        for name, expected in cls.report["inputs"].items():
            if builder.record(builder.read_regular(builder.INPUT / name)) != expected:
                raise RuntimeError("目前程式與指定建置的輸入不同，請用新目錄重建")

    def harness(self, *args):
        result = subprocess.run([self.qemu, str(self.harness_path), *map(str, args)],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=5, check=True)
        return tuple(map(int, result.stdout.split()))

    def parse(self, fields=None, nonce="12345678", extra=""):
        return self.harness("parse", command(fields, nonce) + extra)

    def test_complete_profile_and_runtime_clock(self):
        for clk in (240, 480, 792, 900):
            for level in (0, 1, 2):
                with self.subTest(clk=clk, level=level):
                    self.assertEqual(self.parse({**PROFILE, "clk": str(clk), "level": str(level)}),
                                     (0, 1, clk, 8, 1, 1))

    def test_missing_fields(self):
        for key in PROFILE:
            with self.subTest(key=key):
                self.assertNotEqual(self.parse({k: v for k, v in PROFILE.items() if k != key})[0], 0)

    def test_duplicate_and_unknown_fields(self):
        for key in (*PROFILE, "rows", "voltage", "addr", "pmic", "nonce_hex"):
            with self.subTest(key=key):
                self.assertNotEqual(self.parse(extra=f" {key}=1")[0], 0)

    def test_nonce_mismatch_and_short_input(self):
        for nonce in ("12345679", "00000000", "1234567", "123456789", "z2345678"):
            with self.subTest(nonce=nonce):
                self.assertEqual(self.parse(nonce=nonce)[0], 1)
        for line in ("", "R", "R nonce_hex=", "R nonce_hex=123", "I", "Z"):
            with self.subTest(line=line):
                self.assertEqual(self.harness("parse", line)[0], 1)

    def test_numeric_limits(self):
        cases = {
            "id": ("0", "-1", "4294967296"), "clk": ("239", "901", "481"),
            "passes": ("0", "2", "1001"), "window": ("0", "65", "4294967295"),
            "level": ("3", "-1", "M0"), "tpr11": ("0x100000000", "0xz", "-1", ""),
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertNotEqual(self.parse({**PROFILE, key: value})[0], 0)
        self.assertEqual(self.parse({**PROFILE, "passes": "2"})[0], 3)

    def test_frame_overflow_binary_and_resynchronization(self):
        for bad in (b"a" * 384, b"x\0y", b"x\x1by", b"\xff", b"\t"):
            with self.subTest(bad=bad):
                self.assertEqual(self.harness("frame", (bad + b"\nI\r\n").hex()), (1, 1, 0, 0))
        self.assertEqual(self.harness("frame", (b"a" * 383 + b"\n").hex()), (1, 0, 0, 0))
        self.assertEqual(self.harness("frame", b"\r\n".hex()), (0, 0, 0, 0))
        self.assertEqual(self.harness("frame", b"R nonce_hex=123".hex()), (0, 0, 15, 0))

    def test_context_abi2_and_boundaries(self):
        good = [2, 64, 0x18000, 0x48010, 12, 0x477f0, 0]
        self.assertEqual(self.harness("context", *good), (1,))
        cases = {0: (0, 1, 3), 1: (63, 65), 2: (0, 0x17ff0, 0x18010),
                 3: (0x48000, 0x48011, 0x4ffb1), 4: (4, 8),
                 5: (0x40000, 0x477f1, 0x47800), 6: (1, 4, 0x1000)}
        for index, values in cases.items():
            for value in values:
                with self.subTest(index=index, value=value):
                    args = good.copy()
                    args[index] = value
                    self.assertEqual(self.harness("context", *args), (0,))

    def test_context_zero_nonce_is_valid_but_does_not_bypass_abi(self):
        good = [2, 64, 0x18000, 0x48010, 12, 0x477f0, 0]
        for nonce in (0, 1, 0xffffffff):
            with self.subTest(nonce=nonce):
                self.assertEqual(self.harness("context", *good, nonce), (1,))
        bad = good.copy()
        bad[0] = 1
        self.assertEqual(self.harness("context", *bad, 0), (0,))

    def test_geometry_validation_never_approves_preflight(self):
        self.assertEqual(self.harness("geometry", 10, 16, 1, 1, 64), (1, 2 << 30, 0))
        self.assertEqual(self.harness("geometry", 10, 16, 2, 1, 64), (1, 4 << 30, 0))
        for values in ((0, 16, 2, 1, 1), (10, 18, 2, 1, 1), (10, 16, 3, 1, 1),
                       (10, 16, 2, 2, 1), (11, 17, 2, 1, 1), (8, 13, 1, 0, 64),
                       (10, 16, 2, 1, 65), (10, 16, 2, 1, 0)):
            with self.subTest(values=values):
                result = self.harness("geometry", *values)
                self.assertEqual((result[0], result[2]), (0, 0))

    def test_real_elf_has_driver_and_no_old_bootstrap(self):
        audit = builder.audit_elf(self.elf, self.raw)
        self.assertEqual(audit["runtime_bytes"], 98304)
        self.assertEqual(audit["context_abi"], 2)
        self.assertNotIn("preflight_gate", audit)
        self.assertEqual(audit["preflight_function"], {
            "symbol": "ddr_preflight", "returns": 0,
            "scope": "function_bytes_only", "control_flow_verified": False,
        })
        self.assertTrue(audit["raw_matches_load_segments"])
        self.assertEqual(audit["parser"]["name"], "pyelftools")
        self.assertLessEqual(audit["symbols"]["__bss_end"], 0x40000)
        self.assertTrue(builder.REQUIRED <= audit["symbols"].keys())
        self.assertFalse(builder.FORBIDDEN & audit["symbols"].keys())
        self.assertIn(b"BPI-SPL2 event=ddr-ready nonce_hex=%08x abi=2", self.elf)
        self.assertIn(b"pmic_geometry_unverified", self.elf)
        self.assertNotIn(b"BPI-SPL2 event=smoke", self.elf)

    def test_builder_and_safe_reader_snapshots_match_report(self):
        for name, expected in {
                "build_bpi_sram_ddr.py": self.report["builder"],
                **self.report["builder_dependencies"],
        }.items():
            with self.subTest(name=name):
                self.assertEqual(builder.record(builder.read_regular(REPO / "tools" / name)), expected)
                self.assertEqual(builder.record(builder.read_regular(self.directory / "inputs" / name)),
                                 expected)
        self.assertEqual(set(self.report["builder_dependencies"]), {"bpi_sram_package.py"})
        self.assertFalse(self.report["elf_audit"]["preflight_function"]["control_flow_verified"])

    def test_tiny_printf_64bit_output(self):
        result = subprocess.run([self.qemu, str(self.harness_path), "format"],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=5, check=True)
        self.assertEqual(result.stdout, b"ffffffffffffffff 18446744073709551615 -2147483648\n")

    def test_elf_cannot_enable_preflight(self):
        audit = builder.audit_elf(self.elf, self.raw)
        gate = audit["symbols"]["ddr_preflight"]
        bad = bytearray(self.elf)
        for segment in audit["segments"]:
            if segment["address"] <= gate < segment["address"] + segment["file_bytes"]:
                struct.pack_into("<I", bad, segment["file_offset"] + gate - segment["address"], 0x52800020)
        with self.assertRaises(builder.BuildError):
            builder.audit_elf(bad, self.raw)

    def test_elf_mutated_entry_and_ddr_segment_rejected(self):
        bad = bytearray(self.elf)
        struct.pack_into("<Q", bad, 24, 0x20060)
        with self.assertRaises(builder.BuildError):
            builder.audit_elf(bad, self.raw)
        phoff = struct.unpack_from("<Q", self.elf, 32)[0]
        for address in (0x20000, 0x40000000):
            bad = bytearray(self.elf)
            struct.pack_into("<Q", bad, phoff + 16, address)
            struct.pack_into("<Q", bad, phoff + 24, address)
            with self.assertRaises(builder.BuildError):
                builder.audit_elf(bad, self.raw)

    def test_raw_tampering_truncation_and_padding_rejected(self):
        for raw in (self.raw[:-1], self.raw + b"\0", bytes([self.raw[0] ^ 1]) + self.raw[1:]):
            with self.subTest(size=len(raw)), self.assertRaises(builder.BuildError):
                builder.audit_elf(self.elf, raw)

    def test_alloc_section_outside_load_rejected(self):
        elf = builder.ELFFile(io.BytesIO(self.elf))
        index = next(i for i, section in enumerate(elf.iter_sections()) if section.name == ".rodata")
        bad = bytearray(self.elf)
        # 只製造壞樣本；正式解析一律交由 pyelftools。
        position = elf.header["e_shoff"] + index * elf.header["e_shentsize"]
        struct.pack_into("<Q", bad, position + 16, 0x30090)
        with self.assertRaises(builder.BuildError):
            builder.audit_elf(bad, self.raw)


if __name__ == "__main__":
    unittest.main()
