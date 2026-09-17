#!/usr/bin/env python3
"""原配 extlinux 與組件證據；只解析，不執行映像程式或產生硬體授權。"""

from __future__ import annotations

import copy
import ctypes
import ctypes.util
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import struct
import zlib

if __package__:
    from . import bpi_lab_allwinner as binary
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_allwinner as binary
    import bpi_lab_uboot as uboot


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "bpi-lab-original-components-v1"
TEMPLATE_SCHEMA = "bpi-lab-original-entry-template-v1"
MAX_FILE = binary.MAX_FILE
MAX_TEXT = binary.MAX_TEXT
Error = binary.AllwinnerError
require = binary.require
digest = binary.digest
UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
NAME = r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}"


def path(value):
    require(isinstance(value, str) and re.fullmatch(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", value)
            and not any(p in (".", "..") for p in value.split("/")),
            "image_path", "映像路徑含未支援字元或上層跳轉")
    return value


def resolve_path(value, *, config_path="/boot/extlinux/extlinux.conf", boot_mount="/"):
    """絕對路徑相對於開機檔案系統；相對路徑相對於組態目錄。"""
    require(boot_mount in ("/", "/boot"), "boot_mount", "開機掛載點只能是 / 或 /boot")
    require(isinstance(value, str) and value and not any(p in (".", "..") for p in value.split("/")),
            "image_path", "extlinux 路徑不得含上層跳轉")
    if value.startswith("/"):
        return path(("" if boot_mount == "/" else boot_mount) + value)
    return path(str(PurePosixPath(config_path).parent / value))


def parse(blob):
    """保留全部 label；未知指令、全域 APPEND 與衝突選擇明確拒絕。"""
    require(type(blob) is bytes and len(blob) <= MAX_TEXT, "extlinux_size", "extlinux 大小或型別無效")
    text = blob.decode("utf-8")
    require(not any(ord(c) < 32 and c not in "\n\t\r" for c in text), "extlinux_syntax", "extlinux 含控制字元")
    result = {"global": {}, "labels": []}
    current = None
    aliases = {"LINUX": "KERNEL", "DEVICETREE": "FDT", "DEVICETREE-OVERLAY": "FDTOVERLAYS"}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        key, value = parts[0].upper(), parts[1] if len(parts) == 2 else ""
        key = aliases.get(key, key)
        if key == "LABEL":
            require(re.fullmatch(NAME, value) and all(x["name"] != value for x in result["labels"]),
                    "extlinux_label", "label 名稱無效或重複")
            current = {"name": value, "directives": {}}
            result["labels"].append(current)
            require(len(result["labels"]) <= 32, "extlinux_label", "label 數量超界")
            continue
        if key == "MENU":
            sub = value.split(None, 1)
            require(bool(sub), "extlinux_syntax", "MENU 缺少子指令")
            key = "MENU " + sub[0].upper()
            value = sub[1] if len(sub) == 2 else ""
        global_keys = {"DEFAULT", "TIMEOUT", "PROMPT", "MENU TITLE"}
        local_keys = {"KERNEL", "INITRD", "FDT", "FDTDIR", "FDTOVERLAYS", "APPEND", "MENU LABEL", "MENU DEFAULT", "KASLRSEED"}
        target = result["global"] if key in global_keys else current["directives"] if current else None
        require(key in global_keys | local_keys and target is not None, "extlinux_directive",
                f"第 {number} 行指令位置或語意尚未實作：{key}")
        require(key not in target, "extlinux_duplicate", "extlinux 指令重複：" + key)
        require(value or key in ("MENU DEFAULT", "KASLRSEED"), "extlinux_syntax", "extlinux 指令缺少值")
        if key in ("MENU DEFAULT", "KASLRSEED"):
            require(not value, "extlinux_syntax", "旗標指令不得附加參數")
        if key in ("TIMEOUT", "PROMPT"):
            require(re.fullmatch(r"[0-9]{1,6}", value), "extlinux_syntax", "extlinux 數值欄位無效")
        target[key] = value
    require(result["labels"], "extlinux_label", "extlinux 缺少 label")
    defaults = [x["name"] for x in result["labels"] if "MENU DEFAULT" in x["directives"]]
    explicit = result["global"].get("DEFAULT")
    require(len(defaults) <= 1 and (not explicit or not defaults or defaults == [explicit]),
            "extlinux_default", "DEFAULT 與 MENU DEFAULT 衝突或重複")
    selected = explicit or (defaults[0] if defaults else result["labels"][0]["name"])
    require(any(x["name"] == selected for x in result["labels"]), "extlinux_default", "DEFAULT 指向不存在的 label")
    result["selected_label"] = selected
    result["selection"] = "default" if explicit else "menu-default" if defaults else "first"
    return result


def bootargs(value):
    args = value.split()
    require(0 < len(args) <= 64 and all(re.fullmatch(binary.TOKEN, a) and not a.startswith("-") for a in args),
            "bootargs", "APPEND 含未解析變數、引號、控制字元或未支援參數")
    roots = [a[5:] for a in args if a.startswith("root=")]
    require(len(roots) == 1 and re.fullmatch("UUID=" + UUID, roots[0]),
            "root_uuid", "必須明示唯一 root=UUID；不猜 PARTUUID、裝置編號或執行期變數")
    require(not any(a.startswith("initrd=") for a in args), "append_initrd", "APPEND 的 initrd= 會影響載入語意，尚未支援")
    return args, roots[0][5:].lower()


class Evidence(binary._Evidence):
    """只重用檔案證據及二進位 helper，不套用 Allwinner 板型規則。"""

    def __init__(self, output, read_file, manifest):
        super().__init__(output, read_file, manifest)
        self.total = 0

    def get(self, name, role, *, required=False, maximum=MAX_FILE):
        path(name)
        if name not in self.cache:
            require(self.total < 768 * 1024**2, "total_size", "累計組件大小超界")
        value = super().get(name, role, required=required, maximum=maximum)
        if value is not None:
            require(len(value) <= maximum, "file_size", "組件大小超界")
        self.total = sum(len(item[0]) for item in self.cache.values() if item is not None)
        require(self.total <= 768 * 1024**2, "total_size", "累計組件大小超界")
        return value

    def check(self, label, function):
        def checked():
            value = function()
            require(not isinstance(value, bytes), "check_contract", "檢查結果必須是可序列化摘要，不得包含原始 bytes")
            return json.loads(json.dumps(value))
        try:
            return super().check(label, checked)
        except (KeyError, TypeError, AttributeError):
            self.block("invalid_data", "檢查資料缺欄位或型別不符：" + label)
        return None


def profile(e, board, policies):
    require(board in policies, "board", "板型不在此家族核對範圍")
    matrix = json.loads(e.source("config/bpi-lab/platforms.json"))
    rows = [r for r in matrix["boards"] if r["board"] == board]
    require(len(rows) == 1, "source_mapping", "平台矩陣板型缺失或重複")
    row = rows[0]
    policy_path = "config/validation/bananapi-" + policies[board] + ".json"
    policy = json.loads(e.source(policy_path))["boards"][row["artifact_board"]]
    p = {"board": board, "artifact_board": row["artifact_board"], "arch": row["architecture"],
         "family": row["family"], "boot_profile": row["boot_profile"],
         "dtb": policy["dtb"], "model": policy["model"], "compatible": policy["compatible"],
         "overlay_prefix": policy.get("overlay_prefix"),
         "boot_chain": row["boot_chain"], "special_boot": row["special_boot"]}
    hashes = {s["path"]: s["sha256"] for s in matrix["sources"]}
    for name in row["sources"]:
        data = e.source(name)
        require(digest(data)["sha256"] == hashes.get(name), "source_changed", "板型來源已變動，需重新審閱：" + name)
    e.source("packages/bsp/common/etc/initramfs/post-update.d/99-uboot")
    e.manifest.update(profile=p, arch=p["arch"], artifact_board=p["artifact_board"])
    e.manifest["limitations"] += [row["boot_chain"]["note"],
        "原廠安全啟動狀態未知；未驗證金鑰、簽章、反回滾、熔絲或載入器資格，不變更前置韌體。"]
    return p


def legacy(blob, *, arch, script=False):
    require(len(blob) >= 64, "legacy_header", "legacy 標頭截斷")
    magic, crc, _, size, _, _, data_crc, system, cpu, kind, comp, _ = struct.unpack(">7I4B32s", blob[:64])
    payload = blob[64:]
    require(magic == 0x27051956 and size == len(payload), "legacy_header", "legacy 標頭或長度錯誤")
    require(zlib.crc32(blob[:4] + bytes(4) + blob[8:64]) == crc and zlib.crc32(payload) == data_crc,
            "legacy_crc", "legacy CRC 不符")
    require(system == 5 and cpu == (2 if script else {"arm32": 2, "arm64": 22, "riscv64": 26}[arch])
            and kind == (6 if script else 3) and comp == (0 if script else 1),
            "legacy_type", "legacy 架構、用途或本倉封裝標記不符")
    if script:
        require(len(payload) >= 8 and struct.unpack_from(">II", payload) == (len(payload) - 8, 0),
                "script_format", "腳本不是單項完整封裝")
        return payload[8:]
    return payload


def kernel_config(blob, arch):
    require(type(blob) is bytes and 0 < len(blob) <= 1024**2, "kernel_config", "原配核心配置缺失或超界")
    values = {}
    for line in blob.decode("utf-8").splitlines():
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if unset:
            key, value = unset[1], "n"
        elif not line or line.startswith("#"):
            continue
        else:
            match = re.fullmatch(r'(CONFIG_[A-Za-z0-9_]+)=(y|m|n|[0-9]+|0x[0-9a-fA-F]+|"(?:[^"\\\x00-\x1f]|\\.)*")', line)
            require(match is not None, "kernel_config", "核心配置不是單純 Kconfig 賦值")
            key, value = match.groups()
        require(key not in values, "kernel_config", "核心配置欄位重複：" + key)
        values[key] = value
    expected = {"arm32": "CONFIG_ARM", "arm64": "CONFIG_ARM64", "riscv64": "CONFIG_RISCV"}[arch]
    require(values.get(expected) == "y" and all(values.get(k, "n") == "n" for k in
            {"CONFIG_ARM", "CONFIG_ARM64", "CONFIG_RISCV"} - {expected}), "kernel_config", "原配核心配置架構不符")
    require(all(values.get(k, "n") == "n" for k in ("CONFIG_CMDLINE_FORCE", "CONFIG_CMDLINE_OVERRIDE")),
            "kernel_cmdline", "核心強制命令列會覆寫原入口，尚未支援")
    if values.get("CONFIG_CMDLINE", '""') != '""' or values.get("CONFIG_CMDLINE_EXTEND", "n") != "n":
        require(arch == "arm32" and values.get("CONFIG_CMDLINE_EXTEND") == "y"
                and values.get("CONFIG_CMDLINE_FROM_BOOTLOADER", "n") == "n",
                "kernel_cmdline", "非 ARM32 EXTEND 的內建命令列尚未實作")
        require(re.fullmatch(r'"[A-Za-z0-9_.=,+:/ -]*"', values.get("CONFIG_CMDLINE", '""')),
                "kernel_cmdline", "核心內建命令列含跳脫、變數或未支援字元")
    return {**digest(blob), "values": values}


def _lz4_kernel(blob):
    """解析 Linux lz4_with_size；不使用無輸入長度界限的快速解碼介面。"""
    require(blob.startswith(b"\x02\x21\x4c\x18"), "kernel_compression", "不是 legacy LZ4 核心串流")
    name = ctypes.util.find_library("lz4")
    require(name is not None, "host_tool", "缺少 liblz4，無法核對原配 LZ4 核心")
    try:
        decode = ctypes.CDLL(name).LZ4_decompress_safe
    except (OSError, AttributeError) as exc:
        raise Error("host_tool", "liblz4 缺少有界解碼介面") from exc
    decode.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    decode.restype = ctypes.c_int
    chunk = 8 * 1024**2
    target = ctypes.create_string_buffer(chunk)
    output = bytearray()
    position, previous = 4, chunk
    while True:
        require(position + 4 <= len(blob), "kernel_compression", "LZ4 區塊或展開長度尾碼截斷")
        size = struct.unpack_from("<I", blob, position)[0]
        position += 4
        # Linux 建置規則在最後區塊後附加原始 Image 的 little-endian 長度。
        if output and size == len(output):
            return bytes(output), blob[position:]
        require(previous == chunk and 0 < size <= chunk + chunk // 255 + 16
                and position + size <= len(blob), "kernel_compression", "LZ4 區塊大小、次序或長度尾碼無效")
        capacity = min(chunk, binary.MAX_EXPANDED - len(output))
        require(capacity > 0, "kernel_compression", "LZ4 核心展開超界")
        previous = decode(blob[position:position + size], target, size, capacity)
        require(0 < previous <= capacity, "kernel_compression", "LZ4 區塊損壞或展開超界")
        output += target.raw[:previous]
        position += size


def _fit_tree(blob):
    """以 libfdt 結構化讀取有界 FIT，不解析反編譯文字或執行映像內容。"""
    require(64 <= len(blob) <= MAX_FILE and struct.unpack_from(">I", blob, 4)[0] == len(blob),
            "kernel_fit", "FIT 長度或外部資料封裝尚未支援")
    name = ctypes.util.find_library("fdt")
    require(name is not None, "host_tool", "缺少 libfdt，無法核對原配 FIT")
    try:
        lib = ctypes.CDLL(name)
        signatures = {
            "fdt_check_full": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_size_t]),
            "fdt_first_subnode": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "fdt_next_subnode": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "fdt_get_name": (ctypes.c_char_p, [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]),
            "fdt_first_property_offset": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "fdt_next_property_offset": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "fdt_getprop_by_offset": (ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_int,
                                                       ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int)]),
        }
        for key, (restype, argtypes) in signatures.items():
            function = getattr(lib, key)
            function.restype, function.argtypes = restype, argtypes
    except (OSError, AttributeError) as exc:
        raise Error("host_tool", "libfdt 缺少 FIT 結構核對介面") from exc
    data = ctypes.create_string_buffer(blob)
    require(lib.fdt_check_full(data, len(blob)) == 0, "kernel_fit", "FIT 結構損壞")
    result = {}
    def visit(offset, path):
        require(len(result) < 64 and path.count("/") <= 4 and path not in result,
                "kernel_fit", "FIT 節點過多、過深或重複")
        props = result[path] = {}
        prop = lib.fdt_first_property_offset(data, offset)
        while prop >= 0:
            key, size = ctypes.c_char_p(), ctypes.c_int()
            pointer = lib.fdt_getprop_by_offset(data, prop, ctypes.byref(key), ctypes.byref(size))
            require(pointer is not None and key.value is not None and 0 <= size.value <= MAX_FILE
                    and len(props) < 32, "kernel_fit", "FIT 屬性無效或過多")
            text = key.value.decode("ascii")
            require(text not in props, "kernel_fit", "FIT 屬性重複")
            props[text] = ctypes.string_at(pointer, size.value)
            prop = lib.fdt_next_property_offset(data, prop)
        require(prop == -1, "kernel_fit", "FIT 屬性遍歷失敗")
        child = lib.fdt_first_subnode(data, offset)
        while child >= 0:
            size = ctypes.c_int()
            raw_name = lib.fdt_get_name(data, child, ctypes.byref(size))
            require(raw_name is not None and re.fullmatch(NAME, raw_name.decode("ascii")), "kernel_fit", "FIT 節點名稱未支援")
            visit(child, path.rstrip("/") + "/" + raw_name.decode("ascii"))
            child = lib.fdt_next_subnode(data, child)
        require(child == -1, "kernel_fit", "FIT 節點遍歷失敗")
    visit(0, "/")
    return result


