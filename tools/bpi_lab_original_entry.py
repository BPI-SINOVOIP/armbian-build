#!/usr/bin/env python3
"""借用已配對救援 console，一次性交接 eMMC 原入口；不開啟硬體或持久寫入。"""

from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import re
import tempfile
import time

if __package__:
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_extlinux as core
    from . import bpi_lab_image as image
    from . import bpi_lab_mediatek as mediatek
    from . import bpi_lab_rockchip as rockchip
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_deploy as deploy
    import bpi_lab_extlinux as core
    import bpi_lab_image as image
    import bpi_lab_mediatek as mediatek
    import bpi_lab_rockchip as rockchip
    import bpi_lab_uboot as uboot


SCHEMA = "bpi-lab-original-entry-v1"
ABI = "mainline-original-entry-v2025.01-sd-cid-v1"
K3_ABI = "spacemit-k3-original-entry-v2022.10-v1"
Error = uboot.UBootError
require = uboot.require
BASE_KEYS = "arch uboot ram source files bootargs kernel_release fdt_extra".split()
EXTRA_KEYS = "board hardware_id root_uuid root_source mmc components pairing qualification authorization entry work decompression".split()
REVIEWS = "sd_rescue_origin command_abi volatile_environment no_persistent_writes secure_chain_compatible memory_layout immutable_media".split()
REQUIRED_CONFIG = "CONFIG_HUSH_PARSER CONFIG_CMD_BDI CONFIG_CMD_ECHO CONFIG_CMD_MEMORY CONFIG_CMD_MMC CONFIG_CMD_MMC_REG CONFIG_CMD_PART CONFIG_CMD_FS_GENERIC CONFIG_CMD_FS_UUID CONFIG_CMD_HASH CONFIG_SHA256 CONFIG_CMD_FDT CONFIG_FS_EXT4 CONFIG_LEGACY_IMAGE_FORMAT".split()
SCRIPT_LOAD_ADDR = {"rockchip64": 0x09000000, "rk35xx": 0x09000000, "rk3576": 0x48000000, "rk3506": 0x02000000}
SD_CID_MARKER = "bpi-lab-sd-cid-v1"
SD_CID_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0+
#include <command.h>
#include <mmc.h>
#include <stdio.h>

static int do_bpi_lab_sd_cid(struct cmd_tbl *cmdtp, int flag,
                           int argc, char *const argv[])
{
    struct mmc *mmc;
    unsigned int device = 0, i;
    const char *p;

    if (argc != 2 || !argv[1][0])
        return CMD_RET_USAGE;
    for (p = argv[1]; *p; ++p) {
        if (*p < '0' || *p > '9' || device > 25)
            return CMD_RET_USAGE;
        device = device * 10 + *p - '0';
    }
    if (device > 255)
        return CMD_RET_USAGE;
    mmc = find_mmc_device(device);
    if (!mmc || mmc_init(mmc) || !IS_SD(mmc))
        return CMD_RET_FAILURE;
    printf("bpi-lab-sd-cid-v1 device=%u\n", device);
    for (i = 0; i < 4; ++i)
        printf("CID[%u]: 0x%08x\n", i, mmc->cid[i]);
    return CMD_RET_SUCCESS;
}

U_BOOT_CMD(bpi_lab_sd_cid, 2, 0, do_bpi_lab_sd_cid,
           "讀取 SD 的完整 CID，不寫入媒體或環境", "<device>");
'''.encode("utf-8")
K3_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0+
#include <common.h>
#include <command.h>
#include <mmc.h>
#include <lmb.h>
#include <fb_spacemit.h>
#include <asm/global_data.h>

DECLARE_GLOBAL_DATA_PTR;

static int do_bpi_lab_emmc_cid(struct cmd_tbl *cmdtp, int flag,
                             int argc, char *const argv[])
{
    struct mmc *mmc;
    unsigned int device = 0, i;
    const char *p;

    if (argc != 2 || !argv[1][0])
        return CMD_RET_USAGE;
    for (p = argv[1]; *p; ++p) {
        if (*p < '0' || *p > '9' || device > 25)
            return CMD_RET_USAGE;
        device = device * 10 + *p - '0';
    }
    if (device > 255)
        return CMD_RET_USAGE;
    mmc = find_mmc_device(device);
    if (!mmc || mmc_init(mmc) || IS_SD(mmc))
        return CMD_RET_FAILURE;
    printf("bpi-lab-emmc-cid-v1 device=%u\n", device);
    for (i = 0; i < 4; ++i)
        printf("CID[%u]: 0x%08x\n", i, mmc->cid[i]);
    return CMD_RET_SUCCESS;
}

static int do_bpi_lab_k3_memory(struct cmd_tbl *cmdtp, int flag,
                              int argc, char *const argv[])
{
    struct lmb lmb;
    unsigned long sp, i;

    if (argc != 1 || get_boot_mode() != BOOT_MODE_SD)
        return CMD_RET_FAILURE;
    asm volatile("mv %0, sp" : "=r" (sp));
    printf("bpi-lab-k3-memory-v1 boot_mode=sdcard\n");
    for (i = 0; i < CONFIG_NR_DRAM_BANKS; ++i) {
        if (!gd->bd->bi_dram[i].size)
            continue;
        printf("DRAM bank = 0x%lx\n-> start = 0x%llx\n-> size = 0x%llx\n",
               i, (unsigned long long)gd->bd->bi_dram[i].start,
               (unsigned long long)gd->bd->bi_dram[i].size);
    }
    printf("ram_base = 0x%lx\nrelocaddr = 0x%lx\nsp start = 0x%lx\n",
           gd->ram_base, gd->relocaddr, sp);
    printf("fdt_blob = 0x%lx\nnew_fdt = 0x%lx\nfdt_size = 0x%lx\n",
           (ulong)gd->fdt_blob, (ulong)gd->new_fdt, gd->fdt_size);
    lmb_init_and_reserve(&lmb, gd->bd, (void *)gd->fdt_blob);
    printf("reserved.count = 0x%lx\n", lmb.reserved.cnt);
    for (i = 0; i < lmb.reserved.cnt; ++i) {
        struct lmb_property *p = &lmb.reserved.region[i];
        if (p->flags != LMB_NONE && p->flags != LMB_NOMAP)
            return CMD_RET_FAILURE;
        printf("reserved[%lu] [0x%llx-0x%llx], 0x%llx bytes, flags: %s\n",
               i, (unsigned long long)p->base,
               (unsigned long long)(p->base + p->size - 1),
               (unsigned long long)p->size,
               p->flags == LMB_NONE ? "none" : "no-map");
    }
    return CMD_RET_SUCCESS;
}

U_BOOT_CMD(bpi_lab_emmc_cid, 2, 0, do_bpi_lab_emmc_cid,
           "讀取 eMMC 的完整 CID，不寫入媒體或環境", "<device>");
U_BOOT_CMD(bpi_lab_k3_memory, 1, 0, do_bpi_lab_k3_memory,
           "核對 SD 啟動模式並讀取 K3 RAM 與完整保留區", "");
'''.encode("utf-8")
LIMITS = "只觀察指定核心標記，不證明 root、smoke 或實板資格；原配 Linux 啟動後可能寫入 eMMC。"
K3_CONFIG = "packages/blobs/riscv64/spacemit-k3/bpi-sm10/uboot.config"
K3_CONFIG_SHA256 = "ffb244d91c6d9ce59f20eeabee15f0391e5d6417548856cacd4720d87cf69b9c"
K3_ABI_SOURCES = {
    "cmd/mmc.c": "b9094628c3f68b8822ab128891976d6b5ff96dd9933ee0e3a978106df04022e2",
    "cmd/bdinfo.c": "2940905c39ed1b54777f3a48dbc6b2a0f13e949bd296f2826d9c1c0bf633aa89",
    "lib/lmb.c": "fcea81e58e2b499213766efd9992efdaa75839a136e56b142abdf516323cd0c5",
}


