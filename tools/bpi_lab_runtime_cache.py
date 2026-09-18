#!/usr/bin/env python3
"""唯讀擷取固定 rootfs 快取的 Python 相依；不執行或部署原系統。"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import stat
import subprocess
import tarfile
import tempfile
import time

if __package__:
    from . import bpi_lab_external_bundle as bundle
else:
    import bpi_lab_external_bundle as bundle

guard, deploy, require = bundle.guard, bundle.deploy, bundle.require
MAX_COMPRESSED = 4 * 1024**3
MAX_TAR = 16 * 1024**3
MAX_MEMBERS = 300000
EXCLUDED_NAMES = ("__pycache__", "sitecustomize.py", "usercustomize.py")
MAX_EXTENDED_HEADER = 1024**2
BUILD_DIRECTORY = r"config-3\.\d+(?:-[A-Za-z0-9_.+-]+)?"


class CacheInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        archive.cache_check()
        archive.cache_members += 1
        require(archive.cache_members <= MAX_MEMBERS, "快取標頭數量超界")
        require(self.type != tarfile.GNUTYPE_SPARSE, "快取不接受 GNU 稀疏格式")
        if self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                         tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            require(0 <= self.size <= MAX_EXTENDED_HEADER, "快取延伸標頭超界")
            archive.cache_extension_depth += 1
            try:
                require(archive.cache_extension_depth <= 8, "快取延伸標頭巢狀過深")
                return super()._proc_member(archive)
            finally:
                archive.cache_extension_depth -= 1
        return super()._proc_member(archive)

    def _reject_sparse(self, *args, **kwargs):
        raise ValueError("快取不接受 PAX 稀疏映射")

    _proc_gnusparse_00 = _reject_sparse
    _proc_gnusparse_01 = _reject_sparse
    _proc_gnusparse_10 = _reject_sparse


class CacheTar(tarfile.TarFile):
    def __init__(self, *args, check=lambda: None, **kwargs):
        self.cache_check, self.cache_members, self.cache_extension_depth = check, 0, 0
        super().__init__(*args, tarinfo=CacheInfo, **kwargs)


def extract(archive, *, architecture, python, stdlib, library_dirs, check=lambda: None):
    """只將選定檔案放進記憶體；tar 內路徑不交給檔案系統解壓。"""
    require(architecture in guard.ELF_ARCH, "架構不支援")
    require(re.fullmatch(r"/usr/lib/python3\.\d+", stdlib or ""), "標準函式庫路徑無效")
    version = stdlib.rsplit("/", 1)[1]
    require(python in ("/usr/bin/python3", "/usr/bin/" + version), "Python 路徑與版本不符")
    require(type(library_dirs) is list and library_dirs and len(set(library_dirs)) == len(library_dirs)
            and all(type(path) is str and re.fullmatch(r"/(?:usr/)?lib(?:/[A-Za-z0-9_.+-]+)?", path)
                    for path in library_dirs), "相依目錄不在受限範圍")
    library_roots = set(path.lstrip("/") for path in library_dirs)
    library_roots.update(path.removeprefix("usr/") if path.startswith("usr/") else "usr/" + path
                         for path in tuple(library_roots))
    prefix = stdlib.lstrip("/")
    allowed_dirs = {"usr", "usr/bin", "usr/lib", "lib", *library_roots}

    def excluded(name):
        return (bool(set(EXCLUDED_NAMES).intersection(PurePosixPath(name).parts))
                or (name.startswith(prefix + "/")
                    and bool(re.fullmatch(BUILD_DIRECTORY, name[len(prefix) + 1:].split("/", 1)[0]))))

    def allowed(name):
        return (not excluded(name)
                and (name in allowed_dirs or name in (python.lstrip("/"), "usr/bin/" + version)
                or name == prefix or name.startswith(prefix + "/")
                or str(PurePosixPath(name).parent) in library_roots))

    index = {}
    for count, member in enumerate(archive, 1):
        check()
        require(count <= MAX_MEMBERS, "快取項目數量超界")
        if member.name in (".", "./") and member.isdir():
            continue
        name = guard._name(member.name.rstrip("/") if member.isdir() else member.name)
        require(name not in index, "快取路徑重複")
        require(0 <= member.size <= MAX_TAR, "快取成員長度超界")
        index[name] = member
    entries, copied = {}, 0

    def load(name):
        nonlocal copied
        require(allowed(name), "相依連結超出核定執行環境範圍")
        if name in entries:
            return entries[name]
        require(name in index, "快取缺少相依路徑：" + name)
        item = index[name]
        require(not item.sparse and 0 <= item.mode <= 0o7777 and not item.mode & 0o6000,
                "執行環境不接受稀疏檔或特殊權限")
        require(item.isdir() or item.isfile() or item.issym(), "執行環境含未知節點或硬連結")
        require(all(type(value) is int and 0 <= value <= 0xffffffff
                    for value in (item.uid, item.gid, int(item.mtime))), "快取中繼資料超界")
        check()
        if item.isfile():
            require(copied + item.size <= guard.MAX_ARCHIVE, "執行環境資料超界")
            with archive.extractfile(item) as source:
                data = source.read(item.size + 1)
            require(len(data) == item.size, "快取一般檔案截斷")
            copied += len(data)
            mode = stat.S_IFREG
        elif item.issym():
            require(0 < len(item.linkname) <= 4096 and "\0" not in item.linkname, "快取符號連結無效")
            data, mode = item.linkname.encode(), stat.S_IFLNK
        else:
            data, mode = b"", stat.S_IFDIR
        result = {**guard.entry(data, mode | item.mode), "uid": item.uid, "gid": item.gid, "mtime": int(item.mtime)}
        entries[name] = result
        return result

    def resolve(path, *, within=None):
        parts, resolved, traversals = list(PurePosixPath("/" + path.lstrip("/")).parts[1:]), [], 0
        parents = {str(item) for item in PurePosixPath(within).parents} if within else set()
        while parts:
            part = parts.pop(0)
            require(part not in ("", ".", ".."), "快取相依路徑無效")
            name = "/".join([*resolved, part])
            require(within is None or name in parents or name == within or name.startswith(within + "/"),
                    "標準函式庫中間連結越出封裝選入範圍")
            item = load(name)
            if stat.S_ISLNK(item["mode"]):
                traversals += 1
                require(traversals <= 40, "快取相依連結循環")
                target = item["data"].decode()
                require(".." not in target.split("/"), "快取相依連結含未支援的上層解析")
                absolute = target if target.startswith("/") else "/" + "/".join([*resolved, target])
                parts, resolved = list(PurePosixPath(absolute).parts[1:]) + parts, []
            else:
                require(not parts or stat.S_ISDIR(item["mode"]), "快取相依穿越非目錄")
                resolved.append(part)
        return "/".join(resolved), item

    name, executable = resolve(python)
    require(name == "usr/bin/" + version and executable["mode"] & 0o111, "Python 不是指定可執行版本")
    for directory in [stdlib, *library_dirs]:
        require(stat.S_ISDIR(resolve(directory)[1]["mode"]), "執行環境目錄缺少")
    for name in sorted(index):
        if name.startswith(prefix + "/") and not excluded(name):
            require(resolve(name, within=prefix)[0].startswith(prefix + "/"), "標準函式庫連結越出封裝選入範圍")
    pending = [name for name, item in entries.items() if item["data"].startswith(b"\x7fELF")]
    seen = set()
    while pending:
        check()
        name, item = resolve(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        info = guard.elf_info(item["data"], architecture)
        if info["interpreter"]:
            pending.append(resolve(info["interpreter"])[0])
        for needed in info["needed"]:
            matches = set()
            for directory in library_dirs:
                directory_name, _ = resolve(directory)
                candidate = directory_name + "/" + needed
                if candidate in index:
                    matches.add(resolve(candidate)[0])
            require(len(matches) == 1, "快取相依缺少或有多個來源：" + needed)
            pending.append(matches.pop())
    blob = guard.archive(entries)
    require(guard.parse_archive(blob)[0] == entries, "執行環境不能完整重播")
    return blob, [{"path": "/" + name, "sha256": hashlib.sha256(entries[name]["data"]).hexdigest()}
                  for name in sorted(seen)]


def decompress(source, output, deadline):
    """有界解壓至匿名暫存檔；任何失敗均回收程序，不建立快取內路徑。"""
    total = 0
    with subprocess.Popen(["/usr/bin/zstd", "-d", "-c", "--quiet"], stdin=source,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            while True:
                ready, _, _ = select.select([process.stdout], [], [], guard.linux.remaining(deadline))
                require(ready, "快取解壓逾時")
                data = os.read(process.stdout.fileno(), 1024**2)
                if not data:
                    break
                total += len(data)
                require(total <= MAX_TAR, "快取解壓長度超界")
                output.write(data)
            require(process.wait(timeout=guard.linux.remaining(deadline)) == 0, "快取壓縮串流損壞或截斷")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    require(total >= 1024, "快取解壓內容為空")
    output.seek(0)
    return total


def build(reference, output, *, architecture, python, stdlib, library_dirs, timeout=600):
    require(type(timeout) in (int, float) and 0 < timeout <= 3600, "快取擷取期限無效")
    deploy.fields(reference, "path sha256")
    require(deploy.core.hash_value(reference["sha256"]), "快取摘要格式錯誤")
    location = deploy.path(reference["path"])
    deadline = time.monotonic() + timeout
    def check():
        return guard.linux.remaining(deadline)
    with deploy.safe.open_root(location.parent) as root, deploy.safe.open_file(root, location.name) as source:
        before = deploy.backup.file_identity(os.fstat(source.fileno()))
        digest = deploy.core.hash_stream(source, MAX_COMPRESSED, check)
        require(digest["sha256"] == reference["sha256"] and digest["bytes"] == before["st_size"], "快取摘要不符")
        source.seek(0)
        with tempfile.TemporaryFile() as temporary:
            tar_bytes = decompress(source, temporary, deadline)
            with CacheTar.open(fileobj=temporary, mode="r:", check=check) as archive:
                blob, dependencies = extract(archive, architecture=architecture, python=python,
                                             stdlib=stdlib, library_dirs=library_dirs, check=check)
        deploy.core.unchanged(root, location.name, source, before)
    check()
    output = deploy.new_directory(output)
    deploy.save(output, "runtime-source.cpio", blob)
    result = {"schema": "bpi-lab-runtime-cache-v1", "hardware_validated": False, "runtime_executed": False,
              "bootable_rescue": False, "source": reference, "source_identity": before,
              "architecture": architecture, "python": python, "stdlib": stdlib, "library_dirs": library_dirs,
              "excluded_names": list(EXCLUDED_NAMES),
              "excluded_stdlib_directory_pattern": BUILD_DIRECTORY,
              "tar_bytes": tar_bytes, "archive_bytes": len(blob), "dependencies": dependencies,
              "archive": {"path": str(output / "runtime-source.cpio"), "sha256": hashlib.sha256(blob).hexdigest()},
              "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    deploy.save(output, "cache-extraction.json", guard.linux.encoded(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--cache-sha256", required=True)
    parser.add_argument("--architecture", choices=sorted(guard.ELF_ARCH), required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--stdlib", required=True)
    parser.add_argument("--library-dir", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build({"path": str(args.cache.absolute()), "sha256": args.cache_sha256}, args.output.absolute(),
                       architecture=args.architecture, python=args.python, stdlib=args.stdlib,
                       library_dirs=args.library_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "hardware_validated": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
