#!/usr/bin/env python3
"""把已完成候選整併成單一、可續跑且不重複建置的狀態。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


COMPLETED_STATUSES = {"已驗證候選", "本輪已完成", "候選待整板驗證"}


@dataclass(frozen=True)
class CandidateSource:
    name: str
    release_root: Path
    state_root: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="整併已完成候選，不啟動任何建置。")
    parser.add_argument("--ledger", type=Path, required=True, help="映像盤點 TSV")
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="名稱|發布根目錄|狀態根目錄",
        help="可重複指定原候選來源",
    )
    parser.add_argument("--target-release", type=Path, required=True)
    parser.add_argument("--target-state", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--build-context", required=True)
    return parser.parse_args()


def parse_candidate(raw: str) -> CandidateSource:
    fields = raw.split("|", 2)
    if len(fields) != 3 or not all(fields):
        raise ValueError(f"候選來源格式錯誤：{raw}")
    return CandidateSource(fields[0], Path(fields[1]), Path(fields[2]))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_values(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value
    return lines, values


def replace_value(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    replaced = False
    result: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                result.append(f"{prefix}{value}")
                replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"{prefix}{value}")
    return result


def link_verified(source: Path, target: Path, expected_digest: str = "") -> None:
    if not source.is_file():
        raise ValueError(f"來源檔案不存在：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file():
            raise ValueError(f"目標不是一般檔案：{target}")
    else:
        os.link(source, target)
    if expected_digest and sha256_file(target) != expected_digest:
        raise ValueError(f"整併檔案 SHA-256 不符：{target}")


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def read_ledger(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    required = {
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
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"盤點帳本欄位不足：{path}")
    return rows


def main() -> int:
    args = parse_args()
    if len(args.source_commit) != 40 or len(args.build_context) != 64:
        raise ValueError("來源提交或建置內容雜湊長度錯誤")
    candidates = {
        source.name: source for source in map(parse_candidate, args.candidate)
    }
    if len(candidates) != len(args.candidate):
        raise ValueError("候選來源名稱重複")
    rows = [
        row for row in read_ledger(args.ledger) if row["狀態"] in COMPLETED_STATUSES
    ]
    if not rows:
        raise ValueError("帳本沒有可整併的完成項目")

    args.target_release.mkdir(parents=True, exist_ok=True)
    for directory in (
        "items",
        "logs",
        "framework-logs",
        "boards",
        "markers",
        "runs",
        "transactions",
    ):
        (args.target_state / directory).mkdir(parents=True, exist_ok=True)

    imported: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for row in rows:
        key = row["唯一鍵"]
        if key in seen_keys:
            raise ValueError(f"完成帳本含重複唯一鍵：{key}")
        seen_keys.add(key)
        if row["來源提交"] != args.source_commit:
            raise ValueError(f"來源提交不符：{key}")
        if row["建置內容雜湊"] != args.build_context:
            raise ValueError(f"建置內容雜湊不符：{key}")
        source = candidates.get(row["選用來源"])
        if source is None:
            raise ValueError(f"找不到帳本候選來源：{row['選用來源']}")

        source_archive = Path(row["映像"])
        source_sidecar = Path(f"{source_archive}.sha")
        stage = (
            args.target_release / f".staging-{row['板目錄']}-{args.source_commit[:12]}"
        )
        target_archive = stage / source_archive.name
        target_sidecar = Path(f"{target_archive}.sha")
        link_verified(source_archive, target_archive, row["SHA256"])
        link_verified(source_sidecar, target_sidecar)

        source_marker = (
            source.state_root
            / "items"
            / (f"{row['板目錄']}-{row['發行版']}-{row['類型']}.complete")
        )
        lines, values = read_values(source_marker)
        expected = {
            "source_commit": args.source_commit,
            "build_context_sha256": args.build_context,
            "folder": row["板目錄"],
            "board": row["板卡"],
            "branch": row["分支"],
            "release": row["發行版"],
            "profile": row["類型"],
            "archive": source_archive.name,
            "sha256": row["SHA256"],
        }
        for field, expected_value in expected.items():
            if values.get(field) != expected_value:
                raise ValueError(f"完成標記欄位不符：{source_marker} / {field}")

        source_log = Path(values.get("log", ""))
        log_digest = values.get("log_sha256", "")
        target_log = args.target_state / "logs" / f"{source.name}-{source_log.name}"
        link_verified(source_log, target_log, log_digest)
        lines = replace_value(lines, "log", str(target_log))

        framework_digest = values.get("framework_log_sha256", "")
        source_framework = Path(values.get("framework_log", ""))
        if framework_digest:
            target_framework = (
                args.target_state
                / "framework-logs"
                / f"{source.name}-{source_framework.name}"
            )
            link_verified(source_framework, target_framework, framework_digest)
            lines = replace_value(lines, "framework_log", str(target_framework))

        target_marker = args.target_state / "items" / source_marker.name
        write_atomic(target_marker, "\n".join(lines) + "\n")
        imported.append(
            {
                "唯一鍵": key,
                "原候選": source.name,
                "原完成標記": str(source_marker),
                "整併完成標記": str(target_marker),
                "整併映像": str(target_archive),
                "SHA256": row["SHA256"],
            }
        )

    manifest = args.target_state / "整併清冊.tsv"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        fields = [
            "唯一鍵",
            "原候選",
            "原完成標記",
            "整併完成標記",
            "整併映像",
            "SHA256",
        ]
        writer = csv.DictWriter(stream, fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(imported)
    print(f"整併完成：{len(imported)} 個既有完成項目；未啟動建置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
