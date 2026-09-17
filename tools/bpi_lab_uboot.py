#!/usr/bin/env python3
"""已配對 U-Boot 的一次性引導；CLI 僅離線核對與展開，不是完整平台後端。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import sys
import time
import zlib


SCHEMA = "bpi-lab-uboot-v1"
FORMATS = {"arm32": {"zImage": "bootz", "uImage": "bootm"},
           "arm64": {"Image": "booti", "uImage": "bootm"},
           "riscv64": {"Image": "booti", "uImage": "bootm"}}
LIMITS = ("僅核對配置或觀察核心標記；不證明 root、smoke、救援或實板資格。"
          "不發送媒體寫入命令，但 Linux 啟動後可能寫入根檔案系統，不是整段唯讀。")


class UBootError(ValueError):
    """配置或本次引導不符合已支援契約；不自動重試。"""


def require(condition, message):
    if not condition:
        raise UBootError(message)


def _keys(value, keys, label):
    require(type(value) is dict and set(value) == set(keys.split()), label + "欄位不完整或含未知欄位")


def _match(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _integer(value, minimum, maximum, label):
    require(type(value) is int and minimum <= value <= maximum, label + "超界或不是整數")
    return value


def _address(value, bits, minimum=1):
    if _match(r"0x[0-9a-fA-F]{1,16}", value):
        value = int(value, 16)
    return _integer(value, minimum, (1 << bits) - 1, "位址")


def _span(value, bits):
    _keys(value, "start size", "RAM 區間")
    value["start"] = _address(value["start"], bits, minimum=0)
    _integer(value["size"], 1, (1 << bits) - 1, "RAM 長度")
    require(value["start"] + value["size"] <= (1 << bits), "RAM 區間溢位")
    return value


def _contains(outer, inner):
    return (outer["start"] <= inner["start"]
            and inner["start"] + inner["size"] <= outer["start"] + outer["size"])


def _overlap(a, b):
    return max(a["start"], b["start"]) < min(a["start"] + a["size"], b["start"] + b["size"])


def _disjoint(spans, label):
    for index, span in enumerate(spans):
        require(not any(_overlap(span, other) for other in spans[:index]), label + "重疊")


def _slot(item):
    return {"start": item["address"], "size": item["capacity"]}


def _items(config):
    return [(name, config["files"][name]) for name in ("kernel", "initrd", "dtb")
            if config["files"][name] is not None]


def _source(source):
    require(type(source) is dict, "載入來源必須是物件")
    if source.get("type") == "mmc":
        _keys(source, "type device partition partuuid", "MMC 來源")
        _integer(source["device"], 0, 255, "MMC 裝置")
        _integer(source["partition"], 1, 255, "MMC 分割區")
        require(_match(r"(?:[0-9a-f]{8}-[0-9a-f]{2}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", source["partuuid"]),
                "MMC 來源必須綁定已配對的分割區 PARTUUID")
        return
    require(source.get("type") == "tftp", "只支援明確 MMC 或靜態 TFTP")
    _keys(source, "type ipaddr serverip netmask gatewayip ethact protection", "TFTP 來源")
    require(source["protection"] == "lmb-no-overwrite-v1", "TFTP 缺少已核定的逐區塊 LMB 防護")
    require(_match(r"[A-Za-z0-9_@.,:+-]{1,64}", source["ethact"]), "TFTP 網路裝置無效")
    require(all(_match(r"[0-9.]{7,15}", source[key]) for key in ("ipaddr", "serverip", "netmask", "gatewayip")),
            "靜態 IPv4 必須使用明確的點分十進位字串")
    try:
        addresses = [ipaddress.IPv4Address(source[key]) for key in ("ipaddr", "serverip", "gatewayip")]
        network = ipaddress.IPv4Network((source["ipaddr"], source["netmask"]), strict=False)
    except (ValueError, TypeError) as exc:
        raise UBootError("靜態 IPv4 設定無效") from exc
    require(str(network.netmask) == source["netmask"] and network.prefixlen <= 30, "網路遮罩不支援")
    local, server, gateway = addresses
    require(local != server, "TFTP 主機與板子位址相同")
    for address in (local, server):
        require(not (address.is_loopback or address.is_unspecified or address.is_multicast
                     or address.is_reserved or address == network.broadcast_address
                     or address == network.network_address), "TFTP 位址不是可用單播")
    require(local in network, "板子位址不在網段內")
    require((gateway.is_unspecified and server in network)
            or (gateway in network and gateway not in (local, network.network_address, network.broadcast_address)
                and not (gateway.is_loopback or gateway.is_multicast or gateway.is_reserved)), "TFTP 閘道不符")


def validate_config(config):
    """回傳獨立、正規化配置；不碰檔案、console、網路或硬體。"""
    _keys(config, "schema arch uboot ram source files bootargs kernel_release fdt_extra", "引導配置")
    config = copy.deepcopy(config)
    require(config["schema"] == SCHEMA, "引導 schema 不支援")
    require(isinstance(config["arch"], str) and config["arch"] in FORMATS, "架構不支援")
    uboot = config["uboot"]
    _keys(uboot, "prompt version address_bits line_limit pairing_sha256 qualification_sha256 abi", "U-Boot 配對")
    require(uboot["abi"] == "mainline-v2025.01", "未知 vendor／FIT 引導 ABI，須另行適配")
    require(_match(r"[\x20-\x7e]{1,64}", uboot["prompt"]) and uboot["prompt"].strip(), "必須明示已配對 prompt")
    require(_match(r"U-Boot [\x20-\x7e]{1,200}", uboot["version"]), "必須明示已核定版本完整行")
    for key in ("pairing_sha256", "qualification_sha256"):
        require(_match(r"[0-9a-f]{64}", uboot[key]), "配對及建置核定證據須有 SHA-256")
    bits = uboot["address_bits"]
    require(type(bits) is int and bits in (32, 64), "U-Boot 位址寬度不支援")
    require(bits == (32 if config["arch"] == "arm32" else 64), "架構與 U-Boot 位址寬度不符")
    _integer(uboot["line_limit"], 256, 4096, "已核定命令列上限")
    require(_match(r"[A-Za-z0-9_.+~-]{1,128}", config["kernel_release"]), "必須明示核心版本標記")
    args = config["bootargs"]
    require(type(args) is list and 1 <= len(args) <= 64, "bootargs 必須是非空參數陣列")
    for arg in args:
        require(_match(r"[A-Za-z0-9_./,:=+@%-]{1,256}", arg) and not arg.startswith("-"),
                "bootargs 含未支援字元；不得注入 shell 或變數")
    _integer(config["fdt_extra"], 4096, 1024 * 1024, "DTB 擴充空間")
    ram = config["ram"]
    _keys(ram, "banks reserved kernel_work boot", "RAM 配置")
    for name in ("banks", "reserved"):
        require(type(ram[name]) is list and 1 <= len(ram[name]) <= 64, "RAM 區間必須明示且非空")
        for span in ram[name]:
            _span(span, bits)
        _disjoint(ram[name], "RAM " + name)
    for name in ("kernel_work", "boot"):
        _span(ram[name], bits)
    for span in ram["reserved"] + [ram["kernel_work"], ram["boot"]]:
        require(any(_contains(bank, span) for bank in ram["banks"]), "RAM 區間越界或跨越 bank")
    require(_contains(ram["boot"], ram["kernel_work"]), "核心工作區不在 boot 區間")
    _source(config["source"])
    _keys(config["files"], "kernel initrd dtb", "原配組件")
    for name, item in _items(config):
        _keys(item, "path bytes sha256 address capacity format" + (" entry" if name == "kernel" else ""), "組件")
        require(_match(r"/?[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", item["path"])
                and len(item["path"]) <= 120 and not any(p in (".", "..") for p in item["path"].split("/")),
                "組件路徑無效或含 shell 片段")
        _integer(item["bytes"], 64, 1024**3, "組件長度")
        _integer(item["capacity"], item["bytes"] + 1, 2**32 - 1, "載入區容量")
        require(_match(r"[0-9a-f]{64}", item["sha256"]), "組件 SHA-256 無效")
        item["address"] = _address(item["address"], bits)
        slot = _span(_slot(item), bits)
        require(_contains(ram["boot"], slot), "載入區不在明示 boot 區間")
        if name == "kernel":
            require(isinstance(item["format"], str) and item["format"] in FORMATS[config["arch"]],
                    "架構與核心格式不符；不支援 FIT、vendor 容器或壓縮 Image")
            item["entry"] = _address(item["entry"], bits)
            require(_contains(ram["kernel_work"], slot)
                    and _contains(ram["kernel_work"], {"start": item["entry"], "size": 1}), "核心載入或入口不在核定工作區")
        elif name == "initrd":
            require(item["format"] in ("raw", "legacy"), "initrd 格式必須明示 raw 或 legacy")
        else:
            require(item["format"] == "dtb" and item["address"] % 8 == 0, "DTB 格式或對齊不符")
            require(item["capacity"] >= item["bytes"] + config["fdt_extra"] + 4096, "DTB 缺少擴充及頁面對齊空間")
    require(config["files"]["kernel"] is not None and config["files"]["dtb"] is not None, "缺少核心或 DTB")
    footprints = [ram["kernel_work"]] + [_slot(item) for name, item in _items(config) if name != "kernel"]
    _disjoint(footprints, "組件／核心工作區")
    require(not any(_overlap(a, b) for a in footprints for b in ram["reserved"]), "組件／核心工作區撞到保留區")
    if config["source"]["type"] == "tftp":
        for _, item in _items(config):
            guard = {"start": item["address"] + item["capacity"], "size": 65536}
            require(any(_contains(span, guard) for span in ram["reserved"]), "TFTP 下載區後方缺少 64 KiB 保留防護區")
    for step in _steps(config):
        require(len(_wire(step["command"], "0" * 16)) <= uboot["line_limit"], "產生命令超過已核定命令列上限")
    return config


def _digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _boot_command(config):
    kernel, initrd, dtb = (config["files"][name] for name in ("kernel", "initrd", "dtb"))
    ramdisk = "-" if initrd is None else f"{initrd['address']:x}"
    if initrd and initrd["format"] == "raw":
        ramdisk += f":{initrd['bytes']:x}"
    return f"{FORMATS[config['arch']][kernel['format']]} {kernel['address']:x} {ramdisk} {dtb['address']:x}"


def _steps(config):
    steps = []

    def add(command, check="status", **values):
        steps.append({"command": command, "check": check, **values})

    add("version", "version")
    add("bdinfo", "memory")
    for command in ("setenv", "printenv", "hash", "base", "md", "fdt", _boot_command(config).split()[0]):
        add("help " + command)
    source = config["source"]
    add("help " + ("load" if source["type"] == "mmc" else "tftpboot"))
    # 零長度已知向量確認真正 SHA-256 能力，不因 help 有 hash 就放行。
    add(f"hash sha256 {config['files']['kernel']['address']:x} 0", "sha256-probe")
    add("setenv autostart no")
    add("printenv autostart", "env", variable="autostart", value="no")
    add("base 0", "base")
    if source["type"] == "mmc":
        add("help part")
        add(f"mmc dev {source['device']}")
        add(f"part uuid mmc {source['device']:x}:{source['partition']:x}", "partuuid")
    else:
        for key in ("ipaddr", "serverip", "netmask", "gatewayip", "ethact"):
            add("setenv " + key + " " + source[key])
            add("printenv " + key, "env", variable=key, value=source[key])
        for key, value in (("ethrotate", "no"), ("netretry", "no")):
            add("setenv " + key + " " + value)
            add("printenv " + key, "env", variable=key, value=value)
    loaded = []
    for name, item in _items(config):
        address = item["address"]
        add("setenv filesize")
        if source["type"] == "mmc":
            command = (f"load mmc {source['device']:x}:{source['partition']:x} {address:x} "
                       f"{item['path']} {item['bytes'] + 1:x} 0")
        else:
            add("bdinfo", "memory", loaded=list(loaded))
            command = f"tftpboot {address:x} {source['serverip']}:{item['path']}"
        add(command, "length", component=name)
        add("printenv filesize", "filesize", component=name)
        add(f"hash sha256 {address:x} {item['bytes']:x}", "load-sha256", component=name)
        loaded.append(name)
        add("bdinfo", "memory", loaded=list(loaded))
    # 全部載入後再核對，避免後續載入破壞先前已核對的內容。
    for name, item in _items(config):
        add(f"hash sha256 {item['address']:x} {item['bytes']:x}", "sha256", component=name)
        add(f"md.b {item['address']:x} 40", "header", component=name)
    dtb = config["files"]["dtb"]
    add(f"fdt addr {dtb['address']:x}")
    add("fdt list /", "dtb-root")
    add("fdt rsvmem print", "dtb-reserved")
    add(f"fdt resize {config['fdt_extra']:x}")
    add(f"md.b {dtb['address']:x} 40", "resized-dtb", component="dtb")
    mask = (1 << config["uboot"]["address_bits"]) - 1
    boot = config["ram"]["boot"]
    settings = {"fdt_high": f"{mask:x}", "initrd_high": f"{mask:x}",
                "bootm_low": f"{boot['start']:x}", "bootm_size": f"{boot['size']:x}",
                "bootm_mapsize": f"{boot['size']:x}", "bootargs": " ".join(config["bootargs"])}
    for key, value in settings.items():
        add(f"setenv {key} {value}")
        add(f"printenv {key}", "env", variable=key, value=value)
    add("bdinfo", "memory", loaded=list(loaded))
    add(_boot_command(config), "kernel-marker")
    return steps


def render(config):
    """輸出附有必要回應核對條件的計畫；不是可跳過核對的 shell 腳本。"""
    config = validate_config(config)
    return {"schema": "bpi-lab-uboot-render-v1", "config_sha256": _digest(config),
            "executed": False, "limits": LIMITS, "steps": _steps(config)}


def _wire(command, nonce):
    # 標記拆開，完整標記不會出現在 UART 回顯；只允許內部生成的固定控制結構。
    return (f"echo BPI_'{nonce}'_BEGIN; if {command}; then echo BPI_'{nonce}'_OK; "
            f"else echo BPI_'{nonce}'_FAIL; fi\n")


def _lines(output):
    require(isinstance(output, bytes), "console 回應必須是 bytes")
    try:
        return output.decode("ascii").replace("\r\n", "\n").splitlines()
    except UnicodeError as exc:
        raise UBootError("U-Boot 核對回應含非 ASCII 資料") from exc


def _one(pattern, output, label):
    found = [match for line in _lines(output) if (match := re.fullmatch(pattern, line))]
    require(len(found) == 1, label + "缺少、重複或格式未知")
    return found[0]


def _check_hash(output, address, size, digest):
    found = _one(r"sha256 for ([0-9a-fA-F]+) \.\.\. ([0-9a-fA-F]+) ==> ([0-9a-fA-F]{64})", output, "SHA-256 回應")
    require(int(found[1], 16) == address and int(found[2], 16) == address + size - 1
            and found[3].lower() == digest, "RAM SHA-256 或雜湊範圍不符；不得改用 CRC")


def _memory_gd(output, config):
    """只核對 DRAM／gd；專用適配器須另行完成完整 LMB 核對。"""
    lines = _lines(output)
    banks = []
    bits = config["uboot"]["address_bits"]
    for index, line in enumerate(lines):
        if re.match(r"DRAM bank\s*=", line):
            require(re.fullmatch(r"DRAM bank\s*=\s*(?:0x)?[0-9a-fA-F]+", line) is not None
                    and index + 2 < len(lines), "bdinfo DRAM bank 格式未知")
            start = re.fullmatch(r"-> start\s*=\s*(0x[0-9a-fA-F]+)", lines[index + 1])
            size = re.fullmatch(r"-> size\s*=\s*(0x[0-9a-fA-F]+)", lines[index + 2])
            require(start is not None and size is not None, "bdinfo 缺少明確 RAM 起點／大小")
            banks.append(_span({"start": int(start[1], 16), "size": int(size[1], 16)}, bits))
    require(banks == config["ram"]["banks"], "bdinfo 與明示 RAM banks 不符；不推測地址")
    require(len([line for line in lines if re.match(r"\s*-> (start|size)\s*=", line)]) == 2 * len(banks),
            "bdinfo 有無法配對的 RAM 欄位")
    declared = config["ram"]["reserved"]
    for field in ("relocaddr", "sp start"):
        value = int(_one(re.escape(field) + r"\s*=\s*(0x[0-9a-fA-F]+)", output, "bdinfo " + field)[1], 16)
        require(any(_contains(span, {"start": value, "size": 1}) for span in declared), "U-Boot／堆疊地址不在核定保留區")
    for field in ("irq_sp", "TLB addr", "fdt_blob", "new_fdt"):
        if any(re.match(re.escape(field) + r"\s*=", line) for line in lines):
            value = int(_one(re.escape(field) + r"\s*=\s*(0x[0-9a-fA-F]+)", output, "bdinfo " + field)[1], 16)
            if value:
                size = 1
                if field in ("fdt_blob", "new_fdt"):
                    size = int(_one(r"fdt_size\s*=\s*(0x[0-9a-fA-F]+)", output, "U-Boot DTB 大小")[1], 16)
                    require(size > 0, "U-Boot DTB 大小不得為零")
                require(any(_contains(span, {"start": value, "size": size}) for span in declared), "U-Boot 動態工作區未受保留區保護")


def _lmb(output, bits):
    """解析主線完整保留表；零值遵循 printf 的 %#x／%#llx 格式。"""
    number = r"(?:0x[0-9a-fA-F]+|0)"
    lines = _lines(output)
    counts = [line for line in lines if re.match(r"\s*reserved\.", line)]
    require(len(counts) == 1, "LMB 保留區數量缺少或重複")
    count = re.fullmatch(r"\s*reserved\.(?:cnt|count)\s*=\s*(" + number + ")", counts[0])
    require(count is not None and int(count[1], 16) <= 256, "LMB 保留區數量格式未知或超限")
    result = []
    flag_bits = {"none": 0, "no-map": 2, "no-overwrite": 4, "no-notify": 8}
    for line in lines:
        if not re.match(r"\s*reserved\b", line) or line == counts[0]:
            continue
        match = re.fullmatch(r"\s*reserved\[(\d+)\]\s+\[(" + number + ")-(" + number + r")\],\s*("
                             + number + r") bytes,? flags: ([a-z, -]+)", line)
        require(match is not None and int(match[1]) == len(result), "LMB 保留區索引或格式未知")
        span = _span({"start": int(match[2], 16), "size": int(match[4], 16)}, bits)
        require(span["start"] + span["size"] - 1 == int(match[3], 16), "LMB 保留區長度矛盾")
        flags = match[5].split(", ")
        require(len(set(flags)) == len(flags) and all(flag in flag_bits for flag in flags)
                and ("none" not in flags or flags == ["none"]), "LMB 保留區旗標未知或矛盾")
        require(not result or result[-1]["start"] + result[-1]["size"] <= span["start"],
                "LMB 保留區未排序或重疊")
        result.append({**span, "flags": sum(flag_bits[flag] for flag in flags)})
    require(int(count[1], 16) == len(result), "LMB 保留區輸出不完整")
    return result


