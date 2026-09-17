#!/usr/bin/env python3
"""外部測試根的固定同次採樣；不寫媒體，不以觀測結果授予硬體資格。"""

from contextlib import closing
import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time
import uuid
import zlib

if __package__:
    from . import bpi_lab_media as media
else:
    import bpi_lab_media as media

EXPECTED_SCHEMA = "bpi-lab-external-linux-expected-v1"
OBSERVATION_SCHEMA = "bpi-lab-external-linux-observation-v1"
VALIDATION_SCHEMA = "bpi-lab-external-linux-validation-v1"
COLLECTION_SCHEMA = "bpi-lab-external-linux-collection-v1"
ARCHITECTURES = {"arm": ("armv7l", "armv6l"), "arm64": ("aarch64",), "riscv64": ("riscv64",)}
MAX_OUTPUT = 4 * 1024**2
BLKROGET, BLKROSET = 0x125E, 0x125D
BLKID = "/usr/sbin/blkid"
GUARD_SOURCE_SHA256 = None
require, fields = media.require, media.fields
ExternalLinuxError = media.MediaError


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def parse_json(blob):
    require(type(blob) is bytes and len(blob) <= MAX_OUTPUT, "JSON 證據超界")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "JSON 欄位重複")
            result[key] = value
        return result
    def constant(_):
        raise ExternalLinuxError("JSON 不允許非有限數值")
    return json.loads(blob, object_pairs_hook=pairs, parse_constant=constant)


def validate_expected(expected):
    fields(expected, "schema architecture kernel_release dt_compatible root root_label root_fs_type target protected_sd" +
           (" root_growth" if "root_growth" in expected else ""))
    require(expected["schema"] == EXPECTED_SCHEMA and expected["architecture"] in ARCHITECTURES, "外部根預期格式或架構不符")
    media.token(expected["kernel_release"])
    compatible = expected["dt_compatible"]
    require(type(compatible) is list and 0 < len(compatible) <= 32
            and all(type(item) is str and re.fullmatch(r"[A-Za-z0-9,._+-]{1,128}", item) for item in compatible)
            and len(set(compatible)) == len(compatible), "DT compatible 清單無效")
    media.validate_expected(expected["target"])
    media.validate_sd(expected["protected_sd"])
    require(expected["target"]["topology"]["controller"] != expected["protected_sd"]["controller"], "根與固定 SD 控制器重疊")
    root = expected["root"]
    fields(root, "uuid partition_index start_lba sectors")
    require(type(root["uuid"]) is str and str(uuid.UUID(root["uuid"])) == root["uuid"], "根 UUID 未正規化")
    require(type(root["partition_index"]) is int and 1 <= root["partition_index"] <= 4
            and all(type(root[key]) is int and root[key] > 0 for key in ("start_lba", "sectors"))
            and (root["start_lba"] + root["sectors"]) * 512 <= expected["target"]["bytes"], "根分割範圍不符")
    label = expected["root_label"]
    require(label is None or type(label) is str and re.fullmatch(r"[A-Za-z0-9._+-]{1,16}", label), "根 LABEL 無效")
    require(expected["root_fs_type"] in ("ext2", "ext3", "ext4"), "首版只接受直接 ext 根")
    if "root_growth" in expected:
        growth = expected["root_growth"]
        fields(growth, "policy partitions")
        require(growth["policy"] == "last-primary-to-media-end", "未知根擴容策略")
        parts = growth["partitions"]
        require(type(parts) is list and 1 <= len(parts) <= 4, "原始 MBR 配置不完整")
        for part in parts:
            fields(part, "index start_lba sectors")
            require(all(type(part[key]) is int and part[key] > 0 for key in part)
                    and part["index"] <= 4 and (part["start_lba"] + part["sectors"]) * 512 <= expected["target"]["bytes"],
                    "原始主分割範圍無效")
        require(parts == sorted(parts, key=lambda row: row["index"])
                and len({row["index"] for row in parts}) == len(parts), "原始分割順序或索引重複")
        ordered = sorted(parts, key=lambda row: row["start_lba"])
        require(all(a["start_lba"] + a["sectors"] <= b["start_lba"] for a, b in zip(ordered, ordered[1:]))
                and ordered[-1] == {"index": root["partition_index"], **{key: root[key] for key in ("start_lba", "sectors")}},
                "只能擴大原始最後一個不重疊根分割")
    return copy.deepcopy(expected)


