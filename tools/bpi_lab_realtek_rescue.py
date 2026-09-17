#!/usr/bin/env python3
"""Realtek 固定 SD→RAM 救援；只產生來源或借用既有 UART，不開設備、不寫媒體。"""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import time

if __package__:
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_special_runtime as vendor
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_deploy as deploy
    import bpi_lab_special_runtime as vendor
    import bpi_lab_uboot as uboot

SCHEMA = "bpi-lab-realtek-rescue-v1"
QUALIFICATION_SCHEMA = "bpi-lab-realtek-rescue-qualification-v1"
DEPENDENCIES = ("bpi_lab_realtek_rescue.py", *vendor.DEPENDENCIES, "bpi_lab_deploy.py",
                "bpi_h618_artifacts.py", "bpi_h618_emmc_backup.py", "bpi_h618_emmc_deploy.py")
SD_SOURCES = ("drivers/mmc/sd.c", "drivers/mmc/mmc.c", "drivers/mmc/Makefile",
              "disk/part.c", "fs/fat/fat.c", "include/libfdt.h")
ROLES = ("kernel", "initrd", "dtb", "audio")
require = deploy.require


def _scope_config(config):
    # 平台資格內含合併 binary 摘要，不能再嵌入待建置 C 來源造成自我摘要循環。
    return {**config, "platform": {"scope_sha256": vendor.scope_digest(deploy.load(config["platform"]))}}


def scope_digest(config):
    return vendor.scope_digest(_scope_config(config))


def required_commands():
    return ["version", "help", "echo", "setenv", "printenv", "bpirescue memory",
            "bpirescue probe", "bpirescue load", "bpirescue hash", "bpirescue boot"]


def sd_source_names(board):
    require(board in vendor.VENDOR_BOARDS, "未知 Realtek SD 來源家族")
    return SD_SOURCES + (("drivers/mmc/rtksdmmc_rtd1395.c", "arch/arm/include/asm/arch-rtd1395/rtksdmmc.h")
                         if board == "bpi-m4" else ("drivers/mmc/rtksdmmc.c", "drivers/mmc/rtksdmmc.h"))


def _initrd_identity(blob, kernel):
    vendor.special.validate_initrd(blob, arch="arm64", kernel_release=kernel)
    payload, _ = vendor.special.legacy(blob, kind=3, arch=(22,))
    if not payload.startswith((b"070701", b"070702")):
        payload, tail = vendor.special.shared._uncompress(payload, vendor.special.MAX_EXPANDED)
        require(not tail.strip(b"\0"), "救援 initrd 不接受多段覆蓋身分")
    end, _ = vendor.special.shared._cpio(payload, 0, set())
    require(not payload[end:].strip(b"\0"), "救援 initrd 只接受單一完整 cpio")
    offset, identities = 0, []
    while offset < end:
        header = payload[offset:offset + 110]
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        mode, links, size, namesize = fields[1], fields[4], fields[6], fields[11]
        name = payload[offset + 110:offset + 110 + namesize - 1].decode()
        start = (offset + 110 + namesize + 3) & ~3
        if name.removeprefix("./") == "etc/bpi-rescue.json":
            require(mode & 0o170000 == 0o100000 and links == 1 and size <= 65536,
                    "救援身分須為唯一一般檔，不接受連結")
            identities.append(payload[start:start + size])
        offset = (start + size + 3) & ~3
    require(len(identities) == 1, "救援 initrd 缺少唯一 /etc/bpi-rescue.json")
    return identities[0]


