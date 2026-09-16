#!/usr/bin/env python3
"""固定 EMAC 十套映像的唯讀系統組件證據；不操作硬體或執行映像程式。"""

from __future__ import annotations

import gzip
import fcntl
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import zlib
from contextlib import contextmanager

import bpi_h618_artifacts as safe
import bpi_h618_image_matrix as matrix


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "output/evidence/bpi-h618-no-mux/T0-customer-components-001"
OLD = PROJECT / "output/evidence/bpi-h618-no-mux/T0-bookworm-components-001"
T0 = Path("/media/pi/SMCI/bpi/evidence-20260916T134400Z-737ad9c22dc44db3a1f8c4e9dcb649d3")
INVENTORY_SHA = "3b3bba0de68d79e7a87d6737e61008ad1b0058d14babf0ef546e7e8ee3872bd4"
SUMMARY = "run-20260916T134409Z-1f9bd85e4f414605b8531017663b7e94/summary.json"
SUMMARY_SHA = "2d599719b331924b4395299f61e256a5b567316305b61bd7ce436a0574f0464c"
OLD_INDEX_SHA = "27362d92990f4fd424bde5fb60c50fd8329796ef63cd96c9ad4f60a5a12b7d5d"
BOARD = "bananapim4zeroemac"
RELEASE = "6.18.49-current-sunxi64"
DTB = "sun50i-h618-bananapi-m4-zero-emac.dtb"
OVERLAY = "bananapi-m4-zero-emac-sdio-wifi-bt"
DTDIR = f"/boot/dtb-{RELEASE}/allwinner"
PATHS = {
    "kernel.Image": f"/boot/vmlinuz-{RELEASE}",
    "uInitrd": f"/boot/uInitrd-{RELEASE}",
    "initrd.img": f"/boot/initrd.img-{RELEASE}",
    "board.dtb": f"{DTDIR}/{DTB}",
    "wifi-bt.dtbo": f"{DTDIR}/overlay/sun50i-h616-{OVERLAY}.dtbo",
    "fixup.scr": f"{DTDIR}/overlay/sun50i-h616-fixup.scr",
    "kernel.config": f"/boot/config-{RELEASE}",
    "modules.builtin": f"/lib/modules/{RELEASE}/modules.builtin",
    "boot.cmd": "/boot/boot.cmd", "boot.scr": "/boot/boot.scr",
    "armbianEnv.txt": "/boot/armbianEnv.txt", "fstab": "/etc/fstab",
    "armbian-release": "/etc/armbian-release",
}
ROLES = {"kernel": "kernel.Image", "initrd": "uInitrd", "dtb": "board.dtb",
         "overlay": "wifi-bt.dtbo", "fixup": "fixup.scr"}
AUDIT_PATHS = {
    "firstrun": "/usr/lib/armbian/armbian-firstrun",
    "firstlogin": "/usr/lib/armbian/armbian-firstlogin",
    "resize": "/usr/lib/armbian/armbian-resize-filesystem",
    "resize.service": "/lib/systemd/system/armbian-resize-filesystem.service",
    "firstrun.service": "/lib/systemd/system/armbian-firstrun.service",
    "armbian-install-bin": "/usr/bin/armbian-install",
    "platform_install.sh": "/usr/lib/u-boot/platform_install.sh",
    "uboot.postinst-arm64": "/var/lib/dpkg/info/linux-u-boot-bananapim4zeroemac-current.postinst",
    "bsp.postinst": "/var/lib/dpkg/info/armbian-bsp-cli-bananapim4zeroemac-current.postinst",
    "config.functions.sh": "/usr/lib/armbian-config/config.functions.sh",
    "config.system.sh": "/usr/lib/armbian-config/config.system.sh",
    "os-release": "/usr/lib/os-release",
    "dpkg.status": "/var/lib/dpkg/status",
}
COMBINATIONS = [f"{release}-{variant}" for release in matrix.RELEASES for variant in ("minimal", "xfce_desktop")]
MAX_FILE = 128 * 1024**2
require = safe.require


def read(path, limit=MAX_FILE):
    path = Path(path)
    with safe.open_root(path.parent) as root, safe.open_file(root, path.name) as stream:
        before = matrix.identity(os.fstat(stream.fileno()))
        require(before["st_size"] <= limit, "證據檔案超過大小上限")
        blob = stream.read(limit + 1)
        require(len(blob) == before["st_size"] and before == matrix.identity(os.fstat(stream.fileno())),
                "讀取期間證據有變動")
        return blob


def digest(blob):
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            "crc32": f"{zlib.crc32(blob):08x}"}


def parse_json(blob):
    return json.loads(blob.decode("utf-8"), object_pairs_hook=safe.unique_object)


def mkdir(path):
    path = Path(path)
    with safe.open_root(path.parent) as parent:
        os.mkdir(path.name, mode=0o700, dir_fd=parent)