def _merge_lmb(spans):
    """只合併同旗標且恰好相鄰的區間，不吞入間隙或重疊。"""
    result = []
    for span in sorted(spans, key=lambda item: item["start"]):
        end = result[-1]["start"] + result[-1]["size"] if result else None
        require(end is None or end <= span["start"], "LMB 預期區間重疊")
        if end == span["start"] and result[-1]["flags"] == span["flags"]:
            result[-1]["size"] += span["size"]
        else:
            result.append(dict(span))
    return result


def _memory(output, config, loaded=(), *, initial_lmb=None):
    """核對完整 LMB；loaded 僅能由本次長度、filesize、SHA 核對後提供。"""
    _memory_gd(output, config)
    actual = _merge_lmb(_lmb(output, config["uboot"]["address_bits"]))
    require(isinstance(loaded, (list, tuple)) and all(isinstance(name, str) for name in loaded)
            and len(set(loaded)) == len(loaded)
            and all(config["files"].get(name) is not None for name in loaded), "LMB 已核對載荷清單無效")
    if initial_lmb is None:
        require(not loaded, "LMB 載入核對缺少本次初始保留表")
        footprints = [config["ram"]["kernel_work"]] + [_slot(item) for name, item in _items(config) if name != "kernel"]
        require(not any(_overlap(span, region) for span in actual for region in footprints),
                "bdinfo 動態保留區與工作區重疊")
    else:
        expected = initial_lmb + [{"start": config["files"][name]["address"],
                                   "size": config["files"][name]["bytes"], "flags": 0} for name in loaded]
        require(actual == _merge_lmb(expected), "LMB 與初始保留表及已核對實收範圍不同")
    if config["source"]["type"] == "tftp":
        guards = [span for span in actual if span["flags"] & 4]
        for _, item in _items(config):
            guard = {"start": item["address"] + item["capacity"], "size": 65536}
            require(any(_contains(span, guard) for span in guards), "TFTP 防護區未以 LMB no-overwrite 實際保留")
    return actual


