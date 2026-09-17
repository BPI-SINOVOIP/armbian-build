#!/usr/bin/env python3
"""special 原配組件的一次性 UART 執行器；不開串口、不操作電源或保存環境。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import time

if __package__:
    from . import bpi_lab_special as special
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_special as special
    import bpi_lab_uboot as uboot


SCHEMA = "bpi-lab-special-runtime-v1"
QUALIFICATION_SCHEMA = "bpi-lab-special-runtime-qualification-v1"
SUPPORTED = {"bpi-f2p", "bpi-f2s", "bpi-ai2n", "bpi-m6"}
VENDOR_ABI = "realtek-lab-v1"
VENDOR_BOARDS = {"bpi-m4", "bpi-w2"}
DEPENDENCIES = ("bpi_lab_special_runtime.py", "bpi_lab_special.py", "bpi_lab_amlogic.py",
                "bpi_lab_uboot.py", "bpi_lab_console.py")
LIMITS = ("僅觀察同一 UART 的媒體、RAM 摘要及核心標記；不證明 Linux 根媒體、韌體執行、"
          "完整引導鏈、smoke 或實板資格。核心啟動後可能寫入原配根檔案系統。")


class RuntimeError(uboot.UBootError):
    """執行契約或 UART 回應不符；禁止自動重試或降級。"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _fields(value, fields, name):
    require(type(value) is dict and set(value) == set(fields.split()), name + "欄位缺失或含未知欄位")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON 有重複欄位")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(RuntimeError("JSON 常數無效")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RuntimeError("證據不是有效且無重複欄位的 JSON") from exc


