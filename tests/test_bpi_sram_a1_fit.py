#!/usr/bin/env python3
"""六項來源與 offset 回歸；比對兩份既有獨立建置，不重編 SPL 或操作硬體。"""

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
builder = importlib.import_module("tools.build_bpi_sram_a1_fit")


class A1FitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = Path(os.environ.get("BPI_SRAM_A1_FIT_BUILD", str(
            ROOT / "output/evidence/bpi-sram-a1-fit-2048-002")))
        cls.report = json.loads((cls.output / "build-report.json").read_text())
        cls.combined = (builder.ARCHIVE / "u-boot-sunxi-with-spl.bin").read_bytes()
        cls.spl = (cls.output / "uboot-build/spl/sunxi-spl.bin").read_bytes()
        cls.elf = (cls.output / "uboot-build/spl/u-boot-spl").read_bytes()

    def test_01_pinned_source_and_config_delta(self):
        manifest = builder.source_manifest(builder.SOURCE)
        self.assertEqual(builder.digest(builder.canonical(manifest)), builder.MANIFEST_SHA)
        self.assertEqual(self.report["config_changes"][builder.SECTOR_KEY], ["0x40", "0x7f0"])
        self.assertTrue(set(self.report["config_changes"]) <= {builder.SECTOR_KEY, "CONFIG_GCC_VERSION"})
        self.assertFalse((self.output / "source/.git").exists())
        self.assertTrue(self.report["originals_unchanged"])

    def test_02_matching_fit_components_and_reserved_region(self):
        fit, components = builder.extract_fit(self.combined)
        self.assertEqual((self.output / "a1-fit.itb").read_bytes(), fit)
        region = (self.output / "a1-fit-region.bin").read_bytes()
        self.assertEqual(len(region), 0x100000)
        self.assertEqual(region, fit + bytes(len(region) - len(fit)))
        self.assertLessEqual(2048 * 512 + len(region), 0x200000)
        self.assertEqual(components["atf"], (builder.ARCHIVE / "bl31.bin").read_bytes())
        self.assertEqual(components["uboot"] + components["fdt-1"],
                         (builder.ARCHIVE / "u-boot.bin").read_bytes())

    def test_03_reject_wrong_archive_length_or_hash(self):
        for data in (self.combined[:-1], self.combined + b"\0", b"\0" * len(self.combined),
                     self.combined[:40960] + bytes(0x100001)):
            with self.subTest(size=len(data)), self.assertRaisesRegex(ValueError, "歸檔"):
                builder.extract_fit(data)

    def test_04_actual_max_and_data_offset_path(self):
        for raw, maximum, absolute in ((None, 2032, 2048), (64, 80, 96), (2048, 2048, 2064)):
            with self.subTest(raw=raw):
                result = builder.probe_first_read(self.elf, self.spl, raw_override=raw)
                self.assertEqual((result["after_max_sector"], result["absolute_lba"]), (maximum, absolute))
        config = builder.config_values((self.output / "uboot-build/.config").read_bytes())
        self.assertEqual(builder.absolute_lba(config, len(self.spl)), 2048)
        config[builder.DATA_KEY] = "0x0"
        with self.assertRaisesRegex(ValueError, "DATA_PART_OFFSET"):
            builder.absolute_lba(config, len(self.spl))

    def test_05_bridge_embeds_new_spl_and_output_is_exclusive(self):
        package = (self.output / "bridge/bridge-package.bin").read_bytes()
        parsed = builder.bridge.package.parse_package(package)
        self.assertEqual((parsed["version"], parsed["kind"], parsed["runtime_size"]), (3, 4, 0x18000))
        self.assertEqual(package[512 + 0x2000:512 + 0xc000], self.spl)
        self.assertNotEqual(builder.digest(self.spl), builder.SPL_SHA)
        with self.assertRaisesRegex(ValueError, "新目錄"):
            builder.build(builder.SOURCE, builder.ARCHIVE, self.output)

    def test_06_existing_clean_builds_and_bridge_are_reproducible(self):
        earlier = self.output.parent / "bpi-sram-a1-fit-2048-001"
        # 001 僅模型失敗；不改寫失敗報告，也不將它當作已通過的交付。
        first_report = json.loads((earlier / "build-report.json").read_text())
        self.assertEqual(first_report["status"], "未通過")
        self.assertEqual(first_report["source_manifest_sha256"], self.report["source_manifest_sha256"])
        self.assertEqual(first_report["config_changes"], self.report["config_changes"])
        for name in ("a1-fit.itb", "a1-fit-region.bin", "uboot-build/spl/sunxi-spl.bin"):
            with self.subTest(artifact=name):
                self.assertEqual((self.output / name).read_bytes(), (earlier / name).read_bytes())
        with tempfile.TemporaryDirectory(prefix="bpi-a1-fit-") as temporary:
            other = Path(temporary) / "bridge"
            builder.bridge.build(earlier / "uboot-build/spl/sunxi-spl.bin",
                                 builder.digest(self.spl), other)
            for name in ("bridge.bin", "bridge-package.bin", "bridge.elf"):
                with self.subTest(artifact=name):
                    self.assertEqual((self.output / "bridge" / name).read_bytes(), (other / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
