#!/usr/bin/env python3
"""由完整候選狀態產生逐板候選輸入政策。"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


POLICY_FIELDS = ["folder", "source_commit", "build_context_sha256"]
MATRIX_FIELDS = ["folder", "board", "branch", "releases"]
PROFILES = ("minimal", "xfce")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class MatrixRow:
    folder: str
    board: str
    branch: str
    releases: tuple[str, ...]

    @property
    def expected_items(self) -> int:
        return len(self.releases) * len(PROFILES)


@dataclass(frozen=True)
class InputIdentity:
    source_commit: str
    build_context_sha256: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="依候選完成標記產生逐板來源提交與建置內容政策。"
    )
    parser.add_argument("--matrix", type=Path, required=True, help="受控矩陣 TSV")
    parser.add_argument(
        "--candidate-state", type=Path, required=True, help="候選狀態根目錄"
    )
    parser.add_argument("--output", type=Path, required=True, help="政策輸出 TSV")
    return parser.parse_args(argv)


def read_matrix(path: Path) -> list[MatrixRow]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            header = next(reader, None)
            if header != MATRIX_FIELDS:
                raise ValueError(f"矩陣欄位錯誤：{path}")
            records = list(reader)
    except OSError as error:
        raise ValueError(f"無法讀取矩陣：{path}：{error}") from error

    if not records:
        raise ValueError(f"矩陣沒有板卡資料：{path}")

    result: list[MatrixRow] = []
    folders: set[str] = set()
    boards: set[str] = set()
    for line_number, fields in enumerate(records, start=2):
        if len(fields) != len(MATRIX_FIELDS):
            raise ValueError(f"矩陣第 {line_number} 列欄位數錯誤：{path}")
        folder, board, branch, releases_text = fields
        if not all(NAME_RE.fullmatch(value) for value in (folder, board, branch)):
            raise ValueError(f"矩陣第 {line_number} 列識別值錯誤：{path}")
        releases = tuple(releases_text.split(","))
        if (
            not releases
            or any(not NAME_RE.fullmatch(release) for release in releases)
            or len(set(releases)) != len(releases)
        ):
            raise ValueError(f"矩陣第 {line_number} 列發行版錯誤：{path}")
        if folder in folders:
            raise ValueError(f"矩陣含重複板目錄：{folder}")
        if board in boards:
            raise ValueError(f"矩陣含重複板卡：{board}")
        folders.add(folder)
        boards.add(board)
        result.append(MatrixRow(folder, board, branch, releases))
    return result


def read_marker(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"完成標記不是一般檔案：{path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"無法讀取完成標記：{path}：{error}") from error
    if not lines:
        raise ValueError(f"完成標記是空檔：{path}")

    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if "=" not in line:
            raise ValueError(f"完成標記第 {line_number} 列格式錯誤：{path}")
        key, value = line.split("=", 1)
        if not KEY_RE.fullmatch(key):
            raise ValueError(f"完成標記第 {line_number} 列欄位錯誤：{path}")
        if key in values:
            raise ValueError(f"完成標記含重複欄位 {key}：{path}")
        values[key] = value
    return values


def require_fields(path: Path, values: dict[str, str], fields: set[str]) -> None:
    missing = sorted(field for field in fields if not values.get(field))
    if missing:
        raise ValueError(f"完成標記缺少必要欄位 {','.join(missing)}：{path}")


def read_identity(path: Path, values: dict[str, str]) -> InputIdentity:
    source_commit = values["source_commit"]
    build_context = values["build_context_sha256"]
    if not COMMIT_RE.fullmatch(source_commit):
        raise ValueError(f"完成標記來源提交格式錯誤：{path}")
    if not SHA256_RE.fullmatch(build_context):
        raise ValueError(f"完成標記建置內容雜湊格式錯誤：{path}")
    return InputIdentity(source_commit, build_context)


def load_board_markers(
    state_root: Path, matrix: list[MatrixRow]
) -> dict[str, InputIdentity]:
    board_directory = state_root / "boards"
    if board_directory.is_symlink() or not board_directory.is_dir():
        raise ValueError(f"候選狀態缺少板級標記目錄：{board_directory}")

    matrix_by_folder = {row.folder: row for row in matrix}
    identities: dict[str, InputIdentity] = {}
    for path in sorted(board_directory.glob("*.complete")):
        values = read_marker(path)
        require_fields(
            path,
            values,
            {
                "source_commit",
                "build_context_sha256",
                "folder",
                "board",
                "branch",
                "images",
                "status",
            },
        )
        folder = values["folder"]
        if path.name != f"{folder}.complete":
            raise ValueError(f"板級標記檔名與內容不一致：{path}")
        if folder in identities:
            raise ValueError(f"候選狀態含重複板級標記：{folder}")
        if folder not in matrix_by_folder:
            raise ValueError(f"候選狀態含矩陣外板級標記：{folder}")

        row = matrix_by_folder[folder]
        if values["board"] != row.board or values["branch"] != row.branch:
            raise ValueError(f"板級標記板卡或分支與矩陣不一致：{path}")
        if values["images"] != str(row.expected_items):
            raise ValueError(f"板級標記映像數與矩陣不一致：{path}")
        if values["status"] != "complete":
            raise ValueError(f"板級標記狀態不是 complete：{path}")
        identities[folder] = read_identity(path, values)

    expected_folders = set(matrix_by_folder)
    missing = [row.folder for row in matrix if row.folder not in identities]
    extra = sorted(set(identities) - expected_folders)
    if extra:
        raise ValueError(f"候選狀態含矩陣外板級標記：{','.join(extra)}")
    if missing:
        raise ValueError(f"候選狀態缺少板級標記：{','.join(missing)}")
    return identities


def load_item_identities(
    state_root: Path,
    matrix: list[MatrixRow],
    board_identities: dict[str, InputIdentity],
) -> None:
    item_directory = state_root / "items"
    if item_directory.is_symlink() or not item_directory.is_dir():
        raise ValueError(f"候選狀態缺少項目標記目錄：{item_directory}")

    matrix_by_folder = {row.folder: row for row in matrix}
    found: dict[tuple[str, str, str], tuple[Path, InputIdentity]] = {}
    for path in sorted(item_directory.glob("*.complete")):
        values = read_marker(path)
        require_fields(
            path,
            values,
            {
                "source_commit",
                "build_context_sha256",
                "folder",
                "board",
                "branch",
                "release",
                "profile",
                "status",
            },
        )
        folder = values["folder"]
        if folder not in matrix_by_folder:
            raise ValueError(f"候選狀態含矩陣外項目標記：{path}")
        row = matrix_by_folder[folder]
        release = values["release"]
        profile = values["profile"]
        expected_name = f"{folder}-{release}-{profile}.complete"
        if path.name != expected_name:
            raise ValueError(f"項目標記檔名與內容不一致：{path}")
        if values["board"] != row.board or values["branch"] != row.branch:
            raise ValueError(f"項目標記板卡或分支與矩陣不一致：{path}")
        if release not in row.releases or profile not in PROFILES:
            raise ValueError(f"候選狀態含矩陣外發行版或設定檔：{path}")
        if values["status"] != "complete":
            raise ValueError(f"項目標記狀態不是 complete：{path}")

        key = (folder, release, profile)
        if key in found:
            raise ValueError(f"候選狀態含重複項目標記：{'/'.join(key)}")
        identity = read_identity(path, values)
        if identity != board_identities[folder]:
            raise ValueError(f"項目與板級標記的候選輸入不一致：{path}")
        found[key] = (path, identity)

    for row in matrix:
        expected = {
            (row.folder, release, profile)
            for release in row.releases
            for profile in PROFILES
        }
        actual = {key for key in found if key[0] == row.folder}
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if extra:
            detail = ",".join("/".join(key) for key in extra)
            raise ValueError(f"板卡含矩陣外項目標記：{row.folder}：{detail}")
        if missing:
            detail = ",".join("/".join(key) for key in missing)
            raise ValueError(f"板卡缺少完整項目標記：{row.folder}：{detail}")
        if len(actual) != row.expected_items:
            raise ValueError(
                f"板卡項目數錯誤：{row.folder}："
                f"預期 {row.expected_items}，實際 {len(actual)}"
            )
        item_identities = {found[key][1] for key in actual}
        if len(item_identities) != 1:
            raise ValueError(f"板卡項目標記含多組候選輸入：{row.folder}")


def generate_policy(matrix: list[MatrixRow], state_root: Path) -> list[dict[str, str]]:
    board_identities = load_board_markers(state_root, matrix)
    load_item_identities(state_root, matrix, board_identities)
    return [
        {
            "folder": row.folder,
            "source_commit": board_identities[row.folder].source_commit,
            "build_context_sha256": board_identities[
                row.folder
            ].build_context_sha256,
        }
        for row in matrix
    ]


def write_policy_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ValueError(f"政策輸出不是一般檔案：{path}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, POLICY_FIELDS, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        matrix = read_matrix(args.matrix)
        rows = generate_policy(matrix, args.candidate_state)
        write_policy_atomic(args.output, rows)
    except (OSError, ValueError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    print(f"已產生逐板候選輸入政策：{args.output}（{len(rows)} 板）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
