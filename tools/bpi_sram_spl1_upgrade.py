#!/usr/bin/env python3
"""0845 專用 SPL1 升級：固定候選、全前綴備份，只寫一段 40 KiB。"""

import fcntl
import json
import os
from pathlib import Path
import re
import sys

import bpi_h618_artifacts as files
import bpi_sram_package as package
import bpi_sram_sd_minimal as sd


PREFIX = sd.PREFIX
SPL_OFFSET, SPL_BYTES = sd.SPL_OFFSET, sd.SPL_BYTES
OLD_SHA = sd.SPL_SHA
NEW_SHA = "80e67d7ebaacb58d8b2b64a6a01a94b4d4329c5f11f8f9cb35e917d060f7e33d"
UNCHANGED = ((0, SPL_OFFSET), (SPL_OFFSET + SPL_BYTES, PREFIX))
PURPOSE = "0845-spl1-build009-to-v2-build003"
require, digest = sd.require, sd.digest


def unchanged_matches(before, current):
    return len(before) == len(current) == PREFIX and all(
        before[start:end] == current[start:end] for start, end in UNCHANGED)


def unchanged_hashes(prefix):
    return [digest(prefix[start:end]) for start, end in UNCHANGED]


def planned_prefix(before, spl):
    require(len(before) == PREFIX, "原始備份必須恰為前 4 MiB")
    require(digest(before[SPL_OFFSET:SPL_OFFSET + SPL_BYTES]) == OLD_SHA,
            "卡上 SPL1 不是已核准的舊 build-009")
    slot = before[sd.SLOT_OFFSET:sd.SLOT_OFFSET + sd.SLOT_BYTES]
    require(digest(slot) == sd.SLOT_SHA, "卡上第 0 號槽不是已驗證的 smoke")
    package.parse_package(slot)
    require(len(spl) == SPL_BYTES and digest(spl) == NEW_SHA,
            "新 SPL1 不符合固定 loader-v2-build-003")
    return before[:SPL_OFFSET] + spl + before[SPL_OFFSET + SPL_BYTES:]


def write_spl1(fd, current, desired, *, recheck):
    """不接受任意偏移；短寫重試也不能越過同一白名單。"""
    require(unchanged_matches(current, desired), "白名單外有變動，禁止寫入")
    recheck()
    require(sd.read_exact(fd, 0, PREFIX) == current, "寫入前媒體內容已改變")
    position = 0
    while position < SPL_BYTES:
        recheck()
        count = os.pwrite(fd, desired[SPL_OFFSET + position:SPL_OFFSET + SPL_BYTES],
                          SPL_OFFSET + position)
        require(type(count) is int and 0 < count <= SPL_BYTES - position,
                "媒體寫入長度無效，可能部分寫入")
        position += count
    os.fsync(fd)
    fcntl.ioctl(fd, 0x1261)  # BLKFLSBUF：同步後使後續讀取重新到媒體。
    recheck()
    require(sd.read_exact(fd, 0, PREFIX) == desired, "完整前 4 MiB 回讀不符")


