#!/usr/bin/env python3
"""0845 第三版實驗基礎部署：保留原系統與 FIT，另放配套 FIT、入口與 SRAM 槽。"""

import fcntl
import json
import os
from pathlib import Path
import re
import struct
import sys

if __package__:
    from . import bpi_h618_artifacts as files
    from . import bpi_sram_lab_package as lab
    from . import bpi_sram_sd_minimal as sd
else:
    import bpi_h618_artifacts as files
    import bpi_sram_lab_package as lab
    import bpi_sram_sd_minimal as sd


base = lab.base
require, digest = sd.require, sd.digest
OLD_PREFIX_SHA = "59a8e09244f38ac4134944e23212e4ca69d754171b0ac830694b910a457012f2"
LOADER_SHA = "4e363738b409afdd9b035fa83aeab7e21a565da2a302662455d9003b68fabaae"
PURPOSE = "0845-lab-v3-a1-fit-preserve-original-system"
RANGES = ((8192, 40960), (1024 * 1024, 1024 * 1024),
          (6400 * 512, 128 * 1024), (6912 * 512, 128 * 1024))


def unchanged(before, after):
    if len(before) != sd.PREFIX or len(after) != sd.PREFIX:
        return False
    start = 0
    for offset, size in RANGES:
        if before[start:offset] != after[start:offset]:
            return False
        start = offset + size
    return before[start:] == after[start:]


def planned_prefix(before, loader, fit, updater, boot, hashes):
    require(len(before) == sd.PREFIX and digest(before) == OLD_PREFIX_SHA,
            "卡上前綴不是本輪已核准的 ABI 2 部署，不可直接覆寫")
    require(len(loader) == 40960 and digest(loader) == LOADER_SHA, "固定入口不是已核對的第三版產物")
    require(40 <= len(fit) <= 1024 * 1024 and re.fullmatch(r"[0-9a-f]{64}", hashes["fit"])
            and digest(fit) == hashes["fit"], "配套 FIT 長度或外部雜湊不符")
    magic, total = struct.unpack_from(">II", fit)
    require(magic == 0xd00dfeed and 40 <= total <= len(fit), "配套 FIT 標頭不符")
    for name, blob, kind in (("updater", updater, 3), ("boot", boot, 4)):
        require(re.fullmatch(r"[0-9a-f]{64}", hashes[name]) and digest(blob) == hashes[name],
                "負載與外部明確指定的雜湊不符")
        metadata = lab.parse_package(blob)
        require(metadata["kind"] == kind and metadata["runtime_size"] == 0x18000,
                "負載種類或 SRAM 執行期配置不符")
    after = bytearray(before)
    for (offset, size), blob in zip(RANGES, (loader, fit, updater, boot)):
        require(len(blob) <= size and offset + size <= sd.PREFIX, "部署範圍超出保留區")
        after[offset:offset + size] = blob + bytes(size - len(blob))
    require(unchanged(before, after), "部署計畫修改了白名單以外的資料")
    return bytes(after)


def write_ranges(fd, current, desired, recheck):
    require(unchanged(current, desired), "拒絕白名單外寫入")
    recheck()
    require(sd.read_exact(fd, 0, sd.PREFIX) == current, "寫入前卡片內容已變更")
    # 先放負載，最後才切換固定入口；中斷也保留既有備份與原系統。
    for offset, size in (*RANGES[1:], RANGES[0]):
        done = 0
        while done < size:
            recheck()
            count = os.pwrite(fd, desired[offset + done:offset + size], offset + done)
            require(type(count) is int and 0 < count <= size - done, "寫入長度無效，可能部分寫入")
            done += count
        os.fsync(fd)
        fcntl.ioctl(fd, 0x1261)
        recheck()
        require(sd.read_exact(fd, offset, size) == desired[offset:offset + size],
                "已寫範圍回讀不符，禁止繼續切換入口")
    fcntl.ioctl(fd, 0x1261)
    recheck()
    require(sd.read_exact(fd, 0, sd.PREFIX) == desired, "前 4 MiB 完整回讀不符")


