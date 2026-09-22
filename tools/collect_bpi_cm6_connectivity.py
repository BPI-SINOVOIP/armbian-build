#!/usr/bin/env python3
"""有界唯讀蒐集 CM6 網路與藍牙證據；單檔執行，僅依賴 Python 標準庫。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time


COMMAND_TIMEOUT = 8.0
COMMAND_BYTES = 128 * 1024
FILE_BYTES = 16 * 1024
DIRECTORY_ENTRIES = 32
FILESYSTEM_TIMEOUT = 20.0
FILESYSTEM_BYTES = 2 * 1024 * 1024
NM_FIELDS = "GENERAL.STATE,GENERAL.REASON,GENERAL.NM-MANAGED,GENERAL.AUTOCONNECT,GENERAL.DRIVER,GENERAL.HWADDR,GENERAL.CONNECTION,WIRED-PROPERTIES.CARRIER"
COMMANDS = (
    ("kernel", ("uname", "-r")),
    ("architecture", ("uname", "-m")),
    ("nm_devices", ("nmcli", "--terse", "--fields", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")),
    ("nm_eth0", ("nmcli", "--terse", "--fields", NM_FIELDS, "device", "show", "eth0")),
    ("nm_eth1", ("nmcli", "--terse", "--fields", NM_FIELDS, "device", "show", "eth1")),
    ("nm_connections", ("nmcli", "--terse", "--fields", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT", "connection", "show")),
    ("nm_config", ("NetworkManager", "--print-config")),
    ("service_states", ("systemctl", "show", "--no-pager", "--property=Id,ActiveState,SubState,UnitFileState,Result",
                        "NetworkManager.service", "systemd-networkd.service", "bluetooth.service", "bpi-cm6-bluetooth.service")),
    ("ip_link", ("ip", "-json", "-details", "link", "show")),
    ("ip_address", ("ip", "-json", "address", "show")),
    ("ip_route4", ("ip", "-json", "route", "show", "table", "all")),
    ("ip_route6", ("ip", "-6", "-json", "route", "show", "table", "all")),
    *((f"ethtool_{iface}{suffix}", ("ethtool", *options, iface))
      for iface in ("eth0", "eth1")
      for suffix, options in (("", ()), ("_driver", ("-i",)), ("_statistics", ("-S",)))),
    *((f"udev_{iface}", ("udevadm", "info", "--query=property", f"--path=/sys/class/net/{iface}"))
      for iface in ("eth0", "eth1")),
    ("rfkill", ("rfkill", "--json", "list")),
    ("bluetooth_list", ("bluetoothctl", "--timeout", "5", "list")),
    ("bluetooth_show", ("bluetoothctl", "--timeout", "5", "show")),
    ("kernel_journal", ("journalctl", "--boot", "--kernel", "--no-pager", "--lines=200", "--output=short-monotonic")),
    ("nm_journal", ("journalctl", "--boot", "--unit=NetworkManager.service", "--no-pager", "--lines=200", "--output=short-monotonic")),
    ("bluetooth_journal", ("journalctl", "--boot", "--unit=bluetooth.service", "--unit=bpi-cm6-bluetooth.service", "--no-pager", "--lines=200", "--output=short-monotonic")),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stop_process(process: subprocess.Popen) -> None:
    """只終止本次建立的命令程序群組，避免逾時子程序繼續執行。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGKILL:
            return


def run_command(argv: tuple[str, ...], timeout: float = COMMAND_TIMEOUT,
                byte_limit: int = COMMAND_BYTES) -> dict:
    """固定命令使用引數陣列；輸出與執行時間皆有限制。"""
    record = {"argv": list(argv), "timeout_seconds": timeout, "output_limit_bytes": byte_limit}
    executable = shutil.which(argv[0])
    if not executable:
        return {**record, "status": "missing_command", "stdout": "", "stderr": ""}
    record["executable"] = executable
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "SYSTEMD_PAGER": "cat",
           "SYSTEMD_COLORS": "0", "PAGER": "cat"}
    started = time.monotonic()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    try:
        process = subprocess.Popen([executable, *argv[1:]], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, env=env)
    except PermissionError:
        return {**record, "status": "permission_denied", "stdout": "", "stderr": "無法執行命令：權限不足。"}
    except OSError as exc:
        return {**record, "status": "execution_error", "errno": exc.errno,
                "stdout": "", "stderr": "無法建立命令程序。"}
    status = "completed"
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    status = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    available = byte_limit - total
                    buffers[key.data].extend(chunk[:available])
                    total += min(len(chunk), available)
                    if len(chunk) > available:
                        status = "output_limit"
                        break
                if status != "completed":
                    break
            if status == "completed":
                try:
                    process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    status = "timeout"
    finally:
        if status != "completed" or process.poll() is None:
            stop_process(process)
        process.stdout.close()
        process.stderr.close()
    output = {name: bytes(value).decode("utf-8", errors="replace") for name, value in buffers.items()}
    if status == "completed" and process.returncode:
        status = "permission_denied" if re.search(
            r"permission denied|operation not permitted|access denied|not authori[sz]ed|insufficient permissions",
            output["stderr"] + output["stdout"], re.I) else "command_failed"
    elif status == "completed" and re.search(
            r"not seeing messages from other users|insufficient permissions|permission denied",
            output["stderr"], re.I):
        status = "permission_limited"
    return {**record, **output, "status": status, "returncode": process.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3), "captured_bytes": total,
            "truncated": status == "output_limit"}


