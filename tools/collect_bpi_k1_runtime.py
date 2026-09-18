#!/usr/bin/env python3
"""以唯讀診斷蒐集 K1 板上證據；輸出僅代表已蒐集，不授予驗收通過。"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone


MAX_INPUT_BYTES = 32 * 1024 * 1024
PACKAGE_PATTERNS = (
    "linux-image-*", "linux-dtb-*", "linux-headers-*", "linux-u-boot-*",
    "armbian-*", "img-gpu-powervr", "mesa-*", "lib*mesa*", "libgbm*",
    "libegl*", "libgles*", "libglvnd*", "libglx*", "libvulkan*", "vulkan-tools",
    "libwayland*", "xserver-xorg*", "xfce4*", "weston", "spacemit*",
    "python3-spacemit*", "onnxruntime*", "python3-onnxruntime*", "k1x-*",
    "mpp*", "ffmpeg", "libavcodec*", "libavutil*", "gstreamer1.0*",
    "libc6", "libstdc++6", "python3", "python3.*",
)
PROVIDER_QUERY = """
import importlib.util
import json
result = {"spacemit_import": "not_installed", "available_providers": []}
if importlib.util.find_spec("spacemit_ort") is not None:
    try:
        import spacemit_ort
        result["spacemit_import"] = "loaded"
    except Exception as exc:
        result["spacemit_import"] = "failed"
        result["spacemit_error_type"] = type(exc).__name__
if importlib.util.find_spec("onnxruntime") is None:
    result["onnxruntime_import"] = "not_installed"
else:
    try:
        import onnxruntime
        result["onnxruntime_import"] = "loaded"
        result["onnxruntime_version"] = onnxruntime.__version__
        result["available_providers"] = onnxruntime.get_available_providers()
    except Exception as exc:
        result["onnxruntime_import"] = "failed"
        result["onnxruntime_error_type"] = type(exc).__name__
