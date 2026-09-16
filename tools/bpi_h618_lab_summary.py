#!/usr/bin/env python3
"""唯讀彙整固定清單與歷次小型 JSON；歷史觀察不授權略過目前媒體檢查。"""

import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

import bpi_h618_artifacts as safe
import bpi_h618_lab_progress as progress

INDEX = "T0-customer-components-001/components-index.json"
INDEX_SHA256 = "57c7216efdee5dded8186d749c6aad51bff1ad39bc4066885f657bc839b574d2"
LIMIT = 4 * 1024**2
STAGES = ("deployment", "boot", "smoke", "recovery")
SUCCESS = {"deployment": "verified", "boot": "observed", "smoke": "short_verified", "recovery": "verified"}
ALIASES = {"deploy": "deployment", "return": "recovery", "boot-resume": "boot"}
T3 = re.compile(r"T3-.+-(deploy|boot-resume|boot|smoke|return|memory|transfer)-[0-9]+$")
NAMES = {"summary.json", "receipt.json", "receipt.json.partial", "report.json", "report.json.partial"}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(root, base, name, expected=None):
    digest, blob = safe.fingerprint(root, name, limit=LIMIT, keep=True)
    data = progress.decoded(blob, expected)
    safe.require(isinstance(data, dict), "JSON 頂層須為物件")
    return data, dict(path=str(base / name), relative_path=name, **digest)


def discover(root):
    """限定 T3／T4 報告名稱與深度；不進入映像、組件或日誌目錄。"""
    def walk(directory, prefix, depth):
        with os.scandir(directory) as entries:
            names = sorted((e.name, e.is_dir(follow_symlinks=False)) for e in entries)
        for name, is_dir in names:
            relative = f"{prefix}/{name}"
            if name in NAMES:
                yield relative
            elif is_dir and depth:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    yield from walk(child, relative, depth - 1)
                finally:
                    os.close(child)

    for name in sorted(os.listdir(root)):
        if not (T3.fullmatch(name) or re.fullmatch(r"T4-matrix-[0-9]+", name)):
            continue
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            yield from walk(child, name, 3 if name.startswith("T4-") else 0)
        finally:
            os.close(child)


def timestamp(data):
    for field in ("finished_utc", "finished_unix", "started_utc", "started_unix"):
        value = data.get(field)
        if value is None:
            continue
        if field.endswith("_unix"):
            safe.require(type(value) in (float, int), "報告時間型別無效")
            value = dt.datetime.fromtimestamp(value, dt.timezone.utc)
        else:
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            safe.require(value.tzinfo is not None, "報告時間缺少時區")
        return value.astimezone(dt.timezone.utc).isoformat(), field
    return None, None


def stage_of(name):
    parts = name.split("/")
    match = T3.fullmatch(parts[0])
    stage = match[1] if match else parts[2].split("-")[0] if len(parts) >= 4 else "unknown"
    return ALIASES.get(stage, stage)