def _fit_kernel(blob, arch, release, config):
    tree = _fit_tree(blob)
    def text(props, key):
        raw = props.get(key, b"")
        require(raw.endswith(b"\0") and b"\0" not in raw[:-1], "kernel_fit", "FIT 字串缺失或不是單值：" + key)
        return raw[:-1].decode("ascii")
    require(set(tree.get("/", {})) <= {"description", "timestamp", "#address-cells"}
            and tree.get("/images") == {} and set(tree.get("/configurations", {})) == {"default"},
            "kernel_fit", "FIT 根節點或預設配置含未實作欄位")
    cells = tree["/"].get("#address-cells", b"")
    require(cells in (b"\0\0\0\1", b"\0\0\0\2"), "kernel_fit", "FIT 位址寬度未明示")
    selected = text(tree["/configurations"], "default")
    require(re.fullmatch(NAME, selected), "kernel_fit", "FIT 預設配置名稱無效")
    conf_path = "/configurations/" + selected
    conf = tree.get(conf_path, {})
    require(set(conf) <= {"description", "kernel"} and "kernel" in conf,
            "kernel_fit", "FIT 配置缺失，或含 ramdisk／FDT／loadables／安全鏈等未實作引用")
    image_name = text(conf, "kernel")
    require(re.fullmatch(NAME, image_name), "kernel_fit", "FIT 核心引用無效")
    image_path = "/images/" + image_name
    props = tree.get(image_path, {})
    require(set(props) == {"description", "data", "type", "arch", "os", "compression", "load", "entry"},
            "kernel_fit", "FIT 核心缺欄位或使用外部資料／未支援屬性")
    require(arch == "riscv64" and [text(props, key) for key in ("type", "arch", "os", "compression")]
            == ["kernel", "riscv", "linux", "none"], "kernel_fit", "FIT 僅實作原配單一未壓縮 RISC-V Linux 核心")
    hashes = {name: value for name, value in tree.items() if name.startswith(image_path + "/hash") and name.count("/") == 3}
    require(hashes and set(tree) == {"/", "/images", "/configurations", conf_path, image_path} | set(hashes),
            "kernel_fit", "FIT 有其他配置、簽章、核心或附帶組件，不能靜默忽略")
    checks = {}
    payload = props["data"]
    for name, values in hashes.items():
        require(set(values) == {"algo", "value"}, "kernel_fit", "FIT 雜湊欄位不完整或未知")
        algo = text(values, "algo")
        require(algo in ("crc32", "sha256"), "kernel_fit", "FIT 雜湊演算法尚未實作")
        actual = struct.pack(">I", zlib.crc32(payload)) if algo == "crc32" else hashlib.sha256(payload).digest()
        require(actual == values["value"], "kernel_fit_hash", "FIT 內嵌核心雜湊不符")
        checks[name] = {"algo": algo, "value": actual.hex()}
    require(payload[:4] != b"\xd0\x0d\xfe\xed" and not payload.startswith(b"\x1f\x8b"),
            "kernel_fit", "FIT 宣告未壓縮但資料是巢狀容器或 gzip")
    checked = kernel(payload, arch, release, config)
    width = int.from_bytes(cells, "big") * 4
    require(all(len(props[key]) == width for key in ("load", "entry")), "kernel_fit", "FIT 載入與入口位址寬度不符")
    load, entry = (int.from_bytes(props[key], "big") for key in ("load", "entry"))
    require(load % 4 == entry % 4 == 0 and load <= entry < load + len(payload), "kernel_fit", "FIT 載入或入口位址無效")
    return {"format": "FIT", "kernel_release": release, "payload": {**digest(payload), **checked},
            "fit": {"default": selected, "kernel": image_name, "load": load, "entry": entry,
                    "hashes": checks, "authenticated": False, "external_initrd_dtb": True}}


