#!/usr/bin/env python3
"""K3 原廠 MMC 與固定 SD RAM 救援；核對實際來源、唯讀命令與一次性交接。"""

from __future__ import annotations

import copy
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zlib

if __package__:
    from . import bpi_lab_amlogic as containers
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_extlinux as core
    from . import bpi_lab_image as image
    from . import bpi_lab_original_entry as original
    from . import bpi_lab_spacemit as spacemit
    from . import bpi_lab_uboot as uboot
else:
    import bpi_lab_amlogic as containers
    import bpi_lab_deploy as deploy
    import bpi_lab_extlinux as core
    import bpi_lab_image as image
    import bpi_lab_original_entry as original
    import bpi_lab_spacemit as spacemit
    import bpi_lab_uboot as uboot


SCHEMA = "bpi-lab-k3-runtime-v1"
ABI = "spacemit-k3-lab-v1"
BOARDS = {"bpi-sm10"}
QUALIFICATION_SCHEMA = "bpi-lab-k3-qualification-v1"
RESCUE_SCHEMA = "bpi-lab-k3-ram-components-v1"
BASE_KEYS = original.BASE_KEYS
EXTRA_KEYS = original.EXTRA_KEYS + ["purpose"]
ASSETS = Path(__file__).with_name("bpi_lab_k3")
SDK_COMMIT = "1b10c8119e1a9b5451a4236f6b384f7c91eed1e2"
CONTAINER = "sha256:9531810450c5953c0675515ab7deee5d9634966e8abccfcd5ea60f8aee94e335"
SDK_HOST = spacemit.SDK_UBOOT.parents[1] / "output/k3/host"
RAM_BASE = 0x102000000
BUILD_PINS = {
    "binary": "5122ab8e6150f3ecc0a67092c139aedaa08c608061bd7bdf7eea5662e415f65f",
    "config": "9f9f74698df1bd48a914c491bd5a18a0979d33287c472542c6306c69c3e010e9",
}
TOOLCHAIN_PINS = {
    "bin/toolchain-wrapper": "cb03a477581b0c581d1c683a78b9ef8d269af4db828836417a9f80fa11f77748",
    "opt/ext-toolchain/bin/riscv64-unknown-linux-gnu-gcc": "7357a5d6e1197ca48da9db6e8a2f7a09784f3b6bb9163acfe213d191ee30bb2d",
    "opt/ext-toolchain/bin/riscv64-unknown-linux-gnu-as": "e9a3d6dfe15a0d77f511717e49de932e85af0181723a8fdca42250fc8c2a436a",
    "opt/ext-toolchain/bin/riscv64-unknown-linux-gnu-ld": "bd34f442bb076b589aa9474271641df2f776630cc7c4e0ec41a10c7817b85733",
    "opt/ext-toolchain/bin/riscv64-unknown-linux-gnu-objcopy": "285b4768e784ff100dc94450ef89b37c7c8341c4f3fb036247dc5022efda4688",
    "opt/ext-toolchain/libexec/gcc/riscv64-unknown-linux-gnu/15.2.0/cc1": "d23621e9bd0ae073060f1bb8c7678b0064006e857db1e9b7fdeffca9f944d6cb",
}
SOURCE_PINS = {
    **spacemit.SDK_SOURCES, **original.K3_ABI_SOURCES,
    "drivers/misc/spacemit_k1x_efuse.c": "0304ef7281946ec043852f24286be0183a6af341301b2fa34fc2d1b201f3c858",
    "include/configs/k3.h": "dc748514d44d95f3a141a7c322100cb24fd63ce0c9f1d3814f59a393c5920462",
    "cmd/booti.c": "dc430e68f3d362354ca5c1c0cf2e65dfba77c831fcccf68ab310f27a71fdcbfb",
    "arch/riscv/lib/image.c": "1e1ee60d443bb3299ad53f4382e579e5dde8bd6ddb4607870994fc7093efea0f",
    "fs/fs.c": "7bdb0375fa09ecf7932c1b634c96ea8a400a1666876023322376b88dc4c89728",
}
COMMAND_KEYS = "commonargs set_console set_loglevel add_bootarg set_mmc_args set_root_arg get_rootfs_guid get_bootfs_guid detect_dtb get_esp_index mmc_rootfstype esp_name grub_file".split()
FIXUP_KEYS = "part# wifi_addr bt_addr ethaddr eth1addr eth2addr eth3addr serial#".split()
REVIEWS = original.REVIEWS + ["lcs_external_boot_authorized", "fsbl_esos_sbi_chain", "early_init_readonly", "ram_rescue_readonly"]
REQUIRED_CONFIG = "CONFIG_TARGET_SPACEMIT_K3 CONFIG_RISCV CONFIG_64BIT CONFIG_RISCV_SMODE CONFIG_CMD_BOOTI CONFIG_CMD_MMC CONFIG_CMD_PART CONFIG_CMD_FS_UUID CONFIG_CMD_IMPORTENV CONFIG_CMD_HASH CONFIG_SHA256 CONFIG_LMB CONFIG_OF_BOARD_SETUP CONFIG_FDT_SIMPLEFB CONFIG_ENV_IS_NOWHERE CONFIG_USE_DEFAULT_ENV_FILE CONFIG_HUSH_PARSER CONFIG_SPACEMIT_K1X_EFUSE CONFIG_FS_EXT4 CONFIG_FS_FAT CONFIG_GZIP".split()
DISABLED_CONFIG = "CONFIG_RSA_VERIFY CONFIG_SPL_RSA_VERIFY CONFIG_DDR_TRAINING_SAVE_RESTORE CONFIG_CMD_SAVEENV CONFIG_ENV_IS_IN_MMC CONFIG_ENV_IS_IN_UFS CONFIG_ENV_IS_IN_MTD CONFIG_ENV_IS_IN_FAT CONFIG_ENV_IS_IN_EXT4 CONFIG_ENV_IS_IN_NFS CONFIG_BOOT_FROM_USB_DISK".split()
RESCUE_PATHS = {"kernel": "/boot/bpi-lab/k3/Image", "initrd": "/boot/bpi-lab/k3/initrd",
                "dtb": "/boot/bpi-lab/k3/board.dtb", "kernel_config": "/boot/bpi-lab/k3/config"}
LIMITS = "僅觀察已配對 UART 核心標記；不證明 Linux 根媒體、短測或實板安全鏈。原配 Linux 交接後可能寫入 eMMC。"
Error = uboot.UBootError
require = uboot.require


def _json(blob):
    def unique(pairs):
        values = {}
        for key, value in pairs:
            require(key not in values, "JSON 欄位重複")
            values[key] = value
        return values
    return json.loads(blob.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(Error("JSON 常數無效")))


def source_contract(sdk_root=None):
    """逐檔核對既定 SDK，不將外部來源中的文字或摘要當作授權。"""
    root = spacemit.SDK_UBOOT if sdk_root is None else deploy.path(str(sdk_root))
    return {name: core.digest(deploy.checked_bytes({"path": str(root / name), "sha256": sha}, 4 * 1024**2))
            for name, sha in SOURCE_PINS.items()}


def default_environment():
    path = core.ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env.bin"
    blob = deploy.checked_bytes({"path": str(path), "sha256": spacemit.DEFAULT_ENV_SHA256}, 0x4000)
    return spacemit.parse_vendor_environment(blob)["values"]


