#!/usr/bin/env python3
"""固定 SSH 的外部媒體完整備份與有限部署；不選取開機、不切電、不自動還原。"""

from contextlib import ExitStack, closing, contextmanager
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import stat
import struct
import sys
import time
import uuid
import zlib

if __package__:
    from . import bpi_lab_media as media
else:
    import bpi_lab_media as media

SCHEMA = "bpi-lab-external-v1"
PREFIX = b"BPI_EXTERNAL_V1 "
CHUNK = 1024 * 1024
MAX_LOG = 2 * CHUNK
require, fields = media.require, media.fields
ExternalError = media.MediaError


def _hash(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "SHA-256 格式無效")


def _id(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value), "識別符無效")


def _path(value):
    require(type(value) is str and value.startswith("/") and len(value) <= 4096
            and all(p not in ("", ".", "..") for p in value.split("/")[1:]), "必須使用正規化絕對路徑")
    return Path(value)


def _reference(value):
    fields(value, "path sha256")
    _path(value["path"])
    _hash(value["sha256"])


def _digest_record(value, maximum=media.MAX_BYTES):
    fields(value, "bytes sha256")
    require(type(value["bytes"]) is int and 0 < value["bytes"] <= maximum, "摘要長度超界")
    _hash(value["sha256"])


def validate_source(source, capacity):
    fields(source, "path compressed raw")
    require(_path(source["path"]).suffix == ".xz", "來源必須是固定的一般 XZ 檔")
    _digest_record(source["compressed"])
    _digest_record(source["raw"], min(capacity, 2**32 * 512))
    require(source["raw"]["bytes"] % 512 == 0, "原始映像不是完整磁區")
    return copy.deepcopy(source)


def source_digest(source):
    """授權綁定壓縮與原始內容，不依賴可變的主機路徑。"""
    return media.digest({key: source[key] for key in ("compressed", "raw")})


def reusable(contract):
    return contract["authorization"].get("scope") == "reusable-test-area"


def zero_digest(size, *, check=None):
    """有界計算全零內容摘要，不配置整段記憶體或存取任何媒體。"""
    require(type(size) is int and 0 <= size <= media.MAX_BYTES, "全零範圍長度無效")
    checksum, remaining = hashlib.sha256(), size
    if check is None:
        end = time.monotonic() + 21600
        def check():
            require(time.monotonic() < end, "全零摘要核對逾時")
    block = bytes(CHUNK)
    while remaining:
        check()
        count = min(CHUNK, remaining)
        checksum.update(block[:count])
        remaining -= count
    check()
    return {"bytes": size, "sha256": checksum.hexdigest()}


def validate_contract(contract):
    """純結構驗證，供 D3／D4 共用；不讀檔、不連線、不授權實板。"""
    fields(contract, "schema hardware_id target protected_sd root rescue backup write_plan authorization ssh isolation_dir")
    require(contract["schema"] == SCHEMA, "外部媒體契約 schema 不符")
    _id(contract["hardware_id"])
    target = media.validate_expected(contract["target"])
    media.validate_sd(contract["protected_sd"])
    require(target["topology"]["controller"] != contract["protected_sd"]["controller"], "目標與 SD 控制器重疊")
    root = contract["root"]
    fields(root, "uuid partition_index start_lba sectors")
    require(type(root["uuid"]) is str and str(uuid.UUID(root["uuid"])) == root["uuid"], "根 UUID 必須正規化")
    require(type(root["partition_index"]) is int and 1 <= root["partition_index"] <= 4
            and all(type(root[k]) is int and root[k] > 0 for k in ("start_lba", "sectors"))
            and (root["start_lba"] + root["sectors"]) * 512 <= target["bytes"], "根分割範圍無效")
    rescue = contract["rescue"]
    fields(rescue, "schema kernel identity_sha256")
    _id(rescue["schema"])
    _id(rescue["kernel"])
    _hash(rescue["identity_sha256"])
    auth = contract["authorization"]
    fields(auth, "record hardware_id media_identity backup_read write backup_sha256 source_sha256 write_plan_sha256"
           + (" scope" if "scope" in auth else ""))
    require("scope" not in auth or auth["scope"] == "reusable-test-area", "未知的媒體重用授權範圍")
    _id(auth["record"])
    require(auth["hardware_id"] == contract["hardware_id"] and auth["media_identity"] == media.media_identity(target)
            and auth["backup_read"] is True and type(auth["write"]) is bool, "授權未綁定本板與實體媒體")
    if contract["backup"] is None:
        require(auth["backup_sha256"] is None and not auth["write"], "未完整備份不能授權寫入")
    else:
        _reference(contract["backup"])
        require(auth["backup_sha256"] == contract["backup"]["sha256"], "備份授權摘要不符")
    plan = contract["write_plan"]
    if plan is None:
        require(auth["source_sha256"] is None and auth["write_plan_sha256"] is None and not auth["write"]
                and not reusable(contract),
                "沒有寫入計畫不能授權來源或寫入")
    else:
        fields(plan, "schema source_sha256 ranges tail_policy")
        require(plan["schema"] == "bpi-lab-external-write-v1"
                and plan["tail_policy"] == ("zero" if reusable(contract) else "preserve-zero"),
                "尾端歸零必須有 reusable-test-area 授權；舊契約只保留已為零的尾端")
        _hash(plan["source_sha256"])
        require(type(plan["ranges"]) is list and len(plan["ranges"]) == (2 if reusable(contract) else 1),
                "寫入計畫範圍數不符")
        span = plan["ranges"][0]
        fields(span, "offset bytes sha256")
        require(type(span["offset"]) is int and span["offset"] == 0, "只允許從整碟 offset 0 寫入")
        _digest_record({k: span[k] for k in ("bytes", "sha256")}, target["bytes"])
        require(span["bytes"] % 512 == 0 and auth["source_sha256"] == plan["source_sha256"]
                and auth["write_plan_sha256"] == media.digest(plan), "寫入範圍或計畫授權不符")
        if reusable(contract):
            require(contract["backup"] is not None, "重用區必須保留完整初始備份")
            tail = plan["ranges"][1]
            fields(tail, "offset bytes sha256")
            require(type(tail["offset"]) is int and type(tail["bytes"]) is int
                    and tail["offset"] == span["bytes"] and tail["bytes"] == target["bytes"] - span["bytes"],
                    "歸零範圍必須精確接續來源直到核定媒體末端")
            _hash(tail["sha256"])
    ssh = contract["ssh"]
    fields(ssh, "host port user identity known_hosts")
    require(type(ssh["host"]) is str and str(ipaddress.ip_address(ssh["host"])) == ssh["host"], "SSH 須固定正規化 IP")
    require(type(ssh["port"]) is int and 1 <= ssh["port"] <= 65535 and ssh["user"] == "root", "SSH 須明示 root 與埠")
    _reference(ssh["identity"])
    _reference(ssh["known_hosts"])
    _path(contract["isolation_dir"])
    return copy.deepcopy(contract)