def _context(config, *, qualified=True):
    deploy.fields(config, "schema platform artifact_root files sd sd_sources identity qualification")
    require(config["schema"] == SCHEMA, "Realtek 救援 schema 不符")
    platform = deploy.load(config["platform"])
    require(platform.get("schema") == vendor.SCHEMA and platform.get("board") in vendor.VENDOR_BOARDS,
            "救援須綁定完整 Realtek vendor 配置")
    # 僅重用已核對的家族載荷、RAM 及原廠來源；不以客戶 LABEL 資格代替救援核定。
    base = vendor._vendor_context(copy.deepcopy(platform), qualified=False)
    core = copy.deepcopy(base["core"])
    pairing = base["pairing"]
    deploy.fields(config["sd"], "device index block_device partition partuuid extraction prefix")
    sd = config["sd"]
    require(sd["device"] == "sd" and type(sd["index"]) is int and sd["index"] == 0
            and type(sd["partition"]) is int and sd["partition"] == 1,
            "已讀 BSP 的 SD 是獨立 sd 0:1，不能推論為主線 MMC")
    require(type(sd["block_device"]) is int and 0 <= sd["block_device"] <= 255,
            "SD block descriptor 編號須另由配對明示")
    require(type(sd["partuuid"]) is str and re.fullmatch(
        r"(?:[0-9a-f]{8}-[0-9a-f]{2}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", sd["partuuid"]),
        "救援 SD PARTUUID 不符")
    media = pairing["protected_sd"]
    deploy.backup.validate_expected(media["cid"], media["bytes"], media["controller"])
    require(media["cid"] != pairing["emmc"]["cid"], "救援 SD 不得等於客戶 eMMC")
    deploy.digest_record(sd["prefix"], 4 * 1024**2)
    require(sd["prefix"]["bytes"] == 4 * 1024**2, "固定 SD 前綴須為完整 4 MiB")
    extraction = deploy.load(sd["extraction"])
    require(extraction.get("schema") in ("bpi-lab-image-v1", "bpi-lab-image-replay-v1")
            and extraction.get("ok") is True and extraction.get("source_verified") is True
            and extraction.get("hardware_validated") is False, "救援 SD 原檔擷取證據未完整通過")
    partition = extraction["partition"]
    require(partition["index"] == 1 and partition["partuuid"] == sd["partuuid"],
            "救援原檔不是固定 SD 分割區")
    root = deploy.path(config["artifact_root"])
    deploy.fields(config["files"], " ".join(ROLES))
    for role in ROLES:
        item = config["files"][role]
        deploy.fields(item, "path image_path bytes sha256")
        deploy.safe.relative_parts(item["path"])
        deploy.digest_record({key: item[key] for key in ("bytes", "sha256")}, vendor.special.MAX_FILE)
        blob = deploy.checked_bytes({"path": str(root / item["path"]), "sha256": item["sha256"]},
                                    vendor.special.MAX_FILE)
        require(len(blob) == item["bytes"], "救援載荷大小不符")
        logical = item["image_path"]
        require(type(logical) is str and re.fullmatch(r"/[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*", logical)
                and len(logical) <= 120 and not {".", ".."}.intersection(logical.split("/")), "救援 SD 路徑無效")
        original = extraction["files"].get(logical, {})
        require(original.get("digest") == {key: item[key] for key in ("bytes", "sha256")}
                and original.get("volume_index", partition["index"]) == 1
                and original.get("resolved") == logical, "救援載荷未綁定真實 SD 原檔；不猜測準備產物路徑")
        slot = core["files"][role]
        require(item["bytes"] + 1 <= slot["capacity"], "救援載荷超出核定 RAM 容量")
        if role == "initrd":
            require(_initrd_identity(blob, core["kernel_release"]) == deploy.checked_bytes(config["identity"], 65536),
                    "救援 initrd 內身分不是固定 RAM 契約")
            slot.update(bytes=item["bytes"], sha256=item["sha256"], format="legacy")
        else:
            require(all(item[key] == slot[key] for key in ("bytes", "sha256")),
                    "救援 kernel／DTB／audio 必須沿用已核對的同板實際載荷")
        slot["path"] = logical
    core["files"] = {role: core["files"][role] for role in ROLES}
    identity = deploy.load(config["identity"])
    deploy.fields(identity, "schema kernel")
    require(type(identity["schema"]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,127}", identity["schema"])
            and identity["kernel"] == core["kernel_release"], "救援身分與原配核心不符")
    core["bootargs"] = [arg for arg in core["bootargs"]
                        if not arg.startswith(("root=", "rootfstype=", "rootflags=", "init=", "rdinit="))
                        and arg not in ("rw", "ro", "rootwait")]
    core["bootargs"] += ["root=/dev/ram0", "ro", "rdinit=/init"]
    core["source"] = {"type": "vendor-sd", "device": 0, "partition": 1, "partuuid": sd["partuuid"]}
    core["uboot"]["qualification_sha256"] = config["qualification"].get("sha256", "0" * 64)
    deploy.fields(config["sd_sources"], " ".join(sd_source_names(platform["board"])))
    for reference in config["sd_sources"].values():
        deploy.checked_bytes(reference, 4 * 1024**2)
    context = {**base, "config": config, "platform": platform, "core": core, "cid": media["cid"],
               "firmware": {"audio": core["files"]["audio"]}, "sd": media,
               "identity": {**identity, "identity_sha256": config["identity"]["sha256"]}}
    context["source"] = base["lab_source"] + _rescue_source(context)
    if qualified:
        vendor.validate_config(platform)
        q = deploy.load(config["qualification"])
        deploy.fields(q, "schema scope_sha256 hardware_id approved hardware_validated ram_rescue_verified "
                      "protected_sd_verified source_evidence memory_evidence dependencies required_commands vendor_build")
        require(q["schema"] == QUALIFICATION_SCHEMA and q["scope_sha256"] == scope_digest(config)
                and q["hardware_id"] == pairing["hardware_id"]
                and all(q[key] is True for key in ("approved", "hardware_validated", "ram_rescue_verified", "protected_sd_verified")),
                "固定 SD RAM 救援未取得本板、本配置的獨立核定")
        require(type(q["source_evidence"]) is list and 1 <= len(q["source_evidence"]) <= 32, "救援核定缺少原始證據")
        for reference in q["source_evidence"]:
            deploy.checked_bytes(reference, 16 * 1024**2)
        deploy.fields(q["dependencies"], " ".join(DEPENDENCIES))
        for name in DEPENDENCIES:
            deploy.checked_bytes({"path": str(Path(__file__).resolve().parent / name),
                                  "sha256": q["dependencies"][name]}, 2 * 1024**2)
        vendor._vendor_memory(deploy.checked_bytes(q["memory_evidence"], 65536), context)
        require(q["required_commands"] == required_commands(), "救援命令資格未涵蓋實際 ABI")
        build = q["vendor_build"]
        deploy.fields(build, "source_sha256 binary build_config link_map")
        require(build["source_sha256"] == hashlib.sha256(context["source"].encode()).hexdigest(),
                "救援合併 C 來源未綁定本次建置")
        customer_build = deploy.load(platform["qualification"])["vendor_build"]
        for key in ("binary", "build_config", "link_map"):
            require(build[key] == customer_build[key], "客戶與救援不是同一份 U-Boot 建置")
            deploy.checked_bytes(build[key], 64 * 1024**2)
    return context