def build_environment():
    """原環境逐鍵保留；只將救援 U-Boot 的自動啟動延遲設為停用。"""
    values = {**default_environment(), "bootdelay": "-1"}
    require(all("\n" not in key + value and "\r" not in value for key, value in values.items()), "原環境不適用逐行建置格式")
    return ("\n".join(key + "=" + value for key, value in values.items()) + "\n").encode()


def _projection(c):
    base = copy.deepcopy({"schema": uboot.SCHEMA, **{key: c[key] for key in BASE_KEYS}})
    base["uboot"]["abi"] = "mainline-v2025.01"
    base["files"]["kernel"]["address"] = base["files"]["kernel"]["entry"]
    return base


def _extra_slots(c):
    slots = [c[key] for key in ("entry", "work", "decompression") if c[key] is not None]
    if c["files"]["kernel"]["address"] != c["files"]["kernel"]["entry"]:
        slots.append(c["files"]["kernel"])
    return slots


def validate_config(config):
    """只核對型別、用途、唯讀來源及 RAM 配置；外部證據另行讀取。"""
    uboot._keys(config, "schema " + " ".join(BASE_KEYS + EXTRA_KEYS), "K3 配置")
    c = copy.deepcopy(config)
    require(c["schema"] == SCHEMA and c["board"] in BOARDS and c["arch"] == "riscv64", "K3 schema／板型／架構不符")
    require(c["purpose"] in ("original", "sd-rescue") and c["uboot"]["abi"] == ABI, "K3 用途或 ABI 不符")
    require(c["mmc"] == {"sd": 0, "emmc": 2}, "SDK 主程式固定 SD 0／eMMC 2，不沿用 SPL 的 eMMC 1")
    require(c["files"]["kernel"]["format"] == "Image" and c["files"]["initrd"] is not None,
            "K3 最小執行分支限原 Image 與明示 initrd，不猜 FIT 或缺檔回退")
    address = uboot._address(c["files"]["kernel"]["address"], 64)
    base = uboot.validate_config(_projection(c))
    c.update({key: base[key] for key in BASE_KEYS})
    c["uboot"]["abi"], c["files"]["kernel"]["address"] = ABI, address
    require(len(c["ram"]["banks"]) == 1 and c["ram"]["banks"][0]["start"] == RAM_BASE,
            "K3 主程式 RAM 必須排除前 32 MiB 的 ESOS／SBI 保留區，不沿用 SPL 基址")
    require(c["source"]["type"] == "mmc" and c["source"]["device"] == c["mmc"]["emmc" if c["purpose"] == "original" else "sd"],
            "K3 用途與已配對 MMC 來源不同")
    deploy.identifier(c["hardware_id"])
    require(uboot._match(core.UUID, c["root_uuid"]) and c["root_uuid"] == c["root_uuid"].lower(), "檔案系統 UUID 無效")
    uboot._keys(c["root_source"], "partition partuuid uuid", "根來源")
    uboot._source({"type": "mmc", "device": c["source"]["device"], **{key: c["root_source"][key] for key in ("partition", "partuuid")}})
    require(c["root_source"]["uuid"] == c["root_uuid"], "根來源 UUID 不符")
    uboot._keys(c["components"], "manifest artifact_root extraction", "組件證據")
    for ref in (c["pairing"], c["qualification"], c["components"]["manifest"], c["components"]["extraction"]):
        original._reference(ref)
    root = deploy.path(c["components"]["artifact_root"])
    require(deploy.path(c["components"]["manifest"]["path"]) == root / "manifest.json", "manifest 不在指定證據目錄")
    for name in ("pairing", "qualification"):
        require(c[name]["sha256"] == c["uboot"][name + "_sha256"], "K3 核定參照摘要矛盾")
    uboot._keys(c["authorization"], "record one_shot customer_boot_may_write_emmc", "一次性授權")
    deploy.identifier(c["authorization"]["record"])
    require(c["authorization"]["one_shot"] is True
            and c["authorization"]["customer_boot_may_write_emmc"] is (c["purpose"] == "original"), "用途與持久根交接授權不符")
    if c["purpose"] == "original":
        require(type(c["entry"]) is dict and c["entry"].get("kind") == "vendor-env", "缺少原廠文字環境入口")
        uboot._keys(c["entry"], "kind path bytes sha256 address capacity", "K3 原環境")
        require(c["entry"]["path"] == "/env_k3.txt" and 1 <= c["entry"]["bytes"] < 0x4000
                and uboot._match(r"[0-9a-f]{64}", c["entry"]["sha256"]), "K3 原環境路徑或長度無效")
    else:
        require(c["entry"] is None and c["work"] is None and c["root_source"] == {
            "partition": c["source"]["partition"], "partuuid": c["source"]["partuuid"], "uuid": c["root_uuid"]}, "RAM 救援只讀固定 SD，不執行原環境")
        require([arg for arg in c["bootargs"] if arg.startswith("root=")] == ["root=/dev/ram0"]
                and "rdinit=/init" in c["bootargs"] and "ro" in c["bootargs"], "RAM 救援不得選持久根或其他 init")
    for name in ("entry", "work", "decompression"):
        item = c[name]
        if item is None:
            continue
        if name != "entry":
            uboot._keys(item, "address capacity", "K3 工作區")
        item["address"] = uboot._address(item["address"], 64)
        uboot._integer(item["capacity"], 4096, 512 * 1024**2, "K3 工作區容量")
        if name == "entry":
            require(item["capacity"] > item["bytes"], "原環境缺少限長讀取防護")
    regions = [c["ram"]["kernel_work"]] + [uboot._slot(c["files"][role]) for role in ("initrd", "dtb")]
    regions += [uboot._slot(item) for item in _extra_slots(c)]
    for region in regions:
        uboot._span(region, 64)
        require(uboot._contains(c["ram"]["boot"], region), "K3 載入／搬移範圍不在 boot RAM")
    uboot._disjoint(regions, "K3 載入／解壓／搬移區")
    require(not any(uboot._overlap(a, b) for a in regions for b in c["ram"]["reserved"]), "K3 工作區撞到保留區")
    return c


def scope_digest(config):
    return original.scope_digest(config)


def lifecycle_view(config):
    """投影只供生命週期身分與 RAM 檢查；保留 vendor ABI，禁止用投影執行。"""
    c = validate_config(config)
    view = uboot.validate_config(_projection(c))
    view["uboot"]["abi"] = ABI
    return view


def _rescue_wrapper(blob):
    require(blob.startswith(b"#!/usr/bin/python3 -B\n"), "救援 wrapper 解譯器不同")
    tree = ast.parse(blob.decode("utf-8"))
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
        tree.body.pop(0)
    expected = ast.parse("import bpi_rescue_runtime as runtime\nruntime.SCHEMA = 'bpi-lab-rescue-v1'\nif __name__ == '__main__':\n    raise SystemExit(runtime.main())\n")
    require(ast.dump(tree) == ast.dump(expected), "救援 wrapper 必須只切換共用 schema 並呼叫受控 main")