def _parse(blob):
    require(type(blob) is bytes and len(blob) <= MAX_LOG, "JSON 證據超過上限")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "JSON 欄位重複")
            result[key] = value
        return result
    def constant(_):
        raise ExternalError("JSON 不接受非有限數值")
    return json.loads(blob, object_pairs_hook=pairs, parse_constant=constant)


class ImageProbe:
    """串流保留 MBR 與指定 ext superblock；不掛載或執行映像內程式。"""

    def __init__(self, root, size):
        self.root, self.size, self.position = root, size, 0
        self.header = bytearray()
        self.superblock = bytearray()

    def feed(self, data):
        start, end = self.position, self.position + len(data)
        for offset, length, buffer in ((0, 512, self.header),
                                       (self.root["start_lba"] * 512 + 1024, 1024, self.superblock)):
            lo, hi = max(start, offset), min(end, offset + length)
            if lo < hi:
                buffer.extend(data[lo - start:hi - start])
        self.position = end

    def finish(self):
        require(self.position == self.size and len(self.header) == 512 and self.header[510:] == b"\x55\xaa",
                "映像缺少完整 MBR 或長度不符")
        require(self.header[440:444] != bytes(4), "MBR 磁碟識別碼不可為零")
        partitions = []
        for index in range(4):
            row = self.header[446 + index * 16:462 + index * 16]
            if row == bytes(16):
                continue
            start, count = struct.unpack_from("<II", row, 8)
            require(row[0] in (0, 128) and row[4] in (0x83, 0x0b, 0x0c, 0x0e, 0xea)
                    and start > 0 and count > 0 and (start + count) * 512 <= self.size,
                    "首版不接受 GPT、延伸分割、未知分割型別或越界 MBR")
            partitions.append({"index": index + 1, "start_lba": start, "sectors": count, "type": row[4]})
        ordered = sorted(partitions, key=lambda row: row["start_lba"])
        require(ordered and all(a["start_lba"] + a["sectors"] <= b["start_lba"] for a, b in zip(ordered, ordered[1:])),
                "MBR 分割重疊或為空")
        root = [p for p in partitions if p["index"] == self.root["partition_index"]]
        require(len(root) == 1 and root[0]["type"] == 0x83
                and all(root[0][k] == self.root[k] for k in ("start_lba", "sectors")), "MBR 根分割不是已核定範圍")
        sb = self.superblock
        require(len(sb) == 1024 and sb[56:58] == b"\x53\xef", "根不是可核對的 ext superblock")
        require(str(uuid.UUID(bytes=bytes(sb[104:120]))) == self.root["uuid"], "ext 根 UUID 與契約不同")
        block_shift = struct.unpack_from("<I", sb, 24)[0]
        incompat = struct.unpack_from("<I", sb, 96)[0]
        blocks = struct.unpack_from("<I", sb, 4)[0]
        if incompat & 0x80:
            blocks += struct.unpack_from("<I", sb, 336)[0] << 32
        require(block_shift <= 6 and 0 < blocks * (1024 << block_shift) <= self.root["sectors"] * 512,
                "ext 宣告容量超出根分割")
        return {"table": "dos", "sector_size": 512, "partitions": partitions,
                "root": self.root, "root_header_verified": True, "filesystem_health_verified": False}


def _codec():
    if "_remote_codec" in globals():
        return globals()["_remote_codec"]
    return _libraries()[1].remote_namespace()["process_xz"]


def _source_binding(contract, source):
    validate_source(source, contract["target"]["bytes"])
    plan = contract["write_plan"]
    require(plan is not None and plan["source_sha256"] == source_digest(source)
            and plan["ranges"][0] == {"offset": 0, **source["raw"]}, "來源不在固定寫入計畫內")


def _zero_plan(contract, check):
    if reusable(contract):
        tail = contract["write_plan"]["ranges"][1]
        require(zero_digest(tail["bytes"], check=check) == {k: tail[k] for k in ("bytes", "sha256")},
                "尾端計畫 SHA-256 不是指定長度的全零內容")


def _rescue(contract, ops, procroot):
    require(ops.getuid() == 0, "遠端必須是明確 RAM 救援的 root")
    blob = ops.read_regular("/etc/bpi-rescue.json")
    observed = _parse(blob)
    require(observed.get("schema") == contract["rescue"]["schema"]
            and observed.get("kernel") == ops.uname().release == contract["rescue"]["kernel"]
            and hashlib.sha256(blob).hexdigest() == contract["rescue"]["identity_sha256"], "救援身分或執行核心不同")
    roots = []
    for line in ops.read(Path(procroot) / "self/mountinfo", CHUNK).decode().splitlines():
        head, separator, tail = line.partition(" - ")
        columns, fs = head.split(), tail.split()
        require(separator and len(columns) >= 6 and len(fs) >= 3, "救援掛載表不完整")
        if columns[4] == "/":
            roots.append((columns, fs))
    number = media._devnum(ops.stat("/").st_dev)
    require(len(roots) == 1 and roots[0][0][2:4] == [number, "/"] and number.startswith("0:")
            and roots[0][1][0] in ("rootfs", "ramfs", "tmpfs"), "不是獨立 RAM 根；拒絕 overlay 或持久根")
    return {**contract["rescue"], "root_dev": number, "root_fs": roots[0][1][0], "root_ram": True}


