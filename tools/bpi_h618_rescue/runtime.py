#!/usr/bin/python3 -B
"""RAM 救援的身分、網路、唯讀盤點與映像核對；不含儲存裝置寫入介面。"""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys

from bpi_rescue_cli import ChineseArgumentParser


SCHEMA = "bpi-h618-rescue-v1"
MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_RAW_BYTES = 32 * 1024 * 1024 * 1024
XZ_TIMEOUT_SECONDS = 600


def xz_command(*args):
    return ["/bin/busybox", "timeout", "-s", "KILL", str(XZ_TIMEOUT_SECONDS),
            "xz", "--memlimit-decompress=256MiB", *args]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def run(args, timeout=90):
    subprocess.run(args, check=True, timeout=timeout)


def ready():
    expected = json.loads(Path("/etc/bpi-rescue.json").read_text())
    actual = os.uname().release
    require(expected == {"schema": SCHEMA, "kernel": actual}, "救援 schema 或執行核心不符，禁止宣告就緒")
    record = {"schema": SCHEMA, "kernel": actual, "pid": os.getpid()}
    Path("/run/bpi-rescue-ready.json").write_text(json.dumps(record) + "\n")
    print("BPI_RESCUE_READY " + json.dumps(record, sort_keys=True), flush=True)


def load_module(alias):
    # 不使用 shell，也不接受選項形式的 alias。
    if alias and len(alias) < 4096 and not alias.startswith("-") and not any(c.isspace() for c in alias):
        try:
            result = subprocess.run(["/usr/sbin/modprobe", "-b", "--", alias], check=False,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            emit({"module": alias, "status": "模組載入逾時"})
            return
        if result.returncode:
            emit({"module": alias, "status": "載入失敗", "error": result.stderr.strip()})


def net_events():
    # 先訂閱再掃描，避免冷插拔掃描與熱插拔監聽之間遺失事件；不依賴 udev。
    with socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 15) as events:
        events.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        events.bind((os.getpid(), 1))
        for module in ("rfkill", "cfg80211", "brcmutil", "brcmfmac", "sunxi_mmc", "mmc_block"):
            load_module(module)
        for path in Path("/sys/devices").rglob("modalias"):
            try:
                load_module(path.read_text().strip())
            except OSError:
                pass
        while True:
            try:
                packet, sender = events.recvfrom(65536)
            except OSError as error:
                emit({"status": "網路模組事件讀取失敗", "error": str(error)})
                continue
            if sender[0] != 0:
                continue
            fields = dict(item.split("=", 1) for item in packet.decode(errors="replace").split("\0") if "=" in item)
            if fields.get("ACTION") in ("add", "change"):
                load_module(fields.get("MODALIAS", ""))


def ram_file(value):
    path = Path(value)
    require(path.is_absolute() and path.resolve().is_relative_to("/run") and not path.is_symlink(),
            "輸入必須是 /run 內的非符號連結一般檔案")
    require(stat.S_ISREG(path.stat().st_mode), "輸入不是一般檔案")
    return path


def wifi(args):
    require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}", args.interface), "網路介面名稱不符")
    config = ram_file(args.config)
    require(config.stat().st_uid == 0 and config.stat().st_mode & 0o077 == 0,
            "網路認證檔必須由 root 擁有且僅 root 可讀寫")
    require(not Path("/run/wpa_supplicant.pid").exists(), "已有 Wi-Fi 程序紀錄，請先由串口確認狀態")
    run(["rfkill", "unblock", "wifi"])
    run(["ip", "link", "set", "dev", args.interface, "up"])
    run(["wpa_supplicant", "-B", "-D", "nl80211", "-i", args.interface,
         "-c", str(config), "-P", "/run/wpa_supplicant.pid", "-f", "/run/wpa_supplicant.log"])
    run(["/bin/busybox", "udhcpc", "-f", "-q", "-n", "-t", "15", "-T", "4",
         "-i", args.interface, "-s", "/usr/sbin/bpi-rescue-udhcpc"])


