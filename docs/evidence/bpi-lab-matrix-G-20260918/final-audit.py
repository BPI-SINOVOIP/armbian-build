#!/usr/bin/env python3
"""核對 G 原批次與限定重驗，產生不重複的原映像交付索引。"""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import bpi_lab_matrix as matrix  # noqa: E402


def counts(rows):
    return {"checked": len(rows), **dict(Counter(row["status"] for row in rows))}


def main():
    base = ROOT / "output/evidence/bpi-lab-matrix-G-20260918"
    initial = list((base / "batch-002").glob("incomplete-*.json"))
    matrix.require(len(initial) == 1, "初始不完整摘要不唯一")
    refs = {"original": matrix.review.reference(initial[0]),
            "selected": matrix.review.reference(base / "selected-recheck-001/summary.json")}
    summaries = {name: matrix.review.ref_data(ref) for name, ref in refs.items()}
    plans = {name: matrix.review.ref_data(value["plan"]) for name, value in summaries.items()}
    matrix.require(summaries["original"]["plan"]["sha256"] ==
                   "42cd1da5a5e664e6fab3b35f25101dcf12fb641a567c73cf4008281ce12b11fd", "原批次錯配")
    matrix.require(summaries["selected"]["original_summary"] == refs["original"], "重驗引用原批次錯配")
    matrix.require(plans["selected"]["rows"] == plans["original"]["rows"], "兩批次原始清單不同")
    matrix.require(plans["selected"]["tools"] == matrix.tool_binding(), "重驗後工具漂移")
    bindings = {name: {row["path"]: row for row in plan["tools"]} for name, plan in plans.items()}
    changed = [path for path in sorted(set(bindings["original"]) | set(bindings["selected"]))
               if bindings["original"].get(path) != bindings["selected"].get(path)]
    matrix.require(changed == [str(ROOT / "tools" / name) for name in ("bpi_lab_matrix.py", "bpi_lab_special.py")],
                   "局部修正擴及未重驗工具")
    catalog_rows = plans["original"]["rows"]
    rows_by_id = {row["entry"]["image_id"]: row for row in catalog_rows}
    matrix.require(len(rows_by_id) == len(catalog_rows) == 444, "原清單重複或不完整")
    retryable = summaries["original"]["retryable"]
    retry_ids = {row["image_id"] for row in retryable}
    matrix.require(len(retry_ids) == len(retryable) == 8
                   and all(row["board"] == "bpi-sm10" for row in retryable), "初始待重試範圍不同")
    receipts, snapshots = {}, set()
    for name in ("original", "selected"):
        plan, summary = plans[name], summaries[name]
        directory = Path(summary["plan"]["path"]).parent
        results = {}
        for ref in summary["results"]:
            receipt = matrix.review.ref_data(ref)
            image_id = receipt["image_id"]
            matrix.require(image_id not in results and image_id in rows_by_id, "收據重複或非原清單成員")
            row = rows_by_id[image_id]
            matrix.require(Path(ref["path"]) == directory / "jobs" / image_id / "receipt.json", "收據路徑錯配")
            matrix.verify_completed(receipt, summary["plan"], plan, row, directory / "jobs" / image_id)
            last = receipt["attempts"][-1]
            extraction = matrix.review.reference(Path(last["preparation"]["path"]).parent /
                                                  "extraction/extraction.json")
            for source in matrix.review.extraction_chain(extraction, plan["source_root"], row["entry"]):
                key = (source["path"], source["sha256"])
                if key not in snapshots:
                    matrix.snapshot_integrity(source)
                    snapshots.add(key)
            results[image_id] = (ref, receipt)
        statuses = Counter(value[1]["status"] for value in results.values())
        if name == "original":
            statuses["retryable"] = len(retryable)
        matrix.require(sum(statuses.values()) == summary["checked"], "批次總數與逐筆收據不符")
        matrix.require(dict(statuses) == summary["status_counts"],
                       "批次狀態總數不符")
        receipts[name] = results
        print(json.dumps({"verified_batch": name, "receipts": len(results)}, ensure_ascii=False), flush=True)
    selected = {key for key, row in rows_by_id.items() if row["entry"]["board"] in ("bpi-ai2n", "bpi-m6", "bpi-sm10")}
    matrix.require(set(receipts["original"]) == set(rows_by_id) - retry_ids
                   and retry_ids <= selected and set(receipts["selected"]) == selected and len(selected) == 28,
                   "覆蓋範圍錯誤")
    rows = []
    for row in catalog_rows:
        entry = row["entry"]
        name = "selected" if entry["image_id"] in selected else "original"
        ref, result = receipts[name][entry["image_id"]]
        matrix.require(result["status"] == ("blocked" if entry["board"] == "bpi-r2" else "prepared"),
                       "最終結果仍有未釐清阻擋")
        if name == "selected":
            matrix.require(result["source_reread"] is False, "局部重驗不是限定重播")
        if result["status"] == "blocked":
            matrix.require(result["blockers"] == [{"code": "missing_file", "message":
                           "缺少原配檔案：/boot/dtb/mediatek/mt7623n-bananapi-bpi-r2"}], "R2 阻擋不同")
        rows.append({**{key: result[key] for key in ("image_id", "board", "architecture", "release", "variant",
                     "relative_path", "source_sha256", "status", "blockers", "source_reread")},
                     "validation_batch": name, "receipt": ref})
    database = matrix.review.reference(ROOT / "output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3")
    matrix.require(database["sha256"] == "1b94dde10232cf4028e919c65fae98e0f1946ccb351abe9435012f364ff96e13",
                   "原硬體資料庫改變")
    test_log = base / "lab-final-G-fixes.log"
    test_text = test_log.read_text()
    test_result = re.findall(r"^Ran (\d+) tests in ([0-9.]+)s$", test_text, re.MULTILINE)
    matrix.require(len(test_result) == 1 and int(test_result[0][0]) == 1461
                   and test_text.rstrip().endswith("\nOK"), "完整回歸尚未全數通過")
    source_reads = set()
    for path in (base / "batch-002/jobs").glob("*/attempt-*/source/preparation.json"):
        result = matrix.queue.read_json(path)
        image_id = path.parents[2].name
        matrix.require(image_id in rows_by_id and result["source"]["sha256"] ==
                       rows_by_id[image_id]["entry"]["expected_sha256"], "原讀取嘗試來源不符")
        source_reads.add(image_id)
    report = {"schema": "bpi-lab-matrix-final-audit-v1", "recorded_at": datetime.now(timezone.utc).isoformat(),
              "scope": "全部原映像離線組件準備；不是完整原生開機鏈、部署或實板功能的通過聲明。",
              "catalog": plans["original"]["catalog"], "summaries": refs,
              "plans": {name: summary["plan"] for name, summary in summaries.items()},
              "code_commits": {"original": "1da0d6399631937fe9011f71305260d84f2af8a1",
                               "selected": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()},
              "changed_tool_paths": changed, "final": counts(rows), "remaining": 0, "retryable": 0,
              "source_blocked": 10, "parser_gap_blocked": 0,
              "initial_receipted_full_source_reads": summaries["original"]["full_source_reads"],
              "initial_receipted_replayed_only": summaries["original"]["replayed_only"],
              "initial_retryable": retryable,
              "initial_unique_full_source_reads": len(source_reads),
              "initial_unique_replayed_only": 444 - len(source_reads),
              "additional_replays": 28, "additional_full_source_reads": 0,
              "verified_receipts": 464, "verified_extraction_snapshots": len(snapshots),
              "by_architecture": {key: counts([row for row in rows if row["architecture"] == key])
                                  for key in sorted({row["architecture"] for row in rows})},
              "by_board": {key: counts([row for row in rows if row["board"] == key])
                           for key in sorted({row["board"] for row in rows})},
              "by_release": {key: counts([row for row in rows if row["release"] == key])
                             for key in sorted({row["release"] for row in rows})},
              "original_hardware_database": database, "source_guard_verified": 444,
              "regression": {"passed": int(test_result[0][0]), "seconds": float(test_result[0][1]),
                             "failed": 0, "skipped": 0, "log": matrix.review.reference(test_log)},
              "logs": {"original": matrix.review.reference(base / "batch-002/run.log"),
                       "selected": matrix.review.reference(base / "recheck-selected.log"),
                       "ruff": matrix.review.reference(base / "ruff-G-fixes.log"),
                       "platforms": matrix.review.reference(base / "platforms-G-fixes.json")},
              "observed_process_exit_codes": {"original": 2, "selected": 0, "regression": 0},
              "hardware_validated": False, "media_written": False, "source_images_modified": False,
              "audit_program": matrix.review.reference(Path(__file__).resolve()), "rows": rows}
    path = base / "final-audit-001.json"
    matrix.require(not path.exists(), "最終核對紀錄已存在，不可覆寫")
    matrix.publish_json(path, report)
    print(json.dumps({"report": matrix.review.reference(path), "final": report["final"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
