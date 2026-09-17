#!/usr/bin/env python3
"""USB／NVMe 實體媒體識別；只有明示呼叫才讀取 sysfs 或開啟描述符。"""

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import uuid

MAX_BYTES = 16 * 1024**4


class MediaError(ValueError):
    """媒體識別、拓撲或使用狀態不符合固定契約。"""


def require(condition, message):
    if not condition:
        raise MediaError(message)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), "欄位缺少或含未知欄位")


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def token(value):
    require(type(value) is str and re.fullmatch(r"[!-~]{1,256}(?: [!-~]+)*", value)
            and len(value) <= 256, "識別字串必須是非空、有界且無首尾空白的 ASCII")
    return value


def sys_path(value):
    require(type(value) is str and re.fullmatch(r"/sys/devices/[A-Za-z0-9_./:+-]+", value)
            and all(p not in ("", ".", "..") for p in value.split("/")[1:]), "控制器 sysfs 路徑無效")


def validate_expected(expected):
    """純函式；回傳獨立副本，不以裝置名稱或容量產生穩定身分。"""
    fields(expected, "kind identity bytes logical_block_size physical_block_size topology")
    require(expected["kind"] in ("usb", "nvme"), "只接受 USB 或原生 PCIe NVMe")
    require(type(expected["bytes"]) is int and 0 < expected["bytes"] <= MAX_BYTES
            and expected["bytes"] % 512 == 0, "容量超界或不是完整磁區")
    require(type(expected["logical_block_size"]) is int and expected["logical_block_size"] == 512,
            "首版只接受 512 位元組邏輯磁區")
    physical = expected["physical_block_size"]
    require(type(physical) is int and 512 <= physical <= 65536 and physical & (physical - 1) == 0,
            "實體磁區大小無效")
    identity, topology = expected["identity"], expected["topology"]
    if expected["kind"] == "usb":
        fields(identity, "wwid serial vid pid lun")
        fields(topology, "controller port")
        require(all(type(identity[k]) is str and re.fullmatch(r"[0-9a-f]{4}", identity[k])
                    for k in ("vid", "pid")), "USB VID／PID 必須正規化")
        require(type(identity["lun"]) is int and 0 <= identity["lun"] < 2**64, "USB LUN 無效")
        require(type(topology["port"]) is str and re.fullmatch(r"[1-9][0-9]*(?:\.[1-9][0-9]*)*", topology["port"]),
                "USB 必須固定實體埠鏈")
        require(type(identity["wwid"]) is str and re.fullmatch(r"(?:naa|eui|t10)\.[!-~ ]{1,240}", identity["wwid"]),
                "USB 缺少磁碟 WWID；橋接器 serial 不能代替磁碟")
        kind, value = identity["wwid"].split(".", 1)
        if kind in ("naa", "eui"):
            require(re.fullmatch(r"[0-9a-f]+", value) and len(value) in ((16, 32) if kind == "naa" else (16, 24, 32))
                    and int(value, 16) != 0, "USB WWID 不是有效非零識別碼")
            require(kind != "naa" or (len(value) == 16 and value[0] in "235")
                    or (len(value) == 32 and value[0] == "6"), "NAA 格式種類與長度不符")
        else:
            require(len(value) > 8 and value.strip(" 0"), "T10 WWID 缺少廠商與媒體識別")
    else:
        fields(identity, "wwid serial model nsid")
        fields(topology, "controller")
        token(identity["model"])
        require(type(identity["nsid"]) is int and 1 <= identity["nsid"] < 2**32, "NVMe NSID 無效")
        require(type(identity["wwid"]) is str and re.fullmatch(r"(?:uuid|eui|nvme)\.[A-Za-z0-9.-]{1,240}", identity["wwid"]),
                "NVMe namespace WWID 無效")
        kind, value = identity["wwid"].split(".", 1)
        if kind == "uuid":
            parsed = uuid.UUID(value)
            require(parsed.int != 0 and str(parsed) == value, "NVMe namespace UUID 無效或為零")
        elif kind == "eui":
            require(re.fullmatch(r"[0-9a-f]+", value) and len(value) in (16, 32) and int(value, 16),
                    "NVMe EUI／NGUID 無效或為零")
        else:
            match = re.fullmatch(r"([0-9a-f]{4})-([0-9a-f]{2,40})-([0-9a-f]{2,80})-([0-9a-f]{8})", value)
            require(match is not None and bytes.fromhex(match[2]).decode("ascii") == identity["serial"]
                    and bytes.fromhex(match[3]).decode("ascii") == identity["model"]
                    and int(match[4], 16) == identity["nsid"], "NVMe 回退 WWID 與 serial／model／NSID 矛盾")
    token(identity["serial"])
    token(identity["wwid"])
    sys_path(topology["controller"])
    require(not topology["controller"].startswith("/sys/devices/virtual/"), "不接受虛擬媒體控制器")
    return copy.deepcopy(expected)