def classify(stage, data, manifest):
    """只採明示結果；不執行原報告內的命令，也不將短測升格為全面通過。"""
    details = {k: data[k] for k in ("error", "kernel_release_observed", "ram_dtb_changes") if k in data}
    if stage == "deployment":
        remote = data.get("remote_state") or {}
        verified = (data.get("status") == remote.get("status") == "verified"
                    and remote.get("readback") == manifest["image"]["raw"])
        details["readback"] = remote.get("readback")
        details["remote_error"] = remote.get("error")
    elif stage == "boot":
        verified = (data.get("kernel_release_observed") == manifest["kernel_release"]
                    and (data.get("login_observed") is True or bool(data.get("prompt_observed"))))
        details["original_boot_chain_verified"] = False
    elif stage == "smoke":
        remote = data.get("remote") or {}
        details.update(checks={"identity": remote.get("identity_verified") is True,
                               "cpu": remote.get("cpu", {}).get("ok") is True,
                               "fs": remote.get("file", {}).get("ok") is True},
                       failed_services=remote.get("failed_services"), failures=remote.get("failures"),
                       elapsed_seconds=remote.get("elapsed_seconds"), all_tests_passed=False)
        failures, file = remote.get("failures") or [], remote.get("file") or {}
        space_limited = (failures and all(f.get("stage") == "file" and "暫存空間不足" in f.get("reason", "") for f in failures)
                         and file.get("bytes_written") == file.get("bytes_read") == 0)
        if space_limited:
            details.update(file_status="not_tested", reason="原始根分割區空間不足，檔案短測未執行；不是 DDR 或 OS 故障判定。")
            if remote.get("failed_services") == {"ok": True, "units": []}:
                return "blocked", details
        verified = (remote.get("ok") is True and all(details["checks"].values())
                    and remote.get("failed_services") == {"ok": True, "units": []}
                    and remote.get("failures") == [] and data.get("ssh_exitcode") == 0)
        if remote.get("failures") or (remote.get("failed_services") or {}).get("units"):
            return "failed", details
    elif stage == "recovery":
        verified = all(data.get(k) is True for k in ("recovery_verified", "independent_root_ram_verified",
                                                    "strict_ssh_verified", "sd_prefix_unchanged"))
    else:
        verified = False
    finished = data.get("finished_utc") is not None or data.get("finished_unix") is not None
    if (data.get("ok") is False and finished) or data.get("status") in ("failed", "interrupted") or data.get("error"):
        return "failed", details
    if data.get("ok") is True and verified:
        return SUCCESS[stage], details
    return "incomplete", details


