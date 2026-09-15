#!/usr/bin/env python3
"""僅部署已固定的 SRAM build-009：備份前 4 MiB，只寫 SPL1 與槽 0。"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

import bpi_h618_artifacts as files
import bpi_sram_package as package


PREFIX = 4 * 1024**2
GUARD = 1024**2
SPL_OFFSET, SLOT_OFFSET = 8192, 3 * 1024**2
SPL_BYTES, SLOT_BYTES = 40960, 1536
SPL_SHA = "aef7a1a8c4eb84eb73b4fee519b93561ef5722fc0fc442049e308836695f7f76"
SLOT_SHA = "8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062"
EXTENTS = ((SLOT_OFFSET, SLOT_BYTES), (SPL_OFFSET, SPL_BYTES))
UNCHANGED = ((0, SPL_OFFSET), (SPL_OFFSET + SPL_BYTES, SLOT_OFFSET),
             (SLOT_OFFSET + SLOT_BYTES, PREFIX))


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def read_exact(fd, offset, size):
    chunks = []
    while size:
        chunk = os.pread(fd, min(size, 1024**2), offset)
        require(bool(chunk), "媒體讀取不完整")
        chunks.append(chunk)
        offset, size = offset + len(chunk), size - len(chunk)
    return b"".join(chunks)


def layout(prefix, capacity):
    require(len(prefix) == PREFIX and prefix[510:512] == b"\x55\xaa", "不是完整的 MBR 前綴")
    used = [prefix[p:p + 16] for p in range(446, 510, 16) if any(prefix[p:p + 16])]
    require(len(used) == 1 and used[0][4] == 0x83, "僅接受單一 Linux MBR 分割區，不接受 GPT 或延伸分割區")
    start, sectors = struct.unpack_from("<II", used[0], 8)
    require(start == PREFIX // 512 and sectors * 512 >= 2 * GUARD
            and (start + sectors) * 512 <= capacity, "分割區布局不符合 4 MiB 最小部署")
    return {"start": start, "sectors": sectors}


def planned_prefix(before, spl, slot):
    require(len(before) == PREFIX, "備份長度錯誤")
    require(len(spl) == SPL_BYTES and digest(spl) == SPL_SHA, "SPL1 不符合固定 build-009")
    require(len(slot) == SLOT_BYTES and digest(slot) == SLOT_SHA, "槽 0 封包不符合固定 build-009")
    package.parse_package(slot)
    after = bytearray(before)
    after[SPL_OFFSET:SPL_OFFSET + SPL_BYTES] = spl
    after[SLOT_OFFSET:SLOT_OFFSET + SLOT_BYTES] = slot
    return bytes(after)


def unchanged_matches(before, current):
    return len(before) == len(current) == PREFIX and all(
        before[start:end] == current[start:end] for start, end in UNCHANGED)


def write_extents(fd, current, desired, *, restore=False, recheck=lambda: None):
    require(unchanged_matches(current, desired), "白名單外有變動，禁止寫入或自動復原")
    for start, length in reversed(EXTENTS) if restore else EXTENTS:
        recheck()
        position = 0
        while position < length:
            count = os.pwrite(fd, desired[start + position:start + length], start + position)
            require(0 < count <= length - position, "媒體短寫或寫入長度無效")
            position += count
        os.fsync(fd)
        fcntl.ioctl(fd, 0x1261)  # BLKFLSBUF：刷新後再回讀，不重讀或改寫分割表。
        require(read_exact(fd, start, length) == desired[start:start + length], "單一寫入範圍回讀不符")
    recheck()
    require(read_exact(fd, 0, PREFIX) == desired, "完整前 4 MiB 回讀不符")


def inspect_device(device, expected, fd=None):
    require(re.fullmatch(r"/dev/mmcblk[0-9]+", device) is not None, "只接受明確的原生 MMC 整碟路徑")
    node = os.stat(device, follow_symlinks=False)
    require(stat.S_ISBLK(node.st_mode), "目標不是區塊裝置")
    name = Path(device).name
    base = Path("/sys/class/block") / name
    devnum = f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}"
    actual = {"cid": (base / "device/cid").read_text().strip(),
              "bytes": int((base / "size").read_text()) * 512, "devnum": devnum}
    require(actual == expected, "SD 的 CID、容量或裝置號與預期不符")
    require((base / "device/type").read_text().strip() == "SD", "拒絕 eMMC 或未知媒體")
    require((base / "dev").read_text().strip() == devnum and
            (base / "queue/logical_block_size").read_text().strip() == "512", "sysfs 裝置號或扇區大小不符")
    require((base / "ro").read_text().strip() == "0", "卡片為唯讀")
    partitions = sorted(base.glob(name + "p*"))
    require(len(partitions) == 1 and partitions[0].name == name + "p1", "核心列舉的分割區不符合最小部署")
    numbers = {tuple(map(int, (entry / "dev").read_text().strip().split(":")))
               for entry in [base, *partitions]}
    require(not any(list((entry / "holders").iterdir()) for entry in [base, *partitions]), "媒體有其他區塊裝置使用中")
    root = os.stat("/").st_dev
    require((os.major(root), os.minor(root)) not in numbers, "拒絕根檔案系統所在媒體")
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        require(tuple(map(int, line.split()[2].split(":"))) not in numbers, "媒體或分割區仍被掛載")
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        path = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), line.split()[0])
        swap = os.stat(path)
        number = swap.st_rdev if stat.S_ISBLK(swap.st_mode) else swap.st_dev
        require((os.major(number), os.minor(number)) not in numbers, "媒體包含啟用中的 swap")
    if fd is not None:
        require(os.fstat(fd).st_rdev == node.st_rdev, "開啟後裝置號不符")
        size = struct.unpack("=Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
        require(size == expected["bytes"], "開啟後媒體容量不符")
    return {"start": int((partitions[0] / "start").read_text()),
            "sectors": int((partitions[0] / "size").read_text())}


def save(root, name, blob):
    with files.open_root(root) as directory, files.open_file(directory, name, create=True) as stream:
        stream.write(blob)


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def attempt_directory(root, action):
    with files.open_root(root) as parent:
        for number in range(1, 10000):
            name = f"{action}-{number:04d}"
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                continue
            os.fsync(parent)
            return Path(root) / name
    raise ValueError("嘗試紀錄數量已超過上限")


def guards(fd, partition):
    start, end = partition["start"] * 512, (partition["start"] + partition["sectors"]) * 512
    return [digest(read_exact(fd, start, GUARD)), digest(read_exact(fd, end - GUARD, GUARD))]


def prepare(args, fd, expected, partition):
    spl = package.read_regular_file(Path(args.build_dir) / "spl1-egon.bin", SPL_BYTES)
    slot = package.read_regular_file(Path(args.build_dir) / "spl2-smoke.sram", SLOT_BYTES)
    before = read_exact(fd, 0, PREFIX)
    require(layout(before, expected["bytes"]) == partition, "MBR 與核心分割區資訊不符")
    after = planned_prefix(before, spl, slot)
    require(before != after, "此卡已是相同部署，禁止覆蓋原備份")
    guard_hashes = guards(fd, partition)
    require(read_exact(fd, 0, PREFIX) == before, "兩次讀取的原始前綴不一致")
    out = Path(args.evidence_dir).absolute()
    with files.open_root(out.parent) as parent:
        os.mkdir(out.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    for name, blob in (("before.bin", before), ("expected.bin", after),
                       ("spl1-egon.bin", spl), ("spl2-smoke.sram", slot)):
        save(out, name, blob)
        require(package.read_regular_file(out / name, len(blob)) == blob, "主機備份持久化回讀不符")
    record = {"schema": 1, "board_label": args.board_label, "identity": expected,
              "partition": partition, "before_sha256": digest(before), "expected_sha256": digest(after),
              "partition_guard_sha256": guard_hashes,
              "unchanged_sha256": [digest(before[start:end]) for start, end in UNCHANGED],
              "scope": "只備份前 4 MiB；不是整卡備份，分割區僅抽查首尾各 1 MiB"}
    blob = json_bytes(record)
    save(out, "prepared.json", blob)
    return {"status": "prepared", "prepared_sha256": digest(blob), "before_sha256": digest(before),
            "expected_sha256": digest(after), "media_written": False, "hardware_validated": False}


def change(args, fd, expected, partition):
    out = Path(args.evidence_dir)
    record_blob = package.read_regular_file(out / "prepared.json", 65536)
    require(digest(record_blob) == args.prepared_sha256, "備份清單與可信紀錄的 SHA-256 不符")
    record = files.parse_manifest(record_blob)
    require(record["schema"] == 1 and record["identity"] == expected
            and record["partition"] == partition and record["board_label"] == args.board_label,
            "備份身分、板號或布局不符")
    before = package.read_regular_file(out / "before.bin", PREFIX)
    require(digest(before) == record["before_sha256"] and layout(before, expected["bytes"]) == partition,
            "原始備份內容不符")
    spl = package.read_regular_file(out / "spl1-egon.bin", SPL_BYTES)
    slot = package.read_regular_file(out / "spl2-smoke.sram", SLOT_BYTES)
    after = planned_prefix(before, spl, slot)
    require(digest(after) == record["expected_sha256"], "預期部署內容不符")
    current = read_exact(fd, 0, PREFIX)
    require(guards(fd, partition) == record["partition_guard_sha256"], "分割區抽查範圍已有變動，停止處理")
    restore = args.action == "restore"
    desired = before if restore else after
    require(unchanged_matches(before, current), "白名單外已改變，不能部署或復原")
    if current == desired:
        return {"status": "already_matches", "media_written": False, "hardware_validated": False}
    require(restore or current == before, "開機區不是已核對的原始備份，禁止重複部署")
    attempt = attempt_directory(out, args.action)
    save(attempt, "before.bin", current)
    def recheck():
        require(inspect_device(args.device, expected, fd) == partition, "寫入期間分割區布局已改變")

    try:
        recheck()
        require(read_exact(fd, 0, PREFIX) == current, "寫入前開機區已改變")
        write_extents(fd, current, desired, restore=restore,
                      recheck=recheck)
        require(guards(fd, partition) == record["partition_guard_sha256"], "分割區抽查範圍回讀不符")
        readback = read_exact(fd, 0, PREFIX)
        save(attempt, "after.bin", readback)
        result = {"status": "restored" if restore else "applied", "media_written": True,
                  "readback_sha256": digest(readback), "hardware_validated": False,
                  "attempt_directory": attempt.name,
                  "unchanged_sha256": [digest(readback[start:end]) for start, end in UNCHANGED],
                  "partition_guard_sha256": guards(fd, partition)}
        save(attempt, "result.json", json_bytes(result))
        return result
    except Exception:
        save(attempt, "failure.json", json_bytes({
            "status": "失敗，可能部分寫入；不可拔卡試跑", "automatic_restore": False,
            "next_action": "先保留回讀並核對身分，另行明確執行受控復原"}))
        raise


def main(argv=None):
    parser = package.ChineseArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "restore"), help="備份、最小部署或復原")
    parser.add_argument("--device", required=True, help="已核對的原生 SD 整碟路徑")
    parser.add_argument("--cid", required=True, help="事先核對的完整 CID")
    parser.add_argument("--bytes", required=True, type=int, help="事先核對的精確容量")
    parser.add_argument("--devnum", required=True, help="事先核對的主次裝置號")
    parser.add_argument("--board-label", required=True, help="人工核對的板號，不是自動身分識別")
    parser.add_argument("--evidence-dir", required=True, help="主機上的受保護備份目錄")
    parser.add_argument("--build-dir", help="僅供 prepare 使用的 build-009 路徑")
    parser.add_argument("--prepared-sha256", help="apply／restore 必須提供可信備份清單雜湊")
    parser.add_argument("--confirm-write", action="store_true", help="已明確核准兩個範圍的寫入")
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0, "媒體排他開啟需要 root")
        require(re.fullmatch(r"[0-9a-f]{32}", args.cid) is not None and args.bytes >= 64 * 1024**2
                and re.fullmatch(r"[0-9]+:[0-9]+", args.devnum) is not None
                and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.board_label) is not None, "預期身分格式無效")
        require((args.action == "prepare" and args.build_dir) or
                (args.action != "prepare" and args.confirm_write and args.prepared_sha256), "缺少建置來源或寫入確認及備份雜湊")
        expected = {"cid": args.cid, "bytes": args.bytes, "devnum": args.devnum}
        partition = inspect_device(args.device, expected)
        mode = os.O_RDONLY if args.action == "prepare" else os.O_RDWR
        descriptor = os.open(args.device, mode | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            require(inspect_device(args.device, expected, descriptor) == partition, "開啟期間布局已改變")
            report = prepare(args, descriptor, expected, partition) if args.action == "prepare" else change(args, descriptor, expected, partition)
        finally:
            os.close(descriptor)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "裝置或證據操作失敗，須檢查可能的部分寫入"
        print(json.dumps({"status": "error", "error": message,
                          "error_type": type(exc).__name__, "hardware_validated": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