def _rescue_archive(blob, release):
    """先沿用嚴格 newc 校驗，再讀取實際 init、程式及身分，不執行封裝內容。"""
    if not blob.startswith((b"070701", b"070702")):
        blob, tail = containers._uncompress(blob, 512 * 1024**2)
        require(not tail.strip(b"\0"), "救援 initrd 含第二個壓縮串流")
    releases = set()
    end, _ = containers._cpio(blob, 0, releases)
    require(not blob[end:].strip(b"\0") and releases == {release}, "救援 cpio 含額外封裝或核心模組版本不符")
    files, offset = {}, 0
    while offset < end:
        fields = [int(blob[i:i + 8], 16) for i in range(offset + 6, offset + 110, 8)]
        start = offset + 110
        name = blob[start:start + fields[11] - 1].decode("utf-8")
        start = (start + fields[11] + 3) & ~3
        data = blob[start:start + fields[6]]
        offset = (start + fields[6] + 3) & ~3
        if name == "TRAILER!!!":
            break
        name = name.removeprefix("./")
        require(name not in files and "//" not in name and "." not in name.split("/")[1:], "救援 cpio 路徑重複或不正規")
        files[name] = (fields[1], fields[4], data)
    assets = core.ROOT / "tools/bpi_h618_rescue"
    expected = {"init": "init", "usr/sbin/bpi_rescue_runtime.py": "runtime.py",
                "usr/sbin/bpi-rescue-ssh": "ssh-start", "usr/sbin/bpi-rescue-udhcpc": "udhcpc-script",
                "usr/sbin/bpi_rescue_cli.py": "bpi_rescue_cli.py"}
    # 不允許透過父目錄符號連結或 hardlink 改寫已核對的執行檔。
    for path, asset in {**expected, "usr/sbin/bpi-rescue": None}.items():
        require(path in files, "救援封裝缺少固定程式：" + path)
        mode, links, data = files[path]
        require(mode & 0xf000 == 0x8000 and (mode & 0o111 or path.endswith(".py")) and links == 1,
                "救援程式型別不符：" + path)
        if asset is None:
            _rescue_wrapper(data)
        else:
            require(data == (assets / asset).read_bytes(), "救援程式內容不符：" + path)
        for parent in Path(path).parents:
            if str(parent) != "." and str(parent) in files:
                require(files[str(parent)][0] & 0xf000 == 0x4000, "救援程式父目錄不是目錄")
    require("etc/bpi-rescue.json" in files, "救援封裝缺少實際身分文件")
    mode, links, identity = files["etc/bpi-rescue.json"]
    require(mode & 0xf000 == 0x8000 and links == 1 and len(identity) < 4096, "救援身分檔案型別或長度不符")
    require(_json(identity) == {"schema": "bpi-lab-rescue-v1", "kernel": release}, "救援身分內容與核心不同，拒絕舊 H618 schema")
    for name in ("usr/bin/busybox", "usr/bin/python3"):
        require(name in files, "救援封裝缺少執行環境：" + name)
    return {"schema": "bpi-lab-rescue-v1", "kernel": release, "identity_sha256": hashlib.sha256(identity).hexdigest()}


def prepare_rescue(read_file, *, kernel_release, root_uuid, sd_prefix, output):
    """解析固定 SD RAM 救援組件；root_uuid 只標示 SD 檔案系統，不是 Linux 根。"""
    require(uboot._match(core.NAME, kernel_release) and uboot._match(core.UUID, root_uuid), "救援版本或 SD UUID 無效")
    require(type(sd_prefix) is bytes and len(sd_prefix) == 4 * 1024**2, "救援須提供實際 SD 前 4 MiB，不接受外部摘要替代內容")
    m = {"schema": RESCUE_SCHEMA, "board": "bpi-sm10", "kernel_release": kernel_release,
         "status": "blocked", "hardware_validated": False, "root_uuid": root_uuid,
         "files": {}, "reads": [], "sources": {}, "commands": [], "checks": {}, "blockers": [],
         "runtime_requirements": {"boot_mount": "/boot"}, "limitations": [], "sd_prefix": core.digest(sd_prefix),
         "media_prefix": "/bpi-lab/k3", "sd_prefix_evidence": "sd-prefix.bin"}
    e = core.Evidence(output, read_file, m)
    try:
        e.save("sd-prefix.bin", sd_prefix)
        p = e.check("profile", lambda: core.profile(e, "bpi-sm10", spacemit.POLICIES))
        data = {role: e.get(path, role, required=True) for role, path in RESCUE_PATHS.items()}
        if all(value is not None for value in data.values()) and p is not None:
            config = e.check("kernel_config", lambda: core.kernel_config(data["kernel_config"], "riscv64"))
            if config is not None:
                e.check("kernel", lambda: core.kernel(data["kernel"], "riscv64", kernel_release, config))
                require(config["values"].get("CONFIG_CMDLINE", '""') == '""', "救援核心含內建命令列")
            initrd = data["initrd"]
            m["initrd_format"] = "legacy" if initrd[:4] == b"\x27\x05\x19\x56" else "raw"
            if m["initrd_format"] == "legacy":
                initrd = core.legacy(initrd, arch="riscv64")
            rescue = e.check("rescue", lambda: _rescue_archive(initrd, kernel_release))
            if rescue:
                m["rescue"] = rescue
            e.check("dtb", lambda: core.dtb(e, "dtb", p))
        if not m["blockers"]:
            m["status"] = "prepared"
        e.save("manifest.json", deploy.encode(m))
    finally:
        os.close(e.fd)
    return m


