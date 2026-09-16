#!/usr/bin/env python3
"""跨板唯讀盤點；旁檔僅為 SHA 宣告，不代表實際映像雜湊核對。

scan 不開啟映像內容、不解壓、不執行 shell，也不建立磁碟快取。
image_id 識別相對路徑、完整檔案身分與旁檔宣告的快照，不是內容雜湊。
設定僅解析字面宣告與限定目錄內的靜態 include，不模擬 shell 執行結果。
verify_entry 才完整讀取所選一般檔案；成功仍不代表來源可信或硬體通過。
所有 API 僅回傳資料；CLI、持久化、資格審核由呼叫端負責。
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat


SCHEMA = "bpi-lab-catalog-v1"
IDENTITY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
ARCHITECTURES = ("arm32", "arm64", "riscv64", "unknown")
ARCH_MAP = {"armhf": "arm32", "arm": "arm32", "arm32": "arm32",
            "arm64": "arm64", "aarch64": "arm64", "riscv64": "riscv64"}
RELEASES = {"bookworm", "trixie", "jammy", "noble", "resolute"}
VARIANTS = {"minimal", "xfce_desktop"}
BRANCHES = {"legacy", "current", "edge", "vendor"}
CONFIG_SUFFIXES = {".conf", ".csc", ".wip", ".eos", ".tvb"}
MAX_CONFIG_BYTES = 256 * 1024
MAX_SHA_BYTES = 16 * 1024
MAX_INCLUDE_FILES = 64
MAX_DEPTH = 32

# 由指定月份的實際目錄與檔名 profile 建立，不以板名相似度推測。
BOARD_PROFILES = {
    "bpi-6204": "bananapi6204", "bpi-ai2n": "bpi-ai2n",
    "bpi-aim7": "bananapiaim7", "bpi-cm4io": "bananapicm4io",
    "bpi-cm5pro": "bananapicm5pro", "bpi-cm6": "bananapicm6",
    "bpi-f2p": "bananapif2p", "bpi-f2s": "bananapif2s", "bpi-f3": "bananapif3",
    "bpi-forge1": "bananapiforge1", "bpi-m1": "bananapi",
    "bpi-m1p": "bananapim1plus", "bpi-m1super": "bananapim1super",
    "bpi-m2": "bananapim2", "bpi-m2b": "bananapim2berry",
    "bpi-m2m": "bananapim2magic", "bpi-m2p": "bananapim2plus",
    "bpi-m2pro": "bananapim2pro", "bpi-m2s": "bananapim2s",
    "bpi-m2u": "bananapim2ultra", "bpi-m2z": "bananapim2zero",
    "bpi-m3": "bananapim3", "bpi-m4": "bananapim4",
    "bpi-m4b": "bananapim4berry", "bpi-m4z": "bananapim4zero",
    "bpi-m4z-emac": "bananapim4zeroemac", "bpi-m5": "bananapim5",
    "bpi-m5pro": "bananapim5pro", "bpi-m6": "bananapim6",
    "bpi-m64": "bananapim64", "bpi-m7": "bananapim7",
    "bpi-p2pro": "bananapip2pro", "bpi-p2z": "bananapip2zero",
    "bpi-pro": "bananapipro", "bpi-r2": "bananapir2",
    "bpi-r2pro": "bananapir2pro", "bpi-r3": "bananapir3",
    "bpi-r3mini": "bananapir3mini", "bpi-r4": "bananapir4",
    "bpi-r4lite": "bananapir4lite", "bpi-r4pro": "bananapir4pro",
    "bpi-r64": "bananapir64", "bpi-sm10": "bananapism10",
    "bpi-w2": "bananapiw2", "bpi-w3": "bananapiw3",
}


class CatalogError(ValueError):
    """保留可機讀問題代碼，不將盤點失敗當成已通過。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise CatalogError(code, message)


def _issue(code, path, message):
    return {"code": code, "path": str(path), "message": message}


def _failure(exc, path):
    if isinstance(exc, CatalogError):
        return _issue(exc.code, path, str(exc))
    result = _issue("io_error", path, "無法安全讀取本機檔案或目錄")
    result["errno"] = exc.errno
    return result


