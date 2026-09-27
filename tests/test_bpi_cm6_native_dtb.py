"""以真實 dtc 編譯與破壞案例核對 CM6 原生 DTB 契約。"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bpi_cm6_native_dtb", ROOT / "tools/bpi_cm6_native_dtb.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
PATCH_ROOT = ROOT / "patch/kernel/archive"
PATCHES = (
    PATCH_ROOT / "bananapicm6-legacy/001-identify-bananapi-cm6-and-defer-bootargs.patch",
    PATCH_ROOT / "bananapicm6-native-eth0/0101-cm6-native-eth0.patch",
    PATCH_ROOT / "bananapicm6-native-dual-imx415/0102-cm6-native-dual-imx415.patch",
)


def command(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, capture_output=True, **kwargs)


def compile_dts(source, output):
    command(["dtc", "-I", "dts", "-O", "dtb", "-o", output, source])


def minimal_dts(dual=True, handle_base=0x200):
    """最小契約案例；所有參照仍交由 dtc 配置及解析。"""
    gmac = " ".join(f"{(index + 1) * 4} 1 {64 if index in (12, 13) else 4160}" for index in range(15))
    camera_status = "okay" if dual else "disabled"
    i2c_refs = 'pinctrl-names = "default"; pinctrl-0 = <&i2c5_pins>;' if dual else ""
    bifmode = "" if dual else "spacemit,bifmode-enable;"
    dts = f'''/dts-v1/;
/ {{
    model = "BananaPi BPI-CM6";
    compatible = "bananapi,bpi-cm6", "spacemit,k1-x";
    chosen {{}};
    soc {{
        gpio: gpio@d4019000 {{ gpio-controller; #gpio-cells = <2>; phandle = <{handle_base}>; }};
        ccu: clock-controller@d4050000 {{ #clock-cells = <1>; }};
        pinctrl@d401e000 {{
            gmac0: gmac0_grp {{ pinctrl-single,pins = <{gmac} 0xb8 0 0xb040>; }};
            gmac1: gmac1_grp {{ }};
            i2c0_pins: i2c0_grp {{ pinctrl-single,pins = <0xdc 1 0xc040 0xe0 1 0xc040>; }};
            i2c5_pins: i2c5_0_grp {{ pinctrl-single,pins = <0x148 5 {0xd040 if dual else 0xc040} 0x14c 5 {0xd040 if dual else 0xc040}>; }};
            camera0: camera0_grp {{ pinctrl-single,pins = <0xd8 1 0x1040>; }};
            camera1: camera1_grp {{ pinctrl-single,pins = <0xec 1 0x1040>; }};
        }};
        i2c@d4010800 {{ spacemit,adapter-id = <0>; status = "okay"; pinctrl-0 = <&i2c0_pins>; }};
        i2c@d4013800 {{ compatible = "spacemit,k1x-i2c"; spacemit,adapter-id = <5>;
            status = "{camera_status}"; {i2c_refs} }};
        cam_sensor@1 {{ status = "disabled"; }};
'''
    for index in (0, 1):
        dts += f'''
        ethernet@cac8{index}000 {{
            compatible = "spacemit,k1x-emac"; status = "okay";
            pinctrl-names = "default"; pinctrl-0 = <&gmac{index}>;
            emac,reset-gpio = <&gpio {45 + index} 0>; emac,reset-active-low;
            emac,reset-delays-us = <0 10000 100000>; phy-handle = <&rgmii{index}>;
            mdio-bus {{ rgmii{index}: phy@{index} {{ compatible = "ethernet-phy-id001c.c916";
                reg = <1>; phy-mode = "rgmii"; }}; }};
        }};
'''
    for index, twsi, phy, enabled, pin, pwdn, reset in ((0, 0, 0, True, 0, 113, 111), (2, 5 if dual else 1, 2, dual, 1, 114, 112)):
        dts += f'''
        cam_sensor@{index} {{ compatible = "spacemit,cam-sensor"; cell-index = <{index}>;
            twsi-index = <{twsi}>; dphy-index = <{phy}>; status = "{"okay" if enabled else "disabled"}";
            clock-names = "cam_mclk{pin}"; clocks = <&ccu {118 + pin}>;
            pinctrl-0 = <&camera{pin}>; pwdn-gpios = <&gpio {pwdn} 0>; reset-gpios = <&gpio {reset} 0>;
        }};
'''
    for index, address in ((0, "d420a000"), (1, "d420a800"), (2, "d4206000")):
        dts += f'''
        dphy{index}: csiphy@{address} {{ compatible = "spacemit,csi-dphy"; cell-index = <{index}>;
            status = "{"disabled" if index == 1 else "okay"}"; {bifmode if index == 2 else ""} }};
        ccic@{address} {{ status = "okay"; spacemit,csiphy = <&dphy{0 if index == 0 else 2}>; }};
'''
    return dts + "}; };\n"


@unittest.skipUnless(shutil.which("dtc") and shutil.which("fdtput"), "需要本機 dtc 與 fdtput")
class NativeValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cm6 native dtb ")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.dts = self.work / "cm6.dts"
        self.dtb = self.work / "cm6.dtb"
        self.build()

    def build(self, dual=True, handle_base=0x200):
        self.dts.write_text(minimal_dts(dual, handle_base))
        compile_dts(self.dts, self.dtb)

    def put(self, node, prop, *values, kind="x"):
        command(["fdtput", "-t", kind, self.dtb, node, prop, *values])

    def test_both_profiles_and_changed_phandle(self):
        for profile in native.CAMERA_PROFILES:
            for handle in (0x200, 0x713):
                with self.subTest(profile=profile, handle=handle):
                    self.build(profile == "dual-imx415", handle)
                    before = self.dtb.read_bytes()
                    result = native.validate(self.dtb, profile)
                    self.assertEqual(result["dtb"]["sha256"], hashlib.sha256(before).hexdigest())
                    self.assertEqual(self.dtb.read_bytes(), before)

    def test_profile_mismatch(self):
        with self.assertRaisesRegex(ValueError, "語義不符"):
            native.validate(self.dtb, "none")
        self.build(False)
        with self.assertRaisesRegex(ValueError, "語義不符"):
            native.validate(self.dtb, "dual-imx415")

    def test_wrong_board(self):
        self.put("/", "compatible", "bananapi,bpi-f3", "spacemit,k1-x", kind="s")
        with self.assertRaisesRegex(ValueError, "compatible"):
            native.validate(self.dtb, "dual-imx415")

    def test_disabled_parent_rejected(self):
        self.put("/soc", "status", "disabled", kind="s")
        with self.assertRaisesRegex(ValueError, "父節點或控制器未啟用"):
            native.validate(self.dtb, "dual-imx415")

    def test_missing_eth0_reset_mux(self):
        tree = native.DeviceTree(self.dtb.read_bytes())
        values = tree.cells(native.PINCTRL + "/gmac0_grp", "pinctrl-single,pins")[:-3]
        self.put(native.PINCTRL + "/gmac0_grp", "pinctrl-single,pins", *(f"{x:x}" for x in values))
        with self.assertRaisesRegex(ValueError, "gmac0_grp"):
            native.validate(self.dtb, "dual-imx415")

    def test_wrong_gpio_and_phy_references(self):
        for prop in ("phy-handle", "emac,reset-gpio"):
            with self.subTest(prop=prop):
                self.build()
                self.put(native.ETH0, prop, "200")
                with self.assertRaisesRegex(ValueError, "參照或參數不符"):
                    native.validate(self.dtb, "dual-imx415")

    def test_wrong_i2c_pin_reference(self):
        tree = native.DeviceTree(self.dtb.read_bytes())
        wrong = tree.cells("/soc/cam_sensor@0", "pinctrl-0")[0]
        self.put(native.I2C5, "pinctrl-0", f"{wrong:x}")
        with self.assertRaisesRegex(ValueError, "pinctrl-0"):
            native.validate(self.dtb, "dual-imx415")

    def test_wrong_i2c_drive_strength(self):
        self.put(native.PINCTRL + "/i2c5_0_grp", "pinctrl-single,pins", "148", "5", "c040", "14c", "5", "c040")
        with self.assertRaisesRegex(ValueError, "i2c5_0_grp"):
            native.validate(self.dtb, "dual-imx415")

    def test_wrong_sensor_and_dphy_properties(self):
        changes = (("/soc/cam_sensor@2", "twsi-index", ("1",)),
                   ("/soc/cam_sensor@2", "dphy-index", ("1",)),
                   ("/soc/csiphy@d4206000", "spacemit,bifmode-enable", ()),
                   ("/soc/cam_sensor@0", "clocks", ("200", "77")))
        for node, prop, values in changes:
            with self.subTest(node=node, prop=prop):
                self.build()
                self.put(node, prop, *values)
                with self.assertRaises(ValueError):
                    native.validate(self.dtb, "dual-imx415")

    def test_duplicate_phandle_rejected(self):
        self.put(native.PINCTRL + "/camera0_grp", "phandle", "200")
        with self.assertRaisesRegex(ValueError, "phandle 重複"):
            native.validate(self.dtb, "dual-imx415")

    def test_bootargs_rejected(self):
        self.put("/chosen", "bootargs", "rdinit=/init", kind="s")
        with self.assertRaisesRegex(ValueError, "bootargs"):
            native.validate(self.dtb, "dual-imx415")

    def test_truncated_invalid_and_oversized_blob(self):
        original = self.dtb.read_bytes()
        for broken in (b"not-dtb", original[:-4], b"\0" * (native.MAX_DTB_BYTES + 1)):
            with self.subTest(length=len(broken)):
                self.dtb.write_bytes(broken)
                with self.assertRaises(ValueError):
                    native.validate(self.dtb, "dual-imx415")

    def test_symlink_and_unknown_profile_rejected(self):
        link = self.work / "link.dtb"
        link.symlink_to(self.dtb)
        with self.assertRaisesRegex(ValueError, "一般檔案"):
            native.validate(link, "dual-imx415")
        with self.assertRaisesRegex(ValueError, "不支援"):
            native.validate(self.dtb, "auto")

    def test_cli_manifest_no_overwrite(self):
        report = self.work / "結果.json"
        argv = [sys.executable, ROOT / "tools/bpi_cm6_native_dtb.py", "--dtb", self.dtb,
                "--camera-profile", "dual-imx415", "--manifest", report]
        command(argv)
        self.assertEqual(json.loads(report.read_text())["camera_profile"], "dual-imx415")
        before = report.read_bytes()
        result = subprocess.run(list(map(str, argv)), capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report.read_bytes(), before)

    def test_global_patch_filename_order(self):
        self.assertEqual(sorted(PATCHES, key=lambda path: path.name), list(PATCHES))
        for patch in PATCHES[1:]:
            self.assertFalse((patch.parent / "series.conf").exists())


@unittest.skipUnless(os.environ.get("CM6_NATIVE_KERNEL_SOURCE"), "未指定固定核心來源；可設 CM6_NATIVE_KERNEL_SOURCE 啟用真實 DTS 編譯")
class FixedKernelCompilation(unittest.TestCase):
    def test_pinned_source_patch_compile_and_semantic_validation(self):
        source = Path(os.environ["CM6_NATIVE_KERNEL_SOURCE"]).resolve()
        dts_relative = Path("arch/riscv/boot/dts/spacemit")
        expected = {
            "k1-x_deb1.dts": "3644a30c01e10fc063f463997b9f4c60d920dadf006aee252dda4b6e1b04420b",
            "k1-x_pinctrl.dtsi": "53c5aa1fc4e0f7851845f7d2ca9269a0c9a21996fc6b005fd65b70240470876c",
            "k1-x-camera-sdk.dtsi": "79de254c737f53a587808772a1faa92eef73046a577312392be77adff34f3a32",
            "k1-x-camera-sensor.dtsi": "4013a20e902800b7d23d1f1c15ce79bf419949aa775b891c4880442c9462b264",
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((source / dts_relative / name).read_bytes()).hexdigest(), digest)
        with tempfile.TemporaryDirectory(prefix="cm6 固定來源 ") as temporary:
            work = Path(temporary)
            shutil.copytree(source / dts_relative, work / dts_relative, symlinks=True)
            shutil.copytree(source / "include", work / "include", symlinks=True)
            # 來源可為原始提交擷取目錄；不能預先帶入另一份 CM6 DTS。
            self.assertFalse((work / dts_relative / "k1-x_bpi_cm6.dts").exists())
            for patch, profile in zip(sorted(PATCHES, key=lambda path: path.name), (None, "none", "dual-imx415")):
                command(["patch", "--batch", "--fuzz=0", "-p1", "-i", patch], cwd=work)
                preprocessed = command(["cpp", "-nostdinc", "-undef", "-D__DTS__", "-x", "assembler-with-cpp",
                                        "-I", work / "include", work / dts_relative / "k1-x_bpi_cm6.dts"]).stdout
                pre = work / "cm6.dts"
                pre.write_bytes(preprocessed)
                blob = work / "cm6.dtb"
                compile_dts(pre, blob)
                if profile:
                    self.assertEqual(native.validate(blob, profile)["camera_profile"], profile)
                    other = "none" if profile == "dual-imx415" else "dual-imx415"
                    with self.assertRaises(ValueError):
                        native.validate(blob, other)
                else:
                    with self.assertRaisesRegex(ValueError, "gmac0_grp"):
                        native.validate(blob, "none")


if __name__ == "__main__":
    unittest.main()