def _components(c):
    refs = c["components"]
    claimed = deploy.load(refs["manifest"])
    with tempfile.TemporaryDirectory(prefix="bpi-k3-components-") as directory:
        root = Path(directory)
        with image.SnapshotReader(refs["extraction"]["path"], refs["extraction"]["sha256"], root / "extraction") as reader:
            if c["purpose"] == "original":
                actual = spacemit.prepare(reader.read_file, board="bpi-sm10", kernel_release=c["kernel_release"], output=root / "components")
            else:
                require(claimed.get("sd_prefix_evidence") == "sd-prefix.bin", "救援前綴證據路徑不同")
                prefix = deploy.checked_bytes({"path": str(deploy.path(refs["artifact_root"]) / "sd-prefix.bin"),
                                               "sha256": claimed["sd_prefix"]["sha256"]}, 4 * 1024**2)
                actual = prepare_rescue(reader.read_file, kernel_release=c["kernel_release"], root_uuid=c["root_uuid"],
                                        sd_prefix=prefix, output=root / "components")
            require(actual["status"] == "prepared", "原始擷取重新解析未通過：" + str(actual["blockers"]))
            for key in ("schema", "board", "kernel_release", "root_uuid", "files", "checks", "runtime_requirements"):
                require(actual[key] == claimed.get(key), "外部組件宣告與實際內容不同：" + key)
            if c["purpose"] == "original":
                for key in ("entry", "original_env", "overlay_order", "bootargs_template"):
                    require(actual[key] == claimed.get(key), "外部原入口宣告與內容不同：" + key)
            else:
                require(all(actual[key] == claimed.get(key) for key in ("rescue", "sd_prefix", "media_prefix", "sd_prefix_evidence")), "救援身分或固定前綴不符")
            extraction = reader.original
            data = {role: reader.read_file(item["path"]) for role, item in actual["files"].items() if "path" in item}
    original._dtb_memory(actual["checks"]["dtb"], c)
    require(actual["root_uuid"] == c["root_uuid"] == extraction["filesystem_uuid"], "SD／原配根 UUID 與實際檔案系統不同")
    bootpart = extraction.get("boot_partition", extraction["partition"])
    for part, target in ((bootpart, c["source"]), (extraction["partition"], c["root_source"])):
        require(part["index"] == target["partition"] and part["partuuid"] == target["partuuid"], "實際擷取分割區與目標 MMC 路徑不符")
    targets = {**c["files"], **({"vendor_env": c["entry"]} if c["purpose"] == "original" else {})}
    for role, item in targets.items():
        record = actual["files"][role]
        require(item["path"] == original._media_path(record["path"], "/boot")
                and all(item[key] == record[key] for key in ("bytes", "sha256"))
                and extraction["files"][record["path"]].get("volume_index", bootpart["index"]) == bootpart["index"],
                "K3 載入檔案、摘要或分割區不符：" + role)
        deploy.checked_bytes({"path": str(deploy.path(refs["artifact_root"]) / record["evidence_path"]), "sha256": record["sha256"]}, 512 * 1024**2)
    require(c["files"]["initrd"]["format"] == actual["initrd_format"], "initrd 容器與實際內容不同")
    raw, kernel = data["kernel"], actual["checks"]["kernel"]
    require(kernel["format"] == "Image", "K3 vendor runtime 不轉選未核定 FIT 分支")
    if kernel.get("compression"):
        require(kernel["compression"] == "gzip" and c["decompression"] is not None, "gzip 核心缺少解壓區")
        raw, tail = core.binary._decompress(raw)
        require(not tail and len(raw) <= c["files"]["kernel"]["bytes"] * 10
                and c["decompression"]["capacity"] >= c["files"]["kernel"]["bytes"] * 10, "SDK booti 十倍解壓界限不符")
    else:
        require(c["decompression"] is None, "未壓縮核心不應附加解壓區")
    uboot._header(raw[:64], {**c["files"]["kernel"], "bytes": len(raw)}, "kernel", c)
    require(c["files"]["kernel"]["capacity"] >= kernel["image_size"], "核心暫存區未涵蓋 BSS 與搬移來源")
    if c["purpose"] == "original":
        require(not actual["overlay_order"], "vendor overlay 尚未核定，不靜默忽略")
        req = actual["runtime_requirements"]
        for role, key in (("kernel", "kernel_addr_r"), ("initrd", "ramdisk_addr_r"), ("dtb", "fdt_addr_r")):
            require(c["files"][role]["address"] == int(req[key], 16), "原廠載入位址被改寫：" + key)
        substitutions = {"${rootfs_guid}": c["root_source"]["partuuid"], "${bootfs_guid}": c["source"]["partuuid"], "${boot_mode}": "sdcard"}
        args = actual["bootargs_template"]
        for key, value in substitutions.items():
            args = [arg.replace(key, value) for arg in args]
        require(c["bootargs"] == args, "K3 原環境或 chosen 合併語意被改寫")
    else:
        for role in c["files"]:
            require(c["files"][role]["path"] == RESCUE_PATHS[role][5:], "SD RAM 救援路徑不是固定前綴")
        require(bootpart["index"] == extraction["partition"]["index"], "SD RAM 救援不得借用另一個根分割區")
        # 救援仍保留 SDK chosen 合併；不允許內嵌 DTB 加入另一個 init 或持久根。
        chosen = actual["checks"]["dtb"].get("chosen_bootargs") or ""
        require(not any(a.split("=", 1)[0] in ("init", "rdinit", "root", "rw") for a in chosen.split()), "救援 DTB 含未核定根或 init")
        require("boot_mode=sdcard" in c["bootargs"] and "rw" not in c["bootargs"]
                and not any(a.startswith("init=") for a in c["bootargs"]), "救援 boot_mode 或唯讀根不符")
        keys = {a.split("=", 1)[0] for a in c["bootargs"]}
        require(all(a.split("=", 1)[0] in keys for a in chosen.split()), "救援 bootargs 未明示 DTB 額外參數")
    return actual, extraction


def _memory(output, c, lcs):
    marker = uboot._one(r"BPI_K3_STATE abi=([a-z0-9.-]+) boot_mode=([a-z]+) rsa_verify=([01]) lcs=([0-9a-f]{8})", output, "K3 安全狀態")
    require(marker.groups() == (ABI, "sdcard", "0", lcs), "K3 ABI、SD 啟動或核定生命週期狀態不同")
    base = int(uboot._one(r"ram_base = 0x([0-9a-f]+)", output, "K3 RAM 起點")[1], 16)
    top = int(uboot._one(r"ram_top = 0x([0-9a-f]+)", output, "K3 RAM 終點")[1], 16)
    require(base == c["ram"]["banks"][0]["start"] and base < top
            and c["ram"]["boot"]["start"] + c["ram"]["boot"]["size"] <= top, "SDK booti 搬移基址或 RAM 上限不符")
    actual = uboot._memory(output, _projection(c))
    require(not any(uboot._overlap(span, uboot._slot(slot)) for span in actual for slot in _extra_slots(c)), "K3 LMB 撞到額外載入／解壓區")
    for field in ("relocaddr", "sp start", "fdt_blob", "new_fdt"):
        value = int(uboot._one(re.escape(field) + r" = 0x([0-9a-f]+)", output, "K3 工作區")[1], 16)
        require(not any(slot["address"] <= value < slot["address"] + slot["capacity"] for slot in _extra_slots(c)), "K3 gd 工作區撞到載入暫存")
    bottom = int(uboot._one(r"video_bottom = 0x([0-9a-f]+)", output, "顯示保留區")[1], 16)
    top = int(uboot._one(r"video_top = 0x([0-9a-f]+)", output, "顯示保留區")[1], 16)
    require(top >= bottom and (top == bottom or any(uboot._contains(span, {"start": bottom, "size": top - bottom}) for span in c["ram"]["reserved"])),
            "ft_board_setup 的 framebuffer 未列入核定保留區")
    return actual


def _identity(output, kind, c, pairing):
    match = uboot._one(r"BPI_K3_MMC kind=(sd|emmc) device=([0-9]+) bytes=([0-9a-f]+) cid=([0-9a-f]{32})", output, "完整 MMC 身分")
    expected = pairing["protected_sd" if kind == "sd" else "emmc"]
    require(match[1] == kind and int(match[2]) == c["mmc"][kind] and int(match[3], 16) == expected["bytes"]
            and match[4] == expected["cid"], "實際 MMC 類型、完整 CID 或容量與配對不同")


def _gpt(output, device):
    rows = []
    for line in uboot._lines(output):
        if not line.startswith("BPI_K3_PART"):
            continue
        match = re.fullmatch(r"BPI_K3_PART index=([0-9]+) name=([0-9a-f]+) guid=(" + core.UUID + r") start=([0-9a-f]+) sectors=([0-9a-f]+)", line)
        require(match is not None, "GPT 列格式未知")
        rows.append({"index": int(match[1]), "name": bytes.fromhex(match[2]).decode("ascii"), "partuuid": match[3].lower(),
                     "start": int(match[4], 16), "sectors": int(match[5], 16)})
    tail = uboot._one(r"BPI_K3_GPT device=([0-9]+) count=([0-9]+)", output, "完整 GPT 清單")
    require(int(tail[1]) == device and int(tail[2]) == len(rows) and 1 <= len(rows) <= 128, "GPT 表不完整")
    require(len({row["index"] for row in rows}) == len(rows) and len({row["name"] for row in rows}) == len(rows)
            and len({row["partuuid"] for row in rows}) == len(rows), "GPT 名稱、索引或 GUID 重複")
    for row in rows:
        require(1 <= row["index"] <= 128 and row["sectors"] > 0 and row["start"] > 0, "GPT 區間無效")
    uboot._disjoint([{"start": row["start"], "size": row["sectors"]} for row in rows], "GPT 區間")
    return rows