def prepare(args, fd, identity, partition):
    spl = package.read_regular_file(Path(args.spl1), SPL_BYTES)
    before = sd.read_exact(fd, 0, PREFIX)
    require(sd.layout(before, identity["bytes"]) == partition, "MBR 與核心列舉不符")
    after = planned_prefix(before, spl)
    guards = sd.guards(fd, partition)
    require(sd.inspect_device(args.device, identity, fd) == partition,
            "備份期間媒體身分或布局改變")
    require(sd.read_exact(fd, 0, PREFIX) == before and sd.guards(fd, partition) == guards,
            "兩次媒體讀取不一致")
    out = Path(args.evidence_dir).absolute()
    with files.open_root(out.parent) as parent:
        os.mkdir(out.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    for name, blob in (("before.bin", before), ("expected.bin", after), ("spl1-egon.bin", spl)):
        sd.save(out, name, blob)
        require(package.read_regular_file(out / name, len(blob)) == blob,
                "主機備份持久化回讀不符")
    record = {"schema": 1, "purpose": PURPOSE, "board_label": args.board_label,
              "identity": identity, "partition": partition,
              "before_sha256": digest(before), "expected_sha256": digest(after),
              "old_spl1_sha256": OLD_SHA, "new_spl1_sha256": NEW_SHA,
              "slot0_sha256": sd.SLOT_SHA, "write_offset": SPL_OFFSET, "write_bytes": SPL_BYTES,
              "partition_guard_sha256": guards, "unchanged_sha256": unchanged_hashes(before)}
    blob = sd.json_bytes(record)
    sd.save(out, "prepared.json", blob)
    require(package.read_regular_file(out / "prepared.json", 65536) == blob,
            "主機備份清單持久化回讀不符")
    return {"status": "prepared", "prepared_sha256": digest(blob),
            "before_sha256": digest(before), "expected_sha256": digest(after),
            "media_written": False, "hardware_validated": False}


def load_prepared(args, identity, partition):
    out = Path(args.evidence_dir)
    blob = package.read_regular_file(out / "prepared.json", 65536)
    require(digest(blob) == args.prepared_sha256, "清單與外部提供的可信雜湊不符")
    record = files.parse_manifest(blob)
    fields = {"schema", "purpose", "board_label", "identity", "partition", "before_sha256",
              "expected_sha256", "old_spl1_sha256", "new_spl1_sha256", "slot0_sha256",
              "write_offset", "write_bytes", "partition_guard_sha256", "unchanged_sha256"}
    require(type(record) is dict and set(record) == fields, "升級清單欄位不符")
    require(type(record["schema"]) is int and record["schema"] == 1
            and record["purpose"] == PURPOSE and record["board_label"] == args.board_label
            and record["identity"] == identity and record["partition"] == partition,
            "升級清單身分、板號、布局或用途不符")
    require(record["old_spl1_sha256"] == OLD_SHA and record["new_spl1_sha256"] == NEW_SHA
            and record["slot0_sha256"] == sd.SLOT_SHA
            and type(record["write_offset"]) is int and record["write_offset"] == SPL_OFFSET
            and type(record["write_bytes"]) is int and record["write_bytes"] == SPL_BYTES,
            "升級清單範圍或固定產物不符")
    for key, count in (("partition_guard_sha256", 2), ("unchanged_sha256", 2)):
        require(type(record[key]) is list and len(record[key]) == count
                and all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in record[key]),
                "升級清單抽查雜湊格式不符")
    before = package.read_regular_file(out / "before.bin", PREFIX)
    require(digest(before) == record["before_sha256"]
            and sd.layout(before, identity["bytes"]) == partition
            and unchanged_hashes(before) == record["unchanged_sha256"], "原始備份內容不符")
    spl = package.read_regular_file(out / "spl1-egon.bin", SPL_BYTES)
    after = planned_prefix(before, spl)
    require(digest(after) == record["expected_sha256"]
            and package.read_regular_file(out / "expected.bin", PREFIX) == after,
            "預期升級內容或備份不符")
    return record, before, after