def media_identity(expected):
    validate_expected(expected)
    # 同一顆媒體換埠仍須取得同一把鎖；容量與拓撲另行嚴格配對。
    return "media:" + digest({"kind": expected["kind"], "wwid": expected["identity"]["wwid"]})


def validate_sd(expected):
    fields(expected, "cid bytes controller full_sha256")
    require(type(expected["cid"]) is str and re.fullmatch(r"[0-9a-f]{32}", expected["cid"]), "SD CID 無效")
    require(type(expected["bytes"]) is int and 0 < expected["bytes"] <= MAX_BYTES
            and expected["bytes"] % 512 == 0, "SD 容量無效")
    require(type(expected["full_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", expected["full_sha256"]),
            "SD 必須提供整碟 SHA-256")
    sys_path(expected["controller"])
    return copy.deepcopy(expected)


class DeviceWatch:
    """先訂閱核心事件，再盤點；溢位或相關事件一律使本次操作失效。"""

    def __init__(self):
        self.socket = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 15)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self.socket.bind((0, 1))
        self.socket.setblocking(False)

    def poll(self):
        for _ in range(1024):
            try:
                packet, _, flags, sender = self.socket.recvmsg(65536)
            except BlockingIOError:
                return
            require(sender[0] == 0 and not flags, "媒體事件不是完整核心事件")
            values = packet.split(b"\0")
            require(not any(row in (b"SUBSYSTEM=block", b"SUBSYSTEM=usb", b"SUBSYSTEM=pci", b"SUBSYSTEM=nvme")
                            for row in values), "媒體或連接拓撲在操作期間發生事件，必須重新配對")
        raise MediaError("媒體事件過多，無法證明盤點完整")

    def close(self):
        self.socket.close()


