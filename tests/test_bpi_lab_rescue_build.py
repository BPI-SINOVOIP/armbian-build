"""跨架構救援建置離線回歸；不執行原生建置、不掛載或操作板子。"""

import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import build_bpi_lab_rescue as lab
import test_bpi_h618_rescue as original

builder = lab.builder


def profile(architecture="arm64"):
    return {"schema": "bpi-lab-rescue-build-profile-v1", "board": "bpi-fixture",
            "architecture": architecture, "multiarch": builder.MULTIARCH[architecture],
            "dt_compatible": ["fixture,board", "fixture,soc"],
            "modules": ["mmc_block", "fixture_mmc"], "firmware": [], "wireless": False}


def elf(architecture, program_type=1):
    if architecture != "arm32":
        return original.elf(machine=183 if architecture == "arm64" else 243, program_type=program_type)
    data = bytearray(84)
    data[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<HH", data, 16, 2, 40)
    struct.pack_into("<I", data, 28, 52)
    struct.pack_into("<HH", data, 42, 32, 1)
    struct.pack_into("<I", data, 52, program_type)
    return data


class ProfileTests(unittest.TestCase):
    setUp = original.RescueTests.setUp
    put = original.RescueTests.put
    environment = original.RescueTests.environment

    def test_three_explicit_architectures(self):
        for architecture in builder.MULTIARCH:
            with self.subTest(architecture=architecture):
                self.assertEqual(builder.validate_profile(profile(architecture))["architecture"], architecture)
                binary = self.put(architecture, elf(architecture), 0o755)
                builder.elf_native(binary, architecture, static=True)

    def test_wrong_architecture_and_dynamic_static_rejected(self):
        for architecture in builder.MULTIARCH:
            for kind in (2, 3):
                binary = self.put(architecture + str(kind), elf(architecture, kind), 0o755)
                with self.subTest(architecture=architecture, kind=kind), self.assertRaises(ValueError):
                    builder.elf_native(binary, architecture, static=True)
            binary = self.put(architecture, elf(architecture), 0o755)
            other = "arm32" if architecture != "arm32" else "arm64"
            with self.assertRaises(ValueError):
                builder.elf_native(binary, other)

    def test_profile_rejects_unknown_fields_and_library_abi(self):
        for changes in ({"architecture": "x86_64"}, {"multiarch": "other"}, {"modules": []},
                        {"modules": ["mmc_block", "mmc_block"]}, {"modules": ["module;reboot"]},
                        {"dt_compatible": []}, {"wireless": "false"}, {"extra": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                builder.validate_profile({**profile(), **changes})

    def test_firmware_paths_and_hashes_are_explicit(self):
        for path in ("/etc/shadow", "../outside", "driver/../../outside", "driver//name", "driver name"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                builder.validate_profile({**profile(), "firmware": [{"path": path, "sha256": "a" * 64}]})
        item = {"path": "device/fw.bin", "sha256": "a" * 64}
        builder.validate_profile({**profile(), "firmware": [item]})
        with self.assertRaises(ValueError):
            builder.validate_profile({**profile(), "firmware": [item, item]})

    def test_wireless_requires_regulatory_hashes(self):
        with self.assertRaises(ValueError):
            builder.validate_profile({**profile(), "wireless": True})
        items = [{"path": name, "sha256": "a" * 64} for name in ("regulatory.db", "regulatory.db.p7s")]
        builder.validate_profile({**profile(), "wireless": True, "firmware": items})

    def test_profile_reference_refuses_changed_and_duplicate_json(self):
        path = self.put("profile.json", json.dumps(profile()))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(lab.load_profile(path, sha), profile())
        with self.assertRaises(ValueError):
            lab.load_profile(path, "0" * 64)
        path.write_bytes(b'{"schema":"x","schema":"y"}')
        with self.assertRaises(ValueError):
            lab.load_profile(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_native_preflight_selects_profile_modules(self):
        _, checked = self.environment()
        source_path = builder.Path
        compatible = self.put("compatible", b"fixture,board\0fixture,soc\0")
        def mapped(value):
            return compatible if str(value) == "/sys/firmware/devicetree/base/compatible" else source_path(value)
        with mock.patch.object(builder, "Path", side_effect=mapped):
            result = builder.preflight(self.args, profile=profile())
        self.assertEqual(result[5], [])
        names = [str(call.args[0][-1]) for call in checked.call_args_list if call.args[0][0] == "modinfo"]
        self.assertIn("fixture_mmc", names)
        self.assertNotIn("sunxi_mmc", names)
        self.assertFalse(Path(self.args.output).exists())

    def test_native_preflight_rejects_wrong_dt_without_build(self):
        self.environment()
        source_path = builder.Path
        compatible = self.put("compatible", b"other,board\0")
        def mapped(value):
            return compatible if str(value) == "/sys/firmware/devicetree/base/compatible" else source_path(value)
        with mock.patch.object(builder, "Path", side_effect=mapped), self.assertRaisesRegex(ValueError, "DT"):
            builder.preflight(self.args, profile=profile())
        self.assertFalse(Path(self.args.output).exists())

    def test_cross_build_is_not_silently_allowed(self):
        self.environment()
        with self.assertRaisesRegex(ValueError, "原生"):
            builder.preflight(self.args, profile=profile("riscv64"))

    def test_wrapper_check_only_never_calls_build(self):
        path = self.put("profile.json", json.dumps(profile()))
        args = ["--output", str(self.base / "new"), "--busybox", str(self.busybox),
                "--busybox-sha256", self.args.busybox_sha256, "--profile", str(path),
                "--profile-sha256", hashlib.sha256(path.read_bytes()).hexdigest(), "--check-only"]
        with (mock.patch.object(builder, "preflight") as check, mock.patch.object(builder, "build") as build,
              mock.patch("builtins.print")):
            result = lab.main(args)
        self.assertEqual(result, 0)
        check.assert_called_once()
        self.assertEqual(check.call_args.kwargs["profile"], profile())
        build.assert_not_called()

    def test_profile_not_mutated(self):
        value = profile()
        before = copy.deepcopy(value)
        builder.validate_profile(value)
        self.assertEqual(value, before)

    def test_firmware_copy_uses_profile_digest_after_preflight(self):
        source = self.put("firmware.bin", b"original")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        builder.verified_firmware(source, digest)
        source.write_bytes(b"changed")
        target = self.base / "copy.bin"
        with self.assertRaisesRegex(ValueError, "摘要"):
            builder.verified_firmware(source, digest, target)
        self.assertFalse(target.exists())
        source.write_bytes(b"original")
        builder.verified_firmware(source, digest, target)
        self.assertEqual(target.read_bytes(), b"original")

    def test_firmware_fifo_and_symlink_rejected_before_read(self):
        fifo = self.base / "fifo"
        os.mkfifo(fifo)
        link = self.base / "link"
        link.symlink_to(fifo)
        for path in (fifo, link):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                builder.verified_firmware(path, "a" * 64)


if __name__ == "__main__":
    unittest.main()
