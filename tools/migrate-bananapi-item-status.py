#!/usr/bin/env python3
"""完整驗證舊項目標記後，原子補齊完成狀態與遷移證據。"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


MATRIX_FIELDS = ["folder", "board", "branch", "releases"]
PROFILES = ("minimal", "xfce")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
AUDIT_HEADERS = {
    "待辦佇列.tsv": ["板目錄", "板卡", "分支", "發行版", "類型", "動作", "原因"],
    "候選處置.tsv": [
        "唯一鍵",
        "候選來源",
        "處置",
        "映像",
        "SHA256",
        "來源提交",
        "建置內容雜湊",
    ],
    "映像盤點.tsv": [
        "唯一鍵",
        "板目錄",
        "板卡",
        "分支",
        "發行版",
        "類型",
        "狀態",
        "選用來源",
        "映像",
        "SHA256",
        "來源提交",
        "建置內容雜湊",
        "處置",
    ],
    "板卡決策.tsv": ["板目錄", "板卡", "分支", "預期映像數", "決策", "選用來源"],
    "候選輸入政策.tsv": ["folder", "source_commit", "build_context_sha256"],
    "中止產物.tsv": ["候選來源", "類別", "大小bytes", "處置", "路徑"],
    "候選交易殘留.tsv": [
        "候選來源",
        "類別",
        "板目錄",
        "項目",
        "檔案數",
        "大小bytes",
        "處置",
        "路徑",
    ],
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="完整驗證舊候選項目後，補齊 status=complete。"
    )
    parser.add_argument("--matrix", type=Path, required=True, help="受控矩陣 TSV")
    parser.add_argument(
        "--formal-release", type=Path, required=True, help="既有正式發布目錄"
    )
    parser.add_argument(
        "--candidate-release", type=Path, required=True, help="候選發布目錄"
    )
    parser.add_argument(
        "--candidate-state", type=Path, required=True, help="候選狀態目錄"
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="遷移證據目錄")
    parser.add_argument(
        "--expected-boards", type=int, default=45, help="預期板卡數，正式值為 45"
    )
    parser.add_argument(
        "--expected-items", type=int, default=444, help="預期映像數，正式值為 444"
    )
    parser.add_argument(
        "--policy-tool",
        type=Path,
        default=root / "tools/generate-bananapi-candidate-input-policy.py",
        help="逐板政策產生器",
    )
    parser.add_argument(
        "--audit-tool",
        type=Path,
        default=root / "tools/audit-bananapi-release-state.py",
        help="候選稽核工具",
    )
    parser.add_argument(
        "--allow-test-tools",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def migrated_marker_content(original: str, migration_at: str) -> str:
    return (
        original
        + "status=complete\n"
        + f"status_migration_started_utc={migration_at}\n"
        + "status_migration=legacy-item-v1\n"
    )


def require_real_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label}必須是實體目錄：{path}")
    return path.resolve()


def require_empty_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label}必須是實體目錄：{path}")
    if next(path.iterdir(), None) is not None:
        raise ValueError(f"{label}必須為空：{path}")


def require_real_file_within(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label}必須是實體一般檔案：{path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label}超出受控目錄：{path}") from error
    return resolved


def acquire_lock(path: Path, label: str) -> int:
    if path.is_symlink():
        raise ValueError(f"{label}不得為符號連結：{path}")
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        descriptor_status = os.fstat(descriptor)
        path_status = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(descriptor_status.st_mode)
            or descriptor_status.st_dev != path_status.st_dev
            or descriptor_status.st_ino != path_status.st_ino
        ):
            raise ValueError(f"{label}不是預期的實體一般檔案：{path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"{label}正由其他程序持有：{path}") from error
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def acquire_candidate_locks(candidate: Path, state: Path) -> list[int]:
    paths = [
        (state / ".incremental-queue.lock", "增量佇列鎖"),
        (
            candidate.parent / f".{candidate.name}.build.lock",
            "候選發布固定鎖",
        ),
        (candidate / ".latest-rebuild.lock", "候選發布相容鎖"),
        (state / ".compression-worker.lock", "壓縮工作鎖"),
    ]
    descriptors: list[int] = []
    try:
        for path, label in paths:
            descriptors.append(acquire_lock(path, label))
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    return descriptors


def read_values(path: Path) -> tuple[list[str], dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"標記不是一般檔案：{path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"標記是空檔：{path}")
    values: dict[str, str] = {}
    for number, line in enumerate(lines, start=1):
        if "=" not in line:
            raise ValueError(f"標記第 {number} 列格式錯誤：{path}")
        key, value = line.split("=", 1)
        if not KEY_RE.fullmatch(key) or key in values:
            raise ValueError(f"標記第 {number} 列欄位錯誤或重複：{path}")
        values[key] = value
    return lines, values


def require_fields(path: Path, values: dict[str, str], fields: set[str]) -> None:
    missing = sorted(field for field in fields if not values.get(field))
    if missing:
        raise ValueError(f"標記缺少必要欄位 {','.join(missing)}：{path}")


def read_matrix(path: Path) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"矩陣不是一般檔案：{path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != MATRIX_FIELDS:
            raise ValueError(f"矩陣欄位錯誤：{path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"矩陣沒有板卡資料：{path}")
    if len({row["folder"] for row in rows}) != len(rows):
        raise ValueError("矩陣含重複板目錄。")
    return rows


def collect_legacy_markers(
    matrix: list[dict[str, str]],
    matrix_sha256: str,
    state: Path,
    candidate: Path,
) -> list[tuple[Path, list[str], dict[str, str]]]:
    boards = state / "boards"
    items = state / "items"
    logs = state / "logs"
    framework_logs = state / "framework-logs"
    require_real_directory(boards, "板級標記目錄")
    require_real_directory(items, "項目標記目錄")
    log_root = require_real_directory(logs, "主要日誌目錄")
    framework_root = require_real_directory(framework_logs, "框架日誌目錄")
    expected_names: set[str] = set()
    legacy: list[tuple[Path, list[str], dict[str, str]]] = []
    for row in matrix:
        board_path = boards / f"{row['folder']}.complete"
        _, board = read_values(board_path)
        releases = row["releases"].split(",")
        board_directory = require_real_directory(
            candidate / row["folder"], "候選板卡目錄"
        )
        require_fields(
            board_path,
            board,
            {
                "source_commit",
                "bsp_base_commit",
                "matrix_sha256",
                "userpatches_sha256",
                "build_context_sha256",
                "folder",
                "board",
                "branch",
                "images",
                "status",
            },
        )
        if (
            board["folder"] != row["folder"]
            or board["board"] != row["board"]
            or board["branch"] != row["branch"]
            or board["images"] != str(len(releases) * len(PROFILES))
            or board["status"] != "complete"
            or not COMMIT_RE.fullmatch(board["source_commit"])
            or not COMMIT_RE.fullmatch(board["bsp_base_commit"])
            or board["matrix_sha256"] != matrix_sha256
            or not SHA256_RE.fullmatch(board["userpatches_sha256"])
            or not SHA256_RE.fullmatch(board["build_context_sha256"])
        ):
            raise ValueError(f"板級標記與矩陣或完成狀態不一致：{board_path}")
        for release in releases:
            for profile in PROFILES:
                name = f"{row['folder']}-{release}-{profile}.complete"
                expected_names.add(name)
                path = items / name
                lines, values = read_values(path)
                require_fields(
                    path,
                    values,
                    {
                        "source_commit",
                        "bsp_base_commit",
                        "matrix_sha256",
                        "userpatches_sha256",
                        "build_context_sha256",
                        "folder",
                        "board",
                        "branch",
                        "release",
                        "profile",
                        "archive",
                        "sha256",
                        "log",
                        "log_sha256",
                    },
                )
                expected = {
                    "source_commit": board["source_commit"],
                    "bsp_base_commit": board["bsp_base_commit"],
                    "matrix_sha256": board["matrix_sha256"],
                    "userpatches_sha256": board["userpatches_sha256"],
                    "build_context_sha256": board["build_context_sha256"],
                    "folder": row["folder"],
                    "board": row["board"],
                    "branch": row["branch"],
                    "release": release,
                    "profile": profile,
                }
                if any(values.get(field) != value for field, value in expected.items()):
                    raise ValueError(f"項目標記與矩陣或板級標記不一致：{path}")
                if not SHA256_RE.fullmatch(values["sha256"]):
                    raise ValueError(f"項目標記 SHA-256 格式錯誤：{path}")
                archive_name = values["archive"]
                if archive_name != Path(archive_name).name:
                    raise ValueError(f"項目標記映像名稱不安全：{path}")
                require_real_file_within(
                    board_directory / archive_name,
                    board_directory,
                    "候選映像",
                )
                require_real_file_within(
                    board_directory / f"{archive_name}.sha",
                    board_directory,
                    "候選 SHA 邊車",
                )
                if not SHA256_RE.fullmatch(values["log_sha256"]):
                    raise ValueError(f"主要日誌 SHA-256 格式錯誤：{path}")
                log_path = require_real_file_within(
                    Path(values["log"]), log_root, "主要日誌"
                )
                if file_sha256(log_path) != values["log_sha256"]:
                    raise ValueError(f"主要日誌 SHA-256 不符：{path}")
                framework_digest = values.get("framework_log_sha256", "")
                if framework_digest:
                    if not SHA256_RE.fullmatch(framework_digest):
                        raise ValueError(f"框架日誌 SHA-256 格式錯誤：{path}")
                    require_fields(path, values, {"framework_log"})
                    framework_path = require_real_file_within(
                        Path(values["framework_log"]),
                        framework_root,
                        "框架日誌",
                    )
                    if file_sha256(framework_path) != framework_digest:
                        raise ValueError(f"框架日誌 SHA-256 不符：{path}")
                status = values.get("status")
                if status is None:
                    legacy.append((path, lines, values))
                elif status != "complete":
                    raise ValueError(f"項目標記狀態不是 complete：{path}")
    actual_names = {path.name for path in items.glob("*.complete")}
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise ValueError(
            f"項目標記集合不符合矩陣；缺少={','.join(missing)}；多出={','.join(extra)}"
        )
    return legacy


def atomic_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError(f"原子寫入目標不得為符號連結：{path}")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def copy_file_durable(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    os.chmod(destination, source.stat().st_mode & 0o777)
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())


def create_input_snapshots(
    staging: Path,
    matrix: Path,
    policy_tool: Path,
    audit_tool: Path,
) -> tuple[dict[str, Path], dict[str, str]]:
    snapshot_root = staging / "執行輸入快照"
    snapshot_root.mkdir()
    sources = {
        "矩陣": (matrix, snapshot_root / "發布矩陣.tsv", 0o444),
        "遷移工具": (Path(__file__), snapshot_root / "狀態遷移工具.py", 0o444),
        "政策工具": (policy_tool, snapshot_root / "候選輸入政策工具.py", 0o444),
        "稽核工具": (audit_tool, snapshot_root / "發布狀態稽核工具.py", 0o444),
    }
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for label, (source, destination, mode) in sources.items():
        copy_file_durable(source, destination)
        os.chmod(destination, mode)
        paths[label] = destination
        digests[label] = file_sha256(destination)
    fsync_directory(snapshot_root)
    return paths, digests


def verify_input_snapshots(
    paths: dict[str, Path], digests: dict[str, str]
) -> None:
    if set(paths) != set(digests):
        raise ValueError("執行輸入快照集合不一致。")
    snapshot_root = require_real_directory(
        next(iter(paths.values())).parent,
        "執行輸入快照目錄",
    )
    for label, path in paths.items():
        require_real_file_within(path, snapshot_root, f"{label}快照")
        if file_sha256(path) != digests[label]:
            raise ValueError(f"{label}快照於執行期間遭異動：{path}")


def run_logged(command: list[str], log: Path) -> None:
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        stream.flush()
        os.fsync(stream.fileno())
    if result.returncode != 0:
        raise ValueError(f"外部驗證失敗，退出碼 {result.returncode}：{log}")


def read_audit_rows(root: Path, name: str) -> list[dict[str, str]]:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"完整稽核缺少一般檔案：{path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != AUDIT_HEADERS[name]:
            raise ValueError(f"完整稽核欄位錯誤：{path}")
        return list(reader)


def unique_rows(
    rows: list[dict[str, str]], key: str, label: str
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row.get(key, "")
        if not value or value in result:
            raise ValueError(f"完整稽核 {label} 含空白或重複鍵：{value}")
        result[value] = row
    return result


def validate_audit(
    root: Path, matrix: list[dict[str, str]], state: Path
) -> None:
    pending = read_audit_rows(root, "待辦佇列.tsv")
    dispositions = read_audit_rows(root, "候選處置.tsv")
    inventory = read_audit_rows(root, "映像盤點.tsv")
    boards = read_audit_rows(root, "板卡決策.tsv")
    policy = read_audit_rows(root, "候選輸入政策.tsv")
    interrupted = read_audit_rows(root, "中止產物.tsv")
    transactions = read_audit_rows(root, "候選交易殘留.tsv")
    expected_items: dict[str, tuple[str, str, str, str, str]] = {}
    for row in matrix:
        for release in row["releases"].split(","):
            for profile in PROFILES:
                key = "/".join(
                    (row["folder"], row["board"], row["branch"], release, profile)
                )
                expected_items[key] = (
                    row["folder"],
                    row["board"],
                    row["branch"],
                    release,
                    profile,
                )
    expected_folders = {row["folder"]: row for row in matrix}
    item_count = len(expected_items)
    board_count = len(expected_folders)
    if pending or interrupted or transactions:
        raise ValueError(f"完整稽核仍有待辦、中止產物或交易殘留：{root}")
    if (
        len(dispositions) != item_count
        or len(inventory) != item_count
        or len(boards) != board_count
        or len(policy) != board_count
    ):
        raise ValueError(f"完整稽核未達 {board_count}/{item_count} 精確數量：{root}")
    if any(
        row["候選來源"] != "整併候選" or row["處置"] != "採用"
        for row in dispositions
    ):
        raise ValueError(f"完整稽核含未採用候選：{root}")
    if any(
        row["狀態"] != "已驗證候選"
        or row["選用來源"] != "整併候選"
        or row["處置"] != "不再建置"
        for row in inventory
    ):
        raise ValueError(f"完整稽核映像狀態未全部通過：{root}")
    if any(
        row["決策"] != "沿用完整候選" or row["選用來源"] != "整併候選"
        for row in boards
    ):
        raise ValueError(f"完整稽核板卡決策未全部通過：{root}")
    disposition_by_key = unique_rows(dispositions, "唯一鍵", "候選處置")
    inventory_by_key = unique_rows(inventory, "唯一鍵", "映像盤點")
    if set(disposition_by_key) != set(expected_items):
        raise ValueError(f"完整稽核候選處置未完整覆蓋矩陣：{root}")
    if set(inventory_by_key) != set(expected_items):
        raise ValueError(f"完整稽核映像盤點未完整覆蓋矩陣：{root}")
    for key, expected in expected_items.items():
        item = inventory_by_key[key]
        if tuple(item[field] for field in ("板目錄", "板卡", "分支", "發行版", "類型")) != expected:
            raise ValueError(f"完整稽核映像欄位與矩陣不一致：{key}")
        disposition = disposition_by_key[key]
        for inventory_field, disposition_field in (
            ("映像", "映像"),
            ("SHA256", "SHA256"),
            ("來源提交", "來源提交"),
            ("建置內容雜湊", "建置內容雜湊"),
        ):
            if item[inventory_field] != disposition[disposition_field]:
                raise ValueError(f"完整稽核候選處置與映像盤點不一致：{key}")
    board_by_folder = unique_rows(boards, "板目錄", "板卡決策")
    policy_by_folder = unique_rows(policy, "folder", "候選輸入政策")
    if set(board_by_folder) != set(expected_folders) or set(policy_by_folder) != set(
        expected_folders
    ):
        raise ValueError(f"完整稽核板卡或政策未完整覆蓋矩陣：{root}")
    for folder, row in expected_folders.items():
        releases = row["releases"].split(",")
        decision = board_by_folder[folder]
        if (
            decision["板卡"] != row["board"]
            or decision["分支"] != row["branch"]
            or decision["預期映像數"] != str(len(releases) * len(PROFILES))
        ):
            raise ValueError(f"完整稽核板卡決策與矩陣不一致：{folder}")
        _, marker = read_values(state / "boards" / f"{folder}.complete")
        board_policy = policy_by_folder[folder]
        if (
            board_policy["source_commit"] != marker["source_commit"]
            or board_policy["build_context_sha256"]
            != marker["build_context_sha256"]
        ):
            raise ValueError(f"完整稽核政策與板級標記不一致：{folder}")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_tree(root: Path) -> None:
    require_real_directory(root, "證據樹目錄")
    directories: list[Path] = []
    for current, names, files in os.walk(root, topdown=True, followlinks=False):
        directory = Path(current)
        directories.append(directory)
        for name in names:
            path = directory / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f"證據樹含非實體目錄：{path}")
        for name in files:
            path = directory / name
            require_real_file_within(path, root, "證據檔案")
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for directory in reversed(directories):
        fsync_directory(directory)


def read_execution_status(output: Path) -> dict[str, str]:
    path = output / "執行狀態.tsv"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"遷移證據缺少執行狀態：{path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        rows = list(reader)
    if not rows or rows[0] != ["欄位", "值"]:
        raise ValueError(f"遷移執行狀態格式錯誤：{path}")
    values: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) != 2 or row[0] in values:
            raise ValueError(f"遷移執行狀態資料錯誤：{path}")
        values[row[0]] = row[1]
    return values


def rollback_from_evidence(output: Path, state: Path) -> None:
    execution = read_execution_status(output)
    expected_count_text = execution.get("預計遷移", execution.get("回滾標記數", ""))
    if not expected_count_text.isdigit():
        raise ValueError(f"遷移證據缺少有效預計數量：{output}")
    expected_count = int(expected_count_text)
    ledger = output / "遷移清冊.tsv"
    backups = require_real_directory(output / "原始標記", "原始標記備份目錄")
    if ledger.is_symlink() or not ledger.is_file():
        raise ValueError(f"遷移證據缺少清冊：{ledger}")
    with ledger.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        expected = [
            "標記",
            "原始標記SHA256",
            "預計遷移後標記SHA256",
            "映像",
            "來源提交",
            "建置內容SHA256",
            "框架日誌狀態",
        ]
        if reader.fieldnames != expected:
            raise ValueError(f"遷移清冊欄位錯誤：{ledger}")
        rows = list(reader)
    if len(rows) != expected_count:
        raise ValueError(
            f"遷移清冊數量不符：預期 {expected_count}，實際 {len(rows)}：{ledger}"
        )
    item_root = require_real_directory(state / "items", "項目標記目錄")
    marker_names: set[str] = set()
    for row in rows:
        marker = Path(row["標記"])
        if (
            not marker.is_absolute()
            or marker.parent.resolve() != item_root
            or marker.name in marker_names
        ):
            raise ValueError(f"遷移清冊標記路徑錯誤或重複：{marker}")
        marker_names.add(marker.name)
        if (
            not SHA256_RE.fullmatch(row["原始標記SHA256"])
            or not SHA256_RE.fullmatch(row["預計遷移後標記SHA256"])
        ):
            raise ValueError(f"遷移清冊標記雜湊格式錯誤：{marker}")
        backup = backups / marker.name
        require_real_file_within(backup, backups, "原始標記備份")
        if file_sha256(backup) != row["原始標記SHA256"]:
            raise ValueError(f"原始標記備份雜湊不符：{backup}")
    backup_names: set[str] = set()
    for backup in backups.iterdir():
        require_real_file_within(backup, backups, "原始標記備份")
        backup_names.add(backup.name)
    if backup_names != marker_names:
        raise ValueError("遷移清冊與原始標記備份集合不一致。")
    for row in rows:
        marker = Path(row["標記"])
        backup = backups / marker.name
        if marker.is_symlink() or not marker.is_file():
            raise ValueError(f"目前標記不是實體一般檔案：{marker}")
        current_digest = file_sha256(marker)
        if current_digest == row["原始標記SHA256"]:
            continue
        if current_digest != row["預計遷移後標記SHA256"]:
            raise ValueError(f"目前標記不是可安全回滾的已知版本：{marker}")
        atomic_write(marker, backup.read_text(encoding="utf-8"))
    atomic_write(
        output / "執行狀態.tsv",
        "欄位\t值\n"
        "狀態\t已回滾\n"
        f"預計遷移\t{expected_count}\n"
        f"回滾時間UTC\t{timestamp()}\n"
        f"回滾標記數\t{len(rows)}\n",
    )


def recover_existing_output(output: Path, state: Path) -> bool:
    if not output.exists() and not output.is_symlink():
        return False
    require_real_directory(output, "既有遷移證據目錄")
    status = read_execution_status(output).get("狀態")
    if status == "成功":
        raise ValueError(f"遷移先前已成功；拒絕未重新稽核即回報成功：{output}")
    if status not in {"準備遷移", "已回滾"}:
        raise ValueError(f"既有遷移證據狀態不可自動復原：{output}：{status}")
    rollback_from_evidence(output, state)
    recovered = output.parent / f"{output.name}.recovered-{uuid.uuid4()}"
    os.replace(output, recovered)
    fsync_directory(output.parent)
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    descriptors: list[int] = []
    try:
        if args.expected_boards <= 0 or args.expected_items <= 0:
            raise ValueError("預期板卡數與映像數必須大於零。")
        state = require_real_directory(args.candidate_state, "候選狀態目錄")
        candidate = require_real_directory(args.candidate_release, "候選發布目錄")
        require_real_directory(args.formal_release, "正式發布目錄")
        matrix_source = require_real_file_within(
            args.matrix,
            args.matrix.parent.resolve(),
            "發布矩陣",
        )
        policy_source = require_real_file_within(
            args.policy_tool,
            args.policy_tool.parent.resolve(),
            "逐板政策工具",
        )
        audit_source = require_real_file_within(
            args.audit_tool,
            args.audit_tool.parent.resolve(),
            "候選稽核工具",
        )
        tool_root = Path(__file__).resolve().parent
        if not args.allow_test_tools and (
            policy_source != tool_root / "generate-bananapi-candidate-input-policy.py"
            or audit_source != tool_root / "audit-bananapi-release-state.py"
        ):
            raise ValueError("正式遷移只允許使用同版受控政策與稽核工具。")
        if args.allow_test_tools and (
            args.expected_boards == 45 or args.expected_items == 444
        ):
            raise ValueError("測試替代工具不得使用正式 45/444 規模。")
        requested_parent = args.output_dir.parent
        resolved_parent = requested_parent.resolve(strict=False)
        resolved_parent.relative_to(state)
        if requested_parent.exists() and (
            requested_parent.is_symlink() or not requested_parent.is_dir()
        ):
            raise ValueError(f"遷移證據上層必須是實體目錄：{requested_parent}")
        requested_parent.mkdir(parents=True, exist_ok=True)
        output_parent = require_real_directory(requested_parent, "遷移證據上層")
        output = output_parent / args.output_dir.name
        descriptors = acquire_candidate_locks(candidate, state)
        if recover_existing_output(output, state):
            print(f"項目完成狀態遷移先前已成功：{output}")
            return 0
        require_empty_directory(state / "raw-items", "原始映像狀態目錄")
        require_empty_directory(state / "transactions", "候選交易目錄")

        run_id = str(uuid.uuid4())
        staging = output_parent / f".{args.output_dir.name}.{run_id}.partial"
        staging.mkdir(mode=0o755)
        snapshot_paths, snapshot_digests = create_input_snapshots(
            staging,
            matrix_source,
            policy_source,
            audit_source,
        )
        verify_input_snapshots(snapshot_paths, snapshot_digests)
        matrix_snapshot = snapshot_paths["矩陣"]
        policy_snapshot = snapshot_paths["政策工具"]
        audit_snapshot = snapshot_paths["稽核工具"]
        matrix = read_matrix(matrix_snapshot)
        matrix_digest = snapshot_digests["矩陣"]
        item_count = sum(
            len(row["releases"].split(",")) * len(PROFILES) for row in matrix
        )
        if len(matrix) != args.expected_boards or item_count != args.expected_items:
            raise ValueError(
                "矩陣未達精確數量："
                f"預期 {args.expected_boards}/{args.expected_items}，"
                f"實際 {len(matrix)}/{item_count}。"
            )
        legacy = collect_legacy_markers(matrix, matrix_digest, state, candidate)
        backups = staging / "原始標記"
        backups.mkdir()
        policy = staging / "逐板候選輸入政策-遷移寬限.tsv"
        audit = staging / "完整性稽核"

        ledger_fields = [
            "標記",
            "原始標記SHA256",
            "預計遷移後標記SHA256",
            "映像",
            "來源提交",
            "建置內容SHA256",
            "框架日誌狀態",
        ]
        ledger_rows: list[dict[str, str]] = []
        originals: dict[Path, str] = {}
        migrated_contents: dict[Path, str] = {}
        migration_at = timestamp()
        for path, _, values in legacy:
            original = path.read_text(encoding="utf-8")
            migrated = migrated_marker_content(original, migration_at)
            originals[path] = original
            migrated_contents[path] = migrated
            backup = backups / path.name
            copy_file_durable(path, backup)
            ledger_rows.append(
                {
                    "標記": str(path),
                    "原始標記SHA256": file_sha256(path),
                    "預計遷移後標記SHA256": text_sha256(migrated),
                    "映像": str(candidate / values["folder"] / values["archive"]),
                    "來源提交": values["source_commit"],
                    "建置內容SHA256": values["build_context_sha256"],
                    "框架日誌狀態": (
                        "具雜湊證據"
                        if values.get("framework_log_sha256")
                        else "舊版未留存"
                    ),
                }
            )
        ledger = staging / "遷移清冊.tsv"
        with ledger.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                ledger_fields,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(ledger_rows)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(backups)
        atomic_write(
            staging / "執行狀態.tsv",
            f"欄位\t值\n狀態\t驗證中\n預計遷移\t{len(legacy)}\n",
        )
        fsync_directory(staging)

        verify_input_snapshots(snapshot_paths, snapshot_digests)
        run_logged(
            [
                sys.executable,
                str(policy_snapshot),
                "--matrix",
                str(matrix_snapshot),
                "--candidate-state",
                str(state),
                "--output",
                str(policy),
                "--allow-legacy-item-status",
            ],
            staging / "寬限政策產生日誌.txt",
        )
        verify_input_snapshots(snapshot_paths, snapshot_digests)
        run_logged(
            [
                sys.executable,
                str(audit_snapshot),
                "--matrix",
                str(matrix_snapshot),
                "--formal-release",
                str(args.formal_release),
                "--candidate",
                f"整併候選|{candidate}|{state}",
                "--candidate-input-policy",
                str(policy),
                "--output-dir",
                str(audit),
                "--verify-digests",
                "--verify-xz",
            ],
            staging / "完整性稽核日誌.txt",
        )
        verify_input_snapshots(snapshot_paths, snapshot_digests)
        validate_audit(audit, matrix, state)
        require_empty_directory(state / "raw-items", "稽核後原始映像狀態目錄")
        require_empty_directory(state / "transactions", "稽核後候選交易目錄")

        atomic_write(
            staging / "執行狀態.tsv",
            "欄位\t值\n"
            "狀態\t準備遷移\n"
            f"預計遷移\t{len(legacy)}\n"
            f"狀態遷移起始時間UTC\t{migration_at}\n"
            f"矩陣SHA256\t{matrix_digest}\n"
            f"遷移工具SHA256\t{snapshot_digests['遷移工具']}\n"
            f"政策工具SHA256\t{snapshot_digests['政策工具']}\n"
            f"稽核工具SHA256\t{snapshot_digests['稽核工具']}\n"
            "完整SHA256與XZ稽核\tyes\n",
        )
        fsync_tree(staging)
        os.replace(staging, output)
        fsync_directory(output_parent)
        snapshot_paths = {
            label: output / path.relative_to(staging)
            for label, path in snapshot_paths.items()
        }
        matrix_snapshot = snapshot_paths["矩陣"]
        policy_snapshot = snapshot_paths["政策工具"]
        audit_snapshot = snapshot_paths["稽核工具"]

        strict_policy = output / "逐板候選輸入政策.tsv"
        final_audit = output / "遷移後結構稽核"
        try:
            for path, original in originals.items():
                if path.read_text(encoding="utf-8") != original:
                    raise ValueError(f"稽核後標記遭異動：{path}")
                atomic_write(path, migrated_contents[path])
            verify_input_snapshots(snapshot_paths, snapshot_digests)
            run_logged(
                [
                    sys.executable,
                    str(policy_snapshot),
                    "--matrix",
                    str(matrix_snapshot),
                    "--candidate-state",
                    str(state),
                    "--output",
                    str(strict_policy),
                ],
                output / "嚴格政策產生日誌.txt",
            )
            verify_input_snapshots(snapshot_paths, snapshot_digests)
            run_logged(
                [
                    sys.executable,
                    str(audit_snapshot),
                    "--matrix",
                    str(matrix_snapshot),
                    "--formal-release",
                    str(args.formal_release),
                    "--candidate",
                    f"整併候選|{candidate}|{state}",
                    "--candidate-input-policy",
                    str(strict_policy),
                    "--output-dir",
                    str(final_audit),
                ],
                output / "遷移後結構稽核日誌.txt",
            )
            verify_input_snapshots(snapshot_paths, snapshot_digests)
            validate_audit(final_audit, matrix, state)
            require_empty_directory(state / "raw-items", "遷移後原始映像狀態目錄")
            require_empty_directory(state / "transactions", "遷移後候選交易目錄")
            fsync_tree(output)
            atomic_write(
                output / "執行狀態.tsv",
                "欄位\t值\n"
                "狀態\t成功\n"
                f"遷移數\t{len(legacy)}\n"
                f"狀態遷移起始時間UTC\t{migration_at}\n"
                f"遷移完成時間UTC\t{timestamp()}\n"
                f"矩陣SHA256\t{matrix_digest}\n"
                f"遷移工具SHA256\t{snapshot_digests['遷移工具']}\n"
                f"政策工具SHA256\t{snapshot_digests['政策工具']}\n"
                f"稽核工具SHA256\t{snapshot_digests['稽核工具']}\n"
                "完整SHA256與XZ稽核\tyes\n"
                "遷移後零待辦\tyes\n",
            )
        except BaseException:
            rollback_from_evidence(output, state)
            raise
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    print(f"項目完成狀態遷移完成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