def expected_from_contract(contract, *, architecture, kernel_release, dt_compatible, root_label=None, root_fs_type="ext4", root_growth=None):
    if __package__:
        from . import bpi_lab_external as external
    else:
        import bpi_lab_external as external
    fixed = external.validate_contract(contract)
    return validate_expected({"schema": EXPECTED_SCHEMA, "architecture": architecture, "kernel_release": kernel_release,
                              "dt_compatible": dt_compatible, "root_label": root_label, "root_fs_type": root_fs_type,
                              **({"root_growth": root_growth} if root_growth is not None else {}),
                              **{key: fixed[key] for key in ("root", "target", "protected_sd")}})


def partition_geometry(parts):
    return sorted(({key: row[key] for key in ("index", "start_lba", "sectors")} for row in parts), key=lambda row: row["index"])


def validate_layout(target, expected):
    """只有明示策略允許末根向後增長；其他原始分割與索引集合必須不變。"""
    root = expected["root"]
    actual = partition_geometry(target["partitions"])
    selected = [row for row in actual if row["index"] == root["partition_index"]]
    require(len(selected) == 1 and selected[0]["start_lba"] == root["start_lba"], "根分割範圍的起點或索引改變")
    size = selected[0]["sectors"]
    if "root_growth" not in expected:
        require(size == root["sectors"], "未授權根分割擴容")
    else:
        require(root["sectors"] <= size <= expected["target"]["bytes"] // 512 - root["start_lba"], "根擴容縮小或越界")
        baseline = copy.deepcopy(actual)
        next(row for row in baseline if row["index"] == root["partition_index"])["sectors"] = root["sectors"]
        require(baseline == expected["root_growth"]["partitions"], "新增、遺失或改變其他原始分割")
    return size


def validate_media_transition(before, after, expected):
    """跨交接／階段允許核定根增長；同次 UART／SSH 採樣仍須完全相同。"""
    before, after = copy.deepcopy(before), copy.deepcopy(after)
    old, new = validate_layout(before["target"], expected), validate_layout(after["target"], expected)
    require(new >= old, "同次開機根分割不可縮小")
    if "root_growth" in expected:
        for value in (before, after):
            target = value["target"]
            part = next(row for row in target["partitions"] if row["index"] == expected["root"]["partition_index"])
            part["sectors"] = expected["root"]["sectors"]
            for row in value.get("inventory", []):
                if row["devnum"] == part["devnum"]:
                    row["bytes"] = row["ioctl_bytes"] = part["sectors"] * 512
    for key in ("target", "protected_sd", "sd_readback", "inventory"):
        require(before[key] == after[key], "交接或階段之間媒體身分不同")


def nonce_value(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "採樣 nonce 無效")


def public_key(value):
    require(type(value) is str and len(value) <= 4096, "缺少同次 SSH 公鑰")
    parts = value.split()
    require(len(parts) == 2 and parts[0] == "ssh-ed25519", "只接受無附註的 Ed25519 公鑰")
    raw = base64.b64decode(parts[1], validate=True)
    require(raw[:19] == b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" and len(raw) == 51, "SSH 公鑰編碼無效")
    return value


class NativeOps(media.NativeOps):
    """已掛載根的唯讀描述符；不沿用部署用途的 O_EXCL。"""

    def open_device(self, path, writable=False):
        require(not writable, "收集器不允許可寫描述符")
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)

    def probe(self, fd, deadline):
        result = subprocess.run([BLKID, "-p", "-c", "/dev/null", "-s", "UUID", "-s", "LABEL", "-s", "TYPE", "-s", "PTTYPE",
                                 "-o", "export", "/proc/self/fd/" + str(fd)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                pass_fds=(fd,), timeout=min(5, remaining(deadline)))
        require(result.returncode == 0 and 0 < len(result.stdout) <= 16384 and not result.stderr,
                "blkid 未成功辨識可見媒體；未知或 I/O 失敗不能視為空碟")
        values = {}
        for line in result.stdout.decode("utf-8").splitlines():
            key, separator, value = line.partition("=")
            require(separator and key not in values, "blkid 欄位缺少或重複")
            values[key] = value
        identified = values.get("TYPE") or values.get("PTTYPE")
        require(type(identified) is str and re.fullmatch(r"[A-Za-z0-9._+-]+", identified),
                "blkid 缺少已辨識的檔案系統或分割表；不能證明 UUID／LABEL 唯一")
        return {"uuid": values.get("UUID"), "label": values.get("LABEL"), "fs_type": identified}


def remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0, "外部根採樣逾時")
    return value


def read_text(ops, path, maximum=65536):
    return ops.read(path, maximum).decode("utf-8").strip()


def unescape(value):
    require(not re.search(r"\\(?![0-7]{3})", value), "掛載路徑跳脫無效")
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def mounts(ops, procroot):
    result = []
    for line in read_text(ops, Path(procroot) / "self/mountinfo", 1024**2).splitlines():
        head, separator, tail = line.partition(" - ")
        left, right = head.split(), tail.split()
        require(separator and len(left) >= 6 and len(right) == 3, "mountinfo 證據不完整")
        require(re.fullmatch(r"[0-9]+:[0-9]+", left[2]), "掛載裝置號無效")
        result.append({"id": int(left[0]), "parent_id": int(left[1]), "devnum": left[2],
                       "mount_root": unescape(left[3]), "mount_point": unescape(left[4]),
                       "options": left[5].split(","), "fs_type": right[0], "source": unescape(right[1])})
    require(result and len(result) <= 4096 and len({row["id"] for row in result}) == len(result), "掛載盤點缺少或重複")
    return result


def root_observation(rows, inventory, root_path, ops):
    found = [row for row in rows if row["mount_point"] == str(root_path)]
    require(len(found) == 1 and found[0]["mount_root"] == "/", "客戶根不是唯一直接掛載")
    mount = found[0]
    node = ops.stat(root_path)
    number = f"{os.major(node.st_dev)}:{os.minor(node.st_dev)}"
    require(number == mount["devnum"], "stat 根裝置號與 mountinfo 不符")
    blocks = [row for row in inventory if row["devnum"] == number]
    require(len(blocks) == 1 and blocks[0]["partition_index"] is not None, "根未對應唯一直接實體分割")
    return {"mount": mount, "stat": {"devnum": number, "inode": node.st_ino}, "block": blocks[0]}


def validate_root(value, target, inventory, expected):
    fields(value, "mount stat block")
    mount, node, block = (value[key] for key in ("mount", "stat", "block"))
    fields(mount, "id parent_id devnum mount_root mount_point options fs_type source")
    fields(node, "devnum inode")
    require(type(node["inode"]) is int and node["inode"] > 0, "stat 根 inode 證據缺少")
    require(block in inventory and mount["devnum"] == node["devnum"] == block["devnum"]
            and mount["mount_root"] == "/" and mount["fs_type"] == block["fs_type"] == expected["root_fs_type"]
            and block["parent"] == target["sysfs_path"], "根掛載、stat、檔案系統或父媒體不同")
    root = expected["root"]
    partitions = [item for item in target["partitions"] if item["index"] == root["partition_index"]]
    size = validate_layout(target, expected)
    require(len(partitions) == 1
            and partitions[0]["devnum"] == block["devnum"] and partitions[0]["sysfs_path"] == block["sysfs_path"]
            and block["partition_index"] == root["partition_index"] and block["bytes"] == size * 512,
            "客戶根不在核定 MBR 分割範圍")
    require([row for row in inventory if row["uuid"] == root["uuid"]] == [block], "根 UUID 未在所有可見媒體唯一出現")
    if expected["root_label"] is not None:
        require([row for row in inventory if row["label"] == expected["root_label"]] == [block], "根 LABEL 未唯一出現")


def swaps(ops, procroot):
    lines = read_text(ops, Path(procroot) / "swaps").splitlines()
    require(lines and lines[0].split() == ["Filename", "Type", "Size", "Used", "Priority"], "swap 表頭無效")
    result = []
    for line in lines[1:]:
        parts = line.split()
        require(len(parts) == 5, "swap 資料不完整")
        path = unescape(parts[0])
        node = ops.stat(path)
        number = node.st_rdev if stat.S_ISBLK(node.st_mode) else node.st_dev
        result.append({"path": path, "kind": parts[1], "devnum": f"{os.major(number)}:{os.minor(number)}", "bytes": int(parts[2]) * 1024})
    return result


def probe_inventory(*, sysroot, procroot, devroot, ops, deadline):
    before = media.inventory(sysroot=sysroot, procroot=procroot, devroot=devroot, ops=ops)
    result = []
    for row in before:
        remaining(deadline)
        fd = ops.open_device(row["device"])
        try:
            node = ops.fstat(fd)
            require(stat.S_ISBLK(node.st_mode) and f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}" == row["devnum"],
                    "盤點描述符不是同一區塊裝置")
            size = struct.unpack("=Q", ops.ioctl(fd, 0x80081272, bytes(8)))[0]
            require(size == row["bytes"], "盤點容量與描述符不同")
            identified = ops.probe(fd, deadline)
            fields(identified, "uuid label fs_type")
            read_only = struct.unpack("=i", ops.ioctl(fd, BLKROGET, bytes(4)))[0]
            require(read_only in (0, 1), "BLKROGET 回傳無效")
            sysfs_ro = int(read_text(ops, Path(sysroot) / "class/block" / row["name"] / "ro"))
            require(sysfs_ro == read_only, "sysfs 與描述符唯讀狀態不同")
            result.append({**row, **identified, "ioctl_bytes": size, "ioctl_read_only": read_only, "sysfs_read_only": sysfs_ro})
        finally:
            ops.close(fd)
    require(before == media.inventory(sysroot=sysroot, procroot=procroot, devroot=devroot, ops=ops), "全媒體盤點期間發生漂移")
    return result