def save(path, blob):
    path = Path(path)
    with safe.open_root(path.parent) as root, safe.open_file(root, path.name, create=True) as output:
        output.write(blob)


def save_json(path, value):
    save(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def parse_env(blob):
    """只拆第一個等號；引號、變數及命令替換全部保持字串，不做 shell 展開。"""
    result = {}
    for line in blob.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), "環境檔不是單純鍵值")
        require(key not in result, "環境檔鍵值重複")
        result[key] = value
    return result


def legacy(blob, kind):
    require(len(blob) >= 64, "傳統 U-Boot 標頭截斷")
    fields = struct.unpack(">7I4B32s", blob[:64])
    magic, hcrc, _, size, _, _, dcrc, system, arch, image_type, compression, _ = fields
    header = bytearray(blob[:64])
    header[4:8] = bytes(4)
    payload = blob[64:]
    require(magic == 0x27051956 and size == len(payload), "傳統 U-Boot 格式或長度不符")
    require(zlib.crc32(header) == hcrc and zlib.crc32(payload) == dcrc, "傳統 U-Boot CRC 不符")
    require(image_type == kind and system == 5, "傳統 U-Boot 組件用途不符")
    if kind == 3:
        require(arch == 22 and compression == 1 and payload.startswith(b"\x1f\x8b"),
                "uInitrd 架構或封裝不符")
    if kind == 6:
        require(len(payload) >= 8, "開機腳本資料表截斷")
        size, terminal = struct.unpack(">II", payload[:8])
        require(terminal == 0 and size == len(payload) - 8, "開機腳本不是單一項目")
        payload = payload[8:]
    return payload


def root_uuid(stats, env, fstab):
    match = re.search(r"^Filesystem UUID:\s+([0-9a-f-]{36})$", stats, re.M)
    require(match is not None, "超級區塊缺少根 UUID")
    value = match[1]
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value), "根 UUID 格式不符")
    rows = [line.split() for line in fstab.decode().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    require(all(len(row) == 6 for row in rows), "fstab 欄位數不符")
    roots = [row for row in rows if row[1] == "/"]
    require(len(roots) == 1 and roots[0][0] == "UUID=" + value and roots[0][2] == "ext4",
            "fstab 根 UUID 或檔案系統不符")
    require(env.get("rootdev") == "UUID=" + value and env.get("rootfstype") == "ext4",
            "armbianEnv 根 UUID 或檔案系統不符")
    return value


def kernel_support(config, builtin, kernel):
    settings = parse_env(config)
    required = ("MMC", "MMC_BLOCK", "MMC_SUNXI", "PWRSEQ_EMMC", "EXT4_FS", "BLK_DEV_INITRD", "RD_GZIP")
    require(all(settings.get("CONFIG_" + key) == "y" for key in required), "核心缺少必要內建 MMC/ext4/initrd 支援")
    names = builtin.decode().splitlines()
    for name in ("host/sunxi-mmc.ko", "core/mmc_core.ko", "core/mmc_block.ko", "core/pwrseq_emmc.ko"):
        require(any(item.endswith("/" + name) for item in names), "內建模組清單缺少 " + name)
    require(kernel[56:60] == b"ARM\x64", "核心不是 ARM64 Image")
    match = re.search(rb"Linux version ([^\s\0]+)", kernel)
    require(match and match[1].decode() == RELEASE, "核心內嵌版本不符")
    return {key: "y" for key in required}


def module_versions(listing):
    names = []
    for line in listing.splitlines():
        if not line.strip():
            continue
        name = line.split("/")[5] if line.startswith("/") else line.split()[-1]
        if name not in (".", ".."):
            names.append(name)
    require(names == [RELEASE], "根系統模組目錄不唯一或版本不符")
    return names


class Analysis:
    def __init__(self, directory):
        self.directory = directory
        self.records = []
        mkdir(directory / "analysis")

    def run(self, args, data=None):
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=90, env={**os.environ, "LC_ALL": "C"})
        number = f"command-{len(self.records):03d}"
        for extension, blob in (("stdout", result.stdout), ("stderr", result.stderr)):
            save(self.directory / "analysis" / (number + "." + extension), blob)
        self.records.append({"argv": args, "returncode": result.returncode, "record": number,
                             "stdout": digest(result.stdout), "stderr": digest(result.stderr)})
        require(result.returncode == 0, "靜態解析工具失敗：" + args[0])
        return result.stdout

    def trees(self, env):
        require(env.get("fdtfile") == DTB and env.get("overlay_prefix") == "sun50i-h616" and
                env.get("overlays") == OVERLAY and not env.get("user_overlays"), "原 DTB/overlay 條件不符")
        require(not any(key.startswith("param_") for key in env), "存在尚未核對的 fixup 參數")
        base = self.directory / "files/board.dtb"
        effective = self.directory / "analysis/effective.dtb"
        self.run(["/usr/bin/fdtoverlay", "-i", str(base), "-o", str(effective),
                  str(self.directory / "files/wifi-bt.dtbo")])
        reports = {}
        for label, tree in (("base", base), ("overlay_applied", effective)):
            def get(node, prop, datatype="s"):
                return self.run(["/usr/bin/fdtget", "-t", datatype, str(tree), node, prop]).decode().strip()
            compatible = get("/", "compatible")
            require("sinovoip,bpi-m4-zero-emac" in compatible.split(), "不是 Linux EMAC DTB")
            mmc2 = get("/__symbols__", "mmc2")
            mmc1 = get("/__symbols__", "mmc1")
            require(mmc2 == "/soc/mmc@4022000" and mmc1 == "/soc/mmc@4021000", "MMC 控制器路徑不符")
            require(get(mmc2, "status") == "okay" and get(mmc2, "bus-width", "u") == "8", "MMC2 未啟用八位元模式")
            require("allwinner,sun50i-h616-emmc" in get(mmc2, "compatible").split(), "MMC2 相容條件不符")
            properties = self.run(["/usr/bin/fdtget", "-p", str(tree), mmc2]).decode().split()
            require(all(prop in properties for prop in ("non-removable", "cap-mmc-hw-reset", "mmc-hs200-1_8v")),
                    "MMC2 缺少原配 eMMC 屬性")
            status = get(mmc1, "status")
            require(status == ("disabled" if label == "base" else "okay"), "SDIO overlay 狀態不符")
            reports[label] = {"mmc2": mmc2, "status": "okay", "bus_width": 8, "mmc1_status": status}
        return reports

    def initramfs(self, blob, builtin):
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
            archive = stream.read(256 * 1024**2 + 1)
        require(len(archive) <= 256 * 1024**2, "initramfs 展開超過記憶體上限")
        listing = self.run(["/usr/bin/cpio", "-it", "--quiet"], archive).decode().splitlines()
        versions = sorted(set(re.findall(r"(?:usr/)?lib/modules/([^/\n]+)", "\n".join(listing))))
        require(versions == [RELEASE], "initramfs 模組版本不唯一或不符")
        candidates = [name for name in listing if name.endswith(f"lib/modules/{RELEASE}/modules.builtin")]
        require(len(candidates) == 1, "initramfs 內建模組清單不唯一")
        embedded = self.run(["/usr/bin/cpio", "-i", "--to-stdout", "--quiet", "--no-absolute-filenames",
                             "--", candidates[0]], archive)
        require(embedded == builtin, "initramfs 與根系統內建模組清單不同")
        return {"versions": versions, "builtin_lists_equal": True,
                "resize_paths": [name for name in listing if re.search(r"growroot|resize", name)]}


