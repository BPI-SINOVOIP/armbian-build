#!/usr/bin/env python3
"""唯讀盤點 M4 Zero 十映像；串流驗證不落地 IMG，也不操作硬體。"""

from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import struct
import sys
import uuid

import bpi_h618_artifacts as safe


PROFILES = ("bananapim4zero", "bananapim4zeroemac")
RELEASES = ("bookworm", "jammy", "noble", "resolute", "trixie")
VARIANTS = ("minimal", "cli", "CLI", "xfce_desktop")
CHUNK = 1024**2
MEMLIMIT = 256 * CHUNK
MAX_RAW = 64 * 1024**3
SCHEMA = 1
NAME = re.compile(
    r"Armbian[^_]*_[^_]+_(?P<profile>Bananapim4zero(?:emac)?)_"
    r"(?P<release>[a-z]+)_(?P<branch>[a-z]+)_(?P<kernel>[0-9][A-Za-z0-9.+-]*)_"
    r"(?P<variant>minimal|cli|CLI|xfce_desktop)(?P<suffix>_[A-Za-z0-9.+-]+)?\.img\.xz"
)
IDENTITY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
LIMITS = {
    "hardware_validated": False, "bootable_verified": False,
    "write_authorized": False, "source_authenticated": False,
    "boot_components_verified": False,
    "scope": "僅離線檔名、一般檔案、XZ、雜湊及主分割表初檢；未掛載或解析檔案系統與開機組件",
}
require = safe.require
MatrixError = safe.ArtifactError


