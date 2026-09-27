#!/usr/bin/env python3
"""離線重現 CM6 雙 IMX415 的八項 DTB 調整，保留既有 eth0 修正。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


SOURCE_SHA256 = "f0b9795fda72868cd2bc9a6db0cf5aa2f15ea494f114a5b42adfc1fa931962c1"
CANDIDATE_SHA256 = "91bb7a9cde6380e16d9dbc12161212aad768cf145de79dd2f51665366e0065c3"
MAX_DTB_BYTES = 2 * 1024 * 1024
PIN = "/soc/pinctrl@d401e000/i2c5_0_grp"
BUS = "/soc/i2c@d4013800"
SENSOR = "/soc/cam_sensor@2"
PHY = "/soc/csiphy@d4206000"
GMAC = "/soc/pinctrl@d401e000/gmac0_grp"
PIN_PROPERTY = "pinctrl-single,pins"
NEW_PHANDLE = 0x87
CHANGES = (
    (PIN, "phandle", "x", None, (NEW_PHANDLE,)),
    (PIN, PIN_PROPERTY, "x", (0x148, 5, 0xC040, 0x14C, 5, 0xC040),
     (0x148, 5, 0xD040, 0x14C, 5, 0xD040)),
    (BUS, "pinctrl-names", "s", None, "default"),
    (BUS, "pinctrl-0", "x", None, (NEW_PHANDLE,)),
    (BUS, "status", "s", "disabled", "okay"),
    (SENSOR, "twsi-index", "x", (1,), (5,)),
    (SENSOR, "status", "s", "disabled", "okay"),
    (PHY, "spacemit,bifmode-enable", "x", (), None),
)


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, **kwargs):
        super().__init__(add_help=False, **kwargs)
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


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def run(argv):
    """只處理暫存一般檔案；不接觸板端、映像或區塊裝置。"""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, env={**os.environ, "LC_ALL": "C"}, check=False)
    except FileNotFoundError as exc:
        raise ValueError("找不到必要工具：" + argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("裝置樹工具執行逾時：" + argv[0]) from exc
    require(result.returncode == 0, "裝置樹工具執行失敗：" + argv[0])
    return result.stdout


def get(path, node, prop, kind="x"):
    properties = run(["fdtget", "-p", str(path), node]).decode().splitlines()
    if prop not in properties:
        return None
    value = run(["fdtget", "-t", kind, str(path), node, prop]).decode().strip()
    if kind == "s":
        return value
    try:
        return tuple(int(cell, 16) for cell in value.split())
    except ValueError as exc:
        raise ValueError("裝置樹屬性不是合法的 32 位元資料：" + node + "/" + prop) from exc


def put(path, node, prop, value, kind="x"):
    if value is None:
        run(["fdtput", "-d", str(path), node, prop])
    else:
        values = [value] if kind == "s" else [format(cell, "x") for cell in value]
        run(["fdtput", "-t", kind, str(path), node, prop, *values])


def canonical(path):
    return run(["dtc", "-I", "dtb", "-O", "dtb", "-s", "-o", "-", str(path)])


def validate_source(path):
    require(get(path, "/", "compatible", "s") == "bananapi,bpi-cm6 spacemit,k1-x",
            "DTB 板型不是指定的 BPI-CM6")
    ethernet_pins = get(path, GMAC, PIN_PROPERTY)
    require(ethernet_pins is not None and len(ethernet_pins) == 48 and
            ethernet_pins[-3:] == (0xB8, 0, 0xB040), "來源缺少既有 eth0 GPIO45 修正")
    require(get(path, BUS, "spacemit,adapter-id") == (5,), "來源的 I²C 5 識別不符")
    dts = run(["dtc", "-I", "dtb", "-O", "dts", "-o", "-", str(path)]).decode()
    handles = [int(cell, 16) for cell in re.findall(r"\b(?:linux,)?phandle = <0x([0-9a-f]+)>;", dts)]
    require(handles and max(handles) == NEW_PHANDLE - 1, "來源 phandle 範圍不符，拒絕建立衝突參照")
    for node, prop, kind, before, _after in CHANGES:
        require(get(path, node, prop, kind) == before, "來源屬性不符：" + node + "/" + prop)


def verify_only_changes(source, candidate, directory):
    for node, prop, kind, _before, after in CHANGES:
        require(get(candidate, node, prop, kind) == after,
                "候選屬性未精確套用：" + node + "/" + prop)
    require(get(candidate, GMAC, PIN_PROPERTY) == get(source, GMAC, PIN_PROPERTY),
            "候選改變了既有 eth0 修正")
    restored = directory / "semantic-restored.dtb"
    restored.write_bytes(candidate.read_bytes())
    for node, prop, kind, before, _after in reversed(CHANGES):
        put(restored, node, prop, before, kind)
    source_canonical = canonical(source)
    require(canonical(restored) == source_canonical, "候選改變了指定八項屬性以外的裝置樹語義")
    return sha256(source_canonical)


def publish(staged):
    """以不覆寫的硬連結發布，失敗時僅撤回本次新增的檔案。"""
    created = []
    try:
        for temporary, destination in staged:
            os.link(temporary, destination)
            created.append((temporary, destination))
    except OSError:
        for temporary, destination in reversed(created):
            try:
                if os.path.samestat(temporary.stat(), destination.lstat()):
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise


def build(source, output, manifest):
    source, output, manifest = map(Path, (source, output, manifest))
    require(source.is_file() and not source.is_symlink(), "來源必須是一般 DTB 檔案，不接受符號連結或裝置")
    require(source.stat().st_size <= MAX_DTB_BYTES, "來源 DTB 大小超出允許範圍")
    require(len({path.resolve() for path in (source, output, manifest)}) == 3,
            "來源、候選與清單必須是不同路徑")
    for path in (output, manifest):
        require(not os.path.lexists(path), "目的檔已存在，拒絕覆寫：" + str(path))
        require(path.parent.is_dir(), "目的目錄不存在：" + str(path.parent))
    with source.open("rb") as stream:
        data = stream.read(MAX_DTB_BYTES + 1)
    require(sha256(data) == SOURCE_SHA256, "來源 SHA-256 不符指定的 CM6 eth0 修正 DTB")

    with tempfile.TemporaryDirectory(prefix=".cm6-camera-dtb-", dir=output.parent) as work, \
            tempfile.TemporaryDirectory(prefix=".cm6-camera-manifest-", dir=manifest.parent) as reports:
        directory = Path(work)
        snapshot = directory / "source.dtb"
        snapshot.write_bytes(data)
        validate_source(snapshot)
        candidate = directory / "candidate.dtb"
        candidate.write_bytes(data)
        for node, prop, kind, _before, after in CHANGES:
            put(candidate, node, prop, after, kind)
        canonical_sha = verify_only_changes(snapshot, candidate, directory)
        candidate_data = candidate.read_bytes()
        require(sha256(candidate_data) == CANDIDATE_SHA256,
                "候選 SHA-256 不符已實測的 CM6 雙相機 DTB，拒絕發布")
        record = {
            "schema_version": 1,
            "board": "bpi-cm6",
            "kernel": "6.6.36-legacy-spacemit",
            "status": "離線驗證通過",
            "source": {"name": source.name, "bytes": len(data), "sha256": sha256(data)},
            "candidate": {"name": output.name, "bytes": len(candidate_data), "sha256": sha256(candidate_data)},
            "changed_properties": [node + "/" + prop for node, prop, *_ in CHANGES],
            "changes": [{"node": node, "property": prop, "type": kind,
                         "before": list(before) if isinstance(before, tuple) else before,
                         "after": list(after) if isinstance(after, tuple) else after}
                        for node, prop, kind, before, after in CHANGES],
            "semantic_restore": {"equal": True, "canonical_sha256": canonical_sha},
            "change": "保留 eth0 GPIO45 修正，只套用 CM6 官方 I²C 5、sensor2 與 DPHY2 的八項配置。",
            "semantic_validation": "逐項還原八個屬性後，排序 DTB 與來源完全一致，含保留記憶體與開機 CPU 識別。",
            "hardware_validation": "本工具只驗證離線產物；不執行或判定板端開機、雙路畫質、幀率或停止流程驗收。",
            "known_limitations": "既有雙 IMX415 實測可同時出圖；約 19.4 FPS、pipe1 停止逾時與畫質仍待驗收。",
        }
        report = Path(reports) / "manifest.json"
        report.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        publish(((candidate, output), (report, manifest)))
    return record


def main():
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, metavar="已修正DTB", help="SHA-256 已鎖定的 CM6 eth0 修正 DTB")
    parser.add_argument("--output", required=True, type=Path, metavar="新DTB", help="尚不存在的雙相機 DTB 路徑")
    parser.add_argument("--manifest", required=True, type=Path, metavar="新清單", help="尚不存在的 JSON 清單路徑")
    args = parser.parse_args()
    try:
        record = build(args.source, args.output, args.manifest)
    except ValueError as exc:
        print("離線修正已停止：" + str(exc), file=sys.stderr)
        return 1
    except OSError:
        print("離線修正已停止：檔案讀寫失敗，請檢查路徑、權限與剩餘空間。", file=sys.stderr)
        return 1
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