class NativeOps:
    """正式標準函式庫後端；測試可明示注入替身，不提供略過身分的旗標。"""

    stat = staticmethod(os.stat)
    fstat = staticmethod(os.fstat)
    close = staticmethod(os.close)
    pread = staticmethod(os.pread)
    pwrite = staticmethod(os.pwrite)
    fsync = staticmethod(os.fsync)
    ioctl = staticmethod(fcntl.ioctl)
    getuid = staticmethod(os.geteuid)
    uname = staticmethod(os.uname)

    def read(self, path, maximum=65536):
        with open(path, "rb") as stream:
            blob = stream.read(maximum + 1)
        require(len(blob) <= maximum, "系統證據超過讀取上限")
        return blob

    def read_regular(self, path, maximum=65536):
        path_fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(path_fd)
            require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum, "身分檔不是有界一般檔案")
            fd = os.open(f"/proc/self/fd/{path_fd}", os.O_RDONLY | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as stream:
                blob = stream.read(maximum + 1)
                after = os.fstat(stream.fileno())
            require(len(blob) == before.st_size and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "身分檔在讀取期間變更")
            return blob
        finally:
            os.close(path_fd)

    def listdir(self, path):
        rows = sorted(Path(path).iterdir())
        require(len(rows) <= 4096, "系統目錄項目超過上限")
        return rows

    def exists(self, path):
        return Path(path).exists()

    def resolve(self, path):
        return Path(path).resolve(strict=True)

    def open_device(self, path, writable=False):
        path = Path(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            path_fd = os.open(path.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            try:
                require(stat.S_ISBLK(os.fstat(path_fd).st_mode), "拒絕開啟非區塊媒體節點")
                return os.open(f"/proc/self/fd/{path_fd}", (os.O_RDWR if writable else os.O_RDONLY) |
                               os.O_EXCL | os.O_CLOEXEC)
            finally:
                os.close(path_fd)
        finally:
            os.close(directory)

    @contextmanager
    def watch(self):
        watch = DeviceWatch()
        try:
            yield watch
        finally:
            watch.close()


def _text(ops, path):
    return ops.read(path).decode("ascii").strip()


def _number(ops, path):
    value = _text(ops, path)
    require(re.fullmatch(r"[0-9]{1,20}", value), "sysfs 整數欄位無效")
    return int(value)


def _devnum(value):
    return f"{os.major(value)}:{os.minor(value)}"


def _canonical(path, sysroot):
    relative = Path(path).relative_to(Path(sysroot))
    require(relative.parts and relative.parts[0] == "devices", "sysfs 解析未落在 devices")
    return "/sys/" + relative.as_posix()


def _bus(ops, path):
    return ops.resolve(path / "subsystem").name if ops.exists(path / "subsystem") else None


def _parents(path, sysroot):
    result = []
    while path != sysroot:
        require(path != path.parent and len(result) < 64, "sysfs 父鏈越界")
        result.append(path)
        path = path.parent
    return result


def _external(base, kind, sysroot, ops):
    resolved = ops.resolve(base)
    canonical = _canonical(resolved, sysroot)
    require(not canonical.startswith("/sys/devices/virtual/"), "不接受虛擬或 multipath 媒體")
    device = ops.resolve(base / "device")
    parents = _parents(device, sysroot)
    if kind == "usb":
        interfaces = [p for p in parents if ops.exists(p / "bInterfaceClass")]
        if not interfaces:
            return None
        interface = interfaces[0]
        require(_bus(ops, interface) == "usb" and _text(ops, interface / "bInterfaceClass") == "08"
                and ops.resolve(interface / "driver").name in ("usb-storage", "uas"), "不是 USB storage／UAS")
        bridge = interface.parent
        require(_bus(ops, bridge) == "usb", "USB 橋接器父鏈不符")
        hubs = [p for p in parents if ops.exists(p / "devpath") and _text(ops, p / "devpath") == "0"]
        require(len(hubs) == 1, "USB 根控制器不唯一")
        lun = re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+:([0-9]+)", device.name)
        require(lun is not None and _text(ops, device / "type") == "0", "USB 不是直接存取 SCSI 磁碟")
        identity = {"wwid": _text(ops, device / "wwid"), "serial": _text(ops, bridge / "serial"),
                    "vid": _text(ops, bridge / "idVendor"), "pid": _text(ops, bridge / "idProduct"),
                    "lun": int(lun[1])}
        topology = {"controller": _canonical(hubs[0].parent, sysroot), "port": _text(ops, bridge / "devpath")}
    else:
        controllers = [p for p in parents if _bus(ops, p) == "nvme"]
        require(len(controllers) == 1, "NVMe 控制器父鏈不唯一")
        controller = controllers[0]
        pci = ops.resolve(controller / "device")
        require(_text(ops, controller / "transport") == "pcie" and _bus(ops, pci) == "pci",
                "首版只接受原生 PCIe NVMe，拒絕 fabrics")
        identity = {"wwid": _text(ops, base / "wwid"), "serial": _text(ops, controller / "serial"),
                    "model": _text(ops, controller / "model"), "nsid": _number(ops, base / "nsid")}
        topology = {"controller": _canonical(pci, sysroot)}
    return {"kind": kind, "identity": identity, "topology": topology}


def _usage(entries, procroot, ops):
    numbers = {_text(ops, entry / "dev") for entry in entries}
    holders = any(ops.listdir(entry / "holders") for entry in entries)
    slaves = any(ops.exists(entry / "slaves") and ops.listdir(entry / "slaves") for entry in entries)
    mounted = _devnum(ops.stat("/").st_dev) in numbers
    namespaces = {"self": Path(procroot) / "self/mountinfo"}
    for pid in ops.listdir(procroot):
        if pid.name.isdigit():
            info = ops.stat(pid / "ns/mnt")
            namespaces.setdefault(str(info.st_ino), pid / "mountinfo")
    for path in namespaces.values():
        for line in ops.read(path, 1024 * 1024).decode().splitlines():
            head, sep, tail = line.partition(" - ")
            columns = head.split()
            require(sep and len(columns) >= 6 and len(tail.split()) >= 3, "掛載證據格式不完整")
            mounted |= columns[2] in numbers
    lines = _text(ops, Path(procroot) / "swaps").splitlines()
    require(lines and lines[0].split() == ["Filename", "Type", "Size", "Used", "Priority"], "swap 表頭無效")
    swap = False
    for line in lines[1:]:
        row = line.split()
        require(len(row) == 5, "swap 證據格式不完整")
        path = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), row[0])
        info = ops.stat(path)
        swap |= _devnum(info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev) in numbers
    return {"mounted": bool(mounted), "swap": swap, "holders": holders, "slaves": slaves}


def _observe(base, fixed, *, sysroot, procroot, devroot, ops, require_idle):
    require(not ops.exists(base / "partition"), "只能選取整碟，不能選取分割區")
    resolved = ops.resolve(base)
    entries = [base]
    parts = []
    for row in ops.listdir(Path(sysroot) / "class/block"):
        if ops.exists(row / "partition") and ops.resolve(row).parent == resolved:
            entries.append(row)
            parts.append({"index": _number(ops, row / "partition"), "devnum": _text(ops, row / "dev"),
                          "start_lba": _number(ops, row / "start"), "sectors": _number(ops, row / "size"),
                          "sysfs_path": _canonical(ops.resolve(row), sysroot)})
    device = str(Path(devroot) / base.name)
    info = ops.stat(device, follow_symlinks=False)
    number = _text(ops, base / "dev")
    require(stat.S_ISBLK(info.st_mode) and number == _devnum(info.st_rdev), "裝置節點不是對應的原生區塊裝置")
    require(_canonical(ops.resolve(Path(sysroot) / "dev/block" / number), sysroot) == _canonical(resolved, sysroot),
            "反向 sysfs 裝置號映射不符")
    logical = _number(ops, base / "queue/logical_block_size")
    physical = _number(ops, base / "queue/physical_block_size")
    size = _number(ops, base / "size") * 512
    require(size == fixed["bytes"] and logical == fixed.get("logical_block_size", 512)
            and physical == fixed.get("physical_block_size", physical), "容量或磁區大小與配對不同")
    if ops.exists(base / "queue/zoned"):
        require(_text(ops, base / "queue/zoned") == "none", "不接受 zoned 媒體")
    usage = _usage(entries, procroot, ops)
    require(not require_idle or not any(usage.values()), "媒體／子分割仍掛載、swap、holders 或 slaves")
    require(not usage["slaves"], "不接受堆疊媒體")
    return {**copy.deepcopy(fixed), "device": device, "name": base.name, "devnum": number,
            "sysfs_path": _canonical(resolved, sysroot), "device_path": _canonical(ops.resolve(base / "device"), sysroot),
            "logical_block_size": logical, "physical_block_size": physical,
            "diskseq": _number(ops, base / "diskseq") if ops.exists(base / "diskseq") else None,
            "partitions": sorted(parts, key=lambda p: p["index"]), **usage}


def inspect(expected, *, sysroot="/sys", procroot="/proc", devroot="/dev", ops=None, require_idle=True):
    """核對完整配對並回傳觀測；require_idle=False 僅供 D3 已掛載根的唯讀採樣。"""
    fixed = validate_expected(expected)
    ops, sysroot = ops or NativeOps(), Path(sysroot)
    candidates = []
    for base in ops.listdir(sysroot / "class/block"):
        if ops.exists(base / "partition"):
            continue
        if not re.fullmatch(r"sd[a-z]+" if fixed["kind"] == "usb" else r"nvme[0-9]+n[1-9][0-9]*", base.name):
            continue
        observed = _external(base, fixed["kind"], sysroot, ops)
        if observed is not None and observed["identity"]["wwid"] == fixed["identity"]["wwid"]:
            candidates.append((base, observed))
    require(len(candidates) == 1, "磁碟 WWID 未唯一出現，禁止猜測目標")
    base, observed = candidates[0]
    require(observed["identity"] == fixed["identity"] and observed["topology"] == fixed["topology"],
            "磁碟或橋接器身分／固定拓撲不符")
    return _observe(base, fixed, sysroot=sysroot, procroot=procroot, devroot=devroot, ops=ops, require_idle=require_idle)


def inventory(*, sysroot="/sys", procroot="/proc", devroot="/dev", ops=None):
    """盤點所有非零區塊裝置，包含分割與虛擬裝置；不宣稱可寫或 UUID 唯一。"""
    ops, sysroot = ops or NativeOps(), Path(sysroot)
    rows = []
    for base in ops.listdir(sysroot / "class/block"):
        sectors = _number(ops, base / "size")
        if not sectors:
            continue
        resolved, number = ops.resolve(base), _text(ops, base / "dev")
        device = str(Path(devroot) / base.name)
        info = ops.stat(device, follow_symlinks=False)
        require(stat.S_ISBLK(info.st_mode) and _devnum(info.st_rdev) == number, "全媒體盤點裝置號不符")
        parent = resolved.parent if ops.exists(base / "partition") else resolved
        rows.append({"name": base.name, "device": device, "devnum": number,
                     "sysfs_path": _canonical(resolved, sysroot), "parent": _canonical(parent, sysroot),
                     "bytes": sectors * 512,
                     "partition_index": _number(ops, base / "partition") if parent != resolved else None})
    require(len(rows) <= 256, "全媒體盤點超過上限")
    return rows


def inspect_sd(expected, *, sysroot="/sys", procroot="/proc", devroot="/dev", ops=None, require_idle=True):
    fixed, ops, sysroot = validate_sd(expected), ops or NativeOps(), Path(sysroot)
    matches = []
    for base in ops.listdir(sysroot / "class/block"):
        if re.fullmatch(r"mmcblk[0-9]+", base.name) and _text(ops, base / "device/type") == "SD":
            if _text(ops, base / "device/cid") == fixed["cid"]:
                matches.append(base)
    require(len(matches) == 1, "受保護 SD CID 未唯一出現")
    base = matches[0]
    device = _canonical(ops.resolve(base / "device"), sysroot)
    require(device.split("/mmc_host/")[0] == fixed["controller"] and "/mmc_host/" in device,
            "受保護 SD 控制器不符")
    return _observe(base, fixed, sysroot=sysroot, procroot=procroot, devroot=devroot, ops=ops, require_idle=require_idle)


def check_fd(fd, observation, *, ops=None):
    """對已開啟的同一描述符核對裝置號、精確容量、磁區與可用的 diskseq。"""
    ops = ops or NativeOps()
    info = ops.fstat(fd)
    require(stat.S_ISBLK(info.st_mode) and _devnum(info.st_rdev) == observation["devnum"], "描述符裝置號或型別漂移")
    checks = ((0x80081272, "=Q", "bytes"), (0x1268, "=I", "logical_block_size"),
              (0x127B, "=I", "physical_block_size"))
    for command, fmt, key in checks:
        value = struct.unpack(fmt, ops.ioctl(fd, command, bytes(struct.calcsize(fmt))))[0]
        require(value == observation[key], "描述符 ioctl 核對不符：" + key)
    if observation["diskseq"] is not None:
        value = struct.unpack("=Q", ops.ioctl(fd, 0x80081280, bytes(8)))[0]
        require(value == observation["diskseq"], "描述符 diskseq 已變更")
