"""純 JSON 進度函式；不讀寫檔案、不操作硬體、不推定原始實測報告語意。"""

import copy
import hashlib
import json
import re
import bpi_h618_artifacts as safe

SCHEMA = "bpi-h618-lab-progress-v1"
STAGES = ("deployment", "customer_kernel", "identity", "cpu", "fs", "mem", "network", "recovery", "services")
ROLES = ("kernel", "initrd", "dtb", "overlay", "fixup")
require = safe.require


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def check_hash(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "SHA-256 格式無效")


def decoded(blob, expected=None):
    require(isinstance(blob, bytes) and len(blob) <= 32 * 1024**2, "JSON 大小或型別無效")
    if expected is not None:
        check_hash(expected)
        require(digest(blob) == expected, "JSON 與固定 SHA-256 不符")
    return json.loads(blob, object_pairs_hook=safe.unique_object,
                      parse_constant=lambda _: require(False, "JSON 不允許非有限數值"))


def work_key(identity):
    require(set(identity) == {"board_model", "hardware_id", "image_raw_sha256", "boot_sha256", "test_version"}, "工作鍵欄位不完整")
    for field in ("board_model", "hardware_id", "test_version"):
        require(isinstance(identity[field], str) and identity[field].strip(), "板型、板號及測試版本必須明示")
    check_hash(identity["image_raw_sha256"])
    require(isinstance(identity["boot_sha256"], dict) and set(ROLES) <= set(identity["boot_sha256"]), "缺少開機組件")
    for value in identity["boot_sha256"].values():
        check_hash(value)
    return digest(json.dumps({"schema": SCHEMA, **identity}, sort_keys=True, separators=(",", ":")).encode())


def new_ledger():
    return {"schema": SCHEMA, "items": {}}


def register(ledger, manifest_blob, manifest_sha256, *, board_model, hardware_id, boot_sha256, test_version):
    require(ledger.get("schema") == SCHEMA, "進度清單版本不符")
    manifest = decoded(manifest_blob, manifest_sha256)
    require(manifest.get("schema") == "bpi-h618-customer-components-v1" and manifest.get("hardware_validated") is False,
            "須使用固定的靜態 components 清單")
    require(all(manifest.get("preflight", {}).get(k) is True for k in
                ("source_verified", "legacy_initrd_verified", "mmc_support_verified")), "靜態前提未通過")
    require(all(boot_sha256[k] == manifest["files"][k]["sha256"] for k in ("kernel", "initrd")), "不得換用共用核心或 initrd")
    identity = dict(board_model=board_model, hardware_id=hardware_id, boot_sha256=boot_sha256,
                    image_raw_sha256=manifest["image"]["raw"]["sha256"], test_version=test_version)
    key, result = work_key(identity), copy.deepcopy(ledger)
    item = dict(work_key=key, identity=copy.deepcopy(identity), manifest_sha256=manifest_sha256,
                artifact_board=manifest["board"], attempt_id=None, checks={}, history=[])
    require(key not in result["items"] or result["items"][key]["manifest_sha256"] == manifest_sha256, "同工作鍵來源已更換")
    result["items"].setdefault(key, item)
    return result, key


def begin_attempt(ledger, key, attempt_id):
    require(isinstance(attempt_id, str) and attempt_id.strip(), "嘗試識別不可空白")
    result = copy.deepcopy(ledger)
    item = result["items"][key]
    require(attempt_id not in [item["attempt_id"], *(h["attempt_id"] for h in item["history"])], "嘗試已存在，請直接續作")
    if item["attempt_id"] is not None:
        item["history"].append(dict(attempt_id=item["attempt_id"], checks=item["checks"], state=state(item)))
    item.update(attempt_id=attempt_id, checks={})
    return result


def checked_report(item, blob, sha256):
    report = decoded(blob, sha256)
    require(item["attempt_id"] is not None and work_key(item["identity"]) == item["work_key"] == report.get("work_key") and
            report.get("attempt_id") == item["attempt_id"], "報告不屬於目前工作或嘗試")
    require(report.get("stage") in STAGES and report.get("status") in ("passed", "failed", "not_applicable"), "分項狀態無效")
    services = report.get("failed_services")
    if report["stage"] == "services" or services is not None:
        require(isinstance(services, list) and all(isinstance(s, str) and s for s in services), "服務狀態必須明示")
        require(not services or report["status"] != "passed", "存在失敗服務，不可記為通過")
    return report


def record_report(ledger, key, report_blob, report_sha256, report_path):
    require(isinstance(report_path, str) and report_path.strip(), "須保留原報告路徑")
    result = copy.deepcopy(ledger)
    item = result["items"][key]
    report = checked_report(item, report_blob, report_sha256)
    previous = item["checks"].get(report["stage"])
    require(previous is None or previous["sha256"] == report_sha256, "已有不同結果；重試須另開嘗試，不抹除失敗")
    item["checks"].setdefault(report["stage"], dict(path=report_path, sha256=report_sha256, json=report_blob.decode()))
    return result


def results(item):
    found = {}
    for stage, ref in item["checks"].items():
        report = checked_report(item, ref["json"].encode(), ref["sha256"])
        require(stage == report["stage"], "分項索引與報告不符")
        found[stage] = report
    return found


def can_skip(item, stage):
    require(stage in STAGES, "未知分項")
    report = results(item).get(stage, {})
    return report.get("status") == "passed" and not report.get("failed_services")


def state(item):
    reports = results(item)
    if any(r["status"] == "failed" or r.get("failed_services") for r in reports.values()):
        return "failed"
    if all(reports.get(s, {}).get("status") == "passed" for s in STAGES):
        failed_before = any(state({**item, **h, "history": []}) == "failed" for h in item["history"])
        return "passed_after_retry" if failed_before else "passed"
    for stage, pending in (("deployment", "pending_deployment"), ("customer_kernel", "deployment_verified"),
                           ("identity", "customer_kernel_observed")):
        if reports.get(stage, {}).get("status") != "passed":
            return pending
    return "incomplete"


def summarize(ledger):
    require(ledger.get("schema") == SCHEMA, "進度清單版本不符")
    summary = {}
    for key, item in ledger["items"].items():
        require(key == item["work_key"] == work_key(item["identity"]), "清單索引與工作身分不符")
        reports = results(item)
        summary[key] = dict(state=state(item), stages={s: reports.get(s, {}).get("status", "pending") for s in STAGES})
    return summary


def dumps(ledger):
    summarize(ledger)
    return (json.dumps(ledger, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def loads(blob):
    ledger = decoded(blob)
    summarize(ledger)
    return ledger
