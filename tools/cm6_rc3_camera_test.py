#!/usr/bin/env python3
"""CM6 rc3 有界相機取證；不改開機設定、不選擇磁碟、不安裝套件。"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time


CONFIG_SHA256 = {
    "9ec5536429253c6b20f91592146fa2ca0f8b67e221880f235e5ca165d36da941",
    "19062591b89baba427e2373264f1077d5d7e037aa3667e3fdb196c497b0f9fba",
    "f77e01c08254908fc59ade98c97e3075b86fbd827e323b3e1aab32cc50c0a582",
    "2620c51af222b6ec04ab7a08fef039ef57e98553c70b3bd6eaa38e98a42341cb",
}
CAM_SHA256 = "ee7e40dc46a493e9d9078a729677c7d30fb77bc3305ee87ec1b763c932e76921"
CALIBRATION_SHA256 = "dfb193cc3da6833111812e95e7ecec687bb6b8dcddd197b4627b5345fb803faa"
CALIBRATION_NAME = "sensor_rear_primary_cpp_preview_setting.data"
CALIBRATION_LINK = Path("/usr/share/camera_json")
FRAME_ROOT = Path("/tmp")
THERMAL_ROOT = Path("/sys/class/thermal")
LOCK_FILE = Path("/run/lock/cm6-rc3-camera-test.lock")
PATTERNS = ("cpp[01]_output_*.nv12", "raw_output[01]_*.raw",
            "vi_*_l*.nv12", "vi_*_L*.raw")


class ChineseHelpFormatter(argparse.HelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, prefix or "用法：")


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def checked_regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"必須是一般檔案，拒絕符號連結或裝置：{path}")
    return path


def frames():
    return {p for pattern in PATTERNS for p in FRAME_ROOT.glob(pattern)}


def temperatures():
    result = {}
    for path in sorted(THERMAL_ROOT.glob("thermal_zone*/temp")):
        value = int(path.read_text().strip())
        if value < -40000 or value > 150000:
            raise ValueError(f"溫度讀值無效：{path}")
        result[path.parent.name] = value
    if not result:
        raise ValueError("讀不到溫度，停止測試")
    return result


def validate_inputs(runtime, config):
    executable = checked_regular(runtime / "usr/bin/cam-test")
    calibration = checked_regular(runtime / "usr/share/camera_json" / CALIBRATION_NAME)
    checked_regular(config)
    if digest(executable) != CAM_SHA256:
        raise ValueError("cam-test 與固定版本校驗不符")
    if digest(calibration) != CALIBRATION_SHA256:
        raise ValueError("校正檔與固定版本校驗不符")
    if digest(config) not in CONFIG_SHA256:
        raise ValueError("只接受交付的四份串流 JSON；偵測 JSON 或改寫配置不在此工具範圍")
    if not (runtime / "usr/lib/libsdkcam.so").exists():
        raise ValueError("缺少 libsdkcam.so，請將兩個指定 DEB 解壓到同一 runtime")
    return executable, json.loads(config.read_text())


def existing_camera_processes():
    found = []
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            if path.read_text().strip() == "cam-test":
                found.append(path.parent.name)
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def stop_owned_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def capture_kernel(path):
    result = subprocess.run(["dmesg"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=10)
    path.write_bytes(result.stdout)
    path.with_suffix(".stderr.txt").write_bytes(result.stderr)
    if result.returncode:
        raise ValueError("無法保存核心紀錄，需具備 dmesg 讀取權限")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False,
                                     formatter_class=ChineseHelpFormatter)
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明後結束")
    parser.add_argument("--runtime", type=Path, required=True, help="兩個 DEB 解壓到同一根目錄的位置")
    parser.add_argument("--config", type=Path, required=True, help="交付的四份串流 JSON 其中之一")
    parser.add_argument("--output", type=Path, required=True, help="不存在的新證據目錄；父目錄須已存在")
    parser.add_argument("--seconds", type=int, default=60, help="最長執行秒數，範圍 1 至 60")
    parser.add_argument("--temperature-limit", type=int, default=90, help="停止溫度，攝氏，範圍 50 至 100")
    parser.add_argument("--start-temperature", type=int, default=80, help="開始前須低於此溫度，攝氏")
    parser.add_argument("--check-only", action="store_true", help="僅核對輸入檔案，不接觸相機或建立輸出")
    args = parser.parse_args(argv)
    os.umask(0o077)
    process = None
    owned_link = None
    output_created = False
    lock_file = None
    report = {"status": "BLOCKED", "note": "程序結束不等於相機驗收通過；須另判讀影格、核心警告及畫質。",
              "outputs": [], "temperatures": []}
    started = time.monotonic()
    interrupted = []

    def interrupted_signal(signum, _frame):
        interrupted.append(signum)

    try:
        if not (1 <= args.seconds <= 60 and 50 <= args.temperature_limit <= 100
                and 40 <= args.start_temperature <= args.temperature_limit):
            raise ValueError("時間或溫度界限超出允許範圍")
        runtime = args.runtime.resolve(strict=True)
        config = args.config.absolute()
        output = args.output.absolute()
        if os.path.lexists(output) or not output.parent.is_dir():
            raise ValueError("輸出目錄必須不存在，且父目錄必須已存在")
        executable, configuration = validate_inputs(runtime, config)
        report.update({"config": str(config), "config_sha256": digest(config),
                       "runtime": str(runtime), "cam_test_sha256": CAM_SHA256,
                       "seconds_limit": args.seconds, "temperature_limit_c": args.temperature_limit,
                       "start_temperature_c": args.start_temperature})
        if args.check_only:
            print("輸入檔案校驗通過；未執行相機、未建立輸出，尚未驗證板端動態函式庫。")
            return 0
        if os.geteuid() != 0:
            raise ValueError("實際相機測試須以 sudo 執行")
        if existing_camera_processes():
            raise ValueError("已有 cam-test 執行，停止；不可同時執行其他相機測試")
        # 保留鎖檔本身，避免刪除鎖檔造成下一個程序鎖住不同 inode。
        fd = os.open(LOCK_FILE,
                     os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_file = os.fdopen(fd, "w")
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("鎖路徑不是一般檔案")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(CALIBRATION_LINK):
            raise ValueError("既有校正檔路徑存在，停止；不覆寫、不刪除既有檔案")
        if frames():
            raise ValueError("/tmp 已有相機輸出，停止；請人工保留原始證據，不可直接覆寫")
        initial = temperatures()
        if max(initial.values()) >= args.start_temperature * 1000:
            raise ValueError("起始溫度過高，先改善散熱；不得提高門檻掩蓋問題")
        if shutil.disk_usage(output.parent).free < 128 * 1024 * 1024:
            raise ValueError("證據位置可用空間不足 128 MiB")
        if shutil.disk_usage(FRAME_ROOT).free < 128 * 1024 * 1024:
            raise ValueError("/tmp 可用空間不足 128 MiB")
        output.mkdir(mode=0o700)
        output_created = True
        capture_kernel(output / "kernel-before.txt")
        shutil.copyfile(config, output / "config.json")
        CALIBRATION_LINK.symlink_to(runtime / "usr/share/camera_json")
        owned_link = CALIBRATION_LINK.lstat()
        # 限制環境，避免把使用者憑證或自訂 preload 傳入測試程序。
        environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
                       "LD_LIBRARY_PATH": str(runtime / "usr/lib")}
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted_signal)
        with (output / "stdout.log").open("xb") as out, (output / "stderr.log").open("xb") as err:
            process = subprocess.Popen([str(executable), str(config)], env=environment,
                                       cwd=output, stdout=out, stderr=err, start_new_session=True)
            report["status"] = "NEEDS_REVIEW"
            report["stop_reason"] = "程序正常結束"
            while process.poll() is None:
                current = temperatures()
                elapsed = time.monotonic() - started
                report["temperatures"].append({"seconds": round(elapsed, 3), "millidegrees": current})
                if interrupted or elapsed >= args.seconds or max(current.values()) >= args.temperature_limit * 1000:
                    report["status"] = "FAIL"
                    report["stop_reason"] = ("操作者中止" if interrupted else
                                             "達停止溫度" if max(current.values()) >= args.temperature_limit * 1000
                                             else "達時間上限")
                    stop_owned_process(process)
                    break
                time.sleep(0.25)
            report["returncode"] = process.wait()
            if report["returncode"] != 0:
                report["status"] = "FAIL"
            report["enabled_sensor_ids"] = [n["sensor_id"] for n in configuration["isp_node"] if n["enable"]]
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        report["status"] = "FAIL" if process is not None else "BLOCKED"
    finally:
        try:
            stop_owned_process(process)
        except (OSError, subprocess.SubprocessError) as error:
            report["cleanup_error"] = str(error)
            report["status"] = "FAIL"
        if output_created:
            if process is not None:
                # 僅處理啟動前不存在、名稱符合固定 SDK 的一般影格檔。
                for source in sorted(frames()):
                    try:
                        checked_regular(source)
                        destination = output / source.name
                        with source.open("rb") as src, destination.open("xb") as dst:
                            shutil.copyfileobj(src, dst)
                            dst.flush()
                            os.fsync(dst.fileno())
                        value = digest(destination)
                        if value != digest(source):
                            raise ValueError("複製後的影格 SHA-256 不一致")
                        source.unlink()
                        report["outputs"].append({"name": destination.name, "bytes": destination.stat().st_size,
                                                  "sha256": value})
                    except (OSError, ValueError) as error:
                        report.setdefault("collection_errors", []).append(str(error))
                        report["status"] = "FAIL"
                try:
                    capture_kernel(output / "kernel-after.txt")
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    report["kernel_after_error"] = str(error)
                    report["status"] = "FAIL"
                if not report["outputs"]:
                    report["status"] = "FAIL"
                    report["frame_error"] = "未取得新影格"
        report["calibration_link_cleanup"] = "未建立"
        if owned_link is not None:
            try:
                now = CALIBRATION_LINK.lstat()
                if (stat.S_ISLNK(now.st_mode)
                        and (now.st_dev, now.st_ino, now.st_ctime_ns)
                        == (owned_link.st_dev, owned_link.st_ino, owned_link.st_ctime_ns)
                        and os.readlink(CALIBRATION_LINK) == str(runtime / "usr/share/camera_json")):
                    CALIBRATION_LINK.unlink()
                    report["calibration_link_cleanup"] = "已移除本次連結"
                else:
                    report["calibration_link_cleanup"] = "路徑已遭外部變更，保留現況"
                    report["status"] = "FAIL"
            except FileNotFoundError:
                report["calibration_link_cleanup"] = "連結已不存在"
            except OSError as error:
                report["calibration_link_cleanup"] = str(error)
                report["status"] = "FAIL"
        if lock_file is not None:
            lock_file.close()
        if output_created:
            report["seconds"] = round(time.monotonic() - started, 3)
            try:
                (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            except OSError as error:
                report["summary_write_error"] = str(error)
                report["status"] = "FAIL"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "NEEDS_REVIEW" else 2 if report["status"] == "BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
