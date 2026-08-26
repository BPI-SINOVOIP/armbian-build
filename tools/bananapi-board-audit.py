#!/usr/bin/env python3
"""產生 Banana Pi 全板卡可追溯盤點與最佳化狀態報告。"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
BOARD_DIR = REPO_DIR / "config" / "boards"
STATUS_FILE = REPO_DIR / "config" / "bananapi-optimization-status.json"
BOARD_SUFFIXES = ("conf", "csc", "wip", "eos")
STATUS_NAMES = {
    "conf": "正式",
    "csc": "社群",
    "wip": "開發中",
    "eos": "停止支援",
}
LEVEL_NAMES = {
    "L0": "已盤點",
    "L1": "可建置",
    "L2": "軟體候選",
    "L3": "實機候選",
    "L4": "功能最佳化",
    "L5": "發布候選",
}
ARMHF_FAMILIES = {
    "rockchip",
    "sun6i",
    "sun7i",
    "sun8i",
    "mt7623",
    "sunplus-sp7021-bpi",
}
RISCV64_FAMILIES = {"spacemit", "spacemit-k3-bpi"}
REQUIRED_FIELDS = ("BOARD_NAME", "BOARDFAMILY", "KERNEL_TARGET")
RECOMMENDED_FIELDS = (
    "BOARD_VENDOR",
    "BOARD_MAINTAINER",
    "INTRODUCED",
    "KERNEL_TEST_TARGET",
)
ASSIGN_RE = re.compile(
    r"^(?:(?:export|declare)\s+(?:-[a-zA-Z]+\s+)*)?"
    r"([A-Z_][A-Z0-9_]*)=(.*)$",
    re.MULTILINE,
)
SOURCE_RE = re.compile(
    r'^source\s+["\']?\$\{?SRC\}?/config/boards/'
    r'([^"\'\s]+\.(?:conf|csc|wip|eos))["\']?',
    re.MULTILINE,
)


@dataclass(frozen=True)
class Board:
    board_id: str
    status: str
    path: Path
    fields: dict[str, str]
    batch: str
    level: str
    basis: str
    findings: tuple[str, ...]

    @property
    def family(self) -> str:
        return self.fields.get("BOARDFAMILY", "")

    @property
    def architecture(self) -> str:
        if self.family in ARMHF_FAMILIES:
            return "armhf"
        if self.family in RISCV64_FAMILIES:
            return "riscv64"
        return "arm64"

    @property
    def targets(self) -> tuple[str, ...]:
        value = self.fields.get("KERNEL_TARGET", "")
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @property
    def preferred_target(self) -> str:
        for candidate in ("current", "edge", "vendor", "legacy"):
            if candidate in self.targets:
                return candidate
        return self.targets[0] if self.targets else ""

    @property
    def has_video(self) -> bool:
        return self.fields.get("HAS_VIDEO_OUTPUT", "yes") != "no"

    @property
    def next_gate(self) -> str:
        if self.status == "eos":
            return "保留最後可用基線，不列入新發布"
        if self.level == "L0":
            if self.status == "wip":
                return "確認建置鏈並建立 Trixie CLI 候選"
            return "建立 Trixie CLI 並完成離線守門"
        if self.level == "L1":
            return "完成映像內容與來源同一性守門"
        if self.level == "L2":
            return "執行 UART、啟動與基本周邊實機驗證"
        if self.level == "L3":
            return "補齊加速、I/O、多板與長時間測試"
        if self.level == "L4":
            return "補齊樣本數、冷啟動與發布門檻"
        return "維持回歸並追蹤已知限制"


def strip_value(raw: str) -> str:
    """移除最外層引號與未引用的行尾註解。"""
    value = raw.strip()
    if value and value[0] not in ('"', "'"):
        comment = re.search(r"\s#", value)
        if comment:
            value = value[: comment.start()].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return value


def parse_assignments(text: str) -> dict[str, str]:
    """解析頂層大寫變數；同一欄位以最後一次設定為準。"""
    return {match.group(1): strip_value(match.group(2)) for match in ASSIGN_RE.finditer(text)}


def effective_fields(path: Path, visited: set[Path] | None = None) -> dict[str, str]:
    """唯讀解析板卡繼承鏈，不執行具有副作用的 shell。"""
    visited = set() if visited is None else visited
    resolved = path.resolve()
    if resolved in visited:
        return {}
    visited.add(resolved)
    text = path.read_text(encoding="utf-8", errors="replace")
    fields: dict[str, str] = {}
    for match in SOURCE_RE.finditer(text):
        parent = BOARD_DIR / match.group(1)
        if parent.is_file():
            fields.update(effective_fields(parent, visited))
    fields.update(parse_assignments(text))
    return fields


def board_paths() -> list[Path]:
    """列出 Banana Pi 命名板卡及命名例外 AI2N。"""
    paths: set[Path] = set()
    for suffix in BOARD_SUFFIXES:
        paths.update(BOARD_DIR.glob(f"bananapi*.{suffix}"))
        ai2n = BOARD_DIR / f"bpi-ai2n.{suffix}"
        if ai2n.is_file():
            paths.add(ai2n)
    return sorted(paths, key=lambda path: path.stem)


def load_status() -> dict[str, object]:
    """讀取受版本控制的最佳化狀態登錄檔。"""
    return json.loads(STATUS_FILE.read_text(encoding="utf-8"))


def batch_index(status: dict[str, object]) -> dict[str, str]:
    """將批次清單轉為板卡到批次的唯一索引。"""
    index: dict[str, str] = {}
    duplicates: list[str] = []
    for batch, board_ids in status["batches"].items():
        for board_id in board_ids:
            if board_id in index:
                duplicates.append(board_id)
            index[board_id] = batch
    if duplicates:
        raise ValueError(f"狀態登錄有重複板卡：{', '.join(sorted(set(duplicates)))}")
    return index


def collect_boards() -> tuple[list[Board], list[str]]:
    """合併實際板卡設定與人工證據狀態，並回傳一致性錯誤。"""
    status_data = load_status()
    batches = batch_index(status_data)
    evidence = status_data.get("evidence", {})
    open_findings = status_data.get("open_findings", {})
    paths = board_paths()
    actual_ids = {path.stem for path in paths}
    registered_ids = set(batches)
    errors: list[str] = []
    if actual_ids - registered_ids:
        errors.append("狀態登錄缺少：" + ", ".join(sorted(actual_ids - registered_ids)))
    if registered_ids - actual_ids:
        errors.append("狀態登錄含不存在板卡：" + ", ".join(sorted(registered_ids - actual_ids)))

    boards: list[Board] = []
    for path in paths:
        board_id = path.stem
        fields = effective_fields(path)
        missing = [field for field in REQUIRED_FIELDS if not fields.get(field)]
        if missing:
            errors.append(f"{board_id} 缺少必要欄位：{', '.join(missing)}")
        item = evidence.get(board_id, {})
        level = item.get("level", "L0")
        if level not in LEVEL_NAMES:
            errors.append(f"{board_id} 證據等級無效：{level}")
        basis = item.get("basis", "僅完成本分支靜態盤點，尚未以本次來源重建")
        boards.append(
            Board(
                board_id=board_id,
                status=path.suffix.lstrip("."),
                path=path,
                fields=fields,
                batch=batches.get(board_id, "?"),
                level=level,
                basis=basis,
                findings=tuple(open_findings.get(board_id, [])),
            )
        )
    return boards, errors


def field_gaps(board: Board) -> list[str]:
    """列出建議欄位缺口；不把 WIP 警告誤作正式支援。"""
    return [field for field in RECOMMENDED_FIELDS if not board.fields.get(field)]


def tsv_text(boards: list[Board]) -> str:
    """產生可供工具繼續處理的 TSV。"""
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(
        (
            "board_id",
            "status",
            "board_name",
            "family",
            "architecture",
            "kernel_targets",
            "preferred_target",
            "video_output",
            "batch",
            "evidence_level",
            "bootconfig",
            "boot_fdt_file",
            "field_gaps",
            "next_gate",
        )
    )
    for board in boards:
        writer.writerow(
            (
                board.board_id,
                board.status,
                board.fields.get("BOARD_NAME", ""),
                board.family,
                board.architecture,
                ",".join(board.targets),
                board.preferred_target,
                "yes" if board.has_video else "no",
                board.batch,
                board.level,
                board.fields.get("BOOTCONFIG", ""),
                board.fields.get("BOOT_FDT_FILE", ""),
                ",".join(field_gaps(board)),
                board.next_gate,
            )
        )
    return stream.getvalue()


def markdown_text(boards: list[Board], updated: str) -> str:
    """產生以證據等級為核心的繁體中文盤點報告。"""
    status_counts = Counter(board.status for board in boards)
    level_counts = Counter(board.level for board in boards)
    lines = [
        "# Banana Pi 全系列最佳化盤點",
        "",
        f"更新日期：{updated}",
        "",
        "本報告由 `tools/bananapi-board-audit.py` 從板卡設定與受版本控制的證據登錄檔產生。建置成功、裝置節點存在及歷史映像均不會自動提升證據等級。",
        "",
        "## 摘要",
        "",
        f"- 板卡總數：{len(boards)}。",
        f"- 正式 `.conf`：{status_counts['conf']}；社群 `.csc`：{status_counts['csc']}；開發中 `.wip`：{status_counts['wip']}；停止支援 `.eos`：{status_counts['eos']}。",
        "- 證據分布：" + "；".join(f"{level} {level_counts[level]}" for level in LEVEL_NAMES) + "。",
        "- 未取得實機的板卡最高只能標示 L2；目前 L3／L4 只沿用已納入 Git 的 M4 Zero／M4 Berry 證據。",
        "",
        "## 板卡矩陣",
        "",
        "| 板卡 | 層級 | 名稱 | 家族 | 架構 | 核心目標 | 顯示 | 批次 | 證據 | 下一門檻 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for board in boards:
        targets = ",".join(board.targets) or "未宣告"
        lines.append(
            f"| `{board.board_id}` | {STATUS_NAMES[board.status]} | "
            f"{board.fields.get('BOARD_NAME', '未宣告')} | `{board.family or '未宣告'}` | "
            f"`{board.architecture}` | `{targets}` | {'是' if board.has_video else '否'} | "
            f"{board.batch} | {board.level} {LEVEL_NAMES.get(board.level, '未知')} | {board.next_gate} |"
        )

    lines.extend(["", "## 目前開放問題", ""])
    findings = [(board, finding) for board in boards for finding in board.findings]
    if findings:
        for board, finding in findings:
            lines.append(f"- `{board.board_id}`：{finding}。")
    else:
        lines.append("- 無已登錄的開放問題。")

    lines.extend(["", "## 欄位品質", ""])
    boards_with_gaps = [(board, field_gaps(board)) for board in boards if field_gaps(board)]
    if boards_with_gaps:
        for board, gaps in boards_with_gaps:
            lines.append(f"- `{board.board_id}`：缺少建議欄位 `{', '.join(gaps)}`。")
    else:
        lines.append("- 所有板卡均具備建議欄位。")

    lines.extend(
        [
            "",
            "## 判讀限制",
            "",
            "- 核心目標來自板卡設定；實際版本仍須以每次建置中繼資料為準。",
            "- `.conf`、`.csc`、`.wip` 與 `.eos` 是維護層級，不是實機通過證明。",
            "- vendor BSP、PAC、簽章與預建韌體須另外保存來源及授權邊界。",
            "- 每次完成建置或實機驗證後，必須更新證據登錄檔並重新產生本報告。",
            "",
        ]
    )
    return "\n".join(lines)


def write_output(path: Path, content: str) -> None:
    """以明確 UTF-8 編碼寫入可重現輸出。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="寫入 Markdown 報告")
    parser.add_argument("--tsv", type=Path, help="寫入 TSV 資料")
    parser.add_argument("--check", action="store_true", help="只執行一致性檢查")
    args = parser.parse_args()

    boards, errors = collect_boards()
    if errors:
        for error in errors:
            print(f"錯誤：{error}", file=sys.stderr)
        return 1
    if len(boards) != 48:
        print(f"錯誤：預期 48 個板卡，實際為 {len(boards)}", file=sys.stderr)
        return 1

    status_data = load_status()
    if args.markdown:
        write_output(args.markdown, markdown_text(boards, status_data["updated"]))
    if args.tsv:
        write_output(args.tsv, tsv_text(boards))
    if not args.markdown and not args.tsv and not args.check:
        print(tsv_text(boards), end="")
    print(f"盤點通過：{len(boards)} 個板卡，狀態登錄無漏板", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