def kernel(blob, arch, release, config):
    """自有架構與內嵌配置契約，不依賴另一家族正在變動的核心政策。"""
    require(len(blob) >= 64, "kernel_format", "核心標頭截斷")
    if arch == "riscv64" and blob.startswith(b"\xd0\x0d\xfe\xed"):
        return _fit_kernel(blob, arch, release, config)
    expanded = blob
    compression = None
    if arch == "riscv64" and blob.startswith(b"\x1f\x8b\x08"):
        expanded, tail = binary._decompress(blob)
        require(not tail and len(expanded) >= 64, "kernel_compression", "RISC-V gzip 必須是唯一完整串流，不得附加資料")
        compression = "gzip"
    if arch == "arm32":
        magic, start, end = struct.unpack_from("<3I", blob, 36)
        require(magic == 0x016f2818 and end > start and end - start == len(blob), "kernel_format", "不是完整 ARM32 zImage")
        formats = {b"\x1f\x8b\x08": "gzip", b"\xfd7zXZ\0": "xz", b"\x02\x21\x4c\x18": "lz4"}
        offsets = [(m.start(), codec) for sig, codec in formats.items() for m in re.finditer(re.escape(sig), blob)]
        require(0 < len(offsets) <= 16, "kernel_compression", "核心壓縮入口缺失或過多")
        found = []
        for offset, codec in offsets:
            try:
                data, tail = _lz4_kernel(blob[offset:]) if codec == "lz4" else binary._decompress(blob[offset:])
            except Error as exc:
                if exc.code == "host_tool":
                    raise
                continue
            except (zlib.error, lzma.LZMAError):
                continue
            if re.search(rb"Linux version ([^\s\x00]+)", data):
                found.append((data, offset, codec, len(blob) - offset - len(tail)))
        require(len(found) == 1, "kernel_version", "zImage 無唯一可驗證核心")
        expanded, offset, codec, consumed = found[0]
        values = kernel_config(config, arch)["values"]
        selected = [k for k, value in values.items() if re.fullmatch(r"CONFIG_KERNEL_(GZIP|LZMA|XZ|LZO|LZ4|ZSTD)", k) and value == "y"]
        require(not selected or selected == ["CONFIG_KERNEL_" + codec.upper()],
                "kernel_compression", "核心壓縮格式與原配 config 不符")
        detail = {"format": "zImage", "expanded_bytes": len(expanded), "expanded": digest(expanded),
                  "compressed_offset": offset, "compressed_bytes": consumed, "payload_compression": codec}
    else:
        offset, size, flags = struct.unpack_from("<3Q", expanded, 8)
        require(len(expanded) <= size <= binary.MAX_EXPANDED and not flags & 1, "kernel_format", "Image 長度或位元組序不符")
        if arch == "riscv64":
            require(expanded[48:60] == b"RISCV\0\0\0RSC\x05" and struct.unpack_from("<I", expanded, 32)[0] == 2 and flags == 0,
                    "kernel_format", "RISC-V Image 標頭或版本不符")
        else:
            require(blob[56:60] == b"ARM\x64", "kernel_format", "不是 ARM64 raw Image")
        detail = {"format": "Image", "image_size": size, "text_offset": offset}
        if compression:
            detail.update(compression=compression, expanded=digest(expanded),
                          memory_bytes=size, bss_bytes=size - len(expanded))
    # Bluetooth 的固定格式字串不是已展開版本；其他不同版本仍須阻擋。
    banners = expanded.replace(b"Linux version %s (%s)\0", b"")
    require(set(re.findall(rb"Linux version ([^\s\x00]+)", banners)) == {release.encode()}, "kernel_version", "核心版本缺失、不唯一或不符")
    markers = [m.end() for m in re.finditer(b"IKCFG_ST", expanded)]
    if markers:
        require(len(markers) == 1, "kernel_config", "核心內嵌配置入口不唯一")
        embedded, tail = binary._decompress(expanded[markers[0]:])
        require(tail.startswith(b"IKCFG_ED") and embedded == config, "kernel_config", "原配 config 與核心內嵌配置不符")
        detail["embedded_config"] = digest(embedded)
    return {**detail, "kernel_release": release}