def digest_json(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def identity(info):
    return {field: getattr(info, field) for field in IDENTITY_FIELDS}


def file_identity(directory, name):
    with safe.open_file(directory, name) as stream:
        return identity(os.fstat(stream.fileno()))


def small_file(directory, name):
    return safe.fingerprint(directory, name, limit=safe.MAX_MANIFEST_BYTES, keep=True)


def sha256_value(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def parse_name(name):
    safe.relative_parts(name)
    match = NAME.fullmatch(name)
    require(match is not None, f"映像檔名板型或角色不明；不得猜定 CLI：{name}")
    entry = match.groupdict()
    entry["profile"] = entry["profile"].lower()
    return {"path": name, **entry}


def read_tsv(directory, name):
    digest, blob = small_file(directory, name)
    try:
        reader = csv.DictReader(io.StringIO(blob.decode("utf-8")), delimiter="\t")
        fields = reader.fieldnames
        require(fields and len(fields) == len(set(fields)), "TSV 欄位為空或重複")
        rows = list(reader)
        require(len(rows) <= 32 and all(None not in row and None not in row.values()
                                       for row in rows), "TSV 列數或欄位數無效")
    except (UnicodeError, csv.Error) as exc:
        raise MatrixError("TSV 格式無效") from exc
    require(all("xz_filename" in row for row in rows), "TSV 缺少 xz_filename")
    mapped = {row["xz_filename"]: row for row in rows}
    require(len(mapped) == len(rows), "TSV 映像檔名重複")
    return mapped, {"path": name, **digest}


def metadata(directory, entries, source_commit=None, source_record=None):
    references = []
    require(bool(source_commit) == bool(source_record), "來源提交及來源紀錄必須一起指定")
    if source_commit:
        require(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None, "來源提交格式無效")
        digest, blob = small_file(directory, source_record)
        require(source_commit.encode("ascii") in blob, "來源紀錄未包含指定提交")
        references.append({"path": source_record, **digest})
    tables = {}
    for name in ("IMAGE_MANIFEST.tsv", "BUILD_PROVENANCE.tsv"):
        try:
            table, reference = read_tsv(directory, name)
        except FileNotFoundError:
            continue
        require(set(table) == {entry["path"] for entry in entries}, "TSV 與候選檔名集合不符")
        tables[name] = table
        references.append(reference)
    for entry in entries:
        name = entry["path"]
        digest, blob = small_file(directory, name + ".sha")
        match = re.fullmatch(rb"([0-9a-fA-F]{64}) [ *]" + re.escape(name.encode("ascii")) + rb"\n?", blob)
        require(match is not None, f"雜湊旁檔格式或完整檔名不符：{name}")
        entry["expected_compressed_sha256"] = match[1].decode("ascii").lower()
        entry["checksum_record"] = {"path": name + ".sha", **digest}
        entry["expected_raw"] = None
        entry["source_commit"] = source_commit
        entry["source_record"] = source_record
        entry["source_status"] = "操作人員依紀錄指定；不是密碼學來源證明" if source_commit else "未記錄，不以目前 HEAD 推定"
        entry["declared_metadata"] = {}
        for table_name, table in tables.items():
            row = table[name]
            require(row.get("release") == entry["release"], "TSV 發行版與檔名不符")
            # 保留歷史 profile 的 cli 字樣，不將它覆寫為檔名 minimal。
            entry["declared_metadata"][table_name] = row
            if table_name == "BUILD_PROVENANCE.tsv":
                commit = row.get("artifact_source_commit", "")
                require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "TSV 來源提交格式無效")
                require(not source_commit or commit == source_commit, "來源提交紀錄互相衝突")
                entry.update(source_commit=commit, source_record=table_name,
                             source_status="建置程序紀錄；不是密碼學來源證明")
            else:
                try:
                    raw_size, xz_size = int(row["raw_size"]), int(row["xz_size"])
                    raw_sha, xz_sha = row["raw_sha256"], row["xz_sha256"]
                except (KeyError, ValueError) as exc:
                    raise MatrixError("映像 TSV 大小或雜湊欄位無效") from exc
                require(0 < raw_size <= MAX_RAW and sha256_value(raw_sha), "TSV 解壓大小或雜湊無效")
                require(xz_size == entry["identity"]["st_size"] and
                        xz_sha == entry["expected_compressed_sha256"], "TSV 與檔案大小或旁檔雜湊不符")
                entry["expected_raw"] = {"bytes": raw_size, "sha256": raw_sha}
    return references


def inventory(root, profile, variants, *, source_commit=None, source_record=None):
    require(profile in PROFILES, "只接受明確的普通 Zero 或 EMAC 板型；不接受 Berry")
    require(len(variants) == 2 and len(set(variants)) == 2 and
            all(item in VARIANTS for item in variants), "必須明確指定兩個不同角色，不自動改名")
    entries, issues = [], []
    with safe.open_root(root) as directory:
        with os.scandir(directory) as listing:
            names = sorted(item.name for item in listing if item.name.endswith(".img.xz"))
        require(len(names) <= 32, "單批候選超過 32 個；請縮小指定目錄")
        for name in names:
            try:
                entry = parse_name(name)
                require(entry["profile"] == profile, f"板型不符：{name}")
                entry["identity"] = file_identity(directory, name)
                require(0 < entry["identity"]["st_size"] <= MAX_RAW, f"壓縮檔大小無效：{name}")
                entries.append(entry)
            except (MatrixError, OSError) as exc:
                issues.append({"path": name, "error": str(exc) if isinstance(exc, MatrixError) else "不是可安全讀取的一般檔案"})
        combinations = {}
        for entry in entries:
            combinations.setdefault((entry["release"], entry["variant"]), []).append(entry["path"])
        expected = {(release, variant) for release in RELEASES for variant in variants}
        missing = sorted(expected - set(combinations))
        unexpected = sorted(set(combinations) - expected)
        duplicates = [{"release": key[0], "variant": key[1], "paths": paths}
                      for key, paths in sorted(combinations.items()) if len(paths) > 1]
        if len(entries) != 10 or missing or unexpected or duplicates:
            issues.append({"error": "不是五個 OS 各兩個唯一角色", "missing": missing,
                           "unexpected": unexpected, "duplicates": duplicates})
        references = []
        try:
            references = metadata(directory, entries, source_commit, source_record)
        except (MatrixError, OSError) as exc:
            issues.append({"error": str(exc) if isinstance(exc, MatrixError) else "缺少或無法安全讀取發布中繼資料"})
    return {"schema": SCHEMA, "action": "inventory", "ok": not issues,
            "root": str(Path(root).absolute()), "profile": profile, "variants": list(variants),
            "entries": entries, "references": references, "issues": issues,
            "large_files_read": False, "matrix_selected_by_operator": False, **LIMITS}


def check_mbr(header, raw_bytes, capacity_bytes):
    require(type(capacity_bytes) is int and capacity_bytes > 0, "容量必須是實際核對的正整數位元組")
    require(raw_bytes >= 512 and raw_bytes % 512 == 0, "映像長度不是完整的 512 位元組磁區")
    require(len(header) == 512 and header[510:512] == b"\x55\xaa", "MBR 簽章無效")
    require(raw_bytes <= capacity_bytes, "解壓映像超過指定容量")
    partitions = []
    for index in range(4):
        block = header[446 + index * 16:462 + index * 16]
        boot, _, kind, _, start, sectors = struct.unpack("<B3sB3sII", block)
        if block == bytes(16):
            continue
        require(boot in (0, 128) and kind != 0 and start > 0 and sectors > 0, "MBR 主分割項目無效")
        require(kind not in (0x05, 0x0F, 0x85, 0xEE), "不支援延伸分割或 GPT；不得算作 MBR 初檢通過")
        end = start + sectors
        require(end * 512 <= raw_bytes, "MBR 分割區超過映像尾端")
        partitions.append({"index": index + 1, "type": kind, "bootable": boot == 128,
                           "start_lba": start, "sectors": sectors, "end_lba_exclusive": end})
    require(partitions, "MBR 沒有主分割區")
    ordered = sorted(partitions, key=lambda part: part["start_lba"])
    require(all(left["end_lba_exclusive"] <= right["start_lba"]
                for left, right in zip(ordered, ordered[1:])), "MBR 主分割區重疊")
    require(sum(part["bootable"] for part in partitions) <= 1, "MBR 有多個啟動旗標")
    return {"sector_bytes": 512, "partitions": partitions, "capacity_bytes": capacity_bytes,
            "image_fits": True, "preliminary_only": True}


def stream_verify(directory, entry, capacity_bytes, max_raw_bytes=MAX_RAW):
    require(type(max_raw_bytes) is int and 512 <= max_raw_bytes <= MAX_RAW, "解壓上限無效")
    compressed_hash, raw_hash = hashlib.sha256(), hashlib.sha256()
    compressed_bytes = raw_bytes = stream_count = padding = 0
    header, checks, decoder = bytearray(), set(), None
    with safe.open_file(directory, entry["path"]) as stream:
        before = identity(os.fstat(stream.fileno()))
        require(before == entry["identity"], "原檔身分已變動，請重新盤點")
        try:
            while chunk := stream.read(CHUNK):
                compressed_bytes += len(chunk)
                require(compressed_bytes <= before["st_size"], "讀取期間壓縮檔增長")
                compressed_hash.update(chunk)
                pending = chunk
                while pending or (decoder is not None and not decoder.needs_input):
                    if decoder is None:
                        if stream_count:
                            rest = pending.lstrip(b"\0")
                            padding += len(pending) - len(rest)
                            pending = rest
                            if not pending:
                                break
                        require(padding % 4 == 0, "XZ 填補長度必須是四的倍數")
                        padding = 0
                        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MEMLIMIT)
                    raw = decoder.decompress(pending, max_length=CHUNK)
                    pending = b""
                    raw_bytes += len(raw)
                    require(raw_bytes <= max_raw_bytes, "解壓串流超過安全上限")
                    raw_hash.update(raw)
                    header.extend(raw[:max(0, 512 - len(header))])
                    if decoder.eof:
                        require(decoder.check != lzma.CHECK_NONE and lzma.is_check_supported(decoder.check),
                                "XZ 缺少可驗證的完整性檢查碼")
                        checks.add(decoder.check)
                        pending = decoder.unused_data
                        decoder = None
                        stream_count += 1
        except lzma.LZMAError as exc:
            raise MatrixError("XZ 損壞、格式無效或解碼記憶體超限") from exc
        require(stream_count > 0 and decoder is None and padding % 4 == 0, "XZ 截斷或尾部填補無效")
        require(before == identity(os.fstat(stream.fileno())) and compressed_bytes == before["st_size"],
                "校驗期間原檔有變動")
    require(file_identity(directory, entry["path"]) == before, "校驗期間原檔路徑已替換")
    compressed = {"bytes": compressed_bytes, "sha256": compressed_hash.hexdigest()}
    raw = {"bytes": raw_bytes, "sha256": raw_hash.hexdigest()}
    require(compressed["sha256"] == entry["expected_compressed_sha256"], "壓縮 SHA-256 與發布旁檔不符")
    require(entry["expected_raw"] is None or raw == entry["expected_raw"], "解壓大小或 SHA-256 與發布清單不符")
    return {"ok": True, "compressed": compressed, "raw": raw, "xz_streams": stream_count,
            "xz_checks": sorted(checks), "mbr": check_mbr(bytes(header), raw_bytes, capacity_bytes)}


