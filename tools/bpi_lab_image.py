#!/usr/bin/env python3
"""唯讀擷取 MBR 單一 Linux 分割映像，供各家族準備原配組件。"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import selectors
import struct
import subprocess
import sys
import time
import uuid

if __package__:
    from . import bpi_h618_artifacts as safe
else:
    import bpi_h618_artifacts as safe

require = safe.require
CHUNK = 1024**2
MAX_FILE = 256 * CHUNK


def digest(blob):
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}


def identity(stream):
    value = os.fstat(stream.fileno())
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def save(directory, name, blob):
    with safe.open_root(directory) as root, safe.open_file(root, name, create=True) as out:
        out.write(blob)


def save_json(directory, name, data):
    save(directory, name, (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def create_directory(path):
    path = Path(path).absolute()
    with safe.open_root(path.parent) as parent:
        os.mkdir(path.name, 0o700, dir_fd=parent)
    return path


def image_path(value):
    require(isinstance(value, str) and value.startswith("/") and len(value) <= 1024,
            "映像內路徑須為絕對路徑")
    require(all(re.fullmatch(r"[A-Za-z0-9_.+-]{1,192}", part) and part not in (".", "..")
                for part in value[1:].split("/")), "映像路徑含跳轉或不支援的字元")
    return value


def mbr_partition(header, maximum):
    require(len(header) == 512 and header[510:512] == b"\x55\xaa", "缺少有效 MBR")
    parts = []
    for index in range(4):
        entry = header[446 + index * 16:462 + index * 16]
        if entry == bytes(16):
            continue
        flag, kind = entry[0], entry[4]
        start, sectors = struct.unpack_from("<II", entry, 8)
        require(flag in (0, 0x80) and kind == 0x83 and start > 0 and sectors > 0,
                "只接受 MBR 單一 Linux 主分割；GPT、延伸或其他格式另行適配")
        require((start + sectors) * 512 <= maximum, "分割範圍超出解壓上限")
        parts.append({"index": index + 1, "type": kind, "start_lba": start, "sectors": sectors})
    require(len(parts) == 1, "必須明確只有一個 Linux 主分割")
    part = parts[0]
    part["partuuid"] = f"{struct.unpack_from('<I', header, 440)[0]:08x}-{part['index']:02x}"
    return part


def bounded_run(argv, *, timeout, stdout_limit, pass_fds=()):
    """限制唯讀外部解析器的輸出與時間；不是針對解析器漏洞的沙箱。"""
    require(timeout > 0, "唯讀解析總期限已到")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, pass_fds=pass_fds,
                               env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    deadline = time.monotonic() + timeout
    data = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            for name in data:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                left = deadline - time.monotonic()
                require(left > 0, "唯讀解析逾時")
                for key, _ in selector.select(min(left, 0.2)):
                    blob = os.read(key.fd, 65536)
                    if not blob:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    limit = stdout_limit if name == "stdout" else 65536
                    require(len(data[name]) + len(blob) <= limit, "唯讀解析輸出超界")
                    data[name].extend(blob)
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return code, bytes(data["stdout"]), bytes(data["stderr"])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


class ImageReader:
    """完整驗證來源後提供 read_file；只寫獨立衍生檔，結束即移除暫存分割。"""

    def __init__(self, source, sha256, output, *, max_raw_bytes=32 * 1024**3, timeout=1800):
        self.source = Path(source).absolute()
        require(re.fullmatch(r"[0-9a-f]{64}", sha256 or ""), "來源 SHA-256 無效")
        require(type(max_raw_bytes) is int and 1024**2 <= max_raw_bytes <= 128 * 1024**3,
                "解壓上限須介於 1 MiB 與 128 GiB")
        require(type(timeout) in (int, float) and 1 <= timeout <= 86400, "期限無效")
        self.expected = sha256
        self.maximum = max_raw_bytes
        self.output = Path(output).absolute()
        self.deadline = time.monotonic() + timeout
        self.records = []
        self.cache = {}
        self.ready = False
        self.partition = None
        self.partition_created = False
        self.stack = ExitStack()
        self.report = {"schema": "bpi-lab-image-v1", "ok": False, "source": str(self.source),
                       "hardware_validated": False, "source_authenticated": False,
                       "mounted": False, "image_code_executed": False}

    def check(self):
        left = self.deadline - time.monotonic()
        require(left > 0, "映像擷取總期限已到")
        return left

    def __enter__(self):
        create_directory(self.output)
        self.output_fd = self.stack.enter_context(safe.open_root(self.output))
        try:
            self._extract()
            self.ready = True
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def _save(self, name, blob):
        with safe.open_file(self.output_fd, name, create=True) as out:
            out.write(blob)

    def _extract(self):
        source_root = self.stack.enter_context(safe.open_root(self.source.parent))
        source = self.stack.enter_context(safe.open_file(source_root, self.source.name))
        before = identity(source)
        require(0 < before[2] <= self.maximum, "來源大小超界")
        compressed_hash = hashlib.sha256()
        count = 0
        while blob := source.read(CHUNK):
            self.check()
            count += len(blob)
            require(count <= self.maximum, "來源讀取超界")
            compressed_hash.update(blob)
        require(identity(source) == before and count == before[2], "來源在摘要核對時改變")
        require(compressed_hash.hexdigest() == self.expected, "完整來源 SHA-256 不符")
        source.seek(0)
        is_xz = source.read(6) == b"\xfd7zXZ\0"
        source.seek(0)
        # LZMAFile 的記憶體需求受 XZ 字典控制，改用帶 memlimit 的逐串流解碼。
        stream = self._xz(source) if is_xz else self._raw(source)
        prefix = bytearray()
        raw_hash = hashlib.sha256()
        part_hash = hashlib.sha256()
        total = written = 0
        part = None
        self.partition = self.output / "partition.tmp"
        output_root = self.output_fd
        with safe.open_file(output_root, self.partition.name, create=True) as out:
            self.partition_created = True
            self.created_identity = identity(out)[:2]
            for blob in stream:
                self.check()
                end = total + len(blob)
                require(end <= self.maximum, "解壓輸出超界")
                raw_hash.update(blob)
                if part is None:
                    prefix.extend(blob[:512 - len(prefix)])
                    if len(prefix) == 512:
                        part = mbr_partition(bytes(prefix), self.maximum)
                        start = part["start_lba"] * 512
                        length = part["sectors"] * 512
                        stat = os.fstatvfs(output_root)
                        require(stat.f_bavail * stat.f_frsize >= length + 64 * CHUNK,
                                "暫存分割可用空間不足")
                if part is not None:
                    left, right = max(total, start), min(end, start + length)
                    if right > left:
                        selected = blob[left - total:right - total]
                        out.write(selected)
                        part_hash.update(selected)
                        written += len(selected)
                total = end
            out.flush()
            self.completed_partition_identity = identity(out)
        require(part is not None and total % 512 == 0 and total >= start + length
                and written == length, "原始映像截斷或分割長度不符")
        require(identity(source) == before, "來源在解壓期間改變")
        self.part_stream = self.stack.enter_context(safe.open_file(output_root, self.partition.name))
        require(identity(self.part_stream) == self.completed_partition_identity,
                "暫存分割在寫入完成後遭置換或變更")
        self.part_identity = identity(self.part_stream)
        self.part_stream.seek(1024)
        superblock = self.part_stream.read(1024)
        require(len(superblock) == 1024 and superblock[56:58] == b"\x53\xef",
                "Linux 分割不是已支援的 ext 檔案系統")
        self.filesystem_uuid = str(uuid.UUID(bytes=superblock[104:120]))
        self.filesystem_label = superblock[120:136].split(b"\0", 1)[0].decode("utf-8")
        self.report.update(source_verified=True, source_kind="xz" if is_xz else "raw",
                           source_digest={"bytes": count, "sha256": self.expected},
                           raw={"bytes": total, "sha256": raw_hash.hexdigest()},
                           partition={**part, "bytes": written, "sha256": part_hash.hexdigest()},
                           filesystem_uuid=self.filesystem_uuid, filesystem_label=self.filesystem_label,
                           filesystem_labels_complete=True, filesystem_label_unique=True)
        self._save("mbr.bin", bytes(prefix))

    def _raw(self, source):
        while blob := source.read(CHUNK):
            yield blob

    def _xz(self, source):
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=256 * CHUNK)
        finished = False
        while chunk := source.read(CHUNK):
            self.check()
            require(not finished, "不接受多串流 XZ 或尾端附加資料")
            data = chunk
            while True:
                self.check()
                blob = decoder.decompress(data, max_length=CHUNK)
                data = b""
                if blob:
                    yield blob
                if decoder.eof:
                    require(not decoder.unused_data, "不接受多串流 XZ 或尾端附加資料")
                    finished = True
                    break
                if decoder.needs_input:
                    break
        require(finished, "XZ 串流截斷")

    def _query(self, operation, path, limit=65536, optional=False):
        require(self.ready and operation in ("stat", "cat"), "只允許就緒後唯讀 stat／cat")
        require(path.startswith("/") or re.fullmatch(r"<[0-9]+>", path), "解析查詢路徑無效")
        if path.startswith("/"):
            image_path(path)
        require(identity(self.part_stream) == self.part_identity, "暫存分割在查詢前變動")
        command = operation + " " + path
        code, stdout, stderr = bounded_run(
            ["/usr/sbin/debugfs", "-R", command, f"/proc/self/fd/{self.part_stream.fileno()}"],
            timeout=min(90, self.check()), stdout_limit=limit, pass_fds=(self.part_stream.fileno(),))
        require(identity(self.part_stream) == self.part_identity, "暫存分割在查詢期間變動")
        number = len(self.records)
        self._save(f"query-{number:04d}.stderr", stderr)
        record = {"command": command, "returncode": code, "stdout": digest(stdout),
                  "stderr": digest(stderr), "stderr_file": f"query-{number:04d}.stderr"}
        self.records.append(record)
        diagnostic = re.sub(rb"\Adebugfs [^\n]*\n", b"", stderr)
        if optional and code == 0 and re.fullmatch(
                rb"[^\n]*: File not found by ext2_lookup\s*", diagnostic):
            raise FileNotFoundError(path)
        require(code == 0 and not diagnostic.strip(), "唯讀 debugfs 查詢失敗：" + command)
        if operation == "stat":
            self._save(f"query-{number:04d}.stat", stdout)
        return stdout

    def _resolve(self, path):
        pending = image_path(path)[1:].split("/")
        resolved = []
        links = []
        while pending:
            self.check()
            item = pending.pop(0)
            if item in ("", "."):
                continue
            if item == "..":
                require(resolved, "映像連結超出根目錄")
                resolved.pop()
                continue
            image_path("/" + item)
            prefix = "/" + "/".join(resolved + [item])
            image_path(prefix)
            description = self._query("stat", prefix, optional=True)
            match = re.search(rb"Inode:\s+(\d+)\s+Type:\s+(\w+)", description)
            require(match is not None, "無法解析映像 inode")
            inode, kind = match[1].decode(), match[2]
            size = re.search(rb"\bSize:\s+(\d+)", description)
            require(size is not None, "映像 inode 缺少長度")
            size = int(size[1])
            if kind == b"symlink":
                require(len(links) < 40, "映像符號連結循環或過深")
                require(0 < size <= 1024, "映像連結長度超界")
                fast = re.search(rb'Fast link dest: "([^"\r\n]*)"', description)
                raw = fast[1] if fast else self._query("cat", "<" + inode + ">", 1024)
                require(len(raw) == size, "映像連結長度不符")
                target = raw.decode("ascii")
                self._symlink_target(prefix, target)
                if target.startswith("/"):
                    resolved = []
                pending = target.split("/") + pending
                require(len("/".join(pending + resolved)) <= 2048, "映像連結解析超界")
                links.append({"path": prefix, "target": target})
                continue
            if pending:
                require(kind == b"directory", "映像路徑中間不是目錄")
                resolved.append(item)
            else:
                require(kind == b"regular" and size <= MAX_FILE, "只讀取有界一般檔案")
                return prefix, inode, size, links
        raise safe.ArtifactError("映像路徑未指向一般檔案")

    def _symlink_target(self, path, target):
        """供多分割讀取器在解析目標前檢查掛載界線。"""

    def read_file(self, path):
        image_path(path)
        self.check()
        if path in self.cache:
            item = self.cache[path]
            with safe.open_file(self.output_fd, item["file"]) as stream:
                before = identity(stream)
                require(before[2] <= MAX_FILE, "已擷取組件超界")
                blob = stream.read(MAX_FILE + 1)
                require(before == identity(stream), "已擷取組件在讀取中改變")
            require(digest(blob) == item["digest"], "已擷取組件被改變")
            return blob
        resolved, inode, size, links = self._resolve(path)
        blob = self._query("cat", "<" + inode + ">", max(size, 1))
        require(len(blob) == size, "映像檔案長度與 inode 不符")
        name = f"file-{len(self.cache):04d}.bin"
        self._save(name, blob)
        self.cache[path] = {"resolved": resolved, "links": links, "file": name, "digest": digest(blob)}
        return blob

    def __exit__(self, exc_type, exc, traceback):
        self.ready = False
        removed, cleanup_error = False, None
        try:
            if self.partition_created:
                value = os.stat(self.partition.name, dir_fd=self.output_fd, follow_symlinks=False)
                require((value.st_dev, value.st_ino) == self.created_identity,
                        "暫存分割名稱已指向其他檔案，拒絕刪除")
                os.unlink(self.partition.name, dir_fd=self.output_fd)
                removed = True
            with safe.open_root(self.output) as current:
                old, new = os.fstat(self.output_fd), os.fstat(current)
                require((old.st_dev, old.st_ino) == (new.st_dev, new.st_ino), "輸出目錄路徑已變動")
        except (OSError, ValueError) as error:
            cleanup_error = error
        try:
            self.report.update(ok=exc_type is None and cleanup_error is None, files=self.cache, queries=self.records,
                               temporary_partition_removed=removed)
            if exc is not None:
                self.report["error"] = str(exc)
            if cleanup_error is not None:
                self.report["cleanup_error"] = str(cleanup_error)
            self._save("extraction.json", (json.dumps(self.report, ensure_ascii=False, indent=2) + "\n").encode())
        finally:
            self.stack.close()
        if cleanup_error is not None and exc_type is None:
            raise cleanup_error


class SnapshotReader:
    """以固定摘要重播已核對擷取；未曾讀過的路徑不是「不存在」。"""

    def __init__(self, source, sha256, output, *, max_raw_bytes=32 * 1024**3, timeout=1800):
        self.source, self.output = Path(source).absolute(), Path(output).absolute()
        require(type(sha256) is str and re.fullmatch(r"[0-9a-f]{64}", sha256), "擷取證據 SHA-256 無效")
        require(type(max_raw_bytes) is int and 1024**2 <= max_raw_bytes <= 128 * 1024**3,
                "原映像大小限制無效")
        require(type(timeout) in (int, float) and 1 <= timeout <= 86400, "重播期限無效")
        self.expected, self.maximum = sha256, max_raw_bytes
        self.deadline = time.monotonic() + timeout
        self.stack, self.cache = ExitStack(), {}

    check = ImageReader.check
    _save = ImageReader._save

    def __enter__(self):
        try:
            root = self.stack.enter_context(safe.open_root(self.source.parent))
            actual, raw = safe.fingerprint(root, self.source.name, limit=65536, keep=True)
            require(actual["sha256"] == self.expected, "擷取證據摘要不符")
            original = safe.parse_manifest(raw)
            require(type(original) is dict and original.get("schema") in ("bpi-lab-image-v1", "bpi-lab-image-replay-v1")
                    and original.get("ok") is True and original.get("source_verified") is True
                    and original.get("hardware_validated") is False, "來源擷取未完整核對，禁止重播")
            require(type(original.get("files")) is dict and len(original["files"]) <= 256
                    and type(original.get("queries")) is list and len(original["queries"]) <= 4096,
                    "擷取證據索引超界或型別錯誤")
            require(type(original.get("raw")) is dict
                    and type(original["raw"].get("bytes")) is int
                    and 0 < original["raw"]["bytes"] <= self.maximum, "原映像大小超界")
            self.original, self.original_fd = original, root
            self.filesystem_uuid = str(uuid.UUID(original["filesystem_uuid"]))
            self.filesystem_label = original.get("filesystem_label")
            missing = []
            for query in original["queries"]:
                self.check()
                command = query.get("command", "")
                ext_query = command.startswith("stat /") and query.get("returncode") == 0
                fat_query = command.startswith("mtype /") and query.get("returncode") == 1
                if not (ext_query or fat_query):
                    continue
                path = image_path(query.get("lookup_path", command[5:] if ext_query else command[6:]))
                metadata, blob = safe.fingerprint(root, query["stderr_file"], limit=65536, keep=True)
                require(metadata == query["stderr"], "擷取查詢診斷已變動")
                diagnostic = re.sub(rb"\Adebugfs [^\n]*\n", b"", blob)
                if ((ext_query and re.fullmatch(rb"[^\n]*: File not found by ext2_lookup\s*", diagnostic))
                        or (fat_query and re.fullmatch(rb'(?:/usr/bin/)?mtype: File "[^"\r\n]+" not found\s*', diagnostic))):
                    missing.append((path, query, blob))
            create_directory(self.output)
            self.output_fd = self.stack.enter_context(safe.open_root(self.output))
            self.report = {key: original[key] for key in (
                "source", "source_digest", "raw", "partition", "filesystem_uuid", "source_verified")}
            for key in ("partitions", "boot_partition", "mounts", "layout_reader", "filesystem_label",
                        "filesystem_label_unique", "filesystem_labels_complete"):
                if key in original:
                    self.report[key] = original[key]
            self.report.update(schema="bpi-lab-image-replay-v1", hardware_validated=False,
                               source_authenticated=False, source_reread=False, mounted=False,
                               image_code_executed=False, queries=[],
                               replay_of={"path": str(self.source), **actual})
            self.missing = set()
            for path, query, blob in missing:
                self.missing.add(path)
                name = f"query-{len(self.report['queries']):04d}.stderr"
                self._save(name, blob)
                self.report["queries"].append({**query, "stderr_file": name})
            return self
        except BaseException as exc:
            self.stack.close()
            if isinstance(exc, (KeyError, TypeError, AttributeError)):
                raise safe.ArtifactError("擷取證據結構不完整或型別錯誤") from exc
            raise

    def read_file(self, path):
        image_path(path)
        self.check()
        record = self.original["files"].get(path)
        if record is None:
            if any(path == prefix or path.startswith(prefix + "/") for prefix in self.missing):
                raise FileNotFoundError(path)
            raise safe.ArtifactError("舊證據未擷取此路徑，不能假定不存在：" + path)
        require(type(record) is dict and isinstance(record.get("file"), str)
                and type(record.get("digest")) is dict, "舊組件索引型別不符")
        with safe.open_file(self.original_fd, record["file"]) as stream:
            before = identity(stream)
            require(before[2] <= MAX_FILE, "舊組件大小超界")
            blob = stream.read(MAX_FILE + 1)
            require(before == identity(stream) and digest(blob) == record["digest"], "舊組件已變動")
        if path not in self.cache:
            name = f"file-{len(self.cache):04d}.bin"
            self._save(name, blob)
            self.cache[path] = {**record, "file": name}
        return blob

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.report.update(ok=exc_type is None, files=self.cache, temporary_partition_created=False)
            if exc is not None:
                self.report["error"] = str(exc)
            self._save("extraction.json", (json.dumps(self.report, ensure_ascii=False, indent=2) + "\n").encode())
        finally:
            self.stack.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="唯讀核對映像並擷取原配組件，不操作實板")
    parser.add_argument("image", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-raw-bytes", type=int, default=32 * 1024**3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--layout", choices=("single-ext", "disk"), default="single-ext",
                        help="單一 MBR ext，或以 fstab 核對完整 MBR／GPT 多分割")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--optional-path", action="append", default=[],
                        help="另查詢可選路徑；只有確定不存在才繼續，解析錯誤仍失敗")
    args = parser.parse_args(argv)
    try:
        require(1 <= len(args.path) + len(args.optional_path) <= 128, "須指定 1..128 個映像內檔案")
        reader_type = ImageReader
        if args.layout == "disk":
            if __package__:
                from .bpi_lab_disk import DiskReader
            else:
                from bpi_lab_disk import DiskReader
            reader_type = DiskReader
        with reader_type(args.image, args.sha256, args.output,
                         max_raw_bytes=args.max_raw_bytes, timeout=args.timeout) as reader:
            for path in args.path:
                reader.read_file(path)
            missing = []
            for path in args.optional_path:
                try:
                    reader.read_file(path)
                except FileNotFoundError:
                    missing.append(path)
            reader.report["optional_missing"] = missing
        print(json.dumps({"ok": True, "evidence": str(reader.output / "extraction.json"),
                          "hardware_validated": False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, lzma.LZMAError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "hardware_validated": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