def read_small(path: Path, allowed_roots: tuple[Path, ...], limit: int = FILE_BYTES) -> dict:
    """只讀允許範圍內的一般檔案；不開啟裝置、FIFO 或範圍外的符號連結。"""
    record = {"path": str(path), "limit_bytes": limit}
    try:
        resolved = path.resolve(strict=True)
        if not any(resolved.is_relative_to(root.resolve()) for root in allowed_roots):
            return {**record, "status": "outside_allowed_roots"}
        record["resolved_path"] = str(resolved)
        if not stat.S_ISREG(resolved.stat().st_mode):
            return {**record, "status": "unsupported_file_type"}
        fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {**record, "status": "unsupported_file_type"}
            data = stream.read(limit + 1)
        truncated = len(data) > limit
        data = data[:limit]
        return {**record, "status": "truncated" if truncated else "collected",
                "captured_bytes": len(data), "captured_sha256": digest(data),
                "text": data.decode("utf-8", errors="replace"), "hex": data.hex(),
                "truncated": truncated}
    except FileNotFoundError:
        return {**record, "status": "missing"}
    except PermissionError:
        return {**record, "status": "permission_denied"}
    except (OSError, RuntimeError) as exc:
        return {**record, "status": "read_error", "error_type": type(exc).__name__}


def list_small(path: Path, pattern: str) -> tuple[dict, list[Path]]:
    """限定目錄項目數，不遞迴搜尋。"""
    try:
        entries = []
        scanned = 0
        truncated = False
        with os.scandir(path) as directory:
            for entry in directory:
                scanned += 1
                if scanned > 512 or len(entries) >= DIRECTORY_ENTRIES:
                    truncated = True
                    break
                if re.fullmatch(pattern, entry.name):
                    entries.append(Path(entry.path))
        entries.sort()
        return {"path": str(path), "status": "truncated" if truncated else "collected",
                "entries": [entry.name for entry in entries], "truncated": truncated}, entries
    except FileNotFoundError:
        return {"path": str(path), "status": "missing"}, []
    except PermissionError:
        return {"path": str(path), "status": "permission_denied"}, []
    except OSError as exc:
        return {"path": str(path), "status": "read_error", "errno": exc.errno}, []


def collect_files(source_root: Path = Path("/")) -> dict:
    """蒐集固定系統來源；source_root 僅供測試隔離，不提供命令列覆寫。"""
    root = source_root
    allowed = tuple(root / name for name in ("sys", "proc", "run/NetworkManager/conf.d", "var/lib/NetworkManager"))
    result = {"files": [], "directories": [], "links": []}

    def read(relative: str) -> None:
        result["files"].append(read_small(root / relative, allowed))

    def link(relative: str) -> None:
        path = root / relative
        try:
            target = os.readlink(path)
            result["links"].append({"path": str(path), "status": "collected", "target": target})
        except FileNotFoundError:
            result["links"].append({"path": str(path), "status": "missing"})
        except PermissionError:
            result["links"].append({"path": str(path), "status": "permission_denied"})
        except OSError as exc:
            result["links"].append({"path": str(path), "status": "read_error", "errno": exc.errno})

    for name in ("model", "compatible"):
        read(f"proc/device-tree/{name}")
    read("proc/sys/kernel/osrelease")
    for name in ("no-auto-default.state", "NetworkManager.state"):
        read(f"var/lib/NetworkManager/{name}")
    for iface in ("eth0", "eth1"):
        base = f"sys/class/net/{iface}"
        for name in ("operstate", "carrier", "carrier_changes", "address", "addr_assign_type", "mtu", "flags",
                     "ifindex", "iflink", "type", "speed", "duplex", "phydev/phy_id", "phydev/phy_interface",
                     "phydev/uevent", "device/uevent", "device/of_node/compatible", "device/of_node/status"):
            read(f"{base}/{name}")
        for name in ("device", "device/driver", "device/of_node", "phydev", "phydev/driver", "phydev/of_node"):
            link(f"{base}/{name}")
    for relative, pattern, properties in (
        ("sys/class/tty", r"tty(?:S|AMA|USB|ACM|BT)\d+", ("dev", "device/uevent", "device/of_node/compatible", "device/of_node/status")),
        ("sys/class/bluetooth", r"hci\d+", ("address", "name", "type", "device/uevent", "device/of_node/compatible")),
        ("sys/class/rfkill", r"rfkill\d+", ("name", "type", "soft", "hard", "state", "persistent")),
        ("sys/bus/mdio_bus/devices", r"[^/]+", ("phy_id", "phy_interface", "uevent", "of_node/compatible", "of_node/reg")),
    ):
        record, entries = list_small(root / relative, pattern)
        result["directories"].append(record)
        for entry in entries:
            for name in properties:
                read(str(entry.relative_to(root) / name))
            for name in ("device", "device/driver", "device/of_node", "driver", "of_node"):
                link(str(entry.relative_to(root) / name))
    record, entries = list_small(root / "run/NetworkManager/conf.d", r"[^/]+\.conf")
    result["directories"].append(record)
    for entry in entries:
        if entry.is_symlink():
            link(str(entry.relative_to(root)))
        else:
            read(str(entry.relative_to(root)))
    return result