def new_directory(parent_fd, prefix):
    name = prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return name


def save_json(directory, name, data):
    blob = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    require(len(blob) <= safe.MAX_MANIFEST_BYTES, "證據 JSON 超過大小上限")
    temporary = "partial-" + uuid.uuid4().hex
    with safe.open_file(directory, temporary, create=True) as stream:
        stream.write(blob)
    # 先同步完整內容再以不覆寫的連結發布；中斷的 partial 不作續作證據。
    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
    os.fsync(directory)
    os.unlink(temporary, dir_fd=directory)
    os.fsync(directory)
    return hashlib.sha256(blob).hexdigest()


def write_inventory(report, evidence_parent):
    root, parent = Path(report["root"]), Path(evidence_parent).absolute()
    require(not parent.is_relative_to(root), "證據目錄不得位於原產物目錄內")
    with safe.open_root(parent) as directory:
        name = new_directory(directory, "evidence")
        with safe.open_root(parent / name) as output:
            digest = save_json(output, "inventory.json", report)
    return {"ok": report["ok"], "evidence": str(parent / name), "inventory_sha256": digest,
            "entries": len(report["entries"]), "issues": report["issues"], **LIMITS}


def validate_inventory(data):
    require(isinstance(data, dict) and type(data.get("schema")) is int and data["schema"] == SCHEMA and data.get("ok") is True and
            data.get("action") == "inventory", "盤點尚未通過或版本無效")
    require(data.get("profile") in PROFILES and isinstance(data.get("root"), str) and
            Path(data["root"]).is_absolute(), "盤點板型或來源目錄無效")
    variants, entries = data.get("variants"), data.get("entries")
    require(isinstance(variants, list) and len(variants) == 2 and
            all(isinstance(item, str) and item in VARIANTS for item in variants) and len(set(variants)) == 2,
            "盤點角色無效")
    require(isinstance(entries, list) and len(entries) == 10, "盤點必須正好十套")
    combinations, names = set(), set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("path"), str), "盤點項目無效")
        parsed = parse_name(entry["path"])
        require(all(entry.get(key) == value for key, value in parsed.items()) and
                parsed["profile"] == data["profile"], "盤點板型或檔名欄位不符")
        info = entry.get("identity")
        require(isinstance(info, dict) and set(info) == set(IDENTITY_FIELDS) and
                all(type(value) is int and value >= 0 for value in info.values()) and
                0 < info["st_size"] <= MAX_RAW, "盤點原檔身分無效")
        require(sha256_value(entry.get("expected_compressed_sha256")), "盤點壓縮雜湊無效")
        raw = entry.get("expected_raw")
        require("expected_raw" in entry and (raw is None or
                (isinstance(raw, dict) and set(raw) == {"bytes", "sha256"} and
                 type(raw["bytes"]) is int and 0 < raw["bytes"] <= MAX_RAW and sha256_value(raw["sha256"]))),
                "盤點解壓雜湊或大小無效")
        names.add(entry["path"])
        combinations.add((entry["release"], entry["variant"]))
    require(len(names) == 10 and combinations == {(release, variant) for release in RELEASES for variant in variants},
            "盤點不是五個 OS 各兩個唯一角色")