def trusted_matrix():
    inventory_blob, summary_blob = read(T0 / "inventory.json"), read(T0 / SUMMARY)
    require(digest(inventory_blob)["sha256"] == INVENTORY_SHA and digest(summary_blob)["sha256"] == SUMMARY_SHA,
            "固定 T0 證據雜湊不符")
    inventory, summary = parse_json(inventory_blob), parse_json(summary_blob)
    require(inventory["ok"] is True and summary["ok"] is True and inventory["profile"] == BOARD,
            "固定 T0 不是完整成功的 EMAC 矩陣")
    results = {item["path"]: item for item in summary["results"]}
    entries = inventory["entries"]
    require(len(results) == len(entries) == 10 and
            {(item["release"], item["variant"]) for item in entries} ==
            {(release, variant) for release in matrix.RELEASES for variant in ("minimal", "xfce_desktop")},
            "不是固定五 OS 各兩套")
    for entry in entries:
        require(entry["profile"] == BOARD and entry["kernel"] == "6.18.49", "候選板型或核心不符")
        result = results[entry["path"]]
        require(result["ok"] is True and result["compressed"]["sha256"] == entry["expected_compressed_sha256"],
                "T0 逐檔來源未通過")
    return inventory, results


def check_source(inventory, entry):
    with safe.open_root(inventory["root"]) as source:
        require(matrix.file_identity(source, entry["path"]) == entry["identity"], "原 XZ 身分已變動")