def kernel_command_line(e):
    """保留引導器參數，另建模 ARM32 DT 路徑的核心附加參數。"""
    config = e.manifest["checks"].get("kernel_config")
    if config is None or config["values"].get("CONFIG_CMDLINE_EXTEND") != "y":
        return None
    require(e.manifest["arch"] == "arm32", "kernel_cmdline", "此命令列合併契約只適用 ARM32")
    require(e.manifest["checks"].get("kernel", {}).get("embedded_config", {}).get("sha256") == config["sha256"],
            "kernel_cmdline", "EXTEND 必須由核心內嵌配置核對，不只相信外部 config")
    require(config["values"].get("CONFIG_USE_OF") == "y", "kernel_cmdline", "EXTEND 僅實作傳入 DTB 的命令列次序")
    args = e.manifest.get("bootargs_template")
    require(isinstance(args, list) and args, "kernel_cmdline", "缺少可合併的原入口命令列")
    built_in = config["values"].get("CONFIG_CMDLINE", '""')[1:-1]
    additions = built_in.split()
    keys = [a.split("=", 1)[0] for a in additions]
    sensitive = {"root", "rootfstype", "rootflags", "rootwait", "rootdelay", "console", "ubootpart", "initrd",
                 "init", "rdinit", "mem", "memmap", "cma", "crashkernel", "ro", "rw"}
    require(all(re.fullmatch(binary.TOKEN, a) and not a.startswith("-") for a in additions)
            and len(keys) == len(set(keys)) and not set(keys) & (sensitive | {a.split("=", 1)[0] for a in args}),
            "kernel_cmdline", "核心附加命令列重複、衝突或涉及尚未核定的媒體／記憶體覆寫")
    rendered = " ".join(args).replace("${partuuid}", "0" * 36) + " " + built_in
    require("$" not in rendered and len(rendered.encode()) < 1024,
            "kernel_cmdline", "ARM32 最終命令列可能截斷或含未核對變數")
    contract = {"mode": "arm32-dt-extend", "built_in": built_in, "append": additions,
                "effective_bootargs_template": args + additions, "command_line_size": 1024,
                "config_sha256": config["sha256"]}
    e.manifest["runtime_requirements"]["kernel_cmdline"] = contract
    return contract