def _dtb_fit(blob):
    require(len(blob) >= 64 and blob[:4] == b"\xd0\x0d\xfe\xed", "救援控制 DTB FIT 標頭無效")
    total = struct.unpack_from(">I", blob, 4)[0]
    require(64 <= total < len(blob) and total % 8 == 0, "控制 DTB FIT 外部資料起點無效")
    tree = core._fit_tree(blob[:total])
    require(set(tree) == {"/", "/images", "/images/firmware-1", "/images/firmware-1/hash-1", "/images/fdt-1",
                          "/images/fdt-1/hash-1", "/configurations", "/configurations/conf-1"}, "控制 FIT 不是既定單組配置")
    require(tree["/configurations"] == {"default": b"conf-1\0"}, "控制 FIT 預設配置不同")
    require(all(tree["/configurations/conf-1"].get(k) == v for k, v in
                (("firmware", b"firmware-1\0"), ("loadables", b"firmware-1\0"), ("fdt", b"fdt-1\0"))), "控制 FIT 參照不同")
    cursor, payloads = total, []
    for name, kind in (("firmware-1", b"firmware\0"), ("fdt-1", b"flat_dt\0")):
        props = tree["/images/" + name]
        require(props.get("arch") == b"riscv\0" and props.get("compression") == b"none\0" and props.get("type") == kind
                and "data" not in props and "data-position" not in props, "控制 FIT 類型或壓縮不同")
        require(all(len(props.get(key, b"")) == 4 for key in ("data-offset", "data-size")), "控制 FIT 外部範圍無效")
        start = total + int.from_bytes(props["data-offset"], "big")
        size = int.from_bytes(props["data-size"], "big")
        require(start == cursor and 64 <= size <= len(blob) - start, "控制 FIT 外部資料截斷或重疊")
        payload = blob[start:start + size]
        require(payload[:4] == b"\xd0\x0d\xfe\xed" and struct.unpack_from(">I", payload, 4)[0] == size, "控制 FIT 載荷不是完整 DTB")
        require(tree["/images/" + name + "/hash-1"] == {"algo": b"crc32\0", "value": struct.pack(">I", zlib.crc32(payload))}, "控制 DTB 的原封裝 CRC 不符")
        cursor = (start + size + 7) & ~7
        require(not blob[start + size:cursor].strip(b"\0"), "控制 FIT 對齊區不為零")
        payloads.append(payload)
    require(cursor == len(blob) and payloads[0] == payloads[1], "控制 FIT 含未核對尾段或不同 DTB")


