#!/usr/bin/env python3
"""以固定 SSH 採樣程式讀取軟體宣告；不驗證實體身分、不自行保存證據。"""

import argparse
import ipaddress
import json
import math
import os
import re
import selectors
import subprocess
import time


# 來源：patch/kernel/archive/sunxi-6.18/dt_64/ 的三板根節點。
PROFILES = {
    "bananapim4zero": ("sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"),
    "bananapim4zeroemac": ("sinovoip,bpi-m4-zero-emac", "sinovoip,bpi-m4-zero",
                         "allwinner,sun50i-h618"),
    "bananapim4berry": ("BiPai,bananapi-m4berry", "allwinner,sun50i-h616"),
}
MAX_OUTPUT_BYTES = 1024 * 1024
UNVERIFIED = {"physical_identity_verified": False, "hardware_validation": False}
RELEASE_KEYS = {"BOARD", "BOARD_NAME", "BOARDFAMILY", "VERSION", "BRANCH", "LINUXFAMILY"}
SSH_OPTIONS = (
    "BatchMode=yes", "StrictHostKeyChecking=yes", "ClearAllForwardings=yes",
    "RequestTTY=no", "ControlMaster=no", "ControlPath=none", "PermitLocalCommand=no",
    "ConnectTimeout=5", "ConnectionAttempts=1", "UpdateHostKeys=no",
)

REMOTE_SCRIPT = r'''
import errno
import json
import os
from pathlib import Path
import re
import shlex

def sample(operation):
    try:
        return {"status": "ok", "value": operation()}
    except OSError as exc:
        absent = exc.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO,
                              errno.EINVAL, errno.EOPNOTSUPP)
        return {"status": "unavailable" if absent else "error",
                "reason": "欄位無法提供" if absent else "欄位讀取失敗"}
    except (ValueError, UnicodeError):
        return {"status": "error", "reason": "欄位超過上限或格式錯誤"}

def read(path, limit=4096):
    with open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("欄位超過讀取上限")
    return raw.decode("utf-8")

def text_field(path, limit=4096):
    return sample(lambda: read(path, limit))

def dt_strings(path, single=False):
    raw = read(path)
    if not raw.endswith("\0") or not all(raw[:-1].split("\0")):
        raise ValueError("DT 字串未完整終止")
    values = raw[:-1].split("\0")
    if single and len(values) != 1:
        raise ValueError("DT 型號必須是單一字串")
    return values[0] if single else values

def release():
    allowed = {"BOARD", "BOARD_NAME", "BOARDFAMILY", "VERSION", "BRANCH", "LINUXFAMILY"}
    values = {}
    for line in read("/etc/armbian-release", 16384).splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in allowed:
            tokens = shlex.split(value, comments=True)
            if key in values or len(tokens) != 1:
                raise ValueError("發行資訊欄位重複或格式錯誤")
            values[key] = tokens[0]
    if not values:
        raise ValueError("發行資訊沒有允許欄位")
    return values

def devices(root, mmc=False):
    result = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 128:
                raise ValueError("裝置目錄超過採樣上限")
            if mmc and not re.fullmatch(r"mmcblk[0-9]+", entry.name):
                continue
            if len(result) >= 32:
                raise ValueError("裝置數量超過採樣上限")
            base = root + "/" + entry.name
            if mmc:
                item = {"device": entry.name,
                        "type": text_field(base + "/device/type"),
                        "name": text_field(base + "/device/name"),
                        "size": text_field(base + "/size"),
                        "controlpath": sample(lambda: os.path.dirname(
                            str(Path(base + "/device").resolve(strict=True))))}
            else:
                item = {"interface": entry.name,
                        "operstate": text_field(base + "/operstate"),
                        "speed": text_field(base + "/speed"),
                        "driver": sample(lambda: os.path.basename(
                            str(Path(base + "/device/driver").resolve(strict=True))))}
            result.append(item)
    return sorted(result, key=lambda item: item["device" if mmc else "interface"])

def kernel():
    info = os.uname()
    return {key: getattr(info, key) for key in ("sysname", "release", "version", "machine")}

report = {
    "schema": 1, "sampling_finished": True,
    "physical_identity_verified": False, "hardware_validation": False,
    "model": sample(lambda: dt_strings("/sys/firmware/devicetree/base/model", True)),
    "compatible": sample(lambda: dt_strings("/sys/firmware/devicetree/base/compatible")),
    "kernel": sample(kernel), "armbian_release": sample(release),
    "mmc": sample(lambda: devices("/sys/class/block", True)),
    "net": sample(lambda: devices("/sys/class/net")),
    "mounts": text_field("/proc/mounts", 65536),
    "swaps": text_field("/proc/swaps", 65536),
}
print(json.dumps(report, ensure_ascii=False))
'''