def _reference(value):
    uboot._keys(value, "path sha256", "證據參照")
    deploy.path(value["path"])
    require(uboot._match(r"[0-9a-f]{64}", value["sha256"]), "證據摘要無效")


def _extra_slots(c):
    return [c[name] for name in ("entry", "work", "decompression") if c[name] is not None]


def _base_view(config):
    c = copy.deepcopy({"schema": uboot.SCHEMA, **{k: config[k] for k in BASE_KEYS}})
    if c["files"]["kernel"]["format"] == "FIT":
        c["files"]["kernel"]["format"] = "Image"
    return c


def lifecycle_view(config):
    """只投影 RAM／媒體契約；投影不可代替原 FIT／vendor 執行配置。"""
    return uboot.validate_config(_base_view(validate_config(config)))


def validate_config(config):
    """只正規化配置，不讀取外部摘要內容，也不產生執行授權。"""
    uboot._keys(config, "schema " + " ".join(BASE_KEYS + EXTRA_KEYS), "原入口配置")
    require(config["schema"] == SCHEMA, "原入口 schema 不符")
    require(type(config["entry"]) is dict, "原入口必須是物件")
    c = copy.deepcopy(config)
    fmt = c["files"]["kernel"]["format"]
    require(c["entry"]["kind"] != "vendor-env" and c["board"] != "bpi-sm10",
            "K3 vendor runtime 尚未完成，屬獨立軟體缺口；救援命令建置不代表原入口可執行，詳見 audit_k3_abi()")
    require(fmt != "FIT" or c["board"] == "bpi-cm6" and c["arch"] == "riscv64" and c["entry"]["kind"] == "extlinux",
            "FIT 原入口僅核定 CM6 單核心 extlinux 分支")
    base = uboot.validate_config(_base_view(c))
    c.update({k: base[k] for k in BASE_KEYS})
    c["files"]["kernel"]["format"] = fmt
    require(c["source"]["type"] == "mmc" and c["files"]["initrd"] is not None
            and fmt in ({"zImage"} if c["arch"] == "arm32" else {"Image", "FIT"}),
            "原入口僅支援已核對的 zImage／Image／CM6 FIT、明示 initrd 及 MMC；沒有受控 kernel uImage 原腳本，不猜測其入口")
    require(uboot._match(r"bpi-[a-z0-9]+", c["board"]), "板型識別無效")
    deploy.identifier(c["hardware_id"])
    require(uboot._match(core.UUID, c["root_uuid"]) and c["root_uuid"] == c["root_uuid"].lower(), "根 UUID 無效")
    uboot._keys(c["root_source"], "partition partuuid uuid", "根分割區")
    uboot._source({"type": "mmc", "device": c["source"]["device"],
                   **{k: c["root_source"][k] for k in ("partition", "partuuid")}})
    require(c["root_source"]["uuid"] == c["root_uuid"], "根分割區 UUID 不符")
    uboot._keys(c["mmc"], "sd emmc", "MMC 配對")
    for value in c["mmc"].values():
        uboot._integer(value, 0, 255, "MMC 裝置")
    require(c["mmc"]["sd"] != c["mmc"]["emmc"] == c["source"]["device"], "原入口必須位於配對 eMMC，不能是救援 SD")
    uboot._keys(c["components"], "manifest artifact_root extraction", "原配證據")
    for ref in (c["pairing"], c["qualification"], c["components"]["manifest"], c["components"]["extraction"]):
        _reference(ref)
    root = deploy.path(c["components"]["artifact_root"])
    require(deploy.path(c["components"]["manifest"]["path"]) == root / "manifest.json", "原配 manifest 不在指定證據目錄")
    for name in ("pairing", "qualification"):
        require(c[name]["sha256"] == c["uboot"][name + "_sha256"], "U-Boot 與證據參照摘要不符")
    uboot._keys(c["authorization"], "record one_shot customer_boot_may_write_emmc", "一次性授權")
    deploy.identifier(c["authorization"]["record"])
    require(c["authorization"]["one_shot"] is True and c["authorization"]["customer_boot_may_write_emmc"] is True,
            "未授權一次性交接及客戶 Linux 可能寫入 eMMC")
    for name, slot in ((name, c[name]) for name in ("entry", "work", "decompression") if c[name] is not None):
        uboot._keys(slot, "address capacity" + (" kind path bytes sha256" if name == "entry" else ""), "入口工作區")
        slot["address"] = uboot._address(slot["address"], c["uboot"]["address_bits"])
        uboot._integer(slot["capacity"], 4096, 1024**3, "入口工作區容量")
        require(uboot._contains(c["ram"]["boot"], uboot._slot(slot)), "入口工作區超出 boot 範圍")
    require(c["work"] is not None, "原入口缺少工作區")
    require(c["entry"]["kind"] in ("script", "extlinux"), "原入口類型未實作")
    core.path(c["entry"]["path"])
    uboot._integer(c["entry"]["bytes"], 1, core.MAX_TEXT, "原入口長度")
    require(c["entry"]["bytes"] + 4096 <= c["entry"]["capacity"]
            and uboot._match(r"[0-9a-f]{64}", c["entry"]["sha256"]), "原入口摘要或容量無效")
    regions = [c["ram"]["kernel_work"]] + [uboot._slot(c["files"][k]) for k in ("initrd", "dtb")]
    regions += [uboot._slot(x) for x in _extra_slots(c)]
    uboot._disjoint(regions, "原入口／組件／解壓工作區")
    require(not any(uboot._overlap(a, b) for a in regions for b in c["ram"]["reserved"]), "入口工作區撞到保留區")
    return c


