#!/usr/bin/env python3
"""E3：固定來源、逐筆重播及完整清單候選；不更新既有硬體資料庫。"""

import argparse
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

import bpi_lab_catalog as catalog_api
import bpi_lab_image as image
import bpi_lab_prepare as preparation
import bpi_lab_queue as queue

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "output/evidence/bpi-metadata-review-20260918"
BOARDS = {"bpi-f2p": "bananapif2p", "bpi-f2s": "bananapif2s"}
RELEASES = ("bookworm", "jammy", "noble", "resolute", "trixie")
VARIANTS = ("minimal", "xfce_desktop")
MATRIX = {(b, r, v) for b in BOARDS for r in RELEASES for v in VARIANTS}
require = queue.require
encode = queue.encode
digest = queue.digest


def read_fixed(path, expected, *, limit=32 * 1024**2):
    require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected), "須明示固定 SHA-256")
    path = Path(path).absolute()
    with catalog_api._root(path.parent) as root:
        with catalog_api._regular(root, path.name) as (stream, identity):
            require(0 < identity["st_size"] <= limit, "固定檔案大小超界")
            blob = stream.read(limit + 1)
            require(len(blob) == identity["st_size"] and hashlib.sha256(blob).hexdigest() == expected,
                    "固定檔案摘要不符：" + str(path))
    return queue.decode(blob)