def _firmware(firmware, c):
    deploy.fields(firmware, "binary elf config source patch environment dtb build")
    blobs = {key: deploy.checked_bytes(ref, 64 * 1024**2) for key, ref in firmware.items() if key != "build"}
    require(all(hashlib.sha256(blobs[key]).hexdigest() == sha for key, sha in BUILD_PINS.items()),
            "韌體不是已真編譯核對的固定建置；外部報告不能自我宣告新的可執行碼")
    require(blobs["source"] == (ASSETS / "bpi_lab_k3.c").read_bytes()
            and blobs["patch"] == (ASSETS / "readonly-sdk.patch").read_bytes()
            and blobs["environment"] == build_environment(), "救援建置的 C 來源、唯讀修補或原環境不同")
    source_contract()
    values = original._kconfig(blobs["config"])
    require(all(values.get(key) == "y" for key in REQUIRED_CONFIG)
            and all(values.get(key, "n") == "n" for key in DISABLED_CONFIG)
            and all(value != "y" for key, value in values.items() if key.startswith("CONFIG_ENV_IS_IN_")),
            "K3 建置缺少守門功能或仍含持久環境／自動燒錄設定")
    require(values.get("CONFIG_BOOTDELAY") == "-1" and values.get("CONFIG_SYS_CBSIZE", "").isdecimal()
            and int(values["CONFIG_SYS_CBSIZE"]) >= c["uboot"]["line_limit"], "救援自動啟動或命令容量不符")
    if "version" in c["uboot"]:
        require(c["uboot"]["version"].encode("ascii") + b"\0" in blobs["binary"], "核定完整版本行不在真 U-Boot binary")
    elf = blobs["elf"]
    require(len(elf) > 64 and elf[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", elf, 18)[0] == 243,
            "建置 ELF 不是 RISC-V 64 位元小端")
    with tempfile.TemporaryDirectory(prefix="bpi-k3-elf-") as directory:
        raw = Path(directory) / "uboot.bin"
        elf_path = Path(directory) / "uboot.elf"
        elf_path.write_bytes(elf)
        symbols = subprocess.run(["riscv64-linux-gnu-nm", "--defined-only", str(elf_path)],
                                 check=True, capture_output=True, timeout=30).stdout.decode("ascii")
        for name in ("do_bpi_k3", "do_booti", "board_fdt_chosen_bootargs", "ft_board_setup", "efuse_reload", "sha256_csum_wd"):
            require(re.search(r"(?m)^[0-9a-f]+ [a-zA-Z] " + name + r"$", symbols), "建置 ELF 缺少實際連結符號：" + name)
        subprocess.run(["riscv64-linux-gnu-objcopy", "--gap-fill=0xff", "-O", "binary", str(elf_path), str(raw)],
                       check=True, capture_output=True, timeout=30)
        require(raw.read_bytes() + blobs["dtb"] == blobs["binary"], "U-Boot binary 不是實際 ELF 與 DTB FIT 的封裝")
    _dtb_fit(blobs["dtb"])
    report = deploy.load(firmware["build"])
    require(report.get("schema") == "bpi-lab-k3-build-v1" and report.get("compiled") is True
            and report.get("sdk_commit") == SDK_COMMIT and report.get("container") == CONTAINER
            and report.get("source_contract") == source_contract(), "K3 真建置證據與 SDK 來源不同")
    for key, blob in blobs.items():
        require(report["artifacts"].get(key) == core.digest(blob), "真建置與核定產物不同：" + key)
    # 命令／設定／來源與產物均重新讀取；建置日誌不是獨立的執行或實板證明。
    for ref in report["logs"]:
        deploy.checked_bytes(ref, 16 * 1024**2)


def _qualification(c):
    pairing = deploy.load(c["pairing"])
    deploy.fields(pairing, "schema approved record hardware_id resources uart power emmc protected_sd rescue")
    require(pairing["schema"] == "bpi-lab-pairing-v1" and pairing["approved"] is True
            and pairing["hardware_id"] == c["hardware_id"], "K3 配對未核定本板")
    deploy.identifier(pairing["record"])
    for name in ("emmc", "protected_sd"):
        deploy.fields(pairing[name], "cid bytes controller")
        require(uboot._match(r"[0-9a-f]{32}", pairing[name]["cid"]), "配對 CID 無效")
        uboot._integer(pairing[name]["bytes"], 1024**2, 2**50, "配對媒體容量")
        require(type(pairing[name]["controller"]) is str and bool(pairing[name]["controller"]), "配對控制器缺失")
    require(pairing["emmc"]["cid"] != pairing["protected_sd"]["cid"], "SD 與 eMMC CID 相同")
    q = deploy.load(c["qualification"])
    deploy.fields(q, "schema abi approved record hardware_id purpose scope_sha256 pairing_sha256 firmware observations review security gpt fixups")
    require(q["schema"] == QUALIFICATION_SCHEMA and q["abi"] == ABI and q["approved"] is True
            and q["hardware_id"] == c["hardware_id"] and q["purpose"] == c["purpose"]
            and q["scope_sha256"] == scope_digest(c) and q["pairing_sha256"] == c["pairing"]["sha256"], "K3 資格未綁定本配置、用途或配對")
    deploy.identifier(q["record"])
    deploy.fields(q["review"], " ".join(REVIEWS))
    require(all(q["review"][key] is True for key in REVIEWS), "唯讀救援／安全鏈審閱未完整核定")
    _firmware(q["firmware"], c)
    security = deploy.load(q["security"])
    deploy.fields(security, "schema approved record hardware_id firmware_sha256 lcs external_kernel_authorized chain_evidence")
    require(security["schema"] == "bpi-lab-k3-security-v1" and security["approved"] is True
            and security["hardware_id"] == c["hardware_id"] and security["firmware_sha256"] == q["firmware"]["binary"]["sha256"]
            and security["external_kernel_authorized"] is True and uboot._match(r"[0-9a-f]{8}", security["lcs"]),
            "安全鏈未核定外部核心或完整生命週期字組；不推論零值就是停用 secure boot")
    deploy.identifier(security["record"])
    deploy.fields(security["chain_evidence"], "fsbl esos sbi lifecycle")
    for ref in security["chain_evidence"].values():
        require(deploy.checked_bytes(ref, 32 * 1024**2), "安全鏈原始證據為空")
    deploy.fields(q["fixups"], " ".join(FIXUP_KEYS))
    for value in q["fixups"].values():
        require(value is None or uboot._match(r"[A-Za-z0-9:_.-]{1,128}", value), "板級修正環境值無效")
    proof = deploy.load(q["observations"])
    deploy.fields(proof, "schema hardware_id firmware_sha256 boot_origin reset version_output state_output sd_cid_output emmc_cid_output")
    require(proof["schema"] == "bpi-lab-k3-observation-v1" and proof["hardware_id"] == c["hardware_id"]
            and proof["firmware_sha256"] == q["firmware"]["binary"]["sha256"] and proof["boot_origin"] == "sd" and proof["reset"] == "cold",
            "缺少同份 U-Boot 的 SD 冷啟動觀測")
    uboot._check({"check": "version"}, proof["version_output"].encode("ascii"), c)
    _memory(proof["state_output"].encode("ascii"), c, security["lcs"])
    _identity(proof["sd_cid_output"].encode("ascii"), "sd", c, pairing)
    if c["purpose"] == "original":
        _identity(proof["emmc_cid_output"].encode("ascii"), "emmc", c, pairing)
        require(type(q["gpt"]) is list and 1 <= len(q["gpt"]) <= 128, "原入口缺少完整 GPT 名稱核定")
        rows = q["gpt"]
        for name, target in (("bootfs", c["source"]), ("rootfs", c["root_source"])):
            selected = [row for row in rows if row.get("name") == name]
            require(len(selected) == 1 and selected[0]["index"] == target["partition"]
                    and selected[0]["partuuid"] == target["partuuid"], "原廠 GPT 名稱／GUID 與來源不同：" + name)
        for row in rows:
            deploy.fields(row, "index name partuuid start sectors")
            require(type(row["name"]) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,36}", row["name"])
                    and uboot._match(core.UUID, row["partuuid"]), "GPT 名稱或 GUID 不正規")
            uboot._integer(row["index"], 1, 128, "GPT 索引")
            for key in ("start", "sectors"):
                uboot._integer(row[key], 1, pairing["emmc"]["bytes"] // 512, "GPT 範圍")
            require((row["start"] + row["sectors"]) * 512 <= pairing["emmc"]["bytes"], "GPT 超出配對媒體")
        require(all(len({row[key] for row in rows}) == len(rows) for key in ("index", "name", "partuuid")), "核定 GPT 有重複欄位")
        uboot._disjoint([{"start": row["start"], "size": row["sectors"]} for row in rows], "核定 GPT")
    else:
        require(proof["emmc_cid_output"] is None and q["gpt"] is None, "固定 SD 救援資格不讀取 eMMC")
    return pairing, q, security


def _steps(c, m, pairing, q, security):
    steps = []
    def add(command, check="status", **fields):
        steps.append({"command": command, "check": check, **fields})
    def envcheck(key, value):
        require(uboot._match(r"[A-Za-z0-9_#]+", key), "環境鍵含命令字元")
        add("bpi_k3 env " + key, "k3-env", variable=key, value=value)
    def setting(key, value):
        require(uboot._match(r"[A-Za-z0-9_]+", key) and (value is None or uboot._match(r"[A-Za-z0-9_./,:=+@% -]+", value)), "設定值含未核定字元")
        add("setenv " + key + (" " + value if value is not None else ""))
        envcheck(key, value)
    def load(role, item):
        add(f"bpi_k3 load {role} {c['source']['device']} {c['source']['partition']} {item['address']:x} {item['bytes']:x} {item['capacity']:x} {item['sha256']} {item['path']}",
            "k3-hash", role=role, item=item)
    def identity(kind):
        add(f"bpi_k3 mmc {kind} {c['mmc'][kind]}", "k3-cid", kind=kind, pairing=pairing)
    add("version", "version")
    for name in ("bpi_k3", "booti", "hash", "setenv", "part", "fsuuid", "test", "run"):
        add("help " + name)
    add("bpi_k3 state", "k3-memory", lcs=security["lcs"])
    add("bpi_k3 begin " + security["lcs"])
    identity("sd")
    if c["purpose"] == "sd-rescue":
        add("bpi_k3 prefix 0", "k3-prefix", expected=m["sd_prefix"])
    source, root = c["source"], c["root_source"]
    devpart = f"{source['device']:x}:{source['partition']:x}"
    if c["purpose"] == "original":
        identity("emmc")
        add(f"bpi_k3 envsha {c['mmc']['emmc']}", "k3-envsha")
        add(f"bpi_k3 gpt {c['mmc']['emmc']}", "k3-gpt", expected=q["gpt"])
    add("part uuid mmc " + devpart, "k3-line", expected=source["partuuid"])
    add(f"part uuid mmc {source['device']:x}:{root['partition']:x}", "k3-line", expected=root["partuuid"])
    add(f"fsuuid mmc {source['device']:x}:{root['partition']:x}", "k3-line", expected=c["root_uuid"])
    add(f"hash sha256 {c['files']['kernel']['address']:x} 0", "sha256-probe")
    settings = {"autostart": "no", "verify": "yes", "boot_override": None,
                "bootm_low": f"{c['ram']['boot']['start']:x}", "bootm_size": f"{c['ram']['boot']['size']:x}",
                "bootm_mapsize": f"{c['ram']['boot']['size']:x}", "fdt_high": "ffffffffffffffff", "initrd_high": "ffffffffffffffff",
                "kernel_comp_addr_r": f"{c['decompression']['address']:x}" if c["decompression"] else None,
                "kernel_comp_size": f"{c['files']['kernel']['bytes']:x}" if c["decompression"] else None,
                "devtype": "mmc", "devnum": f"{source['device']:x}", "boot_devnum": f"{source['device']:x}",
                "boot_device": "mmc", "boot_devname": "mmc", "boot_mode": "sdcard", "fdt_addr": f"{c['files']['dtb']['address']:x}"}
    for role, name in (("kernel", "kernel_addr_r"), ("initrd", "ramdisk_addr_r"), ("dtb", "fdt_addr_r")):
        settings[name] = f"{c['files'][role]['address']:x}"
    for key, value in settings.items():
        setting(key, value)
    for key in FIXUP_KEYS:
        envcheck(key, q["fixups"][key])
    if c["purpose"] == "original":
        defaults = default_environment()
        for key in COMMAND_KEYS:
            envcheck(key, defaults[key])
        load("env", c["entry"])
        add("bpi_k3 import", "k3-hash", role="env", item=c["entry"])
        for key, value in m["original_env"].items():
            envcheck(key, value)
        # 原廠 get_esp_index 的缺 ESP 分支不視為成功命令；依已核對完整 GPT 選同一索引。
        esp = next((row["index"] for row in q["gpt"] if row["name"] == defaults["esp_name"]), source["partition"])
        setting("esp_index", f"0x{esp:x}")
        for part in sorted({source["partition"], esp}):
            add(f"if test -e mmc {source['device']:x}:{part:x} /EFI/BOOT/BOOTRISCV64.EFI; then false; else true; fi", "k3-absent")
        for command in ("commonargs", "add_bootarg", "set_mmc_args", "set_root_arg", "detect_dtb"):
            add("run " + command)
        for key, value in (("rootfs_part", f"0x{root['partition']:x}"), ("bootfs_part", f"0x{source['partition']:x}"),
                           ("rootfs_guid", root["partuuid"]), ("bootfs_guid", source["partuuid"]),
                           ("knl_name", "Image"), ("ramdisk_name", "initramfs-generic.img"), ("dtb_name", "dtb/spacemit/k3-bananapi-sm10.dtb")):
            envcheck(key, value)
        # 原 SDK 先生成環境參數，booti 才附加 boot_mode 與未覆寫的 chosen 參數。
        preargs = c["bootargs"][:c["bootargs"].index("boot_mode=sdcard")]
    else:
        preargs = [arg for arg in c["bootargs"] if not arg.startswith("boot_mode=")]
        setting("bootargs", " ".join(preargs))
    envcheck("bootargs", " ".join(preargs))
    for role, item in c["files"].items():
        load(role, item)
    identity("sd")
    if c["purpose"] == "original":
        identity("emmc")
    else:
        add("bpi_k3 prefix 0", "k3-prefix", expected=m["sd_prefix"])
    add("bpi_k3 state", "k3-memory", lcs=security["lcs"])
    for key in FIXUP_KEYS:
        envcheck(key, q["fixups"][key])
    envcheck("bootargs", " ".join(preargs))
    for role, item in c["files"].items():
        add("bpi_k3 hash " + role, "k3-hash", role=role, item=item)
    add("bpi_k3 boot " + c["purpose"], "kernel-marker")
    return steps


def _check(step, output, c):
    name = step["check"]
    if name == "k3-memory":
        _memory(output, c, step["lcs"])
    elif name == "k3-cid":
        _identity(output, step["kind"], c, step["pairing"])
    elif name == "k3-gpt":
        require(_gpt(output, c["source"]["device"]) == step["expected"], "實際完整 GPT 表與核定不同")
    elif name == "k3-envsha":
        match = uboot._one(r"BPI_K3_ENV_STORAGE device=([0-9]+) offset=a0000 bytes=4000 sha256=([0-9a-f]{64})", output, "持久環境內容")
        require(int(match[1]) == c["mmc"]["emmc"] and match[2] == spacemit.DEFAULT_ENV_SHA256, "eMMC 持久環境不是受控原配內容")
    elif name == "k3-prefix":
        match = uboot._one(r"BPI_K3_PREFIX device=0 bytes=400000 sha256=([0-9a-f]{64})", output, "實際 SD 前 4 MiB")
        require(step["expected"]["bytes"] == 4 * 1024**2 and match[1] == step["expected"]["sha256"], "SD 保護前綴與實際內容不同")
    elif name == "k3-env":
        match = uboot._one(r"BPI_K3_ENV key=([A-Za-z0-9_#]+) present=([01]) bytes=([0-9a-f]+) sha256=([0-9a-f]{64})", output, "實際環境值")
        value = step["value"]
        blob = b"" if value is None else value.encode("ascii")
        require(match[1] == step["variable"] and int(match[2]) == (value is not None) and int(match[3], 16) == len(blob)
                and match[4] == hashlib.sha256(blob).hexdigest(), "實際環境內容、存在狀態或長度不同：" + step["variable"])
    elif name == "k3-hash":
        match = uboot._one(r"BPI_K3_HASH role=(kernel|initrd|dtb|env) bytes=([0-9a-f]+) sha256=([0-9a-f]{64})", output, "實際有界載入 SHA-256")
        require(match[1] == step["role"] and int(match[2], 16) == step["item"]["bytes"] and match[3] == step["item"]["sha256"], "載入角色、實收長度或 SHA-256 不符")
    elif name == "k3-line":
        require([line for line in uboot._lines(output) if line] == [step["expected"]], "實際 UUID 與目標不同")
    elif name == "k3-absent":
        require(not any(uboot._lines(output)), "GRUB 缺檔探測含錯誤輸出，不當成不存在")
    else:
        raise Error("未知 K3 回應守門")


def validate_artifacts(config, artifact_root=None):
    """完整讀取核定來源、ELF、原始擷取與封裝內容；不傳送任何硬體命令。"""
    c = validate_config(config)
    if artifact_root is not None:
        require(deploy.path(str(artifact_root)) == deploy.path(c["components"]["artifact_root"]), "呼叫端組件目錄不同")
    try:
        pairing, q, security = _qualification(c)
        manifest, extraction = _components(c)
        if c["purpose"] == "original":
            for part in (extraction["partition"], extraction.get("boot_partition", extraction["partition"])):
                row = next(row for row in q["gpt"] if row["index"] == part["index"])
                require(row["start"] == part.get("start_lba", part.get("start")) and row["sectors"] == part.get("sectors"),
                        "核定 GPT 區間與原始擷取不同")
        steps = _steps(c, manifest, pairing, q, security)
        require(all(len(uboot._wire(step["command"], "0" * 16)) <= c["uboot"]["line_limit"] for step in steps), "K3 命令超過核定緩衝區")
    except (KeyError, TypeError, AttributeError, UnicodeError, StopIteration) as exc:
        raise Error("K3 證據缺欄位、型別或文字格式錯誤") from exc
    return {"config": c, "steps": steps, "artifacts_verified": True, "hardware_validated": False}


def build_uboot_config(template, *, artifact_root):
    """後端沿用原入口呼叫簽章；回傳 K3 schema，禁止交給共用 booti 執行器。"""
    return validate_artifacts(template, artifact_root)["config"]


def backend_binding(config):
    result = validate_artifacts(config)
    c = result["config"]
    return {"driver": SCHEMA, "config": c, "lifecycle_view": lifecycle_view(c), "pairing_sha256": c["pairing"]["sha256"],
            "firmware_roles": ["uboot"], "hardware_validated": False, "requires_runtime_dispatch": True}


def render(config):
    result = validate_artifacts(config)
    return {"schema": "bpi-lab-k3-render-v1", "executed": False, "hardware_validated": False,
            "config_sha256": uboot._digest(result["config"]), "steps": result["steps"], "limits": LIMITS}


def boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic):
    """單次已配對 console 交接；守門失敗停止，不重試、不 saveenv、不操作電源。"""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800, "K3 總期限無效")
    require(records is None or type(records) is list, "records 必須是陣列")
    end = monotonic() + timeout
    result = validate_artifacts(config)
    remaining = end - monotonic()
    require(remaining > 0, "K3 本機核對逾時，未傳送命令")
    records = [] if records is None else records
    c = result["config"]
    runner = uboot._Runner(console, c, records, remaining, monotonic)
    runner.at_prompt()
    for step in result["steps"]:
        if not step["check"].startswith("k3-"):
            runner.execute(step)
            continue
        runner.execute({**step, "check": "status"})
        record = records[-1]
        record["check"] = step["check"]
        try:
            _check(step, record["output"].encode("ascii"), c)
        except (ValueError, UnicodeError) as exc:
            record.update(status="failed", reason=str(exc))
            raise Error(str(exc)) from exc
    return {"schema": "bpi-lab-k3-result-v1", "status": "kernel-marker-observed", "purpose": c["purpose"],
            "config_sha256": uboot._digest(c), "kernel_release": c["kernel_release"], "hardware_validated": False,
            "root_verified": False, "smoke_verified": False, "limits": LIMITS}