def build_manifest(directory, inventory, entry, result, source, stats, modules):
    files = {name: read(directory / "files" / name) for name in PATHS}
    env = parse_env(files["armbianEnv.txt"])
    require(parse_env(files["armbian-release"]).get("BOARD") == BOARD, "映像內板型不是 EMAC")
    uuid = root_uuid(stats, env, files["fstab"])
    module_versions(modules)
    support = kernel_support(files["kernel.config"], files["modules.builtin"], files["kernel.Image"])
    require(legacy(files["uInitrd"], 3) == files["initrd.img"], "uInitrd payload 與 initrd 不同")
    require(legacy(files["boot.scr"], 6) == files["boot.cmd"], "boot.scr 與 boot.cmd 不同")
    legacy(files["fixup.scr"], 6)
    analysis = Analysis(directory)
    try:
        trees = analysis.trees(env)
        initramfs = analysis.initramfs(files["initrd.img"], files["modules.builtin"])
    finally:
        save_json(directory / "analysis/commands.json", analysis.records)
    check_source(inventory, entry)
    return {"schema": "bpi-h618-customer-components-v1", "board": BOARD,
            "os": entry["release"], "desktop": entry["variant"], "kernel_release": RELEASE,
            "image": {"path": str(Path(inventory["root"]) / entry["path"]),
                      "compressed": result["compressed"], "raw": result["raw"]},
            "root_uuid": uuid, "partuuid": source["partuuid"],
            "files": {role: {"path": PATHS[name], **digest(files[name]),
                             "evidence_path": str(directory / "files" / name)} for role, name in ROLES.items()},
            "original_env": env,
            "preflight": {"source_verified": True, "legacy_initrd_verified": True, "mmc_support_verified": True},
            "hardware_validated": False, "source_evidence": source,
            "checks": {"kernel_builtin": support, "device_tree": trees, "initramfs": initramfs,
                       "root_uuid_consistent": True, "boot_scr_matches_cmd": True},
            "scope": "僅原配套組件靜態核對；未執行核心、未操作硬體、未授權本工具寫入媒體",
            "controlled_boot_differences": ["修正或省略原 mmc 0:1 所得 ubootpart",
                "一次性遮蔽擴容及 APT；禁止安裝器與 bootloader 更新",
                "由主代理另行在傳入核心的 RAM DTB 停用 SD host；此清單雜湊仍指原 DTB",
                "救援引導、RAM DTB 與參數差異不能稱原樣開機"]}


def reuse_bookworm(directory, inventory, entry, result):
    index_blob = read(OLD / "evidence-index.json")
    require(digest(index_blob)["sha256"] == OLD_INDEX_SHA, "既有 Bookworm 證據索引不符")
    index = {item["path"]: item for item in parse_json(index_blob)["entries"]}
    verified = []

    def old(name):
        blob = read(OLD / name)
        expected = index[name]
        require(all(digest(blob)[key] == expected[key] for key in ("bytes", "sha256")), "既有證據內容不符：" + name)
        verified.append(expected)
        return blob

    extraction = parse_json(old("extraction.json"))
    verification = parse_json(old("analysis/verification.json"))
    require(extraction["ok"] is True and verification["ok"] is True and verification["source_identity_unchanged"] is True,
            "既有預檢未通過")
    require(extraction["source_identity"] == entry["identity"] and
            {"bytes": extraction["raw_bytes"], "sha256": extraction["raw_sha256"]} == result["raw"],
            "既有預檢與 T0 來源不一致")
    require(verification["root_partition_unchanged"] == extraction["root_partition"], "既有分割檢查證據不一致")
    check_source(inventory, entry)
    mkdir(directory / "files")
    for name, original_path in PATHS.items():
        record = parse_json(old("queries/" + name + ".json"))
        require(record["argv"][1] == "-R" and record["argv"][2].split()[:2] == ["dump", original_path],
                "既有擷取不是指定組件的唯讀查詢")
        save(directory / "files" / name, old("files/" + name))
    prefix = old("boot-prefix.bin")
    require(matrix.check_mbr(prefix[:512], result["raw"]["bytes"], 31289507840) == result["mbr"], "既有 MBR 與 T0 不一致")
    source = {"inventory_sha256": INVENTORY_SHA, "summary_sha256": SUMMARY_SHA,
              "old_evidence_index_sha256": OLD_INDEX_SHA, "old_evidence_path": str(OLD),
              "raw_verification": "沿用已固定擷取證據；本次未重讀 XZ",
              "partuuid": f"{struct.unpack_from('<I', prefix, 440)[0]:08x}-01",
              "partition": result["mbr"]["partitions"][0], "source_commit_declared": entry["source_commit"]}
    stats = old("queries/filesystem.stdout").decode()
    modules = old("queries/modules-list.stdout").decode()
    for name in ("firstrun", "firstlogin", "resize", "resize.service", "firstrun.service",
                 "armbian-install-bin", "platform_install.sh", "uboot.postinst-arm64", "bsp.postinst"):
        save(directory / "files" / name, old("files/" + name))
    save_json(directory / "reused-evidence.json", verified)
    return build_manifest(directory, inventory, entry, result, source, stats, modules)


class HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.sha256 = hashlib.sha256()
        self.count = 0

    def read(self, size=-1):
        blob = self.stream.read(size)
        self.count += len(blob)
        self.sha256.update(blob)
        return blob