def _reference(reference, maximum=4 * 1024**2):
    _fields(reference, "path sha256", "證據參照")
    require(isinstance(reference["path"], str) and Path(reference["path"]).is_absolute(), "證據參照須為絕對路徑")
    require(isinstance(reference["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]), "證據摘要格式不符")
    data = special.shared._read_evidence(reference["path"], maximum)
    require(_sha(data) == reference["sha256"], "證據實際位元組摘要不符")
    return data


def scope_digest(config):
    """資格綁定完整輸入；排除 qualification 參照，避免自我摘要循環。"""
    return _sha(special.encoded({key: value for key, value in config.items() if key != "qualification"}))


def vendor_blockers(board):
    """回報已讀原廠 ABI 的具體缺口；不把舊版入口偽裝成主線。"""
    if board == "bpi-w2":
        return ["gosd 的 boot_from_sd 會重新執行未限長 fatload，並在內部啟動 ACPU 後進入核心；"
                "initrd 讀取失敗亦未必停止；不能只加外部 filelen 宣告",
                "已有 realtek-lab-v1 有界載入與同源交接實作；須提供其來源、建置及實板核定，不能冒充原廠既有 gosd"]
    if board == "bpi-m4":
        return ["go all 的 go a／go k 使用 DDR、不重讀載荷，但會啟動 ACPU、修改 IPC／PMIC；"
                "原 bdinfo 對 Realtek 隱藏 relocation／stack 欄位，主線 ABI 不適用",
                "已有 realtek-lab-v1 實際狀態、載入及雜湊命令；須提供其來源、建置及實板核定"]
    return []


def _lmb(output):
    count = int(uboot._one(r"\s*reserved\.(?:cnt|count)\s*=\s*(0x[0-9a-fA-F]+)", output, "LMB 數量")[1], 16)
    result = []
    for line in uboot._lines(output):
        if not re.match(r"\s*reserved\[", line):
            continue
        match = re.fullmatch(r"\s*reserved\[(\d+)\]\s+\[(0x[0-9a-fA-F]+)-(0x[0-9a-fA-F]+)\],\s*"
                             r"(0x[0-9a-fA-F]+) bytes,? flags: ([a-z0-9x, -]+)", line)
        require(match is not None and int(match[1]) == len(result), "LMB 索引或格式不符")
        start, end, size = (int(match[index], 16) for index in (2, 3, 4))
        require(size > 0 and end == start + size - 1, "LMB 保留區大小矛盾")
        flags = match[5].split(", ")
        require(len(set(flags)) == len(flags) and set(flags) <= {"none", "no-map", "no-overwrite", "no-notify"}
                and ("none" not in flags or flags == ["none"]), "LMB 旗標不是已讀主線格式")
        uboot._span({"start": start, "size": size}, 64)
        result.append({"start": start, "size": size, "flags": match[5]})
    require(count == len(result) and count <= 64, "LMB 保留表截斷或超限")
    uboot._disjoint(result, "LMB 保留表")
    return result


def _memory(output, context):
    core = context["core"]
    actual = _lmb(output)
    if "initial_lmb" in context:
        expected = copy.deepcopy(context["initial_lmb"])
        expected += [{"start": context["loads"][role]["address"],
                      "size": context["loads"][role]["bytes"], "flags": "none"}
                     for role in context.get("loaded", ())]
        merged = []
        for span in sorted(expected, key=lambda item: item["start"]):
            if merged and merged[-1]["start"] + merged[-1]["size"] == span["start"] and merged[-1]["flags"] == span["flags"]:
                merged[-1]["size"] += span["size"]
            else:
                merged.append(dict(span))
        require(actual == merged, "即時 LMB 必須等於原始核定及本輪實收載入範圍；旗標、大小或新增區域不符")
    else:
        for span in actual:
            require(any(uboot._contains(area, span) for area in core["ram"]["reserved"]), "LMB 區域未列入核定保護範圍")
            for item in context["firmware"].values():
                require(not uboot._overlap(span, uboot._slot(item)), "韌體載入區與即時 LMB 保留區重疊")
    # LMB 由上方逐項核對；共用解析只核對即時 gd／DRAM，避免把合法 load 配置誤認保護區。
    gd_output = "\n".join(line for line in uboot._lines(output) if not re.match(r"\s*reserved\[", line)).encode()
    uboot._memory(gd_output, core)
    return actual


def _memreserve(blob):
    cursor = struct.unpack_from(">I", blob, 16)[0]
    result = []
    while cursor + 16 <= len(blob):
        address, size = struct.unpack_from(">QQ", blob, cursor)
        cursor += 16
        if (address, size) == (0, 0):
            return result
        require(len(result) < 256 and size > 0, "DTB 保留表無效或超限")
        result.append({"start": address, "size": size})
    raise RuntimeError("DTB 保留表未終止")


def _transport(config, recipe, pairing):
    execution = config["execution"]
    transport = execution["transport"]
    vendor = recipe.get("vendor") is not None
    _fields(transport, "kind extraction image_paths media" + (" root_preparation" if vendor else ""), "傳輸")
    require(transport["kind"] == "mmc-original" and recipe["source"]["type"] == ("vendor-fat" if vendor else "mmc"), "只執行已配對 MMC 原檔；不猜測 TFTP 發布或原廠 sd ABI")
    require(transport["media"] == "emmc", "CID 契約限固定 eMMC；主線 mmc reg read 明確拒絕 SD")
    require(transport["media"] in pairing, "配對缺少載入媒體")
    media = pairing[transport["media"]]
    require(isinstance(media, dict) and isinstance(media.get("cid"), str)
            and re.fullmatch(r"[0-9a-f]{32}", media["cid"]), "配對 CID 缺失或無效")
    extraction = _json(_reference(transport["extraction"]))
    require(extraction.get("schema") in ("bpi-lab-image-v1", "bpi-lab-image-replay-v1")
            and extraction.get("ok") is True and extraction.get("source_verified") is True
            and extraction.get("hardware_validated") is False, "擷取證據未通過，不能映射原媒體路徑")
    root_uuid = recipe["vendor"]["root_identity"]["uuid"] if vendor else recipe["root_uuid"]
    require(extraction.get("filesystem_uuid") == root_uuid, "擷取根 UUID 與原配環境不符")
    if vendor:
        require(extraction.get("filesystem_labels_complete") is True
                and extraction.get("filesystem_label") == "BPI-ROOT"
                and extraction.get("filesystem_label_unique") is True,
                "LABEL 根識別需要所有分割區均已解析且映像內唯一的完整標籤證據")
        require(recipe["source"]["device"] == "mmc" and recipe["source"]["partition"] == "0:1"
                and recipe["source"]["identity_sha256"] == transport["extraction"]["sha256"], "原廠來源須綁定實際擷取及 eMMC FAT 0:1")
        prepared = _json(_reference(transport["root_preparation"]))
        require(recipe["vendor"]["root_identity"]["evidence_sha256"] == transport["root_preparation"]["sha256"]
                and prepared.get("schema") == "bpi-lab-prepare-v1" and prepared.get("status") == "prepared"
                and prepared.get("board") == config["board"] and prepared.get("hardware_validated") is False
                and prepared.get("root_identity_verified") is True and prepared.get("root_uuid") == root_uuid
                and prepared.get("root_binding", {}).get("label") == "BPI-ROOT"
                and prepared.get("root_binding", {}).get("unique_in_image") is True
                and prepared.get("extraction", {}).get("sha256") == transport["extraction"]["sha256"],
                "Realtek 根標籤綁定須核對實際 prepare 文件，不能只填摘要欄位")
    loads = {item["role"]: item for item in recipe["loads"]}
    _fields(transport["image_paths"], " ".join(loads), "原檔路徑映射")
    paths = {}
    for role, item in loads.items():
        logical = transport["image_paths"][role]
        require(logical == item["image_path"], "載入邏輯路徑不是原配已核對路徑")
        record = extraction["files"].get(logical)
        require(isinstance(record, dict) and record.get("digest") == {key: item[key] for key in ("bytes", "sha256")},
                "原媒體檔案摘要與核對組件不符")
        index = record.get("volume_index", extraction["partition"]["index"])
        rows = [row for row in extraction.get("partitions", [extraction["partition"]]) if row["index"] == index]
        require(len(rows) == 1 and index == (1 if vendor else recipe["source"]["partition"])
                and rows[0]["partuuid"] == recipe["source"]["partuuid"], "原檔不在指定 PARTUUID 的載入分割區")
        path = record.get("resolved")
        require(isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", path)
                and len(path) <= 120 and not {".", ".."}.intersection(path.split("/")), "實際媒體路徑不安全")
        paths[role] = path
    return loads, paths, media["cid"]


def _context(config):
    _fields(config, "schema board artifact_root template execution pairing qualification", "執行器配置")
    config = copy.deepcopy(config)
    require(config["schema"] == SCHEMA and isinstance(config["board"], str), "執行器 schema 或板型不符")
    if config["board"] in VENDOR_BOARDS and config.get("execution", {}).get("uboot", {}).get("abi") == VENDOR_ABI:
        return _vendor_context(config)
    reasons = vendor_blockers(config["board"])
    require(not reasons, "；".join(reasons))
    require(config["board"] in SUPPORTED, "板型未接入此一次性執行器")
    require(isinstance(config["artifact_root"], str) and Path(config["artifact_root"]).is_absolute(), "原配證據根目錄須為絕對路徑")
    recipe = special.bootconfig(config["artifact_root"], template=config["template"])
    require(recipe["board"] == config["board"] and recipe.get("vendor") is None, "配方板型或執行 ABI 不符")
    execution = config["execution"]
    _fields(execution, "uboot boot_region capacities kernel_entry fdt_extra transport", "執行細節")
    _fields(execution["uboot"], "prompt version address_bits line_limit abi", "U-Boot ABI")
    require(execution["uboot"]["abi"] == "mainline-v2025.01", "原廠 ABI 不可冒充已核對主線 ABI")
    pairing = _json(_reference(config["pairing"]))
    require(pairing.get("schema") == "bpi-lab-pairing-v1" and pairing.get("approved") is True,
            "板級配對未核定")
    require(isinstance(pairing.get("hardware_id"), str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", pairing["hardware_id"]), "實板識別無效")
    uart = pairing.get("uart", {})
    require(isinstance(uart.get("stable_path"), str) and re.fullmatch(r"/dev/serial/by-(?:id|path)/[A-Za-z0-9_:+.-]+", uart["stable_path"])
            and pairing.get("resources", {}).get("uart") == uart["stable_path"]
            and type(uart.get("baud")) is int and 9600 <= uart["baud"] <= 4000000, "缺少明示 UART 穩定路徑或速率配對")
    loads, paths, cid = _transport(config, recipe, pairing)
    _fields(execution["capacities"], " ".join(loads), "載荷容量")
    core = {"schema": uboot.SCHEMA, "arch": special.PROFILES[config["board"]]["arch"],
            "uboot": {**execution["uboot"], "pairing_sha256": config["pairing"]["sha256"],
                      "qualification_sha256": config["qualification"]["sha256"]},
            "source": recipe["source"], "ram": {**recipe["ram"], "boot": execution["boot_region"]},
            "files": {}, "bootargs": recipe["bootargs"], "kernel_release": recipe["kernel_release"],
            "fdt_extra": execution["fdt_extra"]}
    firmware, blobs = {}, {}
    for role, item in loads.items():
        record, header = uboot._read_regular(Path(config["artifact_root"]) / item["path"], special.MAX_FILE)
        require(record == {key: item[key] for key in ("bytes", "sha256")}, "本機組件摘要改變")
        blobs[role] = (_reference({"path": str(Path(config["artifact_root"]) / item["path"]),
                                   "sha256": item["sha256"]}, special.MAX_FILE) if role == "dtb" else header)
        values = {key: item[key] for key in ("address", "bytes", "sha256", "format")}
        values.update(path=paths[role], capacity=execution["capacities"][role])
        if role == "kernel":
            values["entry"] = execution["kernel_entry"]
        if role in ("kernel", "initrd", "dtb"):
            core["files"][role] = values
        else:
            require(config["board"] == "bpi-ai2n" and role in ("opencva", "codec")
                    and item["format"] == "opaque-vendor", "未知額外載荷不能直接交接")
            firmware[role] = values
    core = uboot.validate_config(core)
    require(uboot._boot_command(core) == recipe["boot_command"], "核對後的主線命令與原配配方不同")
    for role in core["files"]:
        uboot._header(blobs[role][:64], core["files"][role], role, core)
    require(core["fdt_extra"] <= 65536, "DTB 擴充超出 special 已核對範圍")
    bits = core["uboot"]["address_bits"]
    footprints = [core["ram"]["kernel_work"]] + [uboot._slot(core["files"][role]) for role in ("initrd", "dtb")]
    for role, item in firmware.items():
        uboot._integer(item["capacity"], item["bytes"] + 1, special.MAX_FILE + 1, "韌體載入容量")
        area = uboot._span(uboot._slot(item), bits)
        require(any(uboot._contains(bank, area) for bank in core["ram"]["banks"]), "韌體容量超出 RAM")
        require(not any(uboot._overlap(area, span) for span in core["ram"]["reserved"]), "韌體撞到 U-Boot 核定保護區")
        footprints.append(area)
    uboot._disjoint(footprints, "含容量的原配載荷")
    dtb = loads["dtb"]
    firmware_areas = {"opencva": {"start": 0xa8000000, "size": 0x7cff000},
                      "codec": {"start": 0xafd00000, "size": 0x300000}}
    for role, item in firmware.items():
        require(uboot._contains(firmware_areas[role], uboot._slot(item)), "韌體容量越過原配 DT 專屬區")
    for area in dtb["reservations"]:
        require(not any(uboot._overlap(area, span) for span in footprints[:3]), "DT 保留區與核心、initrd 或 DTB 容量重疊")
        for role, item in firmware.items():
            require(not uboot._overlap(area, uboot._slot(item)) or area == firmware_areas[role], "韌體與其他 DT 保留區重疊")
    if config["board"] == "bpi-m6":
        cma = {"start": 1509949440, "size": 343932928}
        require(not any(uboot._overlap(cma, span) for span in footprints), "載入容量撞到 M6 原配 CMA")
    q = _json(_reference(config["qualification"]))
    _fields(q, "schema scope_sha256 board hardware_id approved hardware_validated source_evidence memory_evidence "
            "dependencies required_commands firmware_loads_approved kernel_may_write_root", "實板資格")
    require(q["schema"] == QUALIFICATION_SCHEMA and q["scope_sha256"] == scope_digest(config)
            and q["board"] == config["board"] and q["hardware_id"] == pairing["hardware_id"]
            and q["approved"] is True and q["hardware_validated"] is True
            and q["kernel_may_write_root"] is True, "缺少綁定本次配置、板型、實板與根寫入風險的核定")
    require(type(q["firmware_loads_approved"]) is bool and (not firmware or q["firmware_loads_approved"]), "額外韌體載入未核定")
    require(type(q["source_evidence"]) is list and 1 <= len(q["source_evidence"]) <= 32, "實板資格缺少原始證據")
    for ref in q["source_evidence"]:
        _reference(ref, 16 * 1024**2)
    _fields(q["dependencies"], " ".join(DEPENDENCIES), "執行器相依摘要")
    for name in DEPENDENCIES:
        _reference({"path": str(Path(__file__).resolve().parent / name), "sha256": q["dependencies"][name]}, 2 * 1024**2)
    context = {"config": config, "core": core, "firmware": firmware, "loads": loads,
               "cid": cid, "pairing": pairing, "recipe": recipe, "memreserve": _memreserve(blobs["dtb"])}
    memory = _reference(q["memory_evidence"], 65536)
    context["initial_lmb"] = _memory(memory, context)
    commands = required_commands(config["board"])
    require(q["required_commands"] == commands, "實板核定未涵蓋完整必要命令")
    for step in _steps(context):
        size = len(step["command"]) + 1 if step["check"] == "kernel-marker" else len(uboot._wire(step["command"], "0" * 16))
        require(size <= core["uboot"]["line_limit"], "命令超出已核定 UART 行長")
    return context


VENDOR_FILES = ("common/cmd_boot.c", "common/cmd_bootm.c", "common/cmd_bdinfo.c", "common/cmd_fat.c",
                "fs/fs.c", "include/fs.h", "include/mmc.h", "include/part.h", "include/malloc.h",
                "include/asm-generic/global_data.h", "include/u-boot/sha256.h")
VENDOR_PINNED = {
    "bpi-w2": {
        "arch/arm/include/asm/arch-rtd1295/rbus/nand_reg.h": "9005a4621226927945a7eb7d3209061daac89cb9155cd715a073edc90566b6db",
        "arch/arm/include/asm/arch-rtd1295/rbus/crt_reg.h": "d311249eeac8eeb1d3c702c930197657d6e06619b31f0e4c02a027aa21d3b760",
        "arch/arm/include/asm/arch-rtd1295/system.h": "4784f39863a50fc3f033be023105cb4e91dab63800ca68a6c8e0a79057422b02",
        "drivers/mtd/nand/rtk_nand.c": "09c436cc7bbda454671c66b1280a11a4e75e6d5d6b7f22df13cd5ad327db918e",
    },
    "bpi-m4": {
        "arch/arm/include/asm/arch-rtd1395/rbus/nand_reg.h": "9005a4621226927945a7eb7d3209061daac89cb9155cd715a073edc90566b6db",
        "arch/arm/include/asm/arch-rtd1395/rbus/crt_reg.h": "d311249eeac8eeb1d3c702c930197657d6e06619b31f0e4c02a027aa21d3b760",
        "arch/arm/include/asm/arch-rtd1395/system.h": "b708f07faac88bc8ecfffda38fb39ae32214481383417940d40a98c32ff19232",
        "drivers/mtd/nand/rtk_nand.c": "09611b79b227b37c496f38cee27d60fec4b8d0236c5ef4850e4602eef27a587a",
    },
}


def vendor_files(board):
    require(board in VENDOR_BOARDS, "沒有此板的原廠來源契約")
    return VENDOR_FILES + tuple(VENDOR_PINNED[board])


def bootconfig(artifact_root, *, template, kernel_placement="original"):
    """direct-final 僅供新 lab ABI；原配範本仍原樣核對，不豁免原入口的保留區。"""
    require(kernel_placement in ("original", "direct-final"), "未知核心載入模式")
    if kernel_placement == "original":
        return special.bootconfig(artifact_root, template=template)
    m = special.validate(artifact_root)
    require(m["board"] in VENDOR_BOARDS, "direct-final 僅支援已讀 Realtek booti_setup")
    t = copy.deepcopy(template)
    _fields(t, "board kernel_release source ram addresses bindings vendor", "原廠直接載入範本")
    require(t["board"] == m["board"] and t["kernel_release"] == m["kernel_release"], "範本板型或核心版本錯配")
    components, vendor, relocation = special._realtek_recipe(m, t)
    ram = t["ram"]
    _fields(ram, "banks reserved kernel_work", "原廠 RAM")
    for kind in ("banks", "reserved"):
        require(type(ram[kind]) is list and 1 <= len(ram[kind]) <= 64, "RAM bank 與保護區須明示")
        for span in ram[kind]:
            uboot._span(span, 32)
        uboot._disjoint(ram[kind], "原廠 RAM " + kind)
    work = uboot._span(ram["kernel_work"], 32)
    require(any(uboot._contains(bank, work) for bank in ram["banks"]), "核心工作區越界")
    dtb = components["dtb"]
    require(not dtb["dynamic_cma"], "動態 CMA 尚未核定為具體保護範圍")
    protected = ram["reserved"] + dtb["reservations"] + [
        {"start": 0x2000, "size": 0x1000},
        {"start": 0x2f000 if m["board"] == "bpi-m4" else 0x1f000, "size": 0x1000}]
    require(not any(uboot._overlap(work, span) for span in protected + dtb["vendor_reservations"]),
            "直接載入核心工作區撞到保護區")
    loads, spans = [], [work]
    for role, item in components.items():
        address = relocation["start"] if role == "kernel" else uboot._address(t["addresses"][role], 32)
        size = max(item["bytes"], item["image_size"]) if role == "kernel" else item["bytes"] + (65536 if role == "dtb" else 0)
        span = uboot._span({"start": address, "size": size}, 32)
        require(role != "dtb" or address % 8 == 0, "DTB 載入位址未對齊")
        require(any(uboot._contains(bank, span) for bank in ram["banks"]), "載荷超出 RAM：" + role)
        require(not any(uboot._overlap(span, area) for area in protected), "直接載入撞到保護區：" + role)
        for area in dtb["vendor_reservations"]:
            require(not uboot._overlap(span, area) or role == "audio" and area["role"] == "audio"
                    and uboot._contains(area, span), "直接載入越過 DT 專屬保留區")
        if role == "kernel":
            require(uboot._contains(work, span), "直接載入及 Image 記憶體範圍超出核心工作區")
        else:
            spans.append(span)
        loads.append({"role": role, "address": address, **item})
    uboot._disjoint(spans, "直接載入載荷與核心工作區")
    args = " ".join(m["bootargs_template"])
    names = set(re.findall(r"\$\{([a-z0-9_]+)\}", args))
    require(type(t["bindings"]) is dict and set(t["bindings"]) == names, "原廠環境綁定缺失或多餘")
    for name, value in t["bindings"].items():
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", value), "環境綁定含指令或無效字元")
        require(name != "board" or value == m["board"], "板型綁定不符")
        require(name != "sdmmc_on" or value in ("0", "1"), "sdmmc_on 須來自原配環境")
        args = args.replace("${" + name + "}", value)
    return {"schema": "bpi-lab-special-bootconfig-v1", "board": m["board"], "kernel_release": m["kernel_release"],
            "source": t["source"], "ram": ram, "loads": loads, "bootargs": args.split(),
            "boot_command": m["boot_command"], "root_uuid": m["root_uuid"], "vendor": vendor,
            "kernel_relocation": relocation, "kernel_placement": kernel_placement,
            "original_kernel_staging": t["addresses"]["kernel"], "original_staging_used": False,
            "status": "validated_offline", "hardware_validated": False, "execution_ready": False,
            "qualification_blockers": m["qualification_blockers"]}


def _vendor_memory(output, context):
    # 原 bdinfo 隱藏 Realtek relocation／stack；lab memory 直接輸出 gd 與 heap 原始值。
    core = context["core"]
    uboot._memory(output, core)
    for key in ("relocaddr", "sp start", "irq_sp", "TLB addr"):
        uboot._one(re.escape(key) + r"\s*=\s*(0x[0-9a-fA-F]+)", output, "原廠 bdinfo " + key)
    values = {key: int(uboot._one(key + r"\s*=\s*(0x[0-9a-fA-F]+)", output, key)[1], 16)
              for key in ("relocaddr", "monitor_size", "malloc_start", "malloc_end")}
    require(values["malloc_end"] > values["malloc_start"], "原廠即時 heap 範圍無效")
    for area in ({"start": values["relocaddr"], "size": values["monitor_size"]},
                 {"start": values["malloc_start"], "size": values["malloc_end"] - values["malloc_start"]}):
        uboot._span(area, 64)
        require(any(uboot._contains(span, area) for span in core["ram"]["reserved"]), "原廠完整 monitor／heap 未受核定區間保護")
    for item in core["files"].values():
        require(not any(uboot._overlap(uboot._slot(item), span) for span in core["ram"]["reserved"]), "原廠載入容量與核定工作記憶體重疊")


def _vendor_context(config, *, qualified=True):
    _fields(config, "schema board artifact_root template execution pairing qualification", "原廠執行器配置")
    require(config["schema"] == SCHEMA and config["board"] in VENDOR_BOARDS, "原廠配置板型或 schema 不符")
    require(Path(config["artifact_root"]).is_absolute(), "組件根目錄須為絕對路徑")
    placement = config["execution"].get("kernel_placement", "original")
    recipe = bootconfig(config["artifact_root"], template=config["template"], kernel_placement=placement)
    require(recipe["board"] == config["board"] and recipe.get("vendor") is not None, "原廠配方錯配")
    ex = config["execution"]
    _fields(ex, "uboot boot_region capacities kernel_entry fdt_extra transport vendor_sources" +
            (" kernel_placement" if "kernel_placement" in ex else ""), "原廠執行細節")
    _fields(ex["uboot"], "prompt version address_bits line_limit abi", "原廠 UART")
    require(ex["uboot"]["abi"] == VENDOR_ABI and ex["uboot"]["address_bits"] == 64, "需要另行建置及核定的 realtek-lab-v1，不是原廠既有命令宣告")
    require(re.fullmatch(r"[\x20-\x7e]{1,64}", ex["uboot"]["prompt"]) and ex["uboot"]["prompt"].strip()
            and re.fullmatch(r"U-Boot [\x20-\x7e]{1,200}", ex["uboot"]["version"]), "原廠版本或提示格式不符")
    uboot._integer(ex["uboot"]["line_limit"], 256, 640, "原廠行長")
    require(type(ex["fdt_extra"]) is int and 4096 <= ex["fdt_extra"] <= 65536, "原廠 FDT 容量無效")
    pairing = _json(_reference(config["pairing"]))
    require(pairing.get("schema") == "bpi-lab-pairing-v1" and pairing.get("approved") is True
            and isinstance(pairing.get("hardware_id"), str), "缺少核定實板配對")
    uart = pairing.get("uart", {})
    require(isinstance(uart.get("stable_path"), str) and re.fullmatch(r"/dev/serial/by-(?:id|path)/[A-Za-z0-9_:+.-]+", uart["stable_path"])
            and uart["stable_path"] == pairing.get("resources", {}).get("uart")
            and type(uart.get("baud")) is int and 9600 <= uart["baud"] <= 4000000, "原廠 UART 配對無效")
    loads, paths, cid = _transport(config, recipe, pairing)
    require(set(loads) == {"kernel", "initrd", "dtb", "audio"}, "原廠載荷集合不符")
    _fields(ex["capacities"], "kernel initrd dtb audio", "原廠容量")
    core = {"schema": SCHEMA, "arch": "arm64", "uboot": {**ex["uboot"], "pairing_sha256": config["pairing"]["sha256"]},
            "ram": {**recipe["ram"], "boot": ex["boot_region"]},
            "source": {"type": "mmc", "device": 0, "partition": 1, "partuuid": recipe["source"]["partuuid"]},
            "kernel_release": recipe["kernel_release"], "files": {}, "bootargs": recipe["bootargs"], "fdt_extra": ex["fdt_extra"]}
    uboot._span(ex["boot_region"], 32)
    require(any(uboot._contains(bank, ex["boot_region"]) for bank in recipe["ram"]["banks"]), "原廠 boot 範圍超出 DRAM")
    footprints = [recipe["ram"]["kernel_work"]]
    for role, item in loads.items():
        record, _ = uboot._read_regular(Path(config["artifact_root"]) / item["path"], special.MAX_FILE)
        require(record == {key: item[key] for key in ("bytes", "sha256")}, "原廠載荷實際摘要錯配")
        capacity = ex["capacities"][role]
        uboot._integer(capacity, item["bytes"] + 1, special.MAX_FILE + 65536, "原廠載入容量")
        slot = {"start": item["address"], "size": capacity}
        uboot._span(slot, 32)
        require(uboot._contains(ex["boot_region"], slot), "原廠載荷容量不在 boot 範圍")
        require(not any(uboot._overlap(slot, span) for span in recipe["ram"]["reserved"] + loads["dtb"]["reservations"]), "原廠載荷容量撞到保護區")
        for area in loads["dtb"]["vendor_reservations"]:
            require(not uboot._overlap(slot, area) or role == "audio" and area["role"] == "audio" and uboot._contains(area, slot), "原廠載入容量越過 DT 專屬保留區")
        if role == "kernel" and placement == "direct-final":
            require(uboot._contains(recipe["ram"]["kernel_work"], slot), "直接載入含超長偵測容量超出核心工作區")
        else:
            footprints.append(slot)
        core["files"][role] = {**{key: item[key] for key in ("bytes", "sha256", "address", "format")}, "capacity": capacity, "path": paths[role]}
    uboot._disjoint(footprints, "原廠含容量載荷與搬移目的")
    require(uboot._contains(ex["boot_region"], recipe["ram"]["kernel_work"])
            and ex["kernel_entry"] == recipe["kernel_relocation"]["start"]
            and ex["capacities"]["kernel"] >= loads["kernel"]["image_size"]
            and ex["capacities"]["dtb"] >= loads["dtb"]["bytes"] + 65536, "原廠搬移讀取區、入口或 DTB 擴充容量不符")
    require(all(isinstance(arg, str) and re.fullmatch(r"[A-Za-z0-9_./,:=+@%-]{1,256}", arg) for arg in core["bootargs"]), "原廠 bootargs 含未核定語法")
    _fields(ex["vendor_sources"], " ".join(vendor_files(config["board"])), "原廠來源")
    for name, ref in ex["vendor_sources"].items():
        _reference(ref, 2 * 1024**2)
        expected = VENDOR_PINNED[config["board"]].get(name) or special.REALTEK_SOURCES[config["board"]]["inspected_local_sha256"].get("u-boot-rtk/" + name)
        require(expected is None or ref["sha256"] == expected, "原廠引導來源偏離已核對版本")
    context = {"config": config, "core": core, "firmware": {"audio": core["files"]["audio"]}, "recipe": recipe,
               "loads": loads, "cid": cid, "pairing": pairing, "vendor_lab": True}
    context["lab_source"] = _vendor_source_code(context)
    if qualified:
        q = _json(_reference(config["qualification"]))
        _fields(q, "schema scope_sha256 board hardware_id approved hardware_validated source_evidence memory_evidence "
                "dependencies required_commands firmware_loads_approved kernel_may_write_root root_label_scope_approved vendor_build", "原廠實板資格")
        require(q["schema"] == QUALIFICATION_SCHEMA and q["scope_sha256"] == scope_digest(config)
                and q["board"] == config["board"] and q["hardware_id"] == pairing["hardware_id"]
                and all(q[key] is True for key in ("approved", "hardware_validated", "firmware_loads_approved", "kernel_may_write_root", "root_label_scope_approved")), "原廠一次性交接缺少完整實板核定與可見根標籤範圍核定")
        require(type(q["source_evidence"]) is list and 1 <= len(q["source_evidence"]) <= 32, "原廠核定缺少原始證據")
        for ref in q["source_evidence"]:
            _reference(ref, 16 * 1024**2)
        _fields(q["dependencies"], " ".join(DEPENDENCIES), "原廠執行相依摘要")
        for name in DEPENDENCIES:
            _reference({"path": str(Path(__file__).resolve().parent / name), "sha256": q["dependencies"][name]}, 2 * 1024**2)
        _vendor_memory(_reference(q["memory_evidence"], 65536), context)
        build = q["vendor_build"]
        _fields(build, "source_sha256 binary build_config link_map", "原廠 lab 建置核定")
        require(build["source_sha256"] == _sha(context["lab_source"].encode()), "原廠 lab 命令來源與核定不符")
        for key in ("binary", "build_config", "link_map"):
            _reference(build[key], 64 * 1024**2)
        require(q["required_commands"] == required_commands(config["board"]), "原廠命令核定不完整")
    for step in _vendor_steps(context):
        length = len(step["command"]) + 1 if step["check"] == "vendor-kernel" else len(uboot._wire(step["command"], "0" * 16))
        require(length <= ex["uboot"]["line_limit"], "原廠 UART 命令超出核定行長")
    return context


def _vendor_source_code(context):
    """產生可附加於已核對 cmd_boot.c 的 lab 命令；不寫入或修補 BSP。"""
    c = context["config"]
    board = c["board"]
    records = []
    for role, item in context["core"]["files"].items():
        checksum = ",".join("0x" + item["sha256"][index:index + 2] for index in range(0, 64, 2))
        records.append(f'    {{"{role}", "{item["path"]}", 0x{item["address"]:x}UL, 0x{item["bytes"]:x}UL, {{{checksum}}}}}')
    spans = ",\n".join(f'    {{0x{span["start"]:x}UL, 0x{span["size"]:x}UL}}' for span in context["core"]["ram"]["reserved"])
    banks = "\n".join(f'    if (gd->bd->bi_dram[{index}].start != 0x{span["start"]:x}UL || '
                        f'gd->bd->bi_dram[{index}].size != 0x{span["size"]:x}UL) return 1;'
                        for index, span in enumerate(context["core"]["ram"]["banks"]))
    environment = {"kernel_loadaddr": f'{context["loads"]["kernel"]["address"]:x}',
                   "rootfs_loadaddr": f'{context["loads"]["initrd"]["address"]:x}',
                   "fdt_loadaddr": f'{context["loads"]["dtb"]["address"]:x}',
                   "audio_loadaddr": f'{context["loads"]["audio"]["address"]:x}',
                   "fdt_high": "ffffffffffffffff", "initrd_high": "ffffffffffffffff",
                   "bootargs": " ".join(context["core"]["bootargs"])}
    settings = "\n".join(f'    if (setenv("{key}", "{value}")) return CMD_RET_FAILURE;' for key, value in environment.items())
    tail = ('    return do_go_all_fw();' if board == "bpi-m4" else
            '    if (do_go_audio_fw()) return CMD_RET_FAILURE;\n'
            '    boot_mode = BOOT_RESCUE_MODE;\n'
            '#ifdef CONFIG_WAIT_AFW_1_SECOND\n    mdelay(1000);\n#endif\n'
            '    return rtk_call_booti();')
    source_ids = "\n".join("/* " + name + " SHA256 " + ref["sha256"] + " */" for name, ref in c["execution"]["vendor_sources"].items())
    return '''/* 僅附加到核定原廠 cmd_boot.c；不得當作任意位址 RAM stub 或直接燒錄。 */
#include <mmc.h>
#include <part.h>
#include <fs.h>
#include <malloc.h>
#include <u-boot/sha256.h>
#if !defined(CONFIG_SHA256) || !defined(CONFIG_PARTITION_UUIDS) || !defined(CONFIG_GENERIC_MMC)
#error "lab 命令需要 SHA256、分割區 UUID 及通用 MMC"
#endif
#if defined(CONFIG_RTK_ARM32) || defined(CONFIG_CPU_V7) || defined(CONFIG_SYS_RTK_NAND_FLASH)
#error "lab 命令只核對 ARM64 eMMC 分支"
#endif
#if OTP_REG_BASE != 0x98017000 || OTP_BIT_SECUREBOOT != 3494 || CLOCK_ENABLE2_reg != 0x98000010
#error "lab 唯讀安全位元與 ACPU 時鐘來源錯配"
#endif
#if defined(CONFIG_ARM64_IMAGE_LEGACY)
#error "lab 載荷不是舊版 ARM64 Image 標頭"
#endif
''' + f'#if !defined({"CONFIG_RTD1395" if board == "bpi-m4" else "CONFIG_RTD1295"})\n#error "lab 板型錯配"\n#endif\n' + f'#if CONFIG_NR_DRAM_BANKS < {len(context["core"]["ram"]["banks"])}\n#error "lab DRAM bank 數不足"\n#endif\n' + source_ids + '''
struct bpi_lab_payload { const char *role, *path; ulong address, size; unsigned char digest[32]; };
static const struct bpi_lab_payload bpi_lab_payloads[] = {
''' + ",\n".join(records) + '''
};
static const ulong bpi_lab_reserved[][2] = {
''' + spans + '''
};
static int bpi_lab_used;
static unsigned int bpi_lab_loaded;
static int bpi_lab_protected(ulong address, ulong size)
{
    unsigned int i;
    if (!size || address + size < address) return 0;
    for (i = 0; i < ARRAY_SIZE(bpi_lab_reserved); ++i)
        if (address >= bpi_lab_reserved[i][0] &&
            address + size <= bpi_lab_reserved[i][0] + bpi_lab_reserved[i][1]) return 1;
    return 0;
}
static int bpi_lab_probe(void)
{
    struct mmc *mmc = find_mmc_device(0);
    disk_partition_t part;
    /* 原廠 secure helper 可被編譯成常數；直接唯讀檢查同源 OTP 位元，不啟用燒錄命令。 */
    unsigned int i, secure = (rtd_inl(OTP_REG_BASE + (OTP_BIT_SECUREBOOT / 32) * 4) >> (OTP_BIT_SECUREBOOT % 32)) & 1;
    if (bpi_lab_used || secure != NONE_SECURE_BOOT || audio_fw_state || ipc_ir_set) return 1;
    if (rtd_inl(CLOCK_ENABLE2_reg) & _BIT4) return 1;
    if (!mmc || mmc_init(mmc) || IS_SD(mmc) || mmc->part_num != 0) return 1;
    if (get_partition_info(&mmc->block_dev, 1, &part)) return 1;
''' + "\n".join(f"    if (mmc->cid[{i}] != 0x{context['cid'][i * 8:(i + 1) * 8]}U) return 1;" for i in range(4)) + f'''
    if (strcmp(part.uuid, "{context['core']['source']['partuuid']}")) return 1;
''' + banks + f'''
    for (i = {len(context['core']['ram']['banks'])}; i < CONFIG_NR_DRAM_BANKS; ++i)
        if (gd->bd->bi_dram[i].size) return 1;
    if (!bpi_lab_protected(gd->relocaddr, gd->mon_len) || !bpi_lab_protected(gd->start_addr_sp, 1) ||
        (gd->irq_sp && !bpi_lab_protected(gd->irq_sp, 1)) ||
        (gd->arch.tlb_addr && !bpi_lab_protected(gd->arch.tlb_addr, 1))) return 1;
    if (mem_malloc_end <= mem_malloc_start || !bpi_lab_protected(mem_malloc_start, mem_malloc_end - mem_malloc_start)) return 1;
    if ((gd->fdt_blob && !bpi_lab_protected((ulong)gd->fdt_blob, gd->fdt_size)) ||
        (gd->new_fdt && !bpi_lab_protected((ulong)gd->new_fdt, gd->fdt_size))) return 1;
    if (ipc_shm.audio_fw_entry_pt && ipc_shm.audio_fw_entry_pt != SWAPEND32(0x{context['loads']['audio']['address']:x}U | MIPS_KSEG0BASE)) return 1;
    printf("BPI_LAB_V1 {scope_digest(c)}\\n");
    printf("BPI_LAB_STATE %u %u\\n", secure, (unsigned int)audio_fw_state);
    for (i = 0; i < 4; ++i) printf("CID[%u]: 0x%08x\\n", i, mmc->cid[i]);
    printf("BPI_LAB_PART %s\\n", part.uuid);
    return 0;
}}
''' + '''static void bpi_lab_memory(void)
{
    unsigned int i;
    for (i = 0; i < CONFIG_NR_DRAM_BANKS; ++i) {
        if (!gd->bd->bi_dram[i].size) continue;
        printf("DRAM bank = 0x%x\\n-> start = 0x%lx\\n-> size = 0x%lx\\n", i,
               (ulong)gd->bd->bi_dram[i].start, (ulong)gd->bd->bi_dram[i].size);
    }
    printf("relocaddr = 0x%lx\\nsp start = 0x%lx\\nirq_sp = 0x%lx\\nTLB addr = 0x%lx\\n",
           gd->relocaddr, gd->start_addr_sp, gd->irq_sp, gd->arch.tlb_addr);
    printf("monitor_size = 0x%lx\\nmalloc_start = 0x%lx\\nmalloc_end = 0x%lx\\n",
           gd->mon_len, mem_malloc_start, mem_malloc_end);
    printf("fdt_blob = 0x%lx\\nnew_fdt = 0x%lx\\nfdt_size = 0x%lx\\n",
           (ulong)gd->fdt_blob, (ulong)gd->new_fdt, gd->fdt_size);
}
static int bpi_lab_load(unsigned int index)
{
    loff_t received = 0;
    const struct bpi_lab_payload *p = &bpi_lab_payloads[index];
    bpi_lab_loaded &= ~(1U << index);
    if (bpi_lab_probe() || fs_set_blk_dev("mmc", "0:1", FS_TYPE_FAT)) return 1;
    if (fs_read(p->path, p->address, 0, p->size + 1, &received) < 0 || received < 0 || (unsigned long long)received != p->size) return 1;
    printf("%llu bytes read\\n", (unsigned long long)received);
    if (setenv_hex("filesize", received)) return 1;
    bpi_lab_loaded |= 1U << index;
    return 0;
}
static int bpi_lab_hash(unsigned int index)
{
    unsigned int j;
    unsigned char actual[32];
    const struct bpi_lab_payload *p = &bpi_lab_payloads[index];
    if (!(bpi_lab_loaded & (1U << index))) return 1;
    sha256_csum_wd((const unsigned char *)p->address, p->size, actual, CHUNKSZ_SHA256);
    printf("sha256 for %08lx ... %08lx ==> ", p->address, p->address + p->size - 1);
    for (j = 0; j < 32; ++j) printf("%02x", actual[j]);
    printf("\\n");
    return memcmp(actual, p->digest, sizeof(actual)) != 0;
}
static int do_bpilab(cmd_tbl_t *cmdtp, int flag, int argc, char * const argv[])
{
    unsigned int i;
    if (argc < 2 || argc > 3 || bpi_lab_used) return CMD_RET_FAILURE;
    if (argc == 2 && !strcmp(argv[1], "memory")) { bpi_lab_memory(); return 0; }
    if (argc == 2 && !strcmp(argv[1], "probe")) return bpi_lab_probe();
    if (argc == 3 && (!strcmp(argv[1], "hash") || !strcmp(argv[1], "load"))) {
        for (i = 0; i < ARRAY_SIZE(bpi_lab_payloads); ++i)
            if (!strcmp(argv[2], bpi_lab_payloads[i].role))
                return !strcmp(argv[1], "load") ? bpi_lab_load(i) : bpi_lab_hash(i);
        return CMD_RET_USAGE;
    }
    if (argc != 2 || strcmp(argv[1], "boot") || bpi_lab_probe()) return CMD_RET_FAILURE;
    if (bpi_lab_loaded != (1U << ARRAY_SIZE(bpi_lab_payloads)) - 1) return CMD_RET_FAILURE;
    /* 最後一次完整 RAM 摘要檢查在原廠交接前；任何錯誤均不啟動 ACPU。 */
    for (i = 0; i < ARRAY_SIZE(bpi_lab_payloads); ++i)
        if (bpi_lab_hash(i)) return CMD_RET_FAILURE;
''' + settings + '''
    if (setenv("hyp_loadaddr", NULL)) return CMD_RET_FAILURE;
    bpi_lab_used = 1;
''' + tail + '''
}
U_BOOT_CMD(bpilab, 3, 0, do_bpilab, "核定原配 RAM 載荷的一次性交接", "memory | probe | load <role> | hash <role> | boot");
'''


def vendor_source(config):
    """僅產生 lab C 來源，不要求已有部署資格，也不修改來源樹或操作硬體。"""
    context = _vendor_context(copy.deepcopy(config), qualified=False)
    code = context["lab_source"]
    return {"schema": "bpi-lab-realtek-source-v1", "board": config["board"], "source": code,
            "sha256": _sha(code.encode()), "append_to": "common/cmd_boot.c", "hardware_validated": False,
            "deployment_verified": False, "original_entry": context["recipe"]["boot_command"],
            "kernel_placement": config["execution"].get("kernel_placement", "original"),
            "original_staging_used": config["execution"].get("kernel_placement", "original") == "original"}


def _vendor_steps(context):
    core = context["core"]
    steps = [{"command": "version", "check": "version"}, {"command": "bpilab memory", "check": "vendor-memory"}]
    steps += [{"command": "help " + command, "check": "status"} for command in ("bpilab", "setenv", "printenv")]
    steps.append({"command": "bpilab probe", "check": "vendor-probe"})
    steps += [{"command": "setenv autostart no", "check": "status"},
              {"command": "printenv autostart", "check": "env", "variable": "autostart", "value": "no"}]
    for role, item in core["files"].items():
        steps += [{"command": "setenv filesize", "check": "status"},
                  {"command": "bpilab load " + role, "check": "length", "component": role},
                  {"command": "printenv filesize", "check": "filesize", "component": role}]
    steps += [{"command": "bpilab hash " + role, "check": "sha256", "component": role} for role in core["files"]]
    steps += [{"command": "bpilab memory", "check": "vendor-memory"}, {"command": "bpilab probe", "check": "vendor-probe"},
              {"command": "bpilab boot", "check": "vendor-kernel"}]
    return steps


def _vendor_probe(output, context):
    lines = [line for line in uboot._lines(output) if line.strip()]
    require(len(lines) == 7 and lines[0] == "BPI_LAB_V1 " + scope_digest(context["config"])
            and lines[1] == "BPI_LAB_STATE 0 0"
            and lines[-1] == "BPI_LAB_PART " + context["core"]["source"]["partuuid"], "原廠 lab ABI、即時安全／音訊狀態或分割區錯配")
    for index in range(4):
        require(lines[index + 2] == f"CID[{index}]: 0x{context['cid'][index * 8:(index + 1) * 8]}", "原廠即時 CID 與配對不同")


def required_commands(board):
    if board in VENDOR_BOARDS:
        return ["version", "help", "echo", "setenv", "printenv",
                "bpilab memory", "bpilab probe", "bpilab load", "bpilab hash", "bpilab boot"]
    require(board in SUPPORTED, "板型沒有可執行命令契約")
    return ["version", "bdinfo", "help", "echo", "setenv", "printenv", "hash sha256", "base", "md.b",
            "mmc dev", "mmc reg read cid", "part uuid", "load", "fdt addr", "fdt list", "fdt rsvmem print",
            "fdt resize", "bootm" if board in ("bpi-f2p", "bpi-f2s") else "booti"]


def validate_config(config):
    """每次重新核對持久證據與原配配方，不接受 execution_ready 之類宣告捷徑。"""
    return _context(config)["config"]


def build_config(artifact_root, *, template, execution, pairing, qualification):
    return validate_config({"schema": SCHEMA, "board": template["board"],
                            "artifact_root": str(Path(artifact_root).absolute()), "template": template,
                            "execution": execution, "pairing": pairing, "qualification": qualification})


def validate_artifacts(config, artifact_root=None):
    context = _context(config)
    if artifact_root is not None:
        require(str(Path(artifact_root).absolute()) == context["config"]["artifact_root"], "外部組件根目錄不符")
    return {"status": "validated_offline", "artifacts_verified": True, "hardware_verified": False,
            "hardware_validated": False, "blockers": [],
            "roles": sorted(context["loads"]), "uart_payloads_verified": False}


def lifecycle_view(config):
    """僅供既有 lifecycle 身分核對；真正執行必須分派本模組 boot，不能丟棄韌體。"""
    core = _context(config)["core"]
    core["uboot"]["qualification_sha256"] = config["qualification"]["sha256"]
    return core


def backend_binding(config):
    context = _context(config)
    context["core"]["uboot"]["qualification_sha256"] = config["qualification"]["sha256"]
    return {"driver": SCHEMA, "config": context["config"], "lifecycle_view": context["core"],
            "pairing_sha256": context["config"]["pairing"]["sha256"], "firmware_roles": sorted(context["firmware"]),
            "hardware_validated": False, "requires_runtime_dispatch": True}


def _steps(context):
    if context.get("vendor_lab"):
        return _vendor_steps(context)
    core, firmware = context["core"], context["firmware"]
    steps = []
    for step in uboot._steps(core):
        step = copy.deepcopy(step)
        if step["command"] == "bdinfo":
            step["check"] = "special-memory"
        if step["command"].startswith("mmc dev "):
            step["command"] += " 0"
        if step["command"] == "help part":
            steps.append({"command": "help mmc", "check": "status"})
        # 核心載入之前取得實際 CID；PARTUUID 核對仍沿用原 runner。
        if step["check"] == "partuuid":
            for offset in range(4):
                steps.append({"command": f"mmc reg read cid {offset}", "check": "cid", "offset": offset})
        # 所有載入完成後才驗證全部內容；額外載入不得破壞先前雜湊。
        if step["check"] == "sha256" and step.get("component") == "kernel":
            for role, item in firmware.items():
                steps.extend([
                    {"command": "setenv filesize", "check": "status"},
                    {"command": f"load mmc {core['source']['device']:x}:{core['source']['partition']:x} "
                                f"{item['address']:x} {item['path']} {item['bytes'] + 1:x} 0",
                     "check": "length", "component": role},
                    {"command": "printenv filesize", "check": "filesize", "component": role},
                ])
            steps.append({"command": "bdinfo", "check": "special-memory"})
            for role, item in firmware.items():
                steps.append({"command": f"hash sha256 {item['address']:x} {item['bytes']:x}", "check": "sha256", "component": role})
        if step["check"] == "dtb-reserved":
            step["check"] = "special-dtb-reserved"
        if step["check"] == "kernel-marker":
            # FDT 擴充後的原始摘要不再適用；其餘載荷在最後交接前再次核對。
            steps.append({"command": "bdinfo", "check": "special-memory"})
            steps.append({"command": f"part uuid mmc {core['source']['device']:x}:{core['source']['partition']:x}", "check": "partuuid"})
            for offset in range(4):
                steps.append({"command": f"mmc reg read cid {offset}", "check": "cid", "offset": offset})
            for role, item in {**core["files"], **firmware}.items():
                if role != "dtb":
                    steps.append({"command": f"hash sha256 {item['address']:x} {item['bytes']:x}", "check": "sha256", "component": role})
        steps.append(step)
    return steps


class _Runner(uboot._Runner):
    def __init__(self, console, context, records, timeout, monotonic):
        core = copy.deepcopy(context["core"])
        core["files"].update(context["firmware"])
        super().__init__(console, core, records, timeout, monotonic)
        self.context = context

    def execute(self, step):
        if step["check"] == "vendor-kernel":
            return self.vendor_kernel(step)
        custom = step["check"] in ("cid", "special-memory", "special-dtb-reserved", "vendor-probe", "vendor-memory")
        wire_step = {**step, "check": "status"} if custom else step
        try:
            super().execute(wire_step)
            if not custom:
                if step["check"] == "length" and not self.context.get("vendor_lab"):
                    loaded = self.context.setdefault("loaded", [])
                    require(step["component"] not in loaded, "同次執行不得重載已核對組件")
                    loaded.append(step["component"])
                return
            record = self.records[-1]
            record["check"] = step["check"]
            raw = record["output"].encode("ascii", errors="strict")
            if step["check"] == "vendor-probe":
                _vendor_probe(raw, self.context)
            elif step["check"] == "vendor-memory":
                _vendor_memory(raw, self.context)
            elif step["check"] == "cid":
                index = step["offset"]
                value = uboot._one(r"CID\[" + str(index) + r"\]: 0x([0-9a-fA-F]{8})", raw, "MMC CID")[1]
                require(value.lower() == self.context["cid"][index * 8:(index + 1) * 8], "實際 MMC CID 與配對不同")
            elif step["check"] == "special-memory":
                _memory(raw, self.context)
            else:
                lines = [line for line in uboot._lines(raw) if line.strip()]
                require(len(lines) >= 2 and re.fullmatch(r"index\s+start\s+size", lines[0])
                        and re.fullmatch(r"-{8,}", lines[1]), "DTB 保留表標頭無效")
                spans = []
                for index, line in enumerate(lines[2:]):
                    match = re.fullmatch(r"\s*([0-9a-f]+)\s+([0-9a-f]{16})\s+([0-9a-f]{16})", line)
                    require(match is not None and int(match[1], 16) == index, "DTB 保留表索引無效")
                    spans.append({"start": int(match[2], 16), "size": int(match[3], 16)})
                require(spans == self.context["memreserve"], "UART DTB 保留表與實際原檔不符")
        except (ValueError, OSError, TimeoutError) as exc:
            if self.records:
                self.records[-1].update(status="failed", reason=str(exc) if isinstance(exc, uboot.UBootError) else "UART 核對失敗或逾時")
            raise

    def vendor_kernel(self, step):
        record = {**step, "status": "started"}
        self.records.append(record)
        try:
            self.send(step["command"] + "\n")
            found = self.expect(rb"(?:^|\r?\n)(?:\[\s*[0-9.]+\]\s*)?Linux version ([^\s]+)[^\r\n]*\r?\n"
                                rb"|(?:^|\r?\n)" + self.prompt + rb"$")
            record["output"] = found.before.decode("ascii", errors="replace")
            require(found.groups and found.groups[0] == self.config["kernel_release"].encode(), "原廠 lab 交接未取得配對核心標記")
            lines = uboot._lines(found.before)
            starts = [i for i, line in enumerate(lines) if line.startswith("BPI_LAB_V1 ")]
            require(len(starts) == 1, "原廠最後交接缺少唯一即時探測")
            _vendor_probe("\n".join(lines[starts[0]:starts[0] + 7]).encode(), self.context)
            hashes = [line.encode() for line in lines if line.startswith("sha256 for ")]
            require(len(hashes) == len(self.config["files"]), "原廠最後交接缺少完整 RAM 摘要")
            for line, item in zip(hashes, self.config["files"].values()):
                uboot._check_hash(line, item["address"], item["bytes"], item["sha256"])
            record.update(status="kernel-marker-observed", marker=found.matched.decode("ascii"),
                          final_ram_roles=list(self.config["files"]))
        except (ValueError, OSError, TimeoutError) as exc:
            record.update(status="failed", reason=str(exc) if isinstance(exc, uboot.UBootError) else "原廠 UART 操作失敗或逾時")
            raise


def render(config):
    context = _context(config)
    return {"schema": "bpi-lab-special-runtime-render-v1", "executed": False,
            "required_commands": required_commands(config["board"]), "steps": _steps(context), "limits": LIMITS}


def boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic):
    """借用已配對、有原始 RX 日誌的 ConsoleSession；真正逐行執行，不重試或開關連線。"""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800, "總期限必須為有限正數且最多 1800 秒")
    require(records is None or type(records) is list and not records, "records 須為空陣列，不能重用失敗嘗試")
    records = [] if records is None else records
    start = monotonic()
    context = _context(config)
    remaining = timeout - (monotonic() - start)
    require(remaining > 0, "離線核對已用盡本次期限；未傳送 UART")
    runner = _Runner(console, context, records, remaining, monotonic)
    runner.at_prompt()
    for step in _steps(context):
        runner.execute(step)
    observations = [{"role": record["component"], "address": context["loads"][record["component"]]["address"],
                     "bytes": context["loads"][record["component"]]["bytes"],
                     "sha256": context["loads"][record["component"]]["sha256"], "source": "same-session-uart"}
                    for record in records if record["check"] == "sha256" and record["status"] == "verified"]
    return {"schema": "bpi-lab-special-runtime-result-v1", "status": "kernel-marker-observed",
            "config_sha256": _sha(special.encoded(config)), "kernel_release": context["core"]["kernel_release"],
            "board": config["board"], "observed_ram_hashes": observations, "observed_media_cid": context["cid"],
            "observed_partuuid": context["core"]["source"]["partuuid"], "root_verified": False,
            "root_uuid": (context["recipe"]["vendor"]["root_identity"]["uuid"] if context.get("vendor_lab")
                          else context["recipe"]["root_uuid"]), "blockers": [],
            "smoke_verified": False, "firmware_execution_verified": False, "hardware_validated": False,
            "vendor_lab_final_ram_roles": records[-1].get("final_ram_roles", []),
            "kernel_placement": config["execution"].get("kernel_placement", "original"),
            "dtb_original_hash_observed_before_resize": not context.get("vendor_lab", False), "boot_chain_changed": False,
            "environment_saved": False, "limits": LIMITS}


def load_config(path):
    return validate_config(_json(special.shared._read_evidence(path, 2 * 1024**2)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="special 一次性 UART 執行器的離線核對；CLI 不開串口")
    parser.add_argument("action", choices=("validate", "render", "vendor-source"), help="核對、展開必要命令或產生原廠 lab C 來源")
    parser.add_argument("--config", required=True, help="包含固定證據參照的執行器配置")
    args = parser.parse_args(argv)
    try:
        if args.action == "vendor-source":
            result = vendor_source(_json(special.shared._read_evidence(args.config, 2 * 1024**2)))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        config = load_config(args.config)
        result = render(config) if args.action == "render" else validate_artifacts(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc) if isinstance(exc, uboot.UBootError)
                          else "證據讀取或結構核對失敗", "hardware_validated": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