def _identity(info):
    return {field: getattr(info, field) for field in IDENTITY_FIELDS}


def _parts(name):
    _require(isinstance(name, str) and 0 < len(name) <= 4096,
             "unsafe_path", "相對路徑格式無效")
    parts = name.split("/")
    _require(all(re.fullmatch(r"[A-Za-z0-9._+-]{1,255}", part)
                 and part not in (".", "..") for part in parts),
             "unsafe_path", "路徑不得越界、含空白或特殊控制字元")
    return parts


@contextmanager
def _root(path):
    path = Path(path).absolute()
    _require(".." not in path.parts, "unsafe_path", "根目錄不得含有 ..")
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
def _parent(root_fd, name):
    parts = _parts(name)
    descriptor = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _stat(root_fd, name):
    with _parent(root_fd, name) as (parent, leaf):
        return os.stat(leaf, dir_fd=parent, follow_symlinks=False)


def _unchanged(root_fd, name, expected):
    current = _stat(root_fd, name)
    _require(stat.S_ISREG(current.st_mode) and _identity(current) == expected,
             "identity_changed", "檔案身分或所在路徑已變動")


@contextmanager
def _regular(root_fd, name, expected=None):
    with _parent(root_fd, name) as (parent, leaf):
        # O_PATH 先確認型別，不觸發裝置驅動或阻塞於管線。
        path_fd = os.open(leaf, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            before = os.fstat(path_fd)
            _require(stat.S_ISREG(before.st_mode), "not_regular", "只接受一般檔案")
            identity = _identity(before)
            _require(expected is None or identity == expected,
                     "identity_changed", "檔案與盤點身分不符")
            descriptor = os.open(f"/proc/self/fd/{path_fd}", os.O_RDONLY | os.O_NONBLOCK)
        finally:
            os.close(path_fd)
        with os.fdopen(descriptor, "rb") as stream:
            _require(_identity(os.fstat(stream.fileno())) == identity,
                     "identity_changed", "開啟檔案時身分已變動")
            yield stream, identity
            _require(_identity(os.fstat(stream.fileno())) == identity,
                     "identity_changed", "讀取期間檔案身分已變動")
            _unchanged(root_fd, name, identity)


def _read_small(root_fd, name, limit):
    with _regular(root_fd, name) as (stream, identity):
        _require(identity["st_size"] <= limit, "metadata_too_large", "旁檔或設定超過讀取上限")
        blob = stream.read(limit + 1)
        _require(len(blob) <= limit and len(blob) == identity["st_size"],
                 "identity_changed", "讀取期間檔案長度已變動")
    try:
        value = blob.decode("utf-8")
    except UnicodeError as exc:
        raise CatalogError("invalid_encoding", "旁檔或設定不是有效 UTF-8") from exc
    return value, hashlib.sha256(blob).hexdigest(), identity


def _sha(root_fd, name):
    text, digest, identity = _read_small(root_fd, name + ".sha", MAX_SHA_BYTES)
    rows = [line for line in text.splitlines() if line.strip()]
    _require(len(rows) == 1, "sha_record_count", "旁檔須恰有一筆宣告，不接受重複或多筆")
    match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", rows[0])
    _require(match is not None, "sha_format", "旁檔不是帶檔名的 SHA-256 宣告")
    _require(match[2] == PurePosixPath(name).name,
             "sha_filename_mismatch", "旁檔宣告檔名與映像不符，不接受絕對或相對路徑")
    return match[1].lower(), {"path": name + ".sha", "sha256": digest, "identity": identity}


class _ConfigReader:
    """只採用宣告區的字面值；動態、條件或多值宣告保留問題。"""

    def __init__(self, root_fd):
        self.root_fd = root_fd
        self.observed = {}

    def declarations(self, path, key, issues, sources, stack=()):
        if path in stack or len(stack) >= MAX_DEPTH or len(sources) >= MAX_INCLUDE_FILES:
            issues.append(_issue("include_cycle_or_limit", path, "設定引用循環或超過解析上限"))
            return []
        try:
            value, digest, identity = _read_small(self.root_fd, path, MAX_CONFIG_BYTES)
        except (OSError, CatalogError) as exc:
            issues.append(_failure(exc, path))
            return []
        previous = self.observed.setdefault(path, identity)
        if previous != identity:
            issues.append(_issue("identity_changed", path, "同次盤點中的設定已變動"))
        sources[path] = digest
        values = []
        declarative = True
        for number, raw in enumerate(value.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            relevant = re.search(rf"\b{key}\s*\+?=", line)
            assignment = re.match(r"(?:(?:declare\s+-g|export|readonly)\s+)?"
                                  r"([A-Za-z_][A-Za-z0-9_]*)=", line)
            location = f"{path}:{number}"
            if relevant:
                literal = re.fullmatch(
                    rf"(?:(?:declare\s+-g|export|readonly)\s+)?{key}="
                    r"(?:\"([A-Za-z0-9.+-]+)\"|'([A-Za-z0-9.+-]+)'|([A-Za-z0-9.+-]+))"
                    r"\s*(?:#.*)?", line)
                if not declarative or literal is None:
                    issues.append(_issue("dynamic_config", location, "不推測條件或動態設定值"))
                else:
                    values.append(next(group for group in literal.groups() if group is not None))
                continue
            if not declarative and re.match(r"(?:source|\.)\s", line):
                issues.append(_issue("dynamic_include", location, "不推測宣告區以外的設定引用"))
                continue
            if declarative and re.match(r"(?:source|\.)\s", line):
                try:
                    tokens = shlex.split(line, comments=True)
                    _require(len(tokens) == 2, "dynamic_include", "只接受單一靜態引用路徑")
                    target = tokens[1]
                    if target.startswith("${SRC}/"):
                        target = target[len("${SRC}/"):]
                    elif target.startswith("${BASH_SOURCE%/*}/"):
                        target = str(PurePosixPath(path).parent) + "/" + target[len("${BASH_SOURCE%/*}/"):]
                    else:
                        _require(not target.startswith("/"), "unsafe_include", "引用不得使用絕對路徑")
                        target = str(PurePosixPath(path).parent) + "/" + target
                    _parts(target)
                    _require(target.startswith(("config/boards/", "config/sources/families/")),
                             "unsafe_include", "引用不得超出板型與家族設定目錄")
                    values.extend(self.declarations(target, key, issues, sources, (*stack, path)))
                except (ValueError, CatalogError) as exc:
                    if not isinstance(exc, CatalogError):
                        exc = CatalogError("dynamic_include", "無法靜態解析引用")
                    issues.append(_failure(exc, location))
            elif not assignment and not line.startswith("enable_extension "):
                # 遇到函式、條件、here-document 或其他命令後，不將內文當頂層宣告。
                declarative = False
        return values

    def board(self, profile, index):
        info = {"architecture": "unknown", "family": "unknown", "config_path": None,
                "config_sha256": None, "config_sources": [], "issues": []}
        issues, sources = info["issues"], {}
        candidates = index.get(profile, [])
        if not candidates:
            issues.append(_issue("unknown_profile", profile, "找不到完全對應的板型設定，不推測別名"))
            return info
        if len(candidates) != 1:
            issues.append(_issue("duplicate_config", profile, "同一 profile 有多個設定，不能任選"))
            return info
        path = info["config_path"] = candidates[0]
        families = set(self.declarations(path, "BOARDFAMILY", issues, sources))
        info["config_sha256"] = sources.get(path)
        if len(families) == 1 and not issues:
            info["family"] = next(iter(families))
            family_path = f"config/sources/families/{info['family']}.conf"
            arches = set(self.declarations(family_path, "ARCH", issues, sources))
            arches.update(self.declarations(path, "ARCH", issues, sources))
            if len(arches) == 1 and not issues:
                info["architecture"] = ARCH_MAP.get(next(iter(arches)), "unknown")
        else:
            issues.append(_issue("unknown_family", path, "家族宣告缺失、動態或互相衝突"))
        if info["architecture"] == "unknown":
            issues.append(_issue("unknown_architecture", path, "架構宣告缺失、動態、不支援或互相衝突"))
        info["config_sources"] = [{"path": key, "sha256": sources[key]} for key in sorted(sources)]
        return info


def _config_index(root_fd):
    with _parent(root_fd, "config/boards/_") as (directory, _):
        result = {}
        for name in sorted(os.listdir(directory)):
            path = PurePosixPath(name)
            if path.suffix in CONFIG_SUFFIXES:
                result.setdefault(path.stem.lower(), []).append("config/boards/" + name)
        return result


def _filename(name, issues):
    result = dict.fromkeys(("artifact_board", "release", "variant", "branch", "kernel"), "unknown")
    match = re.fullmatch(
        r"(?:Armbian(?:-unofficial)?|Bananapi-Armbian)_([^_]+)_([A-Za-z0-9-]+)_"
        r"([A-Za-z0-9.-]+)_([A-Za-z0-9.-]+)_([A-Za-z0-9.+-]+)(?:_([A-Za-z0-9_-]+))?\.img\.xz",
        name)
    if match is None:
        issues.append(_issue("unknown_filename", name, "映像檔名不符合已知格式"))
        return result
    result.update(artifact_board=match[2].lower(), release=match[3].lower(),
                  branch=match[4].lower(), kernel=match[5], variant=match[6] or "unknown")
    for field, allowed in (("release", RELEASES), ("variant", VARIANTS), ("branch", BRANCHES)):
        if result[field] not in allowed:
            issues.append(_issue("unknown_" + field, name, f"未核定的 {field}，保留原值供審查"))
    if result["kernel"] == "0":
        issues.append(_issue("unknown_kernel", name, "核心版本為 0，不能視為已知版本"))
    return result


def _walk(root_fd, issues, directories, prefix="", depth=0):
    if depth > MAX_DEPTH:
        issues.append(_issue("depth_limit", prefix, "目錄超過盤點深度上限"))
        return
    before = _identity(os.fstat(root_fd))
    directories[prefix] = before
    try:
        names = sorted(os.listdir(root_fd))
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            try:
                _parts(relative)
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                yield relative, info
                if stat.S_ISDIR(info.st_mode) and not name.endswith(".img.xz"):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                    try:
                        _require(_identity(os.fstat(child)) == _identity(info),
                                 "identity_changed", "列舉期間目錄已被替換")
                        yield from _walk(child, issues, directories, relative, depth + 1)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(info.st_mode) and not name.endswith(".img.xz"):
                    issues.append(_issue("not_regular", relative, "不跟隨符號連結或讀取特殊檔案"))
            except (OSError, CatalogError) as exc:
                issues.append(_failure(exc, relative))
                if name.endswith(".img.xz"):
                    yield relative, None
    except OSError as exc:
        issues.append(_failure(exc, prefix))
    if _identity(os.fstat(root_fd)) != before:
        issues.append(_issue("identity_changed", prefix, "列舉期間目錄內容已變動"))


def _image_id(entry):
    payload = {key: entry[key] for key in ("relative_path", "identity", "expected_sha256")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _entry(root_fd, name, info):
    issues = []
    parts = name.split("/")
    board = parts[0] if len(parts) >= 2 and re.fullmatch(r"bpi-[a-z0-9-]+", parts[0]) else "unknown"
    entry = {"relative_path": name, "board": board, **_filename(parts[-1], issues),
             "architecture": "unknown", "family": "unknown",
             "compressed_bytes": info.st_size if info is not None else None,
             "expected_sha256": None, "identity": _identity(info) if info is not None else None,
             "issues": issues, "source_verified": False, "sidecar": None}
    if board not in BOARD_PROFILES:
        issues.append(_issue("unknown_board", name, "目錄板別不在明確對應表內"))
    elif BOARD_PROFILES[board] != entry["artifact_board"]:
        issues.append(_issue("board_profile_mismatch", name, "目錄板別與檔名 profile 不符"))
    try:
        _parts(name)
        _require(info is not None and stat.S_ISREG(info.st_mode), "not_regular", "映像不是可盤點的一般檔案")
        _require(info.st_size > 0, "empty_image", "映像為空檔案")
        entry["expected_sha256"], entry["sidecar"] = _sha(root_fd, name)
        _unchanged(root_fd, name, entry["identity"])
    except (OSError, CatalogError) as exc:
        issues.append(_failure(exc, name))
    entry["image_id"] = _image_id(entry)
    return entry


def scan(root, build_root) -> dict:
    """唯讀掃描指定樹與建置設定；所有 SHA 旁檔都只是未驗證的宣告。"""
    root_path = str(Path(root).absolute())
    result = {"schema": SCHEMA, "root": root_path, "entries": [], "boards": [], "issues": [],
              "hardware_validated": False, "metadata_only": True}
    issues, entries = result["issues"], result["entries"]
    directories, sidecars, snapshots = {}, set(), {}
    try:
        with _root(root) as root_fd:
            for name, info in _walk(root_fd, issues, directories):
                if name.endswith(".img.xz"):
                    entries.append(_entry(root_fd, name, info))
                elif name.endswith(".img.xz.sha"):
                    sidecars.add(name)
            images = {entry["relative_path"] for entry in entries}
            for name in sorted(sidecars):
                if name[:-4] not in images:
                    issues.append(_issue("orphan_sha", name, "旁檔找不到對應映像"))
    except (OSError, CatalogError) as exc:
        issues.append(_failure(exc, root_path))

    board_keys = {(entry["board"], entry["artifact_board"]) for entry in entries}
    for path in directories:
        if "/" not in path and path.startswith("bpi-") and not any(board == path for board, _ in board_keys):
            board_keys.add((path, "unknown"))
    try:
        with _root(build_root) as build_fd:
            build_identity = _identity(os.fstat(build_fd))
            reader = _ConfigReader(build_fd)
            index = _config_index(build_fd)
            for profile in sorted({profile for _, profile in board_keys}):
                snapshots[profile] = reader.board(profile, index)
            for path, identity in reader.observed.items():
                try:
                    _unchanged(build_fd, path, identity)
                except (OSError, CatalogError) as exc:
                    issues.append(_failure(exc, path))
                    for snapshot in snapshots.values():
                        if any(source["path"] == path for source in snapshot["config_sources"]):
                            snapshot["issues"].append(_failure(exc, path))
                            snapshot["architecture"] = "unknown"
            _require(_config_index(build_fd) == index,
                     "identity_changed", "盤點期間板型設定清單已變動")
            with _root(build_root) as current_build:
                _require(_identity(os.fstat(current_build)) == build_identity,
                         "identity_changed", "盤點期間建置根目錄已被替換")
    except (OSError, CatalogError) as exc:
        issues.append(_failure(exc, str(build_root)))
        for snapshot in snapshots.values():
            snapshot["issues"].append(_failure(exc, str(build_root)))
            snapshot["architecture"] = "unknown"

    # 設定解析也屬於盤點期間，最後才重驗映像、旁檔及目錄身分。
    try:
        with _root(root) as root_fd:
            for entry in entries:
                if entry["identity"] is not None:
                    try:
                        _unchanged(root_fd, entry["relative_path"], entry["identity"])
                        if entry["sidecar"]:
                            sidecar = entry["sidecar"]
                            _unchanged(root_fd, sidecar["path"], sidecar["identity"])
                    except (OSError, CatalogError) as exc:
                        entry["issues"].append(_failure(exc, entry["relative_path"]))
            for path, identity in directories.items():
                try:
                    current = _stat(root_fd, path) if path else os.fstat(root_fd)
                    _require(stat.S_ISDIR(current.st_mode) and _identity(current) == identity,
                             "identity_changed", "盤點期間目錄已變動")
                except (OSError, CatalogError) as exc:
                    issues.append(_failure(exc, path))
            with _root(root) as current_root:
                _require(_identity(os.fstat(current_root)) == directories.get(""),
                         "identity_changed", "盤點期間根目錄已被替換")
    except (OSError, CatalogError) as exc:
        issues.append(_failure(exc, root_path))
        for entry in entries:
            entry["issues"].append(_failure(exc, root_path))
    for board, profile in sorted(board_keys):
        metadata = snapshots.get(profile, {"architecture": "unknown", "family": "unknown",
            "config_path": None, "config_sha256": None, "config_sources": [],
            "issues": [_issue("config_unavailable", profile, "無法取得板型設定")]})
        row = {"board": board, "artifact_board": profile, **metadata,
               "issues": list(metadata["issues"]), "hardware_status": "awaiting_hardware",
               "backend_status": "unqualified"}
        matches = [entry for entry in entries if (entry["board"], entry["artifact_board"]) == (board, profile)]
        if not matches:
            row["issues"].append(_issue("empty_board", board, "板別目錄內沒有映像"))
        for entry in matches:
            entry.update({key: metadata[key] for key in ("architecture", "family")})
            entry["issues"].extend(metadata["issues"])
            for issue in entry["issues"]:
                if issue not in row["issues"]:
                    row["issues"].append(issue)
        result["boards"].append(row)
    for entry in entries:
        for issue in entry["issues"]:
            if issue not in issues:
                issues.append(issue)
    for row in result["boards"]:
        for issue in row["issues"]:
            if issue not in issues:
                issues.append(issue)
    entries.sort(key=lambda entry: (ARCHITECTURES.index(entry["architecture"]), entry["release"],
                                    entry["board"], entry["variant"], entry["relative_path"]))
    result["boards"].sort(key=lambda row: (ARCHITECTURES.index(row["architecture"]), row["board"], row["artifact_board"]))
    issues.sort(key=lambda issue: (issue["path"], issue["code"], issue["message"]))
    return result


def verify_entry(root, entry) -> dict:
    """完整核對單一壓縮檔，不解壓；只驗內容與宣告一致，不驗真實性或硬體。"""
    result = {"relative_path": entry.get("relative_path") if isinstance(entry, dict) else None,
              "source_verified": False, "hardware_validated": False, "metadata_only": False,
              "source_authenticated": False,
              "actual_sha256": None, "verified_bytes": 0, "issues": []}
    name = result["relative_path"]
    try:
        _parts(name)
        _require(isinstance(entry, dict), "invalid_entry", "盤點項目格式無效")
        identity = entry.get("identity")
        _require(isinstance(identity, dict) and set(identity) == set(IDENTITY_FIELDS)
                 and all(type(value) is int and value >= 0 for value in identity.values()),
                 "invalid_identity", "缺少完整有效的檔案身分")
        _require(type(entry.get("compressed_bytes")) is int and entry["compressed_bytes"] == identity["st_size"]
                 and identity["st_size"] > 0, "invalid_identity", "映像大小與盤點身分不符")
        expected = entry.get("expected_sha256")
        _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
                 "missing_sha256", "沒有可供核對的 SHA-256 宣告")
        _require(not entry.get("issues"), "entry_has_issues", "盤點問題未排除，不進行完整核對")
        _require(entry.get("image_id") == _image_id(entry), "image_id_mismatch", "盤點識別碼與內容不符")
        with _root(root) as root_fd:
            root_identity = _identity(os.fstat(root_fd))
            with _regular(root_fd, name, identity) as (stream, _):
                digest = hashlib.sha256()
                remaining = identity["st_size"]
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    _require(bool(chunk), "identity_changed", "核對期間檔案縮短")
                    remaining -= len(chunk)
                    result["verified_bytes"] += len(chunk)
                    digest.update(chunk)
                _require(not stream.read(1), "identity_changed", "核對期間檔案增長")
                result["actual_sha256"] = digest.hexdigest()
            with _root(root) as current_root:
                _require(_identity(os.fstat(current_root)) == root_identity,
                         "identity_changed", "核對期間根目錄已被替換")
        _require(result["actual_sha256"] == expected, "sha256_mismatch", "實際內容與旁檔宣告不符")
        result["source_verified"] = True
    except (OSError, CatalogError) as exc:
        result["issues"].append(_failure(exc, name))
    return result
