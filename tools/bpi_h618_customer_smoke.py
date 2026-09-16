#!/usr/bin/env python3
"""客戶 Linux 短測與結構化收證；不安裝套件、不執行來源腳本、不控制電源。"""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import time

if __package__:
    from . import bpi_h618_artifacts as safe
    from . import bpi_h618_emmc_backup as backup
    from .bpi_h618_customer_boot import EXPECTED
else:
    import bpi_h618_artifacts as safe
    import bpi_h618_emmc_backup as backup
    from bpi_h618_customer_boot import EXPECTED


REMOTE_SCRIPT = r'''
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time

CHUNK = 1024 * 1024
BLOCK = bytes(range(256)) * 4096
BLOCK_SHA = "fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83"
COMMANDS = {
    "uname": ["uname", "-a"],
    "findmnt": ["findmnt", "--json", "--target", "/", "--output", "TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN"],
    "lsblk": ["lsblk", "--json", "--bytes", "--output", "NAME,KNAME,TYPE,SIZE,FSTYPE,UUID,MOUNTPOINTS"],
    "failed_services": ["systemctl", "--failed", "--type=service", "--no-legend", "--plain", "--no-pager"],
    "ip_address": ["ip", "-j", "address", "show"],
    "ip_link": ["ip", "-j", "link", "show"],
    "iw": ["iw", "dev"],
    "dmesg": ["dmesg", "--color=never"],
}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0, "短測總期限已到")
    return value

def text_file(path, limit=65536):
    with open(path, "rb") as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, "系統資訊超過上限")
    return value.decode("utf-8", errors="replace").rstrip("\x00\r\n")

def os_release(raw):
    result = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(sep and re.fullmatch(r"[A-Z_]+", key) and key not in result, "os-release 格式不符")
        words = shlex.split(value, comments=False)
        require(len(words) <= 1, "os-release 欄位不是單一字串")
        result[key] = words[0] if words else ""
    return result

def command(argv, deadline, limit=CHUNK):
    record = {"argv": argv, "rc": None, "stdout": "", "stderr": "", "ok": False}
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    process = None
    try:
        end = min(deadline, time.monotonic() + 8)
        remaining(end)
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True,
                                   env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"})
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                for key, _ in selector.select(min(0.1, remaining(end))):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    room = limit - sum(len(value) for value in chunks.values())
                    chunks[key.data].extend(data[:room])
                    require(len(data) <= room, "命令輸出超界，保留截斷證據但不算通過")
        record["rc"] = process.wait(timeout=remaining(end))
        record["ok"] = record["rc"] == 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        record["error"] = str(exc) if isinstance(exc, ValueError) else "命令不存在、逾時或執行失敗"
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=1)
            process.stdout.close()
            process.stderr.close()
            record["rc"] = process.returncode
        record.update({name: value.decode("utf-8", errors="replace") for name, value in chunks.items()})
    return record

def media_inventory(sysroot="/sys"):
    root = Path(sysroot)
    devices = []
    for path in sorted((root / "class/block").iterdir()):
        if not re.fullmatch(r"mmcblk[0-9]+", path.name):
            continue
        devices.append({"name": path.name, "path": str(path.resolve()),
                        "device_path": str((path / "device").resolve()),
                        "cid": text_file(path / "device/cid").lower(),
                        "type": text_file(path / "device/type"),
                        "bytes": int(text_file(path / "size")) * 512,
                        "devnum": text_file(path / "dev")})
    return {"devices": devices,
            "sd_status": text_file(root / "firmware/devicetree/base/soc/mmc@4020000/status")}

def inspect_root(findmnt, sysroot="/sys"):
    require(findmnt.get("ok") is True, "findmnt 未成功，禁止暫存寫入")
    entries = json.loads(findmnt["stdout"])["filesystems"]
    require(len(entries) == 1 and entries[0].get("target") == "/", "未找到唯一根掛載")
    root = entries[0]
    require(re.fullmatch(r"[0-9]+:[0-9]+", root.get("maj:min", "")), "根裝置號無效")
    path = (Path(sysroot) / "dev/block" / root["maj:min"]).resolve(strict=True)
    require(re.fullmatch(r"mmcblk[0-9]+p[0-9]+", path.name), "根不在明確的 MMC 分割區")
    return {**root, "sys_path": str(path), "parent_path": str(path.parent)}

def identity(request, commands, sysroot="/sys"):
    data = {**media_inventory(sysroot), "root": inspect_root(commands["findmnt"], sysroot),
            "kernel_release": os.uname().release}
    validate_identity(request, data)
    return data

def validate_identity(request, data):
    expected, root = request["expected"], data["root"]
    require(data["sd_status"] == "disabled" and len(data["devices"]) == 1, "SD 未停用或 MMC 媒體不唯一")
    device = data["devices"][0]
    require(device["type"] == "MMC" and device["cid"] == expected["cid"]
            and device["bytes"] == expected["bytes"]
            and device["device_path"].startswith(expected["controller"] + "/"), "eMMC CID、容量或控制器不符")
    require(root["parent_path"] == device["path"] and root["uuid"] == request["root_uuid"]
            and root["fstype"] == "ext4", "根 UUID、父媒體或檔案系統不符")
    require(data["kernel_release"] == request["kernel_release"], "執行核心與原配組件不符")

def cpu_test(deadline, seconds):
    end = min(deadline, time.monotonic() + seconds)
    def worker(index):
        count, started = 0, time.thread_time()
        while time.monotonic() < end:
            require(hashlib.sha256(BLOCK).hexdigest() == BLOCK_SHA, "CPU SHA-256 計算不符")
            count += 1
        require(count > 0, "CPU 測試沒有完成任何計算")
        return {"worker": index, "iterations": count, "sha256": BLOCK_SHA,
                "cpu_seconds": time.thread_time() - started, "ok": True}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker, index) for index in range(2)]
        workers = [future.result() for future in futures]
    return {"ok": True, "workers": workers, "bytes_per_hash": CHUNK}

def same_entry(directory, name, identity):
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        return (current.st_dev, current.st_ino) == identity
    except FileNotFoundError:
        return False

def file_test(root_devnum, nonce, deadline, state, file_mib=16):
    require(type(file_mib) is int and 1 <= file_mib <= 128, "檔案大小必須介於 1 與 128 MiB")
    file_bytes = file_mib * CHUNK
    reference = hashlib.sha256()
    for _ in range(file_mib):
        remaining(deadline)
        reference.update(BLOCK)
    state.update(ok=False, bytes_written=0, bytes_read=0, cleaned=False,
                 expected_bytes=file_bytes, expected_sha256=reference.hexdigest(),
                 cache_method="fsync+POSIX_FADV_DONTNEED", physical_read_verified=False)
    require(hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"), "缺少單檔快取丟棄 API")
    require(re.fullmatch(r"[0-9a-f]{64}", nonce), "暫存 nonce 格式不符")
    name = "bpi-customer-smoke-" + nonce
    parent = directory = fd = None
    dir_identity = file_identity = None
    try:
        remaining(deadline)
        var = os.open("/var", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parent = os.open("tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=var)
        finally:
            os.close(var)
        info = os.fstat(parent)
        require(info.st_dev == os.makedev(*map(int, root_devnum.split(":"))) and info.st_uid == 0,
                "/var/tmp 不在已核對的 eMMC 根媒體或擁有者不符")
        require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX, "/var/tmp 可被他人替換暫存")
        space = os.fstatvfs(parent)
        require(space.f_bavail * space.f_frsize >= file_bytes + 32 * CHUNK, "eMMC 暫存空間不足，須保留至少 32 MiB 餘量")
        os.mkdir(name, 0o700, dir_fd=parent)
        created = os.stat(name, dir_fd=parent, follow_symlinks=False)
        dir_identity = (created.st_dev, created.st_ino)
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        require((os.fstat(directory).st_dev, os.fstat(directory).st_ino) == dir_identity, "新暫存目錄被替換")
        fd = os.open("payload", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        opened = os.fstat(fd)
        file_identity = (opened.st_dev, opened.st_ino)
        require(stat.S_ISREG(opened.st_mode), "暫存不是一般檔案")
        while state["bytes_written"] < file_bytes:
            offset = 0
            while offset < CHUNK:
                remaining(deadline)
                count = os.write(fd, memoryview(BLOCK)[offset:])
                require(0 < count <= CHUNK - offset, "暫存短寫無進度")
                offset += count
                state["bytes_written"] += count
        remaining(deadline)
        os.fsync(fd)
        os.posix_fadvise(fd, 0, file_bytes, os.POSIX_FADV_DONTNEED)
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while state["bytes_read"] < file_bytes:
            remaining(deadline)
            data = os.read(fd, min(CHUNK, file_bytes - state["bytes_read"]))
            require(data, "暫存回讀截斷")
            digest.update(data)
            state["bytes_read"] += len(data)
        state["sha256"] = digest.hexdigest()
        require(state["sha256"] == state["expected_sha256"] and os.fstat(fd).st_size == file_bytes,
                "暫存完整回讀 SHA-256 或長度不符")
        remaining(deadline)
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                if file_identity is not None:
                    require(same_entry(directory, "payload", file_identity), "暫存檔被替換，只保留、不刪他人檔案")
                    os.unlink("payload", dir_fd=directory)
                if dir_identity is not None:
                    require(same_entry(parent, name, dir_identity), "暫存目錄被替換，拒絕刪除")
                    os.rmdir(name, dir_fd=parent)
                    os.fsync(parent)
                state["cleaned"] = True
            finally:
                if directory is not None:
                    os.close(directory)
                if parent is not None:
                    os.close(parent)
    state["ok"] = True

def execute(request):
    require(type(request.get("duration")) is int and 1 <= request["duration"] <= 300, "duration 必須介於 1 與 300 秒")
    require(type(request.get("file_mib")) is int and 1 <= request["file_mib"] <= 128, "檔案大小必須介於 1 與 128 MiB")
    start = time.monotonic()
    deadline = start + max(0.1, request["duration"] - 2)
    report = {"schema": "bpi-h618-customer-smoke-v1", "nonce": request["nonce"], "ok": False,
              "commands": {}, "identity_verified": False, "cpu": {"ok": False}, "file": {"ok": False},
              "failures": [], "system_verified": False, "original_boot_chain_verified": False,
              "emac": {"status": "not_tested", "reason": "只收集介面狀態，未驗證實體 EMAC 或封包傳輸"},
              "wifi": {"status": "not_tested", "reason": "只保留 iw 與 ip 觀察，不視為 Wi-Fi 功能通過"},
              "peripherals": {"status": "not_tested"}}
    def failure(stage, exc):
        report["failures"].append({"stage": stage, "reason": str(exc)})
    try:
        for name, argv in COMMANDS.items():
            report["commands"][name] = command(argv, deadline, 2 * CHUNK if name == "dmesg" else CHUNK)
        for name, path, limit in (("cpuinfo", "/proc/cpuinfo", 65536), ("meminfo", "/proc/meminfo", 65536),
                                  ("os_release_raw", "/etc/os-release", 16384)):
            try:
                remaining(deadline)
                report[name] = text_file(path, limit)
            except Exception as exc:
                failure(name, exc)
        try:
            report["os_release"] = os_release(report["os_release_raw"])
            require(report["os_release"].get("VERSION_CODENAME") == request["os"], "os-release 與指定映像不符")
            require(os.geteuid() == 0, "短測需要已授權的 root SSH，工具不嘗試提權")
            report["identity_before"] = identity(request, report["commands"])
            report["identity_verified"] = True
        except Exception as exc:
            failure("identity", exc)
        failed = report["commands"]["failed_services"]
        report["failed_services"] = {"ok": failed["ok"] and not failed["stdout"].strip(),
                                     "units": failed["stdout"].splitlines()}
        if not report["failed_services"]["ok"]:
            failure("failed_services", "存在失敗服務或未能取得完整服務狀態")
        # iw 的介面名稱僅作固定唯讀命令的單一引數；不執行其原始文字。
        interfaces = re.findall(r"(?m)^\s*Interface ([A-Za-z0-9_.-]{1,15})\s*$", report["commands"]["iw"]["stdout"])
        report["wifi_links"] = [command(["iw", "dev", name, "link"], deadline) for name in interfaces[:8]]
        if report["identity_verified"]:
            try:
                report["cpu"] = cpu_test(deadline, max(0.01, remaining(deadline) / 3))
            except Exception as exc:
                failure("cpu", exc)
            try:
                fresh = command(COMMANDS["findmnt"], deadline)
                report["commands"]["findmnt_before_file"] = fresh
                require(identity(request, {"findmnt": fresh}) == report["identity_before"], "寫入前根媒體身分變更")
                file_test(report["identity_before"]["root"]["maj:min"], request["nonce"], deadline, report["file"], request["file_mib"])
            except Exception as exc:
                failure("file", exc)
            try:
                fresh = command(COMMANDS["findmnt"], deadline)
                report["commands"]["findmnt_after_file"] = fresh
                report["identity_after"] = identity(request, {"findmnt": fresh})
                require(report["identity_after"] == report["identity_before"], "測試後根媒體身分變更")
            except Exception as exc:
                failure("identity_after", exc)
        report["commands"]["dmesg_after"] = command(COMMANDS["dmesg"], deadline, 2 * CHUNK)
        for name, record in report["commands"].items():
            if not record["ok"]:
                failure(name, "採樣命令失敗、缺少工具或逾時；不得當作通過")
        report["ok"] = (report["identity_verified"] and report["cpu"]["ok"] and report["file"]["ok"]
                        and not report["failures"])
    except Exception as exc:
        failure("interrupted", exc)
    report["elapsed_seconds"] = time.monotonic() - start
    report["limits"] = "單檔 fadvise 是快取丟棄提示，不證明實體儲存回讀；沒有長期穩定性、EMAC、周邊或原開機鏈通過聲明。"
    return report

if __name__ == "__main__":
    def interrupted(signum, frame):
        raise ValueError("收到終止訊號，停止短測並清理自有暫存")
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    request = json.loads(sys.argv[1])
    result = execute(request)
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)
    sys.exit(0 if result["ok"] else 1)
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def remote_namespace():
    namespace = {"__name__": "_bpi_customer_smoke"}
    exec(REMOTE_SCRIPT, namespace)
    return namespace


def load_components(path, sha256):
    require(isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256), "必須提供外部可信 components SHA-256")
    path = Path(path).absolute()
    with safe.open_root(path.parent) as directory:
        digest, blob = safe.fingerprint(directory, path.name, limit=65536, keep=True)
    require(digest["sha256"] == sha256, "components SHA-256 不符")
    item = safe.parse_manifest(blob)
    require(type(item) is dict and item.get("schema") == "bpi-h618-customer-components-v1"
            and item.get("board") == "bananapim4zeroemac", "components schema 或板型不符")
    for key, pattern in (("root_uuid", r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"),
                         ("kernel_release", r"[0-9]+\.[0-9]+\.[0-9]+-current-sunxi64"),
                         ("os", r"[a-z][a-z0-9-]{0,31}")):
        require(isinstance(item.get(key), str) and re.fullmatch(pattern, item[key]), "components 欄位不符：" + key)
    require(type(item.get("preflight")) is dict and item["preflight"].get("source_verified") is True,
            "來源離線核對未通過")
    return item, digest


def validate_result(remote, request):
    require(type(remote) is dict and remote.get("schema") == "bpi-h618-customer-smoke-v1"
            and remote.get("nonce") == request["nonce"] and type(remote.get("ok")) is bool,
            "遠端 JSON 身分或結構不符")
    if not remote["ok"]:
        return
    core = remote_namespace()
    try:
        require(remote["identity_verified"] is True and remote["system_verified"] is False
                and remote["original_boot_chain_verified"] is False and remote["failures"] == [],
                "遠端成功狀態或驗證範圍不符")
        for key in ("identity_before", "identity_after"):
            core["validate_identity"](request, remote[key])
        require(remote["identity_before"] == remote["identity_after"], "遠端根媒體前後不一致")
        workers = remote["cpu"]["workers"]
        require(remote["cpu"]["ok"] is True and len(workers) == 2
                and [w["worker"] for w in workers] == [0, 1]
                and all(w["ok"] is True and type(w["iterations"]) is int and w["iterations"] > 0
                        and w["sha256"] == core["BLOCK_SHA"] for w in workers), "雙 CPU 計算證據不完整")
        file = remote["file"]
        reference = hashlib.sha256()
        for _ in range(request["file_mib"]):
            reference.update(core["BLOCK"])
        require(file["ok"] is True and file["cleaned"] is True
                and file["bytes_written"] == file["bytes_read"] == request["file_mib"] * 1024**2
                and file["sha256"] == reference.hexdigest()
                and file["cache_method"] == "fsync+POSIX_FADV_DONTNEED", "暫存完整校驗或清理證據不符")
        require(remote["failed_services"] == {"ok": True, "units": []}
                and all(remote[key]["status"] == "not_tested" for key in ("wifi", "emac", "peripherals")),
                "失敗服務或周邊範圍被誤判為通過")
        require(all(remote["commands"][key]["ok"] is True and remote["commands"][key]["rc"] == 0
                    for key in (*core["COMMANDS"], "dmesg_after", "findmnt_before_file", "findmnt_after_file")),
                "必要採樣未完整成功")
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("遠端成功證據缺少必要欄位") from exc


def smoke(*, ssh_config, alias, components, components_sha256, output, duration=60, file_mib=16,
          transport=backup.ssh_stream, monotonic=time.monotonic):
    require(type(duration) is int and 1 <= duration <= 300, "duration 必須是 1..300 秒整數")
    require(type(file_mib) is int and 1 <= file_mib <= 128, "file-mib 必須是 1..128 的整數")
    item, digest = load_components(components, components_sha256)
    config_digest = backup.config_fingerprint(ssh_config)
    request = {key: item[key] for key in ("root_uuid", "kernel_release", "os")}
    request.update(expected=EXPECTED, nonce=secrets.token_hex(32), duration=duration, file_mib=file_mib)
    argv = backup.ssh_command(ssh_config, alias, {})
    argv[-1] = shlex.join(["python3", "-B", "-c", REMOTE_SCRIPT, json.dumps(request, separators=(",", ":"))])
    out = Path(output).absolute()
    with safe.open_root(out.parent) as parent:
        os.mkdir(out.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    started = monotonic()
    report = {"schema": "bpi-h618-customer-smoke-host-v1", "ok": False, "status": "started",
              "components": digest, "request": request, "ssh_config": config_digest, "command_argv": argv,
              "remote_script_sha256": hashlib.sha256(REMOTE_SCRIPT.encode()).hexdigest(),
              "ssh_exitcode": None, "remote": None, "started_utc": backup.now(), "system_verified": False}
    received = {"stdout": bytearray(), "stderr": bytearray()}
    with safe.open_root(out) as directory:
        try:
            with safe.open_file(directory, "remote.json.partial", create=True) as stdout, \
                    safe.open_file(directory, "ssh-stderr.log", create=True) as stderr:
                streams = {"stdout": stdout, "stderr": stderr}
                with closing(transport(argv, started + duration, monotonic)) as events:
                    for kind, data in events:
                        require(monotonic() < started + duration, "SSH 短測總期限已到")
                        require(report["ssh_exitcode"] is None, "SSH 結束後仍有事件")
                        if kind == "exit":
                            require(type(data) is int, "SSH 退出碼格式不符")
                            report["ssh_exitcode"] = data
                        else:
                            require(kind in streams and isinstance(data, bytes), "SSH 證據格式不符")
                            require(len(received[kind]) + len(data) <= (16 * 1024**2 if kind == "stdout" else 1024**2),
                                    "SSH 證據超界，未視為通過")
                            streams[kind].write(data)
                            received[kind].extend(data)
            require(backup.config_fingerprint(ssh_config) == config_digest, "SSH 設定在測試期間變更")
            remote = json.loads(received["stdout"], object_pairs_hook=safe.unique_object)
            report["remote"] = remote
            validate_result(remote, request)
            with safe.open_file(directory, "dmesg.json", create=True) as stream:
                stream.write((json.dumps({key: remote.get("commands", {}).get(key)
                                          for key in ("dmesg", "dmesg_after")}, ensure_ascii=False) + "\n").encode())
            report.update(ok=remote["ok"] and report["ssh_exitcode"] == 0, status="collected")
        except (ValueError, OSError, KeyboardInterrupt) as exc:
            report.update(status="failed", error=str(exc) if isinstance(exc, ValueError) else "SSH 中斷或主機檔案操作失敗")
        finally:
            report.update(finished_utc=backup.now(), elapsed_seconds=monotonic() - started)
            with safe.open_file(directory, "report.json.partial", create=True) as stream:
                stream.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def main(argv=None):
    parser = safe.JsonArgumentParser(description=__doc__)
    for name, description in (("ssh-config", "既有嚴格 SSH 專用設定"), ("alias", "設定內明確 alias"),
                              ("components", "可信原配組件 JSON"), ("components-sha256", "外部提供的清單 SHA-256"),
                              ("output", "新的私有收證目錄，拒絕覆寫及符號連結")):
        parser.add_argument("--" + name, required=True, help=description)
    parser.add_argument("--duration", type=int, default=60, help="含收證的短測總秒數，1..300；預設 60")
    parser.add_argument("--file-mib", type=int, default=16, help="新檔大小 MiB，1..128；預設 16，另須保留 32 MiB 空間")
    try:
        report = smoke(**vars(parser.parse_args(argv)))
        print(json.dumps({key: report.get(key) for key in ("ok", "status", "ssh_exitcode", "error", "system_verified")}, ensure_ascii=False))
        return 0 if report["ok"] else 1
    except (ValueError, OSError, KeyboardInterrupt) as exc:
        print(json.dumps({"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "短測或主機收證失敗"},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