def tool_fingerprint():
    result = {}
    for filename in (__file__, safe.__file__):
        path = Path(filename).absolute()
        with safe.open_root(path.parent) as directory:
            result[path.name] = small_file(directory, path.name)[0]["sha256"]
    return result


def valid_success(result, entry, capacity, max_raw):
    try:
        require(isinstance(result, dict) and result.get("ok") is True, "不是成功紀錄")
        compressed, raw, mbr = result["compressed"], result["raw"], result["mbr"]
        require(compressed == {"bytes": entry["identity"]["st_size"],
                               "sha256": entry["expected_compressed_sha256"]}, "續作壓縮雜湊不符")
        require(type(raw["bytes"]) is int and 512 <= raw["bytes"] <= min(capacity, max_raw) and
                raw["bytes"] % 512 == 0 and sha256_value(raw["sha256"]) and
                (entry["expected_raw"] is None or raw == entry["expected_raw"]), "續作解壓證據無效")
        require(type(result["xz_streams"]) is int and result["xz_streams"] > 0 and result["xz_checks"] and
                all(type(check) is int and check in (lzma.CHECK_CRC32, lzma.CHECK_CRC64, lzma.CHECK_SHA256)
                    for check in result["xz_checks"]), "續作 XZ 證據無效")
        require(mbr["capacity_bytes"] == capacity and mbr["image_fits"] is True and mbr["preliminary_only"] is True
                and mbr["sector_bytes"] == 512 and 1 <= len(mbr["partitions"]) <= 4, "續作 MBR 證據無效")
        header = bytearray(512)
        header[510:512] = b"\x55\xaa"
        for part in mbr["partitions"]:
            require(type(part["index"]) is int and 1 <= part["index"] <= 4 and
                    type(part["bootable"]) is bool, "續作 MBR 項目無效")
            struct.pack_into("<B3sB3sII", header, 446 + (part["index"] - 1) * 16,
                             128 if part["bootable"] else 0, bytes(3), part["type"], bytes(3),
                             part["start_lba"], part["sectors"])
        require(check_mbr(bytes(header), raw["bytes"], capacity) == mbr, "續作 MBR 不一致")
        return True
    except (MatrixError, KeyError, TypeError, ValueError, struct.error, OverflowError):
        return False