def _hash_range(fd, size, check, ops, *, start=0, zero=False):
    checksum, position = hashlib.sha256(), 0
    while position < size:
        check()
        count = min(CHUNK, size - position)
        blob = ops.pread(fd, count, start + position)
        require(type(blob) is bytes and 0 < len(blob) <= count, "完整範圍回讀截斷或長度異常")
        require(not zero or not any(blob), "映像外尾端不是零；首版不清除既有尾端 metadata 或資料")
        checksum.update(blob)
        position += len(blob)
    check()
    return {"bytes": position, "sha256": checksum.hexdigest()}


@contextmanager
def _remote_lease(key):
    root = Path("/run/bpi-lab-external")
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(directory)
        require(info.st_uid == 0 and info.st_mode & 0o077 == 0, "遠端媒體鎖目錄權限不符")
        fd = os.open(key.split(":", 1)[1] + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, "遠端鎖不是獨立一般檔")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def execute(request, source, output, emit, *, ops=None, sysroot="/sys", procroot="/proc", devroot="/dev",
            monotonic=time.monotonic):
    """正式遠端核心；受控 ops 測試走相同身分、範圍及資料路徑。"""
    contract = validate_contract(request["contract"])
    action = request["operation"]
    require(action in ("backup", "preflight", "deploy"), "未知遠端操作")
    require(type(request["timeout"]) in (int, float) and 0 < request["timeout"] <= 86400, "遠端期限無效")
    end, ops = monotonic() + request["timeout"], ops or media.NativeOps()
    state = {"identity": None, "sd_identity": None, "rescue": None, "sd_before": None, "sd_after": None,
             "target_before": None, "tail_before": None, "tail_after": None, "readback": None,
             "source": None, "layout": None, "bytes_written": 0, "attempted_end": 0,
             "source_bytes_written": 0, "tail_bytes_written": 0, "tail_readback": None, "baseline_match": None,
             "write_started": False, "descriptors_closed": False, "io_drained": False,
             "status": "failed", "error": None}
    kwargs = {"sysroot": sysroot, "procroot": procroot, "devroot": devroot, "ops": ops, "require_idle": True}
    watch = None
    target_fd = sd_fd = None
    close_errors = []
    def close_device(which):
        nonlocal target_fd, sd_fd
        descriptor = target_fd if which == "target" else sd_fd
        if descriptor is not None:
            if which == "target" and state["write_started"] and not state["io_drained"]:
                try:
                    require(monotonic() < end, "沒有排空失敗寫入的剩餘期限")
                    media.check_fd(descriptor, state["identity"], ops=ops)
                    ops.fsync(descriptor)
                    ops.ioctl(descriptor, 0x1261)
                    require(monotonic() < end, "失敗寫入排空已逾時")
                    state["io_drained"] = True
                except BaseException:
                    state["io_drained"] = False
            try:
                ops.close(descriptor)
            except BaseException:
                close_errors.append(which)
            if which == "target":
                target_fd = None
            else:
                sd_fd = None
    def check():
        require(monotonic() < end, "外部媒體操作總期限已到")
        if watch is not None:
            watch.poll()
        for descriptor, observation in ((target_fd, state["identity"]), (sd_fd, state["sd_identity"])):
            if descriptor is not None:
                media.check_fd(descriptor, observation, ops=ops)
    try:
        if action != "backup":
            _source_binding(contract, request["source"])
            require(contract["backup"] is not None and request["backup_raw"]["bytes"] == contract["target"]["bytes"],
                    "缺少完整備份證據")
            _digest_record(request["backup_raw"])
            _zero_plan(contract, check)
        if action == "deploy":
            require(request.get("confirm_overwrite") is True and contract["authorization"]["write"] is True,
                    "未明示確認覆寫，禁止開啟寫入描述符")
        with ExitStack() as stack:
            watch = stack.enter_context(ops.watch())
            if hasattr(ops, "lease"):
                stack.enter_context(ops.lease(media.media_identity(contract["target"])))
            else:
                stack.enter_context(_remote_lease(media.media_identity(contract["target"])))
            state["rescue"] = _rescue(contract, ops, procroot)
            state["identity"] = media.inspect(contract["target"], **kwargs)
            state["sd_identity"] = media.inspect_sd(contract["protected_sd"], **kwargs)
            require(state["identity"]["devnum"] != state["sd_identity"]["devnum"], "目標不能是受保護 SD")
            check()
            sd_fd = ops.open_device(state["sd_identity"]["device"], writable=False)
            stack.callback(close_device, "sd")
            target_fd = ops.open_device(state["identity"]["device"], writable=action == "deploy")
            stack.callback(close_device, "target")
            check()
            require(media.inspect(contract["target"], **kwargs) == state["identity"]
                    and media.inspect_sd(contract["protected_sd"], **kwargs) == state["sd_identity"], "開啟描述符期間身分漂移")
            state["sd_before"] = _hash_range(sd_fd, contract["protected_sd"]["bytes"], check, ops)
            require(state["sd_before"]["sha256"] == contract["protected_sd"]["full_sha256"], "SD 整碟摘要不是固定內容")
            emit("ready", state=copy.deepcopy(state))
            if action == "backup":
                checksum, count = hashlib.sha256(), 0
                encoder = zlib.compressobj(1, zlib.DEFLATED, 31)
                while count < contract["target"]["bytes"]:
                    check()
                    limit = min(CHUNK, contract["target"]["bytes"] - count)
                    blob = ops.pread(target_fd, limit, count)
                    require(type(blob) is bytes and 0 < len(blob) <= limit, "整碟備份讀取截斷")
                    checksum.update(blob)
                    count += len(blob)
                    output.write(encoder.compress(blob))
                output.write(encoder.flush())
                output.flush()
                state["target_before"] = {"bytes": count, "sha256": checksum.hexdigest()}
            else:
                size, capacity = request["source"]["raw"]["bytes"], contract["target"]["bytes"]
                state["range"] = {"offset": 0, "bytes": size}
                state["ranges"] = copy.deepcopy(contract["write_plan"]["ranges"])
                state["target_before"] = _hash_range(target_fd, capacity, check, ops)
                state["baseline_match"] = state["target_before"] == request["backup_raw"]
                require(reusable(contract) or state["baseline_match"], "目標現況與完整備份不同，須重新備份或明示重用授權")
                state["tail_before"] = _hash_range(target_fd, capacity - size, check, ops, start=size, zero=not reusable(contract))
                if action == "deploy":
                    probe = ImageProbe(contract["root"], size)
                    def write_segment(blob, limit, counter):
                        require(state["bytes_written"] + len(blob) <= limit, "寫入即將超過授權範圍")
                        offset = 0
                        while offset < len(blob):
                            check()
                            state["attempted_end"] = state["bytes_written"] + len(blob) - offset
                            state["write_started"] = True
                            count = ops.pwrite(target_fd, blob[offset:], state["bytes_written"])
                            require(type(count) is int and 0 < count <= len(blob) - offset, "寫入未前進或長度異常")
                            state["bytes_written"] += count
                            state[counter] += count
                            offset += count
                    def write(blob):
                        probe.feed(blob)
                        write_segment(blob, size, "source_bytes_written")
                    state["source"] = _codec()(source, {k: request["source"][k] for k in ("compressed", "raw")},
                                               capacity, check, write)
                    state["layout"] = probe.finish()
                    if reusable(contract):
                        zero = bytes(CHUNK)
                        while state["bytes_written"] < capacity:
                            write_segment(zero[:min(CHUNK, capacity - state["bytes_written"])], capacity, "tail_bytes_written")
                    check()
                    ops.fsync(target_fd)
                    ops.ioctl(target_fd, 0x1261)
                    check()
                    state["io_drained"] = True
                    state["readback"] = _hash_range(target_fd, size, check, ops)
                    require(state["readback"] == request["source"]["raw"], "完整寫入範圍回讀不符")
                zero_required = action == "deploy" or not reusable(contract)
                state["tail_after"] = _hash_range(target_fd, capacity - size, check, ops, start=size, zero=zero_required)
                if reusable(contract) and action == "deploy":
                    state["tail_readback"] = state["tail_after"]
                    require(state["tail_readback"] == {k: state["ranges"][1][k] for k in ("bytes", "sha256")},
                            "尾端完整回讀與全零計畫不符")
                else:
                    require(state["tail_after"] == state["tail_before"], "未授權寫入的尾端變更")
            state["sd_after"] = _hash_range(sd_fd, contract["protected_sd"]["bytes"], check, ops)
            require(state["sd_after"] == state["sd_before"] and _rescue(contract, ops, procroot) == state["rescue"]
                    and media.inspect(contract["target"], **kwargs) == state["identity"]
                    and media.inspect_sd(contract["protected_sd"], **kwargs) == state["sd_identity"], "後置媒體／SD／救援身分不同")
            check()
            state["status"] = "verified"
    except BaseException as exc:
        state["error"] = str(exc) if isinstance(exc, (ValueError, OSError)) else "遠端操作被中斷"
    finally:
        close_device("target")
        close_device("sd")
        state["descriptors_closed"] = not close_errors
        if not state["write_started"]:
            state["io_drained"] = True
        if close_errors or not state["io_drained"]:
            state.update(status="failed", error="無法確認全部媒體描述符已關閉且寫入已排空")
    emit("complete", state=state)
    return state