class ObservationError(ValueError):
    """採樣或契約檢查失敗，不能視為成功證據。"""


def ssh_command(target):
    """只接受單一明確帳號與標準 IPv4，不接受主機別名或額外指令。"""
    if not isinstance(target, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}@[0-9.]+", target):
        raise ObservationError("目標必須為安全帳號加明確 IPv4：user@IPv4")
    try:
        ipaddress.IPv4Address(target.split("@")[1])
    except ipaddress.AddressValueError:
        raise ObservationError("目標 IPv4 格式無效") from None
    return ["ssh", "-F", "/dev/null", "-T"] + [
        part for option in SSH_OPTIONS for part in ("-o", option)
    ] + [target, "python3", "-"]


def fetch(target, timeout=15):
    """在同一截止時間內送出固定程式並有界讀取兩個輸出管線。"""
    command = ssh_command(target)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ObservationError("逾時秒數必須大於零且不超過六十")
    deadline = time.monotonic() + timeout
    output, total, pending = bytearray(), 0, memoryview(REMOTE_SCRIPT.encode("utf-8"))
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, bufsize=0, shell=False) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_WRITE if stream is process.stdin
                                          else selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ObservationError("SSH 採樣逾時")
                        for key, _ in selector.select(remaining):
                            stream = key.fileobj
                            if stream is process.stdin:
                                try:
                                    pending = pending[os.write(stream.fileno(), pending[:4096]):]
                                except BrokenPipeError:
                                    pending = memoryview(b"")
                                if not pending:
                                    selector.unregister(stream)
                                    stream.close()
                            else:
                                chunk = os.read(stream.fileno(), min(65536, MAX_OUTPUT_BYTES - total + 1))
                                total += len(chunk)
                                if total > MAX_OUTPUT_BYTES:
                                    raise ObservationError("SSH 輸出超過位元組上限")
                                if not chunk:
                                    selector.unregister(stream)
                                elif stream is process.stdout:
                                    output.extend(chunk)
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
                if code:
                    raise ObservationError(f"SSH 結束狀態不為零：{code}")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    except subprocess.TimeoutExpired:
        raise ObservationError("SSH 採樣逾時") from None
    except OSError:
        raise ObservationError("SSH 程序或管線失敗") from None
    return bytes(output)


