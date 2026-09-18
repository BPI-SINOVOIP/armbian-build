#!/usr/bin/env python3
"""僅擴大已核對官方配置的 K1 GPT 第六根分區，保留前五分區。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid


PREFIX = (("fsbl", 256, 512), ("env", 768, 128), ("opensbi", 2048, 2048),
          ("uboot", 4096, 4096), ("bootfs", 8192, 524288))
ROOT_START = 532480


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("檔案系統 UUID 不合法") from exc


def validate_layout(marker, mounts, table):
    """純檢查：先核對實際掛載、板型與完整六分區契約。"""
    require(marker.get("board") in ("bpi-cm6", "bpi-f3"), "板型不是支援的 K1 板")
    require(marker.get("storage") in ("sd", "emmc"), "媒體不是指定 SD／eMMC")
    require(marker.get("layout_version") == "bianbu-v2.3", "官方分區版本不符")
    root_uuid, boot_uuid = valid_uuid(marker.get("root_uuid")), valid_uuid(marker.get("boot_uuid"))
    require(root_uuid != boot_uuid, "根分區與開機分區 UUID 不得相同")
    require(set(mounts) == {"/", "/boot"}, "根分區及獨立開機分區必須均已掛載")
    for target, expected in (("/", root_uuid), ("/boot", boot_uuid)):
        mount = mounts[target]
        require(mount.get("target") == target and mount.get("fstype") == "ext4", "掛載點或檔案系統種類不符")
        require(valid_uuid(mount.get("uuid")) == expected, "已掛載檔案系統 UUID 與官方配置標記不符")
    root_device = mounts["/"]["source"]
    require(isinstance(root_device, str) and re.fullmatch(r"/dev/mmcblk[0-9]+p6", root_device) is not None,
            "根分區必須是 SD／eMMC 的第六分區")
    disk = root_device[:-2]
    require(mounts["/boot"]["source"] == disk + "p5", "開機分區必須是同一媒體的第五分區")
    require(table.get("label") == "gpt" and table.get("unit") == "sectors" and table.get("sectorsize") == 512,
            "只接受 512 位元組扇區的 GPT 配置")
    require(table.get("device") == disk, "分割表並非目前根分區所在媒體")
    parts = table.get("partitions", [])
    require(len(parts) == 6, "官方配置必須恰有六個分區")
    for number, (name, start, size) in enumerate(PREFIX, 1):
        part = parts[number - 1]
        require(part.get("node") == f"{disk}p{number}" and part.get("name") == name
                and part.get("start") == start and part.get("size") == size,
                f"第 {number} 分區的名稱或界線不符官方配置")
    root = parts[5]
    require(root.get("node") == root_device and root.get("name") == "rootfs"
            and root.get("start") == ROOT_START, "根分區名稱、編號或起點不符")
    require(type(root.get("size")) is int and root["size"] > 0, "根分區長度不合法")
    require(isinstance(table.get("lastlba"), int) and ROOT_START + root["size"] - 1 <= table["lastlba"],
            "根分區超過 GPT 可用界線")
    return {"disk": disk, "root_device": root_device, "root_bytes": root["size"] * 512}


def validate_growth(before, after):
    """擴容後保留磁碟識別、前五分區及第六分區所有非大小欄位。"""
    for field in ("label", "id", "device", "unit", "sectorsize", "firstlba"):
        require(before.get(field) == after.get(field), f"擴容改變磁碟識別或格式：{field}")
    old, new = before.get("partitions", []), after.get("partitions", [])
    require(len(old) == len(new) == 6 and old[:5] == new[:5], "擴容改變前五分區")
    require({k: v for k, v in old[5].items() if k != "size"}
            == {k: v for k, v in new[5].items() if k != "size"}, "擴容改變根分區起點、名稱或識別")
    require(type(new[5].get("size")) is int and new[5]["size"] >= old[5]["size"], "禁止縮小根分區")


def run(argv, timeout=60):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=timeout, env={**os.environ, "LC_ALL": "C"}, shell=False)
    return result


def checked(argv, timeout=60):
    result = run(argv, timeout)
    require(result.returncode == 0, f"命令失敗：{argv[0]}，退出碼 {result.returncode}")
    return result.stdout


def mounted():
    mounts = {}
    for target in ("/", "/boot"):
        document = json.loads(checked(["findmnt", "--json", "--target", target,
                                       "--output", "TARGET,SOURCE,FSTYPE,UUID"]))
        items = document.get("filesystems", [])
        require(len(items) == 1 and items[0].get("target") == target, "找不到獨立掛載點：" + target)
        item = items[0]
        item["source"] = os.path.realpath(item["source"])
        mounts[target] = item
    return mounts


def grow(marker, mounts, before, dry_run=False):
    plan = validate_layout(marker, mounts, before)
    if dry_run:
        return {"status": "checked_without_changes", **plan}
    require(stat.S_ISBLK(Path(plan["root_device"]).stat().st_mode)
            and stat.S_ISBLK(Path(plan["disk"]).stat().st_mode), "實際擴容只接受已核對的區塊裝置")
    # 保存 K1 開機資訊；growpart 只能調整 GPT 與第六分區界線。
    with Path(plan["disk"]).open("rb") as stream:
        bootinfo = stream.read(80)
    result = run(["growpart", plan["disk"], "6"], timeout=120)
    unchanged = result.returncode == 1 and "NOCHANGE:" in result.stdout + result.stderr
    require(result.returncode == 0 or unchanged, "growpart 失敗，已停止後續檔案系統擴容")
    after = json.loads(checked(["sfdisk", "--json", plan["disk"]]))["partitiontable"]
    validate_growth(before, after)
    new_plan = validate_layout(marker, mounts, after)
    with Path(plan["disk"]).open("rb") as stream:
        require(stream.read(80) == bootinfo, "K1 開機資訊遭改變，已停止後續操作")
    actual_size = int(checked(["blockdev", "--getsize64", plan["root_device"]]).strip())
    if actual_size != new_plan["root_bytes"]:
        checked(["partx", "--update", "--nr", "6", plan["disk"]])
        actual_size = int(checked(["blockdev", "--getsize64", plan["root_device"]]).strip())
    require(actual_size == new_plan["root_bytes"], "核心尚未接受新分區界線；請重新開機後再擴大檔案系統")
    checked(["resize2fs", plan["root_device"]], timeout=300)
    return {"status": "filesystem_expanded", "partition_changed": not unchanged,
            "old_root_bytes": plan["root_bytes"], **new_plan}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", type=Path, default=Path("/etc/bpi-k1-vendor.json"), help="官方格式身分標記。")
    parser.add_argument("--dry-run", action="store_true", help="僅核對已掛載系統與 GPT，不進行寫入。")
    args = parser.parse_args()
    require(os.geteuid() == 0, "必須由管理員執行受控擴容")
    marker = json.loads(args.marker.read_text())
    mounts = mounted()
    source = mounts["/"]["source"]
    require(re.fullmatch(r"/dev/mmcblk[0-9]+p6", source) is not None, "根分區不是支援的 SD／eMMC 第六分區")
    before = json.loads(checked(["sfdisk", "--json", source[:-2]]))["partitiontable"]
    print(json.dumps(grow(marker, mounts, before, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"受控擴容已停止：{exc}", file=sys.stderr)
        raise SystemExit(1)