def components(e, p, release, kernel_path, initrd_path):
    loaded = e.get(kernel_path, "kernel", required=True)
    versioned = e.get(f"/boot/vmlinuz-{release}", "kernel_versioned", required=True)
    initrd = e.get(initrd_path, "initrd", required=True)
    raw = e.get(f"/boot/initrd.img-{release}", "initrd_raw", required=True)
    config = e.get(f"/boot/config-{release}", "kernel_config", required=True, maximum=1024**2)
    if config is not None:
        e.check("kernel_config", lambda: kernel_config(config, p["arch"]))
    if loaded is not None:
        checked = e.check("kernel", lambda: kernel(loaded, p["arch"], release, config))
        if checked is not None and checked["format"] == "FIT":
            e.manifest["runtime_requirements"]["kernel_fit"] = {**checked["fit"], "command": "bootm"}
            e.manifest["limitations"].append("原配單核心 FIT 保持原位元組；內部 CRC／雜湊只核對完整性，不代表簽章、安全鏈或 bootm 載入區已核定。")
        if checked is not None and checked.get("compression"):
            e.manifest["runtime_requirements"]["kernel_decompression"] = {
                "compression": checked["compression"], "compressed_bytes": len(loaded),
                "expanded_bytes": checked["expanded"]["bytes"], "memory_bytes": checked["memory_bytes"],
                "kernel_comp_addr_r": "external-review", "kernel_comp_size": "external-review"}
            e.manifest["limitations"].append("原配 gzip 核心保持原位元組；U-Boot 解壓能力、暫存區、回搬區及含 BSS 的記憶體範圍仍須另行核定。")
        e.check("kernel_alias", lambda: require(loaded == versioned, "kernel_alias", "載入核心與指定版本檔不同"))
    if initrd is not None:
        wrapped = initrd.startswith(b"\x27\x05\x19\x56")
        container = e.check("initrd_container", lambda: digest(legacy(initrd, arch=p["arch"]) if wrapped else initrd))
        e.manifest["initrd_format"] = "legacy" if wrapped else "raw"
        if container is not None:
            payload = initrd[64:] if wrapped else initrd
            e.check("initrd_alias", lambda: require(payload == raw, "initrd_alias", "載入 initrd 與指定版本檔不同"))
            e.check("initramfs", lambda: binary._initramfs(payload, release))


