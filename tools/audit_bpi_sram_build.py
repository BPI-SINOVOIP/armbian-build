#!/usr/bin/env python3
"""唯讀稽核 SRAM 建置的報告、快照、ELF 與 eGON；不執行待測程式或硬體。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
from typing import Sequence

try:
    import elftools
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection
except ImportError:
    elftools = None
    ELFError = ValueError


HEADER_BYTES = 96
ROM_BASE = 0x20000
STAMP = 0x5F0A6C39
SOURCE_DATE_EPOCH = "1789401600"
DEFCONFIG = "bananapi_m4zero_supervisor_defconfig"
SOURCE_FILES = ("supervisor.h", "supervisor_core.c", "supervisor.c",
                "supervisor_entry.S", "supervisor.lds")
INPUT_FILES = ("overlay.patch", *SOURCE_FILES, DEFCONFIG, "smoke_entry.S", "smoke.c", "smoke.lds")
REQUIRED_ARTIFACTS = (
    "spl1-egon.bin", "spl1.bin", "spl1.elf", "spl1.map", "spl1.config",
    "spl2-smoke.elf", "spl2-smoke.bin", "spl2-smoke.map", "build.log",
    "spl1.objdump.txt", "spl1.readelf.txt", "spl1.nm.txt",
    "spl2-smoke.objdump.txt", "spl2-smoke.readelf.txt", "spl2-smoke.nm.txt",
)
REQUIRED_Y = (
    "ARM", "ARM64", "ARCH_SUNXI", "MACH_SUN50I_H616", "SPL", "BPI_SRAM_SUPERVISOR",
    "SPL_SERIAL", "SPL_MMC", "SPL_CRC32", "SPL_SHA256", "SPL_SYS_MALLOC_SIMPLE",
    "SPL_LIBCOMMON_SUPPORT", "SPL_LIBGENERIC_SUPPORT",
)
REQUIRED_N = (
    "DRAM_SUN50I_H616", "SPL_STACK_R", "SPL_OF_CONTROL", "SPL_OF_LIBFDT", "SPL_OF_PLATDATA",
    "SPL_DM", "SPL_DM_SERIAL", "SPL_DM_MMC", "SPL_BLK", "SPL_LOAD_FIT", "SPL_FIT",
    "SPL_ATF", "SPL_OPTEE", "SPL_OS_BOOT", "SPL_ENV_SUPPORT", "SPL_MMC_WRITE",
    "SPL_SPI", "SPL_SPI_SUNXI", "SPL_I2C", "SPL_POWER", "SPL_SYS_MALLOC",
    "SPL_FS_FAT", "SPL_FS_EXT4", "SPL_FS_SQUASHFS", "SPL_RAW_IMAGE_SUPPORT",
    "SPL_LEGACY_IMAGE_FORMAT", "SPL_NET", "SPL_USB_HOST", "SPL_USB_GADGET",
    "SPL_DFU", "SPL_SATA", "SPL_NAND_SUPPORT", "SPL_NOR_SUPPORT", "SPL_RAM_SUPPORT",
    "SPL_RAM", "SPL_DM_RAM", "SPL_WATCHDOG", "SPL_WDT", "SPL_BOOTSTAGE",
    "SPL_PARTITIONS", "SPL_LOG", "AXP305_POWER", "SUPPORT_EMMC_BOOT",
)
FORBIDDEN_SYMBOL = re.compile(
    r"(?:^|_)(?:mctl|dram|ddr)(?:_|$)|"
    r"^(?:board_init_r|sunxi_board_init|clock_init_safe|boot_from_devices|board_boot_order|"
    r"spl_boot_device|spl_get_image_loader|spl_load_image|spl_load_simple_fit|"
    r"spl_load_fit_image|spl_invoke_atf|spl_invoke_optee|spl_board_prepare_for_boot|"
    r"jump_to_image_no_args|jump_to_image_linux)(?:$|[.$])|"
    r"^spl_(?:mmc|spi|nand|nor|net|ymodem|usb|sata|ram|dfu|bootrom).*load"
)


class AuditError(ValueError):
    """證據不足或未滿足第一層離線契約。"""


def require(condition, message: str) -> None:
    if not condition:
        raise AuditError(message)


def relative_name(name: str) -> tuple[str, ...]:
    require(isinstance(name, str) and bool(name), "證據路徑必須是非空字串")
    parts = name.split("/")
    require(not PurePosixPath(name).is_absolute() and "\\" not in name
            and all(part not in ("", ".", "..") for part in parts), "證據路徑不安全")
    return tuple(parts)


def check_digest(record: dict, digest: str, size: int, name: str) -> None:
    require(isinstance(record, dict), f"缺少檔案雜湊紀錄：{name}")
    expected = record.get("sha256")
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
            f"SHA-256 格式無效：{name}")
    require(type(record.get("bytes")) is int and record["bytes"] >= 0,
            f"檔案長度紀錄無效：{name}")
    require((expected, record["bytes"]) == (digest, size), f"檔案長度或 SHA-256 不符：{name}")


class Evidence:
    """只讀取指定建置目錄內的一般檔案，拒絕符號連結與特殊裝置。"""

    def __init__(self, root: Path):
        self.root = root.expanduser().absolute()
        require(".." not in self.root.parts, "建置路徑不得含 ..")
        require(not any(p.is_symlink() for p in (self.root, *self.root.parents)),
                "建置路徑不得經過符號連結")
        require(self.root.is_dir(), "建置目錄不存在")
        self.observed = {}

    def read(self, name: str, *, record: dict | None = None,
             keep: bool = True, limit: int = 256 * 1024 * 1024) -> bytes:
        parts = relative_name(name)
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), f"證據不是一般檔案：{name}")
            require(before.st_size <= limit, f"證據檔超過讀取上限：{name}")
            digest, size, chunks = hashlib.sha256(), 0, []
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                require(size <= limit, f"證據檔在讀取時超過上限：{name}")
                digest.update(chunk)
                if keep:
                    chunks.append(chunk)
            after = os.fstat(stream.fileno())
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and size == before.st_size,
                    f"稽核期間檔案已變動：{name}")
        actual = {"sha256": digest.hexdigest(), "bytes": size}
        if record is not None:
            check_digest(record, actual["sha256"], size, name)
        if name in self.observed:
            require(self.observed[name] == actual, f"重讀證據不一致：{name}")
        self.observed[name] = actual
        return b"".join(chunks)


def parse_config(blob: bytes) -> dict[str, str]:
    values = {}
    for line in blob.decode("utf-8").splitlines():
        enabled = re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.+)", line)
        disabled = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if enabled or disabled:
            key, value = (enabled[1], enabled[2]) if enabled else (disabled[1], "n")
            require(key not in values, f"組態欄位重複：{key}")
            values[key] = value
        else:
            require(not line or line.startswith("#"), "組態包含無法解析的內容")
    require(bool(values), "組態沒有可解析的欄位")
    return values


def config_value(value: str):
    if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", value):
        return int(value, 16 if value.lower().startswith("0x") else 10)
    return value


def inspect_config(snapshot: bytes, actual: bytes) -> dict:
    requested, config = parse_config(snapshot), parse_config(actual)
    for key, expected in requested.items():
        require(config_value(config.get(key, "n")) == config_value(expected),
                f"實際組態與 defconfig 快照不符：{key}")
    for key in REQUIRED_Y:
        require(config.get("CONFIG_" + key) == "y", f"必要組態未啟用：CONFIG_{key}")
    for key in REQUIRED_N:
        require(config.get("CONFIG_" + key, "n") == "n", f"禁止的組態已啟用：CONFIG_{key}")
    require(not any(key.startswith("CONFIG_DRAM_") and value in ("y", "m")
                    for key, value in config.items()), "仍有啟用的 DRAM 組態")
    for key, expected in (("SPL_TEXT_BASE", 0x20060), ("SPL_STACK", 0x58000),
                          ("SPL_BSS_START_ADDR", 0x48010)):
        require(config_value(config.get("CONFIG_" + key, "n")) == expected,
                f"固定 SRAM 位址不符：CONFIG_{key}")
    for key, maximum in (("SPL_MAX_SIZE", 0xFFA0), ("SPL_BSS_MAX_SIZE", 0x7FE0),
                         ("SPL_SYS_MALLOC_F_LEN", 0x2000), ("SYS_MALLOC_F_LEN", 0x2000)):
        number = config_value(config.get("CONFIG_" + key, "n"))
        require(type(number) is int and 0 < number <= maximum, f"SRAM 預算不符：CONFIG_{key}")
    require(config.get("CONFIG_SPL_LDSCRIPT") == '"arch/arm/mach-sunxi/supervisor.lds"',
            "未使用 SRAM 管理器連結腳本")
    return {"snapshot_fields": len(requested), "config_fields": len(config),
            "required_enabled": list(REQUIRED_Y), "required_disabled": list(REQUIRED_N),
            "device_tree_note": "主組態的 DT 名稱可保留；SPL 的 DT／FIT 載入必須關閉"}


def forbidden_symbols(names) -> list[str]:
    return sorted({name for name in names if FORBIDDEN_SYMBOL.search(name.split(".", 1)[0])
                   or ("u_boot_list" in name and "_blk_driver_" not in name)})


def inspect_elf(blob: bytes, raw: bytes, stage: str) -> dict:
    require(elftools is not None, "缺少 pyelftools；尚未解析 ELF，不能宣告通過")
    require(stage in ("spl1", "spl2"), "未知的 ELF 階段")
    entry, ceiling = (0x20060, 0x30000) if stage == "spl1" else (0x30000, 0x40000)
    elf = ELFFile(io.BytesIO(blob))
    require(elf.elfclass == 64 and elf.little_endian and elf["e_machine"] == "EM_AARCH64",
            "ELF 必須是小端序 AArch64／ELF64")
    require(elf["e_type"] == "ET_EXEC" and elf["e_entry"] == entry,
            f"{stage} ELF 類型或入口不符：預期 {entry:#x}")
    loads, segments = [], []
    for segment in elf.iter_segments():
        require(segment["p_type"] not in ("PT_INTERP", "PT_DYNAMIC"), "ELF 含動態載入需求")
        if segment["p_type"] != "PT_LOAD":
            continue
        p = {key: int(segment[key]) for key in
             ("p_vaddr", "p_paddr", "p_offset", "p_filesz", "p_memsz", "p_flags", "p_align")}
        require(p["p_filesz"] <= p["p_memsz"] and p["p_offset"] + p["p_filesz"] <= len(blob),
                "ELF 載入區段長度或檔案偏移無效")
        require(p["p_vaddr"] == p["p_paddr"], "ELF 虛擬／實體載入位址不同")
        start, end = p["p_paddr"], p["p_paddr"] + p["p_memsz"]
        lower = ROM_BASE if stage == "spl1" else entry
        code = lower <= start <= end <= ceiling
        bss = stage == "spl1" and 0x48010 <= start <= end <= 0x4FFF0 and p["p_filesz"] == 0
        require(code or bss, f"ELF PT_LOAD 超出 SRAM 區域：{start:#x}..{end:#x}")
        alignment = p["p_align"]
        require(alignment in (0, 1) or (alignment & (alignment - 1) == 0
                and p["p_offset"] % alignment == start % alignment), "ELF 區段對齊無效")
        loads.append(p)
        segments.append({"start": hex(start), "file_end": hex(start + p["p_filesz"]),
                         "memory_end": hex(end), "flags": p["p_flags"]})
    require(loads, "ELF 沒有 PT_LOAD 區段")
    ranges = sorted((p["p_vaddr"], p["p_vaddr"] + p["p_memsz"]) for p in loads if p["p_memsz"])
    require(all(left[1] <= right[0] for left, right in zip(ranges, ranges[1:])),
            "ELF PT_LOAD 記憶體區間重疊")
    symbols, undefined, tables, allocated, data_sections, bss_sections = {}, [], 0, [], [], []
    for section in elf.iter_sections():
        if isinstance(section, SymbolTableSection):
            tables += 1
            for symbol in section.iter_symbols():
                if not symbol.name:
                    continue
                if symbol["st_shndx"] == "SHN_UNDEF":
                    undefined.append(symbol.name)
                    continue
                value = int(symbol["st_value"])
                symbols.setdefault(symbol.name, set()).add(value)
        require(not (section["sh_type"] in ("SHT_REL", "SHT_RELA", "SHT_DYNAMIC")
                     and section["sh_size"]), "ELF 仍有重定位或動態區段")
        if not section["sh_flags"] & 2 or not section["sh_size"]:
            continue
        start, size = int(section["sh_addr"]), int(section["sh_size"])
        end = start + size
        require(section["sh_type"] in ("SHT_PROGBITS", "SHT_NOBITS"),
                f"不允許的可配置區段類型：{section.name}")
        containers = [p for p in loads if p["p_vaddr"] <= start and end <= p["p_vaddr"] + p["p_memsz"]]
        require(containers, f"區段不在 PT_LOAD 內：{section.name}")
        if section["sh_type"] == "SHT_NOBITS":
            require(not section["sh_flags"] & 4, "BSS 不得標示為可執行")
            if stage == "spl1":
                require(0x48010 <= start < end <= 0x4FFF0, "SPL1 BSS 超出保留區")
            else:
                require(entry <= start < end < ceiling, "SPL2 BSS 超出程式區")
            bss_sections.append((start, end))
        else:
            require(entry <= start < end <= ceiling, f"載入區段超出程式區：{section.name}")
            require(any(start - p["p_vaddr"] + p["p_offset"] == section["sh_offset"]
                        and end <= p["p_vaddr"] + p["p_filesz"] for p in containers),
                    f"區段位址與檔案偏移不一致：{section.name}")
            data = section.data()
            require(len(data) == size, "ELF 區段內容遭截斷")
            data_sections.append((start, end, data, int(section["sh_flags"]), section.name))
        allocated.append((start, end, section.name))
    require(tables and symbols, "ELF 缺少可稽核符號表，不接受已剝除符號的檔案")
    require(not undefined, "ELF 有未定義符號：" + ", ".join(sorted(set(undefined))))
    banned = forbidden_symbols(symbols)
    require(not banned, "ELF 含禁止的 DDR／正常開機符號：" + ", ".join(banned))
    for name in ("_start", "__bss_start", "__bss_end"):
        require(name in symbols and len(symbols[name]) == 1, f"ELF 缺少或具有歧義符號：{name}")
    def value(name):
        return next(iter(symbols[name]))
    require(value("_start") == entry, "_start 與 ELF 入口不一致")
    require(data_sections and min(s[0] for s in data_sections) == entry, "裸映像起點不符入口")
    require(any(start <= entry < end and flags & 4 for start, end, _, flags, _ in data_sections),
            "入口不在可執行區段")
    allocated.sort()
    require(all(left[1] <= right[0] for left, right in zip(allocated, allocated[1:])),
            "可配置的 ELF 區段互相重疊")
    load_end = max(s[1] for s in data_sections)
    bss_start, bss_end = value("__bss_start"), value("__bss_end")
    require(bss_start <= bss_end and bss_start % 16 == 0 and bss_end % 16 == 0,
            "BSS 符號順序或 16 位元組對齊不符")
    if stage == "spl1":
        require(bss_start == 0x48010 and bss_end <= 0x4FFF0, "SPL1 BSS 符號超出保留區")
        for name, expected in (("__image_copy_start", entry), ("_image_binary_end", load_end)):
            require(symbols.get(name) == {expected}, f"SPL1 映像邊界符號不符：{name}")
    else:
        require(load_end <= bss_start <= bss_end < ceiling, "SPL2 載入區／BSS 覆蓋堆疊區")
    if bss_sections:
        first, last = min(s[0] for s in bss_sections), max(s[1] for s in bss_sections)
        require(first == bss_start and last == bss_end, "BSS 區段與符號邊界不一致")
    else:
        require(bss_end == bss_start, "非零長度 BSS 符號缺少 NOBITS 區段")
    expected = bytearray(load_end - entry)
    mismatches = []
    for start, end, data, _, name in data_sections:
        expected[start - entry:end - entry] = data
        if end - entry > len(raw):
            mismatches.append(f"{name} 未完整收錄（{start:#x}..{end:#x}）")
        elif raw[start - entry:end - entry] != data:
            mismatches.append(f"{name} 內容不同（{start:#x}..{end:#x}）")
    require(raw == expected, "裸二進位與 ELF 可載入區段／零填補不一致："
            + "；".join(mismatches) + f"；實際 {len(raw)}，預期 {len(expected)} 位元組")
    return {"machine": "EM_AARCH64", "entry": hex(entry), "load_end_exclusive": hex(load_end),
            "last_loaded_address": hex(load_end - 1), "raw_bytes": len(raw),
            "bss_start": hex(bss_start), "bss_end_exclusive": hex(bss_end),
            "allocated_sections": [{"name": n, "start": hex(a), "end_exclusive": hex(b)}
                                   for a, b, n in allocated],
            "load_segments": segments, "symbol_count": len(symbols), "forbidden_symbols": banned,
            "scope": "符號缺席與區段比對僅屬靜態證據，不是可達性或 MMIO 執行證明"}


def inspect_egon(image: bytes, raw: bytes, config: dict[str, str]) -> dict:
    require(len(image) >= HEADER_BYTES and len(raw) > 0, "eGON 或裸 SPL 長度不足")
    instruction, checksum, length = struct.unpack_from("<I", image)[0], *struct.unpack_from("<II", image, 12)
    require(image[4:12] == b"eGON.BT0", "eGON magic 不符")
    require(instruction & 0xFF000000 == 0xEA000000, "eGON 首指令不是 A32 無條件 B")
    displacement = instruction & 0xFFFFFF
    if displacement & 0x800000:
        displacement -= 1 << 24
    target = ROM_BASE + 8 + displacement * 4
    require(target == ROM_BASE + HEADER_BYTES, "eGON 分支未跳過 96 位元組標頭至 0x20060")
    require(length == len(image) and length % 512 == 0, "eGON 實際長度、宣告長度或 512 位元組對齊不符")
    require(ROM_BASE + length <= 0x30000, "eGON 含填補的載入範圍超出 SPL1 程式區")
    require(HEADER_BYTES + len(raw) <= length, "eGON 未完整容納裸 SPL")
    require(image[HEADER_BYTES:HEADER_BYTES + len(raw)] == raw, "eGON 主體與裸 SPL 不一致")
    require(not any(image[HEADER_BYTES + len(raw):]), "eGON 尾端包含非零填補或附加映像")
    stamped = bytearray(image)
    struct.pack_into("<I", stamped, 12, STAMP)
    calculated = sum(word[0] for word in struct.iter_unpack("<I", stamped)) & 0xFFFFFFFF
    require(calculated == checksum, "eGON 加總校驗不符")
    require(image[20:23] == b"SPL" and image[23] in (1, 2, 3), "eGON SPL 識別或版本不符")
    require(not any(image[24:32]) and not any(image[36:44]), "未執行的 eGON 帶有 FEL／DRAM／媒體狀態")
    offset = struct.unpack_from("<I", image, 32)[0]
    dt_name = None
    if offset:
        require(44 <= offset < HEADER_BYTES and b"\0" in image[offset:HEADER_BYTES], "DT 名稱超出標頭")
        dt_name = image[offset:HEADER_BYTES].split(b"\0", 1)[0].decode("ascii")
        require(config.get("CONFIG_DEFAULT_DEVICE_TREE") == json.dumps(dt_name), "標頭 DT 名稱與組態不符")
    return {"header_bytes": HEADER_BYTES, "branch_encoding": "A32 B", "branch_target": hex(target),
            "elf_machine_expected": "EM_AARCH64", "image_bytes": length, "alignment_bytes": 512,
            "also_aligned_8192": length % 8192 == 0, "padding_bytes": length - HEADER_BYTES - len(raw),
            "checksum": hex(checksum), "dt_name": dt_name,
            "note": "A32 分支標頭與 AArch64 ELF 分開核對；DT 名稱不表示 SPL 載入 DTB"}


def report_metadata(report: dict) -> dict:
    require(report.get("status") == "離線建置完成，尚未實板驗證" and "error" not in report,
            "建置報告未標示離線完成，或仍包含錯誤")
    require(report.get("inputs_unchanged") is True, "建置未確認輸入快照保持一致")
    ddr_v2 = report.get("ddr_v2", False)
    lab_v3 = report.get("lab_v3", False)
    require(type(ddr_v2) is bool, "報告 ddr_v2 必須是布林值")
    require(type(lab_v3) is bool and (not lab_v3 or ddr_v2), "報告 lab_v3 必須是布林值且保留 DDR 契約")
    invocation = report.get("invocation")
    require(isinstance(invocation, list) and bool(invocation)
            and all(isinstance(arg, str) and bool(arg) for arg in invocation),
            "建置 invocation 必須是非空字串參數陣列")
    require(("--lab-v3" in invocation) is lab_v3, "報告 lab_v3 與建置 invocation 的 --lab-v3 不符")
    require(("--ddr-v2" in invocation or "--lab-v3" in invocation) is ddr_v2,
            "報告 ddr_v2 與建置 invocation 的 --ddr-v2 不符")
    source = report.get("source", {})
    for key in ("commit", "tree"):
        require(isinstance(source.get(key), str) and re.fullmatch(r"[0-9a-f]{40}", source[key])
                and source[key] == source.get("expected_" + key), f"來源 {key} 與報告宣告不一致")
    archive = source.get("archive", {})
    check_digest(archive, source.get("expected_archive_sha256"), archive.get("bytes"), "原版封存")
    commands = report.get("commands")
    require(isinstance(commands, list) and bool(commands), "缺少命令紀錄")
    for command in commands:
        require(isinstance(command, dict) and type(command.get("returncode")) is int
                and command["returncode"] == 0, "命令紀錄包含失敗或未完成命令")
        require(isinstance(command.get("argv"), list) and command["argv"]
                and all(isinstance(arg, str) for arg in command["argv"]), "命令 argv 格式無效")
    makes = [c["argv"] for c in commands if Path(c["argv"][0]).name in ("make", "gmake")]
    require(any("spl/sunxi-spl.bin" in argv for argv in makes), "缺少實際 SPL 目標命令紀錄")
    require(not any(target in argv for argv in makes for target in
                    ("all", "u-boot", "u-boot.bin", "u-boot.itb", "u-boot-sunxi-with-spl.bin")),
            "命令紀錄包含正常 U-Boot 建置目標")
    environment = report.get("environment", {})
    epoch = environment.get("SOURCE_DATE_EPOCH")
    require(epoch == SOURCE_DATE_EPOCH, f"SOURCE_DATE_EPOCH 不符固定規格 {SOURCE_DATE_EPOCH}")
    for key, value in (("KBUILD_BUILD_USER", "bpi"),
                       ("KBUILD_BUILD_HOST", "bpi"), ("LC_ALL", "C"), ("GIT_NO_LAZY_FETCH", "1")):
        require(environment.get(key) == value, f"固定建置環境不符：{key}")
    return {"source_commit": source["commit"], "source_tree": source["tree"],
            "archive_sha256": archive["sha256"], "commands": len(commands), "source_date_epoch": epoch,
            "ddr_v2": ddr_v2, "lab_v3": lab_v3,
            "note": "來源身分依建置報告交叉核對，未重新匯出或驗證上游簽章"}


class Audit:
    def __init__(self):
        self.checks = []

    def check(self, name: str, operation):
        try:
            detail = operation()
        except (AuditError, OSError, ValueError, KeyError, TypeError, ELFError) as exc:
            detail = str(exc) if isinstance(exc, AuditError) else f"證據無法解析或讀取（類型={type(exc).__name__}）"
            self.checks.append({"name": name, "status": "未通過", "detail": detail})
            return None
        self.checks.append({"name": name, "status": "通過", "detail": detail})
        return detail


def audit_build(root: Path) -> dict:
    audit = Audit()
    evidence = Evidence(root)
    report_bytes = evidence.read("build-report.json", limit=16 * 1024 * 1024)
    report = json.loads(report_bytes)
    require(isinstance(report, dict), "建置報告頂層必須是物件")
    audit.check("建置報告與來源宣告", lambda: report_metadata(report))

    def verify_inputs():
        records = report.get("inputs", {})
        require(isinstance(records, dict) and set(INPUT_FILES).issubset(records), "輸入快照紀錄不完整")
        for name in INPUT_FILES:
            content = evidence.read("inputs/" + name, record=records[name], limit=8 * 1024 * 1024)
            if name in SOURCE_FILES or name == DEFCONFIG:
                target = "source/" + ("configs/" if name == DEFCONFIG else "arch/arm/mach-sunxi/") + name
                require(evidence.read(target, limit=8 * 1024 * 1024) == content, f"建置來源不符輸入快照：{name}")
        return {"verified_inputs": list(INPUT_FILES)}

    audit.check("輸入快照與實際覆蓋檔案", verify_inputs)

    def verify_outputs():
        records = report.get("artifacts", {})
        require(isinstance(records, dict) and set(REQUIRED_ARTIFACTS).issubset(records), "產物雜湊紀錄不完整")
        for name, record in records.items():
            evidence.read(name, record=record, keep=False)
        for prefix in ("stack-usage/spl1/", "stack-usage/spl2-smoke/"):
            require(any(name.startswith(prefix) and name.endswith(".su") for name in records),
                    f"缺少堆疊用量證據：{prefix}")
        return {"verified_artifacts": len(records)}

    audit.check("所有記錄產物的實際長度與雜湊", verify_outputs)

    def verify_config():
        actual = evidence.read("build/.config", limit=1024 * 1024)
        saved = evidence.read("spl1.config", limit=1024 * 1024)
        require(actual == saved, "建置 .config 與交付組態不一致")
        recorded = report.get("config", {})
        check_digest(recorded, hashlib.sha256(actual).hexdigest(), len(actual), "建置組態")
        require(recorded.get("text") == actual.decode("utf-8"), "報告內組態文字與實檔不同")
        config = parse_config(actual)
        ddr_v2 = config.get("CONFIG_BPI_SRAM_DDR_V2", "n")
        require(ddr_v2 in ("y", "n"), "CONFIG_BPI_SRAM_DDR_V2 必須為 y 或 n")
        require((ddr_v2 == "y") is report.get("ddr_v2", False),
                "最終 CONFIG_BPI_SRAM_DDR_V2 與報告 ddr_v2 不符")
        lab_v3 = config.get("CONFIG_BPI_SRAM_LAB_V3", "n")
        require(lab_v3 in ("y", "n") and (lab_v3 == "y") is report.get("lab_v3", False),
                "最終 CONFIG_BPI_SRAM_LAB_V3 與報告不符")
        generated = parse_config(evidence.read("build/include/config/auto.conf", limit=1024 * 1024))
        expected = {key: config_value(value) for key, value in config.items() if value != "n"}
        require({key: config_value(value) for key, value in generated.items()} == expected,
                "auto.conf 與 .config 不一致")
        return {**inspect_config(evidence.read("inputs/" + DEFCONFIG), actual), "ddr_v2": ddr_v2 == "y"}

    audit.check("快照、建置組態與最低 SRAM 限制", verify_config)
    for stage, stem in (("spl1", "spl1"), ("spl2", "spl2-smoke")):
        audit.check(stem + " ELF 結構與裸二進位", lambda stage=stage, stem=stem:
                    inspect_elf(evidence.read(stem + ".elf", limit=32 * 1024 * 1024),
                                evidence.read(stem + ".bin", limit=128 * 1024), stage))
    audit.check("eGON 標頭、分支、校驗與完整主體", lambda:
                inspect_egon(evidence.read("spl1-egon.bin", limit=128 * 1024),
                             evidence.read("spl1.bin", limit=128 * 1024),
                             parse_config(evidence.read("build/.config"))))
    audit.check("報告在稽核期間保持一致", lambda: {"sha256": hashlib.sha256(
        evidence.read("build-report.json", limit=16 * 1024 * 1024)).hexdigest()})
    passed = all(check["status"] == "通過" for check in audit.checks)
    return {"status": "第一層離線稽核通過" if passed else "第一層離線稽核未通過", "passed": passed,
            "build_directory": str(evidence.root), "hardware_validation": "未執行",
            "parser": {"name": "pyelftools", "version": elftools.__version__ if elftools else None},
            "checks": audit.checks, "observed_files": evidence.observed,
            "limitations": ["未執行韌體、未接觸硬體、SD、UART 或電源，也未重新編譯。",
                            "雜湊只核對報告與檔案一致，不是簽章或獨立來源信任證明。",
                            "符號缺席不證明沒有間接呼叫、內嵌 DDR 存取或任意 MMIO。",
                            "仍須審查入口控制流程、堆疊與配置器的執行期界限及實板行為。"]}


class ChineseParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "參數錯誤：請指定 --build-dir，並以 --help 查看說明。\n")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：")


def build_parser():
    parser = ChineseParser(description=__doc__, add_help=False, allow_abbrev=False)
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明並離開")
    parser.add_argument("--build-dir", required=True, type=Path, metavar="目錄", help="既有建置目錄；JSON 報告輸出至標準輸出")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit_build(args.build_dir)
    except (AuditError, OSError, ValueError, KeyError, TypeError, ELFError) as exc:
        message = str(exc) if isinstance(exc, AuditError) else f"無法讀取稽核證據（類型={type(exc).__name__}）"
        result = {"status": "第一層離線稽核未通過", "passed": False,
                  "hardware_validation": "未執行", "error": message}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