def audit_build(directory, *, logs):
    """核對私有建置產物與來源，產生軟體證據；不授予冷啟動或安全鏈資格。"""
    root = deploy.path(str(directory))
    config_digest, _ = uboot._read_regular(root / "build/.config", 1024**2)
    config = (root / "build/.config").read_bytes()
    require(core.digest(config) == config_digest, "建置設定讀取時變動")
    image.save(root, "uboot.config", config)
    names = {"binary": "build/u-boot.bin", "elf": "build/u-boot", "config": "uboot.config",
             "source": "source/cmd/bpi_lab_k3.c", "patch": "readonly-sdk.patch",
             "environment": "source/cmd/bpi_lab_k3.env", "dtb": "build/fit-dtb.blob"}
    refs, digests = {}, {}
    for key, name in names.items():
        digests[key], _ = uboot._read_regular(root / name, 64 * 1024**2)
        refs[key] = {"path": str(root / name), "sha256": digests[key]["sha256"]}
    require((root / names["source"]).read_bytes() == (ASSETS / "bpi_lab_k3.c").read_bytes()
            and (root / names["patch"]).read_bytes() == (ASSETS / "readonly-sdk.patch").read_bytes(), "私有建置來源不是目前受控實作")
    report = {"schema": "bpi-lab-k3-build-v1", "compiled": True, "hardware_validated": False,
              "sdk_commit": SDK_COMMIT, "container": CONTAINER, "source_contract": source_contract(),
              "artifacts": digests, "logs": [], "firmware": refs,
              "limitations": ["真 BSP 編譯與連結通過不代表 SD 已可冷開機；FSBL、ESOS、SBI 及生命週期另行核定。",
                              "原 SDK 與宿主 glibc 未修改；不包含簽署、燒錄或實板操作。"]}
    for log in logs:
        name = deploy.path(str(log))
        digest, _ = uboot._read_regular(name, 16 * 1024**2)
        report["logs"].append({"path": str(name), "sha256": digest["sha256"]})
    # 私有 board 修補也逐位元組核對，不僅記錄呼叫過 patch。
    with tempfile.TemporaryDirectory(prefix="bpi-k3-patch-") as directory:
        testroot = Path(directory)
        target = testroot / "board/spacemit/k3/k3.c"
        target.parent.mkdir(parents=True)
        target.write_bytes(deploy.checked_bytes({"path": str(spacemit.SDK_UBOOT / "board/spacemit/k3/k3.c"),
                                                "sha256": SOURCE_PINS["board/spacemit/k3/k3.c"]}))
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(ASSETS / "readonly-sdk.patch")], cwd=testroot,
                       capture_output=True, check=True, timeout=30)
        require(target.read_bytes() == (root / "source/board/spacemit/k3/k3.c").read_bytes(), "私有 SDK 板級修補內容不同")
        report["patched_board"] = core.digest(target.read_bytes())
    image.save_json(root, "build-manifest.json", report)
    refs = {**refs, "build": {"path": str(root / "build-manifest.json"), "sha256": hashlib.sha256((root / "build-manifest.json").read_bytes()).hexdigest()}}
    _firmware(refs, {"uboot": {"line_limit": 2048}})
    image.save_json(root, "firmware.json", refs)
    return report


