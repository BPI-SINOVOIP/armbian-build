#!/usr/bin/env python3
"""盤點 Banana Pi 發布映像並產生防重建置帳本。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


RELEASE_ORDER = {
    "trixie": 0,
    "bookworm": 1,
    "jammy": 2,
    "noble": 3,
    "resolute": 4,
}
PROFILE_ORDER = {"minimal": 0, "xfce": 1}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MatrixRow:
    folder: str
    board: str
    branch: str
    releases: tuple[str, ...]

    @property
    def expected(self) -> int:
        return len(self.releases) * 2


@dataclass(frozen=True)
class ItemKey:
    folder: str
    board: str
    branch: str
    release: str
    profile: str

    @property
    def value(self) -> str:
        return "/".join(
            (self.folder, self.board, self.branch, self.release, self.profile)
        )


@dataclass(frozen=True)
class CandidateSource:
    name: str
    release_root: Path
    state_root: Path


@dataclass(frozen=True)
class Artifact:
    key: ItemKey
    source_kind: str
    source_name: str
    archive: Path
    digest: str
    source_commit: str
    build_context: str


@dataclass(frozen=True)
class RawArtifact:
    key: ItemKey
    source: CandidateSource
    image: Path
    digest: str
    source_commit: str
    build_context: str
    marker: Path


@dataclass(frozen=True)
class CandidateBoard:
    source: CandidateSource
    artifacts: dict[ItemKey, Artifact]
    board_marker_valid: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="盤點既有 Banana Pi 映像，先建立帳本再決定是否建置。"
    )
    parser.add_argument("--matrix", type=Path, required=True, help="受控矩陣 TSV")
    parser.add_argument(
        "--formal-release", type=Path, required=True, help="既有正式發布根目錄"
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="名稱|發布根目錄|狀態根目錄",
        help="可重複指定候選來源",
    )
    parser.add_argument(
        "--target-source-commit",
        default="",
        help="只把指定來源提交的候選視為本輪已完成",
    )
    parser.add_argument(
        "--target-build-context",
        default="",
        help="只把指定建置內容雜湊的候選視為本輪已完成",
    )
    parser.add_argument(
        "--reuse-formal",
        action="store_true",
        help="明確允許舊正式映像抵銷本輪待辦；完整重建預設禁止",
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="盤點輸出目錄")
    parser.add_argument(
        "--verify-digests",
        action="store_true",
        help="重新讀取全部映像計算 SHA-256",
    )
    parser.add_argument(
        "--verify-xz", action="store_true", help="重新執行全部 XZ 串流檢查"
    )
    return parser.parse_args()


def read_matrix(path: Path) -> list[MatrixRow]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    expected_fields = ["folder", "board", "branch", "releases"]
    if not rows or list(rows[0]) != expected_fields:
        raise ValueError(f"矩陣欄位錯誤：{path}")
    result: list[MatrixRow] = []
    folders: set[str] = set()
    boards: set[str] = set()
    for raw in rows:
        folder = raw["folder"]
        board = raw["board"]
        if folder in folders or board in boards:
            raise ValueError(f"矩陣含重複板卡或目錄：{folder} / {board}")
        folders.add(folder)
        boards.add(board)
        releases = tuple(raw["releases"].split(","))
        if not releases or any(release not in RELEASE_ORDER for release in releases):
            raise ValueError(f"矩陣含未知發行版：{folder}")
        result.append(MatrixRow(folder, board, raw["branch"], releases))
    return result


def parse_candidate(raw: str) -> CandidateSource:
    fields = raw.split("|", 2)
    if len(fields) != 3 or not all(fields):
        raise ValueError(f"候選來源格式錯誤：{raw}")
    return CandidateSource(fields[0], Path(fields[1]), Path(fields[2]))


def item_keys(row: MatrixRow) -> list[ItemKey]:
    return [
        ItemKey(row.folder, row.board, row.branch, release, profile)
        for release in row.releases
        for profile in ("minimal", "xfce")
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value
    return values


def check_archive(
    archive: Path,
    expected_digest: str = "",
    *,
    verify_digests: bool,
    verify_xz: bool,
) -> str:
    sidecar = Path(f"{archive}.sha")
    if not archive.is_file() or not sidecar.is_file():
        raise ValueError(f"映像或同名 SHA 檔不存在：{archive}")
    lines = sidecar.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError(f"SHA 檔不是單列：{sidecar}")
    fields = lines[0].split()
    if len(fields) != 2 or not SHA256_RE.fullmatch(fields[0]):
        raise ValueError(f"SHA 檔格式錯誤：{sidecar}")
    digest, referenced = fields
    if referenced.lstrip("*") != archive.name:
        raise ValueError(f"SHA 檔指向錯誤檔名：{sidecar}")
    if expected_digest and digest != expected_digest:
        raise ValueError(f"完成標記與 SHA 檔雜湊不一致：{archive}")
    if verify_digests and sha256_file(archive) != digest:
        raise ValueError(f"映像 SHA-256 驗證失敗：{archive}")
    if verify_xz:
        subprocess.run(["xz", "-t", str(archive)], check=True)
    return digest


def profile_matches(filename: str, profile: str) -> bool:
    desktop = "_xfce_desktop" in filename
    if profile == "xfce":
        return desktop
    return not desktop and "_desktop" not in filename and filename.endswith(".img.xz")


def find_formal_artifact(
    root: Path,
    key: ItemKey,
    *,
    verify_digests: bool,
    verify_xz: bool,
) -> Artifact | None:
    directory = root / key.folder
    if not directory.is_dir():
        return None
    token = key.board[0].upper() + key.board[1:]
    needle = f"_{token}_{key.release}_{key.branch}_"
    matches = sorted(
        path
        for path in directory.glob("*.img.xz")
        if needle in path.name and profile_matches(path.name, key.profile)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"正式目錄的唯一鍵有多個映像：{key.value}")
    archive = matches[0]
    digest = check_archive(
        archive, verify_digests=verify_digests, verify_xz=verify_xz
    )
    return Artifact(key, "正式", "既有正式發布", archive, digest, "未知", "未知")


def find_candidate_archive(source: CandidateSource, folder: str, archive: str) -> Path:
    direct = source.release_root / folder / archive
    staged = sorted(source.release_root.glob(f".staging-{folder}-*/{archive}"))
    matches = [path for path in (direct, *staged) if path.is_file()]
    if len(matches) != 1:
        raise ValueError(
            f"候選完成標記找不到唯一映像：{source.name} / {folder} / {archive}"
        )
    return matches[0]


def find_candidate_artifact(
    source: CandidateSource,
    key: ItemKey,
    *,
    verify_digests: bool,
    verify_xz: bool,
) -> Artifact | None:
    marker = source.state_root / "items" / (
        f"{key.folder}-{key.release}-{key.profile}.complete"
    )
    if not marker.is_file():
        return None
    values = read_key_values(marker)
    expected = {
        "folder": key.folder,
        "board": key.board,
        "branch": key.branch,
        "release": key.release,
        "profile": key.profile,
    }
    for field, value in expected.items():
        if values.get(field) != value:
            raise ValueError(f"候選完成標記欄位不符：{marker} / {field}")
    digest = values.get("sha256", "")
    if not SHA256_RE.fullmatch(digest):
        raise ValueError(f"候選完成標記缺少有效雜湊：{marker}")
    archive_name = values.get("archive", "")
    if not archive_name or Path(archive_name).name != archive_name:
        raise ValueError(f"候選完成標記映像名稱錯誤：{marker}")
    archive = find_candidate_archive(source, key.folder, archive_name)
    check_archive(
        archive,
        digest,
        verify_digests=verify_digests,
        verify_xz=verify_xz,
    )
    log = Path(values.get("log", ""))
    log_digest = values.get("log_sha256", "")
    if not log.is_file() or not SHA256_RE.fullmatch(log_digest):
        raise ValueError(f"候選完成標記缺少有效日誌證據：{marker}")
    if verify_digests and sha256_file(log) != log_digest:
        raise ValueError(f"候選日誌 SHA-256 驗證失敗：{log}")
    return Artifact(
        key,
        "候選",
        source.name,
        archive,
        digest,
        values.get("source_commit", "未知"),
        values.get("build_context_sha256", "未知"),
    )


def find_candidate_raw_artifact(
    source: CandidateSource,
    key: ItemKey,
    *,
    verify_digests: bool,
) -> RawArtifact | None:
    prefix = source.state_root / "raw-items" / (
        f"{key.folder}-{key.release}-{key.profile}"
    )
    markers = [
        marker
        for marker in (Path(f"{prefix}.ready"), Path(f"{prefix}.compressing"))
        if marker.is_file()
    ]
    if not markers:
        return None
    if len(markers) != 1:
        raise ValueError(f"原始映像有多個壓縮狀態標記：{key.value}")
    marker = markers[0]
    values = read_key_values(marker)
    expected = {
        "folder": key.folder,
        "board": key.board,
        "branch": key.branch,
        "release": key.release,
        "profile": key.profile,
    }
    for field, value in expected.items():
        if values.get(field) != value:
            raise ValueError(f"原始映像標記欄位不符：{marker} / {field}")
    image = Path(values.get("raw_image", ""))
    digest = values.get("raw_sha256", "")
    raw_root = (source.state_root / "raw-images").resolve()
    try:
        image.resolve().relative_to(raw_root)
    except (OSError, ValueError) as error:
        raise ValueError(f"原始映像不在受控目錄：{marker}") from error
    if not image.is_file() or not SHA256_RE.fullmatch(digest):
        raise ValueError(f"原始映像或雜湊無效：{marker}")
    sidecar = Path(f"{image}.sha")
    if not sidecar.is_file():
        raise ValueError(f"原始映像缺少同名 SHA 檔：{image}")
    lines = sidecar.read_text(encoding="utf-8").splitlines()
    fields = lines[0].split() if len(lines) == 1 else []
    if (
        len(fields) != 2
        or fields[0] != digest
        or fields[1].lstrip("*") != image.name
    ):
        raise ValueError(f"原始映像 SHA 檔格式錯誤：{sidecar}")
    if verify_digests and sha256_file(image) != digest:
        raise ValueError(f"原始映像 SHA-256 驗證失敗：{image}")
    log = Path(values.get("log", ""))
    log_digest = values.get("log_sha256", "")
    if not log.is_file() or not SHA256_RE.fullmatch(log_digest):
        raise ValueError(f"原始映像標記缺少有效日誌證據：{marker}")
    if verify_digests and sha256_file(log) != log_digest:
        raise ValueError(f"原始映像日誌 SHA-256 驗證失敗：{log}")
    return RawArtifact(
        key,
        source,
        image,
        digest,
        values.get("source_commit", "未知"),
        values.get("build_context_sha256", "未知"),
        marker,
    )


def board_marker_valid(source: CandidateSource, row: MatrixRow) -> bool:
    marker = source.state_root / "boards" / f"{row.folder}.complete"
    if not marker.is_file():
        return False
    values = read_key_values(marker)
    return all(
        (
            values.get("folder") == row.folder,
            values.get("board") == row.board,
            values.get("branch") == row.branch,
            values.get("images") == str(row.expected),
            values.get("status") == "complete",
        )
    )


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    matrix = read_matrix(args.matrix)
    candidates = [parse_candidate(raw) for raw in args.candidate]
    for path in (args.matrix, args.formal_release):
        if not path.exists():
            raise ValueError(f"必要路徑不存在：{path}")
    for source in candidates:
        if not source.release_root.is_dir() or not source.state_root.is_dir():
            raise ValueError(f"候選來源路徑不存在：{source.name}")

    formal: dict[ItemKey, Artifact] = {}
    candidate_boards: dict[tuple[str, str], CandidateBoard] = {}
    candidate_artifacts: dict[tuple[str, ItemKey], Artifact] = {}
    raw_artifacts: dict[tuple[str, ItemKey], RawArtifact] = {}
    for row in matrix:
        keys = item_keys(row)
        for key in keys:
            artifact = find_formal_artifact(
                args.formal_release,
                key,
                verify_digests=args.verify_digests,
                verify_xz=args.verify_xz,
            )
            if artifact:
                formal[key] = artifact
        for source in candidates:
            artifacts: dict[ItemKey, Artifact] = {}
            for key in keys:
                artifact_accepted = False
                artifact = find_candidate_artifact(
                    source,
                    key,
                    verify_digests=args.verify_digests,
                    verify_xz=args.verify_xz,
                )
                if artifact:
                    if (
                        args.target_source_commit
                        and artifact.source_commit != args.target_source_commit
                    ):
                        artifact = None
                    elif (
                        args.target_build_context
                        and artifact.build_context != args.target_build_context
                    ):
                        artifact = None
                    else:
                        artifacts[key] = artifact
                        candidate_artifacts[(source.name, key)] = artifact
                        artifact_accepted = True
                raw_artifact = find_candidate_raw_artifact(
                    source,
                    key,
                    verify_digests=args.verify_digests,
                )
                if raw_artifact:
                    if (
                        args.target_source_commit
                        and raw_artifact.source_commit != args.target_source_commit
                    ):
                        continue
                    if (
                        args.target_build_context
                        and raw_artifact.build_context != args.target_build_context
                    ):
                        continue
                    if artifact_accepted:
                        # 壓縮完成標記先落盤、原始標記再清除的短暫交疊是合法狀態。
                        continue
                    raw_artifacts[(source.name, key)] = raw_artifact
            candidate_boards[(source.name, row.folder)] = CandidateBoard(
                source, artifacts, board_marker_valid(source, row)
            )

    ledger_rows: list[dict[str, str]] = []
    board_rows: list[dict[str, str]] = []
    queue_rows: list[dict[str, str]] = []
    chosen_candidates: set[tuple[str, ItemKey]] = set()
    pending_candidates: set[tuple[str, ItemKey]] = set()

    for row in matrix:
        keys = item_keys(row)
        complete_candidates = [
            board
            for (name, folder), board in candidate_boards.items()
            if folder == row.folder
            and board.board_marker_valid
            and len(board.artifacts) == row.expected
        ]
        pending_candidates_for_board = [
            board
            for (name, folder), board in candidate_boards.items()
            if folder == row.folder
            and not board.board_marker_valid
            and len(board.artifacts) == row.expected
        ]
        formal_complete = all(key in formal for key in keys)
        if len(complete_candidates) > 1:
            raise ValueError(f"同一板卡有多個完整候選來源：{row.folder}")
        if len(pending_candidates_for_board) > 1:
            raise ValueError(f"同一板卡有多個待驗證完整候選：{row.folder}")

        if complete_candidates:
            board = complete_candidates[0]
            decision = "沿用完整候選"
            selected = board.source.name
            for key in keys:
                artifact = board.artifacts[key]
                chosen_candidates.add((board.source.name, key))
                ledger_rows.append(ledger_row(artifact, "已驗證候選", "不再建置"))
        elif pending_candidates_for_board:
            board = pending_candidates_for_board[0]
            decision = "候選只補整板驗證"
            selected = board.source.name
            for key in keys:
                artifact = board.artifacts[key]
                pending_candidates.add((board.source.name, key))
                ledger_rows.append(
                    ledger_row(artifact, "候選待整板驗證", "不得重新編譯")
                )
            queue_rows.append(
                queue_row(row, "", "", "補整板驗證", "全部映像已存在")
            )
        elif args.reuse_formal and formal_complete:
            decision = "沿用既有正式"
            selected = "既有正式發布"
            for key in keys:
                ledger_rows.append(ledger_row(formal[key], "沿用既有正式", "不再建置"))
        else:
            selected_items = 0
            for key in keys:
                candidate_options = [
                    artifact
                    for (name, item_key), artifact in candidate_artifacts.items()
                    if item_key == key
                ]
                if len(candidate_options) > 1:
                    raise ValueError(f"缺板項目有多個候選來源：{key.value}")
                if candidate_options:
                    artifact = candidate_options[0]
                    chosen_candidates.add((artifact.source_name, key))
                    selected_items += 1
                    ledger_rows.append(ledger_row(artifact, "本輪已完成", "不再建置"))
                elif raw_options := [
                    artifact
                    for (name, item_key), artifact in raw_artifacts.items()
                    if item_key == key
                ]:
                    if len(raw_options) > 1:
                        raise ValueError(f"待壓縮項目有多個候選來源：{key.value}")
                    raw = raw_options[0]
                    selected_items += 1
                    ledger_rows.append(raw_ledger_row(raw))
                    queue_rows.append(
                        queue_row(
                            row,
                            key.release,
                            key.profile,
                            "等待壓縮",
                            str(raw.marker),
                        )
                    )
                elif args.reuse_formal and key in formal:
                    ledger_rows.append(
                        ledger_row(formal[key], "沿用既有正式", "不再建置")
                    )
                else:
                    ledger_rows.append(missing_ledger_row(key, key in formal))
                    queue_rows.append(
                        queue_row(
                            row,
                            key.release,
                            key.profile,
                            "建置缺少項目",
                            key.value,
                        )
                    )
            if selected_items:
                decision = "保留部分候選並補缺"
                selected = "本輪候選與待建置"
            else:
                decision = "本輪全板待建"
                selected = "待建置"
        board_rows.append(
            {
                "板目錄": row.folder,
                "板卡": row.board,
                "分支": row.branch,
                "預期映像數": str(row.expected),
                "決策": decision,
                "選用來源": selected,
            }
        )

    candidate_rows: list[dict[str, str]] = []
    for (source_name, key), artifact in sorted(
        candidate_artifacts.items(), key=lambda entry: entry[0][1].value
    ):
        identity = (source_name, key)
        if identity in chosen_candidates:
            disposition = "採用"
        elif identity in pending_candidates:
            disposition = "待整板驗證"
        else:
            disposition = "未採用部分候選"
        candidate_rows.append(
            {
                "唯一鍵": key.value,
                "候選來源": source_name,
                "處置": disposition,
                "映像": str(artifact.archive),
                "SHA256": artifact.digest,
                "來源提交": artifact.source_commit,
                "建置內容雜湊": artifact.build_context,
            }
        )

    matrix_folders = {row.folder for row in matrix}
    extra_rows: list[dict[str, str]] = []
    for directory in sorted(args.formal_release.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if directory.name in matrix_folders:
            continue
        archives = sorted(directory.glob("*.img.xz"))
        extra_rows.append(
            {
                "板目錄": directory.name,
                "映像數": str(len(archives)),
                "處置": "不屬於目前矩陣，先保留待封存",
                "路徑": str(directory),
            }
        )

    interrupted_rows: list[dict[str, str]] = []
    seen_interrupted: set[Path] = set()
    for source in candidates:
        output_images = source.state_root.parents[1] / "images"
        patterns = (
            (source.release_root, "*.partial-*", "未完成壓縮檔"),
            (output_images, "*.img", "未完成原始映像"),
            (source.state_root / "markers", "*.marker", "未完成觸發標記"),
        )
        for directory, pattern, kind in patterns:
            if not directory.is_dir():
                continue
            for item in sorted(directory.rglob(pattern)):
                if not item.is_file() or item in seen_interrupted:
                    continue
                seen_interrupted.add(item)
                interrupted_rows.append(
                    {
                        "候選來源": source.name,
                        "類別": kind,
                        "大小bytes": str(item.stat().st_size),
                        "處置": "不得視為完成；正式收斂後才依清冊移除",
                        "路徑": str(item),
                    }
                )

    stale_staging_rows: list[dict[str, str]] = []
    for directory in sorted(args.formal_release.glob(".staging-*")):
        if not directory.is_dir():
            continue
        files = [path for path in directory.rglob("*") if path.is_file()]
        stale_staging_rows.append(
            {
                "目錄": directory.name,
                "檔案數": str(len(files)),
                "大小bytes": str(sum(path.stat().st_size for path in files)),
                "處置": "先保留；正式收斂後依清冊處理",
                "路徑": str(directory),
            }
        )

    board_order = {row.folder: index for index, row in enumerate(matrix)}
    queue_rows.sort(
        key=lambda row: (
            {"補整板驗證": 0, "等待壓縮": 1, "建置缺少項目": 2}.get(
                row["動作"], 9
            ),
            board_order[row["板目錄"]],
            RELEASE_ORDER.get(row["發行版"], -1),
            PROFILE_ORDER.get(row["類型"], -1),
        )
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_tsv(
        args.output_dir / "映像盤點.tsv",
        [
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
        ledger_rows,
    )
    write_tsv(
        args.output_dir / "板卡決策.tsv",
        ["板目錄", "板卡", "分支", "預期映像數", "決策", "選用來源"],
        board_rows,
    )
    write_tsv(
        args.output_dir / "待辦佇列.tsv",
        ["板目錄", "板卡", "分支", "發行版", "類型", "動作", "原因"],
        queue_rows,
    )
    write_tsv(
        args.output_dir / "候選處置.tsv",
        ["唯一鍵", "候選來源", "處置", "映像", "SHA256", "來源提交", "建置內容雜湊"],
        candidate_rows,
    )
    write_tsv(
        args.output_dir / "矩陣外項目.tsv",
        ["板目錄", "映像數", "處置", "路徑"],
        extra_rows,
    )
    write_tsv(
        args.output_dir / "中止產物.tsv",
        ["候選來源", "類別", "大小bytes", "處置", "路徑"],
        interrupted_rows,
    )
    write_tsv(
        args.output_dir / "舊暫存目錄.tsv",
        ["目錄", "檔案數", "大小bytes", "處置", "路徑"],
        stale_staging_rows,
    )
    write_summary(
        args.output_dir / "盤點摘要.md",
        matrix,
        ledger_rows,
        candidate_rows,
        queue_rows,
        extra_rows,
        interrupted_rows,
        stale_staging_rows,
        args,
    )
    print(f"盤點完成：{args.output_dir}")
    return 0


def ledger_row(artifact: Artifact, status: str, disposition: str) -> dict[str, str]:
    key = artifact.key
    return {
        "唯一鍵": key.value,
        "板目錄": key.folder,
        "板卡": key.board,
        "分支": key.branch,
        "發行版": key.release,
        "類型": key.profile,
        "狀態": status,
        "選用來源": artifact.source_name,
        "映像": str(artifact.archive),
        "SHA256": artifact.digest,
        "來源提交": artifact.source_commit,
        "建置內容雜湊": artifact.build_context,
        "處置": disposition,
    }


def missing_ledger_row(key: ItemKey, has_formal_baseline: bool) -> dict[str, str]:
    return {
        "唯一鍵": key.value,
        "板目錄": key.folder,
        "板卡": key.board,
        "分支": key.branch,
        "發行版": key.release,
        "類型": key.profile,
        "狀態": "本輪缺少",
        "選用來源": "無",
        "映像": "",
        "SHA256": "",
        "來源提交": "",
        "建置內容雜湊": "",
        "處置": (
            "列入待建佇列；舊正式僅作待取代基線"
            if has_formal_baseline
            else "列入待建佇列"
        ),
    }


def raw_ledger_row(artifact: RawArtifact) -> dict[str, str]:
    key = artifact.key
    return {
        "唯一鍵": key.value,
        "板目錄": key.folder,
        "板卡": key.board,
        "分支": key.branch,
        "發行版": key.release,
        "類型": key.profile,
        "狀態": "待壓縮",
        "選用來源": artifact.source.name,
        "映像": str(artifact.image),
        "SHA256": artifact.digest,
        "來源提交": artifact.source_commit,
        "建置內容雜湊": artifact.build_context,
        "處置": "不得重新編譯；由壓縮工作續作",
    }


def queue_row(
    row: MatrixRow, release: str, profile: str, action: str, reason: str
) -> dict[str, str]:
    return {
        "板目錄": row.folder,
        "板卡": row.board,
        "分支": row.branch,
        "發行版": release,
        "類型": profile,
        "動作": action,
        "原因": reason,
    }


def write_summary(
    path: Path,
    matrix: list[MatrixRow],
    ledger: list[dict[str, str]],
    candidates: list[dict[str, str]],
    queue: list[dict[str, str]],
    extras: list[dict[str, str]],
    interrupted: list[dict[str, str]],
    stale_staging: list[dict[str, str]],
    args: argparse.Namespace,
) -> None:
    status_counts: dict[str, int] = {}
    for row in ledger:
        status_counts[row["狀態"]] = status_counts.get(row["狀態"], 0) + 1
    disposition_counts: dict[str, int] = {}
    for row in candidates:
        disposition_counts[row["處置"]] = disposition_counts.get(row["處置"], 0) + 1
    integrity = "已重新計算 SHA-256" if args.verify_digests else "沿用既有 SHA-256 證據"
    if args.verify_xz:
        integrity += "，並重新執行 XZ 串流檢查"
    else:
        integrity += "；本次未重跑 XZ 串流檢查"
    lines = [
        "# Banana Pi 映像防重盤點摘要",
        "",
        f"目前矩陣包含 {len(matrix)} 塊板、{len(ledger)} 個唯一映像項目。",
        "",
        "## 結果",
        "",
    ]
    for status in sorted(status_counts):
        lines.append(f"- {status}：{status_counts[status]}")
    lines.extend(["", "## 候選處置", ""])
    for disposition in sorted(disposition_counts):
        lines.append(f"- {disposition}：{disposition_counts[disposition]}")
    lines.extend(
        [
            "",
            "## 待辦",
            "",
            f"待辦列數：{len(queue)}。執行端依架構、發行版、類型與板卡排序；已完成或待壓縮項目不得再次呼叫建置。",
            "",
            "## 矩陣外項目",
            "",
            f"矩陣外板目錄：{len(extras)}。盤點只記錄，不會自動刪除或封存。",
            "",
            "## 中止現場",
            "",
            f"中止產物：{len(interrupted)}；舊暫存目錄：{len(stale_staging)}。這些項目不得視為完成，也不會在盤點階段自動刪除。",
            "",
            "## 完整性範圍",
            "",
            f"{integrity}。舊正式映像預設只作待取代基線，不會抵銷本輪待辦；候選項目須有完成標記、日誌、映像與同名 SHA 檔。",
            "",
            "本工具只盤點與產生帳本，不會啟動建置、替換正式目錄或刪除檔案。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
