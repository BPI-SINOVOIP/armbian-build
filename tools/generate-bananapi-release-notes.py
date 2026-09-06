#!/usr/bin/env python3
"""依受控矩陣為完整候選映像產生繁體中文逐板發行說明。"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


MATRIX_FIELDS = ["folder", "board", "branch", "releases"]
PROFILE_SUFFIXES = {
    "minimal": "minimal",
    "xfce": "xfce_desktop",
}
PROFILE_LABELS = {
    "minimal": "CLI（最小化系統）",
    "xfce": "XFCE（桌面系統）",
}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
KERNEL_RE = r"[A-Za-z0-9][A-Za-z0-9.+~-]*"
NOTE_NAME = "Release-Notes-zh-TW.md"


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
class Artifact:
    release: str
    profile: str
    kernel_version: str
    archive: Path
    sidecar: Path


class ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"錯誤：命令列參數無效：{message}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ChineseArgumentParser(
        description="依受控矩陣產生繁體中文逐板候選映像發行說明。"
    )
    parser.add_argument("--matrix", type=Path, required=True, help="受控矩陣 TSV")
    parser.add_argument(
        "--candidate-release", type=Path, required=True, help="候選發布根目錄"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="原子取代既有逐板發行說明",
    )
    return parser.parse_args(argv)


def require_regular_file(path: Path, description: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{description}必須是實體一般檔案：{path}")


def read_matrix(path: Path) -> list[MatrixRow]:
    require_regular_file(path, "矩陣")
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            header = next(reader, None)
            if header != MATRIX_FIELDS:
                raise ValueError(f"矩陣欄位錯誤：{path}")
            records = list(reader)
    except (OSError, UnicodeError) as error:
        raise ValueError(f"無法讀取矩陣：{path}：{error}") from error

    if not records:
        raise ValueError(f"矩陣沒有板卡資料：{path}")

    rows: list[MatrixRow] = []
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
        rows.append(MatrixRow(folder, board, branch, releases))
    return rows


def validate_candidate_root(root: Path, matrix: list[MatrixRow]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"候選發布根目錄必須是實體目錄：{root}")

    expected = {row.folder for row in matrix}
    found: set[str] = set()
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ValueError(f"無法讀取候選發布根目錄：{root}：{error}") from error

    for entry in entries:
        if entry.is_symlink():
            raise ValueError(f"候選發布根目錄不得含符號連結：{entry}")
        if entry.is_dir():
            if entry.name not in expected:
                raise ValueError(f"候選發布含矩陣外板目錄：{entry}")
            found.add(entry.name)
        elif not entry.is_file():
            raise ValueError(f"候選發布根目錄含不支援的項目：{entry}")

    missing = [row.folder for row in matrix if row.folder not in found]
    if missing:
        raise ValueError(f"候選發布缺少矩陣板目錄：{','.join(missing)}")


def board_token(board: str) -> str:
    return board[0].upper() + board[1:]


def artifact_pattern(row: MatrixRow, release: str, profile: str) -> re.Pattern[str]:
    token = re.escape(board_token(row.board))
    suffix = re.escape(PROFILE_SUFFIXES[profile])
    return re.compile(
        rf"^.+_{token}_{re.escape(release)}_{re.escape(row.branch)}_"
        rf"(?P<kernel>{KERNEL_RE})_{suffix}\.img\.xz$"
    )


def validate_board_directory(root: Path, row: MatrixRow) -> list[Artifact]:
    directory = root / row.folder
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"板目錄必須是實體目錄：{directory}")

    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ValueError(f"無法讀取板目錄：{directory}：{error}") from error

    for entry in entries:
        if entry.is_symlink():
            raise ValueError(f"板目錄不得含符號連結：{entry}")
        if not entry.is_file():
            raise ValueError(f"板目錄第一層只允許實體一般檔案：{entry}")

    archives = [entry for entry in entries if entry.name.endswith(".img.xz")]
    sidecars = [entry for entry in entries if entry.name.endswith(".img.xz.sha")]
    sidecar_by_name = {entry.name: entry for entry in sidecars}
    artifacts: list[Artifact] = []
    matched_archives: set[Path] = set()

    for release in row.releases:
        for profile in PROFILE_SUFFIXES:
            pattern = artifact_pattern(row, release, profile)
            matches = [
                (archive, match)
                for archive in archives
                if (match := pattern.fullmatch(archive.name)) is not None
            ]
            item = f"{row.folder} / {release} / {profile} / {row.branch}"
            if not matches:
                raise ValueError(f"缺少矩陣映像：{item}")
            if len(matches) != 1:
                raise ValueError(f"矩陣映像重複匹配：{item}，實際 {len(matches)}")

            archive, match = matches[0]
            sidecar_name = f"{archive.name}.sha"
            if sidecar_name not in sidecar_by_name:
                raise ValueError(f"映像缺少同名 SHA 邊車：{archive}.sha")
            matched_archives.add(archive)
            artifacts.append(
                Artifact(
                    release=release,
                    profile=profile,
                    kernel_version=match.group("kernel"),
                    archive=archive,
                    sidecar=sidecar_by_name[sidecar_name],
                )
            )

    if len(archives) != row.expected_images:
        raise ValueError(
            f"板目錄映像數量錯誤：{row.folder}，"
            f"預期 {row.expected_images}，實際 {len(archives)}"
        )
    if len(sidecars) != row.expected_images:
        raise ValueError(
            f"板目錄 SHA 邊車數量錯誤：{row.folder}，"
            f"預期 {row.expected_images}，實際 {len(sidecars)}"
        )

    unmatched_archives = sorted(
        (archive for archive in archives if archive not in matched_archives),
        key=lambda path: path.name,
    )
    expected_sidecars = {f"{archive.name}.sha" for archive in archives}
    unmatched_sidecars = sorted(set(sidecar_by_name) - expected_sidecars)
    if unmatched_archives:
        raise ValueError(f"映像未唯一對應矩陣：{unmatched_archives[0]}")
    if unmatched_sidecars:
        raise ValueError(
            f"SHA 邊車沒有同名映像：{directory / unmatched_sidecars[0]}"
        )
    return artifacts


def render_release_note(row: MatrixRow, artifacts: list[Artifact]) -> str:
    release_list = "、".join(f"`{release}`" for release in row.releases)
    lines = [
        f"# Banana Pi 候選映像發行說明：`{row.folder}`",
        "",
        "## 候選識別",
        "",
        f"- 板卡目錄：`{row.folder}`",
        f"- Armbian 板卡代號：`{row.board}`",
        f"- 核心分支：`{row.branch}`",
        f"- 發行版：{release_list}",
        f"- 映像總數：{len(artifacts)}",
        "- 候選層級：軟體候選，尚待逐板硬體 Gate。",
        "",
        "## 映像清單",
        "",
        "| 發行版 | 分支 | 變體 | 實際核心版本 | 映像檔 | SHA-256 邊車 |",
        "|---|---|---|---|---|---|",
    ]
    for artifact in artifacts:
        lines.append(
            "| "
            f"`{artifact.release}` | `{row.branch}` | "
            f"{PROFILE_LABELS[artifact.profile]} | "
            f"`{artifact.kernel_version}` | `{artifact.archive.name}` | "
            f"`{artifact.sidecar.name}` |"
        )

    lines.extend(
        [
            "",
            "CLI 變體提供最小化命令列環境；XFCE 變體提供桌面環境。請依實際部署需求選擇，不應由檔名推定周邊硬體已通過驗證。",
            "",
            "## 檔案驗證",
            "",
            "請在本板目錄執行以下命令，先核對 SHA-256，再檢查 XZ 串流：",
            "",
            "```bash",
            "set -Eeuo pipefail",
            "for file in ./*.img.xz; do",
            "    sha256sum -c -- \"${file}.sha\"",
            "    xz -t -- \"${file}\"",
            "done",
            "```",
            "",
            "任一命令失敗都必須停止燒錄，重新取得完整映像與同名邊車檔。",
            "",
            "## 燒錄與首次啟動",
            "",
            "1. 先以 `lsblk` 核對目標儲存裝置；錯誤的 `/dev/sdX` 會破壞其他磁碟資料。",
            "2. 選定一個映像後解壓縮，並把實際檔名代入下列命令。",
            "",
            "```bash",
            "xz -dk -- \"<映像檔>.img.xz\"",
            "sudo dd if=\"<映像檔>.img\" of=/dev/sdX bs=4M conv=fsync status=progress",
            "sync",
            "```",
            "",
            "3. 完全斷電後插入儲存媒體，依板卡硬體文件選用可用的序列主控台或顯示輸出，再重新上電。",
            "4. 若首次啟動流程要求建立帳號、變更密碼或調整語系，請依畫面完成；不要預設所有映像的互動流程相同。",
            "5. 登入後記錄下列基本軟體資訊，並連同序列日誌保存為該板硬體 Gate 的證據。",
            "",
            "```bash",
            "uname -a",
            "cat /etc/os-release",
            "systemctl --failed",
            "dmesg --level=emerg,alert,crit,err",
            "```",
            "",
            "## 限制與逐板硬體 Gate",
            "",
            "本說明只依受控矩陣與候選檔名記錄軟體產物，不構成開機、記憶體、儲存、網路、無線、顯示、USB、GPIO、休眠、重啟或長時間壓力測試的實機通過證據。",
            "",
            "每一種板卡修訂、記憶體與儲存料號都必須完成可追溯的逐板硬體 Gate；未附實機編號、測試條件、完整日誌與結果時，一律視為尚未通過。",
            "",
            "`legacy`、`vendor`、`current` 與 `edge` 是建置分支識別，不等同支援年限、穩定等級或硬體相容保證。",
            "",
            "本候選不得據此宣稱已通過 L3、量產核准或對外散布授權；相關 Gate 與授權必須由發布責任人依獨立證據正式核准。",
            "",
        ]
    )
    return "\n".join(lines)


def validate_note_targets(
    root: Path, matrix: list[MatrixRow], replace: bool
) -> None:
    for row in matrix:
        path = root / row.folder / NOTE_NAME
        if path.is_symlink():
            raise ValueError(f"發行說明不得為符號連結：{path}")
        if path.exists():
            if not path.is_file():
                raise ValueError(f"發行說明路徑不是一般檔案：{path}")
            if not replace:
                raise ValueError(f"發行說明已存在；如需取代請加入 --replace：{path}")


def write_text_atomic(path: Path, content: str, replace: bool) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ValueError(
                    f"發行說明已存在；如需取代請加入 --replace：{path}"
                ) from error
            temporary.unlink()
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
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
        validate_candidate_root(args.candidate_release, matrix)
        artifacts_by_folder = {
            row.folder: validate_board_directory(args.candidate_release, row)
            for row in matrix
        }
        validate_note_targets(args.candidate_release, matrix, args.replace)
        for row in matrix:
            note = args.candidate_release / row.folder / NOTE_NAME
            content = render_release_note(row, artifacts_by_folder[row.folder])
            write_text_atomic(note, content, args.replace)
    except (OSError, ValueError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1

    action = "已原子取代" if args.replace else "已原子建立"
    print(f"{action}繁體中文逐板候選發行說明：{len(matrix)} 板")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