def summarize(base, index_sha256=INDEX_SHA256):
    base = Path(base).absolute()
    result = dict(schema="bpi-h618-lab-summary-v1", base=str(base), scan_started_utc=utc_now(),
                  historical_only=True, can_skip_current_media=False, all_tests_passed=False,
                  boot={"original_boot_chain_verified": False}, images=[], batches=[], failures=[],
                  unbound=[], read_errors=[], conditions=[], scope="僅歷史部署、核心觀察、短測與救援；不代表桌面、周邊或長測通過。")
    with safe.open_root(base) as root:
        index, result["index"] = read_json(root, base, INDEX, index_sha256)
        entries = index["entries"]
        safe.require(index.get("schema") == "bpi-h618-customer-components-index-v1" and index.get("ok") is True
                     and type(entries) is list and len(entries) == index.get("count") and entries, "固定索引結構或數量不符")
        manifests, by_sha, by_raw, keys = {}, {}, {}, set()
        for entry in entries:
            key = f'{entry["os"]}-{entry["desktop"]}'
            manifest, source = read_json(root, base, str(Path(INDEX).parent / f"{key}.json"), entry["manifest"]["sha256"])
            safe.require(source["bytes"] == entry["manifest"]["bytes"] and key not in keys
                         and manifest.get("schema") == "bpi-h618-customer-components-v1"
                         and (manifest["os"], manifest["desktop"]) == (entry["os"], entry["desktop"]), "組件索引與清單不符或組合重複")
            raw = manifest["image"]["raw"]["sha256"]
            progress.check_hash(raw)
            safe.require(raw not in by_raw, "原始映像雜湊重複，不能唯一歸屬")
            keys.add(key)
            manifests[key], by_sha[source["sha256"]], by_raw[raw] = manifest, key, key
            result["images"].append(dict(key=key, board=manifest["board"], components=source,
                                         image=manifest["image"], stages={s: {"history": []} for s in STAGES},
                                         auxiliary=[], reused_stages=[], can_skip_current_media=False, all_tests_passed=False))
        images = {item["key"]: item for item in result["images"]}
        records = {}
        for name in discover(root):
            try:
                records[name] = read_json(root, base, name)
            except (OSError, ValueError, TypeError, RecursionError) as exc:
                result["read_errors"].append(dict(path=str(base / name), status="incomplete", error=str(exc)))
        contexts, return_hashes = {}, {}
        result["hardware_scope"] = dict(t4_board_serial=None, unknown_hardware_id=None,
                                        counts_include_unknown_hardware=True,
                                        description="依映像彙整歷史紀錄；未知板號不歸屬至 T4 硬體，不代表同板階段完成。")

        def referenced(ref):
            path = Path(ref["path"])
            name = str(path.relative_to(base)) if path.is_absolute() else str(path)
            safe.relative_parts(name)
            safe.require(name in records and records[name][1]["sha256"] == ref["sha256"], "續接來源不存在或 SHA-256 不符：" + name)
            return records[name]

        for name, (batch, source) in records.items():
            if not name.endswith("/summary.json"):
                continue
            safe.require(batch.get("schema") == "bpi-h618-lab-batch-v1"
                         and batch.get("index_sha256") == index_sha256, "批次來源索引不符：" + name)
            serial = batch.get("board_serial")
            safe.require(isinstance(serial, str) and serial.strip(), "T4 批次須明示非空板號：" + name)
            safe.require(result["hardware_scope"]["t4_board_serial"] in (None, serial), "不可合併不同板號的 T4 批次：" + name)
            result["hardware_scope"]["t4_board_serial"] = serial
            result["batches"].append(dict(source=source, **{k: batch[k] for k in
                                         ("board_serial", "status", "error", "started_unix", "finished_unix", "reused") if k in batch}))
            for reuse in batch.get("reused", []):
                key = by_raw.get(reuse.get("raw_sha256")) or f'{reuse.get("os")}-{reuse.get("desktop")}'
                if reuse.get("return_report_path"):
                    referenced(dict(path=reuse["return_report_path"], sha256=reuse["return_report_sha256"]))
                if key in images and reuse.get("return_report_sha256"):
                    return_hashes.setdefault(reuse["return_report_sha256"], set()).add(key)
            for case in batch.get("cases", []):
                key = by_sha.get(case.get("components_sha256"))
                safe.require(key == case.get("key") and key in images
                             and case.get("image", {}).get("raw") == manifests[key]["image"]["raw"], "批次映像與固定清單不符")
                contexts[f'{name.split("/")[0]}/{key}'] = (key, case, batch, source)
                for stage, ref in case.get("reused_stages", {}).items():
                    prior, prior_source = referenced(ref)
                    stage = ALIASES.get(stage, stage)
                    prior_sha = prior.get("evidence", {}).get("components", {}).get("sha256")
                    prior_raw = prior.get("source", {}).get("raw")
                    bound = (prior_sha == case["components_sha256"] if stage == "boot"
                             else prior_raw == manifests[key]["image"]["raw"] if stage == "deployment" else False)
                    safe.require(bound, "續接報告與固定映像不符")
                    images[key]["reused_stages"].append(dict(attempt=name.split("/")[0], stage=stage,
                                                           source=prior_source, declared_by=source, hash_verified=True,
                                                           can_skip_current_media=False))

        def emit(key, stage, status, source, data, *, binding, hardware_id=None, fallback=None, details=None):
            time, basis = timestamp(data)
            if time is None and fallback:
                time, basis = timestamp(fallback)
                basis = "batch_event." + basis if basis else None
            record = dict(image=key, stage=stage, status=status, source=source, attempt=source["relative_path"].split("/")[0],
                          time_utc=time, time_basis=basis, hardware_id=hardware_id, binding=binding, **(details or {}))
            if key:
                target = images[key]["stages"][stage]["history"] if stage in STAGES else images[key]["auxiliary"]
                target.append(record)
            else:
                result["unbound"].append(record)
            if status in ("failed", "interrupted"):
                result["failures"].append(record)
            if status == "blocked":
                result["conditions"].append(record)

        covered = set()
        for name, (data, source) in records.items():
            if name.endswith("/summary.json"):
                continue
            if name.endswith(".partial") and name[:-8] in records and source["sha256"] == records[name[:-8]][1]["sha256"]:
                continue
            stage, parts = stage_of(name), name.split("/")
            context = contexts.get("/".join(parts[:2]))
            ref = data.get("evidence", {}).get("components") or data.get("components") or {}
            raw = (data.get("source") or {}).get("raw", {})
            key = by_sha.get(ref.get("sha256")) or by_raw.get(raw.get("sha256"))
            binding = "components_sha256" if ref.get("sha256") else "image_raw_sha256"
            if ref.get("sha256") or raw.get("sha256"):
                safe.require(key is not None and (not context or context[0] == key), "報告來源雜湊無法歸屬：" + name)
                if raw:
                    safe.require(all(data["source"].get(k) == manifests[key]["image"][k] for k in ("raw", "compressed")),
                                 "部署來源長度或雜湊不符")
            elif context:
                key, binding = context[0], "batch_case"
            elif len(return_hashes.get(source["sha256"], set())) == 1 and stage == "recovery":
                key, binding = next(iter(return_hashes[source["sha256"]])), "reused_return_report_sha256"
            else:
                key, binding = None, "unbound"
            status, details = classify(stage, data, manifests[key]) if key else (
                "failed" if data.get("ok") is False else "incomplete", {"error": data.get("error")})
            event = next((e for e in reversed(context[1].get("events", []))
                          if ALIASES.get(e.get("stage"), e.get("stage")) == stage), {}) if context else {}
            emit(key, stage, status, source, data, binding=binding, details=details, fallback=event,
                 hardware_id=context[2].get("board_serial") if context else None)
            covered.add(("/".join(parts[:2]), stage))
        # 批次事件僅補缺漏與中斷，不以 exitcode 或 reused 宣稱階段驗證成功。
        for prefix, (key, case, batch, source) in contexts.items():
            events = list(case.get("events", []))
            if not any(e.get("stage") == case.get("stage") for e in events) and case.get("stage"):
                events.append({"stage": case["stage"]})
            for event in events:
                stage = ALIASES.get(event.get("stage"), event.get("stage"))
                if (prefix, stage) in covered:
                    continue
                interrupted = case.get("stage") == event.get("stage") and case.get("status") == "interrupted"
                status = "interrupted" if interrupted else "failed" if event.get("exitcode", 0) != 0 else "incomplete"
                emit(key, stage, status, source, event, binding="batch_event", fallback=case,
                     hardware_id=batch.get("board_serial"), details={"error": case.get("error") or batch.get("error") if interrupted else None})

    counts = {"images": len(images), "stages": {}}
    for stage in STAGES:
        for item in images.values():
            summary = item["stages"][stage]
            summary["history"].sort(key=lambda r: (r["time_utc"] or "", r["source"]["path"]))
            history = summary["history"]
            summary["status"] = ("undated" if any(r["time_utc"] is None for r in history)
                                 else history[-1]["status"]) if history else "pending"
            summary["historically_succeeded"] = any(r["status"] == SUCCESS[stage] for r in history)
            if stage == "boot":
                summary["original_boot_chain_verified"] = False
        counts[f"{stage}_{SUCCESS[stage]}_images"] = sum(i["stages"][stage]["historically_succeeded"] for i in images.values())
        counts[f"{stage}_pending_images"] = sum(i["stages"][stage]["status"] == "pending" for i in images.values())
        counts["stages"][stage] = {status: sum(i["stages"][stage]["status"] == status for i in images.values())
                                   for status in sorted({i["stages"][stage]["status"] for i in images.values()})}
    counts.update(failed_images=len({r["image"] for r in result["failures"] if r["image"]}),
                  failures=len(result["failures"]), conditions=len(result["conditions"]),
                  unbound_reports=len(result["unbound"]), read_errors=len(result["read_errors"]))
    result.update(counts=counts, generated_utc=utc_now(), snapshot_complete=not result["read_errors"])
    return result


def main(argv=None):
    parser = safe.JsonArgumentParser(description="唯讀彙整固定 components 與 T3／T4 歷次 JSON，輸出至標準輸出。")
    parser.add_argument("base", help="evidence 根目錄")
    parser.add_argument("--index-sha256", default=INDEX_SHA256, help="外部可信的固定索引 SHA-256")
    args = parser.parse_args(argv)
    try:
        result = summarize(args.base, args.index_sha256)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        print(json.dumps({"error": str(exc), "all_tests_passed": False}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