def _rescue_source(context):
    """附加在同一份 vendor_source 之後，共用其即時 RAM 防護，不重讀原廠 gosd。"""
    c, core = context["config"], context["core"]
    records = []
    for role in ROLES:
        item = core["files"][role]
        digest = ",".join("0x" + item["sha256"][i:i + 2] for i in range(0, 64, 2))
        records.append(f'{{"{role}","{item["path"]}",0x{item["address"]:x}UL,0x{item["bytes"]:x}UL,{{{digest}}}}}')
    prefix = ",".join("0x" + c["sd"]["prefix"]["sha256"][i:i + 2] for i in range(0, 64, 2))
    cid = "\n".join(f"    if (sd->cid[{i}] != 0x{context['cid'][i * 8:(i + 1) * 8]}U) return 1;" for i in range(4))
    settings = {"kernel_loadaddr": f'{core["files"]["kernel"]["address"]:x}',
                "rootfs_loadaddr": f'{core["files"]["initrd"]["address"]:x}',
                "fdt_loadaddr": f'{core["files"]["dtb"]["address"]:x}',
                "audio_loadaddr": f'{core["files"]["audio"]["address"]:x}',
                "fdt_high": "ffffffffffffffff", "initrd_high": "ffffffffffffffff",
                "bootargs": " ".join(core["bootargs"])}
    env = "\n".join(f'    if (setenv("{key}", "{value}")) return CMD_RET_FAILURE;' for key, value in settings.items())
    source_ids = "\n".join("/* " + name + " SHA256 " + c["sd_sources"][name]["sha256"] + " */"
                           for name in sorted(c["sd_sources"]))
    return r'''
/* 固定 SD→RAM 救援；須與前方客戶 bpilab 一起建置、核定，不可獨立燒錄。 */
#include <libfdt.h>
#if !defined(CONFIG_RTK_SD_DRIVER) || !defined(CONFIG_CMD_FAT)
#error "救援需要已核對的 Realtek SD 與 FAT 支援"
#endif
''' + source_ids + r'''
static const struct bpi_lab_payload bpi_rescue_payloads[] = {
''' + ",\n".join(records) + r'''
};
static int bpi_rescue_selected, bpi_rescue_used;
static unsigned int bpi_rescue_loaded;
static int bpi_rescue_sd(void)
{
    struct mmc *sd = find_sd_device();
    disk_partition_t part;
    unsigned int i;
    if (!sd || sd_init(sd) || !IS_SD(sd) || sd->part_num != 0) return 1;
    if (sd->block_dev.if_type != IF_TYPE_SD ||
        sd->block_dev.blksz != 512 || !sd->block_dev.block_read) return 1;
''' + f'    if (sd->block_dev.dev != {c["sd"]["block_device"]} || sd->block_dev.lba != {context["sd"]["bytes"] // 512}ULL) return 1;\n' + cid + f'''
    if (get_partition_info(&sd->block_dev, 1, &part) || strcmp(part.uuid, "{c["sd"]["partuuid"]}")) return 1;
''' + r'''
    printf("BPI_LAB_V1 ''' + scope_digest(c) + r'''\n");
    printf("BPI_LAB_STATE 0 0\n");
    for (i = 0; i < 4; ++i) printf("CID[%u]: 0x%08x\n", i, sd->cid[i]);
    printf("BPI_LAB_PART %s\n", part.uuid);
    return 0;
}
static int bpi_rescue_prefix(void)
{
    struct mmc *sd = find_sd_device();
    unsigned char *buffer, actual[32];
    static const unsigned char wanted[32] = {''' + prefix + r'''};
    sha256_context sha;
    unsigned int sector;
    if (!sd) return 1;
    buffer = memalign(ARCH_DMA_MINALIGN, 65536);
    if (!buffer) return 1;
    if (!bpi_lab_protected((ulong)buffer, 65536)) { free(buffer); return 1; }
    sha256_starts(&sha);
    for (sector = 0; sector < 8192; sector += 128) {
        if (sd->block_dev.block_read(sd->block_dev.dev, sector, 128, buffer) != 128) { free(buffer); return 1; }
        sha256_update(&sha, buffer, 65536);
    }
    sha256_finish(&sha, actual);
    free(buffer);
    return memcmp(actual, wanted, 32) != 0;
}
static int bpi_rescue_probe(void)
{
    if (bpi_rescue_used ||
        ((rtd_inl(OTP_REG_BASE + (OTP_BIT_SECUREBOOT / 32) * 4) >> (OTP_BIT_SECUREBOOT % 32)) & 1) ||
        audio_fw_state || ipc_ir_set || (rtd_inl(CLOCK_ENABLE2_reg) & _BIT4)) return 1;
    if (!bpi_rescue_selected) {
        if (bpi_lab_used || bpi_lab_loaded) return 1;
        bpi_lab_used = 1;
        bpi_rescue_selected = 1;
    }
    if (!bpi_lab_protected(gd->relocaddr, gd->mon_len) ||
        !bpi_lab_protected(gd->start_addr_sp, 1) ||
        (gd->irq_sp && !bpi_lab_protected(gd->irq_sp, 1)) ||
        (gd->arch.tlb_addr && !bpi_lab_protected(gd->arch.tlb_addr, 1)) ||
        mem_malloc_end <= mem_malloc_start ||
        !bpi_lab_protected(mem_malloc_start, mem_malloc_end - mem_malloc_start) ||
        (gd->fdt_blob && !bpi_lab_protected((ulong)gd->fdt_blob, gd->fdt_size)) ||
        (gd->new_fdt && !bpi_lab_protected((ulong)gd->new_fdt, gd->fdt_size))) return 1;
''' + "\n".join(f'    if (gd->bd->bi_dram[{i}].start != 0x{bank["start"]:x}UL || '
                f'gd->bd->bi_dram[{i}].size != 0x{bank["size"]:x}UL) return 1;'
                for i, bank in enumerate(core["ram"]["banks"])) + f'''
    {{
        unsigned int i;
        for (i = {len(core["ram"]["banks"])}; i < CONFIG_NR_DRAM_BANKS; ++i)
            if (gd->bd->bi_dram[i].size) return 1;
    }}
    if (ipc_shm.audio_fw_entry_pt && ipc_shm.audio_fw_entry_pt !=
        SWAPEND32(0x{core["files"]["audio"]["address"]:x}U | MIPS_KSEG0BASE)) return 1;
''' + r'''
    return bpi_rescue_sd() || bpi_rescue_prefix();
}
static int bpi_rescue_load(unsigned int index)
{
    loff_t received = 0;
    const struct bpi_lab_payload *p = &bpi_rescue_payloads[index];
    bpi_rescue_loaded &= ~(1U << index);
    if (bpi_rescue_probe() || fs_set_blk_dev("sd", "0:1", FS_TYPE_FAT)) return 1;
    if (fs_read(p->path, p->address, 0, p->size + 1, &received) < 0 ||
        received < 0 || (unsigned long long)received != p->size || bpi_rescue_probe()) return 1;
    if (setenv_hex("filesize", received)) return 1;
    printf("%llu bytes read\n", (unsigned long long)received);
    bpi_rescue_loaded |= 1U << index;
    return 0;
}
static int bpi_rescue_hash(unsigned int index)
{
    unsigned char actual[32];
    unsigned int i;
    const struct bpi_lab_payload *p = &bpi_rescue_payloads[index];
    if (!(bpi_rescue_loaded & (1U << index))) return 1;
    sha256_csum_wd((const unsigned char *)p->address, p->size, actual, CHUNKSZ_SHA256);
    printf("sha256 for %08lx ... %08lx ==> ", p->address, p->address + p->size - 1);
    for (i = 0; i < 32; ++i) printf("%02x", actual[i]);
    printf("\n");
    return memcmp(actual, p->digest, 32) != 0;
}
static int do_bpirescue(cmd_tbl_t *cmdtp, int flag, int argc, char * const argv[])
{
    unsigned int i;
    int chosen;
''' + f'    void *fdt = (void *)0x{core["files"]["dtb"]["address"]:x}UL;\n' + r'''
    if (argc < 2 || argc > 3 || bpi_rescue_used) return CMD_RET_FAILURE;
    if (argc == 2 && !strcmp(argv[1], "memory")) { bpi_lab_memory(); return 0; }
    if (argc == 2 && !strcmp(argv[1], "probe")) return bpi_rescue_probe();
    if (argc == 3 && (!strcmp(argv[1], "load") || !strcmp(argv[1], "hash"))) {
        for (i = 0; i < ARRAY_SIZE(bpi_rescue_payloads); ++i)
            if (!strcmp(argv[2], bpi_rescue_payloads[i].role))
                return !strcmp(argv[1], "load") ? bpi_rescue_load(i) : bpi_rescue_hash(i);
        return CMD_RET_USAGE;
    }
    if (argc != 2 || strcmp(argv[1], "boot") || bpi_rescue_probe() ||
        bpi_rescue_loaded != (1U << ARRAY_SIZE(bpi_rescue_payloads)) - 1) return CMD_RET_FAILURE;
    for (i = 0; i < ARRAY_SIZE(bpi_rescue_payloads); ++i)
        if (bpi_rescue_hash(i)) return CMD_RET_FAILURE;
    bpi_rescue_used = 1;
''' + env + f'''
    if (setenv("hyp_loadaddr", NULL) || fdt_open_into(fdt, fdt, {core["files"]["dtb"]["capacity"]})) return CMD_RET_FAILURE;
    chosen = fdt_path_offset(fdt, "/chosen");
    if (chosen == -FDT_ERR_NOTFOUND) chosen = fdt_add_subnode(fdt, 0, "chosen");
    if (chosen < 0 || fdt_setprop_string(fdt, chosen, "bootargs", "{settings["bootargs"]}")) return CMD_RET_FAILURE;
''' + r'''
    if (do_go_audio_fw()) return CMD_RET_FAILURE;
    boot_mode = BOOT_RESCUE_MODE;
#ifdef CONFIG_WAIT_AFW_1_SECOND
    mdelay(1000);
#endif
    return rtk_call_booti();
}
U_BOOT_CMD(bpirescue, 3, 0, do_bpirescue, "固定 SD 的受限 RAM 救援", "memory | probe | load <role> | hash <role> | boot");
'''


