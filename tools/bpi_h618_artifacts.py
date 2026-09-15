#!/usr/bin/env python3
"""建立與唯讀核對 H618 產物清單；不下載、啟動、燒錄或授權更新。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


PROFILES = ("bananapim4zero", "bananapim4zeroemac", "bananapim4berry")
ROLES = ("image", "kernel", "dtb", "initramfs", "rootfs", "modules",
         "spl1", "spl2", "uboot", "firmware")
MAX_MANIFEST_BYTES = 65536
MAX_ARTIFACT_BYTES = 64 * 1024**3
MAX_ARTIFACTS = 32


class ArtifactError(ValueError):
    """清單、路徑或檔案未滿足離線檢查條件。"""


def require(condition, message):
    if not condition:
        raise ArtifactError(message)


def relative_parts(name):
    require(isinstance(name, str) and 0 < len(name) <= 1024, "相對路徑格式無效")
    parts = name.split("/")
    require(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,191}", p)
                and p not in (".", "..") for p in parts),
            "產物路徑須使用安全的 ASCII 相對名稱，不能越界")
    return parts


@contextmanager
def open_root(root):
    """逐層開啟目錄，包含根目錄的祖先也不得經過符號連結。"""
    path = Path(root).absolute()
    require(".." not in path.parts, "目錄不得含有 ..")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def open_file(root_fd, name, *, create=False):
    parts = relative_parts(name)
    directory = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        if create:
            descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        else:
            # O_PATH 不觸發裝置驅動；確認型別後，透過保留的描述符開啟同一個一般檔案。
            path_fd = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
            try:
                require(stat.S_ISREG(os.fstat(path_fd).st_mode),
                        "只接受一般檔案，不接受裝置、管線或目錄")
                descriptor = os.open(f"/proc/self/fd/{path_fd}", os.O_RDONLY | os.O_NONBLOCK)
            finally:
                os.close(path_fd)
        with os.fdopen(descriptor, "wb" if create else "rb") as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
                    "只接受一般檔案，不接受裝置、管線或目錄")
            yield stream
            if create:
                stream.flush()
                os.fsync(stream.fileno())
                os.fsync(directory)
    finally:
        os.close(directory)


def fingerprint(root_fd, name, *, limit=MAX_ARTIFACT_BYTES, keep=False):
    with open_file(root_fd, name) as stream:
        before = os.fstat(stream.fileno())
        require(0 < before.st_size <= limit, "檔案為空或超過讀取上限")
        digest, count, chunks = hashlib.sha256(), 0, []
        while chunk := stream.read(min(1024**2, limit + 1 - count)):
            count += len(chunk)
            require(count <= limit, "讀取期間超過檔案上限")
            digest.update(chunk)
            if keep:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and count == before.st_size, "校驗期間檔案有變動")
        return {"bytes": count, "sha256": digest.hexdigest()}, b"".join(chunks)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON 欄位重複")
        result[key] = value
    return result


def parse_manifest(blob):
    require(len(blob) <= MAX_MANIFEST_BYTES, "清單超過上限")
    def bounded_integer(raw):
        require(len(raw) <= 20, "JSON 整數超過長度上限")
        return int(raw)

    try:
        data = json.loads(blob.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_int=bounded_integer)
    except ArtifactError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ArtifactError("清單不是有效的 UTF-8 JSON") from exc
    return data


def validate_manifest(data, expected_profile):
    require(expected_profile in PROFILES, "預期板型不在允許清單")
    require(type(data) is dict and set(data) ==
            {"schema", "profile", "source_commit", "artifacts"}, "清單欄位不完整或含未知欄位")
    require(type(data["schema"]) is int and data["schema"] == 1, "不支援的清單版本")
    require(data["profile"] == expected_profile, "清單與預期板型不符")
    require(isinstance(data["source_commit"], str) and
            re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]), "來源提交格式無效")
    entries = data["artifacts"]
    require(type(entries) is list and 1 <= len(entries) <= MAX_ARTIFACTS,
            "產物數量超出允許範圍")
    names, roles = set(), set()
    for entry in entries:
        require(type(entry) is dict and set(entry) == {"role", "path", "bytes", "sha256"},
                "產物欄位不完整或含未知欄位")
        require(isinstance(entry["role"], str) and entry["role"] in ROLES,
                "產物用途不在允許清單")
        relative_parts(entry["path"])
        require(entry["path"] not in names and entry["role"] not in roles,
                "產物路徑或用途重複")
        names.add(entry["path"])
        roles.add(entry["role"])
        require(type(entry["bytes"]) is int and 0 < entry["bytes"] <= MAX_ARTIFACT_BYTES,
                "產物長度無效")
        require(isinstance(entry["sha256"], str) and
                re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), "產物 SHA-256 格式無效")


def result(action, data, manifest_digest):
    return {
        "ok": True, "action": action, "profile": data["profile"],
        "artifact_count": len(data["artifacts"]), "manifest": manifest_digest,
        "scope": "僅確認指定清單及一般檔案的內容一致性",
        "hardware_validated": False, "bootable_verified": False,
        "write_authorized": False, "source_authenticated": False,
        "limits": "提交碼與板型是清單宣告；未解析韌體相容性、壓縮格式或簽章",
    }


def index(root, profile, source_commit, artifacts, manifest):
    relative_parts(manifest)
    require(profile in PROFILES, "板型不在允許清單")
    require(isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit),
            "來源提交格式無效")
    require(1 <= len(artifacts) <= MAX_ARTIFACTS, "產物數量超出允許範圍")
    entries = []
    for item in artifacts:
        role, sep, path = item.partition("=")
        require(sep and role in ROLES, "產物須以允許的用途=相對路徑指定")
        relative_parts(path)
        require(path != manifest, "清單不能同時是受驗產物")
        entries.append({"role": role, "path": path, "bytes": 1, "sha256": "0" * 64})
    data = {"schema": 1, "profile": profile, "source_commit": source_commit, "artifacts": entries}
    validate_manifest(data, profile)
    with open_root(root) as directory:
        for entry in entries:
            actual, _ = fingerprint(directory, entry["path"])
            entry.update(actual)
        blob = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        require(len(blob) <= MAX_MANIFEST_BYTES, "產生的清單超過上限")
        with open_file(directory, manifest, create=True) as stream:
            stream.write(blob)
    return result("index", data, {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()})


def verify(root, manifest, expected_profile, manifest_sha256):
    require(expected_profile in PROFILES, "預期板型不在允許清單")
    require(isinstance(manifest_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", manifest_sha256),
            "必須提供從可信紀錄取得的清單 SHA-256")
    with open_root(root) as directory:
        manifest_digest, blob = fingerprint(directory, manifest, limit=MAX_MANIFEST_BYTES, keep=True)
        require(manifest_digest["sha256"] == manifest_sha256, "清單 SHA-256 與指定紀錄不符")
        data = parse_manifest(blob)
        validate_manifest(data, expected_profile)
        for entry in data["artifacts"]:
            require(entry["path"] != manifest, "清單不能同時是受驗產物")
            actual, _ = fingerprint(directory, entry["path"], limit=entry["bytes"])
            require(actual == {"bytes": entry["bytes"], "sha256": entry["sha256"]},
                    f"產物長度或 SHA-256 不符：{entry['path']}")
    return result("verify", data, manifest_digest)


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, add_help=False, allow_abbrev=False, **kwargs)
        self._positionals.title = "操作"
        self._optionals.title = "參數"
        self.add_argument("--help", action="help", help="顯示說明並結束")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        raise ArtifactError("命令列參數無效；請以 --help 查看允許格式")


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True, help="清單操作")
    create = subs.add_parser("index", help="建立新清單；拒絕覆寫")
    create.add_argument("--profile", required=True, choices=PROFILES, help="宣告適用板型")
    create.add_argument("--source-commit", required=True, help="固定來源提交碼")
    create.add_argument("--artifact", action="append", required=True, help="用途=相對路徑，可重複指定")
    check = subs.add_parser("verify", help="唯讀核對指定清單及產物")
    check.add_argument("--expected-profile", required=True, choices=PROFILES, help="預期板型")
    check.add_argument("--manifest-sha256", required=True, help="可信紀錄中的清單 SHA-256")
    for sub in (create, check):
        sub.add_argument("--root", required=True, help="已存在的產物目錄，不得經過符號連結")
        sub.add_argument("--manifest", default="manifest.json", help="目錄內清單相對路徑")
    action = None
    try:
        args = parser.parse_args(argv)
        action = args.action
        if args.action == "index":
            report = index(args.root, args.profile, args.source_commit, args.artifact, args.manifest)
        else:
            report = verify(args.root, args.manifest, args.expected_profile, args.manifest_sha256)
    except ArtifactError as exc:
        report = {"ok": False, "action": action, "error": str(exc),
                  "hardware_validated": False, "write_authorized": False}
    except OSError as exc:
        report = {"ok": False, "action": action, "error": "本機檔案操作失敗",
                  "errno": exc.errno, "hardware_validated": False, "write_authorized": False}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
