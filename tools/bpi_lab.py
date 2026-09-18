#!/usr/bin/env python3
"""跨板實驗 CLI；預設只盤點與排程，模擬與實機資格分開。"""

import argparse
from pathlib import Path
import sqlite3
import sys

import bpi_lab_catalog as catalog_api
import bpi_lab_queue as queue


def station_template(board, simulation=False):
    prefix = "sim" if simulation else "pending"
    return {
        "schema": "bpi-lab-station-v1", "station_id": prefix + "-" + board,
        "mode": "simulation" if simulation else "hardware", "enabled": simulation,
        "hardware_id": prefix + "-" + board, "board": board, "compatible_boards": [board],
        "boot_config_sha256": queue.digest({"board": board, "mode": prefix}) if simulation else "0" * 64,
        "test_version": "basic-v1", "timeout_seconds": 5 if simulation else 900,
        "resources": {key: prefix + ":" + board + ":" + key for key in ("uart", "power", "media")},
        "adapter": {"kind": "simulator-v1"} if simulation else
                   {"kind": "external-v1", "argv": [], "sha256": "0" * 64},
        **({} if simulation else {"qualification": {"status": "awaiting_hardware"},
                                  "authorization": {"userarea_write": False}}),
    }


def parser():
    result = argparse.ArgumentParser(description="跨板映像清單、站點與可續作佇列")
    sub = result.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("catalog", help="唯讀盤點，不解壓或讀取映像內容")
    scan.add_argument("--root", required=True)
    scan.add_argument("--build-root", required=True)
    scan.add_argument("--output", required=True)
    init = sub.add_parser("prepare", help="匯入清單並產生各板停用站點範本")
    init.add_argument("--catalog", required=True)
    init.add_argument("--db", required=True)
    init.add_argument("--stations-dir", required=True)
    init.add_argument("--simulation", action="store_true", help="另啟用模擬佇列，絕不計入實板結果")
    register = sub.add_parser("register", help="登記操作人員提供的站點設定")
    register.add_argument("--db", required=True)
    register.add_argument("--station", required=True)
    history = sub.add_parser("history", help="固定摘要引用既有 H618 結果，相關工作先待審，不重跑")
    history.add_argument("--db", required=True)
    history.add_argument("--report", required=True)
    history.add_argument("--sha256", required=True)
    unlock = sub.add_parser("unlock", help="只釋放已安全結束但中斷於鎖清理的工作")
    unlock.add_argument("--db", required=True)
    unlock.add_argument("--work-key", required=True)
    for name, help_text in (("schedule", "為指定站點排程相容映像"),
                            ("run", "執行指定站點佇列，沒有資格時拒絕")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--db", required=True)
        command.add_argument("--station-id", required=True)
        if name == "run":
            command.add_argument("--evidence", required=True)
            command.add_argument("--limit", type=int, default=1)
    simulate = sub.add_parser("simulate", help="執行已排程的內建模擬器，不啟動外部程式或實體站點")
    simulate.add_argument("--db", required=True)
    simulate.add_argument("--evidence", required=True)
    for name, help_text in (("resume", "先核對現況，再續作中斷階段"),
                            ("recover", "僅返回救援，不自動重刷或重試"),
                            ("retry", "保留失敗紀錄，以新嘗試重新排程"),
                            ("release", "審查既有證據後釋出工作，必須說明補測原因")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--db", required=True)
        command.add_argument("--work-key", required=True)
        if name in ("retry", "release"):
            command.add_argument("--reason", required=True)
        else:
            command.add_argument("--evidence", required=True)
    report = sub.add_parser("status", help="分開列出盤點、模擬與實板基本結果")
    report.add_argument("--db", required=True)
    report.add_argument("--output")
    jobs = sub.add_parser("jobs", help="列出排序與個別工作識別")
    jobs.add_argument("--db", required=True)
    jobs.add_argument("--state")
    return result


def prepare(db, catalog, directory, simulation):
    count = queue.import_catalog(db, catalog)
    boards = sorted({item["board"] for item in catalog["entries"]})
    scheduled = 0
    for board in boards:
        data = station_template(board, simulation)
        path = Path(directory)/(data["station_id"] + ".json")
        if path.exists():
            queue.require(queue.read_json(path) == data, "不覆寫已更改的站點設定")
        else:
            queue.save_json(path, data)
        queue.register_station(db, data)
        scheduled += queue.schedule(db, data["station_id"])
    return {"inventory_images": count, "station_templates": len(boards), "new_jobs": scheduled,
            "mode": "simulation" if simulation else "hardware", "hardware_validated": False}


def dispatch(args):
    if args.command == "catalog":
        data = catalog_api.scan(args.root, args.build_root)
        ref = queue.save_json(args.output, data)
        return {**ref, "images": len(data["entries"]), "boards": len(data["boards"]),
                "issues": data["issues"], "hardware_validated": False}
    if args.command == "prepare":
        catalog = queue.read_json(args.catalog)
        queue.validate_catalog_import(catalog)
    if args.command != "prepare":
        queue.require(Path(args.db).is_file(), "資料庫不存在；先執行 prepare，不建立空白進度")
    db = queue.connect(args.db, readonly=args.command in ("status", "jobs"))
    try:
        if args.command == "prepare":
            return prepare(db, catalog, args.stations_dir, args.simulation)
        if args.command == "register":
            return {"station_sha256": queue.register_station(db, queue.read_json(args.station))}
        if args.command == "history":
            return {"referenced_images": queue.import_history(db, args.report, args.sha256),
                    "summary": queue.summary(db)}
        if args.command == "unlock":
            queue.unlock_completed(db, args.work_key)
            return {"work_key": args.work_key, "resources_released": True, "hardware_operated": False}
        if args.command == "schedule":
            return {"new_jobs": queue.schedule(db, args.station_id)}
        if args.command == "run":
            queue.require(1 <= args.limit <= 10000, "工作上限須為 1 至 10000")
            done = []
            for _ in range(args.limit):
                item = queue.run_one(db, args.station_id, args.evidence)
                if item is None:
                    break
                done.append({"work_key": item["work_key"], "state": item["state"]})
                if item["state"] in ("interrupted", "recovery_failed"):
                    break
            return {"jobs": done, "summary": queue.summary(db)}
        if args.command == "simulate":
            completed = 0
            while True:
                row = db.execute("SELECT station_id,work_key FROM jobs WHERE mode='simulation' AND state='queued' "
                                 "ORDER BY priority LIMIT 1").fetchone()
                if row is None:
                    break
                station, _ = queue.get_station(db, row[0])
                queue.require(station["mode"] == "simulation" and
                              station["adapter"]["kind"] == "simulator-v1", "此命令只接受內建模擬器")
                item = queue.run_one(db, row[0], args.evidence, key=row[1], simulation_only=True)
                completed += 1
                if item["state"] in ("interrupted", "recovery_failed"):
                    break
            return {"executed_simulations": completed, "summary": queue.summary(db)}
        if args.command in ("resume", "recover", "retry", "release"):
            job = queue.get_job(db, args.work_key)
            if args.command == "resume":
                result = queue.run_one(db, job["station_id"], args.evidence, key=args.work_key, resume=True)
            elif args.command == "recover":
                result = queue.recover(db, args.work_key, args.evidence)
            elif args.command == "retry":
                queue.retry(db, args.work_key, args.reason)
                result = queue.get_job(db, args.work_key)
            else:
                queue.release_review(db, args.work_key, args.reason)
                result = queue.get_job(db, args.work_key)
            return {key: result[key] for key in ("work_key", "state", "attempt_id", "cursor", "inflight")}
        if args.command == "jobs":
            sql = "SELECT work_key,station_id,mode,state,attempt_id,cursor,inflight FROM jobs"
            params = ()
            if args.state:
                sql += " WHERE state=?"
                params = (args.state,)
            return {"jobs": [dict(row) for row in db.execute(sql + " ORDER BY priority", params)]}
        result = queue.summary(db)
        if args.output:
            return {**queue.save_json(args.output, result), "summary": result}
        return result
    finally:
        db.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(queue.encode({"ok": False, "error": str(exc)}).decode(), end="", file=sys.stderr)
        return 2
    print(queue.encode(result).decode(), end="")
    if args.command == "run" and any(item["state"] != "collected" for item in result["jobs"]):
        return 3
    if args.command in ("resume", "recover") and result["state"] not in ("collected", "interrupted_recovered"):
        return 3
    if args.command == "simulate" and any(state not in ("collected", "superseded")
                                          for state in result["summary"]["jobs"]["simulation"]):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