def scope_digest(config):
    """排除核定參照與其重複摘要，避免核定文件自我摘要循環。"""
    c = copy.deepcopy(config)
    c.pop("qualification", None)
    c["uboot"].pop("qualification_sha256", None)
    return hashlib.sha256(deploy.encode(c)).hexdigest()


def _memory(output, c, loaded=()):
    number = r"(?:0x[0-9a-fA-F]+|0)"
    count = int(uboot._one(r"\s*reserved\.(?:cnt|count)\s*=\s*(" + number + ")", output, "完整 LMB 清單")[1], 16)
    rows = [line for line in uboot._lines(output) if re.match(r"\s*reserved\[", line)]
    require(count == len(rows), "LMB 清單不完整")
    spans = []
    for item in sorted(loaded, key=lambda x: x["address"]):
        region = {"start": item["address"], "size": item["bytes"]}
        if spans and spans[-1]["start"] + spans[-1]["size"] == region["start"]:
            spans[-1]["size"] += region["size"]
        else:
            spans.append(region)
    accepted, observed = set(), []
    for index, line in enumerate(rows):
        match = re.fullmatch(r"\s*reserved\[(\d+)\]\s+\[(" + number + ")-(" + number + r")\],\s*"
                             + "(" + number + r") bytes,? flags: ([a-z0-9x, -]+)", line)
        require(match is not None and int(match[1]) == index, "LMB 清單格式或索引不完整")
        region = {"start": int(match[2], 16), "size": int(match[4], 16)}
        require(region["size"] > 0 and region["start"] + region["size"] - 1 == int(match[3], 16), "LMB 保留區長度矛盾")
        require(not any(uboot._overlap(region, earlier) for earlier in observed), "LMB 保留區重複或重疊")
        observed.append(region)
        if region in spans and match[5] in ("none", "0", "0x0"):
            accepted.add(line)
            continue
        require(not any(uboot._overlap(region, uboot._slot(slot)) for slot in _extra_slots(c)), "LMB 動態保留區撞到入口／解壓暫存區")
    # 只移除本輪完成長度與 SHA-256 核對的精確可覆寫配置，再沿用全部 RAM 守門。
    remaining, index = [], 0
    for line in uboot._lines(output):
        if line in accepted:
            continue
        if re.match(r"reserved\.(cnt|count)\s*=", line):
            line = f"reserved.count = 0x{count - len(accepted):x}"
        elif re.match(r"reserved\[", line):
            line = re.sub(r"^reserved\[\d+\]", f"reserved[{index}]", line)
            index += 1
        remaining.append(line)
    uboot._memory("\n".join(remaining).encode("ascii"), c)


def _cid(output):
    lines = [x for x in uboot._lines(output) if x]
    require(len(lines) == 4, "CID 必須恰有四個完整字組")
    words = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"CID\[" + str(index) + r"\]: 0x([0-9a-fA-F]{8})", line)
        require(match is not None, "CID 回應順序或格式不符")
        words.append(match[1].lower())
    return "".join(words)


def _sd_cid(output, device):
    lines = [line for line in uboot._lines(output) if line]
    require(len(lines) == 5 and lines[0] == f"{SD_CID_MARKER} device={device}", "缺少實際 SD CID 命令 ABI 或裝置不同")
    return _cid("\n".join(lines[1:]).encode("ascii"))


def build_rescue_sd_cid(*, output):
    """輸出可編入 v2025.01 的唯讀 SD 命令來源；不修改救援建置或核發資格。"""
    root = image.create_directory(output)
    image.save(root, "bpi_lab_sd_cid.c", SD_CID_SOURCE)
    image.save(root, "Makefile.fragment", b"obj-$(CONFIG_CMD_MMC) += bpi_lab_sd_cid.o\n")
    result = {"schema": "bpi-lab-rescue-sd-cid-source-v1", "abi": SD_CID_MARKER,
              "source": {"path": str(root / "bpi_lab_sd_cid.c"), **core.digest(SD_CID_SOURCE)},
              "compiled": False, "hardware_validated": False}
    image.save_json(root, "manifest.json", result)
    return result


