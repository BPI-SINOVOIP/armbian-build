#!/usr/bin/env python3
"""外部根交接前的固定 SD 保護層與可重現封裝；所有結果均非硬體核定。"""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import stat
import struct
import subprocess
import tempfile
import time
import zlib

if __package__:
    from . import bpi_lab_external_linux as linux
else:
    import bpi_lab_external_linux as linux

media, require, fields = linux.media, linux.require, linux.fields
SCHEMA = "bpi-lab-external-guard-v1"
BUNDLE_SCHEMA = "bpi-lab-external-python-bundle-v1"
MAX_ARCHIVE = 256 * 1024**2
HOOK_PATH = "scripts/init-bottom/00-bpi-external-guard"
PREFIX = "bpi-external-guard"
ELF_ARCH = {"arm": (32, "EM_ARM"), "arm64": (64, "EM_AARCH64"), "riscv64": (64, "EM_RISCV")}
INIT_PROFILES = {
    ("060ff11d8b8a2bbceddcc4a8034b11d1b55ae163e6255cc7e98e8e71c5dbabad",
     "1c12fdb98f6b03c47e2a5f6d7e6e019ac016345eb0fd8300b808b128e70f00b5"): "initramfs-tools-0.142+deb12u3",
    ("d9d1775d643f6f70a1a6f646dfe023052765f558a167ec58951ad2f1013b3e46",
     "6496880098f6e189d6350feadd0d7ea9e73a87767937eb937e9fe3dca9947c5b"): "initramfs-tools-0.140ubuntu13.5",
    ("26f5f5a706dd766158c01c7a9f4c75814a77519e9a4f7489a3cf8cf6312e8aef",
     "6358cb0bc4784bb8dc13d5c9beaf65d5e88f51c9a459daeed0f1311bfc07aa96"): "bpi-m1-noble-source-20260918",
    ("0a9bb34973c78987922b57f010e99c36ddf102e8a5a2fd198385566539f5d6d5",
     "b328b49756e00ec7c692dcdd28526831def5e9e0d7a0d0963087b450c2db4743"): "bpi-m1-trixie-source-20260918",
    ("eca0482dafc96e4221ecc67b0c65cb3f7a86e4ed142419b90df741017221570c",
     "2e5b29a8a2ea6cc6429f2353fb2f1166b693f50c30b6a3394d1bf450c804eb88"): "bpi-m1-resolute-source-20260918",
}
GuardError = media.MediaError


def customer_file(root, name, ops, *, optional=False):
    root = Path(root)
    path = root / name
    if optional and not ops.exists(path):
        return b""
    resolved = ops.resolve(path)
    require(resolved.is_relative_to(ops.resolve(root)), "客戶設定路徑逃出已掛載根")
    return ops.read(resolved, 1024**2)


def validate_fstab(blob, inventory, sd, root_path, ops):
    """只解析設定，不執行客戶工具或 shell；未知裝置解析方式直接拒絕。"""
    sd_numbers = {sd["devnum"], *(row["devnum"] for row in sd["partitions"])}
    records = []
    for raw in blob.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        columns = line.split()
        if "#" in columns:
            columns = columns[:columns.index("#")]
        require(len(columns) == 6, "fstab 欄位缺少或含不支援的跳脫")
        source, mountpoint, filesystem = (linux.unescape(item) for item in columns[:3])
        if filesystem in ("tmpfs", "proc", "sysfs", "devpts", "cgroup2", "debugfs"):
            require(source in ("none", filesystem), "虛擬掛載含未知來源")
            continue
        if source.startswith(("UUID=", "LABEL=")):
            prefix, value = source.split("=", 1)
            key = "uuid" if prefix == "UUID" else "label"
            found = [row for row in inventory if row[key] == value]
            require(len(found) == 1, "fstab UUID／LABEL 在所有可見媒體不是唯一")
            number = found[0]["devnum"]
        elif source.startswith("/dev/"):
            node = ops.stat(source)
            require(stat.S_ISBLK(node.st_mode), "fstab 裝置不是區塊節點")
            number = f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}"
            require(sum(row["devnum"] == number for row in inventory) == 1, "fstab 裝置未列入完整盤點")
        elif source.startswith("/") and filesystem == "swap":
            candidate = ops.resolve(Path(root_path) / source.lstrip("/"))
            require(candidate.is_relative_to(ops.resolve(root_path)), "swap 檔逃出客戶根")
            node = ops.stat(candidate)
            require(stat.S_ISREG(node.st_mode), "swap 檔不是一般檔案")
            number = f"{os.major(node.st_dev)}:{os.minor(node.st_dev)}"
        else:
            raise GuardError("fstab 含未核定的裝置或檔案系統解析方式")
        require(number not in sd_numbers, "客戶 fstab／boot／swap 指向受保護 SD")
        records.append({"source": source, "mountpoint": mountpoint, "fs_type": filesystem, "devnum": number})
    return records


