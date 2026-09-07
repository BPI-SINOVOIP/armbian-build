#!/usr/bin/env python3
"""以受控中繼資料封存及核對候選 XZ 通過清冊，不讀取映像本體。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


MATRIX_FIELDS = ["folder", "board", "branch", "releases"]
POLICY_FIELDS = ["folder", "source_commit", "build_context_sha256"]
INVENTORY_FIELDS = [
    "唯一鍵", "板目錄", "板卡", "分支", "發行版", "類型", "狀態", "選用來源",
    "映像", "SHA256", "來源提交", "建置內容雜湊", "處置",
]
IDENTITY_FIELDS = ["folder", "board", "branch", "release", "profile"]
ITEM_FIELDS = set(IDENTITY_FIELDS) | {
    "key", "archive", "source_commit", "build_context_sha256", "sha256",
}
SUCCESS = {"狀態": "成功", "完整SHA256與XZ稽核": "yes", "遷移後零待辦": "yes"}
NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def real_path(path: Path, directory: bool = False) -> Path:
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError(f"路徑不得含上層跳轉：{path}")
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError(f"上層路徑必須是無連結實體目錄：{parent}")
    info = path.lstat()
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        raise ValueError(f"路徑不得為連結或非正常檔案：{path}")
    return path


def read_bytes(path: Path, sources: dict[str, str]) -> bytes:
    path = real_path(path)
    if path.suffix in {".xz", ".img"}:
        raise ValueError(f"禁止讀取映像本體：{path}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"來源不是無連結一般檔案：{path}")
        content = stream.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024:
        raise ValueError(f"中繼資料超過大小限制：{path}")
    digest = hashlib.sha256(content).hexdigest()
    if sources.setdefault(str(path), digest) != digest:
        raise ValueError(f"來源於核對期間遭異動：{path}")
    return content


def read_tsv(path: Path, fields: list[str], sources: dict[str, str]) -> list[dict[str, str]]:
    rows = list(csv.reader(io.StringIO(read_bytes(path, sources).decode("utf-8")), delimiter="\t", strict=True))
    if not rows or rows[0] != fields:
        raise ValueError(f"TSV 欄位錯誤：{path}")
    if any(len(row) != len(fields) or any(not value for value in row) for row in rows[1:]):
        raise ValueError(f"TSV 資料欄位缺漏或多出：{path}")
    return [dict(zip(fields, row)) for row in rows[1:]]


def unique(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"資料含重複鍵：{key}")
    return result


def matrix_items(path: Path, sources: dict[str, str]) -> dict[str, dict[str, str]]:
    rows = read_tsv(path, MATRIX_FIELDS, sources)
    if not rows:
        raise ValueError("矩陣不得為空。")
    unique(rows, "folder")
    unique(rows, "board")
    items = {}
    for row in rows:
        releases = row["releases"].split(",")
        if len(set(releases)) != len(releases) or not all(
            NAME_RE.fullmatch(value)
            for value in [row["folder"], row["board"], row["branch"], *releases]
        ):
            raise ValueError("矩陣含不安全識別值或重複發行版。")
        for release in releases:
            for profile in ("minimal", "xfce"):
                identity = dict(zip(IDENTITY_FIELDS, [row["folder"], row["board"], row["branch"], release, profile]))
                items["/".join(identity.values())] = identity
    return items


def validate_items(items: object, expected: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or set(item) != ITEM_FIELDS
        or any(not isinstance(value, str) or not value for value in item.values())
        for item in items
    ):
        raise ValueError("映像清冊欄位缺漏、多出或型別錯誤。")
    indexed = unique(items, "key")
    if set(indexed) != set(expected):
        raise ValueError("映像清冊未精確覆蓋矩陣。")
    for key, item in indexed.items():
        if any(item[field] != value for field, value in expected[key].items()):
            raise ValueError(f"映像身分與矩陣不一致：{key}")
        if not COMMIT_RE.fullmatch(item["source_commit"]) or not all(
            SHA_RE.fullmatch(item[field]) for field in ("sha256", "build_context_sha256")
        ):
            raise ValueError(f"映像來源或雜湊格式錯誤：{key}")
        if Path(item["archive"]).name != item["archive"] or not item["archive"].endswith(".img.xz"):
            raise ValueError(f"映像檔名錯誤：{key}")
    return indexed


def read_policy(path: Path, expected: dict[str, dict[str, str]], sources: dict[str, str]) -> dict[str, dict[str, str]]:
    policy = unique(read_tsv(path, POLICY_FIELDS, sources), "folder")
    if set(policy) != {item["folder"] for item in expected.values()}:
        raise ValueError("逐板政策未精確覆蓋矩陣。")
    for row in policy.values():
        if not COMMIT_RE.fullmatch(row["source_commit"]) or not SHA_RE.fullmatch(row["build_context_sha256"]):
            raise ValueError("逐板政策雜湊格式錯誤。")
    return policy


def audit_items(root: Path, expected: dict[str, dict[str, str]], sources: dict[str, str]) -> dict[str, dict[str, str]]:
    root = real_path(root, directory=True)
    rows = read_tsv(root / "映像盤點.tsv", INVENTORY_FIELDS, sources)
    items = []
    for row in rows:
        if row["狀態"] != "已驗證候選" or row["處置"] != "不再建置":
            raise ValueError("稽核含未通過候選。")
        archive = Path(row["映像"])
        if ".." in archive.parts:
            raise ValueError("稽核映像路徑不得含上層跳轉。")
        item = dict(zip(IDENTITY_FIELDS, [row[field] for field in INVENTORY_FIELDS[1:6]]))
        item.update(key=row["唯一鍵"], archive=archive.name, source_commit=row["來源提交"],
                    build_context_sha256=row["建置內容雜湊"], sha256=row["SHA256"])
        items.append(item)
    indexed = validate_items(items, expected)
    policy = read_policy(root / "候選輸入政策.tsv", expected, sources)
    for item in indexed.values():
        if any(item[field] != policy[item["folder"]][field] for field in POLICY_FIELDS[1:]):
            raise ValueError("映像清冊與逐板政策不一致。")
    return indexed


def read_marker(path: Path, sources: dict[str, str]) -> dict[str, str]:
    values = {}
    for line in read_bytes(path, sources).decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not KEY_RE.fullmatch(key) or key in values:
            raise ValueError(f"標記欄位錯誤或重複：{path}")
        values[key] = value
    return values


def require_values(actual: dict[str, str], expected: dict[str, str], label: str) -> None:
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError(f"{label}缺少必要欄位或內容不一致。")


def create_proof(args: argparse.Namespace, expected: dict[str, dict[str, str]], digest: str, sources: dict[str, str]) -> dict:
    state = real_path(args.candidate_state, directory=True)
    if any(args.output.absolute().is_relative_to(state / name) for name in ("boards", "items")):
        raise ValueError("摘要輸出不得位於標記目錄。")
    migrations = real_path(state / "migrations", directory=True)
    evidence = real_path(args.migration_evidence, directory=True)
    if evidence == migrations or not evidence.is_relative_to(migrations):
        raise ValueError("遷移證據必須位於候選狀態的 migrations 子目錄內。")
    status_rows = read_tsv(evidence / "執行狀態.tsv", ["欄位", "值"], sources)
    status = {key: row["值"] for key, row in unique(status_rows, "欄位").items()}
    required_status = {**SUCCESS, "矩陣SHA256": digest}
    require_values(status, required_status, "遷移成功證據")
    items = audit_items(evidence / "完整性稽核", expected, sources)
    policy = read_policy(evidence / "逐板候選輸入政策.tsv", expected, sources)
    board_root = real_path(state / "boards", directory=True)
    item_root = real_path(state / "items", directory=True)
    if {path.name for path in board_root.glob("*.complete")} != {f"{folder}.complete" for folder in policy}:
        raise ValueError("板級標記未精確覆蓋矩陣。")
    if {path.name for path in item_root.glob("*.complete")} != {
        f"{item['folder']}-{item['release']}-{item['profile']}.complete" for item in items.values()
    }:
        raise ValueError("項目標記未精確覆蓋矩陣。")
    for folder, identity in policy.items():
        board = read_marker(board_root / f"{folder}.complete", sources)
        members = [item for item in items.values() if item["folder"] == folder]
        require_values(board, {**identity, "board": members[0]["board"], "branch": members[0]["branch"],
                              "images": str(len(members)), "status": "complete", "matrix_sha256": digest}, "板級標記")
        for field, pattern in (("bsp_base_commit", COMMIT_RE), ("userpatches_sha256", SHA_RE)):
            if not pattern.fullmatch(board.get(field, "")):
                raise ValueError("板級標記缺少有效建置身分。")
        for item in members:
            require_values(item, identity, "映像逐板政策")
            marker = read_marker(item_root / f"{folder}-{item['release']}-{item['profile']}.complete", sources)
            require_values(marker, {**{key: value for key, value in item.items() if key != "key"},
                                    "matrix_sha256": digest, "status": "complete",
                                    "bsp_base_commit": board["bsp_base_commit"],
                                    "userpatches_sha256": board["userpatches_sha256"]}, "項目標記")
    return {"version": 1, "matrix_sha256": digest, "migration_status": required_status,
            "matrix_path": str(args.matrix.absolute()), "candidate_state": str(state),
            "migration_evidence": str(evidence),
            "items": [items[key] for key in sorted(items)], "source_files": dict(sources)}


def json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 含重複欄位：{key}")
        result[key] = value
    return result


def verify_proof(args: argparse.Namespace, expected: dict[str, dict[str, str]], digest: str, sources: dict[str, str]) -> None:
    if not SHA_RE.fullmatch(args.proof_sha256):
        raise ValueError("摘要 SHA256 格式錯誤。")
    content = read_bytes(args.proof, sources)
    if hashlib.sha256(content).hexdigest() != args.proof_sha256:
        raise ValueError("摘要 SHA256 不一致。")
    proof = json.loads(content, object_pairs_hook=json_object)
    if not isinstance(proof, dict) or set(proof) != {
        "version", "matrix_sha256", "migration_status", "items", "source_files",
        "matrix_path", "candidate_state", "migration_evidence",
    }:
        raise ValueError("摘要欄位錯誤。")
    if type(proof["version"]) is not int or proof["version"] != 1 or proof["matrix_sha256"] != digest:
        raise ValueError("摘要版本或矩陣 SHA256 不一致。")
    if proof["migration_status"] != {**SUCCESS, "矩陣SHA256": digest}:
        raise ValueError("摘要缺少完整成功旗標。")
    for field in ("matrix_path", "candidate_state", "migration_evidence"):
        if not isinstance(proof[field], str) or not Path(proof[field]).is_absolute() or ".." in Path(proof[field]).parts:
            raise ValueError("摘要來源路徑格式錯誤。")
    state = Path(proof["candidate_state"])
    evidence = Path(proof["migration_evidence"])
    if evidence == state / "migrations" or not evidence.is_relative_to(state / "migrations"):
        raise ValueError("摘要遷移來源不在受控 migrations 內。")
    source_files = proof["source_files"]
    if not isinstance(source_files, dict) or not source_files or any(
        not Path(path).is_absolute() or not isinstance(value, str) or not SHA_RE.fullmatch(value)
        for path, value in source_files.items()
    ):
        raise ValueError("摘要來源檔案雜湊證據缺漏或錯誤。")
    required_sources = {
        proof["matrix_path"], str(evidence / "執行狀態.tsv"),
        str(evidence / "完整性稽核" / "映像盤點.tsv"),
        str(evidence / "完整性稽核" / "候選輸入政策.tsv"),
        str(evidence / "逐板候選輸入政策.tsv"),
        *(str(state / "boards" / f"{item['folder']}.complete") for item in expected.values()),
        *(str(state / "items" / f"{item['folder']}-{item['release']}-{item['profile']}.complete") for item in expected.values()),
    }
    if set(source_files) != required_sources or source_files[proof["matrix_path"]] != digest:
        raise ValueError("摘要來源檔案集合或矩陣雜湊不完整。")
    items = validate_items(proof["items"], expected)
    if audit_items(args.audit_output, expected, sources) != items:
        raise ValueError("新稽核與原 XZ 通過清冊的映像身分或 SHA256 不一致。")


def atomic_json(path: Path, proof: dict, sources: dict[str, str]) -> None:
    parent = real_path(path.absolute().parent, directory=True)
    path = parent / path.name
    if path.exists() or path.is_symlink():
        raise ValueError(f"摘要輸出已存在，拒絕覆寫：{path}")
    if str(path) in sources:
        raise ValueError("摘要輸出不得覆寫輸入證據。")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(proof, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # 以不可覆寫的原子發布避免並行建立時蓋掉既有證據。
        os.link(temporary, path)
        temporary.unlink()
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="封存或只讀核對候選 XZ 通過清冊；不讀取映像本體。")
    parser.add_argument("--matrix", type=Path, required=True, help="受控矩陣 TSV")
    for name, label in (("candidate-state", "候選狀態目錄"), ("migration-evidence", "成功遷移證據目錄"),
                        ("output", "新建 JSON 摘要"), ("proof", "既有 JSON 摘要"), ("audit-output", "新稽核目錄")):
        parser.add_argument(f"--{name}", type=Path, help=label)
    parser.add_argument("--proof-sha256", help="外部固定的摘要 SHA256")
    args = parser.parse_args(argv)
    creation = [args.candidate_state, args.migration_evidence, args.output]
    verification = [args.proof, args.proof_sha256, args.audit_output]
    if not ((all(creation) and not any(verification)) or (all(verification) and not any(creation))):
        parser.error("請完整指定產生模式或核對模式的三個選項，不得混用。")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sources: dict[str, str] = {}
    try:
        expected = matrix_items(args.matrix, sources)
        digest = sources[str(args.matrix.absolute())]
        if args.output:
            proof = create_proof(args, expected, digest, sources)
        else:
            verify_proof(args, expected, digest, sources)
        for source in list(sources):
            read_bytes(Path(source), sources)
        if args.output:
            atomic_json(args.output, proof, sources)
    except (OSError, ValueError, UnicodeError, csv.Error) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    print(f"XZ 通過清冊{'摘要已產生' if args.output else '核對通過'}：{len(expected)} 個映像")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