def read_sd(sd, ops, deadline):
    fd = ops.open_device(sd["device"])
    try:
        media.check_fd(fd, sd, ops=ops)
        sha, offset = hashlib.sha256(), 0
        while offset < sd["bytes"]:
            remaining(deadline)
            block = ops.pread(fd, min(1024**2, sd["bytes"] - offset), offset)
            require(type(block) is bytes and 0 < len(block) <= sd["bytes"] - offset, "固定 SD 整碟讀取未完成")
            sha.update(block)
            offset += len(block)
        media.check_fd(fd, sd, ops=ops)
        return {"bytes": offset, "sha256": sha.hexdigest()}
    finally:
        ops.close(fd)


def observe(expected, nonce, *, sysroot="/sys", procroot="/proc", devroot="/dev", root_path="/",
            ops=None, timeout=21600, require_host_key=True):
    validate_expected(expected)
    nonce_value(nonce)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 86400, "採樣期限無效")
    ops = NativeOps() if ops is None else ops
    require(bool(ops), "拒絕假值 I/O 替身，避免底層退回真實裝置操作")
    deadline, started = time.monotonic() + timeout, time.monotonic_ns()
    require(ops.getuid() == 0, "完整外部媒體採樣需要 root")
    arguments = {"sysroot": sysroot, "procroot": procroot, "devroot": devroot, "ops": ops, "require_idle": False}
    with ops.watch() as watch:
        boot_id = read_text(ops, Path(procroot) / "sys/kernel/random/boot_id")
        target, sd = media.inspect(expected["target"], **arguments), media.inspect_sd(expected["protected_sd"], **arguments)
        fd = ops.open_device(target["device"])
        try:
            media.check_fd(fd, target, ops=ops)
            inventory = probe_inventory(sysroot=sysroot, procroot=procroot, devroot=devroot, ops=ops, deadline=deadline)
            mounted = mounts(ops, procroot)
            swap_rows = swaps(ops, procroot)
            root = root_observation(mounted, inventory, root_path, ops)
            sd_readback = read_sd(sd, ops, deadline)
            host_path = Path(root_path) / "etc/ssh/ssh_host_ed25519_key.pub"
            host_key = None
            if ops.exists(host_path):
                host_key = " ".join(read_text(ops, host_path).split()[:2])
            if require_host_key:
                public_key(host_key)
            machine = ops.uname().machine
            architecture = next((key for key, aliases in ARCHITECTURES.items() if machine in aliases), None)
            compatible = ops.read(Path(sysroot) / "firmware/devicetree/base/compatible")
            require(compatible.endswith(b"\0"), "DT compatible 缺少終止字元")
            inventory_after = probe_inventory(sysroot=sysroot, procroot=procroot, devroot=devroot, ops=ops, deadline=deadline)
            root_after = root_observation(mounts(ops, procroot), inventory_after, root_path, ops)
            target_after, sd_after = media.inspect(expected["target"], **arguments), media.inspect_sd(expected["protected_sd"], **arguments)
            media.check_fd(fd, target_after, ops=ops)
            require(read_text(ops, Path(procroot) / "sys/kernel/random/boot_id") == boot_id, "採樣期間核心重啟")
            watch.poll()
        finally:
            ops.close(fd)
    remaining(deadline)
    guard = parse_json(ops.read(Path(root_path) / "run/bpi-external-guard.json", MAX_OUTPUT)) if require_host_key else None
    value = {"schema": OBSERVATION_SCHEMA, "hardware_validated": False, "nonce": nonce, "boot_id": boot_id,
             "kernel": read_text(ops, Path(procroot) / "sys/kernel/osrelease"), "architecture": architecture,
             "machine": machine, "dt_compatible": compatible[:-1].decode().split("\0"), "host_key": host_key,
             "root": root, "root_after": root_after, "target": target, "target_after": target_after,
             "protected_sd": sd, "protected_sd_after": sd_after, "sd_readback": sd_readback,
             "inventory": inventory, "inventory_after": inventory_after, "mounts": mounted,
             "swaps": swap_rows, "swaps_after": swaps(ops, procroot),
             "guard": guard,
             "sampling_started_ns": started, "sampling_finished_ns": time.monotonic_ns()}
    _validate_observation(value, expected, nonce, require_host_key=require_host_key)
    return value