def _memory_bytes(output, address):
    result = bytearray()
    for line in _lines(output):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{8,16}): ((?:[0-9a-fA-F]{2} ){15}[0-9a-fA-F]{2})(?:  .*)?", line)
        require(match is not None and int(match[1], 16) == address + len(result), "md.b 位址、長度或回應格式不符")
        result.extend(bytes.fromhex(match[2]))
    require(len(result) == 64, "未完整取得 64 位元組映像標頭")
    return bytes(result)


def _header(header, item, name, config):
    require(len(header) == 64, "映像標頭長度不符")
    fmt = item["format"]
    if fmt in ("uImage", "legacy"):
        values = struct.unpack(">7I4B32s", header)
        magic, checksum, _, size, load, entry, _, os_id, arch, kind, compression, _ = values
        require(magic == 0x27051956 and size + 64 == item["bytes"] and os_id == 5
                and arch == {"arm32": 2, "arm64": 22, "riscv64": 26}[config["arch"]]
                and kind == (2 if name == "kernel" else 3), "legacy 標頭的長度、架構或類型不符")
        require(zlib.crc32(header[:4] + bytes(4) + header[8:]) & 0xffffffff == checksum, "legacy 標頭校驗不符")
        if name == "kernel":
            require(compression == 0, "本版 bootm 僅支援未壓縮 legacy 核心；壓縮 uImage 須另行適配")
            require(entry == item["entry"] and load <= entry < load + size
                    and _contains(config["ram"]["kernel_work"], {"start": load, "size": size}), "legacy 核心搬移範圍或入口不符")
        else:
            require(compression in (0, 1, 2, 3, 4, 5, 6), "legacy initrd 壓縮識別不支援")
    elif fmt == "zImage":
        magic, start, end = struct.unpack_from("<3I", header, 36)
        require(magic == 0x016f2818 and end > start and end - start == item["bytes"], "不是完整 raw zImage")
        require(item["entry"] % 4 == 0, "zImage 核定解壓入口未對齊")
    elif fmt == "Image":
        offset, size, flags = struct.unpack_from("<3Q", header, 8)
        magic = header[56:60]
        require(item["bytes"] <= size <= item["capacity"] and not flags & 1,
                "Image 大小或位元組序不支援；不猜舊版 image_size，也不越界讀取搬移來源")
        base = config["ram"]["banks"][0]["start"]
        if config["arch"] == "arm64":
            require(magic == b"ARM\x64", "不是 ARM64 raw Image")
            destination = item["address"] - offset if flags & 8 else base
            require(destination >= 0, "Image text_offset 下溢")
            destination = (destination + 0x1fffff) // 0x200000 * 0x200000 + offset
        else:
            require(magic == b"RSC\x05" and header[48:56] == b"RISCV\0\0\0", "不是 RISC-V raw Image")
            destination = base + offset
        require(destination == item["entry"] and _contains(config["ram"]["kernel_work"], {"start": destination, "size": size}),
                "Image 的實際搬移地址或展開長度超出核定工作區")
    elif fmt == "raw":
        require(header[:4] not in (b"\x27\x05\x19\x56", b"\xd0\x0d\xfe\xed"), "raw initrd 不得混入 legacy／FIT 標頭")
    elif fmt == "dtb":
        magic, total, structure, strings, reservations, version, compatible, _, strings_size, structure_size = struct.unpack_from(">10I", header)
        require(magic == 0xd00dfeed and total == item["bytes"] and version == 17 and compatible <= 17
                and 40 <= reservations < total and reservations % 8 == 0
                and 40 <= structure <= total - structure_size and structure % 4 == 0
                and 40 <= strings <= total - strings_size, "DTB 標頭或長度不符")


