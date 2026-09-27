#!/usr/bin/env python3
"""驗證原生編譯的 CM6 DTB 語義，保留舊工具的固定 SHA 驗證界線。"""

import argparse
import hashlib
import json
from pathlib import Path
import stat
import struct
import sys


KERNEL_COMMIT = "0d0af0d895251383baee939d44e523699e31889f"
MAX_DTB_BYTES = 2 * 1024 * 1024
PINCTRL = "/soc/pinctrl@d401e000"
GPIO = "/soc/gpio@d4019000"
CCU = "/soc/clock-controller@d4050000"
ETH0 = "/soc/ethernet@cac80000"
I2C5 = "/soc/i2c@d4013800"
CAMERA_PROFILES = ("none", "dual-imx415")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _cells(raw):
    require(len(raw) % 4 == 0, "DTB 屬性不是完整的 32 位元資料")
    return struct.unpack(">" + "I" * (len(raw) // 4), raw)


class DeviceTree:
    """解析有界 FDT，依實際 phandle 解參照，不依賴編譯器編號。"""

    def __init__(self, data):
        require(40 <= len(data) <= MAX_DTB_BYTES, "DTB 長度超出允許範圍")
        magic, total, off_struct, off_strings, off_reserve, version, last, _, size_strings, size_struct = struct.unpack_from(">10I", data)
        require(magic == 0xD00DFEED and total == len(data), "DTB 標頭或總長度不符")
        require(version == 17 and last <= 17, "只接受第 17 版 FDT 結構")
        require(off_struct % 4 == 0 and off_reserve % 8 == 0, "DTB 區段未對齊")
        require(40 <= off_struct <= total - size_struct and 40 <= off_strings <= total - size_strings,
                "DTB 區段超出檔案")
        require(off_struct + size_struct <= off_strings or off_strings + size_strings <= off_struct,
                "DTB 結構與字串區段重疊")
        require(40 <= off_reserve <= total - 16, "DTB 保留記憶體區段不合法")
        reserve_end = off_reserve
        while True:
            require(reserve_end + 16 <= total, "DTB 保留記憶體缺少結束項")
            entry = struct.unpack_from(">QQ", data, reserve_end)
            reserve_end += 16
            if entry == (0, 0):
                break
        require(reserve_end <= min(off_struct, off_strings), "DTB 保留記憶體區段重疊")
        strings = data[off_strings:off_strings + size_strings]
        block = data[off_struct:off_struct + size_struct]
        self.nodes = {}
        stack = []
        pos = 0
        ended = False
        while pos < len(block):
            require(pos + 4 <= len(block), "DTB 結構遭截斷")
            token = struct.unpack_from(">I", block, pos)[0]
            pos += 4
            if token == 1:
                end = block.find(b"\0", pos)
                require(end >= pos, "DTB 節點名稱未結束")
                name = self._text(block[pos:end])
                require("/" not in name and (name or not self.nodes), "DTB 節點名稱不合法")
                path = (stack[-1].rstrip("/") + "/" + name) if stack else "/"
                require(stack or name == "", "DTB 根節點名稱不合法")
                require(path not in self.nodes, "DTB 節點重複：" + path)
                self.nodes[path] = {}
                stack.append(path)
                pos = (end + 4) & ~3
            elif token == 2:
                require(bool(stack), "DTB 節點結束順序不合法")
                stack.pop()
            elif token == 3:
                require(stack and pos + 8 <= len(block), "DTB 屬性標頭不完整")
                length, offset = struct.unpack_from(">II", block, pos)
                pos += 8
                require(pos + length <= len(block) and offset < len(strings), "DTB 屬性超出區段")
                end = strings.find(b"\0", offset)
                require(end >= offset, "DTB 屬性名稱未結束")
                name = self._text(strings[offset:end])
                require(name and name not in self.nodes[stack[-1]], "DTB 屬性名稱空白或重複")
                self.nodes[stack[-1]][name] = block[pos:pos + length]
                pos = (pos + length + 3) & ~3
            elif token == 4:
                continue
            elif token == 9:
                require(not stack and "/" in self.nodes, "DTB 結束前仍有未封閉節點")
                require(not any(block[pos:]), "DTB 結束後仍有非零結構")
                ended = True
                break
            else:
                raise ValueError("DTB 包含未知結構標記")
        require(ended, "DTB 缺少結束標記")
        self.handles = {}
        for path, props in self.nodes.items():
            values = []
            for name in ("phandle", "linux,phandle"):
                if name in props:
                    values.append(_cells(props[name]))
            if values:
                require(all(len(value) == 1 and 0 < value[0] < 0xFFFFFFFF for value in values),
                        "DTB phandle 格式不合法：" + path)
                require(all(value == values[0] for value in values), "DTB phandle 別名不一致：" + path)
                handle = values[0][0]
                require(handle not in self.handles, "DTB phandle 重複")
                self.handles[handle] = path

    @staticmethod
    def _text(raw):
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("DTB 名稱含非 ASCII 字元") from exc

    def raw(self, node, prop):
        require(node in self.nodes, "DTB 缺少節點：" + node)
        return self.nodes[node].get(prop)

    def cells(self, node, prop):
        raw = self.raw(node, prop)
        require(raw is not None, "DTB 缺少屬性：" + node + "/" + prop)
        return _cells(raw)

    def text(self, node, prop):
        raw = self.raw(node, prop)
        require(raw is not None and raw.endswith(b"\0"), "DTB 字串屬性不完整：" + node + "/" + prop)
        return tuple(self._text(item) for item in raw[:-1].split(b"\0"))

    def reference(self, node, prop, target, arguments=()):
        values = self.cells(node, prop)
        require(len(values) == 1 + len(arguments) and values[1:] == arguments and
                self.handles.get(values[0]) == target,
                "DTB 參照或參數不符：" + node + "/" + prop)


def validate(path, camera_profile="none"):
    """驗證編譯產物並回傳紀錄；不修改 DTB，也不推論來源或硬體驗收。"""
    require(camera_profile in CAMERA_PROFILES, "不支援的 CM6 相機配置")
    path = Path(path)
    mode = path.lstat().st_mode
    require(stat.S_ISREG(mode) and not path.is_symlink(), "DTB 必須是一般檔案，不接受連結或裝置")
    with path.open("rb") as stream:
        data = stream.read(MAX_DTB_BYTES + 1)
    tree = DeviceTree(data)
    checks = []

    def equal(node, prop, expected, kind="cells"):
        observed = getattr(tree, kind)(node, prop)
        require(observed == expected, "DTB 語義不符：" + node + "/" + prop)
        checks.append(node + "/" + prop)

    def ref(node, prop, target, arguments=()):
        tree.reference(node, prop, target, arguments)
        checks.append(node + "/" + prop + " -> " + target)

    equal("/", "compatible", ("bananapi,bpi-cm6", "spacemit,k1-x"), "text")
    equal("/", "model", ("BananaPi BPI-CM6",), "text")
    equal("/chosen", "bootargs", None, "raw")
    for node in ("/soc", PINCTRL, GPIO, CCU):
        require(tree.raw(node, "status") in (None, b"okay\0", b"ok\0"),
                "DTB 必要父節點或控制器未啟用：" + node)
        checks.append(node + "/status")
    pins = tuple(value for index in range(15) for value in
                 ((index + 1) * 4, 1, 0x40 if index in (12, 13) else 0x1040))
    equal(PINCTRL + "/gmac0_grp", "pinctrl-single,pins", pins + (0xB8, 0, 0xB040))
    equal(GPIO, "gpio-controller", b"", "raw")
    equal(GPIO, "#gpio-cells", (2,))
    for index in (0, 1):
        eth = f"/soc/ethernet@cac8{index}000"
        equal(eth, "compatible", ("spacemit,k1x-emac",), "text")
        equal(eth, "status", ("okay",), "text")
        equal(eth, "pinctrl-names", ("default",), "text")
        ref(eth, "pinctrl-0", PINCTRL + f"/gmac{index}_grp")
        ref(eth, "emac,reset-gpio", GPIO, (45 + index, 0))
        equal(eth, "emac,reset-active-low", b"", "raw")
        equal(eth, "emac,reset-delays-us", (0, 10000, 100000))
        phy = eth + f"/mdio-bus/phy@{index}"
        ref(eth, "phy-handle", phy)
        equal(phy, "compatible", ("ethernet-phy-id001c.c916",), "text")
        equal(phy, "reg", (1,))
        equal(phy, "phy-mode", ("rgmii",), "text")
    equal(I2C5, "spacemit,adapter-id", (5,))
    equal(I2C5, "compatible", ("spacemit,k1x-i2c",), "text")
    dual = camera_profile == "dual-imx415"
    equal(I2C5, "status", ("okay" if dual else "disabled",), "text")
    equal(PINCTRL + "/i2c5_0_grp", "pinctrl-single,pins",
          (0x148, 5, 0xD040 if dual else 0xC040, 0x14C, 5, 0xD040 if dual else 0xC040))
    if dual:
        equal(I2C5, "pinctrl-names", ("default",), "text")
        ref(I2C5, "pinctrl-0", PINCTRL + "/i2c5_0_grp")
    else:
        equal(I2C5, "pinctrl-names", None, "raw")
        equal(I2C5, "pinctrl-0", None, "raw")
    for index, twsi, dphy, enabled, camera_pin, pwdn, reset in (
            (0, 0, 0, True, 0, 113, 111), (2, 5 if dual else 1, 2, dual, 1, 114, 112)):
        sensor = f"/soc/cam_sensor@{index}"
        equal(sensor, "compatible", ("spacemit,cam-sensor",), "text")
        equal(sensor, "cell-index", (index,))
        equal(sensor, "twsi-index", (twsi,))
        equal(sensor, "dphy-index", (dphy,))
        equal(sensor, "status", ("okay" if enabled else "disabled",), "text")
        equal(sensor, "clock-names", (f"cam_mclk{camera_pin}",), "text")
        ref(sensor, "clocks", CCU, (118 + camera_pin,))
        ref(sensor, "pinctrl-0", PINCTRL + f"/camera{camera_pin}_grp")
        ref(sensor, "pwdn-gpios", GPIO, (pwdn, 0))
        ref(sensor, "reset-gpios", GPIO, (reset, 0))
    equal("/soc/cam_sensor@1", "status", ("disabled",), "text")
    for index, address in ((0, "d420a000"), (1, "d420a800"), (2, "d4206000")):
        phy = "/soc/csiphy@" + address
        equal(phy, "compatible", ("spacemit,csi-dphy",), "text")
        equal(phy, "cell-index", (index,))
        equal(phy, "status", ("disabled" if index == 1 else "okay",), "text")
        ccic = "/soc/ccic@" + address
        equal(ccic, "status", ("okay",), "text")
        ref(ccic, "spacemit,csiphy", "/soc/csiphy@" + (address if index == 0 else "d4206000"))
    equal("/soc/csiphy@d4206000", "spacemit,bifmode-enable", None if dual else b"", "raw")
    equal("/soc/i2c@d4010800", "spacemit,adapter-id", (0,))
    equal("/soc/i2c@d4010800", "status", ("okay",), "text")
    ref("/soc/i2c@d4010800", "pinctrl-0", PINCTRL + "/i2c0_grp")
    equal(PINCTRL + "/i2c0_grp", "pinctrl-single,pins", (0xDC, 1, 0xC040, 0xE0, 1, 0xC040))
    equal(PINCTRL + "/camera0_grp", "pinctrl-single,pins", (0xD8, 1, 0x1040))
    equal(PINCTRL + "/camera1_grp", "pinctrl-single,pins", (0xEC, 1, 0x1040))
    return {
        "schema_version": 1, "board": "bpi-cm6", "camera_profile": camera_profile,
        "dtb": {"name": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        "contract_kernel_commit": KERNEL_COMMIT,
        "status": "離線語義驗證通過", "checked_properties": checks,
        "source_validation": "本工具核對編譯產物的板級契約；核心來源與補丁身分須由建置紀錄另外證明。",
        "hardware_validation": "未執行實機；不代表網路、相機幀率、畫質或停止流程驗收通過。",
    }


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self):
        super().__init__(description=__doc__, add_help=False)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明後結束")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數不完整或無法辨識，請使用 --help 檢查用法。\n")


def main():
    parser = ChineseArgumentParser()
    parser.add_argument("--dtb", required=True, type=Path, help="本次編譯的 CM6 DTB")
    parser.add_argument("--camera-profile", required=True, choices=CAMERA_PROFILES, help="明確選擇相機配置")
    parser.add_argument("--manifest", type=Path, help="尚不存在的 JSON 紀錄；省略時只輸出至標準輸出")
    args = parser.parse_args()
    try:
        record = validate(args.dtb, args.camera_profile)
        output = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if args.manifest:
            with args.manifest.open("x", encoding="utf-8") as stream:
                stream.write(output)
        print(output, end="")
    except (ValueError, OSError) as exc:
        print("CM6 DTB 驗證停止：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