def build_rescue_k3(*, output):
    """輸出 K3 SDK 專用唯讀命令來源；真正編譯與實板核定是獨立證據。"""
    root = image.create_directory(output)
    image.save(root, "bpi_lab_sd_cid.c", SD_CID_SOURCE)
    image.save(root, "bpi_lab_k3.c", K3_SOURCE)
    image.save(root, "Makefile.fragment", b"obj-$(CONFIG_CMD_MMC) += bpi_lab_sd_cid.o bpi_lab_k3.o\n")
    result = {"schema": "bpi-lab-rescue-k3-source-v1", "abi": K3_ABI,
              "sources": {name: core.digest(blob) for name, blob in
                          (("bpi_lab_sd_cid.c", SD_CID_SOURCE), ("bpi_lab_k3.c", K3_SOURCE))},
              "compiled": False, "hardware_validated": False}
    image.save_json(root, "manifest.json", result)
    return result


def _kconfig(raw):
    values = {}
    for line in raw.decode("utf-8").splitlines():
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if not match and not unset:
            require(not line or line.startswith("#"), "救援 U-Boot config 含無效賦值")
            continue
        key, value = (unset[1], "n") if unset else match.groups()
        require(key not in values, "救援 U-Boot config 重複鍵")
        values[key] = value
    return values


def audit_k3_abi(*, sdk_root=None):
    """核對受控 K3 軟體現況；不產生引導資格，也不把未實作包裝成硬體待測。"""
    if __package__:
        from . import bpi_lab_spacemit as spacemit
    else:
        import bpi_lab_spacemit as spacemit
    root = spacemit.SDK_UBOOT if sdk_root is None else deploy.path(str(sdk_root))
    sources, contents = {}, {}
    expected = {core.ROOT / K3_CONFIG: K3_CONFIG_SHA256}
    expected.update({root / name: sha for name, sha in {**spacemit.SDK_SOURCES, **K3_ABI_SOURCES}.items()})
    for path, sha in expected.items():
        blob = deploy.checked_bytes({"path": str(path), "sha256": sha}, 1024**2)
        sources[str(path)] = core.digest(blob)
        contents[path] = blob
    values = _kconfig(contents[core.ROOT / K3_CONFIG])
    mmc = contents[root / "cmd/mmc.c"]
    require(values.get("CONFIG_CMD_HASH") == "n" and values.get("CONFIG_SHA256") == "y"
            and values.get("CONFIG_SYS_CBSIZE") == "256"
            and b"U_BOOT_CMD_MKENT(reg," not in mmc and b"CID[%" not in mmc,
            "K3 命令來源或能力已變動，須重新實作並審閱，不能沿用舊缺口判定")
    return {
        "schema": "bpi-lab-original-entry-software-audit-v1", "board": "bpi-sm10",
        "status": "software-blocked", "hardware_validated": False, "boot_executed": False,
        "source_verified": True, "sources": sources,
        "capabilities": {"sha256_library": True, "hash_command": False, "full_mmc_cid_command": False,
                         "fsuuid_command": values.get("CONFIG_CMD_FS_UUID") == "y", "command_buffer_bytes": 256},
        "software_blockers": [
            {"code": "k3_hash_command", "message": "原建置 CONFIG_CMD_HASH=n；SHA256 程式庫存在不等於 UART hash 命令可用，須另建救援 U-Boot 並驗證命令。"},
            {"code": "k3_mmc_cid_command", "message": "SDK 的 MMC 子命令沒有 reg read cid；mmc info 的廠商／產品名稱不是完整 CID，須新增或回移唯讀 CID 命令。"},
            {"code": "k3_vendor_runtime", "message": "尚未實作 vendor MMC 執行器：持久環境逐鍵核對、env_k3 匯入、GPT 名稱／ESP 分支、SD boot_mode 及板級 DT 修正，不能改為 sysboot。"},
            {"code": "k3_console_memory_abi", "message": "2022.10 console／bdinfo 與 mainline RAM 守門尚未適配；256 位元組命令緩衝也須逐指令核對，不可只改 ABI 名稱。"},
        ],
        "qualification_requirements": [
            "軟體缺口完成後，仍須核定救援建置、SD 冷啟動來源、UART、雙媒體 CID 與 RAM。",
            "FSBL／ESOS／SBI、RSA／FIT、熔絲與反回滾是獨立安全鏈資格，不因軟體測試而放行。",
        ],
    }


