#!/usr/bin/env python3
"""用真 DTB 驗證相機變更範圍、eth0 保留與拒絕發布條件。"""

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cm6_camera_dtb", ROOT / "tools/bpi_cm6_camera_dtb.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


@unittest.skipUnless(all(shutil.which(name) for name in ("dtc", "fdtget", "fdtput")), "缺少裝置樹工具")
class CameraDtbTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source.dtb"
        self.output = self.base / "candidate.dtb"
        self.manifest = self.base / "manifest.json"
        pins = " ".join(f"{offset * 4} 1 4160" for offset in range(1, 16))
        self.dts = f'''/dts-v1/;
/memreserve/ 0x100000 0x2000;
/ {{
    compatible = "bananapi,bpi-cm6", "spacemit,k1-x";
    model = "BananaPi BPI-CM6";
    soc {{
        pinctrl@d401e000 {{
            gmac0_grp {{ phandle = <59>; pinctrl-single,pins = <{pins} 0xb8 0 0xb040>; }};
            gmac1_grp {{ pinctrl-single,pins = <120 1 4160>; }};
            i2c5_0_grp {{ pinctrl-single,pins = <0x148 5 0xc040 0x14c 5 0xc040>; }};
        }};
        i2c@d4013800 {{ spacemit,adapter-id = <5>; status = "disabled"; }};
        cam_sensor@0 {{ status = "okay"; twsi-index = <0>; }};
        cam_sensor@2 {{ twsi-index = <1>; status = "disabled"; }};
        csiphy@d4206000 {{ spacemit,bifmode-enable; }};
        ethernet@cac80000 {{ pinctrl-0 = <59>; emac,reset-gpio = <48 45 0>; }};
        untouched {{ phandle = <134>; marker = "保留資料"; }};
    }};
}};
'''
        self.compile(self.dts)
        # 由獨立明列的 fdtput 命令建立夾具預期值，不取正式工具的變更表。
        expected = self.base / "expected.dtb"
        expected.write_bytes(self.source.read_bytes())
        edits = (
            ("-t", "x", str(expected), "/soc/pinctrl@d401e000/i2c5_0_grp", "phandle", "87"),
            ("-t", "x", str(expected), "/soc/pinctrl@d401e000/i2c5_0_grp", "pinctrl-single,pins", "148", "5", "d040", "14c", "5", "d040"),
            ("-t", "s", str(expected), "/soc/i2c@d4013800", "pinctrl-names", "default"),
            ("-t", "x", str(expected), "/soc/i2c@d4013800", "pinctrl-0", "87"),
            ("-t", "s", str(expected), "/soc/i2c@d4013800", "status", "okay"),
            ("-t", "x", str(expected), "/soc/cam_sensor@2", "twsi-index", "5"),
            ("-t", "s", str(expected), "/soc/cam_sensor@2", "status", "okay"),
            ("-d", str(expected), "/soc/csiphy@d4206000", "spacemit,bifmode-enable"),
        )
        for args in edits:
            subprocess.run(["fdtput", *args], capture_output=True, check=True)
        self.expected = expected.read_bytes()

    def compile(self, text):
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-b", "2", "-o", str(self.source), "-"],
                       input=text.encode(), capture_output=True, check=True)

    def build_fixture(self):
        # 正式 CLI 不提供來源或輸出雜湊的覆寫參數。
        with mock.patch.object(MOD, "SOURCE_SHA256", MOD.sha256(self.source.read_bytes())), \
                mock.patch.object(MOD, "CANDIDATE_SHA256", MOD.sha256(self.expected)):
            return MOD.build(self.source, self.output, self.manifest)

    def test_real_dtb_preserves_ethernet_reserved_memory_and_boot_cpu(self):
        before = self.source.read_bytes()
        ethernet = MOD.get(self.source, MOD.GMAC, MOD.PIN_PROPERTY)
        record = self.build_fixture()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.output.read_bytes(), self.expected)
        self.assertEqual(MOD.get(self.output, MOD.GMAC, MOD.PIN_PROPERTY), ethernet)
        self.assertEqual(MOD.get(self.output, "/soc/cam_sensor@0", "status", "s"), "okay")
        self.assertEqual(MOD.get(self.output, MOD.PHY, "spacemit,bifmode-enable"), None)
        self.assertEqual(len(record["changed_properties"]), 8)
        self.assertEqual(record, json.loads(self.manifest.read_text()))
        self.assertEqual(record["candidate"]["sha256"], MOD.sha256(self.expected))
        self.assertTrue(record["semantic_restore"]["equal"])
        dts = MOD.run(["dtc", "-I", "dtb", "-O", "dts", "-s", "-o", "-", str(self.output)])
        self.assertIn(b"/memreserve/", dts)
        self.assertEqual(struct.unpack_from(">I", self.output.read_bytes(), 28)[0], 2)

    def test_unapproved_and_tampered_source_hash_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "來源 SHA-256"):
            MOD.build(self.source, self.output, self.manifest)
        digest = MOD.sha256(self.source.read_bytes())
        self.source.write_bytes(self.source.read_bytes() + b"\0")
        with mock.patch.object(MOD, "SOURCE_SHA256", digest), self.assertRaisesRegex(ValueError, "來源 SHA-256"):
            MOD.build(self.source, self.output, self.manifest)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_wrong_board_rejected_with_matching_fixture_hash(self):
        self.compile(self.dts.replace("bananapi,bpi-cm6", "bananapi,bpi-f3"))
        with self.assertRaisesRegex(ValueError, "板型"):
            self.build_fixture()
        self.assertFalse(self.output.exists())

    def test_source_without_ethernet_fix_is_rejected(self):
        self.compile(self.dts.replace("0xb8 0 0xb040", "0xbc 0 0xb040"))
        with self.assertRaisesRegex(ValueError, "eth0"):
            self.build_fixture()

    def test_wrong_bus_or_original_camera_property_is_rejected(self):
        variants = (("spacemit,adapter-id = <5>", "spacemit,adapter-id = <4>", "I²C 5"),
                    ("twsi-index = <1>", "twsi-index = <3>", "來源屬性"),
                    ("spacemit,bifmode-enable;", "spacemit,bifmode-enable = <1>;", "來源屬性"))
        for original, changed, message in variants:
            with self.subTest(changed=changed):
                self.compile(self.dts.replace(original, changed))
                with self.assertRaisesRegex(ValueError, message):
                    self.build_fixture()
                self.assertFalse(self.output.exists())

    def test_conflicting_phandle_is_rejected(self):
        self.compile(self.dts.replace("phandle = <134>", "phandle = <135>"))
        with self.assertRaisesRegex(ValueError, "phandle"):
            self.build_fixture()

    def test_unapproved_candidate_hash_is_rejected(self):
        with mock.patch.object(MOD, "SOURCE_SHA256", MOD.sha256(self.source.read_bytes())), \
                self.assertRaisesRegex(ValueError, "候選 SHA-256"):
            MOD.build(self.source, self.output, self.manifest)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_existing_output_or_manifest_is_never_overwritten(self):
        for occupied in (self.output, self.manifest):
            with self.subTest(occupied=occupied.name):
                occupied.write_bytes(b"existing")
                with self.assertRaisesRegex(ValueError, "覆寫"):
                    self.build_fixture()
                self.assertEqual(occupied.read_bytes(), b"existing")
                self.assertEqual(self.output.exists(), occupied == self.output)
                self.assertEqual(self.manifest.exists(), occupied == self.manifest)
                occupied.unlink()

    def test_output_symlink_and_source_alias_are_rejected(self):
        target = self.base / "absent.dtb"
        self.output.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "覆寫"):
            self.build_fixture()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())
        alias = self.base / "alias.dtb"
        alias.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            MOD.build(alias, self.output, self.manifest)

    def test_same_paths_missing_parent_and_oversized_source_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不同路徑"):
            MOD.build(self.source, self.source, self.manifest)
        with self.assertRaisesRegex(ValueError, "目錄不存在"):
            MOD.build(self.source, self.base / "missing" / "candidate.dtb", self.manifest)
        with mock.patch.object(MOD, "MAX_DTB_BYTES", 1), self.assertRaisesRegex(ValueError, "大小"):
            self.build_fixture()

    def test_unrelated_property_change_is_rejected(self):
        real_put = MOD.put

        def altered(path, node, prop, value, kind="x"):
            real_put(path, node, prop, value, kind)
            if path.name == "candidate.dtb":
                real_put(path, "/", "model", "已竄改", "s")

        with mock.patch.object(MOD, "put", side_effect=altered), self.assertRaisesRegex(ValueError, "語義"):
            self.build_fixture()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_modified_ethernet_fix_is_rejected(self):
        real_put = MOD.put

        def altered(path, node, prop, value, kind="x"):
            real_put(path, node, prop, value, kind)
            if path.name == "candidate.dtb":
                real_put(path, MOD.GMAC, MOD.PIN_PROPERTY, (0xBC, 0, 0xB040))

        with mock.patch.object(MOD, "put", side_effect=altered), self.assertRaisesRegex(ValueError, "eth0 修正"):
            self.build_fixture()
        self.assertFalse(self.output.exists())

    def test_wrong_camera_patch_is_rejected(self):
        real_put = MOD.put

        def altered(path, node, prop, value, kind="x"):
            if path.name == "candidate.dtb" and node == MOD.SENSOR and prop == "twsi-index":
                value = (4,)
            real_put(path, node, prop, value, kind)

        with mock.patch.object(MOD, "put", side_effect=altered), self.assertRaisesRegex(ValueError, "未精確套用"):
            self.build_fixture()
        self.assertFalse(self.output.exists())

    def test_second_publish_failure_keeps_existing_file_and_removes_candidate(self):
        real_link = MOD.os.link

        def race(source, destination):
            if destination == self.manifest:
                self.manifest.write_bytes(b"existing")
            return real_link(source, destination)

        with mock.patch.object(MOD.os, "link", side_effect=race), self.assertRaises(FileExistsError):
            self.build_fixture()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.manifest.read_bytes(), b"existing")

    def test_cli_help_and_failures_use_chinese_without_hash_override(self):
        script = str(ROOT / "tools/bpi_cm6_camera_dtb.py")
        result = subprocess.run(["python3", script, "--help"], capture_output=True, text=True, check=True)
        self.assertIn("用法：", result.stdout)
        self.assertIn("已修正DTB", result.stdout)
        self.assertNotIn("--force", result.stdout)
        self.assertNotIn("--source-sha", result.stdout)
        bad = subprocess.run(["python3", script, "--source", str(self.source), "--output", str(self.output),
                              "--manifest", str(self.manifest)], capture_output=True, text=True, check=False)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("來源 SHA-256", bad.stderr)
        self.assertNotIn("Traceback", bad.stderr)


if __name__ == "__main__":
    unittest.main()