def reference(path):
    path = Path(path).absolute()
    with catalog_api._root(path.parent) as root:
        with catalog_api._regular(root, path.name) as (stream, identity):
            require(0 < identity["st_size"] <= 32 * 1024**2, "證據大小超界")
            blob = stream.read(32 * 1024**2 + 1)
            require(len(blob) == identity["st_size"], "證據讀取截斷")
    return {"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()}


def ref_data(ref):
    validate_ref(ref)
    return read_fixed(ref["path"], ref["sha256"])


def validate_ref(ref):
    require(type(ref) is dict and set(ref) == {"path", "sha256"}
            and type(ref["path"]) is str and Path(ref["path"]).is_absolute()
            and ".." not in Path(ref["path"]).parts
            and type(ref["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]),
            "固定引用須為明確絕對路徑與 SHA-256，不能含未知欄位")


def new_output(path):
    path = Path(path).absolute()
    require(path != OUTPUT_ROOT and path.is_relative_to(OUTPUT_ROOT) and ".." not in path.parts,
            "新證據只能位於 E3 獨立目錄的子目錄")
    queue.safe_directory(path.parent, create=True)
    return image.create_directory(path)


def source_guard(root, entry):
    """檔案與旁檔須仍為固定盤點身分；重播不假冒重新讀取 XZ。"""
    with catalog_api._root(root) as fd:
        catalog_api._unchanged(fd, entry["relative_path"], entry["identity"])
        sha, sidecar = catalog_api._sha(fd, entry["relative_path"])
        require(sha == entry["expected_sha256"] and sidecar == entry["sidecar"], "原來源旁檔已變動")


def validate_catalog(data):
    require(type(data) is dict and data.get("schema") == "bpi-lab-catalog-v1"
            and data.get("hardware_validated") is False and data.get("metadata_only") is True
            and "metadata_review" not in data, "只接受未修正的離線盤點清單")
    entries = data.get("entries")
    require(type(entries) is list and len(entries) == 444, "必須保留完整 444 筆盤點，不接受子集")
    require(len({x["image_id"] for x in entries}) == 444
            and len({x["relative_path"] for x in entries}) == 444, "映像識別或來源路徑重複")
    require(type(data.get("root")) is str and Path(data["root"]).is_absolute(), "來源根目錄須為絕對路徑")
    boards = data.get("boards")
    require(type(boards) is list and len(boards) == 45
            and {x["board"] for x in boards} == {x["board"] for x in entries}, "完整 45 板登錄不符")
    targets, problems = [], []
    for entry in entries:
        if entry["board"] not in BOARDS:
            require(entry.get("issues") == [] and entry.get("kernel") not in (None, "0", "unknown"),
                    "非 E3 範圍含待修問題，拒絕擴張")
            continue
        issues = entry.get("issues")
        require(type(issues) is list and len(issues) == 1 and issues[0].get("code") == "unknown_kernel"
                and entry.get("kernel") == "0", "只接受原核心 0 且唯一問題為 unknown_kernel")
        name = Path(entry["relative_path"]).name
        expected_issues = []
        fields = catalog_api._filename(name, expected_issues)
        require(issues == expected_issues and all(entry.get(k) == v for k, v in fields.items()),
                "板型、OS、角色、分支或問題與原檔名不一致")
        require(entry["relative_path"] == entry["board"] + "/" + name
                and entry["artifact_board"] == BOARDS[entry["board"]]
                and entry.get("architecture") == "arm32" and entry.get("family") == "sunplus-sp7021-bpi"
                and entry["branch"] == "legacy", "Sunplus 來源範圍不符")
        require(type(entry.get("expected_sha256")) is str
                and re.fullmatch(r"[0-9a-f]{64}", entry["expected_sha256"]), "來源缺固定摘要")
        identity = entry.get("identity")
        require(type(identity) is dict and set(identity) == set(catalog_api.IDENTITY_FIELDS)
                and all(type(v) is int and v >= 0 for v in identity.values())
                and identity["st_size"] == entry["compressed_bytes"] > 0, "來源身分不完整")
        require(entry["image_id"] == catalog_api._image_id(entry) and "metadata_review" not in entry,
                "原映像識別不符或已重審")
        targets.append(entry)
        problems.extend(issues)
    require(len(targets) == 20 and {(x["board"], x["release"], x["variant"]) for x in targets} == MATRIX,
            "必須恰為 F2P／F2S 五個 OS、兩個角色的 20 筆矩陣")
    require(len({x["expected_sha256"] for x in targets}) == 20, "不同來源不可共用單一抽樣摘要")
    require(sorted(map(encode, data.get("issues", []))) == sorted(map(encode, problems)),
            "清單總問題含未知問題或不一致")
    for board in boards:
        wanted = [p for x in targets if x["board"] == board["board"] for p in x["issues"]]
        require(sorted(map(encode, board.get("issues", []))) == sorted(map(encode, wanted)),
                "板級問題含未知問題或不一致")
    return targets


def entry_binding(entry):
    return {"image_id": entry["image_id"], "entry_sha256": digest(entry), "board": entry["board"],
            "release": entry["release"], "variant": entry["variant"],
            "source_sha256": entry["expected_sha256"]}


def extraction_chain(ref, root, entry):
    """每一層固定擷取均須回到相同的逐筆來源，不接受跨映像重播。"""
    seen, chain = set(), []
    for _ in range(8):
        require(ref["path"] not in seen, "擷取追溯循環")
        seen.add(ref["path"])
        data = ref_data(ref)
        require(data.get("schema") in ("bpi-lab-image-v1", "bpi-lab-image-replay-v1")
                and data.get("ok") is True and data.get("source_verified") is True
                and data.get("hardware_validated") is False and data.get("mounted") is False
                and data.get("image_code_executed") is False, "擷取未完成或含硬體操作")
        require(data.get("source") == str(Path(root) / entry["relative_path"])
                and data.get("source_digest") == {"bytes": entry["compressed_bytes"],
                                                  "sha256": entry["expected_sha256"]},
                "擷取來源摘要、板名、OS 或角色不屬於此筆")
        chain.append(ref)
        if data["schema"] == "bpi-lab-image-v1":
            require(data.get("source_kind") == "xz", "最初擷取必須來自完整 XZ")
            return chain
        require(data.get("source_reread") is False, "重播不得宣稱重讀來源")
        origin = data.get("replay_of", {})
        ref = {"path": origin.get("path"), "sha256": origin.get("sha256")}
    raise ValueError("擷取追溯超過八層")


def prepare_sources(catalog_ref, output, replay_ref=None, *, timeout=1800):
    data = ref_data(catalog_ref)
    targets = validate_catalog(data)
    replays = {}
    if replay_ref:
        index = ref_data(replay_ref)
        require(set(index) == {"schema", "entries"} and index["schema"] == "bpi-metadata-replays-v1",
                "重播索引格式不符")
        for row in index["entries"]:
            require(set(row) == {"image_id", "extraction"} and row["image_id"] not in replays
                    and row["image_id"] in {x["image_id"] for x in targets}, "重播索引重複或擴張範圍")
            replays[row["image_id"]] = row["extraction"]
    output = new_output(output)
    records = []
    for entry in targets:
        row = entry_binding(entry)
        try:
            source_guard(data["root"], entry)
            replay = replays.get(entry["image_id"])
            if replay:
                extraction_chain(replay, data["root"], entry)
            source = replay["path"] if replay else str(Path(data["root"]) / entry["relative_path"])
            sha = replay["sha256"] if replay else entry["expected_sha256"]
            directory = output / entry["image_id"]
            result = preparation.prepare(source, sha, family="sunplus", board=entry["board"],
                                         kernel_release="0", output=directory, layout="disk",
                                         from_extraction=bool(replay), timeout=timeout)
            source_guard(data["root"], entry)
            row.update(preparation=reference(directory / "preparation.json"), status=result["status"],
                       source_reread=result["source_reread"])
            if result["status"] != "prepared":
                row["error"] = result.get("error", result.get("blockers"))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            row.update(status="blocked", error=str(exc))
        records.append(row)
        print(json.dumps({"board": entry["board"], "release": entry["release"], "variant": entry["variant"],
                          "status": row["status"], "completed": len(records)}, ensure_ascii=False), flush=True)
    result = {"schema": "bpi-metadata-review-request-v1", "catalog": catalog_ref,
              "replays": replay_ref, "entries": records, "hardware_validated": False}
    queue.save_json(output / "request.json", result)
    return result


def verify_preparation(root, entry, ref, output):
    source_guard(root, entry)
    old = ref_data(ref)
    require(old.get("schema") == "bpi-lab-prepare-v1" and old.get("status") == "prepared"
            and old.get("component_status") == "prepared" and old.get("blockers") == []
            and old.get("board") == entry["board"] and old.get("family") == "sunplus",
            "原準備未通過或板型不符")
    require(all(old.get(k) is False for k in ("hardware_validated", "whole_backend_ready", "media_written", "boot_executed"))
            and type(old.get("source_reread")) is bool, "準備證據越過離線界線")
    base = Path(ref["path"]).absolute().parent
    require(old["extraction"]["path"] == "extraction/extraction.json"
            and old["components"]["path"] == "family-result.json", "準備引用路徑不符")
    ext_ref = {"path": str(base / old["extraction"]["path"]), "sha256": old["extraction"]["sha256"]}
    component_ref = {"path": str(base / "family-result.json"), "sha256": old["components"]["sha256"]}
    ext, component = ref_data(ext_ref), ref_data(component_ref)
    chain = extraction_chain(ext_ref, root, entry)
    require(old["source_reread"] == (ext["schema"] == "bpi-lab-image-v1"), "重讀旗標與擷取型別不符")
    require(old.get("source") == ext["source_digest"] and old.get("raw") == ext["raw"]
            and old.get("partition") == ext["partition"], "準備與擷取來源或分割不一致")
    checked = preparation.prepare(ext_ref["path"], ext_ref["sha256"], family="sunplus", board=entry["board"],
                                  kernel_release="0", output=output, from_extraction=True,
                                  layout=old["layout"])
    require(checked["status"] == "prepared" and checked.get("blockers") == [], "固定原組件重播仍有阻擋")
    require(checked["components"] == old["components"]
            and checked["kernel_release"] == old["kernel_release"]
            and checked.get("root_binding") == old.get("root_binding")
            and checked.get("root_identity_verified") is True, "準備派生結果遭修改或來源不一致")
    kernel = component["components"]["kernel"]
    require(component["requested_kernel_release"] == "0"
            and component["release"]["BOARD"] == entry["artifact_board"]
            and kernel["format"] == "uImage" and kernel["header_crc_verified"] is True
            and kernel["data_crc_verified"] is True
            and kernel["kernel_release"] == checked["kernel_release"] != "0"
            and component["components"]["initrd"]["module_releases"] == [checked["kernel_release"]],
            "核心版本缺少逐筆原核心與 initrd 真證據")
    source_guard(root, entry)
    require(ref_data(ref) == old and ref_data(ext_ref) == ext and ref_data(component_ref) == component,
            "重審期間固定證據變動")
    return {"kernel": checked["kernel_release"], "preparation": ref, "extraction": ext_ref,
            "extraction_chain": chain, "components": component_ref,
            "revalidation": reference(output / "preparation.json"),
            "source_reread": old["source_reread"], "revalidation_source_reread": False,
            "source_sha256": entry["expected_sha256"], "source_identity": entry["identity"],
            "binding": entry_binding(entry), "hardware_validated": False}


def review(catalog_ref, request_ref, output, *, reviewer, reason):
    require(type(reviewer) is str and reviewer.strip() and type(reason) is str and reason.strip(),
            "須具名記錄核對者與原因，不代表整合批准")
    data, request = ref_data(catalog_ref), ref_data(request_ref)
    targets = validate_catalog(data)
    require(request.get("schema") == "bpi-metadata-review-request-v1"
            and request.get("catalog") == catalog_ref and request.get("hardware_validated") is False,
            "審查請求未綁定原固定清單")
    rows = request.get("entries")
    require(type(rows) is list and len(rows) == 20
            and {x["image_id"] for x in rows} == {x["image_id"] for x in targets}, "須逐筆提供全部 20 份證據")
    records = {x["image_id"]: x for x in rows}
    seen = set()
    for entry in targets:
        row = records[entry["image_id"]]
        require(all(row.get(k) == v for k, v in entry_binding(entry).items()), "請求板名、OS、角色或原 metadata 錯配")
        require(set(row) <= set(entry_binding(entry)) | {"preparation", "status", "source_reread", "error"},
                "請求含未知更新欄位")
        if "preparation" in row:
            signature = (row["preparation"]["path"], row["preparation"]["sha256"])
            require(signature not in seen, "不可用單一抽樣批次放行")
            seen.add(signature)
    output = new_output(output)
    audits = []
    for entry in targets:
        row = records[entry["image_id"]]
        audit = entry_binding(entry)
        try:
            require(row.get("status") == "prepared", "逐筆來源準備阻擋：" + str(row.get("error", "缺準備證據")))
            proof = verify_preparation(data["root"], entry, row["preparation"], output / entry["image_id"])
            require(row.get("source_reread") is proof["source_reread"], "請求重讀旗標錯配")
            audit.update(status="reviewed", evidence=proof)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            audit.update(status="blocked", error=str(exc))
        audits.append(audit)
    blocked = [row for row in audits if row["status"] != "reviewed"]
    report = {"schema": "bpi-metadata-review-v1", "status": "blocked" if blocked else "candidate",
              "catalog": catalog_ref, "request": request_ref, "reviewer": reviewer, "reason": reason,
              "entries": audits, "reviewed": len(audits) - len(blocked), "blocked": len(blocked),
              "hardware_validated": False, "database_modified": False, "integration_approved": False,
              "scope": "只修正原核心版本 metadata；OS／角色依固定來源檔名配對，不宣稱 rootfs 安裝或實板通過。"}
    record_ref = queue.save_json(output / "review-record.json", report)
    report["review_record"] = record_ref
    candidate = None
    if not blocked:
        for entry in targets:
            source_guard(data["root"], entry)
        candidate = make_candidate(data, audits, catalog_ref, request_ref, reviewer, reason, record_ref)
        report["candidate"] = {"path": str(output / "catalog-candidate.json"), "sha256": digest(candidate)}
    report_ref = queue.save_json(output / "review.json", report)
    if candidate is not None:
        queue.save_json(output / "catalog-candidate.json", candidate)
    return {**report_ref, "status": report["status"], "reviewed": report["reviewed"], "blocked": report["blocked"],
            "candidate": report.get("candidate")}


def make_candidate(original, audits, catalog_ref, request_ref, reviewer, reason, record_ref):
    validate_catalog(original)
    require(len(audits) == 20 and all(x["status"] == "reviewed" for x in audits), "來源未全數核對")
    result = copy.deepcopy(original)
    proofs = {x["image_id"]: x for x in audits}
    for entry in result["entries"]:
        if entry["image_id"] not in proofs:
            continue
        proof = proofs[entry["image_id"]]
        entry.update(kernel=proof["evidence"]["kernel"], issues=[], metadata_review={
            "schema": "bpi-metadata-entry-review-v1", "original_entry_sha256": digest(entry),
            "evidence": proof["evidence"], "catalog": catalog_ref, "request": request_ref,
            "review_record": record_ref, "reviewer": reviewer, "reason": reason,
            "integration_approved": False, "hardware_validated": False})
    result["issues"] = []
    for board in result["boards"]:
        if board["board"] in BOARDS:
            board["issues"] = []
    result["metadata_review"] = {"schema": "bpi-metadata-candidate-v1", "catalog": catalog_ref,
                                 "request": request_ref, "changed": 20, "unchanged": 424,
                                 "review_record": record_ref, "reviewer": reviewer, "reason": reason,
                                 "integration_approved": False, "hardware_validated": False}
    validate_candidate(original, result)
    return result


def validate_candidate(original, candidate):
    targets = {x["image_id"] for x in validate_catalog(original)}
    summary = candidate.get("metadata_review")
    require(type(summary) is dict and set(summary) == {
        "schema", "catalog", "request", "review_record", "reviewer", "reason", "changed", "unchanged",
        "integration_approved", "hardware_validated"}, "候選摘要欄位不符")
    require(summary["schema"] == "bpi-metadata-candidate-v1" and type(summary["changed"]) is int
            and summary["changed"] == 20 and type(summary["unchanged"]) is int and summary["unchanged"] == 424
            and summary["integration_approved"] is False and summary["hardware_validated"] is False,
            "候選摘要範圍或批准聲明不符")
    for field in ("catalog", "request", "review_record"):
        validate_ref(summary[field])
    require(all(type(summary[k]) is str and summary[k].strip() for k in ("reviewer", "reason")),
            "候選審閱者及原因缺失")
    require(len(candidate["entries"]) == 444, "候選筆數不符")
    expected = copy.deepcopy(original)
    for old, new, allowed in zip(original["entries"], candidate["entries"], expected["entries"]):
        if old["image_id"] in targets:
            require(type(new.get("kernel")) is str and re.fullmatch(r"[0-9]+\.[0-9]+\.[A-Za-z0-9_.+~-]+", new["kernel"])
                    and new.get("issues") == [] and type(new.get("metadata_review")) is dict,
                    "候選核心或追溯不完整")
            trace = new["metadata_review"]
            require(set(trace) == {"schema", "original_entry_sha256", "evidence", "catalog", "request",
                                   "review_record", "reviewer", "reason", "integration_approved", "hardware_validated"}
                    and trace["schema"] == "bpi-metadata-entry-review-v1"
                    and trace["original_entry_sha256"] == digest(old)
                    and trace["integration_approved"] is False and trace["hardware_validated"] is False
                    and all(trace[k] == summary[k] for k in ("catalog", "request", "review_record", "reviewer", "reason")),
                    "候選未綁定原 metadata、引用、審閱者或離線界線")
            proof = trace["evidence"]
            require(type(proof) is dict and set(proof) == {
                "kernel", "preparation", "extraction", "extraction_chain", "components", "revalidation",
                "source_reread", "revalidation_source_reread", "source_sha256", "source_identity", "binding", "hardware_validated"},
                "逐筆證據欄位不符")
            require(proof["kernel"] == new["kernel"] and proof["binding"] == entry_binding(old)
                    and proof.get("source_sha256") == old["expected_sha256"]
                    and proof.get("source_identity") == old["identity"]
                    and proof.get("hardware_validated") is False
                    and type(proof.get("source_reread")) is bool
                    and proof.get("revalidation_source_reread") is False,
                    "候選版本或來源與逐筆追溯不一致")
            for field in ("preparation", "extraction", "components", "revalidation"):
                validate_ref(proof[field])
            require(type(proof["extraction_chain"]) is list and 1 <= len(proof["extraction_chain"]) <= 8
                    and proof["extraction_chain"][0] == proof["extraction"], "擷取追溯缺少起點或超界")
            for ref in proof["extraction_chain"]:
                validate_ref(ref)
            allowed.update(kernel=new["kernel"], issues=[], metadata_review=new["metadata_review"])
        require(allowed == new, "候選變更超出原核心、問題及追溯，或動到其他 424 筆")
    expected["issues"] = []
    for board in expected["boards"]:
        if board["board"] in BOARDS:
            board["issues"] = []
    expected["metadata_review"] = candidate["metadata_review"]
    require(candidate == expected, "候選頂層或板級變更越界")


def verify_candidate_evidence(original, candidate, catalog_ref, output):
    """在示範匯入之前讀固定審閱紀錄，並重新核對每份原準備及當時的重驗證。"""
    validate_candidate(original, candidate)
    summary = candidate["metadata_review"]
    require(summary["catalog"] == catalog_ref and ref_data(catalog_ref) == original, "候選原清單引用錯配")
    request, record = ref_data(summary["request"]), ref_data(summary["review_record"])
    require(set(request) == {"schema", "catalog", "replays", "entries", "hardware_validated"}
            and request["schema"] == "bpi-metadata-review-request-v1" and request["catalog"] == catalog_ref
            and request["hardware_validated"] is False, "固定請求不屬於原清單")
    require(set(record) == {"schema", "status", "catalog", "request", "reviewer", "reason", "entries", "reviewed",
                            "blocked", "hardware_validated", "database_modified", "integration_approved", "scope"}
            and record["schema"] == "bpi-metadata-review-v1" and record["status"] == "candidate"
            and record["reviewed"] == 20 and record["blocked"] == 0
            and all(record[k] is False for k in ("hardware_validated", "database_modified", "integration_approved"))
            and all(record[k] == summary[k] for k in ("catalog", "request", "reviewer", "reason")),
            "固定審閱紀錄與候選契約不符")
    targets = validate_catalog(original)
    expected_ids = {x["image_id"] for x in targets}
    for rows in (request["entries"], record["entries"]):
        require(type(rows) is list and len(rows) == 20 and {x["image_id"] for x in rows} == expected_ids,
                "固定請求或審閱紀錄不是逐筆 20 份")
    requests = {x["image_id"]: x for x in request["entries"]}
    audits = {x["image_id"]: x for x in record["entries"]}
    candidates = {x["image_id"]: x for x in candidate["entries"]}
    output = image.create_directory(output)
    results = []
    for entry in targets:
        proof = candidates[entry["image_id"]]["metadata_review"]["evidence"]
        req, audit = requests[entry["image_id"]], audits[entry["image_id"]]
        binding = entry_binding(entry)
        require(audit == {**binding, "status": "reviewed", "evidence": proof}, "候選 proof 不等於固定審閱紀錄")
        require(req == {**binding, "status": "prepared", "preparation": proof["preparation"],
                        "source_reread": proof["source_reread"]}, "逐筆準備與固定請求不符")
        current = verify_preparation(original["root"], entry, proof["preparation"], output / (entry["image_id"] + "-source"))
        require({k: v for k, v in current.items() if k != "revalidation"}
                == {k: v for k, v in proof.items() if k != "revalidation"}, "候選 proof 與重新解析結果不符")
        prior = verify_preparation(original["root"], entry, proof["revalidation"], output / (entry["image_id"] + "-review"))
        require(prior["kernel"] == proof["kernel"] and prior["components"]["sha256"] == proof["components"]["sha256"]
                and prior["source_reread"] is False and prior["extraction_chain"][1:] == proof["extraction_chain"],
                "當時的固定重驗證不屬於此份 preparation／extraction")
        results.append({"image_id": entry["image_id"], "preparation_check": current["revalidation"],
                        "revalidation_check": prior["revalidation"]})
    require(ref_data(summary["request"]) == request and ref_data(summary["review_record"]) == record,
            "示範前固定請求或審閱紀錄改變")
    return results


def validate_job_snapshot(db, original):
    """先驗既有工作本身，再與原清單及目前停用站點逐筆交叉核對。"""
    wanted = {e["image_id"]: e for e in original["entries"]}
    images = db.execute("SELECT * FROM images").fetchall()
    require(len(images) == 444 and all(r["root"] == original["root"] for r in images)
            and {r["image_id"]: queue.decode(r["body"]) for r in images} == wanted, "既有映像表與原清單不一致")
    keys = [r[0] for r in db.execute("SELECT work_key FROM jobs")]
    require(len(keys) == 444, "原工作數量不是 444")
    stations = [dict(r) for r in db.execute("SELECT * FROM stations ORDER BY station_id")]
    require(len(stations) == 45, "原站點數量不是 45")
    configs = {}
    for row in stations:
        station, _ = queue.get_station(db, row["station_id"])
        queue.station_api.validate_station(station)
        require(station["enabled"] is False and station["mode"] == "hardware", "原站點必須停用且為硬體模式")
        configs[row["station_id"]] = station
    before, seen = {}, set()
    for key in keys:
        job = queue.get_job(db, key)
        body = queue.decode(job["body"])
        entry = body["image"]
        station = configs[job["station_id"]]
        require(body["mode"] == station["mode"] == "hardware", "既有工作模式必須與硬體站點一致")
        require(entry["image_id"] not in seen and entry == wanted.get(entry["image_id"])
                and body["image_root"] == original["root"] and body["station"] == station
                and body["hardware_id"] == station["hardware_id"]
                and body["boot_config_sha256"] == station["boot_config_sha256"]
                and body["test_version"] == station["test_version"]
                and entry["board"] == station["board"] and entry["board"] in station["compatible_boards"],
                "既有工作來源、根目錄、板名或站點不屬於原清單")
        require(job["attempt_id"] is None and job["inflight"] is None and job["cursor"] == 0,
                "原工作已有執行或未結束階段")
        allowed = ("metadata_blocked",) if entry["issues"] else ("queued", "review_required")
        require(job["state"] in allowed, "原工作狀態與 metadata 不一致")
        seen.add(entry["image_id"])
        before[key] = job
    require(seen == set(wanted), "444 工作與原清單不是一對一")
    require(db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
            and db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0, "原資料庫已有實機操作")
    return before, stations


def demonstrate(catalog_ref, candidate_ref, database_ref, output):
    original, candidate = ref_data(catalog_ref), ref_data(candidate_ref)
    validate_candidate(original, candidate)
    source = Path(database_ref["path"]).absolute()
    require(reference(source) == database_ref, "既有資料庫摘要不符")
    require(not any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")),
            "既有資料庫有活動旁檔，拒絕 immutable 快照")
    output = new_output(output)
    proofs = verify_candidate_evidence(original, candidate, catalog_ref, output / "evidence-check")
    require(ref_data(candidate_ref) == candidate, "示範前候選清單已變動")
    database = output / "demonstration.sqlite3"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as read_only:
        read_only.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(database)) as destination:
            read_only.backup(destination)
    db = queue.connect(database)
    try:
        before, stations = validate_job_snapshot(db, original)
        blocked = {k for k, r in before.items() if r["state"] == "metadata_blocked"}
        targets = {x["image_id"] for x in validate_catalog(original)}
        require(len(blocked) == 20 and {queue.decode(before[k]["body"])["image"]["image_id"] for k in blocked} == targets,
                "原阻擋工作不是這 20 筆來源")
        queue.import_catalog(db, candidate)
        for station in stations:
            queue.schedule(db, station["station_id"])
        after = {r["work_key"]: dict(r) for r in db.execute("SELECT * FROM jobs")}
        require(all(after[k] == row for k, row in before.items() if k not in blocked), "其他 424 筆工作被改動")
        require(all(after[k]["state"] == "superseded" and after[k]["body"] == before[k]["body"] for k in blocked),
                "舊 20 筆未保留為 superseded")
        require(stations == [dict(r) for r in db.execute("SELECT * FROM stations ORDER BY station_id")], "站點被改動")
        added = sorted(set(after) - set(before))
        require(len(added) == 20 and all(after[k]["state"] == "queued" for k in added)
                and {queue.decode(after[k]["body"])["image"]["image_id"] for k in added} == targets,
                "新工作數量、來源或狀態不符")
        require(db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
                and db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0, "示範產生硬體嘗試")
        result = {"schema": "bpi-metadata-reimport-demo-v1", "ok": True, "catalog": catalog_ref,
                  "candidate": candidate_ref, "original_database": database_ref, "copy": str(database),
                  "old_superseded": sorted(blocked), "new_queued": added, "unchanged_jobs": 424,
                  "disabled_stations": 45, "attempts": 0, "reports": 0, "hardware_validated": False,
                  "evidence_checks": proofs,
                  "original_job_contracts_verified": 444,
                  "original_database_modified": False}
    finally:
        db.close()
    require(reference(source) == database_ref
            and not any(Path(str(source) + s).exists() for s in ("-wal", "-shm", "-journal")),
            "示範期間既有資料庫或旁檔有變動")
    queue.save_json(output / "demonstration.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="E3 逐筆 metadata 審閱；不操作硬體或更新既有 SQLite")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "review", "demonstrate"):
        command = commands.add_parser(name, help={"prepare": "逐筆固定來源準備", "review": "重播並產生完整候選", "demonstrate": "只在資料庫副本示範重匯入"}[name])
        command.add_argument("--catalog", required=True)
        command.add_argument("--catalog-sha256", required=True)
        command.add_argument("--output", required=True)
        if name == "prepare":
            command.add_argument("--replays")
            command.add_argument("--replays-sha256")
            command.add_argument("--timeout", type=int, default=1800)
        elif name == "review":
            command.add_argument("--request", required=True)
            command.add_argument("--request-sha256", required=True)
            command.add_argument("--reviewer", required=True)
            command.add_argument("--reason", required=True)
        else:
            command.add_argument("--candidate", required=True)
            command.add_argument("--candidate-sha256", required=True)
            command.add_argument("--db", required=True)
            command.add_argument("--db-sha256", required=True)
    args = parser.parse_args(argv)
    def ref(name):
        return {"path": str(Path(getattr(args, name)).absolute()), "sha256": getattr(args, name + "_sha256")}
    try:
        if args.command == "prepare":
            require(bool(args.replays) == bool(args.replays_sha256), "重播索引與摘要須成對")
            result = prepare_sources(ref("catalog"), args.output, ref("replays") if args.replays else None, timeout=args.timeout)
            ok = all(x["status"] == "prepared" for x in result["entries"])
        elif args.command == "review":
            result = review(ref("catalog"), ref("request"), args.output, reviewer=args.reviewer, reason=args.reason)
            ok = result["status"] == "candidate"
        else:
            result = demonstrate(ref("catalog"), ref("candidate"), ref("db"), args.output)
            ok = result["ok"]
        print(encode(result).decode(), end="")
        return 0 if ok else 2
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
