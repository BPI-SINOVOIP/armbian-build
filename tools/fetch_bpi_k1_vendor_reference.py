#!/usr/bin/env python3
"""取得並核對固定官方 ZIP 的 13 個小型參考元件，不下載或解開 rootfs。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import urllib.request
import zlib


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "config/spacemit-k1-vendor/sources.lock.json"
SOURCE_URL = "https://archive.spacemit.com/image/k1/version/bianbu/v2.3/Bianbu-LXQt-K1-V2.3.0-20251212104943.zip"
MEMBERS = frozenset((
    "env.bin", "factory/bootinfo_emmc.bin", "factory/FSBL.bin",
    "factory/bootinfo_spinor.bin", "factory/bootinfo_spinand.bin",
    "factory/bootinfo_sd.bin", "fastboot.yaml", "fw_dynamic.itb",
    "genimage.cfg", "partition_2M.json", "partition_flash.json",
    "partition_universal.json", "u-boot.itb",
))
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_RANGE_BYTES = 8 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_lock(path):
    data = Path(path).read_bytes()
    lock = json.loads(data)
    require(lock.get("url") == SOURCE_URL, "來源鎖不是指定官方 ZIP 網址")
    total = lock.get("archive_bytes")
    require(type(total) is int and 30 < total <= 4 * 1024**3, "來源 ZIP 大小不合法")
    records = lock.get("files")
    require(isinstance(records, list) and len(records) == len(MEMBERS), "來源鎖必須恰有 13 個指定元件")
    require(all(isinstance(item, dict) for item in records), "來源鎖元件格式不符")
    require({item.get("path") for item in records} == MEMBERS, "來源鎖元件名稱重複、缺少或不在白名單")
    offsets = set()
    for item in records:
        for key in ("size", "compressed_size", "header_offset"):
            require(type(item.get(key)) is int, "來源鎖大小或偏移不是整數")
        require(0 < item["size"] <= MAX_MEMBER_BYTES, "元件解壓大小超出上限")
        require(0 < item["compressed_size"] <= MAX_RANGE_BYTES, "元件壓縮大小超出上限")
        require(0 <= item["header_offset"] <= total - 30, "元件標頭偏移超出 ZIP")
        require(item["header_offset"] not in offsets, "元件標頭偏移重複")
        offsets.add(item["header_offset"])
        require(re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is not None,
                "元件 SHA-256 格式不符")
        require(re.fullmatch(r"[0-9a-f]{8}", str(item.get("crc32", ""))) is not None,
                "元件 CRC32 格式不符")
    return lock, hashlib.sha256(data).hexdigest()


class LocalReader:
    def __init__(self, path, total):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode), "來源 ZIP 必須是一般檔案")
            require(info.st_size == total, "來源 ZIP 大小與來源鎖不符")
            self.stream = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        self.total = total
        self.identity = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def read(self, offset, count):
        validate_range(offset, count, self.total)
        self.stream.seek(offset)
        data = self.stream.read(count)
        require(len(data) == count, "來源 ZIP 指定範圍長度不足")
        return data

    def finish(self):
        info = os.fstat(self.stream.fileno())
        require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) == self.identity,
                "來源 ZIP 在核對期間遭到變更")

    def close(self):
        self.stream.close()


def validate_range(offset, count, total):
    require(type(offset) is int and type(count) is int and
            0 <= offset and 0 < count <= MAX_RANGE_BYTES and offset + count <= total,
            "指定讀取範圍不合法或超過上限")


class RangeReader:
    def __init__(self, url, total):
        require(url == SOURCE_URL, "下載網址不是指定官方來源")
        self.url, self.total = url, total

    def read(self, offset, count):
        validate_range(offset, count, self.total)
        end = offset + count - 1
        request = urllib.request.Request(self.url, headers={
            "Range": f"bytes={offset}-{end}", "Accept-Encoding": "identity",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            require(response.status == 206, "伺服器未回覆 HTTP 206，拒絕接收整包")
            require(response.geturl() == self.url, "下載來源發生未授權重新導向")
            expected = f"bytes {offset}-{end}/{self.total}"
            require(response.headers.get("Content-Range") == expected,
                    "HTTP Content-Range 與固定 ZIP 範圍或總大小不符")
            require(response.headers.get("Content-Encoding", "identity") == "identity",
                    "HTTP 回覆不是原始位元組")
            length = response.headers.get("Content-Length")
            require(length is None or length == str(count), "HTTP Content-Length 與指定範圍不符")
            data = response.read(count + 1)
            require(len(data) == count, "HTTP 回覆長度與指定範圍不符")
            return data

    def finish(self):
        pass

    def close(self):
        pass


def member(reader, item):
    start = item["header_offset"]
    header = reader.read(start, 30)
    magic, version, flags, method, _time, _date, crc, compressed, size, name_len, extra_len = struct.unpack("<4s5H3I2H", header)
    require(magic == b"PK\x03\x04", "指定偏移不是 ZIP 本地標頭")
    require(version <= 20, "不支援此 ZIP 版本或 ZIP64 成員")
    require(flags & ~0x080E == 0, "ZIP 成員含加密或不支援的旗標")
    require(method in (0, 8), "ZIP 成員不是 stored 或 deflate")
    require(method != 0 or flags & 6 == 0, "stored 成員帶有 deflate 專用旗標")
    require(0 < name_len <= 512, "ZIP 成員名稱長度超出上限")
    require(start + 30 + name_len + extra_len + item["compressed_size"] <= reader.total,
            "ZIP 成員名稱、附加欄位或壓縮資料超出檔案")
    fields = reader.read(start + 30, name_len + extra_len)
    name = fields[:name_len].decode("utf-8" if flags & 0x800 else "cp437")
    require(name == item["path"], "ZIP 成員名稱與來源鎖不符")
    expected_header = (int(item["crc32"], 16), item["compressed_size"], item["size"])
    actual_header = (crc, compressed, size)
    if flags & 8:
        require(all(actual in (0, expected) for actual, expected in zip(actual_header, expected_header)),
                "ZIP 延後描述成員的標頭數值與來源鎖不符")
    else:
        require(actual_header == expected_header, "ZIP 標頭的 CRC32 或大小與來源鎖不符")
    data_start = start + 30 + name_len + extra_len
    packed = reader.read(data_start, item["compressed_size"])
    if method == 0:
        require(item["compressed_size"] == item["size"], "stored 成員的壓縮與原始大小不同")
        data = packed
    else:
        decoder = zlib.decompressobj(-15)
        data = decoder.decompress(packed, item["size"] + 1)
        require(decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data,
                "deflate 資料不完整、超出固定解壓上限或包含尾隨資料")
    require(len(data) == item["size"], "元件解壓大小與來源鎖不符")
    actual_crc = f"{zlib.crc32(data) & 0xffffffff:08x}"
    actual_sha = hashlib.sha256(data).hexdigest()
    require(actual_crc == item["crc32"], "元件解壓後 CRC32 不符")
    require(actual_sha == item["sha256"], "元件解壓後 SHA-256 不符")
    record = {"path": name, "size": len(data), "sha256": actual_sha, "crc32": actual_crc,
              "header_offset": start, "compressed_size": len(packed)}
    return data, record, (start, data_start + len(packed))


def fetch(lock_path, output, archive=None):
    lock, lock_sha = load_lock(lock_path)
    output = Path(output)
    require(not os.path.lexists(output), "輸出路徑已存在，拒絕覆寫")
    reader = LocalReader(archive, lock["archive_bytes"]) if archive is not None else RangeReader(lock["url"], lock["archive_bytes"])
    owned = None
    try:
        output.mkdir()
        owned = output.stat()
        records, intervals = [], []
        for item in lock["files"]:
            data, record, interval = member(reader, item)
            require(all(interval[1] <= prior[0] or interval[0] >= prior[1] for prior in intervals),
                    "指定 ZIP 成員的資料範圍重疊")
            intervals.append(interval)
            target = output / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            records.append(record)
        reader.finish()
        report = {
            "schema_version": 1, "url": lock["url"], "archive_bytes": lock["archive_bytes"],
            "archive_full_sha256": None, "sources_lock_sha256": lock_sha,
            "retrieval_method": "本機完整 ZIP 的指定範圍" if archive is not None else "HTTP Range",
            "scope": "僅核對 13 個指定成員的本地標頭、範圍、大小、CRC32 與 SHA-256；未核對中央目錄或整包 SHA-256，不收錄 rootfs。",
            "files": records,
        }
        with (output / "retrieval.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return report
    except BaseException:
        if owned is not None:
            try:
                current = output.lstat()
                if stat.S_ISDIR(current.st_mode) and os.path.samestat(current, owned):
                    shutil.rmtree(output)
            except FileNotFoundError:
                pass
        raise
    finally:
        reader.close()


class ChineseParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.exit(2, "參數不完整或無法辨識，請使用 --help 核對。\n")


def main():
    parser = ChineseParser(description=__doc__, add_help=False)
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明並結束")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, metavar="來源鎖", help="預設使用倉庫 config 下的來源鎖")
    parser.add_argument("--archive", type=Path, metavar="完整ZIP", help="改用既有完整官方 ZIP；只讀取指定成員")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄", help="尚不存在且上層已存在的輸出目錄")
    args = parser.parse_args()
    try:
        result = fetch(args.lock, args.output, args.archive)
    except (ValueError, OSError, KeyError, TypeError, zlib.error) as exc:
        print("參考元件取得失敗：" + str(exc), file=sys.stderr)
        return 1
    print(f"已核對 {len(result['files'])} 個參考元件：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
