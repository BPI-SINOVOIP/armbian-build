#!/usr/bin/env python3
"""解析 BPI-M4 Zero O1 的 M4ZDDR1 UART 紀錄。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


MARKER_RE = re.compile(r"(M4ZDDR1_[A-Z0-9]+)\s+(.*)$")


def parse_fields(raw_fields: str) -> dict[str, str]:
	fields: dict[str, str] = {}
	for token in raw_fields.split():
		if "=" not in token:
			continue
		key, value = token.split("=", 1)
		fields[key] = value
	return fields


def parse_stream(stream: TextIO, source: str, report: dict[str, object]) -> None:
	current: dict[str, object] | None = None
	initializations = report["初始化"]
	issues = report["問題"]
	assert isinstance(initializations, list)
	assert isinstance(issues, list)

	for line_number, raw_line in enumerate(stream, start=1):
		match = MARKER_RE.search(raw_line.rstrip("\r\n"))
		if not match:
			continue

		marker, raw_fields = match.groups()
		fields = parse_fields(raw_fields)
		record = {
			"來源": source,
			"行號": line_number,
			"欄位": fields,
		}

		if marker.startswith("M4ZDDR1_PROFILE"):
			profile = report["設定"]
			assert isinstance(profile, dict)
			profile.update(fields)
		elif marker == "M4ZDDR1_BEGIN":
			if current is not None:
				issues.append(f"{source}:{line_number}：前一個初始化區塊缺少 END")
			current = {"開始": record, "階段": []}
			initializations.append(current)
		elif marker in {"M4ZDDR1_RUN", "M4ZDDR1_STAGE"}:
			if current is None:
				issues.append(f"{source}:{line_number}：{marker} 位於初始化區塊外")
			else:
				stages = current["階段"]
				assert isinstance(stages, list)
				stages.append({"標記": marker, **record})
		elif marker == "M4ZDDR1_END":
			if current is None:
				issues.append(f"{source}:{line_number}：END 缺少對應 BEGIN")
			else:
				current["結束"] = record
				current = None
		elif marker == "M4ZDDR1_REG":
			registers = report["暫存器"]
			assert isinstance(registers, list)
			registers.append(record)
		elif marker == "M4ZDDR1_FINAL":
			report["最終結果"] = record

	if current is not None:
		issues.append(f"{source}：檔案結束時初始化區塊仍缺少 END")


def open_sources(paths: list[Path]) -> tuple[list[tuple[str, TextIO]], list[TextIO]]:
	if not paths:
		return [("標準輸入", sys.stdin)], []

	opened: list[TextIO] = []
	sources: list[tuple[str, TextIO]] = []
	for path in paths:
		stream = path.open("r", encoding="utf-8", errors="replace")
		opened.append(stream)
		sources.append((str(path), stream))
	return sources, opened


def main() -> int:
	parser = argparse.ArgumentParser(
		description="解析 M4ZDDR1 UART 診斷欄位並輸出 JSON",
	)
	parser.add_argument(
		"logs",
		metavar="日誌",
		nargs="*",
		type=Path,
		help="UART 日誌；省略時讀取標準輸入",
	)
	parser.add_argument(
		"--allow-incomplete",
		action="store_true",
		help="發現不完整區塊時仍回傳成功",
	)
	args = parser.parse_args()

	report: dict[str, object] = {
		"格式版本": "M4ZDDR1",
		"設定": {},
		"初始化": [],
		"暫存器": [],
		"最終結果": None,
		"問題": [],
	}

	sources, opened = open_sources(args.logs)
	try:
		for source_name, stream in sources:
			parse_stream(stream, source_name, report)
	finally:
		for stream in opened:
			stream.close()

	issues = report["問題"]
	assert isinstance(issues, list)
	if report["最終結果"] is None:
		issues.append("沒有找到 M4ZDDR1_FINAL，日誌可能不完整或映像版本不符")

	json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
	sys.stdout.write("\n")

	if issues and not args.allow_incomplete:
		return 2
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