def _check(step, output, config):
    check = step["check"]
    item = config["files"].get(step.get("component"))
    if check == "version":
        require([line for line in _lines(output) if line.startswith("U-Boot ")] == [config["uboot"]["version"]], "U-Boot 版本與配對核定不符")
    elif check == "memory":
        _memory(output, config, step.get("loaded", ()))
    elif check == "partuuid":
        require([line for line in _lines(output) if line] == [config["source"]["partuuid"]],
                "MMC 分割區身分不符；禁止改猜裝置或繼續載入")
    elif check == "env":
        require([line for line in _lines(output) if line] == [step["variable"] + "=" + step["value"]], "RAM 環境值未正確設定")
    elif check == "base":
        require(int(_one(r"Base Address: 0x([0-9a-fA-F]+)", output, "記憶體基址")[1], 16) == 0, "md.b 基址非零")
    elif check == "sha256-probe":
        _check_hash(output, config["files"]["kernel"]["address"], 0, hashlib.sha256(b"").hexdigest())
    elif check in ("sha256", "load-sha256"):
        _check_hash(output, item["address"], item["bytes"], item["sha256"])
    elif check == "length":
        pattern = (r"([0-9]+) bytes read(?: in .*)?" if config["source"]["type"] == "mmc"
                   else r"Bytes transferred = ([0-9]+) \(([0-9a-fA-F]+) hex\)")
        match = _one(pattern, output, "載入長度")
        require(int(match[1]) == item["bytes"], "實收長度不符")
        if config["source"]["type"] == "tftp":
            require(int(match[2], 16) == item["bytes"], "TFTP 十六進位長度不符")
    elif check == "filesize":
        require(int(_one(r"filesize=([0-9a-fA-F]+)", output, "filesize")[1], 16) == item["bytes"], "filesize 與實收長度不符")
    elif check == "header":
        _header(_memory_bytes(output, item["address"]), item, step["component"], config)
    elif check == "resized-dtb":
        header = _memory_bytes(output, item["address"])
        magic, size = struct.unpack_from(">II", header)
        require(magic == 0xd00dfeed and 40 <= size <= item["capacity"], "擴充 DTB 超出核定容量")
    elif check == "dtb-root":
        lines = _lines(output)
        require(any(line.strip() == "/ {" for line in lines) and any(line.strip() == "};" for line in lines)
                and not any(re.match(r"\s*(images|configurations)\s*\{", line) for line in lines), "DTB 根節點未知或是 FIT 容器")
    elif check == "dtb-reserved":
        lines = [line for line in _lines(output) if line.strip()]
        require(len(lines) >= 2 and re.fullmatch(r"index\s+start\s+size", lines[0])
                and re.fullmatch(r"-{8,}", lines[1]), "DTB 保留表回應格式未知")
        for index, line in enumerate(lines[2:]):
            match = re.fullmatch(r"\s*([0-9a-f]+)\s+([0-9a-f]{16})\s+([0-9a-f]{16})", line)
            require(match is not None and int(match[1], 16) == index, "DTB 保留表不完整")
            span = _span({"start": int(match[2], 16), "size": int(match[3], 16)}, config["uboot"]["address_bits"])
            require(any(_contains(reserved, span) for reserved in config["ram"]["reserved"]), "DTB 保留表含未核定 RAM 範圍")