def _validate_device(value, expected):
    fields(value, " ".join(set(expected) | set("device name devnum sysfs_path device_path logical_block_size physical_block_size "
                                             "diskseq partitions mounted swap holders slaves".split())))
    require(all(value[key] == item for key, item in expected.items()), "完整媒體契約不符")
    require(all(type(value[key]) is bool for key in ("mounted", "swap", "holders", "slaves")), "媒體使用狀態型別不符")
    require(value["diskseq"] is None or type(value["diskseq"]) is int and value["diskseq"] >= 0, "diskseq 證據不完整")
    for key in ("sysfs_path", "device_path"):
        media.sys_path(value[key])
    require(type(value["device"]) is str and value["device"].startswith("/")
            and Path(value["device"]).name == value["name"] == Path(value["sysfs_path"]).name
            and type(value["devnum"]) is str and re.fullmatch(r"[0-9]+:[0-9]+", value["devnum"]), "媒體名稱或裝置號不符")
    require(type(value["logical_block_size"]) is int and value["logical_block_size"] == 512
            and type(value["physical_block_size"]) is int and 512 <= value["physical_block_size"] <= 65536
            and value["physical_block_size"] & (value["physical_block_size"] - 1) == 0, "媒體磁區證據無效")
    require(type(value["partitions"]) is list and len(value["partitions"]) <= 256, "分割盤點缺少或超界")
    for part in value["partitions"]:
        fields(part, "index devnum start_lba sectors sysfs_path")
        require(all(type(part[key]) is int and part[key] > 0 for key in ("index", "start_lba", "sectors"))
                and (part["start_lba"] + part["sectors"]) * 512 <= value["bytes"]
                and type(part["devnum"]) is str and re.fullmatch(r"[0-9]+:[0-9]+", part["devnum"])
                and str(Path(part["sysfs_path"]).parent) == value["sysfs_path"], "分割身分、父鏈或範圍無效")
    require(len({row["index"] for row in value["partitions"]}) == len(value["partitions"])
            and len({value["devnum"], *(row["devnum"] for row in value["partitions"])}) == len(value["partitions"]) + 1,
            "媒體分割編號或裝置號重複")


