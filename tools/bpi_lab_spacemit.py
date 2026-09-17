#!/usr/bin/env python3
"""K1 extlinux 與 K3 原廠 MMC 啟動鏈離線解析；不執行 FSBL／SBI／ESOS。"""

from pathlib import Path
import re
import struct
import zlib

if __package__:
    from . import bpi_lab_extlinux as core
else:
    import bpi_lab_extlinux as core


POLICIES = {
    "bpi-cm6": "spacemit-k1-cm6-legacy", "bpi-f3": "spacemit-k1-f3-current",
    "bpi-sm10": "spacemit-k3-sm10-current",
}
validate_template = core.validate_template
SDK_UBOOT = Path("/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0/bsp-src/uboot-2022.10")
SDK_SOURCES = {
    "board/spacemit/k3/k3.c": "a329a2aa305cb6a1294c86354e4660fdee122d16da2566f697d298bbddd7cee7",
    "board/spacemit/k3/k3.env": "5cbd9f9aaa57485e4910245b775d708534ec198cdf46af6b18ecb83b2ddc97ad",
}
DEFAULT_ENV_SHA256 = "e73d2c0c44c6b00019a4c6190e8e7d03a37be710a20fa76b887bdd098fa1ff51"


def parse_vendor_environment(blob):
    """解析此 SDK 的非冗餘 CRC32 環境；依 himport_r 保留後寫值並記錄覆寫。"""
    core.require(len(blob) == 0x4000 and struct.unpack_from("<I", blob)[0] == zlib.crc32(blob[4:]),
                 "vendor_env_crc", "K3 原廠環境長度或 CRC32 不符")
    payload = blob[4:]
    end = payload.find(b"\0\0")
    core.require(end >= 0 and (not payload[end + 2:].strip(b"\0") or not payload[end + 2:].strip(b"\xff")),
                 "vendor_env_format", "K3 原廠環境結尾或填補無效")
    values, duplicates = {}, []
    for entry in payload[:end].split(b"\0"):
        key, sep, value = entry.decode("utf-8").partition("=")
        core.require(sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), "vendor_env_format", "K3 原廠環境鍵無效")
        if key in values:
            duplicates.append(key)
        values[key] = value
    return {"values": values, "overwritten_keys": duplicates, "crc_verified": True}