def parse_observation(raw):
    """驗證完整結構，保留缺項狀態；不把遠端任意 JSON 當成採樣證據。"""
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        raise ObservationError("採樣資料型別錯誤或超過上限")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ObservationError("JSON 欄位重複")
            result[key] = value
        return result

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise ObservationError("採樣不是完整有效的 JSON") from None
    missing = []

    def require(condition):
        if not condition:
            raise ObservationError("採樣欄位缺漏或格式錯誤")

    def field(node, kind, path):
        require(type(node) is dict)
        if node.get("status") == "ok":
            require(set(node) == {"status", "value"} and type(node["value"]) is kind)
            return node["value"]
        require(node.get("status") in ("unavailable", "error")
                and set(node) == {"status", "reason"} and type(node["reason"]) is str
                and bool(node["reason"]))
        missing.append(path)
        return kind()

    kinds = {"model": str, "compatible": list, "kernel": dict, "armbian_release": dict,
             "mmc": list, "net": list, "mounts": str, "swaps": str}
    require(type(data) is dict and set(data) == set(kinds) | {
        "schema", "sampling_finished", *UNVERIFIED})
    require(type(data["schema"]) is int and data["schema"] == 1
            and data["sampling_finished"] is True
            and all(data[key] is False for key in UNVERIFIED))
    values = {key: field(data[key], kind, key) for key, kind in kinds.items()}
    require(all(type(value) is str and value for value in values["compatible"]))
    require(all(key in missing or values[key] for key in ("model", "mounts", "swaps", "armbian_release")))
    require(all(key in missing or values[key].strip() for key in ("model", "mounts", "swaps")))
    for group, keys in (("kernel", {"sysname", "release", "version", "machine"}),
                        ("armbian_release", RELEASE_KEYS)):
        require(set(values[group]) <= keys and all(type(v) is str for v in values[group].values()))
        if group == "kernel" and group not in missing:
            require(set(values[group]) == keys and all(values[group].values()))
    for group, name, keys in (("mmc", "device", {"type", "name", "size", "controlpath"}),
                              ("net", "interface", {"operstate", "speed", "driver"})):
        require(len(values[group]) <= 32)
        seen = set()
        for item in values[group]:
            require(type(item) is dict and set(item) == keys | {name})
            require(type(item[name]) is str and bool(item[name]) and item[name] not in seen)
            seen.add(item[name])
            for key in keys:
                value = field(item[key], str, group + "." + item[name] + "." + key)
                if item[key]["status"] == "ok":
                    require(bool(value.strip()))
                    if group == "mmc" and key == "size":
                        require(re.fullmatch(r"[1-9][0-9]*", value.strip()) is not None)
    return data, sorted(missing)


def observe(target, expected_profile):
    """只比對軟體配置；EMAC 的 model 或網路可用性不能證明擴充板存在。"""
    if expected_profile not in PROFILES:
        raise ObservationError("預期配置不在三板允許清單")
    data, missing = parse_observation(fetch(target))
    not_applicable = sorted("net.lo." + key for item in data["net"].get("value", [])
                            if item["interface"] == "lo" for key in ("driver", "speed")
                            if item[key]["status"] == "unavailable")
    missing = [path for path in missing if path not in not_applicable]
    compatible = tuple(data["compatible"].get("value", []))
    profile = next((key for key, value in PROFILES.items() if compatible == value), None)
    board = data["armbian_release"].get("value", {}).get("BOARD")
    if "armbian_release" not in missing and board is None:
        missing.append("armbian_release.BOARD")
        missing.sort()
    kernel = data["kernel"].get("value", {})
    result = {"schema": 1, "target": target, "expected_profile": expected_profile,
              "software_profile": profile, **UNVERIFIED, "observation": data,
              "incomplete_fields": missing, "not_applicable_fields": not_applicable,
              "limitation": "僅比對 DT 軟體宣告；不證明實體板型、EMAC 擴充板或 HW_ID。"}
    if profile is None or profile != expected_profile:
        result.update(status="rejected", error="未知軟體配置或與預期板型不符")
    elif ((board is not None and board != profile)
          or (kernel and (kernel["sysname"] != "Linux" or kernel["machine"] != "aarch64"))):
        result.update(status="rejected", error="DT、發行資訊或核心的軟體宣告衝突")
    elif missing:
        result.update(status="incomplete", error="採樣含不可用或讀取失敗欄位，不算完整成功")
    else:
        result["status"] = "ok"
    return result


class JsonArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        raise ObservationError("命令列參數無效；請以 --help 查看允許格式")


def main(argv=None):
    parser = JsonArgumentParser(add_help=False, allow_abbrev=False, usage=(
        "%(prog)s --target user@IPv4 --expected-profile " + "{" + ",".join(PROFILES) + "}"),
        description="固定唯讀採樣；軟體宣告不等於實體身分驗證。")
    parser._optionals.title = "參數"
    parser.add_argument("--help", action="help", help="顯示說明並結束")
    parser.add_argument("--target", required=True, metavar="user@IPv4", help="明確指定單一目標")
    parser.add_argument("--expected-profile", required=True, choices=PROFILES, help="預期軟體配置")
    try:
        args = parser.parse_args(argv)
        result = observe(args.target, args.expected_profile)
    except ObservationError as exc:
        result = {"status": "error", "error": str(exc), **UNVERIFIED}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