def _validate_observation(value, expected, nonce, *, require_host_key=True):
    validate_expected(expected)
    nonce_value(nonce)
    fields(value, "schema hardware_validated nonce boot_id kernel architecture machine dt_compatible host_key root root_after "
                  "target target_after protected_sd protected_sd_after sd_readback inventory inventory_after mounts "
                  "swaps swaps_after guard sampling_started_ns sampling_finished_ns")
    require(value["schema"] == OBSERVATION_SCHEMA and value["hardware_validated"] is False and value["nonce"] == nonce,
            "外部根觀測 schema 或 nonce 不符")
    require(type(value["boot_id"]) is str and str(uuid.UUID(value["boot_id"])) == value["boot_id"], "boot_id 無效")
    require(value["kernel"] == expected["kernel_release"] and value["architecture"] == expected["architecture"]
            and value["machine"] in ARCHITECTURES[expected["architecture"]]
            and value["dt_compatible"] == expected["dt_compatible"], "核心、架構或 DT 不符")
    require(all(type(value[key]) is int for key in ("sampling_started_ns", "sampling_finished_ns"))
            and 0 <= value["sampling_started_ns"] <= value["sampling_finished_ns"], "採樣時間不完整")
    if require_host_key:
        public_key(value["host_key"])
        require(value["root"]["mount"]["mount_point"] == "/", "客戶採樣必須核對實際 stat(/)，不能借用 initramfs 子掛載")
    for key in ("root", "target", "protected_sd", "inventory", "swaps"):
        require(value[key] == value[key + "_after"], "採樣前後身分或全媒體盤點不同")
    target, sd, inventory = value["target"], value["protected_sd"], value["inventory"]
    _validate_device(target, expected["target"])
    _validate_device(sd, expected["protected_sd"])
    require(target["sysfs_path"] != sd["sysfs_path"] and target["devnum"] != sd["devnum"], "目標與 SD 裝置重疊")
    require(type(inventory) is list and 0 < len(inventory) <= 256
            and len({row["devnum"] for row in inventory}) == len(inventory), "全媒體清單缺少或裝置號重複")
    for row in inventory:
        fields(row, "name device devnum sysfs_path parent bytes partition_index uuid label fs_type ioctl_bytes ioctl_read_only sysfs_read_only")
        require(type(row["bytes"]) is int and row["bytes"] > 0 and row["ioctl_bytes"] == row["bytes"]
                and type(row["ioctl_read_only"]) is int and row["ioctl_read_only"] in (0, 1)
                and row["sysfs_read_only"] == row["ioctl_read_only"], "盤點缺少容量或核心唯讀核對")
    validate_root(value["root"], target, inventory, expected)
    require(type(value["mounts"]) is list
            and [row for row in value["mounts"] if row["mount_point"] == value["root"]["mount"]["mount_point"]]
            == [value["root"]["mount"]], "原始 mountinfo 缺少唯一同一根掛載")
    sd_rows = [row for row in inventory if row["parent"] == sd["sysfs_path"]]
    require({row["devnum"] for row in sd_rows} == {sd["devnum"], *(part["devnum"] for part in sd["partitions"])}
            and all(row["ioctl_read_only"] == row["sysfs_read_only"] == 1 for row in sd_rows), "SD 整碟或分割未完整唯讀")
    require(not any(row["devnum"] in {item["devnum"] for item in sd_rows} for row in value["mounts"]), "固定 SD 仍被掛載")
    require(type(value["swaps"]) is list and not any(row["devnum"] in {item["devnum"] for item in sd_rows}
                                                  for row in value["swaps"]), "固定 SD 仍用作 swap")
    require(not sd["mounted"] and not sd["swap"] and not sd["holders"] and not sd["slaves"]
            and not target["holders"] and not target["slaves"], "固定 SD 或外部根仍有不允許的使用關係")
    require(value["sd_readback"] == {"bytes": expected["protected_sd"]["bytes"],
                                    "sha256": expected["protected_sd"]["full_sha256"]}, "固定 SD 整碟讀回摘要不同")
    if require_host_key:
        validate_guard_receipt(value["guard"], value, expected)
    else:
        require(value["guard"] is None, "guard 本身的採樣不得巢狀借用另一份保護收據")
    return value