def previous_success(evidence_fd, contract, key, entry, capacity, max_raw):
    with os.scandir(evidence_fd) as listing:
        names = sorted(item.name for item in listing if item.name.startswith("run-"))
    require(len(names) <= 1024, "續作批次超過上限，請另建盤點")
    for name in reversed(names):
        try:
            reference = name + "/" + key + ".json"
            digest, blob = small_file(evidence_fd, reference)
            record = safe.parse_manifest(blob)
            payload = record["payload"]
            require(isinstance(payload, dict) and set(payload) ==
                    {"contract", "path", "identity", "result", "completed_utc", "resumed_from"},
                    "續作紀錄欄位不完整")
            require(isinstance(payload["completed_utc"], str) and
                    datetime.fromisoformat(payload["completed_utc"]).utcoffset() is not None,
                    "續作紀錄缺少含時區的完成時間")
            if (record["sha256"] == digest_json(payload) and payload["contract"] == contract and
                    payload["path"] == entry["path"] and payload["identity"] == entry["identity"] and
                    valid_success(payload["result"], entry, capacity, max_raw)):
                return {"record": reference, "sha256": digest["sha256"], "result": payload["result"]}
        except (MatrixError, OSError, KeyError, TypeError, ValueError):
            continue
    return None


@contextmanager
def locked_evidence(path):
    with safe.open_root(path) as directory:
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MatrixError("另一個程序正在使用此證據目錄") from exc
        try:
            yield directory
        finally:
            fcntl.flock(directory, fcntl.LOCK_UN)