def _qualification(c):
    pairing = deploy.load(c["pairing"])
    deploy.fields(pairing, "schema approved record hardware_id resources uart power emmc protected_sd rescue")
    require(pairing["schema"] == "bpi-lab-pairing-v1" and pairing["approved"] is True
            and pairing["hardware_id"] == c["hardware_id"], "配對文件未核定或實板識別不同")
    deploy.identifier(pairing["record"])
    for name in ("emmc", "protected_sd"):
        deploy.fields(pairing[name], "cid bytes controller")
        require(uboot._match(r"[0-9a-f]{32}", pairing[name]["cid"]), "配對媒體 CID 無效")
        uboot._integer(pairing[name]["bytes"], 1024**2, 2**50, "配對媒體大小")
        require(type(pairing[name]["controller"]) is str and bool(pairing[name]["controller"]), "缺少媒體控制器配對")
    require(pairing["emmc"]["cid"] != pairing["protected_sd"]["cid"], "雙媒體 CID 相同")
    q = deploy.load(c["qualification"])
    deploy.fields(q, "schema abi approved record hardware_id scope_sha256 pairing_sha256 firmware observations review")
    require(q["schema"] == "bpi-lab-original-entry-qualification-v1" and q["abi"] == ABI
            and q["approved"] is True and q["hardware_id"] == c["hardware_id"]
            and q["scope_sha256"] == scope_digest(c) and q["pairing_sha256"] == c["pairing"]["sha256"],
            "原入口資格未核定本配置、板號或配對")
    deploy.identifier(q["record"])
    deploy.fields(q["review"], " ".join(REVIEWS))
    require(all(q["review"][key] is True for key in REVIEWS), "原入口前置韌體／唯讀命令／媒體穩定性審閱未完整核定")
    deploy.fields(q["firmware"], "binary config sd_cid_source")
    firmware = deploy.checked_bytes(q["firmware"]["binary"], 32 * 1024**2)
    require(deploy.checked_bytes(q["firmware"]["sd_cid_source"], 64 * 1024) == SD_CID_SOURCE,
            "救援 SD CID 命令來源與受控實作不同")
    require(b"bpi_lab_sd_cid" in firmware and SD_CID_MARKER.encode() in firmware,
            "救援建置沒有唯讀 SD CID 命令；主線 mmc reg read 不支援 SD")
    values = _kconfig(deploy.checked_bytes(q["firmware"]["config"], 1024**2))
    required = REQUIRED_CONFIG + (["CONFIG_CMD_SOURCE", "CONFIG_CMD_IMPORTENV"] if c["entry"]["kind"] == "script" else ["CONFIG_CMD_SYSBOOT"])
    required += (["CONFIG_CMD_BOOTM", "CONFIG_FIT"] if c["files"]["kernel"]["format"] == "FIT" else
                 ["CONFIG_CMD_BOOTZ" if c["arch"] == "arm32" else "CONFIG_CMD_BOOTI"])
    if c["board"] == "bpi-r2":
        required += ["CONFIG_CMD_EXT4"]
    if c["decompression"] is not None:
        required += ["CONFIG_GZIP"]
    require(all(values.get(key) == "y" for key in required), "救援 U-Boot 原配 config 缺少必要命令或解壓功能")
    capacity = values.get("CONFIG_SYS_CBSIZE", "")
    require(re.fullmatch(r"[0-9]{1,6}", capacity) and c["uboot"]["line_limit"] <= int(capacity),
            "核定命令列上限超出救援 U-Boot 實際 CONFIG_SYS_CBSIZE，或原配置缺少容量")
    proof = deploy.load(q["observations"])
    deploy.fields(proof, "schema hardware_id firmware_sha256 boot_origin reset version_output bdinfo_output sd_cid_output emmc_cid_output")
    require(proof["schema"] == "bpi-lab-original-entry-observation-v1" and proof["hardware_id"] == c["hardware_id"]
            and proof["firmware_sha256"] == q["firmware"]["binary"]["sha256"]
            and proof["boot_origin"] == "sd" and proof["reset"] == "cold", "救援核定缺少綁定建置的 SD 冷開機觀測")
    uboot._check({"check": "version"}, proof["version_output"].encode("ascii"), c)
    _memory(proof["bdinfo_output"].encode("ascii"), c)
    require(_sd_cid(proof["sd_cid_output"].encode("ascii"), c["mmc"]["sd"]) == pairing["protected_sd"]["cid"], "核定觀測 SD CID 與配對不符")
    require(_cid(proof["emmc_cid_output"].encode("ascii")) == pairing["emmc"]["cid"], "核定觀測 eMMC CID 與配對不符")
    return pairing


def _media_path(path, mount):
    core.path(path)
    if mount == "/boot":
        require(path.startswith("/boot/"), "映像路徑不在獨立 boot 分割區")
        return path[5:]
    require(mount == "/", "開機掛載點未實作")
    return path


def _dtb_memory(checked, c):
    """解讀原始屬性位元組；固定 RAM 保留區不得靠摘要中的核對旗標略過。"""
    regions = list(checked["memreserve"])
    nodes = checked["reserved_memory"]
    parents = [name for name in nodes if name.count("/") == 1]
    require(not parents or parents == ["/reserved-memory"], "DTB 保留區命名或層次未實作")
    def raw(props, name):
        return bytes(int(x, 16) for x in props[name].split())
    if parents:
        parent = nodes[parents[0]]
        require({"#address-cells", "#size-cells", "ranges"} <= set(parent) and parent["ranges"] == "",
                "DTB 保留區 cells 或位址轉換未明示")
        ac, sc = (int.from_bytes(raw(parent, name), "big") for name in ("#address-cells", "#size-cells"))
        require(ac in (1, 2) and sc in (1, 2)
                and all(len(raw(parent, name)) == 4 for name in ("#address-cells", "#size-cells")), "DTB 保留區 cells 不支援")
        for name, props in nodes.items():
            if name == parents[0]:
                continue
            if "status" in props:
                status = raw(props, "status")
                if status == b"disabled\0":
                    continue
                require(status in (b"okay\0", b"ok\0"), "DTB 保留區狀態未知")
            if "reg" not in props:
                # 動態分配不偽裝成固定區間；精確原始屬性與核心配置由 scope 綁定的 RAM 審閱核定。
                require("size" in props and len(raw(props, "size")) == 4 * sc
                        and int.from_bytes(raw(props, "size"), "big") > 0, "動態保留區缺少有效 size")
                continue
            data = raw(props, "reg")
            width = 4 * (ac + sc)
            require(data and len(data) % width == 0, "DTB reg cells 長度不符")
            for offset in range(0, len(data), width):
                regions.append({"start": int.from_bytes(data[offset:offset + ac * 4], "big"),
                                "size": int.from_bytes(data[offset + ac * 4:offset + width], "big")})
    for region in regions:
        require(region["size"] > 0 and region["start"] + region["size"] <= 2**64, "DTB 固定保留區無效")
        for bank in c["ram"]["banks"]:
            start = max(region["start"], bank["start"])
            end = min(region["start"] + region["size"], bank["start"] + bank["size"])
            if start < end:
                require(any(uboot._contains(span, {"start": start, "size": end - start}) for span in c["ram"]["reserved"]),
                        "DTB 固定保留區的 RAM 交集未完整列入 ram.reserved")