def protect(expected, *, root_path="/root", sysroot="/sys", procroot="/proc", devroot="/dev", ops=None, timeout=21600):
    """僅供 init-bottom 呼叫；失敗不回復可寫，也不交接客戶服務。"""
    linux.validate_expected(expected)
    require(str(root_path) == "/root" or ops is not None, "正式 guard 僅核定 initramfs 的 /root 掛載點")
    ops = linux.NativeOps() if ops is None else ops
    require(bool(ops), "拒絕假值 I/O 替身，避免底層退回真實裝置操作")
    require(type(timeout) in (int, float) and linux.math.isfinite(timeout) and 0 < timeout <= 86400, "保護總期限無效")
    deadline = time.monotonic() + timeout
    args = {"sysroot": sysroot, "procroot": procroot, "devroot": devroot, "ops": ops}
    opened = []
    with ops.watch() as watch:
        target = media.inspect(expected["target"], **args, require_idle=False)
        sd = media.inspect_sd(expected["protected_sd"], **args, require_idle=True)
        inventory = linux.probe_inventory(**args, deadline=deadline)
        mounted = linux.mounts(ops, procroot)
        root = linux.root_observation(mounted, inventory, root_path, ops)
        linux.validate_root(root, target, inventory, expected)
        target_fd = ops.open_device(target["device"])
        opened.append(target_fd)
        try:
            media.check_fd(target_fd, target, ops=ops)
            fstab = customer_file(root_path, "etc/fstab", ops)
            settings = validate_fstab(fstab, inventory, sd, root_path, ops)
            for name in ("etc/crypttab",):
                blob = customer_file(root_path, name, ops, optional=True)
                require(not any(line.strip() and not line.lstrip().startswith(b"#") for line in blob.splitlines()),
                        "首版不接受客戶加密裝置設定")
            sd_rows = [row for row in inventory if row["parent"] == sd["sysfs_path"]]
            require({row["devnum"] for row in sd_rows} == {sd["devnum"], *(part["devnum"] for part in sd["partitions"])},
                    "固定 SD 分割盤點不完整")
            changes = []
            for row in sorted(sd_rows, key=lambda row: row["partition_index"] or 0):
                linux.remaining(deadline)
                fd = ops.open_device(row["device"])
                opened.append(fd)
                node = ops.fstat(fd)
                require(stat.S_ISBLK(node.st_mode) and f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}" == row["devnum"],
                        "SD 保護描述符身分漂移")
                require(struct.unpack("=Q", ops.ioctl(fd, 0x80081272, bytes(8)))[0] == row["bytes"], "SD 分割容量漂移")
                if row["devnum"] == sd["devnum"]:
                    media.check_fd(fd, sd, ops=ops)
                before = struct.unpack("=i", ops.ioctl(fd, linux.BLKROGET, bytes(4)))[0]
                ops.ioctl(fd, linux.BLKROSET, struct.pack("=i", 1))
                after = struct.unpack("=i", ops.ioctl(fd, linux.BLKROGET, bytes(4)))[0]
                require(before in (0, 1) and after == 1, "SD BLKROSET 未由 BLKROGET 證實")
                changes.append({"devnum": row["devnum"], "bytes": row["bytes"], "before": before, "after": after})
            observation = linux.observe(expected, linux.digest(expected), root_path=root_path, **args,
                                        timeout=linux.remaining(deadline), require_host_key=False)
            require(customer_file(root_path, "etc/fstab", ops) == fstab, "保護期間客戶 fstab 改變")
            media.check_fd(target_fd, target, ops=ops)
            for fd in opened[1:]:
                require(struct.unpack("=i", ops.ioctl(fd, linux.BLKROGET, bytes(4)))[0] == 1, "交接前 SD 唯讀狀態被撤銷")
            watch.poll()
        finally:
            for fd in reversed(opened):
                ops.close(fd)
    linux.remaining(deadline)
    return {"schema": "bpi-lab-external-guard-result-v1", "status": "protected", "hardware_validated": False,
            "expected_sha256": linux.digest(expected), "observation": observation,
            "fstab_sha256": hashlib.sha256(fstab).hexdigest(), "fstab": settings, "read_only_changes": changes,
            "guard_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _name(value):
    require(type(value) is str and len(value) <= 4096 and "\0" not in value, "封裝路徑無效")
    while value.startswith("./"):
        value = value[2:]
    require(value and not value.startswith("/") and all(part not in ("", ".", "..") for part in value.split("/")),
            "封裝路徑含上層跳轉或不是相對路徑")
    return value


def parse_archive(blob):
    require(type(blob) is bytes and len(blob) <= MAX_ARCHIVE, "initramfs 超過大小上限")
    compression = "none"
    if blob.startswith(b"\x1f\x8b"):
        decoder = zlib.decompressobj(31)
        blob = decoder.decompress(blob, MAX_ARCHIVE + 1)
        require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(blob) <= MAX_ARCHIVE,
                "只接受單一有界 gzip initramfs")
        compression = "gzip"
    require(blob.startswith((b"070701", b"070702")), "只支援明確的 newc 或單一 gzip newc；不猜 uInitrd 容器")
    result, position, regulars = {}, 0, {}
    while True:
        header = blob[position:position + 110]
        require(len(header) == 110 and header[:6] in (b"070701", b"070702")
                and re.fullmatch(rb"[0-9a-fA-F]{104}", header[6:]), "newc 標頭不完整")
        values = [int(header[offset:offset + 8], 16) for offset in range(6, 110, 8)]
        inode, mode, uid, gid, links, mtime, size, devmajor, devminor, major, minor, namesize, checksum = values
        require(1 <= namesize <= 4096, "newc 名稱長度超界")
        name = blob[position + 110:position + 110 + namesize]
        require(len(name) == namesize and name.endswith(b"\0") and b"\0" not in name[:-1], "newc 名稱不完整")
        start = (position + 110 + namesize + 3) & ~3
        end = start + size
        require(end <= len(blob), "newc 資料截斷")
        data = blob[start:end]
        require(header[:6] != b"070702" or sum(data) & 0xFFFFFFFF == checksum, "newc CRC 不符")
        position = (end + 3) & ~3
        if name == b"TRAILER!!!\0":
            require(size == 0 and not blob[position:].strip(b"\0"), "newc 結尾後含另一個未核定封裝")
            break
        name = name[:-1].decode("utf-8")
        if name in (".", "./"):
            require(stat.S_ISDIR(mode), "newc 根項目不是目錄")
            continue
        name = _name(name)
        require(name not in result and len(result) < 100000, "newc 重複路徑或項目超界")
        require(stat.S_IFMT(mode) in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK), "newc 含不支援節點")
        require(links >= 1 and (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or links == 1),
                "newc 連結數無效或硬連結不是一般檔案")
        result[name] = {"mode": mode, "uid": uid, "gid": gid, "mtime": mtime, "data": data,
                        "devmajor": devmajor, "devminor": devminor, "major": major, "minor": minor}
        if stat.S_ISREG(mode):
            regulars.setdefault((devmajor, devminor, inode), []).append((name, links))
    for members in regulars.values():
        if not any(links > 1 for _, links in members):
            continue
        names = tuple(sorted(name for name, _ in members))
        require(all(links == len(names) for _, links in members), "newc 硬連結群組不完整或連結數衝突")
        metadata = {key: value for key, value in result[names[0]].items() if key != "data"}
        bodies = [result[name]["data"] for name in names if result[name]["data"]]
        require(len(bodies) <= 1, "newc 硬連結含多份資料本體")
        body = bodies[0] if bodies else b""
        for name in names:
            item = result[name]
            require({key: value for key, value in item.items() if key != "data"} == metadata,
                    "newc 硬連結中繼資料衝突")
            result[name] = {**item, "data": body, "hardlinks": names}
    return result, compression