def _libraries():
    if __package__:
        from . import bpi_h618_emmc_backup, bpi_h618_emmc_deploy
    else:
        import bpi_h618_emmc_backup
        import bpi_h618_emmc_deploy
    return bpi_h618_emmc_backup, bpi_h618_emmc_deploy


def _safe():
    return _libraries()[0].safe


def _read(reference, maximum=MAX_LOG):
    _reference(reference)
    path = _path(reference["path"])
    with _safe().open_root(path.parent) as root:
        record, blob = _safe().fingerprint(root, path.name, limit=maximum, keep=True)
    require(record["sha256"] == reference["sha256"], "固定檔案摘要不符：" + path.name)
    return blob


def _save(directory, name, value):
    blob = value if type(value) is bytes else media.encoded(value)
    with _safe().open_root(directory) as root, _safe().open_file(root, name, create=True) as stream:
        stream.write(blob)
    return {"path": str(Path(directory) / name), "sha256": hashlib.sha256(blob).hexdigest()}


def _new_directory(path):
    path = _path(str(path))
    with _safe().open_root(path.parent) as parent:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    return path


def _same_file(root, name, stream, original):
    _libraries()[1].unchanged(root, name, stream, original)


def _verify_backup(contract, deadline, monotonic, lock_root):
    require(contract["backup"] is not None, "尚未提供已完成的整碟備份")
    report = _parse(_read(contract["backup"]))
    _validate_result_data(report, report["contract"], check=lambda: require(monotonic() < deadline, "備份核對逾時"))
    require(report["contract"]["isolation_dir"] == contract["isolation_dir"], "初始備份隔離目錄不可重綁")
    _validate_publication(report, lock_root)
    require(contract["backup"]["path"] == report["publication"]["directory"] + "/manifest.json", "備份不可使用重綁副本")
    require(report["operation"] == "backup" and report["status"] == "verified"
            and all(report["contract"][key] == contract[key] for key in ("hardware_id", "target", "protected_sd", "rescue")),
            "備份不是同板、同媒體、同 SD 及救援的完整成功證據")
    artifact = report["artifact"]
    fields(artifact, "path raw compressed")
    require(artifact["path"] == "disk.img.gz", "備份必須是完整整碟 gzip")
    directory = _path(contract["backup"]["path"]).parent
    with _safe().open_root(directory) as root:
        checked = _libraries()[0].verify_gzip(root, artifact["path"], contract["target"]["bytes"], deadline, monotonic)
    require(all(checked[k] == artifact[k] for k in ("raw", "compressed")), "備份落盤重讀與清單摘要不同")
    return {"reference": contract["backup"], "raw": checked["raw"], "compressed": checked["compressed"],
            "file_identity": checked["file_identity"]}


def _verify_source(contract, source, check):
    _source_binding(contract, source)
    path = _path(source["path"])
    probe = ImageProbe(contract["root"], source["raw"]["bytes"])
    with _safe().open_root(path.parent) as root, _safe().open_file(root, path.name) as stream:
        original = _libraries()[0].file_identity(os.fstat(stream.fileno()))
        _codec()(stream, {key: source[key] for key in ("compressed", "raw")}, contract["target"]["bytes"], check, probe.feed)
        _same_file(root, path.name, stream, original)
    _zero_plan(contract, check)
    return {"layout": probe.finish(), "file_identity": original}


