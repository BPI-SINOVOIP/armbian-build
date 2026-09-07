#!/usr/bin/env python3
"""嚴格翻譯本輪簡版中文候選說明；英文正文依專案特定授權產生。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


CHINESE_NAME = "Release-Notes-zh-TW.md"
ENGLISH_NAME = "Release-Notes-English.md"
MATRIX_FIELDS = ["folder", "board", "branch", "releases"]
NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
PROFILE_SUFFIXES = ("minimal", "xfce_desktop")

# 全文錨定，固定正文不使用萬用匹配，避免漏掉新增或改寫的限制。
CHINESE_PATTERN = re.compile(
    rf"# (?P<board>{NAME}) 最新內部候選映像\n\n"
    r"BSP 整合基準提交：`(?P<bsp_commit>[0-9a-f]{40})`\n\n"
    r"建置工具與最終來源提交：`(?P<source_commit>[0-9a-f]{40})`\n\n"
    r"建置矩陣 SHA-256：`(?P<matrix_sha256>[0-9a-f]{64})`\n\n"
    rf"核心分支：`(?P<branch>{NAME})`\n\n"
    rf"發行版：`(?P<releases>{NAME}(?:,{NAME})*)`\n\n"
    r"本目錄包含 (?P<count>8|10) 個映像，分別為精簡命令列版與 XFCE 桌面版。"
    r"所有映像均由上述最終來源提交執行 `compile\.sh build`；"
    r"第一個 Trixie 精簡映像另強制清理並重建 U-Boot、Kernel、ATF 與 Crust 等實際適用元件。"
    r"同板後續映像只可沿用本輪已驗證的元件快取。\n\n"
    r"每個映像均通過原始映像唯讀內容與板型檢查、SHA-256 及 XZ 串流完整性檢查。"
    r"這是軟體候選結果，不代表未執行的實機、全介面、長時間壓力、量產或再散布門檻已通過。"
    r"燒錄前請再次核對同名 `\.img\.xz\.sha`。\n"
)

# 僅此英文產生範本屬本輪中文規則的特定例外。
ENGLISH_TEMPLATE = """# {board} Latest Internal Candidate Images

BSP integration baseline commit: `{bsp_commit}`

Build tools and final source commit: `{source_commit}`

Build matrix SHA-256: `{matrix_sha256}`

Kernel branch: `{branch}`

Releases: `{releases}`

This directory contains {count} images, in minimal command-line and XFCE desktop variants. All images were built by running `compile.sh build` from the final source commit above; for the first Trixie minimal image, applicable components such as U-Boot, Kernel, ATF, and Crust were also forcibly cleaned and rebuilt. Subsequent images for the same board may only reuse component caches verified in this build round.

