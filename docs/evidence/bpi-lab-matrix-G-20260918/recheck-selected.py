#!/usr/bin/env python3
"""G 階段限定重驗三板二十八套；保留最初四百四十四套的批次。"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import groupby
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import bpi_lab_matrix as matrix  # noqa: E402


def main():
    base = ROOT / "output/evidence/bpi-lab-matrix-G-20260918"
    initial = list((base / "batch-002").glob("incomplete-*.json"))
    matrix.require(len(initial) == 1, "須指定唯一的初始批次不完整摘要")
    original = matrix.review.reference(initial[0])
    summary = matrix.review.ref_data(original)
    matrix.require(summary["checked"] == 444 and len(summary["retryable"]) == 8
                   and all(row["board"] == "bpi-sm10" for row in summary["retryable"]), "初始批次範圍不同")
    previous = matrix.review.ref_data(summary["plan"])
    output = base / "selected-recheck-001"
    if output.exists():
        ref = matrix.review.reference(output / "plan.json")
    else:
        ref = matrix.initialize(previous["catalog"], previous["sample"], output,
                                replay_roots=[base / "batch-002"], workers=4)
    plan = matrix.review.ref_data(ref)
    matrix.require(plan["catalog"] == previous["catalog"] and plan["sample"] == previous["sample"]
                   and plan["rows"] == previous["rows"], "重驗來源或參數漂移")
    rows = [row for row in plan["rows"] if row["entry"]["board"] in ("bpi-ai2n", "bpi-m6", "bpi-sm10")]
    matrix.require(len(rows) == 28 and all(row["entry"]["image_id"] in plan["replays"] for row in rows),
                   "三板二十八套的既有擷取不完整")
    results = []
    with matrix.lock(output):
        matrix.require(plan["tools"] == matrix.tool_binding(), "工具變動，禁止混用重驗版本")
        for _, group in groupby(rows, key=lambda row: (row["entry"]["architecture"], row["entry"]["release"])):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results.extend(pool.map(lambda row: matrix.try_row(ref, plan, row, output), group))
            matrix.require(plan["tools"] == matrix.tool_binding(), "重驗期間工具改變")
        matrix.require(all(row["status"] == "prepared" and row["source_reread"] is False for row in results),
                       "重驗仍有阻擋或非重播結果，請逐筆檢查")
        result = {"schema": "bpi-lab-selected-recheck-v1", "original_summary": original, "plan": ref,
                  "scope": "僅重播 AI2N、M6、SM10 二十八套原配擷取，不重做其他板、不覆寫最初收據。",
                  "checked": len(results), "status_counts": dict(Counter(row["status"] for row in results)),
                  "source_rereads": 0, "hardware_validated": False, "media_written": False,
                  "results": [matrix.review.reference(output / "jobs" / row["image_id"] / "receipt.json")
                              for row in results]}
        path = output / "summary.json"
        if path.exists():
            matrix.require(matrix.queue.read_json(path) == result, "重驗總結與既有內容不同")
        else:
            matrix.publish_json(path, result)
        print(json.dumps(matrix.review.reference(path), ensure_ascii=False))


if __name__ == "__main__":
    main()