def stream_partition(compressed, destination, expected, progress=None):
    """串流完整原 IMG，但僅將可信 MBR 的根分割範圍寫入普通衍生檔。"""
    parts = expected["mbr"]["partitions"]
    require(len(parts) == 1 and parts[0]["index"] == 1 and parts[0]["type"] == 0x83, "只接受單一 ext4 主分割")
    start, length = parts[0]["start_lba"] * 512, parts[0]["sectors"] * 512
    require(512 <= start <= 16 * 1024**2 and start + length == expected["raw"]["bytes"], "可信分割範圍不符")
    require(expected["raw"]["bytes"] <= 31289507840, "原映像超過本輪 eMMC 容量")
    reader = HashingReader(compressed)
    raw_hash, partition_hash, prefix = hashlib.sha256(), hashlib.sha256(), bytearray()
    count = written = 0
    next_progress = 512 * 1024**2
    with lzma.LZMAFile(reader, "rb", format=lzma.FORMAT_XZ) as decoder:
        while blob := decoder.read(matrix.CHUNK):
            end = count + len(blob)
            require(end <= expected["raw"]["bytes"], "解壓輸出超過可信長度")
            raw_hash.update(blob)
            if count == 0:
                require(matrix.check_mbr(blob[:512], expected["raw"]["bytes"], 31289507840) == expected["mbr"],
                        "本次 MBR 與可信證據不一致")
            if count < start:
                prefix.extend(blob[:min(len(blob), start - count)])
            selected = blob[max(0, start - count):max(0, min(len(blob), start + length - count))]
            if selected:
                destination.write(selected)
                partition_hash.update(selected)
                written += len(selected)
            count = end
            if progress and count >= next_progress:
                progress(count)
                next_progress += 512 * 1024**2
    require({"bytes": reader.count, "sha256": reader.sha256.hexdigest()} == expected["compressed"], "原 XZ 長度或 SHA-256 不符")
    require({"bytes": count, "sha256": raw_hash.hexdigest()} == expected["raw"], "原 IMG 長度或 SHA-256 不符")
    require(written == length and len(prefix) == start, "分割擷取長度不符")
    return {"root_partition": {"bytes": written, "sha256": partition_hash.hexdigest()},
            "prefix": digest(prefix), "partuuid": f"{struct.unpack_from('<I', prefix, 440)[0]:08x}-01",
            "partition": parts[0], "raw": expected["raw"], "compressed": expected["compressed"]}, bytes(prefix)


class Debugfs:
    def __init__(self, partition, directory):
        self.partition = partition
        self.directory = directory
        self.records = []
        mkdir(directory / "queries")
        mkdir(directory / "files")

    def query(self, key, operation, path=None, *, optional=False, output=None):
        require(operation in ("stats", "stat", "ls", "cat"), "只允許唯讀 debugfs 操作")
        safe.relative_parts(key)
        request = operation
        if path is not None:
            require(path.startswith("/") and all(re.fullmatch(r"[A-Za-z0-9_.+-]+", part) and
                    part not in (".", "..") for part in path.split("/")[1:]), "映像內查詢路徑無效")
            request += (" -p " if operation == "ls" else " ") + path
        with safe.open_root(self.partition.parent) as root, safe.open_file(root, self.partition.name) as source:
            before = matrix.identity(os.fstat(source.fileno()))
            args = ["/usr/sbin/debugfs", "-R", request, f"/proc/self/fd/{source.fileno()}"]
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    pass_fds=(source.fileno(),), timeout=90, env={**os.environ, "LC_ALL": "C"})
            require(before == matrix.identity(os.fstat(source.fileno())), "衍生分割檔在查詢時變動")
        diagnostic = re.sub(rb"\Adebugfs [^\n]*\n", b"", result.stderr)
        missing = b"File not found by ext2_lookup" in diagnostic
        ok = result.returncode == 0 and not diagnostic.strip()
        stdout_path = ("files/" + output) if output and ok else ("queries/" + key + ".stdout")
        save(self.directory / stdout_path, result.stdout)
        save(self.directory / "queries" / (key + ".stderr"), result.stderr)
        record = {"argv": args, "partition_path": str(self.partition), "returncode": result.returncode,
                  "ok": ok, "missing": missing, "stdout_path": stdout_path,
                  "stdout": digest(result.stdout), "stderr": digest(result.stderr)}
        self.records.append(record)
        save_json(self.directory / "queries" / (key + ".json"), record)
        if optional and missing and result.returncode == 0:
            return None
        require(ok, "debugfs 查詢失敗：" + key)
        return result.stdout

    def file(self, name, path, optional=False):
        description = self.query(name + "-stat", "stat", path, optional=optional)
        if description is None:
            return None
        require(b"Type: regular" in description, "擷取目標不是映像內的一般檔案：" + path)
        size = re.search(rb"\bSize:\s+(\d+)", description)
        require(size and int(size[1]) <= MAX_FILE, "擷取檔案超過上限")
        blob = self.query(name, "cat", path, output=name)
        require(len(blob) == int(size[1]), "debugfs 擷取長度與 inode 不符")
        return blob


