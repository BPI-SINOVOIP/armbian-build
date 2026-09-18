#!/usr/bin/env python3
"""固定 G 來源的 R2 內部候選矩陣；不替換原發布檔或啟用硬體。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from itertools import groupby
import json
from pathlib import Path
import re
import shutil

import bpi_lab_matrix as matrix
import bpi_lab_r2_repack as repack

ROOT = matrix.ROOT
AUDIT = {"path": str(ROOT / "docs/evidence/bpi-lab-matrix-G-20260918/final-audit.json"),
         "sha256": "1244f805eb86553e598074320ad1bcda670c74bbba24cd3db322db1b32a683d9"}
LEGACY = ROOT / "output/evidence/bpi-lab-handoff-F-20260918"
LEGACY_CANDIDATE = {"path": str(LEGACY / "r2-candidate-002/candidate.json"),
                    "sha256": "fce0f7ff1a1594bb873b07b79a74cf02f8ca74981ac3f75aa3dba98ed559c253"}
LEGACY_PREPARATION = {"path": str(LEGACY / "r2-final-xz-check-001/preparation.json"),
                      "sha256": "0a372596fb6f0c8b2f7934d55f21f8691643669f2194e7193877d80a5d6a3dce"}
require = matrix.require
review = matrix.review


def source_rows():
    audit = review.ref_data(AUDIT)
    catalog = review.ref_data(audit["catalog"])
    review.validate_catalog(catalog)
    entries = {entry["image_id"]: entry for entry in catalog["entries"]}
    rows = []
    for item in audit["rows"]:
        if item["board"] != "bpi-r2":
            continue
        entry = entries[item["image_id"]]
        require(entry["board"] == "bpi-r2" and entry["kernel"] == "6.6.153"
                and entry["architecture"] == "arm32" and entry["branch"] == "current"
                and item["source_sha256"] == entry["expected_sha256"], "R2 原來源錯配")
        receipt = review.ref_data(item["receipt"])
        require(receipt["status"] == "blocked" and receipt["kernel_release"] == repack.RELEASE
                and receipt["image_id"] == entry["image_id"], "原 R2 收據錯配")
        plan = review.ref_data(receipt["plan"])
        matched = [row for row in plan["rows"] if row["entry"] == entry]
        require(len(matched) == 1, "原計畫來源不唯一")
        matrix.verify_completed(receipt, receipt["plan"], plan, matched[0], Path(item["receipt"]["path"]).parent)
        extraction = review.reference(Path(receipt["attempts"][-1]["preparation"]["path"]).parent /
                                      "extraction/extraction.json")
        for ref in review.extraction_chain(extraction, catalog["root"], entry):
            matrix.snapshot_integrity(ref)
        data = review.ref_data(extraction)
        original = review.ref_data(review.extraction_chain(extraction, catalog["root"], entry)[-1])
        record = data["files"]["/boot/armbianEnv.txt"]
        env = (Path(extraction["path"]).parent / record["file"]).read_bytes()
        require(matrix.image.digest(env) == record["digest"], "原環境擷取摘要不同")
        contract = {"source_sha256": entry["expected_sha256"], "raw_sha256": original["raw"]["sha256"],
                    "raw_bytes": original["raw"]["bytes"], "environment_hex": env.hex(),
                    "name": Path(entry["relative_path"]).name[:-7] + "_i1-dtb-path.img"}
        repack.validate_contract(contract)
        rows.append({"entry": entry, "contract": contract, "root_uuid": original["filesystem_uuid"],
                     "original_receipt": item["receipt"], "original_extraction": extraction})
    require(len(rows) == 10 and {(row["entry"]["release"], row["entry"]["variant"]) for row in rows} ==
            {(os_name, variant) for os_name in review.RELEASES for variant in review.VARIANTS}, "R2 十套矩陣不完整")
    return catalog["root"], sorted(rows, key=lambda row: (row["entry"]["release"], row["entry"]["variant"]))


def validate_plan(plan, root, rows):
    require(plan["schema"] == "bpi-r2-matrix-plan-v1" and plan["audit"] == AUDIT
            and plan["rows"] == rows and plan["source_root"] == root and plan["workers"] == 2
            and plan["internal_only"] is True and plan["hardware_validated"] is False
            and plan["boot_blob_redistribution_authorized"] is False, "候選計畫錯配")
    reuses = plan.get("reuses", {})
    require(isinstance(reuses, dict) and set(reuses) <= {row["entry"]["image_id"] for row in rows},
            "跨批次重用範圍錯誤")


def plan_directory(plan_ref):
    path = Path(plan_ref["path"])
    output = path.parent
    require(path.name == "plan.json" and output.is_relative_to(matrix.OUTPUT_ROOT)
            and output != matrix.OUTPUT_ROOT and ".." not in output.parts, "候選批次目錄越界")
    matrix.queue.safe_directory(output)
    return output


def verify_previous(ref, row, source_root, depth=0):
    require(depth < 8, "跨批次重用鏈過深")
    receipt = review.ref_data(ref)
    plan_ref = receipt["plan"]
    plan = review.ref_data(plan_ref)
    root, rows = source_rows()
    validate_plan(plan, root, rows)
    require(source_root == root and row in rows, "跨批次原來源不符")
    directory = plan_directory(plan_ref) / "jobs" / row["entry"]["image_id"]
    require(Path(ref["path"]) == directory / "receipt.json", "跨批次收據路徑錯配")
    return verify_receipt(receipt, plan_ref, plan, row, directory, depth=depth + 1)


def initialize(output, previous_plan_ref=None):
    output = Path(output).absolute()
    require(output.is_relative_to(matrix.OUTPUT_ROOT) and output != matrix.OUTPUT_ROOT
            and ".." not in output.parts, "只能建立獨立證據子目錄")
    root, rows = source_rows()
    reuses = {}
    if previous_plan_ref is not None:
        previous = review.ref_data(previous_plan_ref)
        validate_plan(previous, root, rows)
        previous_output = plan_directory(previous_plan_ref)
        require(previous_output != output, "不可從相同批次建立新計畫")
        for row in rows:
            path = previous_output / "jobs" / row["entry"]["image_id"] / "receipt.json"
            if row["entry"]["expected_sha256"] != repack.SOURCE_SHA256 and path.exists():
                ref = review.reference(path)
                verify_previous(ref, row, root)
                reuses[row["entry"]["image_id"]] = ref
    matrix.queue.safe_directory(output.parent, create=True)
    matrix.image.create_directory(output)
    matrix.image.create_directory(output / "jobs")
    return matrix.queue.save_json(output / "plan.json", {
        "schema": "bpi-r2-matrix-plan-v1", "audit": AUDIT, "source_root": root, "rows": rows,
        "tools": matrix.tool_binding(), "workers": 2, "internal_only": True, "reuses": reuses,
        "hardware_validated": False, "boot_blob_redistribution_authorized": False})


def verify_extraction(ref, *, source, kind, source_digest, raw, root_uuid):
    matrix.snapshot_integrity(ref)
    data = review.ref_data(ref)
    require(data.get("schema") == "bpi-lab-image-v1" and data.get("ok") is True
            and data.get("source_verified") is True and data.get("hardware_validated") is False
            and data.get("mounted") is False and data.get("image_code_executed") is False
            and data.get("source") == str(source) and data.get("source_kind") == kind
            and data.get("source_digest") == source_digest and data.get("raw") == raw
            and data.get("filesystem_uuid") == root_uuid, "擷取證據與壓縮來源或原始映像錯配")


def verify_candidate_source(candidate_ref, row, source_root):
    c = review.ref_data(candidate_ref)
    contract, entry = row["contract"], row["entry"]
    require(c["schema"] == "bpi-r2-derived-candidate-v1" and c["hardware_validated"] is False
            and c["rebuilt"] is False and c["source_modified"] is False
            and c["original_source_still_blocked"] is True and c["family_prepared"] is True
            and c["e2fsck_exit_code"] == 0,
            "候選狀態或發布限制不符")
    legacy = candidate_ref == LEGACY_CANDIDATE
    for field, expected in (("internal_only", True), ("integration_approved", False),
                            ("boot_blob_redistribution_authorized", False)):
        require(c.get(field, expected if legacy else None) is expected, "候選發布限制缺漏或矛盾")
    require(c["source"] == {"path": str(Path(source_root) / entry["relative_path"]),
                             "sha256": entry["expected_sha256"]}
            and c["raw"]["original_sha256"] == contract["raw_sha256"]
            and c["raw"]["bytes"] == contract["raw_bytes"], "候選原來源綁定不符")
    target = Path(c["candidate"]["path"])
    require(target.parent == Path(candidate_ref["path"]).parent and target.name.endswith(".img.xz"),
            "候選產物路徑越界")
    actual = matrix.file_digest(target)
    require(actual == {key: c["candidate"][key] for key in ("bytes", "sha256")}, "候選 XZ 改變")
    require(actual["sha256"] != entry["expected_sha256"], "候選 XZ 不得冒用原來源")
    old = bytes.fromhex(contract["environment_hex"])
    new = repack.replacement(old, expected=old)
    changed = c["changed_range"]
    require(changed["bytes"] == len(old) and changed["before"] == matrix.image.digest(old)
            and changed["after"] == matrix.image.digest(new), "修改環境與原來源契約不符")
    with matrix.catalog_api._root(target.parent) as root:
        with matrix.catalog_api._regular(root, target.name[:-3]) as (stream, identity):
            require(identity["st_size"] == contract["raw_bytes"], "候選 raw 大小改變")
            inverse = repack.verify_reversal(stream.fileno(), contract["raw_bytes"], changed["offset"], old, new)
    require(inverse["original_sha256"] == contract["raw_sha256"]
            and inverse["candidate_sha256"] == c["raw"]["sha256"], "完整逆向核對不符")
    raw = {"bytes": c["raw"]["bytes"], "sha256": c["raw"]["sha256"]}
    verify_extraction(review.reference(target.parent / "original-extraction/extraction.json"),
                      source=c["source"]["path"], kind="xz",
                      source_digest={"bytes": entry["compressed_bytes"], "sha256": entry["expected_sha256"]},
                      raw={"bytes": contract["raw_bytes"], "sha256": contract["raw_sha256"]}, root_uuid=row["root_uuid"])
    verify_extraction(review.reference(target.parent / "candidate-extraction/extraction.json"),
                      source=target.with_suffix(""), kind="raw", source_digest=raw, raw=raw, root_uuid=row["root_uuid"])
    review.source_guard(source_root, entry)
    verified = {"candidate": c["candidate"], "raw": c["raw"], "changed_range": changed,
                "inverse_verification": inverse, "root_uuid": row["root_uuid"]}
    return c, actual, verified


def verify_candidate(candidate_ref, preparation_ref, row, source_root):
    c, actual, verified = verify_candidate_source(candidate_ref, row, source_root)
    p = review.ref_data(preparation_ref)
    require(p["schema"] == "bpi-lab-prepare-v1" and p["status"] == "prepared" and not p["blockers"]
            and p["family"] == "mediatek" and p["board"] == "bpi-r2" and p["kernel_release"] == repack.RELEASE
            and p["source"] == actual and p["raw"] == {"bytes": c["raw"]["bytes"], "sha256": c["raw"]["sha256"]}
            and p["root_uuid"] == row["root_uuid"] and p["root_uuid_verified"] is True
            and p["source_reread"] is True and p["hardware_validated"] is False
            and p["whole_backend_ready"] is False and p["media_written"] is False and p["boot_executed"] is False,
            "最終 XZ 未通過固定來源與根身分核對")
    directory = Path(preparation_ref["path"]).parent
    require(p["components"]["path"] == "family-result.json" and p["extraction"]["path"] == "extraction/extraction.json",
            "最終組件引用越界")
    for key in ("components", "extraction"):
        require(matrix.file_digest(directory / p[key]["path"]) ==
                {field: p[key][field] for field in ("bytes", "sha256")}, "最終準備產物改變")
    matrix.verify_build_sources(matrix.queue.read_json(directory / "family-result.json"))
    verify_extraction(review.reference(directory / "extraction/extraction.json"),
                      source=c["candidate"]["path"], kind="xz", source_digest=actual,
                      raw=p["raw"], root_uuid=row["root_uuid"])
    review.source_guard(source_root, row["entry"])
    return verified


def verify_receipt(receipt, plan_ref, plan, row, directory, depth=0):
    require(receipt["schema"] == "bpi-r2-matrix-result-v1" and receipt["plan"] == plan_ref
            and receipt["row_sha256"] == matrix.queue.digest(row) and receipt["status"] == "prepared"
            and receipt["internal_only"] is True and receipt["hardware_validated"] is False
            and receipt["boot_blob_redistribution_authorized"] is False
            and receipt["source_modified"] is False, "候選完成收據錯配")
    require(all(receipt.get(key) == row["entry"][key] for key in ("image_id", "release", "variant"))
            and receipt.get("source_sha256") == row["entry"]["expected_sha256"], "候選完成收據身分錯配")
    previous = receipt.get("reused_previous", False)
    require(type(previous) is bool, "跨批次重用旗標無效")
    if previous:
        origin = plan.get("reuses", {}).get(row["entry"]["image_id"])
        require(origin is not None and receipt.get("origin_receipt") == origin
                and receipt["reused_legacy"] is False and receipt["attempt"] is None
                and receipt["artifacts"] is None, "跨批次重用引用錯配")
        old = verify_previous(origin, row, plan["source_root"], depth=depth)
        require(all(receipt[key] == old[key] for key in ("candidate_manifest", "preparation", "verified")),
                "跨批次重用產物錯配")
        return receipt
    require(receipt.get("origin_receipt") is None and row["entry"]["image_id"] not in plan.get("reuses", {}),
            "未使用計畫固定的跨批次候選")
    if receipt["reused_legacy"] is True:
        require(row["entry"]["expected_sha256"] == repack.SOURCE_SHA256
                and receipt["candidate_manifest"] == LEGACY_CANDIDATE
                and receipt["preparation"] == LEGACY_PREPARATION, "既有候選重用範圍錯誤")
    else:
        require(receipt["reused_legacy"] is False, "重用旗標無效")
        attempt = Path(receipt["attempt"])
        require(attempt.parent == directory and attempt.name.startswith("attempt-")
                and Path(receipt["candidate_manifest"]["path"]) == attempt / "candidate/candidate.json"
                and Path(receipt["preparation"]["path"]).parent.parent == attempt
                and re.fullmatch(r"final(?:-[0-9]{4})?", Path(receipt["preparation"]["path"]).parent.name)
                and Path(receipt["preparation"]["path"]).name == "preparation.json"
                and receipt["artifacts"] == matrix.artifact_digest(attempt), "候選收據產物或路徑不同")
    verified = verify_candidate(receipt["candidate_manifest"], receipt["preparation"], row, plan["source_root"])
    require(receipt["verified"] == verified, "候選核對摘要不同")
    return receipt


def execute_row(plan_ref, plan, row, output):
    directory = output / "jobs" / row["entry"]["image_id"]
    matrix.queue.safe_directory(directory, create=True)
    path = directory / "receipt.json"
    if path.exists():
        return verify_receipt(matrix.queue.read_json(path), plan_ref, plan, row, directory)
    review.source_guard(plan["source_root"], row["entry"])
    legacy = row["entry"]["expected_sha256"] == repack.SOURCE_SHA256
    origin = plan.get("reuses", {}).get(row["entry"]["image_id"])
    attempt = None
    if origin is not None:
        require(not legacy, "固定既有候選不得另行覆寫重用來源")
        previous = verify_previous(origin, row, plan["source_root"])
        candidate_ref, preparation_ref = previous["candidate_manifest"], previous["preparation"]
    elif legacy:
        candidate_ref, preparation_ref = LEGACY_CANDIDATE, LEGACY_PREPARATION
    else:
        existing = sorted(directory.glob("attempt-*/candidate/candidate.json"))
        require(len(existing) <= 1, "有多個完整候選，須先釐清嘗試來源")
        if existing:
            candidate_ref = review.reference(existing[0])
            attempt = existing[0].parent.parent
            candidate, _, _ = verify_candidate_source(candidate_ref, row, plan["source_root"])
        else:
            attempt = matrix.new_attempt(directory)
            candidate = repack.create_candidate(Path(plan["source_root"]) / row["entry"]["relative_path"],
                                                attempt / "candidate", contract=row["contract"])
            candidate_ref = review.reference(attempt / "candidate/candidate.json")
        preparation_ref = None
        for previous in sorted(attempt.glob("final*/preparation.json")):
            require(re.fullmatch(r"final(?:-[0-9]{4})?", previous.parent.name), "最終準備路徑無效")
            ref = review.reference(previous)
            if review.ref_data(ref).get("status") == "prepared":
                preparation_ref = ref
                break
        if preparation_ref is None:
            for index in range(1, 10000):
                final = attempt / ("final" if index == 1 else f"final-{index:04d}")
                if not final.exists():
                    break
            else:
                raise ValueError("最終準備嘗試次數超界")
            matrix.preparation.prepare(candidate["candidate"]["path"], candidate["candidate"]["sha256"],
                                       family="mediatek", board="bpi-r2", kernel_release=repack.RELEASE,
                                       output=final, layout="disk", max_raw_bytes=row["contract"]["raw_bytes"], timeout=1800)
            preparation_ref = review.reference(final / "preparation.json")
    verified = verify_candidate(candidate_ref, preparation_ref, row, plan["source_root"])
    receipt = {"schema": "bpi-r2-matrix-result-v1", "plan": plan_ref, "row_sha256": matrix.queue.digest(row),
               "image_id": row["entry"]["image_id"], "release": row["entry"]["release"],
               "variant": row["entry"]["variant"], "source_sha256": row["entry"]["expected_sha256"],
               "status": "prepared", "reused_legacy": legacy, "reused_previous": origin is not None,
               "origin_receipt": origin, "attempt": str(attempt) if attempt else None,
               "candidate_manifest": candidate_ref, "preparation": preparation_ref, "verified": verified,
               "artifacts": matrix.artifact_digest(attempt) if attempt else None, "internal_only": True,
               "hardware_validated": False, "source_modified": False, "boot_blob_redistribution_authorized": False}
    matrix.publish_json(path, receipt)
    print(json.dumps({key: receipt[key] for key in ("image_id", "release", "variant", "status", "reused_legacy")},
                     ensure_ascii=False), flush=True)
    return receipt


def run(plan_ref):
    plan = review.ref_data(plan_ref)
    root, rows = source_rows()
    validate_plan(plan, root, rows)
    output = plan_directory(plan_ref)
    with matrix.lock(output):
        require(plan["tools"] == matrix.tool_binding(), "工具變動，請保留舊結果另建計畫")
        results = []
        for release, group in groupby(rows, key=lambda row: row["entry"]["release"]):
            require(shutil.disk_usage(output).free >= 80 * 1024**3, "候選批次剩餘空間不足")
            print(json.dumps({"release": release}, ensure_ascii=False), flush=True)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results.extend(pool.map(lambda row: execute_row(plan_ref, plan, row, output), group))
            require(plan["tools"] == matrix.tool_binding(), "執行期間工具漂移")
        require(len(results) == len(rows) == 10
                and {item["image_id"] for item in results} == {row["entry"]["image_id"] for row in rows},
                "完成收據未覆蓋十套唯一來源")
        result = {"schema": "bpi-r2-matrix-summary-v1", "plan": plan_ref, "checked": len(results),
                  "prepared": sum(row["status"] == "prepared" for row in results),
                  "reused_legacy": sum(row["reused_legacy"] for row in results),
                  "reused_previous": sum(row.get("reused_previous", False) for row in results), "internal_only": True,
                  "hardware_validated": False, "source_modified": False, "boot_blob_redistribution_authorized": False,
                  "results": [review.reference(output / "jobs" / row["image_id"] / "receipt.json") for row in results]}
        if (output / "summary.json").exists():
            require(matrix.queue.read_json(output / "summary.json") == result, "續作總結與原紀錄不同")
        else:
            matrix.publish_json(output / "summary.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description="固定十套 R2 內部候選，不改原來源或硬體")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="固定十套來源與工具")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--previous-plan", type=Path)
    init.add_argument("--previous-plan-sha256")
    execute = sub.add_parser("run", help="建立候選或核對後續作")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    if args.command == "init":
        require(bool(args.previous_plan) == bool(args.previous_plan_sha256), "舊計畫路徑與摘要須同時提供")
        previous = {"path": str(args.previous_plan.absolute()), "sha256": args.previous_plan_sha256} \
            if args.previous_plan else None
        result = initialize(args.output, previous)
    else:
        result = run({"path": str(args.plan.absolute()), "sha256": args.plan_sha256})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