def dhcp(args):
    interface = os.environ.get("interface", "")
    require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}", interface), "DHCP 介面不符")
    if args.event == "deconfig":
        run(["ip", "-4", "addr", "flush", "dev", interface])
        Path("/run/resolv.conf").write_text("")
        return
    if args.event not in ("bound", "renew"):
        return
    ip = str(ipaddress.IPv4Address(os.environ["ip"]))
    subnet = os.environ.get("subnet") or os.environ.get("mask")
    require(subnet, "DHCP 缺少子網遮罩，不猜測 /24")
    prefix = ipaddress.IPv4Network(f"{ip}/{subnet}", strict=False).prefixlen
    routers = [str(ipaddress.IPv4Address(p)) for p in os.environ.get("router", "").split()]
    dns = [str(ipaddress.ip_address(p)) for p in os.environ.get("dns", "").split()]
    run(["ip", "-4", "addr", "flush", "dev", interface])
    run(["ip", "-4", "addr", "add", f"{ip}/{prefix}", "dev", interface])
    if routers:
        run(["ip", "-4", "route", "replace", "default", "via", routers[0], "dev", interface])
    Path("/run/resolv.conf").write_text("".join(f"nameserver {value}\n" for value in dns))


def inventory(sys_block=Path("/sys/class/block"), proc=Path("/proc")):
    devices = []
    for device in sorted(sys_block.glob("mmcblk*")):
        if not re.fullmatch(r"mmcblk\d+", device.name):
            continue
        fields = {}
        for name in ("device/type", "device/cid", "device/name", "size", "ro", "queue/logical_block_size"):
            fields[name] = (device / name).read_text().strip()
        devices.append({"name": device.name, "sysfs_path": str(device.resolve()), **fields,
                        "bytes": int(fields["size"]) * 512})
    return {"schema": SCHEMA, "devices": devices, "mountinfo": (proc / "self/mountinfo").read_text(),
            "swaps": (proc / "swaps").read_text(), "read_only_inventory": True}


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def verify(args):
    path = ram_file(args.file)
    for digest in (args.sha256, args.raw_sha256):
        require(re.fullmatch(r"[0-9a-f]{64}", digest), "SHA-256 格式不符")
    require(0 < path.stat().st_size <= MAX_COMPRESSED_BYTES, "壓縮檔必須大於零且不超過 512 MiB")
    require(0 < args.raw_size <= MAX_RAW_BYTES, "可信解壓長度必須大於零且不超過 32 GiB")
    require(file_hash(path) == args.sha256, "壓縮映像 SHA-256 不符")
    run(xz_command("--test", "--", str(path)), timeout=XZ_TIMEOUT_SECONDS + 10)
    digest, count = hashlib.sha256(), 0
    with subprocess.Popen(xz_command("--decompress", "--stdout", "--", str(path)), stdout=subprocess.PIPE) as process:
        try:
            for data in iter(lambda: process.stdout.read(1024 * 1024), b""):
                count += len(data)
                require(count <= args.raw_size, "解壓資料超過可信長度")
                digest.update(data)
            require(process.wait() == 0, "XZ 解壓未完整成功")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    require(count == args.raw_size and digest.hexdigest() == args.raw_sha256, "解壓長度或 SHA-256 不符")
    return {"schema": SCHEMA, "sha256": args.sha256, "raw_sha256": digest.hexdigest(),
            "raw_size": count, "status": "核對通過，未寫入儲存裝置"}


def main():
    parser = ChineseArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    commands.add_parser("ready", help="核對執行核心並宣告救援身分")
    commands.add_parser("net-events", help="冷插拔掃描與熱插拔模組載入")
    commands.add_parser("inventory", help="只讀 sysfs 與 proc，不開啟區塊裝置")
    network = commands.add_parser("wifi", help="使用串口注入 /run 的認證設定")
    network.add_argument("--interface", default="wlan0", help="Wi-Fi 介面")
    network.add_argument("--config", required=True, help="/run 下 root 專用 wpa_supplicant 設定檔")
    lease = commands.add_parser("dhcp", help="處理 udhcpc 租約資料")
    lease.add_argument("event", help="租約事件")
    check = commands.add_parser("verify", help="完整核對 XZ、壓縮與解壓 SHA-256 及長度，不落地解壓")
    check.add_argument("--file", required=True, help="/run 內的壓縮映像")
    check.add_argument("--sha256", required=True, help="可信壓縮 SHA-256")
    check.add_argument("--raw-sha256", required=True, help="可信解壓 SHA-256")
    check.add_argument("--raw-size", required=True, type=int, help="可信解壓位元組數")
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            emit(inventory())
        elif args.command == "verify":
            emit(verify(args))
        elif args.command == "wifi":
            wifi(args)
        elif args.command == "dhcp":
            dhcp(args)
        elif args.command == "net-events":
            net_events()
        else:
            ready()
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"救援操作拒絕或失敗：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