def entry(data, mode=0o100644):
    return {"mode": mode, "uid": 0, "gid": 0, "mtime": 0, "data": data,
            "devmajor": 0, "devminor": 0, "major": 0, "minor": 0}


def archive(entries, compression="none"):
    require(compression in ("none", "gzip"), "未知 initramfs 壓縮格式")
    require("TRAILER!!!" not in entries, "封裝路徑使用保留結尾名稱")
    checked = set()
    for name, item in entries.items():
        names = item.get("hardlinks")
        if names is None:
            continue
        require(type(names) is tuple and 2 <= len(names) <= len(entries)
                and all(type(member) is str for member in names)
                and tuple(sorted(set(names))) == names and name in names, "封裝硬連結群組無效")
        if names in checked:
            continue
        require(stat.S_ISREG(item["mode"]) and all(entries.get(member) == item for member in names),
                "封裝硬連結群組不完整、非一般檔案或內容衝突")
        checked.add(names)
    indices = {name: index for index, name in enumerate(sorted(entries), 1)}
    output = bytearray()
    for index, (name, item) in enumerate([*sorted(entries.items()), ("TRAILER!!!", entry(b""))], 1):
        _name(name)
        encoded = name.encode() + b"\0"
        names = item.get("hardlinks")
        inode, links = (indices[names[0]], len(names)) if names else (index, 1)
        data = item["data"] if not names or name == names[-1] else b""
        values = (inode, item["mode"], item["uid"], item["gid"], links, item["mtime"], len(data),
                  item["devmajor"], item["devminor"], item["major"], item["minor"], len(encoded), 0)
        output.extend(b"070701" + b"".join(f"{value:08x}".encode() for value in values) + encoded)
        output.extend(bytes(-len(output) % 4))
        output.extend(data)
        output.extend(bytes(-len(output) % 4))
        require(len(output) <= MAX_ARCHIVE, "衍生 initramfs 超界")
    return gzip.compress(bytes(output), compresslevel=9, mtime=0) if compression == "gzip" else bytes(output)