def verify(evidence, inventory_sha256, capacity_bytes, *, max_raw_bytes=MAX_RAW):
    require(sha256_value(inventory_sha256), "必須提供確認過的盤點 SHA-256")
    require(type(capacity_bytes) is int and capacity_bytes >= 512, "容量必須是實際核對的位元組，不可用約略 GiB")
    require(type(max_raw_bytes) is int and 512 <= max_raw_bytes <= MAX_RAW, "解壓上限無效")
    with locked_evidence(evidence) as output:
        digest, blob = small_file(output, "inventory.json")
        require(digest["sha256"] == inventory_sha256, "盤點 SHA-256 不符")
        data = safe.parse_manifest(blob)
        validate_inventory(data)
        require(not Path(evidence).absolute().is_relative_to(Path(data["root"])), "證據不得位於原產物目錄內")
        contract = {"schema": SCHEMA, "inventory_sha256": inventory_sha256,
                    "tools": tool_fingerprint(), "capacity_bytes": capacity_bytes,
                    "max_raw_bytes": max_raw_bytes, "xz_memlimit": MEMLIMIT}
        results = []
        with safe.open_root(data["root"]) as source:
            run_name = new_directory(output, "run")
            with safe.open_root(Path(evidence) / run_name) as run:
                save_json(run, "contract.json", contract)
                for index, entry in enumerate(data["entries"]):
                    key = f"item-{index:02d}"
                    resumed = None
                    try:
                        require(file_identity(source, entry["path"]) == entry["identity"], "原檔身分已變動，請重新盤點")
                        resumed = previous_success(output, contract, key, entry, capacity_bytes, max_raw_bytes)
                        result = resumed["result"] if resumed else stream_verify(source, entry, capacity_bytes, max_raw_bytes)
                    except MatrixError as exc:
                        result = {"ok": False, "error": str(exc)}
                    except OSError as exc:
                        result = {"ok": False, "error": "本機唯讀檔案操作失敗", "errno": exc.errno}
                    payload = {"contract": contract, "path": entry["path"], "identity": entry["identity"],
                               "result": result, "completed_utc": datetime.now(timezone.utc).isoformat(),
                               "resumed_from": None if resumed is None else {k: resumed[k] for k in ("record", "sha256")}}
                    save_json(run, key + ".json", {"payload": payload, "sha256": digest_json(payload)})
                    results.append({"path": entry["path"], "resumed": resumed is not None, **result})
                    print(json.dumps({"path": entry["path"], "status": "通過" if result["ok"] else "失敗",
                                      "resumed": resumed is not None}, ensure_ascii=False), file=sys.stderr, flush=True)
                report = {"ok": all(item["ok"] for item in results), "action": "verify", "run": run_name,
                          "results": results, "contract": contract, "matrix_selected_by_operator": True,
                          "resume_limits": "僅信任受控本機證據與 dev/ino/size/mtime/ctime；未重新讀取已成功原檔，不能偵測繞過檔案系統的外部修改或惡意偽造證據",
                          **LIMITS}
                save_json(run, "summary.json", report)
    return report


def main(argv=None):
    parser = safe.JsonArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True, help="操作")
    scan = subs.add_parser("inventory", help="只讀檔名、大小與小型紀錄；不讀大檔內容")
    scan.add_argument("--root", required=True, help="明確指定單批映像目錄，不遞迴搜尋")
    scan.add_argument("--expected-profile", required=True, choices=PROFILES, help="明確板型")
    scan.add_argument("--variant", required=True, action="append", choices=VARIANTS, help="原始檔名角色；恰好指定兩次")
    scan.add_argument("--evidence-parent", required=True, help="已存在且位於產物目錄外的證據父目錄；只新建子目錄")
    scan.add_argument("--source-commit", help="明確來源紀錄中的完整提交；不指定則不推定")
    scan.add_argument("--source-record", help="原產物目錄內含該提交的安全相對紀錄檔名")
    check = subs.add_parser("verify", help="主代理確認批次後才執行完整串流；自動沿用相同條件的成功紀錄")
    check.add_argument("--evidence", required=True, help="已建立的盤點證據目錄")
    check.add_argument("--inventory-sha256", required=True, help="確認過的盤點 SHA-256")
    check.add_argument("--capacity-bytes", required=True, type=int, help="實際核對的目標容量；不存取任何裝置")
    check.add_argument("--max-raw-bytes", default=MAX_RAW, type=int, help="串流解壓安全上限")
    try:
        args = parser.parse_args(argv)
        if args.action == "inventory":
            report = write_inventory(inventory(args.root, args.expected_profile, args.variant,
                                               source_commit=args.source_commit, source_record=args.source_record),
                                     args.evidence_parent)
        else:
            report = verify(args.evidence, args.inventory_sha256, args.capacity_bytes, max_raw_bytes=args.max_raw_bytes)
    except MatrixError as exc:
        report = {"ok": False, "error": str(exc), **LIMITS}
    except OSError as exc:
        report = {"ok": False, "error": "本機檔案操作失敗", "errno": exc.errno, **LIMITS}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