print(json.dumps(result))
"""


def digest_file(path: Path) -> dict:
    """為實際留存的原始位元組計算雜湊。"""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def gpu_observation(outputs: dict[str, str], has_display: bool) -> dict:
    """僅辨識診斷輸出，不能以硬體名稱替代實際工作負載。"""
    software = []
    hardware = []
    for source, output in outputs.items():
        for line in output.splitlines():
            if re.search(r"llvmpipe|softpipe|lavapipe|swrast|software rasterizer", line, re.I):
                software.append({"source": source, "line": line.strip()})
            elif re.search(r"(?:renderer|deviceName|device name|driverName|driver name)", line, re.I):
                if re.search(r"PowerVR|Imagination|\bpvr\b|IMG BXE|Rogue", line, re.I):
                    hardware.append({"source": source, "line": line.strip()})
    if software:
        status = "software_renderer_observed"
    elif hardware:
        status = "hardware_renderer_reported_unverified"
    else:
        status = "renderer_not_identified"
    return {
        "status": status,
        "display_session": "present" if has_display else "not_available",
        "software_renderer_lines": software,
        "hardware_renderer_lines": hardware,
        "validated": False,
        "note": "渲染器名稱與裝置存在均不能取代畫面、負載、穩定性及加速驗收；多個裝置須分別判讀。",
    }


def profile_observation(data: bytes) -> dict:
    """辨識既有 ONNX Runtime 剖析，不執行模型，也不認證輸入來源。"""
    try:
        document = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {"status": "invalid_json", "validated": False}
    events = document.get("traceEvents") if isinstance(document, dict) else document
    if not isinstance(events, list):
        return {"status": "unsupported_profile_shape", "validated": False}
    providers: dict[str, dict] = {}
    unattributed = 0
    for event in events:
        if not isinstance(event, dict) or event.get("cat") != "Node":
            continue
        args = event.get("args")
        duration = event.get("dur")
        if not isinstance(args, dict) or not isinstance(duration, (int, float)):
            continue
        try:
            usable_duration = not isinstance(duration, bool) and math.isfinite(duration) and duration > 0
        except OverflowError:
            usable_duration = False
        if not usable_duration:
            continue
        provider = args.get("provider")
        if not isinstance(provider, str) or not provider:
            unattributed += 1
            continue
        count = providers.setdefault(provider, {"node_events": 0, "duration_us": 0})
        count["node_events"] += 1
        count["duration_us"] += duration
    if "SpaceMITExecutionProvider" in providers:
        status = "spacemit_execution_reported_unverified"
    elif providers:
        status = "spacemit_execution_not_observed"
    else:
        status = "execution_not_identified"
    total = sum(item["node_events"] for item in providers.values())
    cpu = providers.get("CPUExecutionProvider", {}).get("node_events", 0)
    return {
        "status": status,
        "providers": providers,
        "unattributed_node_events": unattributed,
        "cpu_node_event_fraction": cpu / total if total else None,
        "validated": False,
        "note": "僅統計有執行時間的節點事件；比例不是模型算子或耗時回退比例，來源、模型、正確性及 IME 指令仍須核對。",
    }


class Collector:
    """所有命令皆使用固定引數；只在指定的新目錄寫入證據。"""

    def __init__(self, output: Path, timeout: float = 15.0):
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("命令逾時秒數必須大於零且不超過 120。")
        output.mkdir(parents=True, exist_ok=False)
        self.output = output
        self.raw = output / "raw"
        self.raw.mkdir()
        self.timeout = timeout
        self.records: dict[str, dict] = {}

    def artifact(self, path: Path) -> dict:
        return {"path": str(path.relative_to(self.output)), **digest_file(path)}

    def command(self, key: str, argv: list[str], skip_reason: str | None = None) -> dict:
        stdout_path = self.raw / f"{key}.stdout"
        stderr_path = self.raw / f"{key}.stderr"
        started = time.monotonic()
        executable = shutil.which(argv[0])
        record = {"kind": "command", "argv": argv, "timeout_seconds": self.timeout,
                  "executable_available": executable is not None}
        # 防止圖形診斷建立著色器快取；不載入使用者的 Python 起始設定。
        env = {**os.environ, "LC_ALL": "C", "MESA_SHADER_CACHE_DISABLE": "true",
               "PYTHONDONTWRITEBYTECODE": "1", "__GL_SHADER_DISK_CACHE": "0",
               "GST_REGISTRY_UPDATE": "no", "XDG_CACHE_HOME": str(self.output.resolve() / "cache")}
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            if skip_reason:
                record.update(status="skipped", reason=skip_reason)
            elif not executable:
                record.update(status="command_not_installed")
            else:
                try:
                    process = subprocess.Popen(
                        argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                        env=env, shell=False, start_new_session=True,
                    )
                    try:
                        code = process.wait(timeout=self.timeout)
                        record.update(status="collected" if code == 0 else "command_failed", exit_code=code)
                    except subprocess.TimeoutExpired:
                        # 同時終止診斷程序及其子程序，避免逾時後繼續占用資源。
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        record.update(status="timeout", exit_code=process.returncode)
                except OSError as exc:
                    record.update(status="launch_failed", errno=exc.errno)
        record.update(
            elapsed_seconds=round(time.monotonic() - started, 3),
            stdout=self.artifact(stdout_path), stderr=self.artifact(stderr_path),
        )
        self.records[key] = record
        return record

    def snapshot(self, key: str, source: Path) -> dict:
        record: dict = {"kind": "file", "source": str(source)}
        try:
            mode = source.stat().st_mode
            if not stat.S_ISREG(mode):
                record["status"] = "not_regular_file"
            else:
                # procfs／sysfs 的檔案大小可能為零，必須讀到上限才判斷。
                with source.open("rb") as stream:
                    data = stream.read(MAX_INPUT_BYTES + 1)
                if len(data) > MAX_INPUT_BYTES:
                    record.update(status="input_too_large", limit_bytes=MAX_INPUT_BYTES)
                else:
                    target = self.raw / f"{key}.bin"
                    target.write_bytes(data)
                    record.update(status="collected", artifact=self.artifact(target))
        except FileNotFoundError:
            record["status"] = "not_found"
        except PermissionError:
            record["status"] = "permission_denied"
        except OSError as exc:
            record.update(status="read_failed", errno=exc.errno)
        self.records[key] = record
        return record

    def content(self, key: str) -> str:
        record = self.records.get(key, {})
        artifact = record.get("artifact", record.get("stdout"))
        return decode((self.output / artifact["path"]).read_bytes()) if artifact else ""

    def collect(self, profiles: list[Path]) -> dict:
        display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        files = {
            "dt_model": "/proc/device-tree/model", "dt_compatible": "/proc/device-tree/compatible",
            "cmdline": "/proc/cmdline", "cpuinfo": "/proc/cpuinfo", "os_release": "/etc/os-release",
            "armbian_release": "/etc/armbian-release", "fstab": "/etc/fstab",
            "pvr_status": "/sys/kernel/debug/pvr/status", "pvr_version": "/proc/pvr/version",
        }
        for key, filename in files.items():
            self.snapshot(key, Path(filename))
        patterns = (
            "/sys/class/drm/card*/device/uevent", "/sys/class/drm/card*-*/status",
            "/sys/class/thermal/thermal_zone*/temp", "/sys/class/thermal/thermal_zone*/type",
            "/sys/devices/system/cpu/cpufreq/policy*/scaling_governor",
            "/sys/devices/system/cpu/cpufreq/policy*/scaling_cur_freq",
        )
        for pattern in patterns:
            for path in sorted(Path("/").glob(pattern.lstrip("/"))):
                self.snapshot("sys_" + str(path).strip("/").replace("/", "_"), path)
        commands = {
            "uname": ["uname", "-a"],
            "mounts": ["findmnt", "--json", "--output", "TARGET,SOURCE,FSTYPE,OPTIONS"],
            "lsblk": ["lsblk", "--json", "--bytes", "--output", "NAME,KNAME,TYPE,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS,MODEL"],
            "packages": ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Status}\n"],
            "drm_info": ["drm_info", "-j"],
            "glxinfo": ["glxinfo", "-B"],
            "eglinfo": ["eglinfo", "-B"],
            "vulkaninfo": ["vulkaninfo", "--summary"],
            "ai_providers": [sys.executable, "-I", "-B", "-c", PROVIDER_QUERY],
            "v4l2_devices": ["v4l2-ctl", "--list-devices"],
            "ffmpeg_hwaccels": ["ffmpeg", "-hide_banner", "-hwaccels"],
            "ffmpeg_decoders": ["ffmpeg", "-hide_banner", "-decoders"],
            "ffmpeg_encoders": ["ffmpeg", "-hide_banner", "-encoders"],
            "gstreamer_plugins": ["gst-inspect-1.0"],
            "kernel_log": ["dmesg", "--color=never"],
        }
        for key, argv in commands.items():
            reason = "no_x11_display_session" if key == "glxinfo" and not os.environ.get("DISPLAY") else None
            self.command(key, argv, reason)
        selected_packages = []
        for line in self.content("packages").splitlines():
            fields = line.split("\t")
            if len(fields) == 4 and any(fnmatch.fnmatchcase(fields[0].split(":")[0], p) for p in PACKAGE_PATTERNS):
                selected_packages.append(dict(zip(("package", "version", "architecture", "status"), fields)))
        ai = {"status": "provider_not_identified", "validated": False,
              "note": "可用 provider 僅表示可載入；本工具不執行模型，不能證明實際推論或 RVV／IME 加速。"}
        # 保留原始輸出；某些套件會先印日誌，因此從末行尋找 JSON。
        if self.records["ai_providers"]["status"] == "collected":
            for line in reversed(self.content("ai_providers").splitlines()):
                try:
                    observation = json.loads(line)
                except ValueError:
                    continue
                if isinstance(observation, dict) and isinstance(observation.get("available_providers"), list):
                    ai["query"] = observation
                    providers = observation["available_providers"]
                    ai["status"] = "spacemit_provider_available_unverified" if "SpaceMITExecutionProvider" in providers else "spacemit_provider_unavailable"
                    break
        profile_records = []
        for index, source in enumerate(profiles):
            key = f"ai_profile_{index:03d}"
            record = self.snapshot(key, source)
            observation = profile_observation((self.output / record["artifact"]["path"]).read_bytes()) if record["status"] == "collected" else {"status": "profile_unreadable", "validated": False}
            profile_records.append({"record": key, **observation})
        return {
            "schema_version": 1,
            "collector": {"name": Path(__file__).name, **digest_file(Path(__file__))},
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "status": "collected_unverified",
            "hardware_validation_passed": False,
            "note": "本報告僅蒐集證據，不執行燒錄、安裝、儲存寫入測試或壓力測試；不提升硬體驗收等級。",
            "environment": {key: os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_RUNTIME_DIR")},
            "board": {"model": self.content("dt_model").rstrip("\0\n"), "compatible": self.content("dt_compatible").strip("\0\n").split("\0") if self.content("dt_compatible") else []},
            "selected_packages": selected_packages,
            "gpu": gpu_observation({key: self.content(key) for key in ("glxinfo", "eglinfo", "vulkaninfo")}, display),
            "ai": {**ai, "profiles": profile_records},
            "vpu": {"status": "capabilities_collected_unverified", "validated": False,
                    "note": "裝置、解碼器及外掛清單不代表影片實際使用硬體編解碼。"},
            "records": self.records,
        }


def main(argv: list[str] | None = None) -> int:
    class ChineseParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage:", "用法：", 1)

        def format_usage(self):
            return super().format_usage().replace("usage:", "用法：", 1)

    parser = ChineseParser(description=__doc__, add_help=False)
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示使用說明並結束。")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的證據輸出目錄。")
    parser.add_argument("--timeout", type=float, default=15.0, help="每個診斷命令的逾時秒數，預設 15，最大 120。")
    parser.add_argument("--ai-profile", type=Path, action="append", default=[], help="附加既有 ONNX Runtime JSON 剖析，可重複指定；不會執行模型。")
    args = parser.parse_args(argv)
    try:
        collector = Collector(args.output, args.timeout)
        report = collector.collect(args.ai_profile)
        report_path = args.output / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        checksum = digest_file(report_path)["sha256"]
        (args.output / "report.json.sha256").write_text(f"{checksum}  report.json\n", encoding="ascii")
    except (OSError, ValueError) as exc:
        print(f"無法完成蒐證：{type(exc).__name__}；請檢查輸出目錄權限、是否已存在及逾時設定。", file=sys.stderr)
        return 2
    print(f"證據已儲存至 {report_path}；硬體加速仍須驗證。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
