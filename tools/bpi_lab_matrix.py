#!/usr/bin/env python3
"""完整清單的唯讀組件準備與續跑；不操作板子或硬體佇列。"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

import bpi_lab_catalog as catalog_api
import bpi_lab_image as image
import bpi_lab_metadata_review as review
import bpi_lab_prepare as preparation
import bpi_lab_queue as queue

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "output/evidence"
require = queue.require


def file_digest(path):
    path = Path(path).absolute()
    with catalog_api._root(path.parent) as root:
        with catalog_api._regular(root, path.name) as (stream, identity):
            checksum = hashlib.sha256()
            size = 0
            while blob := stream.read(1024**2):
                checksum.update(blob)
                size += len(blob)
            require(size == identity["st_size"], "檔案讀取截斷")
    return {"bytes": size, "sha256": checksum.hexdigest()}


def tool_binding():
    paths = sorted((ROOT / "tools").glob("bpi_*.py"))
    for name in ("boards", "sources", "bootscripts", "bootenv", "validation", "bpi-lab"):
        paths += sorted(p for p in (ROOT / "config" / name).rglob("*") if p.is_file() and p.suffix != ".md")
    paths += [Path(sys.executable).resolve()]
    for command in ("debugfs", "sfdisk", "sgdisk", "mtype", "dumpe2fs", "blkid", "dtc"):
        found = shutil.which(command)
        require(found is not None, "缺少唯讀解析工具：" + command)
        paths.append(Path(found).resolve())
    return [{"path": str(p), **file_digest(p)} for p in paths]


def order(entry):
    return (catalog_api.ARCHITECTURES.index(entry["architecture"]), entry["release"],
            entry["board"], entry["variant"], entry["relative_path"])


def matrix_rows(catalog, sample):
    review.validate_catalog(catalog)
    require(type(sample) is dict and sample.get("schema") == "bpi-lab-readonly-sample-audit-v1",
            "逐板參考清單格式錯誤")
    rows = sample.get("rows")
    require(type(rows) is list and len(rows) == 45
            and len({x["board"] for x in rows}) == 45, "逐板參考必須完整且不重複")
    lookup = {x["board"]: x for x in rows}
    require(set(lookup) == {x["board"] for x in catalog["entries"]}, "逐板參考與原清單錯配")
    entries = {x["relative_path"]: x for x in catalog["entries"]}
    for row in rows:
        old = entries.get(row["source"]["relative_path"], {})
        require(old.get("board") == row["board"] and old.get("architecture") == row["architecture"]
                and old.get("expected_sha256") == row["source"]["sha256"]
                and old.get("compressed_bytes") == row["source"]["bytes"], "逐板參考來源不符")
    result = []
    for entry in sorted(catalog["entries"], key=order):
        row = lookup[entry["board"]]
        requested = row["kernel_release"]
        baseline = entries[row["source"]["relative_path"]]
        require((entry["kernel"], entry["branch"], entry["family"]) ==
                (baseline["kernel"], baseline["branch"], baseline["family"]),
                "同板核心或分支不同，不能沿用抽樣的版本請求")
        require(entry["kernel"] == "0" or requested.startswith(entry["kernel"] + "-"),
                "抽樣完整核心版本與來源版本不符")
        require(row["family"] in preparation.FAMILIES, "家族尚未接入")
        result.append({"entry": entry, "family": row["family"],
                       "kernel_release": "0" if entry["kernel"] == "0" else requested})
    return result


def replay_index(catalog, roots):
    """只索引確實回到本清單來源的完整擷取；不能以同板替代。"""
    entries = {x["expected_sha256"]: x for x in catalog["entries"]}
    found, ignored = {}, Counter()
    for root in roots:
        queue.safe_directory(root)
        for path in sorted(Path(root).rglob("extraction.json")):
            try:
                ref = review.reference(path)
                data = review.ref_data(ref)
                require(type(data) is dict and type(data.get("source_digest")) is dict, "擷取索引格式錯誤")
                entry = entries.get(data.get("source_digest", {}).get("sha256"))
                if entry is None:
                    continue
                chain = review.extraction_chain(ref, catalog["root"], entry)
                score = (data.get("filesystem_labels_complete") is True,
                         len(data.get("files", {})), len(data.get("queries", [])), -len(chain))
                old = found.get(entry["image_id"])
                if old is None or score > old[0]:
                    found[entry["image_id"]] = (score, ref)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                ignored[type(exc).__name__] += 1
    return {key: value[1] for key, value in found.items()}, dict(ignored)


def validate_limits(workers, max_raw_bytes, timeout):
    require(type(workers) is int and 1 <= workers <= 4, "同時工作數限一至四")
    require(type(max_raw_bytes) is int and 1024**3 <= max_raw_bytes <= 32 * 1024**3,
            "單筆原映像上限須為 1 至 32 GiB")
    require(type(timeout) is int and 1 <= timeout <= 86400, "單筆期限超界")


def initialize(catalog_ref, sample_ref, output, *, replay_roots=(), workers=4,
               max_raw_bytes=16 * 1024**3, timeout=1800):
    validate_limits(workers, max_raw_bytes, timeout)
    catalog = review.ref_data(catalog_ref)
    rows = matrix_rows(catalog, review.ref_data(sample_ref))
    output = Path(output).absolute()
    require(output.is_relative_to(OUTPUT_ROOT) and output != OUTPUT_ROOT
            and not output.is_relative_to(Path(catalog["root"])), "輸出須位於獨立證據目錄")
    queue.safe_directory(output.parent, create=True)
    require(not output.exists(), "批次目錄已存在，請使用 run 續跑")
    replays, ignored = replay_index(catalog, replay_roots)
    tools = tool_binding()
    image.create_directory(output)
    image.create_directory(output / "jobs")
    plan = {"schema": "bpi-lab-matrix-plan-v1", "catalog": catalog_ref, "sample": sample_ref,
            "source_root": catalog["root"], "rows": rows, "replays": replays,
            "ignored_replay_candidates": ignored, "tools": tools, "workers": workers,
            "max_raw_bytes": max_raw_bytes, "timeout": timeout,
            "hardware_validated": False, "media_written": False}
    return queue.save_json(output / "plan.json", plan)


@contextmanager
def lock(output):
    queue.safe_directory(output)
    fd = os.open(Path(output) / "runner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "批次鎖不是一般檔案")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def artifact_digest(directory):
    queue.safe_directory(directory)
    rows = []
    for path in sorted(Path(directory).rglob("*")):
        mode = path.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), "產物包含連結或特殊檔案")
        if stat.S_ISREG(mode):
            rows.append({"path": str(path.relative_to(directory)), **file_digest(path)})
    return {"files": len(rows), "bytes": sum(x["bytes"] for x in rows), "sha256": queue.digest(rows)}


def new_attempt(directory):
    queue.safe_directory(directory, create=True)
    for index in range(1, 10000):
        path = directory / f"attempt-{index:04d}"
        if not path.exists():
            return image.create_directory(path)
    raise ValueError("嘗試次數超界")


def publish_json(path, data):
    """先完整落盤，再以不覆寫的連結發布；中斷不留下半份完成收據。"""
    staging = path.parent / (".publish-" + uuid.uuid4().hex + ".json")
    queue.save_json(staging, data)
    os.link(staging, path, follow_symlinks=False)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot_integrity(ref):
    data = review.ref_data(ref)
    root = Path(ref["path"]).parent
    for record in data.get("files", {}).values():
        path = Path(record["file"])
        require(not path.is_absolute() and ".." not in path.parts, "擷取組件路徑越界")
        require(file_digest(root / path) == record["digest"], "舊擷取組件已改變，不可靜默重讀來源")
    for record in data.get("queries", []):
        path = Path(record["stderr_file"])
        require(not path.is_absolute() and ".." not in path.parts, "查詢診斷路徑越界")
        require(file_digest(root / path) == record["stderr"], "舊擷取查詢診斷已改變")


def read_failed(result, directory):
    if result.get("error"):
        return True
    component = queue.read_json(directory / "family-result.json")
    return (any(x.get("status") in ("error", "failed") for x in component.get("reads", []))
            or any(x.get("code") in ("reader_failed", "io_error", "execution_failed", "host_tool")
                   for x in result.get("blockers", [])))


def verify_build_sources(component):
    sources = component.get("sources", {})
    require(type(sources) in (dict, list), "家族建置來源索引錯誤")
    external = {}
    if component.get("board") == "bpi-sm10" and component.get("adapter") == "spacemit" and type(sources) is dict:
        sdk = preparation.family_module("spacemit")
        external = {str(sdk.SDK_UBOOT / name): sha for name, sha in sdk.SDK_SOURCES.items()}
    rows = sources.items() if type(sources) is dict else ((r["source_path"], r) for r in sources)
    for name, metadata in rows:
        path = Path(name)
        require(".." not in path.parts, "建置來源路徑越界")
        if path.is_absolute():
            require(name in external and metadata["sha256"] == external[name], "建置來源路徑或固定摘要越界")
        require(file_digest(ROOT / path)["sha256"] == metadata["sha256"], "建置來源已變動，不能跳過舊結果")


def verify_completed(receipt, plan_ref, plan, row, directory):
    require(receipt.get("schema") == "bpi-lab-matrix-result-v1"
            and receipt.get("plan") == plan_ref and receipt.get("row_sha256") == queue.digest(row)
            and receipt.get("status") in ("prepared", "blocked")
            and receipt.get("hardware_validated") is False
            and receipt.get("media_written") is False, "續跑收據錯配")
    attempt = Path(receipt["attempt"])
    require(attempt.parent == directory and attempt.name.startswith("attempt-")
            and receipt["artifacts"] == artifact_digest(attempt), "既有結果產物已變動")
    attempts = receipt.get("attempts")
    require(type(attempts) is list and len(attempts) in (1, 2), "收據缺少實際準備結果")
    for item in attempts:
        require(item["mode"] in ("replay", "source")
                and Path(item["preparation"]["path"]) == attempt / item["mode"] / "preparation.json",
                "準備結果引用越界")
        result = review.ref_data(item["preparation"])
    require(result.get("status") == receipt["status"]
            and result.get("family") == row["family"] and result.get("board") == row["entry"]["board"]
            and result.get("source") == {"bytes": row["entry"]["compressed_bytes"],
                                         "sha256": row["entry"]["expected_sha256"]}
            and result.get("hardware_validated") is False and result.get("media_written") is False
            and result.get("boot_executed") is False
            and result.get("whole_backend_ready") is False
            and not read_failed(result, attempt / attempts[-1]["mode"]), "收據與實際準備結果不符")
    require(receipt.get("source_reread") is any(x["mode"] == "source" for x in attempts),
            "收據來源讀取方式不符")
    extraction = review.reference(attempt / attempts[-1]["mode"] / "extraction/extraction.json")
    require(result["extraction"]["sha256"] == extraction["sha256"], "準備與擷取摘要不符")
    review.extraction_chain(extraction, plan["source_root"], row["entry"])
    verify_build_sources(queue.read_json(attempt / attempts[-1]["mode"] / "family-result.json"))
    review.source_guard(plan["source_root"], row["entry"])
    return receipt


def execute_row(plan_ref, plan, row, output):
    entry = row["entry"]
    directory = output / "jobs" / entry["image_id"]
    receipt_path = directory / "receipt.json"
    if receipt_path.exists():
        return verify_completed(queue.read_json(receipt_path), plan_ref, plan, row, directory)
    review.source_guard(plan["source_root"], entry)
    attempt = new_attempt(directory)
    replay = plan["replays"].get(entry["image_id"])
    # 中斷若發生在完整擷取之後，優先重播該次證據，避免重讀大檔。
    previous, _ = replay_index({"entries": [entry], "root": plan["source_root"]}, [directory])
    replay = previous.get(entry["image_id"], replay)
    attempts = []
    for mode in (["replay", "source"] if replay else ["source"]):
        if mode == "replay":
            for ref in review.extraction_chain(replay, plan["source_root"], entry):
                snapshot_integrity(ref)
        source = replay["path"] if mode == "replay" else str(Path(plan["source_root"]) / entry["relative_path"])
        sha = replay["sha256"] if mode == "replay" else entry["expected_sha256"]
        destination = attempt / mode
        result = preparation.prepare(source, sha, family=row["family"], board=entry["board"],
                                     kernel_release=row["kernel_release"], output=destination,
                                     layout="disk", from_extraction=mode == "replay",
                                     max_raw_bytes=plan["max_raw_bytes"], timeout=plan["timeout"])
        attempts.append({"mode": mode, "preparation": review.reference(destination / "preparation.json")})
        # 有完整可用重播證據的來源阻擋也保留；只有讀取不足才重讀原件。
        if mode == "source" or result.get("status") == "prepared":
            break
        if not read_failed(result, destination):
            break
    review.source_guard(plan["source_root"], entry)
    require(not read_failed(result, destination), "讀取或執行未完成；保留本次嘗試，續跑時重試")
    extraction = review.reference(destination / "extraction/extraction.json")
    review.extraction_chain(extraction, plan["source_root"], entry)
    receipt = {"schema": "bpi-lab-matrix-result-v1", "plan": plan_ref,
               "row_sha256": queue.digest(row), "image_id": entry["image_id"],
               "source_sha256": entry["expected_sha256"], "relative_path": entry["relative_path"],
               "architecture": entry["architecture"], "release": entry["release"],
               "board": entry["board"], "variant": entry["variant"],
               "family": row["family"], "kernel_release": result.get("kernel_release"),
               "status": result["status"], "blockers": result.get("blockers", []),
               "error": result.get("error"), "attempt": str(attempt), "attempts": attempts,
               "source_reread": any(x["mode"] == "source" for x in attempts),
               "hardware_validated": False, "media_written": False,
               "artifacts": artifact_digest(attempt)}
    verify_completed(receipt, plan_ref, plan, row, directory)
    publish_json(receipt_path, receipt)
    print(json.dumps({key: receipt[key] for key in ("image_id", "board", "release", "variant",
                                                   "status", "source_reread")}, ensure_ascii=False), flush=True)
    return receipt


def try_row(plan_ref, plan, row, output):
    try:
        return execute_row(plan_ref, plan, row, output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = {"status": "retryable", "image_id": row["entry"]["image_id"],
                  "board": row["entry"]["board"], "error": str(exc), "hardware_validated": False}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def run(plan_ref):
    plan = review.ref_data(plan_ref)
    require(plan.get("schema") == "bpi-lab-matrix-plan-v1" and plan.get("hardware_validated") is False
            and plan.get("media_written") is False,
            "批次計畫格式不符")
    validate_limits(plan["workers"], plan["max_raw_bytes"], plan["timeout"])
    catalog = review.ref_data(plan["catalog"])
    require(plan["source_root"] == catalog["root"], "來源根目錄與固定清單不符")
    require(plan["rows"] == matrix_rows(catalog, review.ref_data(plan["sample"])),
            "批次內容與固定原清單不符")
    output = Path(plan_ref["path"]).parent
    require(output.is_relative_to(OUTPUT_ROOT) and output != OUTPUT_ROOT
            and not output.is_relative_to(Path(catalog["root"])), "輸出須位於獨立證據目錄")
    require(type(plan["replays"]) is dict
            and set(plan["replays"]) <= {r["entry"]["image_id"] for r in plan["rows"]}, "重播範圍超出原清單")
    for ref in plan["replays"].values():
        review.validate_ref(ref)
    with lock(output):
        require(plan["tools"] == tool_binding(), "工具已改變，請新建批次並重播舊擷取")
        if (output / "summary.json").exists():
            old = queue.read_json(output / "summary.json")
            require(old.get("plan") == plan_ref, "既有總結與計畫錯配")
        results = []
        for key, group in groupby(plan["rows"], key=lambda r: (r["entry"]["architecture"], r["entry"]["release"])):
            rows = list(group)
            remaining = sum(not (output / "jobs" / r["entry"]["image_id"] / "receipt.json").exists() for r in rows)
            reserve = min(plan["workers"], remaining) * 2 * plan["max_raw_bytes"] + 32 * 1024**3
            require(shutil.disk_usage(output).free >= reserve, "剩餘空間不足以安全啟動此群組")
            print(json.dumps({"group": key, "remaining": remaining}, ensure_ascii=False), flush=True)
            with ThreadPoolExecutor(max_workers=plan["workers"]) as pool:
                results.extend(pool.map(lambda row: try_row(plan_ref, plan, row, output), rows))
            require(plan["tools"] == tool_binding(), "執行期間工具改變，禁止發布批次總結")
        summary = {"schema": "bpi-lab-matrix-summary-v1", "plan": plan_ref,
                   "checked": len(results), "boards": len({r["board"] for r in results}),
                   "status_counts": dict(Counter(r["status"] for r in results)),
                   "full_source_reads": sum(r.get("source_reread", False) for r in results),
                   "replayed_only": sum(r.get("source_reread") is False for r in results),
                   "hardware_validated": False, "media_written": False,
                   "retryable": [r for r in results if r["status"] == "retryable"],
                   "results": [review.reference(output / "jobs" / r["image_id"] / "receipt.json")
                               for r in results if r["status"] != "retryable"]}
        summary["by_board"] = {board: dict(Counter(r["status"] for r in results if r["board"] == board))
                               for board in sorted({r["board"] for r in results})}
        if summary["retryable"]:
            queue.save_json(output / ("incomplete-" + uuid.uuid4().hex + ".json"), summary)
            return summary
        if (output / "summary.json").exists():
            require(queue.read_json(output / "summary.json") == summary, "續跑總結與原總結不一致")
        else:
            publish_json(output / "summary.json", summary)
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="完整映像離線準備與續跑，不操作實板")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="固定清單、工具與可重用擷取")
    init.add_argument("--catalog", type=Path, required=True)
    init.add_argument("--catalog-sha256", required=True)
    init.add_argument("--sample", type=Path, required=True)
    init.add_argument("--sample-sha256", required=True)
    init.add_argument("--replay-root", type=Path, action="append", default=[])
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--workers", type=int, default=4)
    resume = sub.add_parser("run", help="補做未完成項目，已完成者核對後跳過")
    resume.add_argument("--plan", type=Path, required=True)
    resume.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize({"path": str(args.catalog.absolute()), "sha256": args.catalog_sha256},
                                {"path": str(args.sample.absolute()), "sha256": args.sample_sha256},
                                args.output, replay_roots=args.replay_root, workers=args.workers)
        else:
            result = run({"path": str(args.plan.absolute()), "sha256": args.plan_sha256})
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("retryable") else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "hardware_validated": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