def release_identity(e, p):
    blob = e.get("/etc/armbian-release", "release", required=True, maximum=MAX_TEXT)
    if blob is None:
        return
    values = e.check("release_parse", lambda: binary._env(blob, release=True))
    if values is not None:
        e.manifest["original_release"] = values
        expected = {"BOARD": p["artifact_board"], "BOARDFAMILY": p["family"],
                    "KERNEL_IMAGE_TYPE": "zImage" if p["arch"] == "arm32" else "Image",
                    "INITRD_ARCH": {"arm32": "arm", "arm64": "arm64", "riscv64": "riscv"}[p["arch"]]}
        e.check("release_identity", lambda: require(all(values.get(k) == v for k, v in expected.items()),
                                                    "release_identity", "release 板型、家族或架構不符"))


def dtb(e, role, p):
    record = e.manifest["files"][role]
    filename = e.output / record["evidence_path"]
    blob = filename.read_bytes()
    require(len(blob) >= 64, "dtb_format", "DTB 截斷")
    magic, total, structure, strings, reserve, version, compat, _, strsize, stsize = struct.unpack_from(">10I", blob)
    require(magic == 0xd00dfeed and total == len(blob) and version == 17 and compat <= 17
            and 40 <= structure <= total - stsize and structure % 4 == 0
            and 40 <= strings <= total - strsize and 40 <= reserve < total and reserve % 8 == 0
            and (structure + stsize <= strings or strings + strsize <= structure),
            "dtb_format", "DTB 標頭或資料區無效")
    def get(node, prop, fmt="s"):
        return e.run(["/usr/bin/fdtget", "-t", fmt, filename, node, prop]).decode().strip()
    actual = {"model": get("/", "model"), "compatible": get("/", "compatible").split()}
    require(actual == {k: p[k] for k in actual}, "dtb_identity", "DTB 根節點板型或 SoC 不符")
    require(not structure <= reserve < structure + stsize and not strings <= reserve < strings + strsize,
            "dtb_memreserve", "DTB 保留表與資料區重疊")
    limit = min(x for x in (structure, strings, total) if x > reserve)
    spans = []
    while True:
        require(reserve + 16 <= limit, "dtb_memreserve", "DTB 保留表截斷")
        start, size = struct.unpack_from(">QQ", blob, reserve)
        reserve += 16
        if start == size == 0:
            break
        require(size > 0 and start + size <= 2**64 and len(spans) < 1024, "dtb_memreserve", "DTB 保留區無效")
        spans.append({"start": start, "size": size})
    children = e.run(["/usr/bin/fdtget", "-l", filename, "/"]).decode().split()
    require(len(children) <= 4096, "dtb_size", "DTB 根節點數量超界")
    reserved = {}
    for child in children:
        if child.split("@", 1)[0] != "reserved-memory":
            continue
        node = "/" + child
        props = e.run(["/usr/bin/fdtget", "-p", filename, node]).decode().split()
        reserved[node] = {key: get(node, key, "bx") for key in props}
        nested = e.run(["/usr/bin/fdtget", "-l", filename, node]).decode().split()
        require(len(nested) <= 64, "reserved_memory", "DTB 保留節點數量超界")
        for name in nested:
            full = node + "/" + name
            props = e.run(["/usr/bin/fdtget", "-p", filename, full]).decode().split()
            reserved[full] = {key: get(full, key, "bx") for key in props}
            require(not e.run(["/usr/bin/fdtget", "-l", filename, full]).strip(),
                    "reserved_memory", "尚未實作多層保留記憶體節點")
    chosen = None
    if "chosen" in children:
        props = e.run(["/usr/bin/fdtget", "-p", filename, "/chosen"]).decode().split()
        if "bootargs" in props:
            chosen = get("/chosen", "bootargs")
    return {**actual, "memreserve": spans, "reserved_memory": reserved, "chosen_bootargs": chosen}