Each image has passed read-only checks of the raw image contents and board type, SHA-256 verification, and XZ stream integrity checks. This is a software candidate result and does not mean that unperformed hardware, full-interface, long-duration stress, mass-production, or redistribution gates have passed. Before flashing, recheck the matching `.img.xz.sha` file.
"""


@dataclass(frozen=True)
class MatrixRow:
    folder: str
    board: str
    branch: str
    releases: tuple[str, ...]

    @property
    def expected_images(self) -> int:
        return len(self.releases) * len(PROFILE_SUFFIXES)


@dataclass(frozen=True)
class PreparedNote:
    target: Path
    content: bytes
    previous: bytes | None


class ChineseHelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or "用法：")


class ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "錯誤：命令列參數無效，請以 --help 檢視用法。\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ChineseArgumentParser(
        description="逐板嚴格翻譯本輪中文簡版發行說明，整批檢查後才寫入英文檔。",
        add_help=False,
        formatter_class=ChineseHelpFormatter,
    )
    options = parser.add_argument_group("選項")
    options.add_argument("-h", "--help", action="help", help="顯示用法後離開")
    options.add_argument("--matrix", type=Path, required=True, help="受控矩陣 TSV")
    options.add_argument(
        "--candidate-release", type=Path, required=True, help="候選發布根目錄"
    )
    modes = options.add_mutually_exclusive_group()
    modes.add_argument(
        "--replace", action="store_true", help="只原子取代內容不同的英文檔"
    )
    modes.add_argument(
        "--check", action="store_true", help="唯讀檢查所有英文檔與中文翻譯一致"
    )
    return parser.parse_args(argv)


def reject_symlinks(path: Path) -> None:
    absolute = path.absolute()
    for component in (*reversed(absolute.parents), absolute):
        if component.is_symlink():
            raise ValueError(f"路徑不得含符號連結：{component}")


def require_regular_file(path: Path) -> None:
    reject_symlinks(path)
    if not path.is_file():
        raise ValueError(f"路徑必須是實體一般檔案：{path}")


def read_matrix(path: Path) -> tuple[list[MatrixRow], str]:
    require_regular_file(path)
    data = path.read_bytes()
    try:
        records = list(csv.reader(io.StringIO(data.decode("utf-8")), delimiter="\t"))
    except (UnicodeError, csv.Error) as error:
        raise ValueError(f"矩陣不是有效的 UTF-8 TSV：{path}") from error
    if not records or records[0] != MATRIX_FIELDS or len(records) == 1:
        raise ValueError(f"矩陣欄位錯誤或沒有板卡資料：{path}")

    rows: list[MatrixRow] = []
    folders: set[str] = set()
    boards: set[str] = set()
    for number, fields in enumerate(records[1:], start=2):
        if len(fields) != len(MATRIX_FIELDS):
            raise ValueError(f"矩陣第 {number} 列欄位數錯誤")
        folder, board, branch, releases_text = fields
        releases = tuple(releases_text.split(","))
        if not all(re.fullmatch(NAME, value) for value in (folder, board, branch, *releases)):
            raise ValueError(f"矩陣第 {number} 列識別值錯誤")
        if len(set(releases)) != len(releases):
            raise ValueError(f"矩陣第 {number} 列含重複發行版")
        if folder in folders or board in boards:
            raise ValueError(f"矩陣第 {number} 列含重複板目錄或板卡")
        folders.add(folder)
        boards.add(board)
        rows.append(MatrixRow(folder, board, branch, releases))
    return rows, hashlib.sha256(data).hexdigest()


def validate_candidate_root(root: Path, rows: list[MatrixRow]) -> None:
    reject_symlinks(root)
    if not root.is_dir():
        raise ValueError(f"候選發布根目錄必須是實體目錄：{root}")
    expected = {row.folder for row in rows}
    found: set[str] = set()
    for entry in sorted(root.iterdir()):
        reject_symlinks(entry)
        if entry.is_dir():
            if entry.name not in expected:
                raise ValueError(f"候選發布含矩陣外板目錄：{entry}")
            found.add(entry.name)
        elif not entry.is_file():
            raise ValueError(f"候選發布根目錄含不支援的項目：{entry}")
    if missing := expected - found:
        raise ValueError(f"候選發布缺少矩陣板目錄：{','.join(sorted(missing))}")


def validate_board_directory(root: Path, row: MatrixRow) -> None:
    directory = root / row.folder
    reject_symlinks(directory)
    if not directory.is_dir():
        raise ValueError(f"板目錄必須是實體目錄：{directory}")
    entries = sorted(directory.iterdir())
    for entry in entries:
        require_regular_file(entry)
    archives = {entry.name for entry in entries if entry.name.endswith(".img.xz")}
    sidecars = {entry.name for entry in entries if entry.name.endswith(".img.xz.sha")}
    if len(archives) != row.expected_images:
        raise ValueError(f"板目錄映像數量不符矩陣：{directory}")
    if sidecars != {f"{archive}.sha" for archive in archives}:
        raise ValueError(f"映像與同名 SHA 邊車不一致：{directory}")

    # 僅檢查檔名與結構，不讀取映像或邊車內容，不重跑 SHA/XZ 核驗。
    token = row.board[0].upper() + row.board[1:]
    matched: set[str] = set()
    for release in row.releases:
        for suffix in PROFILE_SUFFIXES:
            pattern = re.compile(
                rf".+_{re.escape(token)}_{re.escape(release)}_{re.escape(row.branch)}_"
                rf"[A-Za-z0-9][A-Za-z0-9.+~-]*_{suffix}\.img\.xz"
            )
            matches = {name for name in archives if pattern.fullmatch(name)}
            if len(matches) != 1:
                raise ValueError(f"映像未唯一對應矩陣：{row.folder}/{release}/{suffix}")
            matched.update(matches)
    if matched != archives:
        raise ValueError(f"板目錄含矩陣外映像：{directory}")


def translate_note(data: bytes, row: MatrixRow, matrix_sha256: str) -> bytes:
    try:
        source = data.decode("utf-8")
    except UnicodeError as error:
        raise ValueError(f"中文說明不是有效的 UTF-8：{row.folder}") from error
    match = CHINESE_PATTERN.fullmatch(source)
    if match is None:
        raise ValueError(f"中文說明不符合已知完整簡版範本，含未知文字或限制：{row.folder}")
    metadata = match.groupdict()
    expected = {
        "board": row.board,
        "branch": row.branch,
        "releases": ",".join(row.releases),
        "count": str(row.expected_images),
        "matrix_sha256": matrix_sha256,
    }
    for field, value in expected.items():
        if metadata[field] != value:
            raise ValueError(f"中文說明不符矩陣：{row.folder}，欄位 {field}")
    return ENGLISH_TEMPLATE.format(**metadata).encode("utf-8")


def prepare_notes(
    root: Path, rows: list[MatrixRow], matrix_sha256: str, *, replace: bool, check: bool
) -> list[PreparedNote]:
    validate_candidate_root(root, rows)
    notes: list[PreparedNote] = []
    for row in rows:
        validate_board_directory(root, row)
        source = root / row.folder / CHINESE_NAME
        require_regular_file(source)
        content = translate_note(source.read_bytes(), row, matrix_sha256)
        target = source.with_name(ENGLISH_NAME)
        reject_symlinks(target)
        previous = None
        if target.exists():
            require_regular_file(target)
            previous = target.read_bytes()
        if check and previous != content:
            raise ValueError(f"英文說明缺少或與中文翻譯不一致：{target}")
        if not check and not replace and previous is not None and previous != content:
            raise ValueError(f"英文說明已存在且內容不同；如需取代請加入 --replace：{target}")
        notes.append(PreparedNote(target, content, previous))
    return notes


def write_note_atomic(note: PreparedNote) -> None:
    path = note.target
    if path.name != ENGLISH_NAME:
        raise ValueError(f"只允許寫入英文說明：{path}")
    reject_symlinks(path)
    if note.previous is not None:
        require_regular_file(path)
        if path.read_bytes() != note.previous:
            raise ValueError(f"英文說明在前置檢查後已改變：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{ENGLISH_NAME}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(note.content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        if note.previous is not None:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ValueError(f"英文說明在前置檢查後已出現，拒絕覆寫：{path}") from error
            temporary.unlink()
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, matrix_sha256 = read_matrix(args.matrix)
        notes = prepare_notes(
            args.candidate_release, rows, matrix_sha256,
            replace=args.replace, check=args.check,
        )
        if args.check:
            print(f"唯讀檢查通過：{len(notes)} 板英文說明與中文翻譯一致。")
            return 0
        changed = [note for note in notes if note.previous != note.content]
        for note in changed:
            write_note_atomic(note)
    except ValueError as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"錯誤：檔案操作失敗（errno={error.errno}）：{error.filename}", file=sys.stderr)
        return 1
    print(
        f"英文說明處理完成：{len(notes)} 板，原子寫入 {len(changed)} 份，"
        f"相同略過 {len(notes) - len(changed)} 份；中文檔未修改。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