def _revalidate_backup(proof, lock_root):
    report = _parse(_read(proof["reference"]))
    _validate_publication(report, lock_root)
    directory = _path(proof["reference"]["path"]).parent
    with _safe().open_root(directory) as root, _safe().open_file(root, "disk.img.gz") as stream:
        _same_file(root, "disk.img.gz", stream, proof["file_identity"])


def _ssh(contract, directory, program, request):
    ssh = contract["ssh"]
    require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", str(directory)), "SSH 固定副本路徑含展開字元")
    for key in ("identity", "known_hosts"):
        _save(directory, key, _read(ssh[key], 65536))
    config = (f"Host bpi-external\n HostName {ssh['host']}\n Port {ssh['port']}\n User root\n"
              f" IdentityFile {directory}/identity\n UserKnownHostsFile {directory}/known_hosts\n"
              " GlobalKnownHostsFile /dev/null\n KnownHostsCommand none\n VerifyHostKeyDNS no\n"
              " ProxyCommand none\n ProxyJump none\n StrictHostKeyChecking yes\n")
    reference = _save(directory, "ssh-config", config.encode())
    with _safe().open_root(directory) as root:
        for name in ("identity", "known_hosts", "ssh-config"):
            with _safe().open_file(root, name) as stream:
                os.fchmod(stream.fileno(), 0o400)
    import base64
    packed = base64.b64encode(zlib.compress(program.encode(), 9)).decode()
    bootstrap = "import base64,zlib;exec(zlib.decompress(base64.b64decode(" + repr(packed) + ")))"
    command = shlex.join(["python3", "-I", "-B", "-c", bootstrap, media.encoded(request).decode()])
    argv = _libraries()[0].ssh_command(reference["path"], "bpi-external", {})
    argv[-1] = command
    require(len(command.encode()) < 120000, "固定遠端程式超過命令長度上限")
    return argv, reference


def _program():
    import ast
    media_path, own_path = Path(media.__file__).absolute(), Path(__file__).absolute()
    sources = []
    for path in (media_path, own_path):
        with _safe().open_root(path.parent) as root:
            record, blob = _safe().fingerprint(root, path.name, limit=MAX_LOG, keep=True)
        sources.append((blob.decode(), {"path": str(path), **record}))
    old = _libraries()[1].REMOTE_CORE
    function = next(node for node in ast.parse(old).body if isinstance(node, ast.FunctionDef) and node.name == "process_xz")
    codec = "import lzma\n" + ast.get_source_segment(old, function)
    program = ("import sys,types\n"
               "m=types.ModuleType('bpi_lab_media')\n"
               "sys.modules[m.__name__]=m\n"
               "exec(" + repr(sources[0][0]) + ",m.__dict__)\n"
               "scope={'__name__':'_bpi_external_remote','__package__':None}\n"
               "exec(" + repr(sources[1][0]) + ",scope)\n"
               "exec(" + repr(codec) + ",scope)\n"
               "scope['_remote_codec']=scope['process_xz']\n"
               "scope['_remote_main']()\n")
    return program, [entry[1] for entry in sources] + [{"role": "xz_codec", "sha256": hashlib.sha256(codec.encode()).hexdigest()}]


def _remote_main():
    request = _parse(sys.argv[1].encode())
    _hash(request["nonce"])
    require(type(request["timeout"]) in (int, float) and 0 < request["timeout"] <= 86400, "遠端期限無效")
    def emit(event, **values):
        record = {"schema": "bpi-lab-external-wire-v1", "nonce": request["nonce"], "event": event, **values}
        sys.stderr.buffer.write(PREFIX + media.encoded(record))
        sys.stderr.buffer.flush()
    def interrupted(*_):
        raise ExternalError("遠端媒體操作逾時或收到中斷")
    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    signal.setitimer(signal.ITIMER_REAL, request["timeout"])
    try:
        state = execute(request, sys.stdin.buffer, sys.stdout.buffer, emit)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.exit(0 if state["status"] == "verified" else 1)