def vendor_source(config):
    context = _context(config, qualified=False)
    source = context["source"]
    return {"schema": "bpi-lab-realtek-source-v1", "board": context["platform"]["board"],
            "source": source, "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "append_to": "common/cmd_boot.c", "commands": ["bpilab", "bpirescue"],
            "hardware_validated": False, "deployment_verified": False}


def validate_config(config):
    return _context(config)["config"]


def validate_artifacts(config, artifact_root=None):
    checked = validate_config(config)
    require(artifact_root is None or str(deploy.path(artifact_root)) == checked["artifact_root"], "救援組件根目錄不同")
    return {"status": "validated_offline", "hardware_validated": False, "roles": list(ROLES)}


def lifecycle_view(config):
    return _context(config)["core"]


def _steps(context):
    steps = [{"command": "version", "check": "version"}, {"command": "bpirescue memory", "check": "vendor-memory"}]
    steps += [{"command": "help " + name, "check": "status"} for name in ("bpirescue", "setenv", "printenv")]
    steps += [{"command": "bpirescue probe", "check": "vendor-probe"}, {"command": "setenv autostart no", "check": "status"},
              {"command": "printenv autostart", "check": "env", "variable": "autostart", "value": "no"}]
    for role in ROLES:
        steps += [{"command": "setenv filesize", "check": "status"},
                  {"command": "bpirescue load " + role, "check": "length", "component": role},
                  {"command": "printenv filesize", "check": "filesize", "component": role}]
    steps += [{"command": "bpirescue hash " + role, "check": "sha256", "component": role} for role in ROLES]
    steps += [{"command": "bpirescue memory", "check": "vendor-memory"},
              {"command": "bpirescue probe", "check": "vendor-probe"}, {"command": "bpirescue boot", "check": "vendor-kernel"}]
    return steps


def boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic):
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800, "救援 UART 期限無效")
    require(records is None or type(records) is list and not records, "救援不得重用失敗命令紀錄")
    start = monotonic()
    context = _context(config)
    remaining = timeout - (monotonic() - start)
    require(remaining > 0, "救援離線核對已逾時；未傳送 UART")
    records = [] if records is None else records
    wire_context = copy.deepcopy(context)
    # 共用 length 解析器的 bytes read 格式；命令仍只有 bpirescue，實際來源不改成 MMC。
    wire_context["core"]["source"]["type"] = "mmc"
    wire_context["config"] = _scope_config(config)
    runner = vendor._Runner(console, wire_context, records, remaining, monotonic)
    runner.at_prompt()
    for step in _steps(context):
        length = len(step["command"]) + 1 if step["check"] == "vendor-kernel" else len(uboot._wire(step["command"], "0" * 16))
        require(length <= context["core"]["uboot"]["line_limit"], "救援命令超過核定行長")
        runner.execute(step)
    return {"schema": "bpi-lab-realtek-rescue-result-v1", "status": "kernel-marker-observed",
            "config_sha256": hashlib.sha256(deploy.encode(config)).hexdigest(),
            "board": context["platform"]["board"], "kernel_release": context["core"]["kernel_release"],
            "observed_media_cid": context["cid"], "observed_partuuid": context["core"]["source"]["partuuid"],
            "sd_prefix": config["sd"]["prefix"], "expected_identity": context["identity"],
            "ram_root_verified": False, "hardware_validated": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Realtek 固定 SD RAM 救援的離線來源產生／配置核對")
    parser.add_argument("action", choices=("source", "check"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        config = deploy.load({"path": args.config, "sha256": args.config_sha256})
        result = vendor_source(config) if args.action == "source" else validate_artifacts(config)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"status": "blocked", "reason": "固定救援來源、配置或資格不符"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
