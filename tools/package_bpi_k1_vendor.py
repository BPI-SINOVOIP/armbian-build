#!/usr/bin/env python3
"""建立獨立 SD GPT 映像與 Titan eMMC 套件；僅操作候選一般檔案。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
import zlib

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from prepare_bpi_k1_vendor_rootfs import CM6_CAMERA_PACKAGES, camera_package_records
from bpi_k1_board_targets import expected_board

MIB = 1024 * 1024
DTBS = {"bpi-cm6": "k1-x_bpi_cm6.dtb", "bpi-f3": "k1-bananapi-f3.dtb"}
CM6_CONNECTIVITY_DTB = "k1-x_bpi_cm6-eth0-reset.dtb"
CM6_CONNECTIVITY_PATH = "/bpi-k1-vendor/" + CM6_CONNECTIVITY_DTB
CM6_CONNECTIVITY_SHA256 = "f0b9795fda72868cd2bc9a6db0cf5aa2f15ea494f114a5b42adfc1fa931962c1"
CM6_CAMERA_DTB = "k1-x_bpi_cm6-dual-imx415.dtb"
CM6_CAMERA_PATH = "/bpi-k1-vendor/" + CM6_CAMERA_DTB
CM6_CAMERA_SHA256 = "91bb7a9cde6380e16d9dbc12161212aad768cf145de79dd2f51665366e0065c3"
# 固定兩個新目錄建置一致的正式載荷；來源變更必須重新審查此封裝契約。
CM6_CAMERA_DERIVED = {
    "version": "0.2.34+cm6.2", "size": 974380,
    "sha256": "813be3b38d5bd3d86cae88c234bd7560e6f8b5b78cc8f9cf42fdba9ed0855027",
    "source_lock_sha256": "3ad11b5f5a9a2af3beab6c2976b53dc07a96568fc4370e815ab6816d8df5f324",
    "builder_sha256": "a20a36f26fe269bdb64a5811547ec93d1323f51391665ba02c52d6ad1e09b096",
    "library_sha256": "c962d8f724507cacb7366517f4366246c3d8f2d8b419e134d62f10118b2bd14e",
}
PARTS = [("fsbl", 128*1024, 256*1024, "factory/FSBL.bin"),
         ("env", 384*1024, 64*1024, "env.bin"),
         ("opensbi", MIB, MIB, "fw_dynamic.itb"),
         ("uboot", 2*MIB, 2*MIB, "u-boot.itb"),
         ("bootfs", 4*MIB, 256*MIB, "bootfs.ext4"),
         ("rootfs", 260*MIB, None, "rootfs.ext4")]


def release_id(value):
    if not re.fullmatch(r"[0-9]{8}-rc[1-9][0-9]*", value):
        raise ValueError("候選版本須為 YYYYMMDD-rcN，不可包含路徑或沿用舊正式檔名")
    return value


def digest(path):
    with Path(path).open("rb") as f:
        return digest_stream(f)


def digest_stream(stream, count=None):
    h = hashlib.sha256()
    left = count
    while left is None or left > 0:
        block = stream.read(8*MIB if left is None else min(left, 8*MIB))
        if not block:
            if left not in (None, 0):
                raise ValueError("映像內容過短")
            break
        h.update(block)
        if left is not None:
            left -= len(block)
    return h.hexdigest()


def run(argv, codes=(0,), **kw):
    r = subprocess.run(list(map(str, argv)), **kw)
    if r.returncode not in codes:
        raise RuntimeError(f"命令失敗：{argv[0]}，退出碼 {r.returncode}")
    return r


def regular(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"只允許一般檔案：{path}")
    return path


def child_file(base, name):
    name = str(name)
    relative = Path(name)
    if not name or "\\" in name or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("檔案路徑超出指定目錄")
    path = Path(base) / relative
    if not path.resolve().is_relative_to(Path(base).resolve()):
        raise ValueError("檔案連結超出指定目錄")
    return regular(path)


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def boot_dtb_path(board, cm6_connectivity=False, cm6_dual_imx415=False, native=False):
    if board not in DTBS:
        raise ValueError("不支援的板型")
    if cm6_connectivity and board != "bpi-cm6":
        raise ValueError("CM6 網路修正不能套用其他板型")
    if native:
        if cm6_dual_imx415 and board != "bpi-cm6":
            raise ValueError("雙 IMX415 選項只允許 BPI-CM6")
        return "/dtb/spacemit/" + DTBS[board]
    if cm6_dual_imx415:
        if board != "bpi-cm6":
            raise ValueError("雙 IMX415 選項只允許 BPI-CM6")
        return CM6_CAMERA_PATH
    return CM6_CONNECTIVITY_PATH if cm6_connectivity else "/dtb/spacemit/" + DTBS[board]


def extlinux(board, root_uuid, cm6_connectivity=False, cm6_dual_imx415=False, native=False):
    dtb_path = boot_dtb_path(board, cm6_connectivity, cm6_dual_imx415, native)
    root_uuid = str(uuid.UUID(root_uuid))
    return ("DEFAULT armbian\nTIMEOUT 20\nLABEL armbian\n"
            "  KERNEL /Image\n  INITRD /uInitrd\n"
            f"  FDT {dtb_path}\n"
            f"  APPEND root=UUID={root_uuid} rootwait rootfstype=ext4 rw "
            "earlycon=sbi console=tty1 console=ttyS0,115200 loglevel=4 "
            "fsck.repair=yes net.ifnames=0\n")


def native_target_contract(identity, board, storage):
    """核對新增板名的媒體邊界，保留未含別名的歷史原生紀錄。"""
    armbian_board = identity.get("armbian_board")
    if armbian_board is not None and not expected_board(armbian_board, board, storage):
        raise ValueError("官方格式板名與封裝板型或媒體不符")


def native_dtb_contract(identity, board, path=None):
    """原生 DTB 使用本次編譯身分及語義，絕不套用歷史候選 SHA 常數。"""
    if identity.get("source_kind") != "armbian-native-rootfs" or identity.get("board") != board or identity.get("schema_version") != 2:
        raise ValueError("原生來源或板型身分不符")
    camera = identity.get("camera_profile")
    if camera not in ("none", "dual-imx415") or (board != "bpi-cm6" and camera != "none"):
        raise ValueError("原生相機配置不符板型")
    record = identity.get("native_dtb", {})
    if record.get("path") != boot_dtb_path(board, native=True) or not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")):
        raise ValueError("原生 DTB 路徑或 SHA-256 身分不符")
    if camera == "dual-imx415":
        verify_camera_packages(identity)
    if path is not None:
        if digest(regular(path)) != record["sha256"]:
            raise ValueError("原生 DTB 與本次編譯 SHA-256 不符")
        if board == "bpi-cm6":
            import bpi_cm6_native_dtb
            if bpi_cm6_native_dtb.validate(path, camera) != record.get("validation"):
                raise ValueError("原生 DTB 語義與匯出紀錄不符")
    elif board == "bpi-cm6":
        validation = record.get("validation", {})
        if (validation.get("board"), validation.get("camera_profile"), validation.get("dtb", {}).get("sha256")) != (board, camera, record["sha256"]):
            raise ValueError("原生 DTB 缺少一致的語義驗證紀錄")
    return record


def boot_inventory(root):
    from bpi_k1_native import tree_inventory
    result = tree_inventory(root)
    # 根系統內 /boot 絕對連結在獨立 boot-tree 中會改為等價的相對連結。
    for name, entry in result.items():
        if entry["type"] == "symlink" and entry["target"].startswith("/boot/"):
            entry["target"] = os.path.relpath(entry["target"][6:], str(Path(name).parent))
    return result


def prepared_contract(prepared, prep, board, camera_option=None):
    schema = prep.get("schema_version", 1)
    native = schema == 2
    if schema not in (1, 2) or (not native and prep.get("identity", {}).get("source_kind") == "armbian-native-rootfs"):
        raise ValueError("不支援或混用的 prepared 結構版本")
    if native:
        record = native_dtb_contract(prep["identity"], board)
        dual = prep["identity"]["camera_profile"] == "dual-imx415"
        if camera_option is not None and camera_option != dual:
            raise ValueError("明確相機選項與原生 prepared 配置不一致")
        if not prep.get("boot_tree") or boot_inventory(prepared / "boot-tree") != prep["boot_tree"]:
            raise ValueError("原生 boot-tree 已偏離匯出內容")
        path = child_file(prepared / "boot-tree", record["path"].lstrip("/"))
        native_dtb_contract(prep["identity"], board, path)
    else:
        dual = bool(camera_option)
    return native, dual


def layout(root_size, emmc=False):
    if root_size <= 0 or root_size % 4096:
        raise ValueError("根分區大小必須為正數且按 4096 位元組對齊")
    result = json.loads((REPO / "config/spacemit-k1-vendor/partition_universal.json").read_text())
    # Titan 的 flash bootinfo 會重建 eMMC 開機區標頭，保留原廠通用表的來源欄位。
    return result


def fastboot_config():
    source = (REPO / "config/spacemit-k1-vendor/fastboot.yaml").read_text()
    official = "relate_partition: ['partition_{size0}.json', 'partition_{size1}.json']"
    if source.count(official) != 1:
        raise ValueError("官方燒錄範本結構已變更，需重新驗證 Titan 契約")
    # 一般 GUI 模式必須經變數匹配；固定檔名只適用明確指定分區表的另一條路徑。
    return source.replace(official, "relate_partition: ['partition_{size1}.json']")


def verify_titan_contract(archive):
    required = {"factory/FSBL.bin", "factory/bootinfo_sd.bin", "fw_dynamic.itb",
                "u-boot.itb", "env.bin", "bootfs.ext4", "rootfs.ext4",
                "fastboot.yaml", "partition_universal.json"}
    names = set(archive.namelist())
    if not required <= names:
        raise ValueError("Titan 套件缺少必要元件：" + ", ".join(sorted(required - names)))
    tables = {name for name in names if name.startswith("partition_") and name.endswith(".json")}
    if tables != {"partition_universal.json"}:
        raise ValueError("eMMC 候選不可包含未核對的額外分區表")
    actual = yaml.safe_load(archive.read("fastboot.yaml"))
    expected = yaml.safe_load(fastboot_config())
    if actual != expected:
        raise ValueError("Titan 控制流程不符合已核對的變數匹配契約；不可使用固定分區檔名")
    root_size = archive.getinfo("rootfs.ext4").file_size
    partition = json.loads(archive.read("partition_universal.json"))
    if partition != layout(root_size, emmc=True):
        raise ValueError("Titan 分區描述與官方通用表不符")
    return {"status": "passed", "scope": "required-members-and-official-control-contract",
            "hardware_validation": "pending"}


def check_payloads(directory):
    for _, _, maximum, name in PARTS:
        size = regular(directory / name).stat().st_size
        if not size or (maximum and size > maximum):
            raise ValueError(f"載荷超過分區或為空：{name}")
    for name in ("bootinfo_sd.bin", "bootinfo_emmc.bin"):
        data = regular(directory / "factory" / name).read_bytes()
        if len(data) != 80:
            raise ValueError("開機資訊必須為 80 位元組，不可覆蓋 GPT 保護 MBR")
        if struct.unpack_from("<I", data)[0] != 0xB00714F0 or zlib.crc32(data[:64]) != struct.unpack_from("<I", data, 64)[0]:
            raise ValueError("開機資訊標頭或 CRC32 不符")
    env = (directory / "env.bin").read_bytes()
    if len(env) != 16384 or zlib.crc32(env[4:]) != struct.unpack_from("<I", env)[0]:
        raise ValueError("官方環境映像長度或 CRC32 不符")


def source_reference(reference):
    record = json.loads(child_file(reference, "retrieval.json").read_text())
    config = REPO / "config/spacemit-k1-vendor"
    lock_path = child_file(config, "sources.lock.json")
    lock = json.loads(lock_path.read_text())
    for key in ("url", "archive_bytes"):
        if record[key] != lock[key]:
            raise ValueError("參考封裝來源與倉內來源鎖不符")
    entries = {item["path"]: item for item in record["files"]}
    if len(entries) != len(record["files"]) or set(entries) != {item["path"] for item in lock["files"]}:
        raise ValueError("參考元件清單有重複、缺少或額外項目")
    for item in lock["files"]:
        for key in ("size", "sha256", "crc32", "header_offset", "compressed_size"):
            if entries[item["path"]][key] != item[key]:
                raise ValueError(f"參考元件紀錄與來源鎖不符：{item['path']}")
        path = child_file(reference, item["path"])
        if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise ValueError(f"參考元件內容與來源鎖不符：{item['path']}")
    for item in lock.get("local_templates", []):
        if digest(child_file(config, item["path"])) != item["sha256"]:
            raise ValueError(f"官方格式範本雜湊不符：{item['path']}")
    record["sources_lock_sha256"] = digest(lock_path)
    return record


def filesystem_uuid(stream, offset=0):
    stream.seek(offset + 1024)
    superblock = stream.read(120)
    if len(superblock) != 120 or superblock[56:58] != b"\x53\xef":
        raise ValueError("找不到有效的 ext4 超級區塊")
    return str(uuid.UUID(bytes=superblock[104:120]))


def normalize_kernel(boot):
    """保留可由 bootm 啟動的 FIT；gzip 核心轉為 booti 可讀的 Image。"""
    image = boot / "Image"
    with image.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    if compressed:
        temporary = boot / "Image.vendor-new"
        with gzip.open(image, "rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        image.unlink()
        temporary.rename(image)
        image.chmod(0o644)
    with image.open("rb") as stream:
        header = stream.read(64)
    kind = "raw"
    if header[:4] == b"\xd0\x0d\xfe\xed":
        details = subprocess.check_output(["dumpimage", "-l", str(image)], text=True)
        if "FIT description:" not in details or "Architecture: RISC-V" not in details or "Type:         Kernel Image" not in details:
            raise ValueError("FIT 不是合法的 RISC-V 核心")
        kind = "fit"
    elif len(header) != 64 or header[56:60] != b"RSC\x05":
        raise ValueError("核心不是合法的 RISC-V 開機 Image")
    if image.stat().st_size > 0x0c200000 - 0x08000000:
        raise ValueError("核心超過已驗證的官方載入記憶體區間")
    return {"source_was_gzip": compressed, "format": kind, "image_size": image.stat().st_size,
            "image_sha256": digest(image)}


def prepare_cm6_connectivity(boot, output):
    """只在官方格式的開機樹副本新增獨立 DTB，保留套件原檔。"""
    source = child_file(boot, "dtb/spacemit/" + DTBS["bpi-cm6"]).resolve()
    target = boot / CM6_CONNECTIVITY_PATH.lstrip("/")
    target.parent.mkdir(exist_ok=True)
    manifest = output / "cm6-ethernet-dtb.json"
    run([sys.executable, REPO / "tools/bpi_cm6_ethernet_dtb.py", "--source", source,
         "--output", target, "--manifest", manifest], stdout=subprocess.DEVNULL)
    record = json.loads(manifest.read_text())
    if digest(regular(target)) != CM6_CONNECTIVITY_SHA256:
        raise ValueError("產生的 CM6 DTB 與已實機驗證候選不同")
    return record


def verify_camera_record(record):
    """相機紀錄必須同時固定 eth0 來源與已測雙相機候選。"""
    if (not isinstance(record, dict) or record.get("board") != "bpi-cm6" or
            not isinstance(record.get("source"), dict) or
            not isinstance(record.get("candidate"), dict) or
            record.get("source", {}).get("sha256") != CM6_CONNECTIVITY_SHA256 or
            record.get("candidate", {}).get("sha256") != CM6_CAMERA_SHA256 or
            record.get("candidate", {}).get("name") != CM6_CAMERA_DTB):
        raise ValueError("CM6 雙相機 DTB 紀錄的板型、路徑或 SHA-256 缺失／不符")


def verify_camera_packages(identity):
    """保留固定官方配套，另嚴格核對原生 CM6 的來源修正版。"""
    records = identity.get("cm6_camera_packages")
    if identity.get("board") != "bpi-cm6":
        raise ValueError("相機配套身分只適用 BPI-CM6")
    if records == CM6_CAMERA_PACKAGES or records == camera_package_records(None):
        return None
    if (identity.get("schema_version") != 2 or identity.get("source_kind") != "armbian-native-rootfs" or
            identity.get("camera_profile") != "dual-imx415"):
        raise ValueError("雙 IMX415 封裝缺少固定 CM6 相機配套身分，請以 --cm6-camera-cache 重新準備根系統")
    config = REPO / "config/spacemit-k1-camera"
    pinned = CM6_CAMERA_DERIVED
    if (digest(regular(config / "source-lock.json")) != pinned["source_lock_sha256"] or
            digest(regular(REPO / "tools/build_bpi_cm6_camera.py")) != pinned["builder_sha256"]):
        raise ValueError("相機配套身分的來源鎖或建置工具已變更")
    lock = json.loads((config / "source-lock.json").read_text())
    for item in lock["patches"]:
        if digest(child_file(config, item["path"])) != item["sha256"]:
            raise ValueError("相機配套身分的兩補丁內容不符來源鎖")
    if digest(child_file(config, lock["config"]["path"])) != lock["config"]["sha256"]:
        raise ValueError("相機配套身分的 mode2 配置不符來源鎖")
    provenance = {key: pinned[key] for key in ("source_lock_sha256", "builder_sha256", "library_sha256")}
    provenance.update(source_sha256=lock["source"]["sha256"], patches=lock["patches"],
                      streamoff_fixed=False, stop_order_patch_applied=True,
                      hardware_validation="pending", scope=lock["scope"])
    expected = {
        "k1x-cam": {**{key: pinned[key] for key in ("version", "size", "sha256")},
                    "filename": "k1x-cam_0.2.34+cm6.2_riscv64.deb", "provenance": provenance},
        "k1x-cam-lib": {**CM6_CAMERA_PACKAGES["k1x-cam-lib"], "filename": "k1x-cam-lib_0.1.8_riscv64.deb",
                        "provenance": {"signature_verified": False, "scope": "保持固定官方閉源 SDK，未變更內容。"}},
    }
    if records != expected:
        raise ValueError("相機配套身分缺少固定 cm6.2 套件 SHA-256 或完整來源紀錄")
    return {"provenance": provenance, "config": lock["config"], "packages": expected}


def verify_camera_rootfs(identity, filesystem):
    """對修正版再次核對實際根系統載荷與安裝狀態，避免只憑身分欄位放行。"""
    contract = verify_camera_packages(identity)
    if contract is None:
        return
    def content(name):
        return subprocess.check_output(["debugfs", "-R", "cat /" + name, str(filesystem)], stderr=subprocess.DEVNULL)
    provenance = contract["provenance"]
    config = contract["config"]
    for name, expected in (("usr/lib/libsdkcam.so", provenance["library_sha256"]),
                           (config["installed_path"], config["sha256"])):
        if hashlib.sha256(content(name)).hexdigest() != expected:
            raise ValueError("實際根系統的相機載荷 SHA-256 不符：" + name)
    if json.loads(content("usr/share/doc/k1x-cam/cm6-source.json")) != provenance:
        raise ValueError("實際根系統的相機來源紀錄不符")
    from bpi_k1_acceleration import paragraphs
    packages = [row for row in paragraphs(content("var/lib/dpkg/status").decode())
                if row.get("Package") in contract["packages"]]
    if len(packages) != 2 or {row.get("Package") for row in packages} != set(contract["packages"]):
        raise ValueError("實際根系統缺少相機套件安裝紀錄")
    for row in packages:
        if (row.get("Version"), row.get("Architecture"), row.get("Status")) != (
                contract["packages"][row["Package"]]["version"], "riscv64", "install ok installed"):
            raise ValueError("實際根系統的相機套件版本、架構或安裝狀態不符")


def prepare_cm6_camera(boot, output):
    """在已修 eth0 的獨立 DTB 上套用明確選用的雙相機候選。"""
    source = child_file(boot, CM6_CONNECTIVITY_PATH.lstrip("/"))
    if digest(source) != CM6_CONNECTIVITY_SHA256:
        raise ValueError("雙相機 DTB 來源缺少固定 eth0 修正")
    target = boot / CM6_CAMERA_PATH.lstrip("/")
    manifest = output / "cm6-camera-dtb.json"
    run([sys.executable, REPO / "tools/bpi_cm6_camera_dtb.py", "--source", source,
         "--output", target, "--manifest", manifest], stdout=subprocess.DEVNULL)
    record = json.loads(regular(manifest).read_text())
    verify_camera_record(record)
    if digest(regular(target)) != CM6_CAMERA_SHA256:
        raise ValueError("產生的 CM6 雙相機 DTB 與已實測候選不同")
    return record


def verify_boot_contract(payload, board, root_uuid, boot_uuid, cm6_connectivity=False, cm6_dual_imx415=False, native_identity=None):
    def content(filesystem, name):
        return subprocess.check_output(["debugfs", "-R", "cat " + name,
                                        str(payload / filesystem)], stderr=subprocess.DEVNULL, text=True)
    expected = extlinux(board, root_uuid, cm6_connectivity, cm6_dual_imx415, native=bool(native_identity))
    if content("bootfs.ext4", "/extlinux/extlinux.conf") != expected:
        raise ValueError("實際 bootfs 開機選項與板型或根分區不符")
    environment = content("bootfs.ext4", "/env_k1-x.txt")
    if "bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf" not in environment:
        raise ValueError("bootfs 缺少官方 U-Boot 的明確開機入口")
    fstab = content("rootfs.ext4", "/etc/fstab")
    for value, point in ((root_uuid, "/"), (boot_uuid, "/boot")):
        matches = [line.split() for line in fstab.splitlines() if not line.startswith("#") and len(line.split()) > 2 and line.split()[1] == point]
        if len(matches) != 1 or matches[0][:3] != ["UUID=" + value, point, "ext4"]:
            raise ValueError("實際根檔案系統掛載設定不符：" + point)
    marker = json.loads(content("rootfs.ext4", "/etc/bpi-k1-vendor.json"))
    if (marker["board"], marker["root_uuid"], marker["boot_uuid"]) != (board, root_uuid, boot_uuid):
        raise ValueError("根檔案系統內的媒體身分不符")
    if native_identity:
        record = native_dtb_contract(native_identity, board)
        if board == "bpi-cm6":
            gpio_records = native_identity.get("cm6_gpio_packages")
            if not gpio_records or marker.get("cm6_gpio_packages") != gpio_records:
                raise ValueError("CM6 成品缺少一致的 GPIO 套件來源身分")
            root_marker = json.loads(content("rootfs.ext4", "/etc/bpi-k1-native.json"))
            if root_marker.get("cm6_gpio_packages") != gpio_records:
                raise ValueError("CM6 原生根系統 GPIO manifest 與封裝不符")
            for package in gpio_records.values():
                for name, item in package["payload"].items():
                    if item["type"] != "file":
                        continue
                    if not re.fullmatch(r"usr/[A-Za-z0-9_./+\-]+", name) or ".." in Path(name).parts:
                        raise ValueError("GPIO 成品驗證檔案路徑不合法")
                    data = subprocess.check_output(["debugfs", "-R", "cat /" + name,
                                                    str(payload / "rootfs.ext4")], stderr=subprocess.DEVNULL)
                    if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                        raise ValueError("GPIO 套件檔案未完整進入成品：" + name)
        if native_identity.get("camera_profile") == "dual-imx415":
            if marker.get("cm6_camera_packages") != native_identity.get("cm6_camera_packages"):
                raise ValueError("實際根系統的相機配套身分與原生來源不符")
            verify_camera_rootfs(native_identity, payload / "rootfs.ext4")
        if marker.get("native_dtb") != record or marker.get("boot_dtb_path") != record["path"]:
            raise ValueError("原生媒體 DTB 標記或更新入口不一致")
        data = subprocess.check_output(["debugfs", "-R", "cat " + record["path"], str(payload / "bootfs.ext4")], stderr=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix="bpi-k1-dtb-check-") as temporary:
            path = Path(temporary) / Path(record["path"]).name
            path.write_bytes(data)
            native_dtb_contract(native_identity, board, path)
        return {"status": "passed", "scope": "native-bootfs-extlinux-env-rootfs-fstab-marker-dtb"}
    if cm6_connectivity or cm6_dual_imx415:
        if marker.get("boot_dtb_path") != boot_dtb_path(board, cm6_connectivity, cm6_dual_imx415):
            raise ValueError("CM6 核心更新入口未保留指定的獨立修正 DTB")
    dtbs = [(CM6_CONNECTIVITY_PATH, CM6_CONNECTIVITY_SHA256)] if cm6_connectivity else []
    if cm6_dual_imx415:
        verify_camera_packages(marker)
        if marker.get("cm6_camera_dtb_sha256") != CM6_CAMERA_SHA256:
            raise ValueError("CM6 雙相機身分標記缺少正確 DTB SHA-256")
        dtbs.append((CM6_CAMERA_PATH, CM6_CAMERA_SHA256))
    for path, expected_sha in dtbs:
        data = subprocess.check_output(["debugfs", "-R", "cat " + path,
                                        str(payload / "bootfs.ext4")], stderr=subprocess.DEVNULL)
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError("bootfs 的 CM6 修正 DTB 與實機驗證內容不符")
    return {"status": "passed", "scope": "bootfs-extlinux-env-rootfs-fstab-marker"}


def verify_sd_image(target, payload):
    """逐一核對實際 GPT、保護 MBR 與分區載荷，不依賴建立命令成功碼。"""
    total = regular(target).stat().st_size
    with target.open("rb") as stream:
        mbr = stream.read(512)
        if mbr[:80] != (payload / "factory/bootinfo_sd.bin").read_bytes() or mbr[450] != 0xEE or mbr[510:] != b"\x55\xaa":
            raise ValueError("SD 開機資訊或 GPT 保護 MBR 不符")
        headers = []
        for lba in (1, total // 512 - 1):
            stream.seek(lba * 512)
            data = bytearray(stream.read(512))
            if data[:8] != b"EFI PART":
                raise ValueError("SD GPT 標頭不存在")
            length, crc = struct.unpack_from("<II", data, 12)
            if not 92 <= length <= 512:
                raise ValueError("SD GPT 標頭長度不合法")
            data[16:20] = b"\0" * 4
            if zlib.crc32(data[:length]) != crc:
                raise ValueError("SD GPT 標頭 CRC32 不符")
            if struct.unpack_from("<Q", data, 24)[0] != lba:
                raise ValueError("SD GPT 標頭位置不符")
            entries_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", data, 72)
            if entry_size != 128 or count < len(PARTS) or count > 4096:
                raise ValueError("SD GPT 分區表大小不合法")
            stream.seek(entries_lba * 512)
            table = stream.read(count * entry_size)
            if len(table) != count * entry_size or zlib.crc32(table) != table_crc:
                raise ValueError("SD GPT 分區表 CRC32 不符")
            headers.append((data, table))
        if headers[0][1] != headers[1][1] or struct.unpack_from("<Q", headers[0][0], 32)[0] != total // 512 - 1 or struct.unpack_from("<Q", headers[1][0], 32)[0] != 1:
            raise ValueError("SD GPT 主副本不一致")
        table = headers[0][1]
        for index, (name, offset, maximum, source) in enumerate(PARTS):
            size = maximum or (payload / source).stat().st_size
            entry = table[index*128:(index+1)*128]
            if struct.unpack_from("<QQ", entry, 32) != (offset//512, (offset+size)//512-1) or entry[56:128].decode("utf-16-le").rstrip("\0") != name:
                raise ValueError(f"SD 分區界線或名稱不符：{name}")
            stream.seek(offset)
            if digest_stream(stream, (payload / source).stat().st_size) != digest(payload / source):
                raise ValueError(f"SD 分區載荷內容不符：{name}")
        if any(table[len(PARTS)*128:]):
            raise ValueError("SD 映像含額外 GPT 分區")
    return {"status": "passed", "scope": "gpt-crc-mbr-payload-ranges"}


def make_sd(payload, target, identity):
    check_payloads(payload)
    size = (payload / "rootfs.ext4").stat().st_size
    total = 260*MIB + size + MIB
    with target.open("xb") as f:
        f.truncate(total)
    seed = json.dumps(identity, sort_keys=True)
    argv = ["sgdisk", "--clear", "--set-alignment=1", "--disk-guid=" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed))]
    for n, (name, offset, maximum, _) in enumerate(PARTS, 1):
        length = size if maximum is None else maximum
        argv += [f"--new={n}:{offset//512}:{(offset+length)//512-1}",
                 f"--change-name={n}:{name}", f"--typecode={n}:8300",
                 f"--partition-guid={n}:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed + name))]
    run([*argv, target], stdout=subprocess.DEVNULL)
    with target.open("r+b") as dst:
        dst.write((payload / "factory/bootinfo_sd.bin").read_bytes())
        for _, offset, _, name in PARTS:
            dst.seek(offset)
            with (payload / name).open("rb") as src:
                shutil.copyfileobj(src, dst, 8*MIB)
        dst.flush()
        os.fsync(dst.fileno())
    run(["sgdisk", "--verify", target])
    verify_sd_image(target, payload)
    return total


def archive(target, entries):
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=3, allowZip64=True) as z:
        for name, path in sorted(entries.items()):
            z.write(path, name)


def verify_native_archive(archive, manifest):
    record = native_dtb_contract(manifest["identity"], manifest["board"])
    if manifest.get("native_dtb_sha256") != record["sha256"]:
        raise ValueError("原生封裝的獨立 DTB SHA-256 欄位不符")
    with tempfile.TemporaryDirectory(prefix="bpi-k1-native-verify-") as temporary:
        bootfs = Path(temporary) / "bootfs.ext4"
        if manifest["storage"] == "emmc":
            member, offset = "bootfs.ext4", 0
            length = archive.getinfo(member).file_size
        else:
            member, offset, length = next(iter(manifest["archive_members"])), 4 * MIB, 256 * MIB
        if not 0 < length <= 256 * MIB:
            raise ValueError("原生 bootfs 長度超出格式上限")
        with archive.open(member) as source, bootfs.open("wb") as target:
            source.seek(offset)
            left = length
            while left:
                block = source.read(min(left, MIB))
                if not block:
                    raise ValueError("原生 bootfs 載荷不足")
                target.write(block)
                left -= len(block)
        data = subprocess.check_output(["debugfs", "-R", "cat " + record["path"], str(bootfs)], stderr=subprocess.DEVNULL)
        path = Path(temporary) / Path(record["path"]).name
        path.write_bytes(data)
        native_dtb_contract(manifest["identity"], manifest["board"], path)


def verify(manifest_path):
    manifest_path = Path(manifest_path)
    m = json.loads(regular(manifest_path).read_text())
    native = m.get("schema_version", 1) == 2
    if m.get("schema_version", 1) not in (1, 2):
        raise ValueError("不支援的封裝紀錄版本")
    if native:
        native_target_contract(m.get("identity", {}), m["board"], m["storage"])
        native_dtb_contract(m.get("identity", {}), m["board"])
        if any(key in m.get("identity", {}) for key in ("cm6_camera_dtb_sha256", "cm6_ethernet_dtb_sha256")):
            raise ValueError("原生封裝不可混用歷史 DTB 身分")
    elif m.get("identity", {}).get("source_kind") == "armbian-native-rootfs":
        raise ValueError("原生來源必須使用 schema 2")
    camera_hash = m.get("identity", {}).get("cm6_camera_dtb_sha256")
    if "cm6_camera_dtb" in m or "cm6_camera_dtb_sha256" in m.get("identity", {}):
        if m["board"] != "bpi-cm6" or camera_hash != CM6_CAMERA_SHA256:
            raise ValueError("封裝的 CM6 雙相機板型或身分 SHA-256 不符")
        verify_camera_packages(m["identity"])
        verify_camera_record(m.get("cm6_camera_dtb"))
    base = manifest_path.parent
    artifact = child_file(base, m["artifact"]["name"])
    if digest(artifact) != m["artifact"]["sha256"]:
        raise ValueError("交付壓縮檔雜湊不符")
    with zipfile.ZipFile(artifact) as z:
        names = z.namelist()
        expected = m["archive_members"]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("壓縮套件有重複、缺少或多餘檔案")
        contract = verify_titan_contract(z) if m["storage"] == "emmc" else None
        for name in names:
            if not name or "\\" in name or Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("不安全的壓縮套件路徑")
            i = z.getinfo(name)
            if i.file_size != expected[name]["size"]:
                raise ValueError(f"元件大小不符：{name}")
            with z.open(name) as f:
                if digest_stream(f) != expected[name]["sha256"]:
                    raise ValueError(f"元件雜湊不符：{name}")
        if "root_uuid" in m and "boot_uuid" in m:
            if m["root_uuid"] == m["boot_uuid"]:
                raise ValueError("根檔案系統與開機分區 UUID 重複")
            for part, offset in (("boot", 4*MIB), ("root", 260*MIB)):
                member = next(iter(expected)) if m["storage"] == "sd" else part + "fs.ext4"
                with z.open(member) as stream:
                    actual = filesystem_uuid(stream, offset if m["storage"] == "sd" else 0)
                if actual != m[part + "_uuid"]:
                    raise ValueError(f"封裝內檔案系統 UUID 不符：{part}")
        if native:
            verify_native_archive(z, m)
    status = {"status": "passed", "scope": "offline-archive-integrity",
              "board": m["board"], "storage": m["storage"],
              "release_status": m.get("release_status", "candidate_unverified"),
              "hardware_validation": m.get("hardware_validation", "pending")}
    if contract is not None:
        status["titan_control_contract"] = contract
    advisory_path = base / "release-status.json"
    if advisory_path.exists():
        advisory = json.loads(regular(advisory_path).read_text())
        if advisory.get("historical_artifact_sha256") != m["artifact"]["sha256"]:
            raise ValueError("撤回公告與套件雜湊不符")
        status.update(release_status=advisory["release_status"],
                      hardware_validation=advisory["hardware_validation"])
    return status


def build(args):
    candidate_release = release_id(args.release_id)
    camera_option = getattr(args, "cm6_dual_imx415", None)
    cm6_dual_imx415 = bool(camera_option)
    cm6_connectivity = args.board == "bpi-cm6"
    selected_dtb = boot_dtb_path(args.board, cm6_connectivity, cm6_dual_imx415)
    if os.geteuid() != 0:
        raise ValueError("需要 sudo，在隔離掛載中產生媒體專屬檔案系統")
    if not args.inside:
        os.execvp("unshare", ["unshare", "--mount", "--propagation", "private",
                             sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--inside"])
    prepared = args.prepared.resolve()
    prep = json.loads((prepared / "preparation.json").read_text())
    if prep["status"] != "complete" or prep["identity"]["board"] != args.board:
        raise ValueError("根檔案系統尚未完成或板型不符")
    if prep.get("schema_version") == 2:
        native_target_contract(prep["identity"], args.board, args.storage)
    native, cm6_dual_imx415 = prepared_contract(prepared, prep, args.board, camera_option)
    cm6_connectivity = args.board == "bpi-cm6" and not native
    selected_dtb = boot_dtb_path(args.board, cm6_connectivity, cm6_dual_imx415, native=native)
    if cm6_dual_imx415:
        verify_camera_packages(prep["identity"])
    preflight = json.loads((prepared / "acceleration-preflight.json").read_text())
    if not preflight.get("passed") or preflight.get("board") != args.board or preflight.get("stage") != "installed":
        raise ValueError("已安裝加速配套尚未通過離線預檢")
    acceleration_lock = REPO / "config/spacemit-k1-acceleration/noble.lock.json"
    if prep["identity"]["acceleration_lock_sha256"] != digest(regular(acceleration_lock)):
        raise ValueError("準備映像使用的加速來源鎖已變更，必須重新核對準備結果")
    if digest(regular(prepared / "rootfs.ext4")) != prep["rootfs_sha256"]:
        raise ValueError("根檔案系統完成後遭到變更")
    reference = args.reference.resolve()
    reference_record = source_reference(reference)
    out = args.output.absolute()
    if out.exists():
        raise ValueError("交付目錄已存在，拒絕覆寫")
    out.mkdir(parents=True)
    payload = out / "payload"
    (payload / "factory").mkdir(parents=True)
    files = ["factory/FSBL.bin", "factory/bootinfo_sd.bin", "factory/bootinfo_emmc.bin",
             "fw_dynamic.itb", "u-boot.itb", "env.bin"]
    for name in files:
        shutil.copyfile(regular(reference / name), payload / name)
    # source_reference 已以倉內固定來源清單核對實際內容。
    reference_hashes = {name: digest(reference / name) for name in files}
    run(["cp", "--sparse=always", "--reflink=auto", prepared / "rootfs.ext4", payload / "rootfs.ext4"])
    identity = {**prep["identity"], "storage": args.storage, "layout_version": "bianbu-v2.3",
                "release_id": candidate_release,
                "reference_payloads": reference_hashes,
                "sources_lock_sha256": reference_record["sources_lock_sha256"]}
    if cm6_connectivity:
        identity["cm6_ethernet_dtb_sha256"] = CM6_CONNECTIVITY_SHA256
    if cm6_dual_imx415 and not native:
        identity["cm6_camera_dtb_sha256"] = CM6_CAMERA_SHA256
    root_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True) + ":root"))
    boot_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True) + ":boot"))
    run(["tune2fs", "-U", root_uuid, "-L", "armbian-root", payload / "rootfs.ext4"], stdout=subprocess.DEVNULL)
    boot = out / "boot-tree"
    run(["cp", "-a", prepared / "boot-tree", boot])
    for path in boot.rglob("*"):
        if path.is_symlink() and str(path.readlink()).startswith("/boot/"):
            target = str(path.readlink())[6:]
            path.unlink()
            path.symlink_to(os.path.relpath(boot / target, path.parent))
    for name in ("Image", "uInitrd", "dtb/spacemit/" + DTBS[args.board]):
        if not (boot / name).is_file():
            raise ValueError(f"開機元件不存在：{name}")
    kernel_record = normalize_kernel(boot)
    ethernet_record = prepare_cm6_connectivity(boot, out) if cm6_connectivity else None
    camera_record = prepare_cm6_camera(boot, out) if cm6_dual_imx415 and not native else None
    write(boot / "extlinux/extlinux.conf", extlinux(args.board, root_uuid, cm6_connectivity, cm6_dual_imx415, native=native))
    write(boot / "env_k1-x.txt", "kernel_addr_r=0x08000000\nfdt_addr_r=0x31000000\n"
          "ramdisk_addr_r=0x21000000\npxefile_addr_r=0x0c200000\n"
          "bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf\n")
    with (payload / "bootfs.ext4").open("xb") as f:
        f.truncate(256*MIB)
    run(["mkfs.ext4", "-q", "-F", "-U", boot_uuid, "-L", "bootfs", "-d", boot, payload / "bootfs.ext4"])
    mount = out / "mount"
    mount.mkdir()
    policy_record = None
    try:
        run(["mount", "-o", "loop,nodev,nosuid", payload / "rootfs.ext4", mount])
        if native and boot_inventory(mount / "boot") != prep["boot_tree"]:
            raise ValueError("原生根系統與獨立 boot-tree 不一致，停止媒體配置")
        fstab = mount / "etc/fstab"
        old = fstab.read_text()
        kept = []
        for line in old.splitlines():
            f = line.split()
            if len(f) >= 2 and not line.lstrip().startswith("#") and f[1] in ("/", "/boot"):
                continue
            kept.append(line)
        kept += [f"UUID={root_uuid} / ext4 defaults,noatime,errors=remount-ro 0 1",
                 f"UUID={boot_uuid} /boot ext4 defaults,noatime 0 2"]
        write(fstab, "\n".join(kept) + "\n")
        marker = {**identity, "root_uuid": root_uuid, "boot_uuid": boot_uuid,
                  "dtb": DTBS[args.board],
                  "hardware_validation": "pending"}
        if cm6_connectivity or native:
            marker["boot_dtb_path"] = selected_dtb
        if native:
            import bpi_k1_vendor_policy
            marker["native_boot"] = bpi_k1_vendor_policy.boot_record(boot, prep["kernel"], selected_dtb)
        write(mount / "etc/bpi-k1-vendor.json", json.dumps(marker, ensure_ascii=False, indent=2) + "\n")
        grow_tool = mount / "usr/local/sbin/bpi_k1_grow_rootfs.py"
        shutil.copyfile(REPO / "tools/bpi_k1_grow_rootfs.py", grow_tool)
        grow_tool.chmod(0o755)
        (mount / "var/lib/bpi-k1-vendor").mkdir(parents=True, exist_ok=True)
        write(mount / "etc/systemd/system/bpi-k1-grow-rootfs.service",
              "[Unit]\nDescription=擴大已核對的 K1 官方 GPT 根分區\n"
              "DefaultDependencies=no\nAfter=sysinit.target local-fs.target\nBefore=basic.target\n"
              "ConditionPathExists=/etc/bpi-k1-vendor.json\n"
              "ConditionPathExists=!/var/lib/bpi-k1-vendor/rootfs-expanded\n"
              "[Service]\nType=oneshot\nRemainAfterExit=yes\nTimeoutStartSec=6min\n"
              "ExecStart=/usr/local/sbin/bpi_k1_grow_rootfs.py\n"
              "ExecStartPost=/usr/bin/touch /var/lib/bpi-k1-vendor/rootfs-expanded\n"
              "[Install]\nWantedBy=basic.target\n")
        enabled = mount / "etc/systemd/system/basic.target.wants/bpi-k1-grow-rootfs.service"
        enabled.parent.mkdir(parents=True, exist_ok=True)
        enabled.symlink_to("../bpi-k1-grow-rootfs.service")
        # 舊 /boot 留在原始準備映像，新候選則由獨立 bootfs 掛載。
        for child in (mount / "boot").iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
        hook = '#!/usr/bin/python3\n"""更新核心後維持官方分區配置的開機選項。"""\nimport gzip,json,os,shutil\nfrom pathlib import Path\nm=json.loads(Path("/etc/bpi-k1-vendor.json").read_text())\n'
        hook += 'source=Path("/boot/vmlinuz")\nif not source.is_file():\n    raise SystemExit("找不到套件更新後的核心")\nwith source.open("rb") as f:\n    compressed=f.read(2)==b"\\x1f\\x8b"\ntarget=Path("/boot/Image.vendor-new")\nwith (gzip.open(source,"rb") if compressed else source.open("rb")) as src, target.open("wb") as dst:\n    shutil.copyfileobj(src,dst)\ndata=target.read_bytes()\nexpected={"bpi-cm6":b"23.2@6460340","bpi-f3":b"24.2@6603887"}[m["board"]]\nif len(data)>0x04200000 or (data[:4]!=b"\\xd0\\x0d\\xfe\\xed" and data[56:60]!=b"RSC\\x05") or expected not in data:\n    target.unlink()\n    raise SystemExit("核心格式、大小或 GPU 配套已變更，須重新驗證整套映像")\nos.replace(target,"/boot/Image")\n'
        hook += 'dtb_path=m.get("boot_dtb_path", "/dtb/spacemit/"+m["dtb"])\ntext="DEFAULT armbian\\nTIMEOUT 20\\nLABEL armbian\\n  KERNEL /Image\\n  INITRD /uInitrd\\n  FDT "+dtb_path+"\\n  APPEND root=UUID="+m["root_uuid"]+" rootwait rootfstype=ext4 rw earlycon=sbi console=tty1 console=ttyS0,115200 loglevel=4 fsck.repair=yes net.ifnames=0\\n"\n'
        hook += 'Path("/boot/extlinux").mkdir(exist_ok=True)\nPath("/boot/extlinux/extlinux.conf").write_text(text)\n'
        compile(hook, "zz-bpi-k1-vendor", "exec")
        write(mount / "etc/kernel/postinst.d/zz-bpi-k1-vendor", hook, 0o755)
        if native:
            policy_record = bpi_k1_vendor_policy.apply(mount, marker, boot)
    finally:
        if os.path.ismount(mount):
            run(["umount", mount])
    for name in ("rootfs.ext4", "bootfs.ext4"):
        run(["e2fsck", "-fn", payload / name])
        with (payload / name).open("rb") as stream:
            if filesystem_uuid(stream) != (root_uuid if name == "rootfs.ext4" else boot_uuid):
                raise ValueError(f"建立的檔案系統 UUID 不符：{name}")
    boot_contract = verify_boot_contract(payload, args.board, root_uuid, boot_uuid, cm6_connectivity, cm6_dual_imx415,
                                         native_identity=identity if native else None)
    check_payloads(payload)
    layout_obj = layout((payload / "rootfs.ext4").stat().st_size, args.storage == "emmc")
    write(payload / "partition_universal.json", json.dumps(layout_obj, indent=2) + "\n")
    write(payload / "fastboot.yaml", fastboot_config())
    stem = f"Armbian_Noble_{args.board}_gnome_{'vendor-sd' if args.storage == 'sd' else 'titan-emmc'}_{candidate_release}"
    manifest = {"schema_version": 2 if native else 1, "board": args.board, "release": "noble", "desktop": "gnome-wayland",
                "storage": args.storage, "identity": identity, "root_uuid": root_uuid, "boot_uuid": boot_uuid,
                "release_id": candidate_release, "release_status": "candidate_unverified",
                "producer_sha256": digest(Path(__file__)),
                "kernel": prep["kernel"], "hardware_validation": "pending", "archive_members": {},
                "boot_kernel": kernel_record,
                "boot_contract": boot_contract, "acceleration_preflight": preflight,
                "reference": reference_record}
    if native:
        manifest["native_dtb_sha256"] = identity["native_dtb"]["sha256"]
        manifest["native_boot"] = marker["native_boot"]
        manifest["vendor_policy"] = policy_record
    if ethernet_record:
        manifest["cm6_ethernet_dtb"] = ethernet_record
    if camera_record:
        manifest["cm6_camera_dtb"] = camera_record
    if args.storage == "sd":
        img = out / (stem + ".img")
        manifest["minimum_media_bytes"] = make_sd(payload, img, identity)
        manifest["sd_structure_validation"] = {"status": "passed", "scope": "gpt-crc-mbr-payload-ranges"}
        entries = {img.name: img}
        target = out / (stem + ".img.zip")
    else:
        manifest["minimum_media_bytes"] = 260*MIB + (payload / "rootfs.ext4").stat().st_size + MIB
        entries = {p.relative_to(payload).as_posix(): p for p in payload.rglob("*") if p.is_file()}
        target = out / (stem + ".zip")
    for name, path in entries.items():
        manifest["archive_members"][name] = {"size": path.stat().st_size, "sha256": digest(path)}
    archive(target, entries)
    artifact_hash = digest(target)
    manifest["artifact"] = {"name": target.name, "sha256": artifact_hash, "size": target.stat().st_size}
    write(out / (target.name + ".sha256"), artifact_hash + "  " + target.name + "\n")
    write(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    result = verify(out / "manifest.json")
    write(out / "verification.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"已建立候選並通過封裝完整性檢查：{target}；尚未取得實際 Titan 燒錄與開機證據。")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="建立單一媒體候選")
    b.add_argument("--board", choices=DTBS, required=True)
    b.add_argument("--storage", choices=("sd", "emmc"), required=True)
    b.add_argument("--prepared", type=Path, required=True)
    b.add_argument("--reference", type=Path, required=True)
    b.add_argument("--output", type=Path, required=True)
    b.add_argument("--release-id", required=True, help="明確候選版本，例如 20260918-rc2")
    b.add_argument("--cm6-dual-imx415", action=argparse.BooleanOptionalAction, default=None,
                   help="僅 CM6：歷史來源明確啟用雙相機；原生來源省略時依 prepared 配置，明確衝突則拒絕")
    b.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    v = sub.add_parser("verify", help="重新核對發布套件與內部元件")
    v.add_argument("manifest", type=Path)
    a = p.parse_args()
    if a.command == "verify":
        print(json.dumps(verify(a.manifest), ensure_ascii=False, indent=2))
    else:
        build(a)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print(f"封裝失敗：{exc}", file=sys.stderr)
        sys.exit(1)
