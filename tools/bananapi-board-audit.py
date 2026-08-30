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
MARKDOWN_REPORT = REPO_DIR / "docs" / "bananapi-family-optimization-audit-20260826.md"
TSV_REPORT = REPO_DIR / "docs" / "evidence" / "bananapi-family-optimization" / "board-audit-20260826.tsv"
BOARD_SUFFIXES = ("conf", "csc", "wip", "eos")
EXPECTED_BATCHES = frozenset(("A", "B", "C", "D", "E", "F", "G", "R"))
EXPECTED_BOARD_IDS = frozenset(
    (
        "bananapi",
        "bananapi6204",
        "bananapiaim7",
        "bananapicm2",
        "bananapicm4io",
        "bananapicm5pro",
        "bananapicm6",
        "bananapif2p",
        "bananapif2s",
        "bananapif3",
        "bananapiforge1",
        "bananapim1plus",
        "bananapim1super",
        "bananapim2",
        "bananapim2berry",
        "bananapim2c",
        "bananapim2magic",
        "bananapim2plus",
        "bananapim2pro",
        "bananapim2s",
        "bananapim2ultra",
        "bananapim2zero",
        "bananapim3",
        "bananapim4",
        "bananapim4berry",
        "bananapim4super",
        "bananapim4zero",
        "bananapim5",
        "bananapim5pro",
        "bananapim6",
        "bananapim64",
        "bananapim7",
        "bananapip2pro",
        "bananapip2zero",
        "bananapipro",
        "bananapir1",
        "bananapir2",
        "bananapir2pro",
        "bananapir3",
        "bananapir3mini",
        "bananapir4",
        "bananapir4lite",
        "bananapir4pro",
        "bananapir64",
        "bananapism10",
        "bananapiw2",
        "bananapiw3",
        "bpi-ai2n",
    )
)
EXPECTED_VARIANT_IDS = frozenset(("bananapim4zeroemac",))
STATUS_NAMES = {
    "conf": "正式",
    "csc": "社群",
    "wip": "開發中",
    "eos": "停止支援",
}
LEVEL_NAMES = {
    "L0": "已盤點",
    "L1": "元件可建置",
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
    next_gate: str
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


def all_board_paths() -> list[Path]:
    """列出 Banana Pi 命名板卡、功能變體及命名例外 AI2N。"""
    paths: set[Path] = set()
    for suffix in BOARD_SUFFIXES:
        paths.update(BOARD_DIR.glob(f"bananapi*.{suffix}"))
        ai2n = BOARD_DIR / f"bpi-ai2n.{suffix}"
        if ai2n.is_file():
            paths.add(ai2n)
    return sorted(paths, key=lambda path: path.stem)


def board_paths() -> list[Path]:
    """列出產品板卡，功能變體不併入產品數量。"""
    return [path for path in all_board_paths() if path.stem not in EXPECTED_VARIANT_IDS]


def variant_paths() -> dict[str, Path]:
    """列出獨立追蹤的功能變體設定。"""
    return {path.stem: path for path in all_board_paths() if path.stem in EXPECTED_VARIANT_IDS}


def load_status() -> dict[str, object]:
    """讀取受版本控制的最佳化狀態登錄檔。"""
    return json.loads(STATUS_FILE.read_text(encoding="utf-8"))


def key_set_errors(name: str, value: object, expected: frozenset[str]) -> list[str]:
    """要求物件鍵集合逐項吻合，禁止缺項、額外項與默認補值。"""
    if not isinstance(value, dict):
        return [f"{name} 必須是物件"]
    actual = set(value)
    errors: list[str] = []
    if expected - actual:
        errors.append(f"{name} 缺少：{', '.join(sorted(expected - actual))}")
    if actual - expected:
        errors.append(f"{name} 含未登錄項目：{', '.join(sorted(actual - expected))}")
    return errors


def registry_errors(status: dict[str, object]) -> list[str]:
    """驗證 schema v3、產品板卡狀態及獨立功能變體。"""
    errors: list[str] = []
    if status.get("schema_version") != 3:
        errors.append("狀態登錄 schema_version 必須是 3")
    if not isinstance(status.get("updated"), str) or not status["updated"]:
        errors.append("狀態登錄 updated 必須是非空字串")

    batches = status.get("batches")
    errors.extend(key_set_errors("batches 批次", batches, EXPECTED_BATCHES))
    if isinstance(batches, dict):
        for batch, board_ids in batches.items():
            if not isinstance(board_ids, list) or not all(isinstance(item, str) for item in board_ids):
                errors.append(f"批次 {batch} 必須是板卡字串陣列")

    for section in ("evidence", "next_gates", "open_findings"):
        errors.extend(key_set_errors(section, status.get(section), EXPECTED_BOARD_IDS))

    variants = status.get("variants")
    errors.extend(key_set_errors("variants 功能變體", variants, EXPECTED_VARIANT_IDS))
    if isinstance(variants, dict):
        for variant_id, item in variants.items():
            if not isinstance(item, dict):
                errors.append(f"{variant_id} 功能變體必須是物件")
                continue
            base_board = item.get("base_board")
            if base_board not in EXPECTED_BOARD_IDS:
                errors.append(f"{variant_id} 基礎板卡無效：{base_board}")
            if item.get("level") not in LEVEL_NAMES:
                errors.append(f"{variant_id} 證據等級無效：{item.get('level')}")
            for field, label in (("basis", "證據依據"), ("next_gate", "下一門檻")):
                if not isinstance(item.get(field), str) or not item[field]:
                    errors.append(f"{variant_id} {label}必須是非空字串")
            findings = item.get("open_findings")
            if not isinstance(findings, list) or not all(
                isinstance(finding, str) and finding for finding in findings
            ):
                errors.append(f"{variant_id} 開放問題必須是非空字串陣列或空陣列")

    evidence = status.get("evidence")
    if isinstance(evidence, dict):
        for board_id, item in evidence.items():
            if not isinstance(item, dict):
                errors.append(f"{board_id} evidence 必須是物件")
                continue
            if item.get("level") not in LEVEL_NAMES:
                errors.append(f"{board_id} 證據等級無效：{item.get('level')}")
            if not isinstance(item.get("basis"), str) or not item["basis"]:
                errors.append(f"{board_id} 證據依據必須是非空字串")

    next_gates = status.get("next_gates")
    if isinstance(next_gates, dict):
        for board_id, value in next_gates.items():
            if not isinstance(value, str) or not value:
                errors.append(f"{board_id} 下一門檻必須是非空字串")

    findings = status.get("open_findings")
    if isinstance(findings, dict):
        for board_id, values in findings.items():
            if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
                errors.append(f"{board_id} 開放問題必須是非空字串陣列或空陣列")
    return errors


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
    actual = set(index)
    if actual != EXPECTED_BOARD_IDS:
        missing = ", ".join(sorted(EXPECTED_BOARD_IDS - actual))
        extra = ", ".join(sorted(actual - EXPECTED_BOARD_IDS))
        raise ValueError(f"批次板卡集合不符；缺少：{missing or '無'}；多餘：{extra or '無'}")
    return index


def collect_boards() -> tuple[list[Board], list[str]]:
    """合併實際板卡設定與人工證據狀態，並回傳一致性錯誤。"""
    status_data = load_status()
    errors = registry_errors(status_data)
    try:
        batches = batch_index(status_data)
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        batches = {}
        errors.append(str(error))
    evidence = status_data.get("evidence") if isinstance(status_data.get("evidence"), dict) else {}
    next_gates = status_data.get("next_gates") if isinstance(status_data.get("next_gates"), dict) else {}
    open_findings = status_data.get("open_findings") if isinstance(status_data.get("open_findings"), dict) else {}
    all_paths = all_board_paths()
    duplicate_ids = sorted(
        board_id
        for board_id, count in Counter(path.stem for path in all_paths).items()
        if count > 1
    )
    if duplicate_ids:
        errors.append(f"板卡同時具有多種維護層級：{', '.join(duplicate_ids)}")
    paths = [path for path in all_paths if path.stem not in EXPECTED_VARIANT_IDS]
    actual_ids = {path.stem for path in paths}
    if actual_ids != EXPECTED_BOARD_IDS:
        errors.append(
            "板卡設定集合不符；缺少："
            + (", ".join(sorted(EXPECTED_BOARD_IDS - actual_ids)) or "無")
            + "；多餘："
            + (", ".join(sorted(actual_ids - EXPECTED_BOARD_IDS)) or "無")
        )

    variants = {
        path.stem: path for path in all_paths if path.stem in EXPECTED_VARIANT_IDS
    }
    actual_variant_ids = set(variants)
    if actual_variant_ids != EXPECTED_VARIANT_IDS:
        errors.append(
            "功能變體設定集合不符；缺少："
            + (", ".join(sorted(EXPECTED_VARIANT_IDS - actual_variant_ids)) or "無")
            + "；多餘："
            + (", ".join(sorted(actual_variant_ids - EXPECTED_VARIANT_IDS)) or "無")
        )
    registered_variants = status_data.get("variants")
    if isinstance(registered_variants, dict):
        for variant_id, item in registered_variants.items():
            path = variants.get(variant_id)
            if path is None or not isinstance(item, dict):
                continue
            base_board = item.get("base_board")
            if base_board not in actual_ids:
                errors.append(f"{variant_id} 的基礎板卡設定不存在：{base_board}")
            fields = effective_fields(path)
            missing = [field for field in REQUIRED_FIELDS if not fields.get(field)]
            if missing:
                errors.append(f"{variant_id} 缺少必要欄位：{', '.join(missing)}")

    boards: list[Board] = []
    for path in paths:
        board_id = path.stem
        fields = effective_fields(path)
        missing = [field for field in REQUIRED_FIELDS if not fields.get(field)]
        if missing:
            errors.append(f"{board_id} 缺少必要欄位：{', '.join(missing)}")
        item = evidence.get(board_id)
        level = item.get("level", "") if isinstance(item, dict) else ""
        basis = item.get("basis", "") if isinstance(item, dict) else ""
        next_gate = next_gates.get(board_id, "")
        findings = open_findings.get(board_id, [])
        boards.append(
            Board(
                board_id=board_id,
                status=path.suffix.lstrip("."),
                path=path,
                fields=fields,
                batch=batches.get(board_id, "?"),
                level=level,
                basis=basis,
                next_gate=next_gate if isinstance(next_gate, str) else "",
                findings=tuple(findings) if isinstance(findings, list) else (),
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


def markdown_text(boards: list[Board], updated: str, variants: object | None = None) -> str:
    """產生以證據等級為核心的繁體中文盤點報告。"""
    status_counts = Counter(board.status for board in boards)
    level_counts = Counter(board.level for board in boards)
    lines = [
        "# Banana Pi 全系列最佳化盤點",
        "",
        f"更新日期：{updated}",
        "",
        "**歷史快照，非現行發布狀態。** 本報告只呈現指定日期已納入 Git 的證據，不得取代最新候選狀態、實機驗證或對外發布核准。",
        "",
        "本報告由 `tools/bananapi-board-audit.py` 從板卡設定與受版本控制的證據登錄檔產生。建置成功、裝置節點存在及歷史映像均不會自動提升證據等級。",
        "",
        "## 摘要",
        "",
        f"- 板卡總數：{len(boards)}。",
        f"- 獨立功能變體：{len(variants) if isinstance(variants, dict) else 0}。",
        f"- 正式 `.conf`：{status_counts['conf']}；社群 `.csc`：{status_counts['csc']}；開發中 `.wip`：{status_counts['wip']}；停止支援 `.eos`：{status_counts['eos']}。",
        "- 證據分布：" + "；".join(f"{level} {level_counts[level]}" for level in LEVEL_NAMES) + "。",
        "- 未取得實機的板卡最高只能標示 L2；目前沒有板卡達到完整 L3／L4／L5 門檻。",
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

    lines.extend(["", "## 功能變體", ""])
    if isinstance(variants, dict) and variants:
        lines.extend(
            [
                "功能變體沿用基礎產品板卡，不計入產品板卡總數。",
                "",
                "| 變體 | 基礎板卡 | 證據 | 依據 | 下一門檻 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for variant_id, item in sorted(variants.items()):
            if not isinstance(item, dict):
                continue
            level = item.get("level", "")
            lines.append(
                f"| `{variant_id}` | `{item.get('base_board', '')}` | "
                f"{level} {LEVEL_NAMES.get(level, '未知')} | {item.get('basis', '')} | "
                f"{item.get('next_gate', '')} |"
            )
            for finding in item.get("open_findings", []):
                lines.append(f"\n- `{variant_id}`：{finding}。")
    else:
        lines.append("- 無已登錄的功能變體。")

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


def generated_output_errors(
    boards: list[Board], updated: str, variants: object | None = None
) -> list[str]:
    """比較兩份受版本控制報告，不執行任何寫入。"""
    expected = {
        MARKDOWN_REPORT: markdown_text(boards, updated, variants),
        TSV_REPORT: tsv_text(boards),
    }
    errors: list[str] = []
    for path, content in expected.items():
        if not path.is_file():
            errors.append(f"缺少產生報告：{path.relative_to(REPO_DIR)}")
        elif path.read_text(encoding="utf-8") != content:
            errors.append(f"產生報告已過期：{path.relative_to(REPO_DIR)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", type=Path, help="寫入 Markdown 報告")
    parser.add_argument("--tsv", type=Path, help="寫入 TSV 資料")
    parser.add_argument("--check", action="store_true", help="只執行一致性檢查")
    args = parser.parse_args()

    if args.check and (args.markdown or args.tsv):
        parser.error("--check 不得與 --markdown 或 --tsv 混用")

    boards, errors = collect_boards()
    if errors:
        for error in errors:
            print(f"錯誤：{error}", file=sys.stderr)
        return 1
    if len(boards) != 48:
        print(f"錯誤：預期 48 個板卡，實際為 {len(boards)}", file=sys.stderr)
        return 1

    status_data = load_status()
    if args.check:
        output_errors = generated_output_errors(
            boards, status_data["updated"], status_data.get("variants")
        )
        if output_errors:
            for error in output_errors:
                print(f"錯誤：{error}", file=sys.stderr)
            return 1
        print(f"盤點通過：{len(boards)} 個板卡，狀態與兩份報告一致", file=sys.stderr)
        return 0
    if args.markdown:
        write_output(
            args.markdown,
            markdown_text(boards, status_data["updated"], status_data.get("variants")),
        )
    if args.tsv:
        write_output(args.tsv, tsv_text(boards))
    if not args.markdown and not args.tsv and not args.check:
        print(tsv_text(boards), end="")
    print(f"盤點通過：{len(boards)} 個板卡，狀態登錄無漏板", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