def build_rescue_uboot(*, output, sdk_root=None, toolchain_root=None):
    """只在全新私有目錄真編譯 SDK；固定既有官方容器，不升級宿主或修改 SDK。"""
    sdk = spacemit.SDK_UBOOT if sdk_root is None else deploy.path(str(sdk_root))
    host = SDK_HOST if toolchain_root is None else deploy.path(str(toolchain_root))
    source_contract(sdk)
    for name, expected in TOOLCHAIN_PINS.items():
        actual, _ = uboot._read_regular(host / name, 256 * 1024**2)
        require(actual["sha256"] == expected, "SDK 工具鏈內容變動：" + name)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=sdk, capture_output=True, check=True).stdout.decode().strip()
    require(commit == SDK_COMMIT, "SDK 提交不是受控版本")
    root = deploy.path(str(output))
    require(not root.exists(), "建置目錄已存在，不覆寫證據")
    root.mkdir(parents=True)
    (root / "source").mkdir()
    archive = root / "sdk-source.tar"
    with archive.open("xb") as stream:
        subprocess.run(["git", "archive", SDK_COMMIT], cwd=sdk, stdout=stream, check=True, timeout=120)
    subprocess.run(["tar", "--no-same-owner", "-xf", str(archive), "-C", str(root / "source")], check=True, timeout=120)
    shutil.copyfile(ASSETS / "bpi_lab_k3.c", root / "source/cmd/bpi_lab_k3.c")
    shutil.copyfile(ASSETS / "readonly-sdk.patch", root / "readonly-sdk.patch")
    (root / "source/cmd/bpi_lab_k3.env").write_bytes(build_environment())
    with (root / "source/cmd/Makefile").open("ab") as stream:
        stream.write(b"\nobj-$(CONFIG_CMD_MMC) += bpi_lab_k3.o\n")
    subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(root / "readonly-sdk.patch")], cwd=root / "source",
                   capture_output=True, check=True, timeout=30)
    docker = ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--read-only", "--tmpfs", "/tmp",
              "--user", f"{os.getuid()}:{os.getgid()}", "--pids-limit", "512", "--memory", "4g", "--cpus", "4",
              "--env", "SOURCE_DATE_EPOCH=1777390324",
              "--mount", f"type=bind,src={host},dst=/sdk,readonly", "--mount", f"type=bind,src={root},dst=/work",
              "--workdir", "/work/source", "--entrypoint", "make", CONTAINER,
              "O=../build", "CROSS_COMPILE=/sdk/bin/riscv64-unknown-linux-gnu-"]
    logs = []
    def make(name, args):
        path = root / name
        logs.append(path)
        with path.open("xb") as stream:
            completed = subprocess.run(docker + args, stdout=stream, stderr=subprocess.STDOUT, timeout=1200)
        require(completed.returncode == 0, "K3 真建置失敗，保留日誌：" + str(path))
    make("configure.log", ["k3_defconfig"])
    config = [str(root / "source/scripts/config"), "--file", str(root / "build/.config")]
    for name in REQUIRED_CONFIG:
        config += ["--enable", name.removeprefix("CONFIG_")]
    for name in DISABLED_CONFIG:
        config += ["--disable", name.removeprefix("CONFIG_")]
    config += ["--set-str", "DEFAULT_ENV_FILE", "/work/source/cmd/bpi_lab_k3.env", "--set-val", "BOOTDELAY", "-1",
               "--set-val", "SYS_CBSIZE", "2048"]
    subprocess.run(config, check=True, timeout=30)
    make("olddefconfig.log", ["olddefconfig"])
    make("build.log", ["-j4", "u-boot.bin"])
    return audit_build(root, logs=logs)