def _components(c):
    refs = c["components"]
    original = deploy.load(refs["manifest"])
    require(original.get("board") == c["board"] and original.get("kernel_release") == c["kernel_release"], "原配證據板型或版本不同")
    t = {k: original[k] for k in ("board", "kernel_release", "root_uuid", "entry", "runtime_requirements", "bootargs_template")}
    t.update(schema=core.TEMPLATE_SCHEMA, pairing_sha256=c["pairing"]["sha256"], firmware_review_sha256=c["qualification"]["sha256"],
             dtb_checks={k: original["checks"][k] for k in ("dtb", "overlay_application")})
    core.validate_template(original, template=t, artifact_root=refs["artifact_root"])
    # 從成功擷取重跑原配解析；摘要相同不代表外部 manifest 的語意宣告可信。
    with tempfile.TemporaryDirectory(prefix="bpi-original-entry-") as directory:
        root = Path(directory)
        ref = refs["extraction"]
        with image.SnapshotReader(ref["path"], ref["sha256"], root / "extraction") as reader:
            prepare = mediatek.prepare if c["board"] == "bpi-r2" else rockchip.prepare if c["entry"]["kind"] == "script" else core.prepare
            actual = prepare(reader.read_file, board=c["board"], kernel_release=c["kernel_release"], output=root / "components")
            require(actual["status"] == "prepared", "重新解析原始組件失敗：" + str(actual["blockers"]))
            extraction = reader.original
            for key in ("entry", "files", "checks", "runtime_requirements", "root_uuid", "bootargs_template", "overlay_order"):
                require(actual[key] == original[key], "外部組件摘要與重新解析內容不同：" + key)
            data = {role: reader.read_file(item["path"]) for role, item in actual["files"].items() if "path" in item}
    m = actual
    _dtb_memory(m["checks"]["dtb"], c)
    require(m["root_uuid"] == c["root_uuid"] == extraction["filesystem_uuid"], "原配 root UUID 與根檔案系統不同")
    boot_partition = extraction.get("boot_partition", extraction["partition"])
    for item, expected in ((boot_partition, c["source"]), (extraction["partition"], c["root_source"])):
        require(item["index"] == expected["partition"] and item["partuuid"] == expected["partuuid"], "原始擷取的開機／根分割區與執行目標不同")
    mount = m["runtime_requirements"]["boot_mount"]
    require((boot_partition["index"] == extraction["partition"]["index"]) == (mount == "/"), "分割區與開機路徑命名空間不符")
    require(not m["overlay_order"], "最小原入口 ABI 尚未核定 overlay 的執行期套用與失敗分支；不靜默忽略")
    expected_args = [arg.replace("${partuuid}", c["source"]["partuuid"]) for arg in m["bootargs_template"]]
    require(c["bootargs"] == expected_args, "配置改寫了原入口 APPEND／bootargs 語意")
    require(not any(a.startswith("rootfstype=") and a != "rootfstype=ext4" for a in expected_args), "最小 ABI 只核定 ext4 根檔案系統")
    role = "boot_scr" if c["entry"]["kind"] == "script" else "extlinux"
    require(m["entry"]["kind"] == c["entry"]["kind"], "入口類型不符")
    target = {**c["files"], "entry": c["entry"]}
    for name, item in target.items():
        record = m["files"][role if name == "entry" else name]
        require(item["path"] == _media_path(record["path"], mount)
                and all(item[k] == record[k] for k in ("bytes", "sha256")), "實際載入路徑、長度或摘要不符：" + name)
        require(extraction["files"][record["path"]].get("volume_index", boot_partition["index"]) == boot_partition["index"], "原入口組件不在指定 boot 分割區")
    require(c["files"]["initrd"]["format"] == m["initrd_format"], "原配 initrd 容器不同")
    k = m["checks"]["kernel"]
    require(k["format"] == c["files"]["kernel"]["format"], "實際原配核心格式與執行配置不同")
    if k["format"] == "Image":
        require(c["files"]["kernel"]["capacity"] >= k["image_size"], "核心載入區未涵蓋 Image 記憶體大小與 BSS")
    elif k["format"] == "zImage":
        require(uboot._contains(c["ram"]["kernel_work"], {"start": c["files"]["kernel"]["entry"], "size": k["expanded_bytes"]}),
                "ARM32 解壓目的區未涵蓋實際展開核心；BSS／搬移暫存仍須核定 RAM 審閱")
    raw = data["kernel"]
    projected = copy.deepcopy(c["files"]["kernel"])
    if k.get("compression"):
        require(k["compression"] == "gzip" and c["decompression"] is not None, "原配 gzip 核心缺少獨立解壓工作區")
        raw, tail = core.binary._decompress(raw)
        require(not tail and c["decompression"]["capacity"] >= c["files"]["kernel"]["bytes"] * 10
                and len(raw) <= c["files"]["kernel"]["bytes"] * 10, "booti 十倍解壓界限或暫存區不符")
        projected["bytes"] = len(raw)
    else:
        require(c["decompression"] is None, "未壓縮核心不得附加未使用的解壓配置")
    if k["format"] == "FIT":
        fit = k["fit"]
        qualification = deploy.load(c["qualification"])
        config = _kconfig(deploy.checked_bytes(qualification["firmware"]["config"], 1024**2))
        limit = config.get("CONFIG_SYS_BOOTM_LEN", "")
        require(re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", limit)
                and int(limit, 16 if limit.startswith("0x") else 10) >= k["payload"]["bytes"],
                "FIT 原核心超過救援 CONFIG_SYS_BOOTM_LEN，或建置未明示搬移界限")
        destination = {"start": fit["load"], "size": k["payload"]["image_size"]}
        require(fit["entry"] == c["files"]["kernel"]["entry"] == fit["load"]
                and uboot._contains(c["ram"]["kernel_work"], destination)
                and not uboot._overlap(destination, uboot._slot(c["files"]["kernel"])),
                "FIT 固定載入／入口／BSS 範圍超出工作區或覆蓋原容器")
    else:
        uboot._header(raw[:64], projected, "kernel", c)
    if c["entry"]["kind"] == "script":
        mode = "mt7623" if c["board"] == "bpi-r2" else m.get("script_profile")
        require(mode in ("rockchip64", "rk35xx", "rk3576", "rk3506", "mt7623")
                and (c["arch"] == "arm32") == (mode in ("rk3506", "mt7623"))
                and mount == "/" and c["files"]["initrd"]["format"] == "legacy", "boot.scr 不在已核對的 Rockchip／MT7623 分支")
        require(c["work"]["capacity"] > len(data["env"]), "原腳本環境暫存容量不足")
        if mode == "mt7623":
            require(c["files"]["kernel"]["capacity"] > len(data["env"]), "MT7623 使用 kernel_addr_r 匯入環境，暫存容量不足")
        else:
            require(c["work"]["address"] == SCRIPT_LOAD_ADDR[mode], "原腳本固定 load_addr 不符")
            require(c["fdt_extra"] >= 0x65536, "原腳本 fdt resize 65536 依十六進位解讀，擴充容量不足")
        if mode == "rk3506":
            require(c["source"]["partition"] == 1 and c["files"]["initrd"]["address"] == 0x02800000,
                    "Forge1 原腳本隱含第 1 分割區及固定 ramdisk_addr_r，不可改指其他配置")
    else:
        directives = m["extlinux"]["labels"][0]["directives"]
        require("KASLRSEED" not in directives and m["extlinux"]["global"].get("PROMPT", "0") == "0",
                "最小 extlinux ABI 不核定互動 PROMPT 或 KASLRSEED；不得悄悄忽略")
    return m, extraction