def change(args, fd, identity, partition):
    require(args.action in ("apply", "restore"), "不是明確升級或復原動作")
    record, before, after = load_prepared(args, identity, partition)
    current = sd.read_exact(fd, 0, PREFIX)
    require(sd.guards(fd, partition) == record["partition_guard_sha256"], "分割區抽查已變動")
    require(unchanged_matches(before, current), "白名單外已變動，拒絕寫入或復原")
    desired = after if args.action == "apply" else before
    require(args.action == "restore" or current in (before, after),
            "SPL1 不是已驗證原始狀態，拒絕再次升級；須另行評估復原")
    attempt = sd.attempt_directory(Path(args.evidence_dir), args.action)
    sd.save(attempt, "before.bin", current)

    def recheck():
        require(sd.inspect_device(args.device, identity, fd) == partition,
                "操作期間媒體身分或布局改變")

    try:
        recheck()
        require(sd.read_exact(fd, 0, PREFIX) == current, "操作前媒體內容已改變")
        changed = current != desired
        if changed:
            write_spl1(fd, current, desired, recheck=recheck)
        recheck()
        readback = sd.read_exact(fd, 0, PREFIX)
        require(readback == desired, "最終前 4 MiB 回讀不符")
        guards = sd.guards(fd, partition)
        require(guards == record["partition_guard_sha256"], "分割區抽查回讀不符")
        sd.save(attempt, "after.bin", readback)
        result = {"status": ("applied" if args.action == "apply" else "restored") if changed else "already_matches",
                  "media_written": changed, "hardware_validated": False,
                  "attempt_directory": attempt.name, "readback_sha256": digest(readback),
                  "spl1_sha256": digest(readback[SPL_OFFSET:SPL_OFFSET + SPL_BYTES]),
                  "slot0_sha256": digest(readback[sd.SLOT_OFFSET:sd.SLOT_OFFSET + sd.SLOT_BYTES]),
                  "unchanged_sha256": unchanged_hashes(readback), "partition_guard_sha256": guards,
                  "write_offset": SPL_OFFSET, "write_bytes": SPL_BYTES if changed else 0}
        sd.save(attempt, "result.json", sd.json_bytes(result))
        return result
    except Exception:
        failure = {"status": "失敗，可能部分寫入；不可拔卡試跑", "automatic_restore": False,
                   "next_action": "保留媒體，核對本次備份與身分後再明確執行 restore"}
        try:
            failed_readback = sd.read_exact(fd, 0, PREFIX)
            sd.save(attempt, "failure-readback.bin", failed_readback)
            failure["readback_sha256"] = digest(failed_readback)
        except (OSError, ValueError):
            failure["readback_saved"] = False
        try:
            sd.save(attempt, "failure.json", sd.json_bytes(failure))
        except (OSError, ValueError):
            pass
        raise


def main(argv=None):
    parser = package.ChineseArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "restore"), help="備份、升級或復原")
    parser.add_argument("--device", required=True, help="已核對的原生 SD 整碟路徑")
    parser.add_argument("--cid", required=True, help="事先核對的完整 CID")
    parser.add_argument("--bytes", required=True, type=int, help="事先核對的精確容量")
    parser.add_argument("--devnum", required=True, help="事先核對的主次裝置號")
    parser.add_argument("--board-label", required=True, choices=("0845",), help="人工核對的本輪板號")
    parser.add_argument("--evidence-dir", required=True, help="備份目錄，prepare 不得已存在")
    parser.add_argument("--spl1", help="prepare 使用的固定新版 SPL1 一般檔案")
    parser.add_argument("--prepared-sha256", help="apply／restore 使用的外部可信清單雜湊")
    parser.add_argument("--confirm-write", action="store_true", help="明確確認僅寫入 40 KiB SPL1")
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0, "媒體排他操作需要 root")
        require(re.fullmatch(r"[0-9a-f]{32}", args.cid) is not None
                and args.bytes >= 64 * 1024**2 and re.fullmatch(r"[0-9]+:[0-9]+", args.devnum),
                "預期身分格式無效")
        if args.action == "prepare":
            require(args.spl1 and not args.prepared_sha256 and not args.confirm_write,
                    "prepare 僅接受候選來源，不接受寫入確認或舊清單")
        else:
            require(args.confirm_write and not args.spl1 and args.prepared_sha256
                    and re.fullmatch(r"[0-9a-f]{64}", args.prepared_sha256),
                    "缺少明確寫入確認或可信清單雜湊，或混入候選來源")
        identity = {"cid": args.cid, "bytes": args.bytes, "devnum": args.devnum}
        partition = sd.inspect_device(args.device, identity)
        mode = os.O_RDONLY if args.action == "prepare" else os.O_RDWR
        fd = os.open(args.device, mode | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            require(sd.inspect_device(args.device, identity, fd) == partition, "開啟期間布局已變動")
            result = (prepare if args.action == "prepare" else change)(args, fd, identity, partition)
        finally:
            os.close(fd)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "裝置或證據操作失敗，須檢查是否部分寫入"
        print(json.dumps({"status": "error", "error": message, "error_type": type(exc).__name__,
                          "hardware_validated": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