def guard_source_digest():
    if GUARD_SOURCE_SHA256 is not None:
        return GUARD_SOURCE_SHA256
    return hashlib.sha256(Path(__file__).with_name("bpi_lab_external_guard.py").read_bytes()).hexdigest()


def validate_guard_receipt(receipt, observation, expected):
    fields(receipt, "schema status hardware_validated expected_sha256 observation fstab_sha256 fstab read_only_changes guard_source_sha256")
    require(receipt["schema"] == "bpi-lab-external-guard-result-v1" and receipt["status"] == "protected"
            and receipt["hardware_validated"] is False and receipt["expected_sha256"] == digest(expected)
            and receipt["guard_source_sha256"] == guard_source_digest(), "缺少本版固定 guard 的完整執行收據")
    prior = _validate_observation(receipt["observation"], expected, digest(expected), require_host_key=False)
    require(prior["root"]["mount"]["mount_point"] == "/root" and prior["boot_id"] == observation["boot_id"]
            and prior["sampling_finished_ns"] <= observation["sampling_started_ns"], "guard 不在本次 initramfs 交接前執行")
    validate_media_transition(prior, observation, expected)
    changes = receipt["read_only_changes"]
    sd = observation["protected_sd"]
    wanted = {row["devnum"]: row["bytes"] for row in observation["inventory"] if row["parent"] == sd["sysfs_path"]}
    require(type(receipt["fstab_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", receipt["fstab_sha256"])
            and type(receipt["fstab"]) is list, "guard 缺少客戶 fstab 原始摘要或解析結果")
    for row in receipt["fstab"]:
        fields(row, "source mountpoint fs_type devnum")
        require(all(type(item) is str and item for item in row.values()) and row["devnum"] not in wanted,
                "guard 的客戶 fstab／boot／swap 仍指向 SD 或證據不完整")
    require(type(changes) is list and len(changes) == len(wanted)
            and {row["devnum"]: row["bytes"] for row in changes} == wanted
            and all(type(row["before"]) is int and row["before"] in (0, 1)
                    and type(row["after"]) is int and row["after"] == 1 for row in changes), "guard 未逐個證實 SD 核心唯讀")


def validate_observation(observation, expected, nonce, *, uart_observation=None):
    value = _validate_observation(observation, expected, nonce)
    if uart_observation is not None:
        prior = _validate_observation(uart_observation, expected, nonce)
        for key in ("boot_id", "kernel", "architecture", "machine", "dt_compatible", "host_key", "root", "target",
                    "protected_sd", "sd_readback", "inventory", "guard"):
            require(prior[key] == value[key], "UART 與 SSH 未屬同次完整根／媒體身分")
        require(value["sampling_started_ns"] >= prior["sampling_finished_ns"], "SSH 採樣早於本次 UART 核對")
    return {"schema": VALIDATION_SCHEMA, "status": "passed", "ok": True, "hardware_validated": False,
            "expected_sha256": digest(expected), "observation_sha256": digest(value), "nonce": nonce,
            "boot_id": value["boot_id"], "uart_observation_sha256": digest(uart_observation) if uart_observation is not None else None}


def program(expected, nonce):
    validate_expected(expected)
    nonce_value(nonce)
    payload = {"media": Path(media.__file__).read_text(), "linux": Path(__file__).read_text(), "expected": expected,
               "nonce": nonce, "guard_source_sha256": guard_source_digest()}
    packed = base64.b64encode(zlib.compress(encoded(payload), 9)).decode("ascii")
    return ("import base64,json,sys,types,zlib\n"
            f"p=json.loads(zlib.decompress(base64.b64decode({packed!r})))\n"
            "m=types.ModuleType('bpi_lab_media');sys.modules[m.__name__]=m\n"
            "exec(compile(p['media'],'<bpi_lab_media>','exec'),m.__dict__)\n"
            "n={'__name__':'_bpi_external_linux','__package__':None}\n"
            "exec(compile(p['linux'],'<bpi_lab_external_linux>','exec'),n)\n"
            "n['GUARD_SOURCE_SHA256']=p['guard_source_sha256']\n"
            "try:\n"
            " v=n['observe'](p['expected'],p['nonce'])\n"
            " print(json.dumps(v,ensure_ascii=True,sort_keys=True))\n"
            "except Exception:\n"
            " print('{\"status\":\"blocked\",\"hardware_validated\":false}');sys.exit(2)\n")


def collect(expected, ssh, output, *, nonce, uart_observation, timeout=21600, transport=None):
    if __package__:
        from . import bpi_lab_deploy as deploy
    else:
        import bpi_lab_deploy as deploy
    validate_expected(expected)
    _validate_observation(uart_observation, expected, nonce)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 86400, "收集總期限無效")
    deploy.validate_ssh(ssh)
    host = ssh["host"] if ssh["port"] == 22 else f"[{ssh['host']}]:{ssh['port']}"
    require(deploy.checked_bytes(ssh["known_hosts"], 65536) == (host + " " + uart_observation["host_key"] + "\n").encode(),
            "SSH hostkey 不是本次 UART 公鑰")
    deadline = time.monotonic() + timeout
    output = deploy.new_directory(output)
    fixed = deploy.snapshot_ssh(ssh, output)
    payload = program(expected, nonce).encode()
    argv = deploy.backup.ssh_command(fixed["path"], "bpi-lab", {})
    argv[-1] = "/usr/bin/python3 -I -B -"
    buffers, code, sent = {"stdout": bytearray(), "stderr": bytearray()}, None, 0
    runner = deploy.core.upload_stream if transport is None else transport
    with closing(runner(argv, iter([payload]), deadline, time.monotonic)) as events:
        for kind, data in events:
            remaining(deadline)
            require(code is None, "SSH 退出後仍有事件")
            if kind == "sent":
                require(type(data) is int and data > 0 and sent + data <= len(payload), "SSH 程式傳送長度不符")
                sent += data
            elif kind == "exit":
                require(type(data) is int, "SSH 退出碼無效")
                code = data
            else:
                require(kind in buffers and type(data) is bytes
                        and sum(len(item) for item in buffers.values()) + len(data) <= MAX_OUTPUT, "SSH 證據超界")
                buffers[kind].extend(data)
    require(code == 0 and sent == len(payload), "固定採樣程式未完整執行；不保存原始診斷")
    observation = parse_json(bytes(buffers["stdout"]))
    validation = validate_observation(observation, expected, nonce, uart_observation=uart_observation)
    deploy.validate_ssh(ssh)
    deploy.checked_bytes(fixed)
    result = {"schema": COLLECTION_SCHEMA, "status": "collected", "hardware_validated": False,
              "nonce": nonce, "collector_sha256": hashlib.sha256(payload).hexdigest(), "ssh_config": fixed,
              "known_hosts": ssh["known_hosts"], "expected_sha256": digest(expected),
              "uart_observation_sha256": digest(uart_observation), "observation": observation, "validation": validation}
    remaining(deadline)
    deploy.save(output, "collection.json", encoded(result))
    remaining(deadline)
    return result
