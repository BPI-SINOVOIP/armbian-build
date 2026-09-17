#!/usr/bin/env python3
"""跨平台 Linux 唯讀預檢；不操作電源、UART、區塊媒體內容或安裝套件。

collect 只代表收集完成；validate 才核對配置。兩者均不代表硬體資格、
完整 smoke 或壓力測試。遠端只需 Python 標準函式庫；systemd 系統另需
已有的 systemctl。UUID 來自裝置管理器連結，不冒稱直接驗證磁碟超級區塊。
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import time

if __package__:
    from .bpi_h618_emmc_backup import config_fingerprint, ssh_command as _ssh_command
else:
    from bpi_h618_emmc_backup import config_fingerprint, ssh_command as _ssh_command


MAX_OUTPUT_BYTES = 1024 * 1024
OBSERVATION_SCHEMA = "bpi-lab-linux-observation-v1"
COLLECTION_SCHEMA = "bpi-lab-linux-collection-v1"
EXPECTED_SCHEMA = "bpi-lab-linux-expected-v1"
ARCHITECTURES = {
    "arm": "arm32", "arm32": "arm32", "armhf": "arm32", "armel": "arm32",
    "armv5tel": "arm32", "armv5tejl": "arm32", "armv6l": "arm32", "armv6b": "arm32",
    "armv7l": "arm32", "armv7b": "arm32", "armv8l": "arm32", "armv8b": "arm32",
    "aarch64": "arm64", "aarch64_be": "arm64", "arm64": "arm64", "riscv64": "riscv64",
}
SCOPE = {"read_only": True, "hardware_validation": False,
         "smoke_tested": False, "stress_tested": False}


REMOTE_SCRIPT = r'''
import errno
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

class Unavailable(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sample(operation):
    try:
        return {"status": "ok", "value": operation()}
    except Unavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except TimeoutError:
        raise
    except OSError as exc:
        return {"status": "unavailable" if exc.errno in (errno.ENOENT, errno.ENODEV) else "error",
                "reason": "必要系統欄位不存在或無法讀取"}
    except (ValueError, UnicodeError, RuntimeError):
        return {"status": "error", "reason": "系統資料格式、路徑或讀取上限不符"}

def read(path, limit=4096):
    with open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "系統資料超過上限")
    return raw.decode("utf-8")

def entries(path, limit=256):
    names = []
    with os.scandir(path) as directory:
        for entry in directory:
            require(len(names) < limit, "目錄項目超過上限")
            names.append(entry.name)
    return sorted(names)

def positive(raw):
    require(re.fullmatch(r"[1-9][0-9]{0,19}", raw) is not None, "數值格式不符")
    return int(raw)

def devnum(value):
    require(re.fullmatch(r"(?:0|[1-9][0-9]{0,9}):(?:0|[1-9][0-9]{0,9})", value) is not None,
            "裝置號格式不符")
    return value

def number(device):
    return str(os.major(device)) + ":" + str(os.minor(device))

def resolved(path):
    value = str(Path(path).resolve(strict=True))
    require(value.startswith("/sys/devices/"), "解析後不是 sysfs 裝置路徑")
    return value

def unescape(value):
    require(re.search(r"\\(?!040|011|012|134)", value) is None, "掛載跳脫格式不符")
    return re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value)

def mounts():
    result, seen = [], set()
    for line in read("/proc/self/mountinfo", 131072).splitlines():
        require(len(result) < 512, "掛載數超過上限")
        before, sep, after = line.partition(" - ")
        left, right = before.split(), after.split()
        require(sep and len(left) >= 6 and len(right) == 3, "mountinfo 欄位不完整")
        identifier = positive(left[0])
        require(identifier not in seen and left[1].isdigit(), "掛載編號重複或父編號無效")
        seen.add(identifier)
        result.append({"mount_id": identifier, "parent_id": int(left[1]),
                       "major_minor": devnum(left[2]), "root": unescape(left[3]),
                       "mount_point": unescape(left[4]), "options": left[5].split(","),
                       "optional_fields": left[6:], "fs_type": right[0],
                       "source": unescape(right[1]), "super_options": right[2].split(",")})
    require(result, "掛載資料為空")
    return result

def dt(name, single=False):
    raw = read("/sys/firmware/devicetree/base/" + name)
    require(raw.endswith("\0") and all(raw[:-1].split("\0")), "DT 字串未完整終止")
    values = raw[:-1].split("\0")
    require(not single or len(values) == 1, "DT model 不是單一字串")
    return values[0] if single else values

def cpuinfo():
    raw = read("/proc/cpuinfo", 131072)
    require(raw.strip(), "CPU 資料為空")
    records = []
    for block in raw.strip().split("\n\n"):
        fields = []
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            require(sep and key.strip(), "CPU 資料欄位不符")
            fields.append({"key": key.strip(), "value": value.strip()})
        records.append(fields)
    return {"raw": raw, "records": records}

def meminfo():
    result = {}
    for line in read("/proc/meminfo", 16384).splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_()]+):\s+([0-9]+)(?:\s+(kB))?", line)
        require(match is not None and match[1] not in result, "記憶體資料欄位不符")
        result[match[1]] = {"value": int(match[2]), "unit": match[3] or "count"}
    require(result, "記憶體資料為空")
    return result

def swaps():
    lines = read("/proc/swaps", 32768).splitlines()
    require(lines and lines[0].split() == ["Filename", "Type", "Size", "Used", "Priority"],
            "swap 標頭不符")
    result = []
    for line in lines[1:]:
        parts = line.split()
        require(len(parts) == 5 and parts[2].isdigit() and parts[3].isdigit()
                and re.fullmatch(r"-?[0-9]+", parts[4]), "swap 欄位不符")
        result.append({"path": unescape(parts[0]), "type": parts[1],
                       "size_kib": int(parts[2]), "used_kib": int(parts[3]), "priority": int(parts[4])})
    return result

def media():
    result = []
    for name in entries("/sys/class/block"):
        if not re.fullmatch(r"mmcblk[0-9]+", name):
            continue
        require(len(result) < 32, "MMC 媒體數超過上限")
        base = resolved("/sys/class/block/" + name)
        device = resolved(base + "/device")
        controller, sep, suffix = device.partition("/mmc_host/")
        require(sep and re.fullmatch(r"mmc[0-9]+/mmc[0-9]+:[0-9a-fA-F]+", suffix),
                "MMC 控制器路徑無法確認")
        cid = read(base + "/device/cid").strip().lower()
        require(re.fullmatch(r"[0-9a-f]{32}", cid) is not None, "CID 格式不符")
        sectors = positive(read(base + "/size").strip())
        result.append({"name": name, "sysfs_path": base, "device_path": device,
                       "major_minor": devnum(read(base + "/dev").strip()),
                       "cid": cid, "type": read(base + "/device/type").strip(),
                       "controller": controller, "sectors": sectors, "bytes": sectors * 512,
                       "slaves": entries(base + "/slaves"),
                       "is_partition": os.path.exists(base + "/partition")})
    return result

def root_identity(table):
    roots = [item for item in table if item["mount_point"] == "/"]
    require(len(roots) == 1, "根掛載不唯一")
    root = roots[0]
    if root["fs_type"] not in ("ext2", "ext3", "ext4", "f2fs", "xfs", "vfat") or root["root"] != "/":
        raise Unavailable("不支援此根檔案系統或子目錄掛載，不能確認單一根媒體")
    major_minor = root["major_minor"]
    actual = number(os.stat("/").st_dev)
    require(actual == major_minor, "根掛載與 stat 裝置號不同")
    base = resolved("/sys/dev/block/" + major_minor)
    if base.startswith("/sys/devices/virtual/"):
        raise Unavailable("不支援虛擬或堆疊根裝置")
    partition = os.path.exists(base + "/partition")
    if not partition and entries(base + "/slaves"):
        raise Unavailable("不支援虛擬或堆疊根裝置")
    parent = str(Path(base).parent) if partition else base
    if not re.fullmatch(r"mmcblk[0-9]+", Path(parent).name):
        raise Unavailable("根媒體不是可確認 CID 的直接 MMC 或 SD")
    require(not os.path.exists(parent + "/partition") and not entries(parent + "/slaves"),
            "根父媒體仍是分割區或堆疊裝置")
    require(devnum(read(base + "/dev").strip()) == major_minor, "根 sysfs 裝置號不符")
    parent_number = devnum(read(parent + "/dev").strip())
    require(resolved("/sys/dev/block/" + parent_number) == parent, "父媒體 sysfs 裝置號不符")
    uuids = []
    for name in entries("/dev/disk/by-uuid"):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", name), "UUID 連結名稱格式不符")
        try:
            info = os.stat("/dev/disk/by-uuid/" + name)
        except FileNotFoundError:
            continue
        if stat.S_ISBLK(info.st_mode) and number(info.st_rdev) == major_minor:
            uuids.append(name)
    if len(uuids) != 1:
        raise Unavailable("根 UUID 連結不存在或不唯一，不能猜測 UUID")
    return {"mount_id": root["mount_id"], "major_minor": major_minor,
            "stat_major_minor": actual, "sysfs_path": base,
            "sysfs_major_minor": devnum(read(base + "/dev").strip()),
            "parent_path": parent, "parent_major_minor": parent_number,
            "partition_number": positive(read(base + "/partition").strip()) if partition else None,
            "bytes": positive(read(base + "/size").strip()) * 512,
            "uuid": uuids[0], "uuid_source": "/dev/disk/by-uuid",
            "uuid_major_minor": major_minor, "uuid_sysfs_path": resolved("/sys/dev/block/" + major_minor)}

def service_command(argv):
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + 3
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, shell=False, start_new_session=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                                    "SYSTEMD_COLORS": "0"})
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, "systemctl 查詢逾時")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        buffers[key.data].extend(chunk)
                        require(sum(map(len, buffers.values())) <= 32768, "systemctl 輸出超過上限")
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ValueError("systemctl 查詢逾時") from None
        require(code == 0 and not buffers["stderr"], "systemctl 查詢失敗或含診斷訊息")
        return buffers["stdout"].decode("utf-8")
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()

def failed_services():
    init = read("/proc/1/comm").strip()
    require(init, "PID 1 資訊為空")
    if init != "systemd":
        if os.path.isdir("/run/systemd/system"):
            raise Unavailable("PID 1 與 systemd 執行目錄不一致")
        return {"manager": init, "status": "skipped", "reason": "PID 1 非 systemd，不適用失敗服務查詢"}
    binary = next((path for path in ("/usr/bin/systemctl", "/bin/systemctl")
                   if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if binary is None:
        raise Unavailable("systemd 系統缺少既有 systemctl，不安裝軟體")
    raw = service_command([binary, "--failed", "--type=service", "--no-legend", "--plain", "--no-pager"])
    units = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split(None, 4)
        require(len(fields) == 5 and fields[0].endswith(".service") and fields[2] == "failed",
                "systemctl 服務資料格式不符")
        units.append({"unit": fields[0], "load": fields[1], "active": fields[2],
                      "sub": fields[3], "description": fields[4]})
    return {"manager": "systemd", "status": "ok", "units": units}

def collect(nonce):
    kernel = os.uname()
    table = sample(mounts)
    data = {"schema": "bpi-lab-linux-observation-v1", "nonce": nonce,
            "uname": sample(lambda: {key: getattr(kernel, key) for key in
                                     ("sysname", "nodename", "release", "version", "machine")}),
            "cpuinfo": sample(cpuinfo), "dt_model": sample(lambda: dt("model", True)),
            "dt_compatible": sample(lambda: dt("compatible")), "mounts": table,
            "media": sample(media),
            "root": sample(lambda: root_identity(table["value"])) if table["status"] == "ok" else
                    {"status": "unavailable", "reason": "沒有可用的結構化掛載資料"},
            "swaps": sample(swaps), "meminfo": sample(meminfo), "failed_services": sample(failed_services),
            "cpu_test": {"status": "not_tested", "reason": "本工具不執行 CPU 負載測試"},
            "memory_test": {"status": "not_tested", "reason": "本工具不執行記憶體負載測試"}}
    data["root_after"] = sample(lambda: root_identity(mounts()))
    data["media_after"] = sample(media)
    data["sampling_finished"] = True
    return data

def main():
    nonce, duration = sys.argv[1:]
    require(re.fullmatch(r"[0-9a-f]{64}", nonce) is not None, "採樣識別碼不符")
    seconds = float(duration)
    require(0 < seconds <= 55, "遠端期限不符")
    def expired(signum, frame):
        raise TimeoutError("唯讀收集總期限已到")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        data = collect(nonce)
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        require(len(raw) <= 786432, "遠端報告超過上限")
        sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

if __name__ == "__main__":
    main()
'''


class LinuxError(ValueError):
    """預檢輸入、收集或證據契約無效。"""


def require(condition, message):
    if not condition:
        raise LinuxError(message)


def _json_bytes(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise LinuxError("資料無法編碼為嚴格 JSON") from None
    require(len(raw) <= MAX_OUTPUT_BYTES, "JSON 超過 1 MiB 上限")
    return raw


def parse_json(raw):
    require(type(raw) is bytes and len(raw) <= MAX_OUTPUT_BYTES, "JSON 輸入型別或長度不符")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON 含重複欄位")
            result[key] = value
        return result
    def constant(value):
        raise LinuxError("JSON 不允許非有限數值")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
        _json_bytes(result)
    except (ValueError, UnicodeError, RecursionError):
        raise LinuxError("輸入不是完整且唯一的有效 JSON") from None
    return result


def load_json(path):
    """先以 O_PATH 確認一般檔案，避免開啟管線或裝置後才做型別檢查。"""
    descriptor = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_OUTPUT_BYTES,
                "JSON 必須是有界一般檔案，不接受符號連結、裝置或管線")
        with open(f"/proc/self/fd/{descriptor}", "rb") as stream:
            raw = stream.read(MAX_OUTPUT_BYTES + 1)
        after = os.fstat(descriptor)
        require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "JSON 讀取期間被修改")
    finally:
        os.close(descriptor)
    return parse_json(raw)


def _text(value):
    return (type(value) is str and 0 < len(value) <= 4096 and value == value.strip()
            and all(ord(char) >= 32 for char in value))


def _sysfs(value):
    return (type(value) is str and len(value) <= 4096
            and re.fullmatch(r"/sys/devices/(?:[A-Za-z0-9_.:@,+-]+/)*[A-Za-z0-9_.:@,+-]+", value)
            and not {".", ".."}.intersection(value.split("/")))


def _devnum(value):
    return type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,9}):(?:0|[1-9][0-9]{0,9})", value)


def _capacity(value):
    return type(value) is int and 0 < value < 2**63 and value % 512 == 0


def validate_expected(expected):
    require(type(expected) is dict and set(expected) == {
        "schema", "architecture", "kernel_release", "dt_compatible", "root"}, "預期配置欄位不完整或含未知欄位")
    require(expected["schema"] == EXPECTED_SCHEMA, "預期配置 schema 不符")
    require(type(expected["architecture"]) is str and expected["architecture"] in ARCHITECTURES,
            "預期架構不支援")
    require(_text(expected["kernel_release"]), "預期核心版本無效")
    compatible = expected["dt_compatible"]
    require(type(compatible) is list and 0 < len(compatible) <= 32 and all(_text(x) for x in compatible)
            and len(set(compatible)) == len(compatible), "預期 DT compatible 必須為不重複的有序字串清單")
    root = expected["root"]
    require(type(root) is dict and set(root) == {"uuid", "cid", "controller", "bytes", "media_type"},
            "預期根媒體欄位不完整或含未知欄位")
    require(type(root["uuid"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", root["uuid"]),
            "預期根 UUID 格式不符")
    require(type(root["cid"]) is str and re.fullmatch(r"[0-9a-fA-F]{32}", root["cid"]), "預期 CID 格式不符")
    require(_sysfs(root["controller"]) and "/mmc_host/" not in root["controller"], "預期控制器路徑無效")
    require(_capacity(root["bytes"]), "預期容量必須為正整數且為 512 位元組倍數")
    require(root["media_type"] in ("MMC", "SD"), "預期媒體須明示 MMC 或 SD")
    _json_bytes(expected)
    return expected


def ssh_argv(ssh_config, alias, known_hosts, nonce, remote_timeout):
    require(type(nonce) is str and re.fullmatch(r"[0-9a-f]{64}", nonce), "採樣識別碼無效")
    require(type(remote_timeout) in (int, float) and math.isfinite(remote_timeout)
            and 0 < remote_timeout <= 55, "遠端期限無效")
    config, hosts = str(Path(ssh_config).absolute()), str(Path(known_hosts).absolute())
    require(_text(config) and _text(hosts), "SSH 檔案路徑無效")
    require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", hosts) is not None
            and not {".", ".."}.intersection(hosts.split("/")), "hostkey 檔案須使用無展開字元的絕對路徑")
    argv = _ssh_command(config, alias, {})[:-1]
    argv[0] = "/usr/bin/ssh"
    # SSH 會由遠端登入 shell 解讀命令；只傳固定程式路徑及受限數值，不傳任意命令。
    extras = ["UserKnownHostsFile=" + hosts,
              "GlobalKnownHostsFile=/dev/null", "KnownHostsCommand=none", "VerifyHostKeyDNS=no",
              "ProxyCommand=none", "ProxyJump=none"]
    position = len(argv) - 1
    argv[position:position] = [part for option in extras for part in ("-o", option)]
    return argv + ["/usr/bin/python3", "-I", "-B", "-", nonce, str(remote_timeout)]


def _fetch(argv, payload, timeout):
    deadline = time.monotonic() + timeout
    output, total, pending = bytearray(), 0, memoryview(payload)
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0, shell=False, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_WRITE if stream is process.stdin else selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, "SSH 唯讀收集逾時")
                for key, _ in selector.select(remaining):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            pending = pending[os.write(stream.fileno(), pending[:4096]):]
                        except BrokenPipeError:
                            raise LinuxError("SSH 未完整接收固定收集程式") from None
                        if not pending:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), min(65536, MAX_OUTPUT_BYTES - total + 1))
                        total += len(chunk)
                        require(total <= MAX_OUTPUT_BYTES, "SSH stdout 與 stderr 合計超過上限")
                        if not chunk:
                            selector.unregister(stream)
                        elif stream is process.stdout:
                            output.extend(chunk)
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
        require(code == 0, f"SSH 結束碼非零：{code}")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise LinuxError("SSH 唯讀收集逾時") from None
    except OSError as exc:
        raise LinuxError(f"SSH 程序建立或管線操作失敗，errno={exc.errno}") from None
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def collect(*, ssh_config, alias, known_hosts, timeout=20):
    """執行真 SSH 並回傳收集報告，不因收集完成而宣稱配置通過。"""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 2 <= timeout <= 60,
            "總期限必須為 2..60 秒有限數值")
    config_before, hosts_before = config_fingerprint(ssh_config), config_fingerprint(known_hosts)
    nonce = secrets.token_hex(32)
    argv = ssh_argv(ssh_config, alias, known_hosts, nonce, min(55, timeout - 1))
    started = time.monotonic()
    raw = _fetch(argv, REMOTE_SCRIPT.encode("utf-8"), timeout)
    require(config_before == config_fingerprint(ssh_config) and hosts_before == config_fingerprint(known_hosts),
            "SSH 設定或 hostkey 檔案在收集期間改變")
    observation = parse_json(raw)
    require(type(observation) is dict and observation.get("schema") == OBSERVATION_SCHEMA
            and observation.get("nonce") == nonce and observation.get("sampling_finished") is True,
            "遠端收集 schema、識別碼或完成狀態不符")
    result = {"schema": COLLECTION_SCHEMA, "status": "collected", **SCOPE,
              "source": "ssh", "alias": alias, "nonce": nonce,
              "ssh_exitcode": 0, "ssh_config": config_before, "known_hosts": hosts_before,
              "collector_sha256": hashlib.sha256(REMOTE_SCRIPT.encode("utf-8")).hexdigest(),
              "elapsed_seconds": time.monotonic() - started, "observation": observation}
    _json_bytes(result)
    return result


def validate(report, expected):
    """只檢查既有資料；不啟動子程序、不讀取目前主機或遠端硬體。"""
    validate_expected(expected)
    _json_bytes(report)
    checks = []
    def check(name, passed, reason, *, blocked=False):
        status = "passed" if passed else "blocked" if blocked else "failed"
        checks.append({"check": name, "status": status, "reason": reason})
        return passed
    def field(name, kind):
        node = observation.get(name)
        valid = (type(node) is dict and node.get("status") == "ok" and set(node) == {"status", "value"}
                 and type(node.get("value")) is kind)
        check(name + ".available", valid, "必要收集欄位必須完整且型別正確", blocked=True)
        return node["value"] if valid else None

    envelope = (type(report) is dict and report.get("schema") == COLLECTION_SCHEMA
                and report.get("status") == "collected" and report.get("source") == "ssh"
                and type(report.get("ssh_exitcode")) is int and report["ssh_exitcode"] == 0
                and all(report.get(key) is value for key, value in SCOPE.items()))
    check("collection", envelope, "報告必須是完成且未升格硬體資格的 SSH 唯讀收集", blocked=True)
    observation = report.get("observation", {}) if type(report) is dict else {}
    if type(observation) is not dict:
        observation = {}
    nonce = report.get("nonce") if type(report) is dict else None
    check("observation", observation.get("schema") == OBSERVATION_SCHEMA
          and observation.get("sampling_finished") is True and type(nonce) is str
          and re.fullmatch(r"[0-9a-f]{64}", nonce) is not None and observation.get("nonce") == nonce,
          "收集識別碼、版本與完整結束標記必須一致", blocked=True)
    check("collector_version", type(report) is dict and report.get("collector_sha256") ==
          hashlib.sha256(REMOTE_SCRIPT.encode("utf-8")).hexdigest(), "報告必須對應本版固定收集程式", blocked=True)
    check("observation.fields", set(observation) == {
        "schema", "nonce", "sampling_finished", "uname", "cpuinfo", "dt_model", "dt_compatible",
        "mounts", "media", "root", "swaps", "meminfo", "failed_services", "cpu_test", "memory_test",
        "root_after", "media_after"}, "遠端報告欄位必須符合本版契約", blocked=True)
    uname = field("uname", dict)
    if uname is not None:
        machine = uname.get("machine")
        arch = ARCHITECTURES.get(machine) if type(machine) is str else None
        check("architecture", uname.get("sysname") == "Linux" and arch is not None
              and arch == ARCHITECTURES[expected["architecture"]], "Linux uname 架構須符合預期別名")
        check("kernel_release", uname.get("release") == expected["kernel_release"], "核心版本須完整相等")
        check("uname.fields", set(uname) == {"sysname", "nodename", "release", "version", "machine"}
              and all(_text(value) for value in uname.values()), "uname 欄位必須完整", blocked=True)
    compatible = field("dt_compatible", list)
    if compatible is not None:
        check("dt_compatible", compatible == expected["dt_compatible"], "DT compatible 有序清單須完整相等")
    model = field("dt_model", str)
    if model is not None:
        check("dt_model", _text(model), "DT model 必須非空", blocked=True)
    cpu = field("cpuinfo", dict)
    if cpu is not None:
        records = cpu.get("records")
        check("cpuinfo.fields", type(cpu.get("raw")) is str and bool(cpu["raw"].strip())
              and type(records) is list and bool(records) and all(type(row) is list and bool(row)
                  and all(type(item) is dict and set(item) == {"key", "value"}
                          and _text(item["key"]) and type(item["value"]) is str for item in row) for row in records),
              "CPU 原始資料與結構化欄位必須存在", blocked=True)
    swaps = field("swaps", list)
    if swaps is not None:
        check("swaps.fields", all(type(item) is dict and set(item) == {
            "path", "type", "size_kib", "used_kib", "priority"} and _text(item["path"])
            and item["type"] in ("file", "partition") and type(item["priority"]) is int
            and type(item["size_kib"]) is int and type(item["used_kib"]) is int
            and 0 <= item["used_kib"] <= item["size_kib"] for item in swaps),
            "swap 資料必須完整；不要求停用 swap，也不執行換出測試", blocked=True)
    memory = field("meminfo", dict)
    if memory is not None:
        valid = bool(memory) and all(type(item) is dict and set(item) == {"value", "unit"}
                                    and type(item["value"]) is int and item["value"] >= 0
                                    and item["unit"] in ("kB", "count") for item in memory.values())
        total, available = memory.get("MemTotal", {}), memory.get("MemAvailable", {})
        valid = (valid and type(total) is dict and type(available) is dict
                 and total.get("unit") == available.get("unit") == "kB"
                 and 0 <= available.get("value", -1) <= total.get("value", 0) and total.get("value", 0) > 0)
        check("meminfo.fields", valid, "記憶體總量與可用量必須是有效的 kB 資料", blocked=True)
    services = field("failed_services", dict)
    if services is not None:
        if (set(services) == {"manager", "status", "reason"}
                and services.get("status") == "skipped" and _text(services.get("manager"))
                and services["manager"] != "systemd" and _text(services.get("reason"))):
            checks.append({"check": "failed_services", "status": "skipped", "reason": services["reason"]})
        else:
            units = services.get("units")
            valid = (set(services) == {"manager", "status", "units"} and services.get("manager") == "systemd"
                     and services.get("status") == "ok" and type(units) is list)
            check("failed_services", valid and not units, "systemd 查詢必須成功且沒有失敗服務",
                  blocked=not valid)
    for name in ("cpu_test", "memory_test"):
        node = observation.get(name)
        check(name + ".scope", type(node) is dict and node.get("status") == "not_tested"
              and _text(node.get("reason")), "唯讀採樣不得宣稱執行負載測試", blocked=True)

    table, root, media = field("mounts", list), field("root", dict), field("media", list)
    after_root, after_media = field("root_after", dict), field("media_after", list)
    check("identity_stable", root is not None and media is not None and root == after_root and media == after_media,
          "根身分與媒體採樣前後必須一致", blocked=True)
    mount = None
    if table is not None:
        valid = bool(table) and len(table) <= 512 and all(type(row) is dict and set(row) == {
            "mount_id", "parent_id", "major_minor", "root", "mount_point", "options", "optional_fields",
            "fs_type", "source", "super_options"} and type(row["mount_id"]) is int and row["mount_id"] > 0
            and type(row["parent_id"]) is int and row["parent_id"] >= 0 and _devnum(row["major_minor"])
            and all(type(row[key]) is str and bool(row[key]) for key in ("root", "mount_point", "fs_type", "source"))
            and all(type(row[key]) is list and all(_text(x) for x in row[key]) for key in
                    ("options", "optional_fields", "super_options")) for row in table)
        valid = valid and len({row["mount_id"] for row in table}) == len(table)
        check("mounts.fields", valid, "結構化 mountinfo 必須完整且掛載編號不重複", blocked=True)
        roots = [row for row in table if type(row) is dict and row.get("mount_point") == "/"]
        if check("root_mount", valid and len(roots) == 1, "必須找到唯一根掛載", blocked=True):
            mount = roots[0]
            check("root_stack", mount["root"] == "/" and mount["fs_type"] in
                  ("ext2", "ext3", "ext4", "f2fs", "xfs", "vfat"),
                  "overlay、NFS、Btrfs 或未知根堆疊均不支援", blocked=True)
    selected = None
    if root is not None:
        keys = {"mount_id", "major_minor", "stat_major_minor", "sysfs_path", "sysfs_major_minor", "parent_path",
                "parent_major_minor", "partition_number", "bytes", "uuid", "uuid_source", "uuid_major_minor",
                "uuid_sysfs_path"}
        valid = (set(root) == keys and all(_devnum(root.get(key)) for key in
                 ("major_minor", "stat_major_minor", "sysfs_major_minor", "parent_major_minor", "uuid_major_minor"))
                 and all(_sysfs(root.get(key)) for key in ("sysfs_path", "parent_path", "uuid_sysfs_path"))
                 and _capacity(root.get("bytes")) and _text(root.get("uuid"))
                 and type(root.get("mount_id")) is int and root["mount_id"] > 0)
        check("root.fields", valid, "根身分欄位必須完整且型別有效", blocked=True)
        if valid:
            base, parent = root["sysfs_path"], root["parent_path"]
            partition = root["partition_number"]
            path_valid = ((partition is None and base == parent
                           and root["major_minor"] == root["parent_major_minor"]) or
                          (type(partition) is int and partition > 0 and str(Path(base).parent) == parent
                           and Path(base).name == Path(parent).name + "p" + str(partition)
                           and root["major_minor"] != root["parent_major_minor"]))
            check("root_device_chain", mount is not None and root["mount_id"] == mount["mount_id"]
                  and root["major_minor"] == root["stat_major_minor"] == root["sysfs_major_minor"]
                  == root["uuid_major_minor"] == mount["major_minor"] and root["uuid_sysfs_path"] == base
                  and path_valid and not parent.startswith("/sys/devices/virtual/")
                  and re.fullmatch(r"mmcblk[0-9]+", Path(parent).name) is not None,
                  "根掛載、stat、sysfs、UUID 及父媒體路徑必須連成同一裝置", blocked=True)
            if media is not None:
                matches = [item for item in media if type(item) is dict and item.get("sysfs_path") == parent]
                if check("root_media", len(matches) == 1, "依根父媒體選取唯一媒體，不依 expected CID 搜尋", blocked=True):
                    selected = matches[0]
        check("root_uuid", root.get("uuid") == expected["root"]["uuid"]
              and root.get("uuid_source") == "/dev/disk/by-uuid", "根 UUID 必須與配置完全相等且有明確來源")
    if media is not None:
        keys = {"name", "sysfs_path", "device_path", "major_minor", "cid", "type", "controller",
                "sectors", "bytes", "slaves", "is_partition"}
        valid = len(media) <= 32
        for item in media:
            good = (type(item) is dict and set(item) == keys and _text(item.get("name"))
                    and re.fullmatch(r"mmcblk[0-9]+", item["name"]) is not None
                    and all(_sysfs(item.get(key)) for key in ("sysfs_path", "device_path", "controller"))
                    and _devnum(item.get("major_minor")) and type(item.get("cid")) is str
                    and re.fullmatch(r"[0-9a-f]{32}", item["cid"]) is not None and item.get("type") in ("MMC", "SD")
                    and _capacity(item.get("bytes")) and type(item.get("sectors")) is int
                    and item["bytes"] == item["sectors"] * 512 and item.get("slaves") == []
                    and item.get("is_partition") is False)
            if good:
                prefix = item["controller"] + "/mmc_host/"
                good = (item["sysfs_path"] == item["device_path"] + "/block/" + item["name"]
                        and item["device_path"].startswith(prefix)
                        and re.fullmatch(r"mmc[0-9]+/mmc[0-9]+:[0-9a-fA-F]+", item["device_path"][len(prefix):])
                        is not None and not item["sysfs_path"].startswith("/sys/devices/virtual/"))
            valid = valid and bool(good)
        if valid:
            valid = all(len({item[key] for item in media}) == len(media) for key in
                        ("name", "sysfs_path", "major_minor", "cid"))
        check("media.fields", valid, "媒體身分、控制器路徑、容量與唯一性必須有效", blocked=True)
    if selected is not None:
        wanted = expected["root"]
        check("parent_device", selected.get("major_minor") == root.get("parent_major_minor")
              and _capacity(selected.get("bytes")) and root["bytes"] <= selected["bytes"],
              "父媒體裝置號及根容量須一致", blocked=True)
        for key, actual, target in (("root_cid", selected.get("cid"), wanted["cid"].lower()),
                                    ("root_controller", selected.get("controller"), wanted["controller"]),
                                    ("root_bytes", selected.get("bytes"), wanted["bytes"]),
                                    ("root_media_type", selected.get("type"), wanted["media_type"])):
            check(key, actual == target, "根媒體欄位須符合預期配置")
    statuses = {item["status"] for item in checks}
    status = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
    return {"schema": "bpi-lab-linux-validation-v1", "status": status, "ok": status == "passed", **SCOPE,
            "scope": "read-only-preflight", "checks": checks,
            "limitation": "僅核對所提供的 SSH 系統宣告；離線 JSON 不證明來源真實性、實體板號或完整硬體資格。"}


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.update(add_help=False, allow_abbrev=False)
        super().__init__(*args, **kwargs)
        self._positionals.title, self._optionals.title = "子命令", "參數"
        self.add_argument("--help", action="help", help="顯示說明並結束")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        raise LinuxError("命令列參數無效；請以 --help 查看格式")


def main(argv=None):
    parser = JsonArgumentParser(description="跨平台 Linux 唯讀預檢，不代表完整短測或壓測。")
    commands = parser.add_subparsers(dest="command", required=True, title="子命令", metavar="{collect,validate}")
    collect_parser = commands.add_parser("collect", help="執行嚴格 SSH 唯讀收集，JSON 輸出至 stdout")
    for name, description in (("ssh-config", "可信專用 SSH 設定檔"), ("alias", "設定內的明確別名"),
                              ("known-hosts", "事前可信配對的 hostkey 檔案，不自動新增")):
        collect_parser.add_argument("--" + name, required=True, help=description)
    collect_parser.add_argument("--timeout", type=float, default=20, help="含傳輸總期限，2..60 秒，預設 20")
    validate_parser = commands.add_parser("validate", help="離線核對收集報告與預期配置")
    validate_parser.add_argument("--report", required=True, help="已收集的 JSON 一般檔案")
    validate_parser.add_argument("--expected", required=True, help="完整預期配置 JSON 一般檔案")
    try:
        args = vars(parser.parse_args(argv))
        command = args.pop("command")
        result = collect(**args) if command == "collect" else validate(load_json(args["report"]), load_json(args["expected"]))
        print(_json_bytes(result).decode("utf-8"))
        return 0 if result["status"] in ("collected", "passed") else 1
    except (ValueError, OSError, KeyboardInterrupt) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "檔案、SSH 程序或操作中斷，未完成預檢"
        print(json.dumps({"status": "error", "error": reason, "raw_streams_saved": False, **SCOPE}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