def overlays(e, p, roles):
    if "dtb" not in e.manifest["files"]:
        return
    e.check("dtb", lambda: dtb(e, "dtb", p))
    def apply():
        require(all(role in e.manifest["files"] for role in roles), "overlay_missing", "overlay 缺失，不可省略")
        original = e.output / e.manifest["files"]["dtb"]["evidence_path"]
        if roles:
            target = e.output / "analysis/effective.dtb"
            e.run(["/usr/bin/fdtoverlay", "-i", original, "-o", target,
                   *[e.output / e.manifest["files"][role]["evidence_path"] for role in roles]])
            data = target.read_bytes()
        else:
            data = original.read_bytes()
        e.save("files/effective.dtb", data)
        e.manifest["files"]["effective_dtb"] = {"evidence_path": "files/effective.dtb", **digest(data)}
        return dtb(e, "effective_dtb", p)
    e.check("overlay_application", apply)


def extlinux(e, p, release):
    e.source("lib/functions/image/partitioning.sh")
    blob = e.get("/boot/extlinux/extlinux.conf", "extlinux", required=True, maximum=MAX_TEXT)
    for alternative in ("/extlinux/extlinux.conf", "/boot/boot.scr", "/boot/boot.scr.uimg", "/boot/uEnv.txt"):
        if e.get(alternative, "alternate_" + str(len(e.cache)), maximum=MAX_TEXT) is not None:
            e.block("alternate_entry", "存在其他引導入口，優先序尚未核定：" + alternative)
    if blob is None:
        return
    parsed = e.check("extlinux_parse", lambda: parse(blob))
    if parsed is None:
        return
    e.manifest["extlinux"] = parsed
    selected = next(x for x in parsed["labels"] if x["name"] == parsed["selected_label"])
    d = selected["directives"]
    require("KERNEL" in d and "INITRD" in d, "extlinux_components", "所選 label 缺少核心或 initrd")
    absolute = [v for key, v in d.items() if key in ("KERNEL", "INITRD", "FDT", "FDTDIR") and v.startswith("/")]
    styles = {"/" if value.startswith("/boot/") else "/boot" for value in absolute}
    require(len(styles) == 1, "boot_mount", "無法唯一判定開機分割區路徑命名空間，或混用兩種前綴")
    mount = styles.pop()
    e.manifest["runtime_requirements"].update(boot_mount=mount, pxe_label_override="",
                                              selected_label=selected["name"], selection=parsed["selection"])
    e.manifest["entry"] = {"kind": "extlinux", "path": "/boot/extlinux/extlinux.conf",
                            "sha256": digest(blob)["sha256"], "selected_label": selected["name"]}
    if len(parsed["labels"]) > 1:
        e.block("extlinux_fallback", "多 label 的互動選擇及失敗後備尚未逐項核對；保留完整選單但不放行")
    if "KASLRSEED" in d:
        e.manifest["runtime_requirements"]["kaslrseed"] = "original-entry"
    components(e, p, release, resolve_path(d["KERNEL"], boot_mount=mount), resolve_path(d["INITRD"], boot_mount=mount))
    args = e.check("append", lambda: bootargs(d.get("APPEND", "")))
    if args is not None:
        e.manifest["bootargs_template"], e.manifest["root_uuid"] = args
    require(not ("FDT" in d and "FDTDIR" in d), "extlinux_fdt", "FDT 與 FDTDIR 並存，不靜默忽略其中之一")
    if "FDT" in d:
        fdt_path = resolve_path(d["FDT"], boot_mount=mount)
    elif "FDTDIR" in d:
        directory = d["FDTDIR"].rstrip("/")
        fdt_path = resolve_path(directory + "/" + p["dtb"], boot_mount=mount)
        e.manifest["runtime_requirements"]["fdtfile"] = p["dtb"]
        e.manifest["limitations"].append("FDTDIR 僅按板型來源擷取候選；外部範本須明示相同執行期 fdtfile，不證明已配對 U-Boot 的預設值。")
    else:
        raise Error("extlinux_fdt", "未明示 FDT／FDTDIR；不借用 U-Boot 內部 DTB")
    require(fdt_path.endswith("/" + p["dtb"]), "dtb_name", "extlinux DTB 路徑與板型來源不符")
    e.get(fdt_path, "dtb", required=True, maximum=16 * 1024**2)
    roles = []
    names = d.get("FDTOVERLAYS", "").split()
    require(len(names) <= 32 and len(set(names)) == len(names), "overlay_count", "overlay 數量超界或重複")
    for i, name in enumerate(names):
        role = f"overlay_{i:02d}"
        e.get(resolve_path(name, boot_mount=mount), role, required=True, maximum=16 * 1024**2)
        roles.append(role)
    e.manifest["overlay_order"] = roles
    overlays(e, p, roles)