def prepare(args, fd, identity, partition):
    payloads = {name: base.read_regular_file(Path(getattr(args, name)), limit) for name, limit in (
        ("loader", 40960), ("fit", 1024 * 1024), ("updater", base.MAX_PACKAGE_BYTES), ("boot", base.MAX_PACKAGE_BYTES))}
    hashes = {name: getattr(args, name + "_sha256") for name in ("fit", "updater", "boot")}
    before = sd.read_exact(fd, 0, sd.PREFIX)
    require(sd.layout(before, identity["bytes"]) == partition, "MBR 與核心分割區列舉不符")
    after = planned_prefix(before, **payloads, hashes=hashes)
    guards = sd.guards(fd, partition)
    require(sd.inspect_device(args.device, identity, fd) == partition, "備份期間媒體身分變更")
    require(sd.read_exact(fd, 0, sd.PREFIX) == before, "備份期間媒體內容變更")
    out = Path(args.evidence_dir).absolute()
    with files.open_root(out.parent) as parent:
        os.mkdir(out.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    for name, blob in {"before": before, "expected": after, **payloads}.items():
        sd.save(out, name + ".bin", blob)
        require(base.read_regular_file(out / (name + ".bin"), len(blob)) == blob, "備份持久化回讀不符")
    record = {"schema": 1, "purpose": PURPOSE, "identity": identity, "partition": partition,
              "board": "0845", "before_sha256": digest(before), "expected_sha256": digest(after),
              "loader_sha256": LOADER_SHA, "payload_sha256": hashes,
              "ranges": [list(x) for x in RANGES], "partition_guard_sha256": guards}
    blob = sd.json_bytes(record)
    sd.save(out, "prepared.json", blob)
    require(base.read_regular_file(out / "prepared.json", 65536) == blob, "備份清單持久化回讀不符")
    return {"status": "prepared", "prepared_sha256": digest(blob), "expected_sha256": digest(after),
            "media_written": False, "hardware_validated": False}


def change(args, fd, identity, partition):
    out = Path(args.evidence_dir)
    blob = base.read_regular_file(out / "prepared.json", 65536)
    require(digest(blob) == args.prepared_sha256, "備份清單與外部可信雜湊不符")
    record = files.parse_manifest(blob)
    require(type(record) is dict and set(record) == {
        "schema", "purpose", "identity", "partition", "board", "before_sha256", "expected_sha256",
        "loader_sha256", "payload_sha256", "ranges", "partition_guard_sha256"}, "備份清單欄位不符")
    require(type(record["schema"]) is int and record["schema"] == 1
            and record["purpose"] == PURPOSE and record["board"] == "0845"
            and sd.json_bytes(record["identity"]) == sd.json_bytes(identity)
            and sd.json_bytes(record["partition"]) == sd.json_bytes(partition)
            and record["ranges"] == [list(x) for x in RANGES] and record["loader_sha256"] == LOADER_SHA,
            "備份清單身分、用途或範圍不符")
    require(type(record["ranges"]) is list and all(type(pair) is list
            and all(type(value) is int for value in pair) for pair in record["ranges"]), "範圍必須是整數陣列")
    require(type(record["payload_sha256"]) is dict and set(record["payload_sha256"]) == {"fit", "updater", "boot"},
            "負載清單欄位不符")
    guards = record["partition_guard_sha256"]
    require(type(guards) is list and len(guards) == 2 and all(isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value) for value in guards), "分割區抽查雜湊格式不符")
    before = base.read_regular_file(out / "before.bin", sd.PREFIX)
    require(digest(before) == record["before_sha256"], "原始備份雜湊不符")
    payloads = {name: base.read_regular_file(out / (name + ".bin"), limit) for name, limit in (
        ("loader", 40960), ("fit", 1024 * 1024), ("updater", base.MAX_PACKAGE_BYTES), ("boot", base.MAX_PACKAGE_BYTES))}
    after = planned_prefix(before, **payloads, hashes=record["payload_sha256"])
    require(digest(after) == record["expected_sha256"]
            and base.read_regular_file(out / "expected.bin", sd.PREFIX) == after, "預期備份不符")
    require(sd.layout(before, identity["bytes"]) == partition, "備份分割區配置不符")
    current = sd.read_exact(fd, 0, sd.PREFIX)
    require(unchanged(before, current), "部署白名單外內容變動，拒絕寫入或復原")
    require(args.action == "restore" or current in (before, after), "目前不是核對過的完整狀態，須評估復原")
    require(sd.guards(fd, partition) == record["partition_guard_sha256"], "原分割區抽查已有變更")
    desired = after if args.action == "apply" else before
    attempt = sd.attempt_directory(out, args.action)
    sd.save(attempt, "before.bin", current)

    def recheck():
        require(sd.inspect_device(args.device, identity, fd) == partition, "操作期間裝置身分或布局變動")

    try:
        if current != desired:
            write_ranges(fd, current, desired, recheck)
        recheck()
        readback = sd.read_exact(fd, 0, sd.PREFIX)
        require(readback == desired, "最終前綴回讀不符")
        require(sd.guards(fd, partition) == record["partition_guard_sha256"], "原分割區抽查回讀不符")
        sd.save(attempt, "after.bin", readback)
        result = {"status": "applied" if args.action == "apply" else "restored",
                  "media_written": current != desired, "hardware_validated": False,
                  "prefix_sha256": digest(readback), "unchanged_outside_ranges": unchanged(before, readback),
                  "partition_guards_match": True, "attempt_directory": str(attempt)}
        sd.save(attempt, "result.json", sd.json_bytes(result))
        return result
    except Exception:
        try:
            sd.save(attempt, "failure-readback.bin", sd.read_exact(fd, 0, sd.PREFIX))
            sd.save(attempt, "failure.json", sd.json_bytes({"status": "可能部分寫入，保留卡片與備份", "automatic_restore": False}))
        except (OSError, ValueError):
            pass
        raise