@contextmanager
def _isolation(contract, *, create=True):
    directory = _path(contract["isolation_dir"])
    if create:
        try:
            _new_directory(directory)
        except FileExistsError:
            pass
    name = media.media_identity(contract["target"]).split(":", 1)[1]
    with _safe().open_root(directory) as root:
        info = os.fstat(root)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, "隔離目錄擁有者或權限不符")
        try:
            if not create:
                raise FileExistsError
            fd = os.open(name + ".lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
        except FileExistsError:
            path_fd = os.open(name + ".lock", os.O_PATH | os.O_NOFOLLOW, dir_fd=root)
            try:
                require(stat.S_ISREG(os.fstat(path_fd).st_mode), "隔離鎖不是一般檔案")
                fd = os.open(f"/proc/self/fd/{path_fd}", os.O_RDWR | os.O_CLOEXEC)
            finally:
                os.close(path_fd)
        try:
            require(os.fstat(fd).st_nlink == 1 and os.fstat(fd).st_uid == os.geteuid(), "隔離鎖擁有者或連結數不符")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pending = name + ".pending.json"
            require(not os.path.lexists(directory / pending), "前次 writer 未證明停止；媒體仍隔離，禁止自動解除")
            yield root, pending
        finally:
            os.close(fd)


def _terminal(log, request):
    records = []
    for line in log.splitlines():
        if not line.startswith(PREFIX):
            continue
        row = _parse(line[len(PREFIX):])
        require(row.get("schema") == "bpi-lab-external-wire-v1" and row.get("nonce") == request["nonce"], "遠端紀錄 nonce 不符")
        records.append(row)
    require([row.get("event") for row in records] in (["complete"], ["ready", "complete"]), "遠端事件缺少、重複或順序不符")
    return records[-1]["state"]


def _validate_remote(state, contract, operation, source, *, success, check=None):
    require(type(state) is dict and type(state.get("descriptors_closed")) is bool
            and type(state.get("io_drained")) is bool, "遠端終止證據不完整")
    size = source["raw"]["bytes"] if source is not None else 0
    capacity = contract["target"]["bytes"]
    limit = capacity if size and reusable(contract) else size
    require(all(type(state.get(k)) is int for k in
                ("bytes_written", "attempted_end", "source_bytes_written", "tail_bytes_written"))
            and 0 <= state["bytes_written"] <= state["attempted_end"] <= limit
            and 0 <= state["source_bytes_written"] <= size
            and 0 <= state["tail_bytes_written"] <= limit - size
            and state["bytes_written"] == state["source_bytes_written"] + state["tail_bytes_written"]
            and (state["tail_bytes_written"] == 0 or state["source_bytes_written"] == size)
            and type(state.get("write_started")) is bool, "寫入計數缺少或越界")
    if not success:
        require(state.get("status") == "failed", "遠端失敗狀態不符")
        return
    require(state["status"] == "verified" and state["descriptors_closed"] is True and state["io_drained"] is True,
            "遠端沒有成功關閉描述符及排空寫入")
    identity, sd, rescue = state.get("identity"), state.get("sd_identity"), state.get("rescue")
    require(type(identity) is dict and type(sd) is dict and type(rescue) is dict, "遠端身分證據缺少")
    for expected, observed in ((contract["target"], identity), (contract["protected_sd"], sd)):
        require(all(observed.get(k) == v for k, v in expected.items())
                and all(observed.get(k) is False for k in ("mounted", "swap", "holders", "slaves"))
                and type(observed.get("devnum")) is str and re.fullmatch(r"[0-9]+:[0-9]+", observed["devnum"]),
                "遠端媒體配對或閒置證據不符")
    require(identity["devnum"] != sd["devnum"] and all(rescue.get(k) == v for k, v in contract["rescue"].items())
            and rescue.get("root_ram") is True and rescue.get("root_fs") in ("rootfs", "ramfs", "tmpfs"), "SD／RAM 根證據不符")
    require(state.get("sd_before") == state.get("sd_after") == {
        "bytes": contract["protected_sd"]["bytes"], "sha256": contract["protected_sd"]["full_sha256"]}, "SD 整碟前後摘要不符")
    _digest_record(state.get("target_before"))
    require(state["target_before"]["bytes"] == contract["target"]["bytes"], "沒有完整目標盤點")
    if operation == "backup":
        require(state["bytes_written"] == state["attempted_end"] == 0 and state["write_started"] is False, "備份不應寫入")
        return
    require(state.get("range") == {"offset": 0, "bytes": size}
            and state.get("ranges") == contract["write_plan"]["ranges"]
            and type(state.get("baseline_match")) is bool, "範圍或初始備份核對證據缺少")
    for key in ("tail_before", "tail_after"):
        fields(state.get(key), "bytes sha256")
        require(type(state[key]["bytes"]) is int and state[key]["bytes"] == capacity - size, "尾端回讀長度不符")
        _hash(state[key]["sha256"])
    if operation != "deploy" or not reusable(contract):
        require(state["tail_before"] == state["tail_after"] and state.get("tail_readback") is None,
                "未授權寫入的尾端前後不同")
    if not reusable(contract):
        require(state["tail_before"] == zero_digest(capacity - size, check=check), "舊契約尾端不是完整全零內容")
    if operation == "deploy":
        require(state["bytes_written"] == state["attempted_end"] == limit and state["write_started"] is True
                and state["source_bytes_written"] == size and state["tail_bytes_written"] == limit - size
                and state.get("source") == {k: source[k] for k in ("raw", "compressed")}
                and state.get("readback") == source["raw"] and state.get("layout", {}).get("root") == contract["root"]
                and state["layout"].get("root_header_verified") is True, "部署缺少完整來源、根解析與全範圍回讀")
        if reusable(contract):
            tail = contract["write_plan"]["ranges"][1]
            require(state.get("tail_readback") == state["tail_after"] == {k: tail[k] for k in ("bytes", "sha256")},
                    "缺少完整尾端歸零回讀")
    else:
        require(state["bytes_written"] == state["attempted_end"] == 0 and not state["write_started"], "唯讀前檢不應寫入")


def _validate_result_data(report, contract, source=None, *, check=None):
    """僅供發布前資料核對；不是正式收據驗證入口。"""
    validate_contract(contract)
    require(type(report) is dict and report.get("schema") == "bpi-lab-external-result-v1" and report.get("contract") == contract
            and report.get("contract_sha256") == media.digest(contract)
            and report.get("media_identity") == media.media_identity(contract["target"])
            and report.get("root_uuid") == contract["root"]["uuid"] and report.get("hardware_validated") is False,
            "結果未綁定完整契約")
    operation = report.get("operation")
    require(operation in ("backup", "preflight", "deploy"), "結果操作無效")
    require(report.get("status") == "verified" and report.get("blockers") == []
            and report.get("writer_stopped") is True and report.get("quarantined") is False, "不是可採信的完整成功結果")
    if operation != "backup":
        require(source is not None and report.get("source") == source, "結果缺少固定來源")
        _source_binding(contract, source)
        _zero_plan(contract, check)
        require(type(report.get("backup")) is dict and type(report.get("remote")) is dict
                and report["backup"].get("reference") == contract["backup"], "缺少固定完整初始備份")
        _digest_record(report["backup"].get("raw"), contract["target"]["bytes"])
        matched = report["backup"]["raw"] == report["remote"].get("target_before")
        require(report["backup"]["raw"]["bytes"] == contract["target"]["bytes"]
                and report["remote"].get("baseline_match") is matched
                and (reusable(contract) or matched), "初始備份比較不符，或未授權重用已變更媒體")
    _validate_remote(report["remote"], contract, operation, source, success=True, check=check)
    require(report.get("full_readback_verified") is (operation == "deploy"), "回讀狀態與操作不符")
    if operation == "backup":
        require(type(report.get("artifact")) is dict
                and report["artifact"].get("raw") == report["remote"]["target_before"], "備份產物與遠端完整摘要不同")
        _digest_record(report["artifact"]["compressed"], media.MAX_BYTES + media.MAX_BYTES // 100 + CHUNK)
    return copy.deepcopy(report)


def _directory_identity(fd):
    info = os.fstat(fd)
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, "發布目錄擁有者或權限不符")
    return {"st_dev": info.st_dev, "st_ino": info.st_ino}


def _snapshot(root, name):
    with _safe().open_file(root, name) as stream:
        before = os.fstat(stream.fileno())
        require(before.st_uid == os.geteuid() and before.st_nlink == 1 and before.st_mode & 0o077 == 0
                and 0 < before.st_size <= MAX_LOG, "發布證據檔案身分或權限不符")
        identity = _libraries()[0].file_identity(before)
        blob = stream.read(MAX_LOG + 1)
        _same_file(root, name, stream, identity)
        require(len(blob) == before.st_size, "發布證據檔案截斷")
        return blob, identity


def _completion_name(report):
    return report["media_identity"].split(":", 1)[1] + "." + report["publication"]["nonce"] + ".complete.json"


def _publication_record(report, lock_root):
    publication = report.get("publication")
    fields(publication, "schema nonce directory")
    require(publication["schema"] == "bpi-lab-external-publication-v1", "缺少正式發布身分")
    _hash(publication["nonce"])
    directory = _path(publication["directory"])
    with _safe().open_root(directory) as root:
        directory_identity = _directory_identity(root)
        blob, identity = _snapshot(root, "manifest.json")
    require(blob == media.encoded(report), "原始發布 manifest 與待驗收據不同")
    return {"schema": "bpi-lab-external-completion-v1", "nonce": publication["nonce"],
            "manifest": {"path": str(directory / "manifest.json"), "sha256": hashlib.sha256(blob).hexdigest()},
            "manifest_identity": identity, "directory_identity": directory_identity,
            "isolation_identity": _directory_identity(lock_root)}


def _validate_publication(report, lock_root):
    expected = _publication_record(report, lock_root)
    blob, _ = _snapshot(lock_root, _completion_name(report))
    require(_parse(blob) == expected, "發布完成憑證不符；不可重綁摘要、檔案或目錄副本")
    publication = report["publication"]
    for key, name in (("request_evidence", "request.json"), ("stderr_evidence", "stderr.log"),
                      ("stdout_evidence", "stdout.log")):
        require(report[key]["path"] == publication["directory"] + "/" + name, "發布證據不能搬移重綁")
    request = _parse(_read(report["request_evidence"]))
    require(request["nonce"] == publication["nonce"] and request["contract"] == report["contract"]
            and request["operation"] == report["operation"] and request["source"] == report["source"],
            "原始遠端請求與發布契約不同")
    require(_terminal(_read(report["stderr_evidence"]), request) == report["remote"], "原始遠端終止證據與摘要不同")
    if report["operation"] != "backup":
        require(request["backup_raw"] == report["backup"]["raw"], "原始請求與完整初始備份不同")


def validate_result(report, contract, source=None):
    """正式重播須讀取原始發布檔、完成憑證及即時隔離狀態；不連線或存取設備。"""
    _validate_result_data(report, contract, source)
    with _isolation(contract, create=False) as (root, _):
        _validate_publication(report, root)
        if report["operation"] != "backup":
            backup_report = _parse(_read(contract["backup"]))
            _validate_result_data(backup_report, backup_report["contract"])
            require(backup_report["operation"] == "backup"
                    and all(backup_report["contract"][key] == contract[key]
                            for key in ("hardware_id", "target", "protected_sd", "rescue", "isolation_dir"))
                    and contract["backup"]["path"] == backup_report["publication"]["directory"] + "/manifest.json"
                    and backup_report["artifact"]["raw"] == report["backup"]["raw"], "初始備份來源重綁或不符")
            _validate_publication(backup_report, root)
            directory = _path(contract["backup"]["path"]).parent
            with _safe().open_root(directory) as backup_root, _safe().open_file(backup_root, "disk.img.gz") as stream:
                _same_file(backup_root, "disk.img.gz", stream, report["backup"]["file_identity"])
        # 所有完成證據先持久化，最後解除 pending；重播亦要求解除狀態已同步。
        os.fsync(root)
    return copy.deepcopy(report)


def _release_pending(root, pending, marker):
    try:
        os.unlink(pending, dir_fd=root)
        os.fsync(root)
    except BaseException:
        # 同步失敗時保守重建隔離，不能讓已寫出的 verified JSON 自行放行。
        try:
            with _safe().open_file(root, pending, create=True) as stream:
                stream.write(media.encoded(marker))
        except FileExistsError:
            pass
        raise


def _operate(operation, contract, source, output, *, confirm_overwrite=False, timeout=21600,
             transport=None, monotonic=time.monotonic):
    contract = validate_contract(contract)
    require(type(timeout) in (int, float) and 0 < timeout <= 86400, "總期限無效")
    require(operation != "deploy" or (confirm_overwrite is True and contract["authorization"]["write"] is True),
            "缺少確認或覆寫授權；未啟動 SSH")
    end = monotonic() + timeout
    def check():
        require(monotonic() < end, "主機操作總期限已到")
    report = {"schema": "bpi-lab-external-result-v1", "operation": operation, "status": "failed",
              "hardware_validated": False, "root_uuid": contract["root"]["uuid"], "blockers": [],
              "media_identity": media.media_identity(contract["target"]), "contract_sha256": media.digest(contract),
              "contract": contract, "source": source, "backup": None, "remote": None,
              "writer_stopped": operation != "deploy", "quarantined": False, "full_readback_verified": False}
    with _isolation(contract) as (lock_root, pending):
        output = _new_directory(output)
        _save(output, "intent.json", report)
        started, marker = False, None
        stdout, stderr = bytearray(), bytearray()
        try:
            if operation != "backup":
                report["backup"] = _verify_backup(contract, end, monotonic, lock_root)
                verified = _verify_source(contract, source, check)
                report["source_layout"] = verified["layout"]
            program, sources = _program()
            report["program_sources"] = sources
            report["program_sha256"] = hashlib.sha256(program.encode()).hexdigest()
            request = {"operation": operation, "contract": contract, "source": source,
                       "backup_raw": report["backup"]["raw"] if report["backup"] else None,
                       "confirm_overwrite": confirm_overwrite, "nonce": secrets.token_hex(32),
                       "timeout": max(0.001, end - monotonic())}
            argv, report["ssh_config"] = _ssh(contract, output, program, request)
            report["request_evidence"] = _save(output, "request.json", request)
            if report["backup"] is not None:
                _revalidate_backup(report["backup"], lock_root)
            check()
            marker = {"nonce": request["nonce"], "output": str(output),
                      "media_identity": report["media_identity"], "writer_stopped": False}
            with _safe().open_file(lock_root, pending, create=True) as stream:
                stream.write(media.encoded(marker))
            if operation == "deploy":
                report["quarantined"] = True
            def chunks():
                if operation != "deploy":
                    return
                path = _path(source["path"])
                with _safe().open_root(path.parent) as root, _safe().open_file(root, path.name) as stream:
                    _same_file(root, path.name, stream, verified["file_identity"])
                    while blob := stream.read(CHUNK):
                        check()
                        yield blob
                    _same_file(root, path.name, stream, verified["file_identity"])
            transfer = transport or _libraries()[1].upload_stream
            exitcode, sent = None, 0
            with ExitStack() as stack:
                artifact = None
                if operation == "backup":
                    artifact_root = stack.enter_context(_safe().open_root(output))
                    artifact = stack.enter_context(_libraries()[0].new_file(artifact_root, "disk.img.gz.partial"))
                started = True
                iterator = stack.enter_context(closing(transfer(argv, iter(chunks()), end, monotonic)))
                for channel, value in iterator:
                    check()
                    if channel == "stdout":
                        require(type(value) is bytes, "SSH stdout 型別不符")
                        if artifact is not None:
                            require(artifact.tell() + len(value) <= contract["target"]["bytes"] + contract["target"]["bytes"] // 100 + CHUNK,
                                    "備份壓縮串流超過有界容量")
                            artifact.write(value)
                        else:
                            stdout.extend(value)
                            require(len(stdout) <= MAX_LOG, "SSH stdout 超限")
                    elif channel == "stderr":
                        require(type(value) is bytes, "SSH stderr 型別不符")
                        stderr.extend(value)
                        require(len(stderr) <= MAX_LOG, "SSH stderr 超限")
                    elif channel == "sent":
                        require(type(value) is int and value > 0, "SSH 傳輸計數無效")
                        sent += value
                        require(operation == "deploy" and sent <= source["compressed"]["bytes"], "SSH 傳輸超出來源")
                    elif channel == "exit":
                        require(exitcode is None and type(value) is int, "SSH 結束狀態重複或無效")
                        exitcode = value
                    else:
                        raise ExternalError("未知 SSH 傳輸事件")
            require(exitcode is not None, "SSH 沒有結束證據")
            state = _terminal(bytes(stderr), request)
            report["remote"] = state
            success = exitcode == 0 and state.get("status") == "verified"
            _validate_remote(state, contract, operation, source, success=success, check=check)
            report["writer_stopped"] = state["descriptors_closed"] and state["io_drained"]
            if marker and report["writer_stopped"]:
                report["quarantined"] = False
            require(success, state.get("error") or "遠端操作未完整成功")
            require(operation != "deploy" or sent == source["compressed"]["bytes"], "SSH 沒有完整傳送來源")
            if report["backup"] is not None:
                _revalidate_backup(report["backup"], lock_root)
            if operation == "backup":
                with _safe().open_root(output) as root:
                    checked = _libraries()[0].verify_gzip(root, "disk.img.gz.partial", contract["target"]["bytes"], end, monotonic)
                    require(checked["raw"] == state["target_before"], "主機重讀備份與遠端原始摘要不同")
                    os.link("disk.img.gz.partial", "disk.img.gz", src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
                    os.unlink("disk.img.gz.partial", dir_fd=root)
                    os.fsync(root)
                report["artifact"] = {"path": "disk.img.gz", **{k: checked[k] for k in ("raw", "compressed")}}
            report.update(status="verified", full_readback_verified=operation == "deploy")
            _validate_result_data(report, contract, source, check=check)
        except BaseException as exc:
            report.update(status="failed", full_readback_verified=False)
            report["blockers"].append(str(exc) if isinstance(exc, (ValueError, OSError)) else "主機操作被中斷")
            if operation == "deploy" and not started:
                report["writer_stopped"] = True
            report["quarantined"] = marker is not None and not report["writer_stopped"]
        report["stderr_evidence"] = _save(output, "stderr.log", bytes(stderr))
        report["stdout_evidence"] = _save(output, "stdout.log", bytes(stdout))
        if report["status"] == "verified":
            report["publication"] = {"schema": "bpi-lab-external-publication-v1", "nonce": request["nonce"],
                                     "directory": str(output)}
        _save(output, "manifest.json", report)
        if report["status"] == "verified":
            check()
            _save(contract["isolation_dir"], _completion_name(report), _publication_record(report, lock_root))
            _validate_publication(report, lock_root)
            check()
        if marker and report["writer_stopped"]:
            _release_pending(lock_root, pending, marker)
        return report


def backup(contract, output, *, timeout=21600, transport=None, monotonic=time.monotonic):
    return _operate("backup", contract, None, output, timeout=timeout, transport=transport, monotonic=monotonic)


def preflight(contract, source, output, *, timeout=21600, transport=None, monotonic=time.monotonic):
    return _operate("preflight", contract, source, output, timeout=timeout, transport=transport, monotonic=monotonic)


def deploy(contract, source, output, *, confirm_overwrite=False, timeout=21600, transport=None, monotonic=time.monotonic):
    return _operate("deploy", contract, source, output, confirm_overwrite=confirm_overwrite,
                    timeout=timeout, transport=transport, monotonic=monotonic)


def main(argv=None):
    import argparse
    class Formatter(argparse.HelpFormatter):
        def _format_usage(self, usage, actions, groups, prefix=None):
            return super()._format_usage(usage, actions, groups, "用法：" if prefix is None else prefix)
    parser = argparse.ArgumentParser(description="外部媒體完整備份與有限部署；不操作開機鏈或電源",
                                     add_help=False, formatter_class=Formatter)
    parser._positionals.title = "操作"
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明並結束")
    parser.add_argument("operation", choices=("backup", "preflight", "deploy"))
    parser.add_argument("--contract", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--source")
    parser.add_argument("--source-sha256")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=21600)
    parser.add_argument("--confirm-overwrite", action="store_true")
    args = parser.parse_args(argv)
    contract = _parse(_read({"path": args.contract, "sha256": args.contract_sha256}))
    if args.operation == "backup":
        result = backup(contract, args.output, timeout=args.timeout)
    else:
        source = _parse(_read({"path": args.source, "sha256": args.source_sha256}))
        function = deploy if args.operation == "deploy" else preflight
        options = {"confirm_overwrite": args.confirm_overwrite} if args.operation == "deploy" else {}
        result = function(contract, source, args.output, timeout=args.timeout, **options)
    print(media.encoded(result).decode(), end="")
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