class _Runner:
    def __init__(self, console, config, records, timeout, monotonic):
        self.console, self.config, self.records = console, config, records
        self.clock, self.deadline = monotonic, monotonic() + timeout
        self.prompt = re.escape(config["uboot"]["prompt"].encode("ascii"))
        self.initial_lmb = None
        self.received, self.sized, self.hashed = set(), set(), set()

    def remaining(self):
        remaining = self.deadline - self.clock()
        require(remaining > 0, "引導總期限已到；不重試、不重啟")
        return remaining

    def expect(self, pattern):
        result = self.console.expect_regex(pattern, timeout=self.remaining())
        self.remaining()
        return result

    def send(self, wire):
        data = wire.encode("ascii")
        count = self.console.send(data, timeout=self.remaining())
        self.remaining()
        require(type(count) is int and count == len(data), "console 未完整傳送；不重送")

    def at_prompt(self):
        self.expect(rb"(?:^|\r?\n)" + self.prompt + rb"$")

    def execute(self, step):
        record = {**step, "status": "started"}
        self.records.append(record)
        try:
            if step["check"] == "kernel-marker":
                self.send(step["command"] + "\n")
                pattern = (rb"(?:^|\r?\n)(?:\[\s*[0-9.]+\]\s*)?Linux version ([^\s]+)[^\r\n]*\r?\n"
                           rb"|(?:^|\r?\n)" + self.prompt + rb"$")
                found = self.expect(pattern)
                require(found.groups and found.groups[0] is not None, "引導返回 U-Boot，未觀察到核心標記")
                require(found.groups[0] == self.config["kernel_release"].encode("ascii"), "核心版本標記與配置不符")
                record.update(status="kernel-marker-observed", marker=found.matched.decode("ascii"))
            else:
                nonce = secrets.token_hex(8)
                prefix = b"BPI_" + nonce.encode()
                self.send(_wire(step["command"], nonce))
                self.expect(rb"(?:^|\r?\n)" + prefix + rb"_BEGIN\r?\n")
                found = self.expect(rb"(?:^|\r?\n)" + prefix + rb"_(OK|FAIL)\r?\n")
                record["output"] = found.before.decode("ascii", errors="replace")
                require(found.groups == (b"OK",), "U-Boot 命令失敗，停止交接")
                self.at_prompt()
                check, name = step["check"], step.get("component")
                if check == "memory":
                    require(self.received == self.hashed, "LMB 核對前仍有未通過 SHA-256 的載荷")
                    actual = _memory(found.before, self.config, sorted(self.hashed), initial_lmb=self.initial_lmb)
                    if self.initial_lmb is None:
                        self.initial_lmb = actual
                else:
                    _check(step, found.before, self.config)
                    if check == "length":
                        require(name not in self.received, "同次執行不得重載已核對組件")
                        self.received.add(name)
                    elif check == "filesize":
                        require(name in self.received, "filesize 缺少本次實收長度核對")
                        self.sized.add(name)
                    elif check in ("sha256", "load-sha256"):
                        require(name in self.sized, "SHA-256 缺少本次長度及 filesize 核對")
                        self.hashed.add(name)
                record["status"] = "verified"
        except (ValueError, OSError, TimeoutError) as exc:
            record["status"] = "failed"
            record["reason"] = str(exc) if isinstance(exc, UBootError) else "console 操作失敗或逾時；保留原始 RX"
            if isinstance(exc, UBootError):
                raise
            raise UBootError(record["reason"]) from exc


def boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic):
    """借用已配對 console，真正逐行執行；不開關連線、不登入、不操作電源。

    console 須提供 ConsoleSession 相容的 send 與 expect_regex，保存原始 RX，
    並遵守 timeout。替身可供測試，但回傳值沒有實板通過或完整後端通過含意。
    """
    config = validate_config(config)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800, "總期限須為有限正數且最多 1800 秒")
    require(records is None or type(records) is list, "records 必須為陣列")
    records = [] if records is None else records
    runner = _Runner(console, config, records, timeout, monotonic)
    runner.at_prompt()
    for step in _steps(config):
        runner.execute(step)
    return {"schema": "bpi-lab-uboot-result-v1", "status": "kernel-marker-observed",
            "config_sha256": _digest(config), "kernel_release": config["kernel_release"],
            "root_verified": False, "smoke_verified": False, "limits": LIMITS}


def _read_regular(path, maximum):
    path = Path(path).absolute()
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        require(".." not in path.parts, "本機路徑不得含上層跳轉")
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptor)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum, "輸入須為有界一般檔案，拒絕裝置、空檔與過大檔案")
            digest, header, count = hashlib.sha256(), b"", 0
            while chunk := source.read(min(1024 * 1024, maximum - count + 1)):
                count += len(chunk)
                require(count <= maximum, "讀取中檔案超過上限")
                digest.update(chunk)
                if len(header) < 65536:
                    header += chunk[:65536 - len(header)]
            after = os.fstat(source.fileno())
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and count == before.st_size, "核對期間檔案變更")
            return {"bytes": count, "sha256": digest.hexdigest()}, header
    finally:
        os.close(descriptor)


