#!/usr/bin/env python3
"""唯讀重驗 R2 十套候選與原證據；只向標準輸出產生交付紀錄。"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import bpi_lab_r2_matrix as r
m, review = r.matrix, r.review
base = r.ROOT / "output/evidence/bpi-r2-matrix-I-20260918"
batch = base / "batch-003"
with m.lock(batch):
    summary_ref = review.reference(batch / "summary.json")
    summary = review.ref_data(summary_ref)
    r.require(summary["checked"] == summary["prepared"] == 10 and summary["reused_legacy"] == 1
              and summary["reused_previous"] == 1 and summary["hardware_validated"] is False
              and summary["source_modified"] is False and summary["internal_only"] is True
              and summary["boot_blob_redistribution_authorized"] is False, "總結狀態錯誤")
    plan_ref = summary["plan"]
    r.require(plan_ref["sha256"] == "53b7394fb0182ee58c212bc679b25f5b23697b7e57cba8b937632d6921352cb4", "計畫摘要不同")
    plan = review.ref_data(plan_ref)
    root, rows = r.source_rows()
    r.validate_plan(plan, root, rows)
    r.require(plan["tools"] == m.tool_binding(), "最終工具不同")
    by_id = {row["entry"]["image_id"]: row for row in rows}
    results = []
    for ref in summary["results"]:
        receipt = review.ref_data(ref)
        row = by_id[receipt["image_id"]]
        directory = batch / "jobs" / row["entry"]["image_id"]
        r.require(Path(ref["path"]) == directory / "receipt.json", "總結收據路徑不符")
        r.verify_receipt(receipt, plan_ref, plan, row, directory)
        c = review.ref_data(receipt["candidate_manifest"])
        p = review.ref_data(receipt["preparation"])
        checksum = Path(c["candidate"]["path"] + ".sha")
        expected = (c["candidate"]["sha256"] + "  " + Path(c["candidate"]["path"]).name + "\n").encode()
        r.require(m.file_digest(checksum) == m.image.digest(expected), "候選校驗旁檔錯配")
        results.append({
            "image_id": receipt["image_id"], "release": receipt["release"], "variant": receipt["variant"],
            "status": receipt["status"], "source": c["source"], "candidate": c["candidate"],
            "checksum_sidecar": review.reference(checksum),
            "raw": c["raw"], "root_uuid": row["root_uuid"], "changed_range": c["changed_range"],
            "inverse_verification": receipt["verified"]["inverse_verification"],
            "candidate_manifest": receipt["candidate_manifest"], "preparation": receipt["preparation"],
            "receipt": ref, "origin_receipt": receipt.get("origin_receipt"),
            "reused_legacy": receipt["reused_legacy"], "reused_previous": receipt.get("reused_previous", False),
            "e2fsck_exit_code": c["e2fsck_exit_code"], "final_xz_source_reread": p["source_reread"],
            "internal_only": True, "hardware_validated": False, "integration_approved": False,
            "boot_blob_redistribution_authorized": False
        })
    r.require(len(results) == len({x["image_id"] for x in results}) == 10, "候選來源重複或缺漏")
    audit = review.ref_data(r.AUDIT)
    r.require(audit["final"] == {"checked": 444, "prepared": 434, "blocked": 10}
              and audit["remaining"] == audit["retryable"] == 0, "原全矩陣結論不同")
    catalog = review.ref_data(audit["catalog"])
    for entry in catalog["entries"]:
        review.source_guard(catalog["root"], entry)
    database = review.reference(r.ROOT / "output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3")
    r.require(database["sha256"] == "1b94dde10232cf4028e919c65fae98e0f1946ccb351abe9435012f364ff96e13", "原硬體資料庫變動")
    regression_ref = review.reference(base / "lab-final-I-001.log")
    r.require(regression_ref["sha256"] == "d02c874cc17d865fbda36638c2f1a15abdcf5639704eaea9958234adb2719622", "回歸日誌不同")
    platforms_ref = review.reference(base / "platforms-I.json")
    platforms = review.ref_data(platforms_ref)
    r.require(platforms["status"] == "valid" and platforms["board_count"] == 45 and platforms["source_count"] == 98,
              "來源盤點結果不同")
    result = {
        "schema": "bpi-r2-matrix-delivery-v1", "date": "2026-09-19",
        "audit_program": review.reference(Path(__file__).resolve()),
        "code_commit": "92fd3d32f637b4573d1bce846eb8b8ab1180a9ef",
        "plan": plan_ref, "summary": summary_ref, "previous_original_audit": r.AUDIT,
        "original_catalog": audit["catalog"], "original_sources_identity_verified": len(catalog["entries"]),
        "original_prepared": audit["final"]["prepared"], "original_blocked": audit["final"]["blocked"],
        "candidate_prepared": summary["prepared"],
        "new_candidates_this_stage": 9, "legacy_candidates_reused": 1, "cross_batch_candidates_reused": 1,
        "original_hardware_database": database, "internal_only": True, "hardware_validated": False,
        "media_written": False, "boot_executed": False, "integration_approved": False,
        "boot_blob_redistribution_authorized": False,
        "tests": {"command": "BPI_LAB_REAL_C3=1 output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p 'test_bpi_lab*.py'",
                  "count": 1535, "failures": 0, "skipped": 0, "seconds": 173.278, "log": regression_ref},
        "ruff": review.reference(base / "ruff-I.log"), "platforms": platforms_ref,
        "run_logs": [review.reference(base / name / "run.log") for name in ("batch-002", "batch-003")],
        "tools": {name: m.file_digest(r.ROOT / name) for name in
                  ("tools/bpi_lab_r2_matrix.py", "tools/bpi_lab_r2_repack.py", "tests/test_bpi_lab_r2_matrix.py")},
        "rows": results
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
