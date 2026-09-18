#!/usr/bin/env python3
"""補核首次總核對未涵蓋的前次重播祖先，不重做組件準備或整份 XZ 讀取。"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import bpi_lab_matrix as matrix  # noqa: E402


def main():
    base = ROOT / "output/evidence/bpi-lab-matrix-G-20260918"
    original = matrix.review.reference(base / "final-audit-001.json")
    matrix.require(original["sha256"] == "6992c4daf67cd951bead88f3e3b21141138a077861e9ffcb7f1c2c11c846d741",
                   "原總核對摘要不同")
    report = matrix.review.ref_data(original)
    matrix.require(matrix.review.reference(Path(report["audit_program"]["path"])) == report["audit_program"],
                   "原核對程式改變")
    verified, earlier, records = set(), set(), []
    for name, summary_ref in report["summaries"].items():
        summary = matrix.review.ref_data(summary_ref)
        plan = matrix.review.ref_data(summary["plan"])
        if name == "selected":
            matrix.require(plan["tools"] == matrix.tool_binding(), "補核期間工具漂移")
        rows = {row["entry"]["image_id"]: row["entry"] for row in plan["rows"]}
        for receipt_ref in summary["results"]:
            receipt = matrix.review.ref_data(receipt_ref)
            for index, attempt in enumerate(receipt["attempts"]):
                matrix.review.ref_data(attempt["preparation"])
                extraction = matrix.review.reference(Path(attempt["preparation"]["path"]).parent /
                                                      "extraction/extraction.json")
                chain = matrix.review.extraction_chain(extraction, plan["source_root"], rows[receipt["image_id"]])
                keys = {(ref["path"], ref["sha256"]) for ref in chain}
                if index == len(receipt["attempts"]) - 1:
                    verified.update(keys)
                else:
                    matrix.require(receipt["board"] == "bpi-m1" and attempt["mode"] == "replay",
                                   "前次嘗試範圍不同")
                    for ref in chain:
                        matrix.snapshot_integrity(ref)
                    matrix.review.source_guard(plan["source_root"], rows[receipt["image_id"]])
                    earlier.update(keys)
                    records.append({"receipt": receipt_ref, "preparation": attempt["preparation"], "chain": chain})
    matrix.require(len(verified) == report["verified_extraction_snapshots"] and len(records) == 4,
                   "原核對或前次嘗試計數不符")
    matrix.require(matrix.review.reference(Path(report["original_hardware_database"]["path"])) ==
                   report["original_hardware_database"], "原硬體資料庫改變")
    report.update(recorded_at=datetime.now(timezone.utc).isoformat(), initial_audit=original,
                  verified_extraction_snapshots=len(verified | earlier), all_attempt_chains_verified=True,
                  supplemental_audit={"program": matrix.review.reference(Path(__file__).resolve()),
                                      "earlier_attempts": len(records), "new_snapshots": len(earlier - verified),
                                      "records": records})
    path = base / "final-audit-002.json"
    matrix.require(not path.exists(), "補核紀錄已存在，不可覆寫")
    matrix.publish_json(path, report)
    print(json.dumps({"report": matrix.review.reference(path), "final": report["final"],
                      "earlier_attempts": len(records), "new_snapshots": len(earlier - verified)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