def resolve_entry(entries, path):
    parts = list(PurePosixPath("/" + path.lstrip("/")).parts[1:])
    resolved, traversals = [], 0
    while parts:
        piece = parts.pop(0)
        require(piece not in ("", ".", ".."), "initramfs 相依路徑無效")
        current = "/".join([*resolved, piece])
        item = entries.get(current)
        require(item is not None, "initramfs 相依檔不存在：" + current)
        if stat.S_ISLNK(item["mode"]):
            traversals += 1
            require(traversals <= 40, "initramfs 符號連結循環")
            target = item["data"].decode()
            require("\0" not in target and target, "initramfs 符號連結無效")
            absolute = target if target.startswith("/") else "/" + "/".join([*resolved, target])
            depth = 0
            for part in absolute.split("/"):
                if part == "..":
                    depth -= 1
                    require(depth >= 0, "initramfs 符號連結逃出根")
                elif part not in ("", "."):
                    depth += 1
            parts, resolved = list(PurePosixPath(posixpath.normpath(absolute)).parts[1:]) + parts, []
        else:
            require(not parts or stat.S_ISDIR(item["mode"]), "initramfs 相依路徑穿越非目錄")
            resolved.append(piece)
    name = "/".join(resolved)
    return name, entries[name]


def elf_info(blob, architecture):
    from elftools.elf.elffile import ELFFile
    require(blob.startswith(b"\x7fELF"), "執行工具不是 ELF")
    try:
        elf = ELFFile(io.BytesIO(blob))
        require((elf.elfclass, elf["e_machine"]) == ELF_ARCH[architecture] and elf.little_endian,
                "initramfs 工具不是指定目標架構")
        require(elf["e_type"] in ("ET_EXEC", "ET_DYN"), "ELF 不是可載入映像")
        loads, interpreter, needed = [], None, []
        for segment in elf.iter_segments():
            require(segment["p_offset"] + segment["p_filesz"] <= len(blob), "ELF segment 截斷")
            if segment["p_type"] == "PT_LOAD":
                loads.append(segment)
            elif segment["p_type"] == "PT_INTERP":
                require(interpreter is None, "ELF 含多個載入器")
                interpreter = segment.get_interp_name()
            elif segment["p_type"] == "PT_DYNAMIC":
                for tag in segment.iter_tags():
                    require(tag.entry.d_tag not in ("DT_RPATH", "DT_RUNPATH"), "首版要求明確相依目錄，不接受 ELF 搜尋路徑覆寫")
                    if tag.entry.d_tag == "DT_NEEDED":
                        require(re.fullmatch(r"[A-Za-z0-9._+-]+", tag.needed), "ELF 相依名稱無效")
                        needed.append(tag.needed)
        require(loads, "ELF 缺少可載入 segment")
        return {"interpreter": interpreter, "needed": needed}
    except GuardError:
        raise
    except Exception as exc:
        raise GuardError("ELF 結構不完整") from exc


def executable_closure(entries, paths, architecture, library_dirs):
    seen, pending = {}, list(paths)
    while pending:
        name, item = resolve_entry(entries, pending.pop())
        if name in seen:
            continue
        require(stat.S_ISREG(item["mode"]) and item["mode"] & 0o444, "ELF 相依不是可讀的一般檔案")
        info = elf_info(item["data"], architecture)
        seen[name] = {"path": "/" + name, "sha256": hashlib.sha256(item["data"]).hexdigest()}
        if info["interpreter"]:
            pending.append(info["interpreter"])
        for needed in info["needed"]:
            matches = []
            for directory in library_dirs:
                try:
                    found, _ = resolve_entry(entries, directory + "/" + needed)
                except GuardError:
                    continue
                if found not in matches:
                    matches.append(found)
            require(len(matches) == 1, "ELF 相依缺少或存在多份不同路徑：" + needed)
            pending.append(matches[0])
    return [seen[key] for key in sorted(seen)]