def audit_summary(directory, query_states=None):
    scripts = {}
    for name in AUDIT_PATHS:
        path = directory / "files" / name
        if path.exists():
            blob = read(path)
            scripts[name] = {"path": AUDIT_PATHS[name], **digest(blob)}
    env = parse_env(read(directory / "files/armbian-release"))
    boot = read(directory / "files/boot.cmd").decode()
    return {"scripts": scripts, "states": query_states or {},
            "ubootpart_mmc0_hardcoded": bool(re.search(r"part uuid mmc 0:1 partuuid", boot)),
            "force_uboot_update": env.get("FORCE_UBOOT_UPDATE"),
            "bootscript_force_update": env.get("BOOTSCRIPT_FORCE_UPDATE"),
            "limits": "腳本僅做原文與雜湊差異比對；未執行。擴容依根分割父盤，更新及安裝器仍須禁止"}


def extract_customer(directory, inventory, entry, result):
    partition = directory / "root-partition.img"
    require(not list(OUTPUT.glob("*/root-partition.img")), "本次證據仍有暫存分割；先完成或檢查該套，不新增第二份")
    space = os.statvfs(directory)
    require(space.f_bavail * space.f_frsize > result["raw"]["bytes"] + 512 * 1024**2, "衍生分割暫存空間不足")
    key = f"{entry['release']}-{entry['variant']}"

    def progress(count):
        print(json.dumps({"stage": "串流擷取", "item": key, "raw_bytes": count,
                          "expected_bytes": result["raw"]["bytes"]}, ensure_ascii=False), flush=True)

    with safe.open_root(inventory["root"]) as source, safe.open_file(source, entry["path"]) as compressed:
        require(matrix.identity(os.fstat(compressed.fileno())) == entry["identity"], "原 XZ 身分已變動")
        with safe.open_root(directory) as destination, safe.open_file(destination, partition.name, create=True) as output:
            extraction, prefix = stream_partition(compressed, output, result, progress)
        require(matrix.identity(os.fstat(compressed.fileno())) == entry["identity"], "串流期間原 XZ 身分已變動")
    check_source(inventory, entry)
    extraction.update(inventory_sha256=INVENTORY_SHA, summary_sha256=SUMMARY_SHA,
                      source_commit_declared=entry["source_commit"], raw_verification="本次完整串流比對成功")
    save(directory / "boot-prefix.bin", prefix)
    with safe.open_root(directory) as destination:
        extraction["temporary_identity"] = matrix.file_identity(destination, partition.name)
    save_json(directory / "extraction.json", extraction)
    debug = Debugfs(partition, directory)
    stats = debug.query("filesystem", "stats").decode()
    modules = debug.query("modules-list", "ls", "/lib/modules").decode()
    for name, path in PATHS.items():
        debug.file(name, path)
    for name, path in AUDIT_PATHS.items():
        debug.file(name, path, optional=name in ("config.functions.sh", "config.system.sh"))
    states = {}
    for key, path in {
        "resize_enabled": "/etc/systemd/system/basic.target.wants/armbian-resize-filesystem.service",
        "no_resize_marker": "/root/.no_rootfs_resize",
        "first_run_marker": "/root/.not_logged_in_yet",
        "firstrun_enabled": "/etc/systemd/system/multi-user.target.wants/armbian-firstrun.service",
        "apt_daily_timer": "/etc/systemd/system/timers.target.wants/apt-daily.timer",
        "apt_upgrade_timer": "/etc/systemd/system/timers.target.wants/apt-daily-upgrade.timer",
    }.items():
        states[key] = debug.query(key, "stat", path, optional=True) is not None
    manifest = build_manifest(directory, inventory, entry, result, extraction, stats, modules)
    os_release = parse_env(read(directory / "files/os-release"))
    require(os_release.get("VERSION_CODENAME") == entry["release"], "根系統發行版與清單不同")
    audit = audit_summary(directory, states)
    save_json(directory / "firstboot-audit.json", audit)
    manifest["firstboot_audit"] = {"path": str(directory / "firstboot-audit.json"), **digest(read(directory / "firstboot-audit.json"))}
    manifest["checks"]["os_codename"] = os_release["VERSION_CODENAME"]
    return manifest, partition


@contextmanager
def output_lock():
    with safe.open_root(OUTPUT) as root:
        try:
            with safe.open_file(root, "components.lock", create=True):
                pass
        except FileExistsError:
            pass
        with safe.open_file(root, "components.lock") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise safe.ArtifactError("已有組件盤點程序，不同時擷取兩份分割") from exc
            yield


