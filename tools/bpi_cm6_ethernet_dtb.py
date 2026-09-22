#!/usr/bin/env python3
"""離線修正指定 CM6 DTB 的 GPIO45 reset pinctrl，保留其餘裝置樹語義。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SOURCE_SHA256 = "6d8db2aa3dc0a106190052316a0839cf9dda6a64b41ff6fe8fcc317f96a46f67"
NODE = "/soc/pinctrl@d401e000/gmac0_grp"
PROPERTY = "pinctrl-single,pins"
ADDED_CELLS = (0xB8, 0, 0xB040)
MAX_DTB_BYTES = 2 * 1024 * 1024


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
    """僅對暫存檔呼叫裝置樹工具，不接觸板端或區塊裝置。"""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, env={**os.environ, "LC_ALL": "C"}, check=False)
    except FileNotFoundError as exc:
        raise ValueError("找不到必要工具：" + argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("裝置樹工具執行逾時：" + argv[0]) from exc
    require(result.returncode == 0, "裝置樹工具執行失敗：" + argv[0])
    return result.stdout


def cells(path):
    data = run(["fdtget", "-t", "x", str(path), NODE, PROPERTY])
    try:
        return tuple(int(value, 16) for value in data.split())
    except ValueError as exc:
        raise ValueError("GMAC0 腳位屬性不是合法的 32 位元資料") from exc


def set_cells(path, values):
    run(["fdtput", "-t", "x", str(path), NODE, PROPERTY,
         *(format(value, "x") for value in values)])


def canonical(path):
    # 排序後重新輸出 DTB，連同保留記憶體與開機 CPU 識別一併比較。
    return run(["dtc", "-I", "dtb", "-O", "dtb", "-s", "-o", "-", str(path)])


def validate_source(path):
    compatible = run(["fdtget", "-t", "s", str(path), "/", "compatible"]).split()
    require(compatible == [b"bananapi,bpi-cm6", b"spacemit,k1-x"], "DTB 板型不是指定的 BPI-CM6")
    original = cells(path)
    require(len(original) == 45, "GMAC0 原始腳位資料長度不符")
    require(ADDED_CELLS[0] not in original[::3], "GMAC0 已含 GPIO45，拒絕重複修正")
    return original


def verify_only_change(source, candidate, original, directory):
    require(cells(candidate) == original + ADDED_CELLS, "候選未精確附加 GPIO45 reset 設定")
    restored = directory / "semantic-restored.dtb"
    restored.write_bytes(candidate.read_bytes())
    set_cells(restored, original)
    require(canonical(source) == canonical(restored), "候選改變了指定屬性以外的裝置樹語義")


def publish(staged):
    """以不覆寫的硬連結發布；第二個檔案失敗時撤回本次已發布檔案。"""
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
    require(sha256(data) == SOURCE_SHA256, "來源 SHA-256 不符指定 CM6 原始 DTB")

    with tempfile.TemporaryDirectory(prefix=".cm6-dtb-", dir=output.parent) as work, \
            tempfile.TemporaryDirectory(prefix=".cm6-dtb-manifest-", dir=manifest.parent) as reports:
        directory = Path(work)
        snapshot = directory / "source.dtb"
        snapshot.write_bytes(data)
        original = validate_source(snapshot)
        candidate = directory / "candidate.dtb"
        candidate.write_bytes(data)
        set_cells(candidate, original + ADDED_CELLS)
        verify_only_change(snapshot, candidate, original, directory)
        candidate_data = candidate.read_bytes()
        record = {
            "schema_version": 1,
            "board": "bpi-cm6",
            "kernel": "6.6.36-legacy-spacemit",
            "status": "離線驗證通過",
            "source": {"name": source.name, "bytes": len(data), "sha256": sha256(data)},
            "candidate": {"name": output.name, "bytes": len(candidate_data),
                          "sha256": sha256(candidate_data)},
            "changed_properties": [NODE + "/" + PROPERTY],
            "appended_cells": [hex(value) for value in ADDED_CELLS],
            "change": "僅在 GMAC0 群組加入 GPIO45 的 GPIO mux、下拉與官方驅動強度設定。",
            "semantic_validation": "還原唯一修改的屬性後，排序輸出的 DTB 完全一致，含保留記憶體與開機 CPU 識別。",
            "hardware_validation": "本工具只驗證離線產物；不執行或判定板端安裝、開機及網路驗收。",
        }
        report = Path(reports) / "manifest.json"
        report.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        publish(((candidate, output), (report, manifest)))
    return record


def main():
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, metavar="原始DTB", help="SHA-256 已鎖定的 CM6 原始 DTB")
    parser.add_argument("--output", required=True, type=Path, metavar="新DTB", help="尚不存在的候選 DTB 路徑")
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