def main(argv=None):
    parser = base.ChineseArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "restore"), help="備份、部署或復原")
    for name in ("device", "cid", "bytes", "devnum", "evidence-dir"):
        parser.add_argument("--" + name, required=True, type=int if name == "bytes" else str, help="已核對的部署身分或主機證據位置")
    parser.add_argument("--board-label", choices=("0845",), required=True, help="已確認的板號")
    for name in ("loader", "fit", "updater", "boot", "fit-sha256", "updater-sha256", "boot-sha256", "prepared-sha256"):
        parser.add_argument("--" + name, help="建置來源或外部核對的 SHA-256")
    parser.add_argument("--confirm-write", action="store_true", help="明確確認白名單部署或復原")
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0, "媒體排他操作需要 root")
        require(re.fullmatch(r"[0-9a-f]{32}", args.cid) and re.fullmatch(r"[0-9]+:[0-9]+", args.devnum), "媒體識別格式不符")
        candidates = (args.loader, args.fit, args.updater, args.boot, args.fit_sha256, args.updater_sha256, args.boot_sha256)
        if args.action == "prepare":
            require(all(candidates) and not args.confirm_write and not args.prepared_sha256, "備份必須提供完整來源且不得帶寫入確認")
        else:
            require(not any(candidates) and args.confirm_write and args.prepared_sha256
                    and re.fullmatch(r"[0-9a-f]{64}", args.prepared_sha256), "部署或復原必須明確確認並指定可信清單")
        identity = {"cid": args.cid, "bytes": args.bytes, "devnum": args.devnum}
        partition = sd.inspect_device(args.device, identity)
        mode = os.O_RDONLY if args.action == "prepare" else os.O_RDWR
        fd = os.open(args.device, mode | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            require(sd.inspect_device(args.device, identity, fd) == partition, "開啟期間裝置變更")
            result = (prepare if args.action == "prepare" else change)(args, fd, identity, partition)
        finally:
            os.close(fd)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else "裝置或證據操作失敗，須確認是否部分寫入"
        print(json.dumps({"status": "error", "error": detail, "hardware_validated": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