def load_config(path):
    """唯讀小型 JSON；拒絕重複鍵、特殊檔案及符號連結。"""
    _, data = _read_regular(path, 65536)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON 欄位重複")
            result[key] = value
        return result

    try:
        config = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise UBootError("配置不是有效且無重複欄位的 UTF-8 JSON") from exc
    return validate_config(config)


def validate_artifacts(config, artifact_root):
    """串流核對來源大小、SHA-256 及標頭；不是板上 RAM 核對替代品。"""
    config = validate_config(config)
    for name, item in _items(config):
        record, header = _read_regular(Path(artifact_root) / item["path"].lstrip("/"), item["bytes"])
        require(record == {key: item[key] for key in ("bytes", "sha256")}, "本機組件長度或 SHA-256 不符：" + name)
        _header(header[:64], item, name, config)
    return {"artifacts_verified": True, "hardware_verified": False}


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("--help", action="help", help="顯示說明並結束")

    def error(self, message):
        self.exit(2, "參數無效；請使用 --help 查看離線介面。\n")

    def format_help(self):
        return super().format_help().replace("usage:", "用法:")


def main(argv=None):
    parser = _Parser(description="一次性 U-Boot 離線配置工具；不開啟 UART，不是完整平台後端。")
    parser.add_argument("action", choices=("validate", "render"), help="核對配置或展開逐行計畫")
    parser.add_argument("--config", required=True, help="已配對引導配置 JSON")
    parser.add_argument("--artifact-root", help="可選：本機原配組件根目錄，串流核對大小與 SHA-256")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        checked = validate_artifacts(config, args.artifact_root) if args.artifact_root else {"artifacts_verified": False}
        result = render(config) if args.action == "render" else {
            "schema": "bpi-lab-uboot-validation-v1", "config_valid": True,
            "config_sha256": _digest(config), "executed": False, "limits": LIMITS}
        print(json.dumps({**result, **checked, "hardware_verified": False}, ensure_ascii=False, indent=2))
        return 0
    except (UBootError, OSError) as exc:
        reason = str(exc) if isinstance(exc, UBootError) else "無法唯讀開啟本機配置或組件"
        print("離線核對失敗：" + reason, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