def _fstab_root(blob):
    roots = []
    for raw in blob.decode("utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        core.require(4 <= len(fields) <= 6, "fstab", "fstab 欄位數無效")
        if fields[1] == "/":
            core.require(re.fullmatch("UUID=" + core.UUID, fields[0]) and fields[2] == "ext4",
                         "root_uuid", "K3 MMC 分支須有唯一 UUID 的 ext4 根掛載，不猜 PARTUUID 對應")
            roots.append(fields[0][5:].lower())
    core.require(len(roots) == 1, "root_uuid", "fstab 缺少唯一根 UUID")
    return roots[0]


def _vendor_contract(e):
    for name, sha in SDK_SOURCES.items():
        data = (SDK_UBOOT / name).read_bytes()
        core.require(core.digest(data)["sha256"] == sha, "sdk_source", "K3 SDK 來源與已審閱版本不同")
        e.manifest["sources"][str(SDK_UBOOT / name)] = core.digest(data)
    data = e.source("packages/blobs/riscv64/spacemit-k3/bpi-sm10/env.bin")
    core.require(core.digest(data)["sha256"] == DEFAULT_ENV_SHA256, "vendor_env_source", "K3 原廠環境不是受控候選")
    parsed = parse_vendor_environment(data)
    e.save("files/vendor-default-env.bin", data)
    e.manifest["files"]["vendor_default_env"] = {"evidence_path": "files/vendor-default-env.bin", "origin": "reviewed-source", **core.digest(data)}
    config = e.source("packages/blobs/riscv64/spacemit-k3/bpi-sm10/uboot.config")
    core.require(b"CONFIG_RSA_VERIFY=y" not in config and b"CONFIG_CMD_BOOTI=y\n" in config,
                 "vendor_secure_build", "K3 來源配置會跳過外部環境匯入或缺少 booti")
    return parsed


def _k3(e, p, release):
    defaults = e.check("vendor_default_environment", lambda: _vendor_contract(e))
    source = e.source("packages/blobs/riscv64/spacemit-k3/bpi-sm10/env_k3.txt")
    original = e.get("/boot/env_k3.txt", "vendor_env", required=True, maximum=core.MAX_TEXT)
    if original is not None:
        e.check("vendor_env", lambda: core.require(original == source, "vendor_env", "K3 環境不同於已核對原廠來源"))
        # dtb_env 是原廠命令字串，只記錄，不以 shell 或 U-Boot 執行。
        env = e.check("vendor_env_parse", lambda: core.binary._env(original))
        if env is not None:
            e.manifest["original_env"] = env
        e.manifest["entry"] = {"kind": "vendor-env", "path": "/boot/env_k3.txt", "sha256": core.digest(original)["sha256"]}
    core.components(e, p, release, "/boot/Image", "/boot/initramfs-generic.img")
    generic = e.cache.get("/boot/initramfs-generic.img")
    uinitrd = e.get("/boot/uInitrd", "initrd_vendor_alias", required=True)
    if generic is not None:
        e.check("vendor_initrd_alias", lambda: core.require(generic[0] == uinitrd, "initrd_alias", "K3 initramfs-generic.img 必須等同家族複製的 uInitrd"))
    e.get("/boot/dtb/" + p["dtb"], "dtb", required=True, maximum=16 * 1024**2)
    e.manifest["runtime_requirements"].update(
        boot_mount="/boot", knl_name="Image", ramdisk_name="initramfs-generic.img", dtb_name="dtb/" + p["dtb"],
        vendor_default_env_sha256=DEFAULT_ENV_SHA256, boot_device="mmc", boot_devname="mmc",
        boot_devnum="external-review", bootfs_partition_name="bootfs", rootfs_partition_name="rootfs",
        bootfs_guid="external-review", rootfs_guid="external-review", rsa_verify_build_enabled=False,
        secure_boot_state="external-review", esp_grub_absent=True, boot_override="",
        boot_mode="sdcard-or-emmc", board_fdt_fixups="original-firmware",
        chosen_merge="environment-precedes-dtb", fdt_addr="paired-kernel-dtb",
    )
    fstab = e.get("/etc/fstab", "fstab", required=True, maximum=core.MAX_TEXT)
    if fstab is not None:
        root = e.check("fstab_root", lambda: _fstab_root(fstab))
        e.manifest["root_uuid"] = root
        e.manifest["root_uuid_source"] = "/etc/fstab"
        e.manifest["runtime_requirements"]["rootfs_filesystem_uuid"] = root
    e.manifest["overlay_order"] = []
    core.overlays(e, p, [])
    if defaults is not None and original == source:
        env = {**defaults["values"], **e.manifest.get("original_env", {})}
        # 只展開已固定摘要的 MMC 非 GRUB 分支，絕不以一般 shell 解譯器執行環境。
        args = ["earlycon=sbi", "earlyprintk", "plymouth.ignore-serial-consoles", "plymouth.prefer-fbcon", "splash",
                "clk_ignore_unused", "random.trust_bootloader=1", "console=" + env["console"], "loglevel=" + env["loglevel"],
                "rootwait", "rootfstype=ext4", "root=PARTUUID=${rootfs_guid}", "bootfs=PARTUUID=${bootfs_guid}", "boot_mode=${boot_mode}"]
        chosen = e.manifest["checks"].get("dtb", {}).get("chosen_bootargs")
        if chosen:
            additions = chosen.split()
            core.require(all(re.fullmatch(core.binary.TOKEN, a) for a in additions), "vendor_chosen", "K3 /chosen/bootargs 語法尚未支援")
            original_keys = {a.split("=", 1)[0] for a in args}
            args += [a for a in additions if a.split("=", 1)[0] not in original_keys]
        e.manifest["bootargs_template"] = args
        e.manifest["vendor_route"] = ["import_env_from_bootfs", "autoboot", "mmc_boot", "commonargs", "add_bootarg",
                                       "set_mmc_args", "get_esp_index", "boot_kernel", "set_root_arg", "detect_dtb",
                                       "loadknl", "loaddtb", "loadramdisk", "start_kernel", "booti", "board_fdt_chosen_bootargs"]
        for name in ("kernel_addr_r", "fdt_addr_r", "ramdisk_addr_r"):
            e.manifest["runtime_requirements"][name] = env[name]
    if e.get("/boot/EFI/BOOT/BOOTRISCV64.EFI", "grub_efi") is not None:
        e.block("vendor_grub", "K3 存在 GRUB EFI 載荷，須解析其後續入口，不能轉選 MMC raw Image 分支")
    unused = e.get("/boot/armbianEnv.txt", "inactive_armbian_env", maximum=core.MAX_TEXT)
    if unused is not None:
        e.manifest["inactive_files"] = [{"path": "/boot/armbianEnv.txt",
            "reason": "已核對 SDK _load_env_from_blk 只匯入 env_k3.txt；受控 autoboot／mmc_boot 分支不讀此檔。",
            **core.digest(unused)}]
    for name in ("/boot/extlinux/extlinux.conf", "/boot/boot.scr"):
        if e.get(name, "alternate_" + str(len(e.cache)), maximum=core.MAX_TEXT) is not None:
            e.block("vendor_entry", "K3 額外入口與原廠環境的互動尚未核定：" + name)
    e.manifest["limitations"] += [
        "K3 已解析受控 env.bin 的 MMC 非 GRUB 原配分支；PARTUUID 仍由原廠 part number／part uuid 取得，不改成檔案系統 UUID。",
        "fstab UUID 是映像宣告；外部範本還須核定 bootfs／rootfs 的 GPT 名稱、GUID 與根檔案系統 UUID 對應。",
        "獨立 ESP 是否有 GRUB、持久環境是否符合候選、boot_mode 與 DT 記憶體／MAC 修正須由磁碟及實板配對證據核定。",
        "CONFIG_RSA_VERIFY 分支會停用外部環境匯入；此解析只適用受控非 RSA 建置，不推論熔絲或實板安全狀態。",
        "FSBL、ESOS、OpenSBI、FIT 驗章、反回滾及授權仍待原廠鏈審閱；不改寫前置韌體，也不套 K1 的 sysboot 契約。",
    ]


def prepare(read_file, *, board="bpi-f3", kernel_release, output):
    return core.prepare_adapter(read_file, board=board, kernel_release=kernel_release, output=output,
                                policies=POLICIES, handler=_k3 if board == "bpi-sm10" else core.extlinux,
                                family="spacemit")
