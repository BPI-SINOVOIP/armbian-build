#!/usr/bin/env python3
"""CM6 受溫度保護的 CPU 短測；僅依賴標準庫，不執行磁碟負載。"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import time


START_LIMIT_C = 75.0
STOP_LIMIT_C = 90.0
INTERVAL_SECONDS = 0.5


class ChineseArgumentParser(argparse.ArgumentParser):
    """讓現場操作說明及參數錯誤維持繁體中文。"""

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數錯誤：請提供 --output 新目錄，秒數須為 1–60，worker 數須為 1–2。\n")


def read_snapshot(sys_root: Path = Path("/sys")) -> dict:
    """只讀固定 thermal 與 cpufreq 節點；讀取不完整即保留錯誤。"""
    snapshot = {"temperatures": {}, "cooling": {}, "frequencies": {}, "errors": []}

    def value(path, numeric=False):
        try:
            text = path.read_text(encoding="utf-8").strip()
            return int(text) if numeric else text
        except (OSError, ValueError) as exc:
            snapshot["errors"].append(f"無法讀取 {path}：{type(exc).__name__}")
            return None

    for zone in sorted((sys_root / "class/thermal").glob("thermal_zone*")):
        temperature = value(zone / "temp", True)
        if temperature is not None and not 0 <= temperature <= 200000:
            snapshot["errors"].append(f"溫度數值超出可驗證範圍：{zone.name}")
            temperature = None
        snapshot["temperatures"][zone.name] = {
            "type": value(zone / "type"),
            "celsius": temperature / 1000 if temperature is not None else None,
        }
    for device in sorted((sys_root / "class/thermal").glob("cooling_device*")):
        snapshot["cooling"][device.name] = {
            "type": value(device / "type"),
            "cur_state": value(device / "cur_state", True),
            "max_state": value(device / "max_state", True),
        }
    for policy in sorted((sys_root / "devices/system/cpu/cpufreq").glob("policy*")):
        snapshot["frequencies"][policy.name] = {
            name: value(policy / name, True)
            for name in ("scaling_cur_freq", "scaling_max_freq", "cpuinfo_max_freq")
        }
    return snapshot


def snapshot_issues(snapshot: dict, baseline: dict | None = None) -> list[str]:
    """初檢不合格為 BLOCKED；開始後的不合格由主程式判為 FAIL。"""
    issues = list(snapshot["errors"])
    for key, title in (("temperatures", "溫度"), ("cooling", "散熱狀態"),
                       ("frequencies", "CPU 頻率")):
        if not snapshot[key]:
            issues.append(f"缺少{title}節點")
    limit = START_LIMIT_C if baseline is None else STOP_LIMIT_C
    for zone, item in snapshot["temperatures"].items():
        if item["celsius"] is None:
            issues.append(f"{zone} 缺少有效溫度")
        elif item["celsius"] >= limit:
            issues.append(f"{zone} 溫度 {item['celsius']:.1f} °C 達 {limit:.0f} °C 門檻")
    for device, item in snapshot["cooling"].items():
        if item["cur_state"] is None or item["max_state"] is None:
            issues.append(f"{device} 缺少完整散熱狀態")
        elif not 0 <= item["cur_state"] <= item["max_state"]:
            issues.append(f"{device} 散熱狀態超出範圍")
        elif item["cur_state"] > 0:
            issues.append(f"{device} 已啟動散熱節流，state={item['cur_state']}")
    for policy, item in snapshot["frequencies"].items():
        if any(value is None or value <= 0 for value in item.values()):
            issues.append(f"{policy} 頻率讀值不完整或無效")
            continue
        if item["scaling_max_freq"] < item["cpuinfo_max_freq"]:
            issues.append(f"{policy} scaling_max_freq 低於 cpuinfo_max_freq")
        if baseline and policy in baseline["frequencies"]:
            original = baseline["frequencies"][policy]
            for field in ("scaling_max_freq", "cpuinfo_max_freq"):
                if original[field] is not None and item[field] < original[field]:
                    issues.append(f"{policy} {field} 低於初始基準")
    if baseline:
        for key in ("temperatures", "cooling", "frequencies"):
            if set(snapshot[key]) != set(baseline[key]):
                issues.append(f"執行期間 {key} 節點集合改變")
    return issues


def cpu_worker(seconds: int, parent_pid: int) -> None:
    """有界父程序管理的純整數 CPU 負載；本函式不讀寫檔案或裝置。"""
    number = 1
    # 父程序消失時自行停止；即使監測程序意外失效也不留下無限負載。
    deadline = time.monotonic() + seconds + 2
    while time.monotonic() < deadline and os.getppid() == parent_pid:
        for _ in range(4096):
            number = (number * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF


def stop_workers(processes: list) -> tuple[list[dict], list[str]]:
    """僅處理本次建立的 worker，先全部送停止訊號再逐一回收。"""
    errors = []
    for process in processes:
        try:
            if process.pid is not None and process.is_alive():
                process.terminate()
        except Exception as exc:
            errors.append(f"停止 worker 失敗：{type(exc).__name__}")
    for process in processes:
        try:
            if process.pid is None:
                continue
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if process.is_alive():
                errors.append(f"worker {process.pid} 尚未退出")
        except Exception as exc:
            errors.append(f"回收 worker 失敗：{type(exc).__name__}")
    return [{"pid": process.pid, "exit_code": process.exitcode} for process in processes], errors


def prepare_output(output: Path) -> Path:
    if output.exists() or output.is_symlink():
        raise ValueError("輸出位置已存在，拒絕覆寫")
    output = output.resolve()
    if any(output == root or root in output.parents for root in map(Path, ("/dev", "/proc", "/sys"))):
        raise ValueError("不可將裝置或核心介面目錄作為輸出")
    if not output.parent.is_dir():
        raise ValueError("輸出父目錄不存在")
    output.mkdir(mode=0o700)
    return output


def run_test(output: Path, seconds: int, workers: int) -> dict:
    """執行短測並保留初檢、逐次量測、停止原因與各 worker 結束碼。"""
    if not 1 <= seconds <= 60 or not 1 <= workers <= 2:
        raise ValueError("秒數須介於 1–60，worker 數須介於 1–2")
    previous_umask = os.umask(0o077)
    try:
        output = prepare_output(output)
    finally:
        os.umask(previous_umask)
    summary = {
        "schema_version": 1,
        "status": "BLOCKED",
        "scope": "僅本次 CPU 短測；不代表持續壓力、儲存裝置或整板驗收通過",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "requested_seconds": seconds,
        "workers_requested": workers,
        "start_limit_celsius_exclusive": START_LIMIT_C,
        "stop_limit_celsius_inclusive": STOP_LIMIT_C,
        "sample_interval_seconds": INTERVAL_SECONDS,
        "reasons": [],
        "samples": [],
        "worker_exit_codes": [],
        "cleanup_errors": [],
    }
    processes = []
    started = time.monotonic()
    original_handlers = {}

    def interrupted(signum, _frame):
        raise InterruptedError(f"收到訊號 {signum}，停止本次 worker")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            original_handlers[signum] = signal.signal(signum, interrupted)
        initial = read_snapshot()
        summary["samples"].append({"elapsed_seconds": 0.0, "snapshot": initial})
        issues = snapshot_issues(initial)
        if issues:
            summary["reasons"] = issues
            return summary
        context = multiprocessing.get_context("spawn")
        for _ in range(workers):
            process = context.Process(target=cpu_worker, args=(seconds, os.getpid()), daemon=True)
            processes.append(process)
            process.start()
        started = time.monotonic()
        summary["status"] = "PASS"
        while True:
            current = read_snapshot()
            elapsed = time.monotonic() - started
            summary["samples"].append({"elapsed_seconds": round(elapsed, 3), "snapshot": current})
            issues = snapshot_issues(current, initial)
            for process in processes:
                if process.exitcode is not None:
                    issues.append(f"worker {process.pid} 提前結束，exit_code={process.exitcode}")
            if issues:
                summary["status"] = "FAIL"
                summary["reasons"] = issues
                break
            if elapsed >= seconds:
                summary["reasons"] = ["本次限定時間內未觀察到溫度中止、降頻或 worker 異常"]
                break
            time.sleep(min(INTERVAL_SECONDS, seconds - elapsed))
    except (Exception, KeyboardInterrupt) as exc:
        summary["status"] = "FAIL" if processes else "BLOCKED"
        summary["reasons"].append(f"測試中斷：{type(exc).__name__}：{exc}")
    finally:
        # 清理期間忽略第二次 SIGINT／SIGTERM，避免留下未回收的 worker。
        for signum in original_handlers:
            signal.signal(signum, signal.SIG_IGN)
        summary["worker_exit_codes"], summary["cleanup_errors"] = stop_workers(processes)
        if summary["cleanup_errors"]:
            summary["status"] = "FAIL"
            summary["reasons"].extend(summary["cleanup_errors"])
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        values = [item["celsius"] for sample in summary["samples"]
                  for item in sample["snapshot"]["temperatures"].values() if item["celsius"] is not None]
        summary["peak_celsius"] = max(values) if values else None
        summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
        try:
            write_results(output, summary)
        finally:
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
    return summary


def write_results(output: Path, summary: dict) -> None:
    previous_umask = os.umask(0o077)
    try:
        with (output / "summary.json").open("x", encoding="utf-8") as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        with (output / "temperature.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("elapsed_seconds", "thermal_zone", "type", "temperature_celsius",
                             "cooling_json", "frequency_khz_json"))
            for sample in summary["samples"]:
                snapshot = sample["snapshot"]
                for zone, item in snapshot["temperatures"].items():
                    writer.writerow((sample["elapsed_seconds"], zone, item["type"], item["celsius"],
                                     json.dumps(snapshot["cooling"], ensure_ascii=False, sort_keys=True),
                                     json.dumps(snapshot["frequencies"], ensure_ascii=False, sort_keys=True)))
    finally:
        os.umask(previous_umask)


def main() -> int:
    parser = ChineseArgumentParser(
        description="受溫度保護的 CM6 CPU 短測；僅在待測板執行，不執行磁碟讀寫負載。",
        epilog="輸出父目錄須存在且輸出目錄不得存在。結束碼：0＝短測 PASS；1＝FAIL；2＝BLOCKED。",
        add_help=False,
    )
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明並離開")
    parser.add_argument("--output", type=Path, required=True, help="全新證據目錄")
    parser.add_argument("--seconds", type=int, default=30, choices=range(1, 61), metavar="1..60",
                        help="CPU 負載秒數，預設 30")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2), help="CPU worker 數，預設 2")
    args = parser.parse_args()
    try:
        summary = run_test(args.output, args.seconds, args.workers)
    except (OSError, ValueError) as exc:
        print(f"BLOCKED：{exc}", file=sys.stderr)
        return 2
    print(f"{summary['status']}：" + "；".join(summary["reasons"]))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[summary["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