def prepare_adapter(read_file, *, board, kernel_release, output, policies, handler, family):
    require(isinstance(kernel_release, str) and re.fullmatch(NAME, kernel_release), "kernel_release", "核心版本格式無效")
    require(board in policies, "board", "板型不在此適配器範圍")
    m = {"schema": SCHEMA, "adapter": family, "board": board, "kernel_release": kernel_release,
         "status": "blocked", "ready": False, "components_available": False,
         "hardware_validated": False, "execution_ready": False, "boot_config_validated": False,
         "root_uuid": None, "files": {}, "reads": [], "sources": {}, "commands": [], "checks": {},
         "blockers": [], "runtime_requirements": {}, "limitations": [
             "read_file 必須提供已核對的根檔案系統視圖及 /boot 掛載；不從回呼得知來源真實性或檔案系統 UUID。",
             "組件通過不代表實板、RAM、部署、救援、安全啟動或公開發布已核定。"]}
    e = Evidence(output, read_file, m)
    try:
        p = e.check("profile", lambda: profile(e, board, policies))
        if p is not None:
            release_identity(e, p)
            e.check("prepare", lambda: handler(e, p, kernel_release))
            if m["checks"].get("kernel_config", {}).get("values", {}).get("CONFIG_CMDLINE_EXTEND") == "y":
                e.check("kernel_command_line", lambda: kernel_command_line(e))
        if not m["blockers"]:
            m.update(status="prepared", ready=True, components_available=True)
        else:
            required = [
                "release_identity", "kernel", "kernel_config", "kernel_alias", "initrd_container", "initrd_alias",
                "initramfs", "dtb", "overlay_application"]
            if board == "bpi-sm10":
                required.append("vendor_initrd_alias")
            m["components_available"] = all(k in m["checks"] for k in required)
        e.save("manifest.json", binary._json(m))
    finally:
        os.close(e.fd)
    return m


def prepare(read_file, *, board="bpi-r2pro", kernel_release, output):
    """依家族交由板級適配器；此入口限明示 extlinux 的九板。"""
    if __package__:
        from . import bpi_lab_rockchip, bpi_lab_mediatek, bpi_lab_spacemit
    else:
        import bpi_lab_rockchip
        import bpi_lab_mediatek
        import bpi_lab_spacemit
    require(board in {"bpi-r2pro", "bpi-r3", "bpi-r3mini", "bpi-r4", "bpi-r4lite", "bpi-r4pro", "bpi-r64", "bpi-cm6", "bpi-f3"},
            "board", "此板不是已核對的 extlinux 入口")
    module = next(x for x in (bpi_lab_rockchip, bpi_lab_mediatek, bpi_lab_spacemit) if board in x.POLICIES)
    return module.prepare(read_file, board=board, kernel_release=kernel_release, output=output)


def validate_template(manifest, *, template, artifact_root):
    """驗證保留原入口的外部範本；回傳證據，不產生可直接執行的 boot 配置。"""
    require(manifest.get("schema") == SCHEMA and manifest.get("status") == "prepared"
            and manifest.get("ready") is True and manifest.get("blockers") == []
            and manifest.get("hardware_validated") is False and manifest.get("execution_ready") is False,
            "not_prepared", "組件未完整通過，禁止綁定範本")
    root = Path(artifact_root)
    actual, _ = uboot._read_regular(root / "manifest.json", 4 * 1024**2)
    require(actual == digest(binary._json(manifest)), "manifest_changed", "manifest 與持久證據不符")
    for record in manifest["files"].values():
        name = record["evidence_path"]
        path("/" + name)
        if record["bytes"] == 0:
            actual = binary._empty_marker(root / name)
        else:
            actual, _ = uboot._read_regular(root / name, MAX_FILE)
        require(actual == {k: record[k] for k in ("bytes", "sha256")}, "evidence_changed", "原配檔案證據已變動")
    keys = {"schema", "board", "kernel_release", "root_uuid", "entry", "runtime_requirements", "bootargs_template",
            "dtb_checks", "pairing_sha256", "firmware_review_sha256"}
    require(isinstance(template, dict) and set(template) == keys and template["schema"] == TEMPLATE_SCHEMA,
            "template_schema", "原入口範本欄位不完整或含未知欄位")
    for name in ("board", "kernel_release", "root_uuid", "entry", "runtime_requirements", "bootargs_template"):
        require(template[name] == manifest[name], "template_identity", "範本與原配契約不同：" + name)
    expected_dtb = {name: manifest["checks"][name] for name in ("dtb", "overlay_application")}
    require(template["dtb_checks"] == expected_dtb, "template_dtb", "範本未保留完整 DTB／保留記憶體契約")
    for name in ("pairing_sha256", "firmware_review_sha256"):
        require(isinstance(template[name], str) and re.fullmatch(r"[0-9a-f]{64}", template[name]),
                "template_evidence", "範本缺少配對或前置韌體審閱摘要")
    return {"schema": "bpi-lab-original-entry-validation-v1", "template": copy.deepcopy(template),
            "manifest_sha256": digest(binary._json(manifest))["sha256"], "artifact_contract_verified": True,
            "execution_ready": False, "hardware_validated": False,
            "limitations": ["此結果僅核對範本與原配檔案；外部證據摘要不等於已驗證其內容或授權。",
                            "保留原入口；未生成 UART 指令，不得交給共用 render／boot 當作直接引導配置。"]}