def collect(output: Path) -> dict:
    """只建立新證據目錄；所有結果均為 collected_unverified。"""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    command_dir = output / "commands"
    command_dir.mkdir(mode=0o700)
    artifacts = []

    def save(relative: str, value: dict) -> None:
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        path = output / relative
        with path.open("xb") as stream:
            stream.write(data)
        artifacts.append({"path": relative, "bytes": len(data), "sha256": digest(data)})

    commands = []
    for key, argv in COMMANDS:
        record = run_command(argv)
        save(f"commands/{key}.json", record)
        commands.append({"key": key, "status": record["status"]})
    # sysfs 讀取也可能等待驅動；放在可逾時終止的獨立程序，限制整份快照大小。
    snapshot = run_command((sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--_snapshot"),
                           timeout=FILESYSTEM_TIMEOUT, byte_limit=FILESYSTEM_BYTES)
    filesystem = {"collection": {key: value for key, value in snapshot.items() if key != "stdout"}}
    if snapshot["status"] == "completed":
        try:
            filesystem["snapshot"] = json.loads(snapshot["stdout"])
        except (ValueError, TypeError):
            filesystem["collection"]["status"] = "invalid_snapshot"
            filesystem["partial_output"] = snapshot["stdout"]
    else:
        filesystem["partial_output"] = snapshot["stdout"]
    save("filesystem.json", filesystem)
    manifest = {
        "schema_version": 1, "status": "collected_unverified", "hardware_verified": False,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "collector_sha256": digest(Path(__file__).read_bytes()),
        "note": "僅蒐集當前狀態；未啟用或重啟網卡、服務與藍牙，未開啟 GPIO、UART 或其他裝置，未執行吞吐測試。命令原始輸出保留供追查，並不代表驗收通過。",
        "limits": {"command_timeout_seconds": COMMAND_TIMEOUT, "command_output_bytes": COMMAND_BYTES,
                   "file_bytes": FILE_BYTES, "directory_entries": DIRECTORY_ENTRIES,
                   "filesystem_timeout_seconds": FILESYSTEM_TIMEOUT, "filesystem_output_bytes": FILESYSTEM_BYTES,
                   "journal_lines_per_command": 200, "command_count": len(COMMANDS)},
        "commands": commands, "filesystem_status": filesystem["collection"]["status"], "files": list(artifacts),
    }
    save("manifest.json", manifest)
    sums = "".join(f"{item['sha256']}  {item['path']}\n" for item in artifacts).encode()
    with (output / "SHA256SUMS").open("xb") as stream:
        stream.write(sums)
    return {"status": "collected_unverified", "output": str(output.resolve()),
            "manifest_sha256": artifacts[-1]["sha256"], "sha256sums_sha256": digest(sums)}


class ChineseParser(argparse.ArgumentParser):
    """使用繁體中文顯示本工具的命令列說明與參數錯誤。"""

    def format_help(self) -> str:
        return super().format_help().replace("usage:", "用法：", 1)

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：", 1)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "參數錯誤；請使用 --help 查看用法。\n")


def main() -> int:
    if sys.argv[1:] == ["--_snapshot"]:
        print(json.dumps(collect_files(), ensure_ascii=False))
        return 0
    parser = ChineseParser(description=__doc__, add_help=False)
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示使用說明並結束")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄", help="尚不存在的輸出目錄；既有目錄一律拒絕")
    args = parser.parse_args()
    try:
        summary = collect(args.output)
    except FileExistsError:
        print("拒絕覆寫：輸出目錄或檔案已存在。", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"蒐集未完成：檔案系統錯誤 {exc.errno}；已建立的部分證據仍保留。", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