def publish(directory, manifest, key, partition=None):
    manifest_path = OUTPUT / (key + ".json")
    pending = key + ".json.pending"
    save_json(OUTPUT / pending, manifest)
    with safe.open_root(OUTPUT) as root:
        os.link(pending, manifest_path.name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        os.unlink(pending, dir_fd=root)
        os.fsync(root)
    receipt = {"manifest": str(manifest_path), **digest(read(manifest_path)), "hardware_validated": False}
    save_json(directory / "receipt.json", receipt)
    if partition is not None:
        require(partition == directory / "root-partition.img" and directory.parent == OUTPUT,
                "只能清理本次衍生分割")
        with safe.open_root(directory) as root, safe.open_file(root, partition.name) as owned:
            info = matrix.identity(os.fstat(owned.fileno()))
            require(info == manifest["source_evidence"]["temporary_identity"], "暫存分割已被替換或修改；拒絕清理")
            os.unlink(partition.name, dir_fd=root)
        save_json(directory / "temporary-partition-removed.json", {"path": str(partition), "identity": info,
                  "reason": "本套組件及證據已完整保存；僅清除此程序建立的普通衍生分割"})
    print(json.dumps({"ok": True, **receipt}, ensure_ascii=False), flush=True)


def validate_manifest(manifest, inventory, entry, result):
    require(manifest["schema"] == "bpi-h618-customer-components-v1" and manifest["board"] == BOARD,
            "組件清單 schema 或板型不符")
    require(manifest["os"] == entry["release"] and manifest["desktop"] == entry["variant"] and
            manifest["kernel_release"] == RELEASE, "組件清單 OS、角色或版本不符")
    require(manifest["hardware_validated"] is False and
            all(manifest["preflight"].get(key) is True for key in
                ("source_verified", "legacy_initrd_verified", "mmc_support_verified")), "清單缺少靜態通過條件或混入硬體通過")
    require(manifest["image"] == {"path": str(Path(inventory["root"]) / entry["path"]),
                                  "compressed": result["compressed"], "raw": result["raw"]}, "清單映像身分不符")
    require(re.fullmatch(r"[0-9a-f]{8}-01", manifest["partuuid"]), "PARTUUID 格式不符")
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", manifest["root_uuid"]), "根 UUID 格式不符")
    require(all(isinstance(key, str) and isinstance(value, str) for key, value in manifest["original_env"].items()),
            "原環境必須保留純字串")
    for role, name in ROLES.items():
        item = manifest["files"][role]
        require(item["path"] == PATHS[name] and type(item["bytes"]) is int and 0 < item["bytes"] <= MAX_FILE and
                re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and re.fullmatch(r"[0-9a-f]{8}", item["crc32"]),
                "組件路徑、長度或雜湊格式不符：" + role)


def verify_existing():
    """重核對已交付小型組件與可信來源關聯，不重讀 XZ 或已刪除的分割。"""
    inventory, results = trusted_matrix()
    entries = []
    for entry in inventory["entries"]:
        key = f"{entry['release']}-{entry['variant']}"
        manifest_path = OUTPUT / (key + ".json")
        blob = read(manifest_path)
        manifest = parse_json(blob)
        validate_manifest(manifest, inventory, entry, results[entry["path"]])
        check_source(inventory, entry)
        directory = Path(manifest["files"]["kernel"]["evidence_path"]).parent.parent
        require(directory.parent == OUTPUT and re.fullmatch(re.escape(key) + r"(?:-[0-9]{3})?", directory.name),
                "衍生證據路徑不在本套私有目錄")
        for role, name in ROLES.items():
            item = manifest["files"][role]
            require(Path(item["evidence_path"]) == directory / "files" / name, "組件證據指向其他套")
            require(digest(read(item["evidence_path"])) == {field: item[field] for field in ("bytes", "sha256", "crc32")},
                    "已交付組件內容變動：" + key + "/" + role)
        files = {name: read(directory / "files" / name) for name in PATHS}
        require(manifest["original_env"] == parse_env(files["armbianEnv.txt"]), "已交付原環境不符")
        require(legacy(files["uInitrd"], 3) == files["initrd.img"], "已交付 initrd 配套不同")
        require(legacy(files["boot.scr"], 6) == files["boot.cmd"], "已交付 boot.scr 配套不同")
        legacy(files["fixup.scr"], 6)
        kernel_support(files["kernel.config"], files["modules.builtin"], files["kernel.Image"])
        source = manifest["source_evidence"]
        require(source["inventory_sha256"] == INVENTORY_SHA and source["summary_sha256"] == SUMMARY_SHA,
                "組件來源索引未固定")
        if key == "bookworm-minimal":
            old_blob = read(OLD / "evidence-index.json")
            require(digest(old_blob)["sha256"] == OLD_INDEX_SHA, "舊 Bookworm 索引已變動")
            old_index = {item["path"]: item for item in parse_json(old_blob)["entries"]}
            stats_blob = read(OLD / "queries/filesystem.stdout")
            require(digest(stats_blob)["sha256"] == old_index["queries/filesystem.stdout"]["sha256"], "舊根 UUID 證據已變動")
            prefix = read(OLD / "boot-prefix.bin")
            require(digest(prefix)["sha256"] == old_index["boot-prefix.bin"]["sha256"], "舊 MBR 證據已變動")
            stats = stats_blob.decode()
            audit = audit_summary(directory)
        else:
            extraction = parse_json(read(directory / "extraction.json"))
            expected = results[entry["path"]]
            require(extraction["raw"] == expected["raw"] and extraction["compressed"] == expected["compressed"] and
                    extraction["partition"] == expected["mbr"]["partitions"][0], "串流擷取紀錄與 T0 不符")
            stats = read(directory / "queries/filesystem.stdout").decode()
            reference = manifest["firstboot_audit"]
            require(digest(read(reference["path"])) == {field: reference[field] for field in ("bytes", "sha256", "crc32")},
                    "首次啟動證據已變動")
            audit = parse_json(read(reference["path"]))
            prefix = read(directory / "boot-prefix.bin")
            require(digest(prefix) == extraction["prefix"], "本次 MBR 證據已變動")
        require(matrix.check_mbr(prefix[:512], manifest["image"]["raw"]["bytes"], 31289507840) == results[entry["path"]]["mbr"],
                "已交付清單的 MBR 與 T0 不一致")
        require(manifest["partuuid"] == source["partuuid"] == f"{struct.unpack_from('<I', prefix, 440)[0]:08x}-01",
                "已交付 PARTUUID 與原 MBR 不符")
        require(root_uuid(stats, manifest["original_env"], files["fstab"]) == manifest["root_uuid"], "已交付根 UUID 不符")
        for name, reference in audit["scripts"].items():
            require(digest(read(directory / "files" / name)) ==
                    {field: reference[field] for field in ("bytes", "sha256", "crc32")}, "已擷取腳本有變動")
        entries.append({"os": entry["release"], "desktop": entry["variant"],
                        "manifest": {"path": str(manifest_path), **digest(blob)},
                        "root_uuid": manifest["root_uuid"], "partuuid": manifest["partuuid"],
                        "files": manifest["files"], "firstboot": audit})
    require(not list(OUTPUT.glob("*/root-partition.img")), "本次暫存分割尚未清理")
    return {"schema": "bpi-h618-customer-components-index-v1", "ok": True, "entries": entries,
            "count": len(entries), "hardware_validated": False, "large_images_reread": False,
            "temporary_partitions_remaining": 0, "source_inventory_sha256": INVENTORY_SHA,
            "source_summary_sha256": SUMMARY_SHA}


def main(argv=None):
    parser = safe.JsonArgumentParser(description="唯讀建立固定 EMAC 十套原配系統組件清單")
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--only", choices=COMBINATIONS, help="指定本次處理組合；不覆寫已交付清單")
    select.add_argument("--remaining", action="store_true", help="依序處理 Bookworm minimal 以外的九套")
    select.add_argument("--verify-existing", action="store_true", help="只重核對已交付組件；不重讀大映像")
    os.umask(0o077)
    try:
        args = parser.parse_args(argv)
        if args.verify_existing:
            with output_lock():
                report = verify_existing()
                destination = OUTPUT / "components-index.json"
                if not destination.exists():
                    save_json(destination, report)
                else:
                    require(parse_json(read(destination)) == report, "既有總索引不符；拒絕覆寫")
            print(json.dumps({"ok": True, "count": report["count"], "index": str(destination),
                              **digest(read(destination)), "hardware_validated": False}, ensure_ascii=False), flush=True)
            return 0
        inventory, results = trusted_matrix()
        if not OUTPUT.exists():
            mkdir(OUTPUT)
        with safe.open_root(OUTPUT):
            pass
        selected = COMBINATIONS[1:] if args.remaining else [args.only]
        with output_lock():
            for key in selected:
                require(not (OUTPUT / (key + ".json")).exists(), "清單已交付，不覆寫或重跑：" + key)
                entry = next(item for item in inventory["entries"] if f"{item['release']}-{item['variant']}" == key)
                directory = OUTPUT / key
                attempt = 1
                while directory.exists():
                    attempt += 1
                    directory = OUTPUT / f"{key}-{attempt:03d}"
                mkdir(directory)
                if key == "bookworm-minimal":
                    manifest = reuse_bookworm(directory, inventory, entry, results[entry["path"]])
                    validate_manifest(manifest, inventory, entry, results[entry["path"]])
                    publish(directory, manifest, key)
                else:
                    manifest, partition = extract_customer(directory, inventory, entry, results[entry["path"]])
                    validate_manifest(manifest, inventory, entry, results[entry["path"]])
                    publish(directory, manifest, key, partition)
        return 0
    except (safe.ArtifactError, OSError, ValueError, TypeError, KeyError, EOFError, struct.error, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, safe.ArtifactError) else "本機檔案讀取或解析失敗"
        print(json.dumps({"ok": False, "error": message, "error_type": type(exc).__name__,
                          "hardware_validated": False}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
