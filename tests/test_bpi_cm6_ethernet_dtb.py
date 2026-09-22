#!/usr/bin/env python3
"""以臨時真 DTB 驗證 CM6 單屬性修正與拒絕條件，不接觸板端。"""

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
SPEC = importlib.util.spec_from_file_location("cm6_ethernet_dtb", ROOT / "tools/bpi_cm6_ethernet_dtb.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


@unittest.skipUnless(all(shutil.which(name) for name in ("dtc", "fdtget", "fdtput")), "缺少裝置樹工具")
class EthernetDtbTests(unittest.TestCase):
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
            gmac0_grp {{ phandle = <59>; pinctrl-single,pins = <{pins}>; }};
            gmac1_grp {{ pinctrl-single,pins = <120 1 4160>; }};
        }};
        ethernet@cac80000 {{ pinctrl-0 = <59>; emac,reset-gpio = <48 45 0>; }};
    }};
}};
'''
        self.compile(self.dts)

    def compile(self, text):
        result = subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-b", "2", "-o", str(self.source), "-"],
                                input=text.encode(), capture_output=True, check=True)
        self.assertEqual(result.returncode, 0)

    def build_fixture(self):
        # 夾具雜湊只在測試中替換；正式 CLI 沒有解除來源鎖定的參數。
        with mock.patch.object(MOD, "SOURCE_SHA256", MOD.sha256(self.source.read_bytes())):
            return MOD.build(self.source, self.output, self.manifest)

    def test_real_dtb_changes_only_gmac0_and_keeps_reserved_memory(self):
        before = self.source.read_bytes()
        original = MOD.cells(self.source)
        record = self.build_fixture()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(MOD.cells(self.output), original + MOD.ADDED_CELLS)
        self.assertEqual(record["changed_properties"], [MOD.NODE + "/" + MOD.PROPERTY])
        self.assertEqual(record, json.loads(self.manifest.read_text()))
        self.assertEqual(record["candidate"]["sha256"], MOD.sha256(self.output.read_bytes()))
        source_dts = MOD.run(["dtc", "-I", "dtb", "-O", "dts", "-s", "-o", "-", str(self.source)]).decode()
        output_dts = MOD.run(["dtc", "-I", "dtb", "-O", "dts", "-s", "-o", "-", str(self.output)]).decode()
        before_lines, after_lines = source_dts.splitlines(), output_dts.splitlines()
        differences = [(a, b) for a, b in zip(before_lines, after_lines) if a != b]
        self.assertEqual(len(before_lines), len(after_lines))
        self.assertEqual(len(differences), 1)
        self.assertIn("0xb8 0x00 0xb040", differences[0][1])
        self.assertIn("/memreserve/", output_dts)
        self.assertEqual(struct.unpack_from(">I", self.output.read_bytes(), 28)[0], 2)

    def test_unapproved_or_tampered_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            MOD.build(self.source, self.output, self.manifest)
        digest = MOD.sha256(self.source.read_bytes())
        self.source.write_bytes(self.source.read_bytes() + b"\0")
        with mock.patch.object(MOD, "SOURCE_SHA256", digest), self.assertRaisesRegex(ValueError, "SHA-256"):
            MOD.build(self.source, self.output, self.manifest)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_wrong_board_is_rejected_even_with_matching_fixture_hash(self):
        self.compile(self.dts.replace("bananapi,bpi-cm6", "bananapi,bpi-f3"))
        with self.assertRaisesRegex(ValueError, "板型"):
            self.build_fixture()
        self.assertFalse(self.output.exists())

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

    def test_dangling_output_symlink_is_not_followed(self):
        target = self.base / "absent.dtb"
        self.output.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "覆寫"):
            self.build_fixture()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_same_paths_and_symlink_source_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不同路徑"):
            MOD.build(self.source, self.source, self.manifest)
        alias = self.base / "alias.dtb"
        alias.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            MOD.build(alias, self.output, self.manifest)

    def test_unrelated_property_change_is_rejected(self):
        real_set = MOD.set_cells

        def altered(path, values):
            real_set(path, values)
            if path.name == "candidate.dtb":
                MOD.run(["fdtput", "-t", "s", str(path), "/", "model", "tampered"])

        with mock.patch.object(MOD, "set_cells", side_effect=altered), self.assertRaisesRegex(ValueError, "語義"):
            self.build_fixture()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest.exists())

    def test_wrong_reset_cells_are_rejected(self):
        real_set = MOD.set_cells

        def altered(path, values):
            real_set(path, values)
            if path.name == "candidate.dtb":
                real_set(path, values[:-3] + (0xBC, 0, 0xB040))

        with mock.patch.object(MOD, "set_cells", side_effect=altered), self.assertRaisesRegex(ValueError, "精確附加"):
            self.build_fixture()
        self.assertFalse(self.output.exists())

    def test_second_publish_failure_preserves_existing_file_and_removes_candidate(self):
        real_link = MOD.os.link

        def race(source, destination):
            if destination == self.manifest:
                self.manifest.write_bytes(b"existing")
            return real_link(source, destination)

        with mock.patch.object(MOD.os, "link", side_effect=race), self.assertRaises(FileExistsError):
            self.build_fixture()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.manifest.read_bytes(), b"existing")

    def test_cli_help_is_chinese_and_has_no_source_bypass(self):
        result = subprocess.run(["python3", str(ROOT / "tools/bpi_cm6_ethernet_dtb.py"), "--help"],
                                capture_output=True, text=True, check=True)
        self.assertIn("用法：", result.stdout)
        self.assertIn("原始DTB", result.stdout)
        self.assertNotIn("--force", result.stdout)
        self.assertNotIn("--source-sha", result.stdout)


if __name__ == "__main__":
    unittest.main()