def validate_artifacts(config, artifact_root=None):
    """讀取完整配對、核定、原始觀測及擷取，回傳已核對計畫；不碰硬體。"""
    c = validate_config(config)
    if artifact_root is not None:
        require(deploy.path(str(artifact_root)) == deploy.path(c["components"]["artifact_root"]), "呼叫端證據目錄不同")
    try:
        pairing = _qualification(c)
        m, _ = _components(c)
    except (KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise Error("原入口證據內容缺欄位、型別不符或不是有效文字") from exc
    steps = _steps(c, m, pairing)
    require(all(len(uboot._wire(s["command"], "0" * 16)) <= c["uboot"]["line_limit"] for s in steps), "原入口指令超過已核定命令列上限")
    return {"config": c, "steps": steps, "artifacts_verified": True, "hardware_validated": False}


def build_uboot_config(template, *, artifact_root):
    """後端範本入口；回傳獨立原入口 schema，禁止交給共用 uboot.boot。"""
    return validate_artifacts(template, artifact_root)["config"]


def _steps(c, m, pairing):
    steps = []
    def add(command, check="status", **fields):
        steps.append({"command": command, "check": check, **fields})
    def setting(key, value):
        require(uboot._match(r"[A-Za-z0-9_]+", key) and uboot._match(r"[A-Za-z0-9_./,:=+@% -]*", value), "執行期環境含未支援字元")
        add("setenv " + key + (" " + value if value else ""))
        if value:
            add("printenv " + key, "env", variable=key, value=value)
        else:
            add('test -z "${' + key + '}"')
    source, root = c["source"], c["root_source"]
    devpart = f"{source['device']:x}:{source['partition']:x}"
    add("version", "version")
    add("bdinfo", "entry-memory")
    for cmd in ("load", "mmc", "bpi_lab_sd_cid", "part", "fsuuid", "hash", "setenv", "printenv", "test", "source" if c["entry"]["kind"] == "script" else "sysboot"):
        add("help " + cmd)
    add("help " + ("bootm" if c["files"]["kernel"]["format"] == "FIT" else "bootz" if c["arch"] == "arm32" else "booti"))
    if c["board"] == "bpi-r2":
        add("help ext4load")
    add(f"hash sha256 {c['files']['kernel']['address']:x} 0", "sha256-probe")
    setting("autostart", "no")
    add("base 0", "base")
    add(f"bpi_lab_sd_cid {c['mmc']['sd']}", "entry-sd-cid", expected=pairing["protected_sd"]["cid"])
    add(f"mmc dev {c['mmc']['emmc']} 0")
    for index in range(4):
        add(f"mmc reg read cid {index}", "entry-cid", index=index, expected=pairing["emmc"]["cid"][index * 8:index * 8 + 8])
    add("part uuid mmc " + devpart, "entry-line", expected=source["partuuid"])
    if m.get("script_profile") == "rk3506":
        add(f"part uuid mmc {source['device']:x}", "entry-line", expected=source["partuuid"])
    add(f"part uuid mmc {source['device']:x}:{root['partition']:x}", "entry-line", expected=root["partuuid"])
    add(f"fsuuid mmc {source['device']:x}:{root['partition']:x}", "entry-line", expected=root["uuid"])
    mount = m["runtime_requirements"]["boot_mount"]
    for row in m["reads"]:
        if row["status"] == "missing" and row["path"].startswith("/boot/"):
            name = _media_path(row["path"], mount)
            add(f"if test -e mmc {devpart} {name}; then false; else true; fi", "entry-absent")
    records = [(key, item) for key, item in c["files"].items()]
    if c["entry"]["kind"] == "script":
        records.append(("env", {**m["files"]["env"], **c["work"], "path": _media_path(m["files"]["env"]["path"], mount)}))
    records.append(("entry", c["entry"]))
    for name, item in records:
        add("setenv filesize")
        add(f"load mmc {devpart} {item['address']:x} {item['path']} {item['bytes'] + 1:x} 0", "entry-length", expected=item["bytes"])
        add("printenv filesize", "entry-size", expected=item["bytes"])
        add(f"hash sha256 {item['address']:x} {item['bytes']:x}", "entry-hash", item=item)
    mask = (1 << c["uboot"]["address_bits"]) - 1
    settings = {"fdt_high": f"{mask:x}", "initrd_high": f"{mask:x}", "bootm_low": f"{c['ram']['boot']['start']:x}",
                "bootm_size": f"{c['ram']['boot']['size']:x}", "bootm_mapsize": f"{c['ram']['boot']['size']:x}",
                "kernel_addr_r": f"{c['files']['kernel']['address']:x}", "ramdisk_addr_r": f"{c['files']['initrd']['address']:x}",
                "fdt_addr_r": f"{c['files']['dtb']['address']:x}", "fdt_addr": "", "bootargs": "",
                "kernel_comp_addr_r": f"{c['decompression']['address']:x}" if c["decompression"] else "",
                "kernel_comp_size": f"{c['files']['kernel']['bytes']:x}" if c["decompression"] else "",
                "pxe_label_override": "", "fdtfile": m["profile"]["dtb"]}
    if c["files"]["kernel"]["format"] == "FIT":
        settings["verify"] = "yes"
    if c["entry"]["kind"] == "script":
        settings.update({k: "" for k in sorted(rockchip.ENV_KEYS | {"consoleargs", "partuuid", "overlay_file", "rootfs", "rootuuid"})})
        settings.update(devtype="mmc", devnum=f"{source['device']:x}", distro_bootpart=f"{source['partition']:x}",
                        prefix="boot/", fdtfile=m["profile"]["dtb"])
        if c["board"] == "bpi-r2":
            settings.update(mmcpart=f"{source['partition']:x}", fdtfile=m["runtime_requirements"]["fdtfile"])
    for key, value in settings.items():
        setting(key, value)
    # source 直接使用已核對 RAM 腳本；sysboot 依核定的穩定媒體契約重讀同一設定。
    add("bdinfo", "entry-memory")
    for _, item in records:
        add(f"hash sha256 {item['address']:x} {item['bytes']:x}", "entry-hash", item=item)
    command = f"source {c['entry']['address']:x}" if c["entry"]["kind"] == "script" else f"sysboot mmc {devpart} any {c['entry']['address']:x} {c['entry']['path']}"
    add(command, "kernel-marker")
    return steps


def _check(step, output, c, loaded=()):
    check = step["check"]
    if check == "entry-memory":
        _memory(output, c, loaded)
    elif check == "entry-sd-cid":
        require(_sd_cid(output, c["mmc"]["sd"]) == step["expected"], "實際 SD CID 與配對不符，禁止交接")
    elif check == "entry-cid":
        found = uboot._one(r"CID\[" + str(step["index"]) + r"\]: 0x([0-9a-fA-F]{8})", output, "MMC CID")
        require(found[1].lower() == step["expected"], "實際 MMC CID 與配對不符，禁止交接")
    elif check == "entry-line":
        require([x for x in uboot._lines(output) if x] == [step["expected"]], "實際分割區／檔案系統 UUID 不符")
    elif check == "entry-absent":
        require(not any(uboot._lines(output)), "缺檔探測含錯誤輸出，不能當作確認不存在")
    elif check in ("entry-length", "entry-size"):
        pattern = r"([0-9]+) bytes read(?: in .*)?" if check == "entry-length" else r"filesize=([0-9a-fA-F]+)"
        actual = int(uboot._one(pattern, output, "原入口載入長度")[1], 10 if check == "entry-length" else 16)
        require(actual == step["expected"], "原入口實際載入長度不符")
    elif check == "entry-hash":
        item = step["item"]
        uboot._check_hash(output, item["address"], item["bytes"], item["sha256"])
    else:
        raise Error("未知原入口回應檢查")


def render(config):
    """離線展開已完整核對的受限命令，不是可略過回應核對的 shell 腳本。"""
    result = validate_artifacts(config)
    return {"schema": "bpi-lab-original-entry-render-v1", "executed": False, "hardware_validated": False,
            "config_sha256": uboot._digest(result["config"]), "steps": result["steps"], "limits": LIMITS}


def boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic):
    """全部本機守門通過後才傳送，無重試、saveenv、登入或電源操作。"""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800, "原入口總期限無效")
    require(records is None or type(records) is list, "records 必須為陣列")
    end = monotonic() + timeout
    result = validate_artifacts(config)
    remaining = end - monotonic()
    require(remaining > 0, "原入口本機核對已逾時，未傳送命令")
    records = [] if records is None else records
    c = result["config"]
    runner = uboot._Runner(console, c, records, remaining, monotonic)
    runner.at_prompt()
    loaded = {}
    for step in result["steps"]:
        if not step["check"].startswith("entry-"):
            runner.execute(step)
            continue
        runner.execute({**step, "check": "status"})
        record = records[-1]
        record["check"] = step["check"]
        try:
            _check(step, record["output"].encode("ascii"), c, loaded.values())
            if step["check"] == "entry-hash":
                loaded[step["item"]["address"]] = step["item"]
        except (ValueError, UnicodeError) as exc:
            record.update(status="failed", reason=str(exc))
            raise Error(str(exc)) from exc
    return {"schema": "bpi-lab-original-entry-result-v1", "status": "kernel-marker-observed",
            "config_sha256": uboot._digest(c), "kernel_release": c["kernel_release"],
            "hardware_validated": False, "root_verified": False, "smoke_verified": False, "limits": LIMITS}