def _bundle(value):
    fields(value, "schema architecture archive python blkid library_dirs stdlib_dirs init_profile")
    require(value["schema"] == BUNDLE_SCHEMA and value["architecture"] in ELF_ARCH, "救援 Python bundle 格式不符")
    for key in ("python", "blkid"):
        require(type(value[key]) is str and re.fullmatch(r"/[A-Za-z0-9_./+-]+", value[key]), "bundle 工具路徑無效")
        _name(value[key].lstrip("/"))
    require(value["blkid"] == linux.BLKID, "固定採樣器只接受 /usr/sbin/blkid")
    for key in ("library_dirs", "stdlib_dirs"):
        require(type(value[key]) is list and 0 < len(value[key]) <= 32, "bundle 相依目錄缺少")
        for name in value[key]:
            require(type(name) is str and re.fullmatch(r"/[A-Za-z0-9_./+-]+", name), "bundle 相依目錄不是固定安全絕對路徑")
            _name(name.lstrip("/"))
    fields(value["init_profile"], "init_sha256 functions_sha256")
    require(all(re.fullmatch(r"[0-9a-f]{64}", item) for item in value["init_profile"].values()), "initramfs 來源摘要無效")
    return value


def _init_order(entries, profile):
    require((profile["init_sha256"], profile["functions_sha256"]) in INIT_PROFILES,
            "未知 initramfs 執行流程；需先核對完整來源，不接受自行重綁摘要")
    _, init = resolve_entry(entries, "/init")
    _, functions = resolve_entry(entries, "/scripts/functions")
    require(hashlib.sha256(init["data"]).hexdigest() == profile["init_sha256"]
            and hashlib.sha256(functions["data"]).hexdigest() == profile["functions_sha256"], "initramfs 執行入口不是已核對來源")
    require(init["mode"] & 0o111 and init["data"].startswith(b"#!/bin/sh\n"), "initramfs init 不是已知 shell 入口")
    active = [line.strip() for line in init["data"].decode().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    require(active.count("run_scripts /scripts/init-bottom") == 1 and active.count("mountroot") == 1,
            "initramfs 缺少唯一實際 root 掛載與 init-bottom 呼叫")
    handoff = [index for index, line in enumerate(active) if line.startswith("exec run-init ")]
    require(len(handoff) == 1 and active.index("mountroot") < active.index("run_scripts /scripts/init-bottom") < handoff[0],
            "init-bottom 未位於根掛載後與客戶 init 前")
    function = functions["data"].decode()
    require(re.search(r'(?m)^run_scripts\(\)\s*\n\{\s*\n\s*initdir=\$\{1\}\s*\n'
                      r'\s*\[ ! -d "\$\{initdir\}" \] && return\s*\n\s*shift\s*\n'
                      r'\s*\. "\$\{initdir\}/ORDER"\s*\n\}', function), "未知 run_scripts 執行語意")
    _, order = resolve_entry(entries, "/scripts/init-bottom/ORDER")
    require(stat.S_ISREG(order["mode"]) and b"bpi-external-guard" not in order["data"], "init-bottom 排程已含 guard 或不是一般檔案")
    return order["data"]


def hook(python):
    return ("#!/bin/sh\n"
            "case \"${1:-}\" in prereqs) exit 0 ;; esac\n"
            f"if [ \"${{rootmnt:-}}\" = /root ] && {python} -I -S -B /{PREFIX}/run.py; then\n"
            "    exit 0\nfi\n"
            "printf '%s\\n' '固定 SD 保護未完成；禁止交接客戶服務' >&2\n"
            "while :; do :; done\n").encode()


def _library_path(bundle, root=None):
    return ":".join(str(Path(root) / name.lstrip("/")) if root is not None else name for name in bundle["library_dirs"])


def _order_invocation(bundle):
    # 指派必須先於 exec hook，讓其 /bin/sh 解譯器也取得相同相依路徑。
    return ("if ! LD_LIBRARY_PATH=" + _library_path(bundle) + " /" + HOOK_PATH + "; then\n"
            "    printf '%s\\n' '固定 SD 保護入口無法執行；禁止交接客戶服務' >&2\n"
            "    while :; do :; done\n"
            "fi\n").encode()


def _put(entries, name, value):
    name = _name(name)
    for parent in reversed(PurePosixPath(name).parents):
        if str(parent) == ".":
            continue
        old = entries.setdefault(str(parent), entry(b"", stat.S_IFDIR | 0o755))
        require(stat.S_ISDIR(old["mode"]), "新增 guard 路徑穿越符號連結或非目錄")
    require(name not in entries, "衍生層不覆蓋既有檔案：" + name)
    entries[name] = value


def _probe(entries, bundle, emulator, timeout):
    """執行指定目標 runtime 的固定匯入探測；不執行原 init 或客戶命令。"""
    if __package__:
        from . import bpi_lab_deploy as deploy
    else:
        import bpi_lab_deploy as deploy
    runner, deadline = [], time.monotonic() + timeout
    if emulator is not None:
        deploy.checked_bytes(emulator, 128 * 1024**2)
        wanted = {"arm": "qemu-arm-static", "arm64": "qemu-aarch64-static", "riscv64": "qemu-riscv64-static"}[bundle["architecture"]]
        require(Path(emulator["path"]).name == wanted and os.access(emulator["path"], os.X_OK), "模擬器名稱或執行權限不符")
    else:
        require(platform.machine() in linux.ARCHITECTURES[bundle["architecture"]], "跨架構探測須指定固定摘要的 QEMU，不使用主機 Python")
    with tempfile.TemporaryDirectory(prefix="bpi-external-runtime-") as temporary:
        root = Path(temporary)
        # 將所有連結解析在封裝內，探測樹不建立能逃往主機的絕對符號連結。
        for name, item in entries.items():
            if not stat.S_ISREG(item["mode"]):
                continue
            resolved, _ = resolve_entry(entries, name)
            path = root / resolved
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["data"])
            path.chmod(stat.S_IMODE(item["mode"]))
        for name, item in entries.items():
            if stat.S_ISLNK(item["mode"]):
                try:
                    target, _ = resolve_entry(entries, name)
                except GuardError:
                    # 執行相依已完整解析；不搬入未引用的 /proc 等執行時連結。
                    continue
                destination = root / name
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(os.path.relpath(root / target, destination.parent))
        if emulator is not None:
            runner = [emulator["path"], "-L", str(root)]
        code = ("import base64,contextlib,copy,fcntl,hashlib,json,math,os,pathlib,re,socket,stat,struct,subprocess,time,uuid,zlib,sys;"
                "r=pathlib.Path(sys.argv[1]).resolve();"
                "assert sys.version_info>=(3,9);"
                "assert all(pathlib.Path(p).resolve().is_relative_to(r) for p in sys.path if p);"
                f"sys.path.insert(0,str(r/{PREFIX!r}));import bpi_lab_external_guard;"
                "print(json.dumps({'python_major':3,'machine':os.uname().machine,'imports':'complete'}))")
        environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
        shell_command = [*runner, str(root / "bin/sh"), "-c", "printf '%s\\n' bpi-external-shell-v1"]
        bootstrap = subprocess.run(shell_command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=linux.remaining(deadline), env=environment)
        require(bootstrap.returncode == 0 and bootstrap.stdout == b"bpi-external-shell-v1\n" and not bootstrap.stderr,
                "init 初始 shell 在 guard 設定前無法執行")
        environment = {**environment, "LD_LIBRARY_PATH": _library_path(bundle, root)}
        python = subprocess.run([*runner, str(root / bundle["python"].lstrip("/")), "-I", "-S", "-B", "-c", code, str(root)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=linux.remaining(deadline), env=environment)
        require(python.returncode == 0 and len(python.stdout) <= 65536 and len(python.stderr) <= 65536, "目標救援 Python 實際探測失敗")
        observed = linux.parse_json(python.stdout)
        require(observed == {"python_major": 3, "machine": linux.ARCHITECTURES[bundle["architecture"]][0], "imports": "complete"},
                "目標 Python 架構或標準函式庫探測不符")
        blkid = subprocess.run([*runner, str(root / bundle["blkid"].lstrip("/")), "--version"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=linux.remaining(deadline), env=environment)
        require(blkid.returncode == 0 and 0 < len(blkid.stdout) <= 65536 and len(blkid.stderr) <= 65536, "目標 blkid 實際探測失敗")
        shell = subprocess.run(shell_command,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=linux.remaining(deadline), env=environment)
        require(shell.returncode == 0 and shell.stdout == b"bpi-external-shell-v1\n" and not shell.stderr,
                "目標 init-bottom shell 實際探測失敗")
        linux.remaining(deadline)
        return {"schema": "bpi-lab-external-runtime-probe-v1", "hardware_validated": False,
                "architecture": bundle["architecture"], "python": observed,
                "blkid_stdout_sha256": hashlib.sha256(blkid.stdout).hexdigest(), "emulator": emulator,
                "runtime_archive_sha256": bundle["archive"]["sha256"]}


def _assemble(original, expected, bundle, bundle_archive):
    entries, compression = parse_archive(original)
    require(bundle["architecture"] == expected["architecture"], "runtime bundle 與客戶架構不同")
    additions, _ = parse_archive(bundle_archive)
    order = _init_order(entries, bundle["init_profile"])
    for name, item in additions.items():
        require(not name.startswith(("scripts/", PREFIX + "/")) and name != "init", "runtime bundle 不得替換 init 執行鏈")
        if name in entries:
            require(item == entries[name], "runtime bundle 與原 initramfs 檔案衝突：" + name)
        else:
            _put(entries, name, item)
    roots = [bundle["python"], bundle["blkid"], "/bin/sh"]
    for path in roots:
        _, item = resolve_entry(entries, path)
        require(stat.S_ISREG(item["mode"]) and item["mode"] & 0o111, "initramfs 工具無可執行權限")
    for name, item in additions.items():
        if stat.S_ISREG(item["mode"]) and item["data"].startswith(b"\x7fELF"):
            roots.append("/" + name)
    dependencies = executable_closure(entries, roots, bundle["architecture"], bundle["library_dirs"])
    for directory in bundle["stdlib_dirs"]:
        _, item = resolve_entry(entries, directory)
        require(stat.S_ISDIR(item["mode"]), "Python 標準函式庫目錄缺少")
    for module in (media, linux):
        _put(entries, PREFIX + "/" + Path(module.__file__).name, entry(Path(module.__file__).read_bytes()))
    _put(entries, PREFIX + "/" + Path(__file__).name, entry(Path(__file__).read_bytes()))
    _put(entries, PREFIX + "/expected.json", entry(linux.encoded(expected)))
    launch = ("import json,os,sys\n" + f"sys.path.insert(0,'/{PREFIX}')\n"
              "from bpi_lab_external_guard import protect\n"
              f"with open('/{PREFIX}/expected.json') as stream: expected=json.load(stream)\n"
              "result=protect(expected)\n"
              "with open('/run/bpi-external-guard.json','x') as stream:\n"
              " json.dump(result,stream,ensure_ascii=True,sort_keys=True);stream.flush();os.fsync(stream.fileno())\n")
    _put(entries, PREFIX + "/run.py", entry(launch.encode()))
    guard_hook = hook(bundle["python"])
    _put(entries, HOOK_PATH, entry(guard_hook, 0o100755))
    entries["scripts/init-bottom/ORDER"] = {**entries["scripts/init-bottom/ORDER"],
                                             "data": _order_invocation(bundle) + order}
    return entries, compression, dependencies


def _validate_probe(probe, bundle):
    fields(probe, "schema hardware_validated architecture python blkid_stdout_sha256 emulator runtime_archive_sha256")
    require(probe["schema"] == "bpi-lab-external-runtime-probe-v1" and probe["hardware_validated"] is False
            and probe["architecture"] == bundle["architecture"]
            and probe["python"] == {"python_major": 3, "machine": linux.ARCHITECTURES[bundle["architecture"]][0], "imports": "complete"}
            and re.fullmatch(r"[0-9a-f]{64}", probe["blkid_stdout_sha256"])
            and probe["runtime_archive_sha256"] == bundle["archive"]["sha256"], "runtime 實際探測未綁定此 bundle 或缺少完整結果")


def build(original_initrd, expected, runtime_bundle, output, *, emulator=None, timeout=120):
    if __package__:
        from . import bpi_lab_deploy as deploy
    else:
        import bpi_lab_deploy as deploy
    linux.validate_expected(expected)
    require(type(timeout) in (int, float) and 0 < timeout <= 600, "runtime 探測期限無效")
    original = deploy.checked_bytes(original_initrd, MAX_ARCHIVE)
    bundle = _bundle(deploy.load(runtime_bundle))
    entries, compression, dependencies = _assemble(original, expected, bundle, deploy.checked_bytes(bundle["archive"], MAX_ARCHIVE))
    probe = _probe(entries, bundle, emulator, timeout)
    _validate_probe(probe, bundle)
    guard_hook = hook(bundle["python"])
    derived = archive(entries, compression)
    output = deploy.new_directory(output)
    deploy.save(output, "initrd.guard", derived)
    manifest = {"schema": SCHEMA, "status": "prepared", "hardware_validated": False,
                "derived": True, "original_boot_chain_verified": False, "expected_sha256": linux.digest(expected),
                "original_initrd": original_initrd, "runtime_bundle": runtime_bundle, "runtime_archive": bundle["archive"],
                "derived_initrd": {"path": str(output / "initrd.guard"), "sha256": hashlib.sha256(derived).hexdigest(), "bytes": len(derived)},
                "format": "gzip-newc" if compression == "gzip" else "newc", "architecture": bundle["architecture"],
                "hook": {"path": "/" + HOOK_PATH, "sha256": hashlib.sha256(guard_hook).hexdigest()},
                "dependencies": dependencies, "runtime_probe": probe,
                "sources": {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                            for module in (media, linux)},
                "guard_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    validate_manifest(manifest, expected, original_initrd=original_initrd)
    deploy.save(output, "guard-manifest.json", linux.encoded(manifest))
    return manifest


def validate_manifest(manifest, expected, *, original_initrd=None):
    if __package__:
        from . import bpi_lab_deploy as deploy
    else:
        import bpi_lab_deploy as deploy
    linux.validate_expected(expected)
    fields(manifest, "schema status hardware_validated derived original_boot_chain_verified expected_sha256 original_initrd "
                     "runtime_bundle runtime_archive derived_initrd format architecture hook dependencies runtime_probe sources guard_source_sha256")
    require(manifest["schema"] == SCHEMA and manifest["status"] == "prepared"
            and manifest["hardware_validated"] is False and manifest["derived"] is True
            and manifest["original_boot_chain_verified"] is False and manifest["expected_sha256"] == linux.digest(expected),
            "guard 清單不是本次明示衍生層")
    require(original_initrd is None or manifest["original_initrd"] == original_initrd, "guard 借用另一份原 initrd")
    original = deploy.checked_bytes(manifest["original_initrd"], MAX_ARCHIVE)
    bundle = _bundle(deploy.load(manifest["runtime_bundle"]))
    require(manifest["runtime_archive"] == bundle["archive"] and manifest["architecture"] == expected["architecture"] == bundle["architecture"],
            "guard runtime bundle 不符")
    wanted, compression, dependencies = _assemble(original, expected, bundle, deploy.checked_bytes(bundle["archive"], MAX_ARCHIVE))
    ref = manifest["derived_initrd"]
    fields(ref, "path sha256 bytes")
    blob = deploy.checked_bytes({key: ref[key] for key in ("path", "sha256")}, MAX_ARCHIVE)
    require(len(blob) == ref["bytes"], "衍生 initrd 長度不符")
    entries, actual_compression = parse_archive(blob)
    require(actual_compression == compression and manifest["format"] == ("gzip-newc" if compression == "gzip" else "newc"),
            "衍生 initrd 壓縮格式變動")
    require(entries == wanted and blob == archive(wanted, compression), "衍生 initrd 不是原件、固定 guard 與 bundle 的可重現組合")
    require(manifest["dependencies"] == dependencies, "runtime 相依清單缺少或未完整重驗")
    require(entries[HOOK_PATH] == entry(hook(bundle["python"]), 0o100755)
            and manifest["hook"] == {"path": "/" + HOOK_PATH, "sha256": hashlib.sha256(hook(bundle["python"])).hexdigest()}, "guard hook 內容或執行權限不同")
    require(entries[PREFIX + "/expected.json"]["data"] == linux.encoded(expected), "封裝預期根配置不同")
    for module in (media, linux):
        name = Path(module.__file__).name
        source = Path(module.__file__).read_bytes()
        require(entries[PREFIX + "/" + name]["data"] == source and manifest["sources"].get(name) == hashlib.sha256(source).hexdigest(),
                "guard 共用來源已改變")
    source = Path(__file__).read_bytes()
    require(entries[PREFIX + "/" + Path(__file__).name]["data"] == source
            and manifest["guard_source_sha256"] == hashlib.sha256(source).hexdigest(), "guard 本體已改變")
    _validate_probe(manifest["runtime_probe"], bundle)
    for dependency in manifest["dependencies"]:
        _, item = resolve_entry(entries, dependency["path"])
        require(hashlib.sha256(item["data"]).hexdigest() == dependency["sha256"], "runtime 相依摘要不同")
        elf_info(item["data"], expected["architecture"])
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="建立明示衍生的外部根 SD 保護 initramfs")
    for name in ("original-initrd", "expected", "runtime-bundle"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--emulator", type=Path)
    parser.add_argument("--emulator-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if __package__:
        from . import bpi_lab_deploy as deploy
    else:
        import bpi_lab_deploy as deploy
    def reference(name):
        return {"path": str(getattr(args, name).absolute()), "sha256": getattr(args, name + "_sha256")}
    try:
        result = build(reference("original_initrd"), deploy.load(reference("expected")), reference("runtime_bundle"),
                       args.output.absolute(), emulator=reference("emulator") if args.emulator else None)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print(json.dumps({"status": "blocked", "hardware_validated": False, "reason": "guard 封裝或執行探測未通過"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
