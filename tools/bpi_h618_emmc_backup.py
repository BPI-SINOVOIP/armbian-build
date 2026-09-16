#!/usr/bin/env python3
"""透過明確 SSH 設定唯讀備份 eMMC userarea；不修改媒體、不操作電源或 UART。

所有產物只建立在新的 --output-dir，失敗保留 .partial。完整成功須同時具備
manifest.json 與其中指定的 gzip；僅檔案存在不代表備份成功或已驗證復原。
遠端須有 Python 3 與讀取區塊裝置的權限，不使用 sudo、不安裝套件。
"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
import zlib

if __package__:
    from . import bpi_h618_artifacts as safe
else:
    import bpi_h618_artifacts as safe


CHUNK = 1024 * 1024
MAX_STDERR = 256 * 1024
PREFIX = b"BPI_EMMC_BACKUP_V1 "
BackupError = safe.ArtifactError
require = safe.require

# 遠端程式只使用標準函式庫；stdout 專供 gzip，stderr 專供有界結構化證據。
REMOTE_SCRIPT = r'''
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import sys
import time
import zlib
from datetime import datetime, timezone

def require(condition, message):
    if not condition:
        raise ValueError(message)

def text(path):
    return Path(path).read_text(encoding="ascii").strip()

def now():
    return datetime.now(timezone.utc).isoformat()

def inspect(expected, sysroot="/sys/class/block", procroot="/proc", devroot="/dev"):
    candidates = []
    for base in Path(sysroot).iterdir():
        if not re.fullmatch(r"mmcblk[0-9]+", base.name):
            continue
        if text(base / "device/type") != "MMC":
            continue
        cid = text(base / "device/cid").lower()
        device_path = str((base / "device").resolve())
        if cid == expected["cid"] and device_path.startswith(expected["controller"] + "/"):
            candidates.append(base)
    require(len(candidates) == 1, "無法唯一找到指定 CID 與控制器的 MMC userarea")
    base = candidates[0]
    size = int(text(base / "size")) * 512
    require(size == expected["bytes"], "eMMC 容量與指定值不完全相等")
    require(not (base / "partition").exists(), "拒絕以分割區作為 userarea")
    device = str(Path(devroot) / base.name)
    node = os.stat(device, follow_symlinks=False)
    require(stat.S_ISBLK(node.st_mode), "eMMC 路徑不是原生區塊裝置")
    devnum = text(base / "dev")
    require(devnum == f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}", "裝置號與 sysfs 不符")
    entries = [base] + sorted(base.glob(base.name + "p*"))
    numbers = {text(entry / "dev") for entry in entries}
    require(all(not list((entry / "holders").iterdir()) for entry in entries),
            "eMMC 或子分割區存在 holders")
    root_dev = os.stat("/").st_dev
    require(f"{os.major(root_dev)}:{os.minor(root_dev)}" not in numbers, "拒絕根系統所在媒體")
    for line in text(Path(procroot) / "self/mountinfo").splitlines():
        fields = line.split()
        require(len(fields) >= 6, "無法解析掛載證據")
        require(fields[2] not in numbers, "eMMC 或子分割區仍被掛載")
    for line in text(Path(procroot) / "swaps").splitlines()[1:]:
        fields = line.split()
        require(len(fields) >= 5, "無法解析 swap 證據")
        path = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[0])
        info = os.stat(path)
        value = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
        require(f"{os.major(value)}:{os.minor(value)}" not in numbers,
                "eMMC 或子分割區仍作為 swap 或 swap 檔案使用")
    return {"device": device, "devnum": devnum, "cid": expected["cid"], "bytes": size,
            "controller": expected["controller"], "type": "MMC", "partitions": sorted(numbers - {devnum}),
            "mounted": False, "swap": False, "holders": False}

def check_fd(fd, identity):
    info = os.fstat(fd)
    require(stat.S_ISBLK(info.st_mode), "唯讀描述符不是區塊裝置")
    require(f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}" == identity["devnum"],
            "開啟期間裝置身分已變更")
    size = struct.unpack("=Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
    require(size == identity["bytes"], "BLKGETSIZE64 與預期容量不符")

def stream_fd(fd, size, output, check_deadline):
    digest, total = hashlib.sha256(), 0
    encoder = zlib.compressobj(1, zlib.DEFLATED, 31)
    while total < size:
        check_deadline()
        data = os.read(fd, min(1024 * 1024, size - total))
        require(bool(data) and len(data) <= size - total, "eMMC 讀取截斷或長度異常")
        digest.update(data)
        total += len(data)
        output.write(encoder.compress(data))
    check_deadline()
    output.write(encoder.flush())
    output.flush()
    return {"bytes": total, "sha256": digest.hexdigest()}

def run(request, emit, output):
    start = time.monotonic()
    deadline = start + request["timeout"]
    def check_deadline(*args):
        if args or time.monotonic() >= deadline:
            raise ValueError("遠端備份逾時")
    previous = signal.signal(signal.SIGALRM, check_deadline)
    signal.setitimer(signal.ITIMER_REAL, request["timeout"])
    try:
        identity = inspect(request["expected"])
        # O_EXCL 阻止與已掛載的媒體並用；全程只開啟唯讀描述符，不切換裝置旗標。
        fd = os.open(identity["device"], os.O_RDONLY | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            check_fd(fd, identity)
            require(inspect(request["expected"]) == identity, "開啟期間媒體狀態已變更")
            emit("start", identity=identity, read_only=True)
            raw = stream_fd(fd, identity["bytes"], output, check_deadline)
            check_fd(fd, identity)
            require(inspect(request["expected"]) == identity, "備份期間媒體狀態已變更")
            check_deadline()
            emit("complete", identity=identity, raw=raw, elapsed_seconds=time.monotonic() - start)
        finally:
            os.close(fd)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

def main():
    request = json.loads(sys.argv[1])
    def emit(event, **fields):
        record = {"schema": 1, "event": event, "nonce": request["nonce"], "time_utc": now(), **fields}
        sys.stderr.write("BPI_EMMC_BACKUP_V1 " + json.dumps(record, ensure_ascii=False) + "\n")
        sys.stderr.flush()
    try:
        run(request, emit, sys.stdout.buffer)
    except Exception as exc:
        emit("error", error=str(exc) if isinstance(exc, ValueError) else "遠端唯讀備份失敗")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def check_deadline(deadline, monotonic):
    remaining = deadline - monotonic()
    require(remaining > 0, "備份總期限已到；未完成產物保留為 .partial")
    return remaining


def validate_expected(cid, size, controller):
    require(isinstance(cid, str) and re.fullmatch(r"[0-9a-fA-F]{32}", cid), "CID 須為 32 位十六進位")
    require(type(size) is int and 0 < size <= safe.MAX_ARTIFACT_BYTES and size % 512 == 0,
            "容量須為正整數、512 位元組倍數且不超過 64 GiB")
    require(isinstance(controller, str) and re.fullmatch(r"/sys/devices/[A-Za-z0-9_./:+-]+", controller)
            and ".." not in controller.split("/") and "." not in controller.split("/"),
            "控制器須為明確的 /sys/devices 絕對路徑")
    return {"cid": cid.lower(), "bytes": size, "controller": controller.rstrip("/")}


def config_fingerprint(path):
    path = Path(path).absolute()
    with safe.open_root(path.parent) as directory:
        digest, _ = safe.fingerprint(directory, path.name, limit=safe.MAX_MANIFEST_BYTES)
    return digest


def ssh_command(config, alias, request):
    require(isinstance(alias, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", alias),
            "SSH alias 格式無效，不接受任意 SSH 引數")
    options = (
        "StrictHostKeyChecking=yes", "BatchMode=yes", "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no", "PreferredAuthentications=publickey", "IdentitiesOnly=yes",
        "UpdateHostKeys=no", "ControlMaster=no", "ControlPath=none", "ControlPersist=no",
        "ClearAllForwardings=yes", "ForwardAgent=no", "PermitLocalCommand=no",
        "RequestTTY=no", "RemoteCommand=none", "Compression=no", "ConnectTimeout=15",
        "ConnectionAttempts=1", "ServerAliveInterval=15", "ServerAliveCountMax=3",
    )
    argv = ["ssh", "-F", str(Path(config).absolute()), "-T"]
    for option in options:
        argv.extend(["-o", option])
    command = shlex.join(["python3", "-B", "-c", REMOTE_SCRIPT,
                          json.dumps(request, ensure_ascii=True, separators=(",", ":"))])
    return [*argv, alias, command]


def ssh_stream(argv, deadline, monotonic):
    """單一 SSH 子行程的有界 stdout/stderr 串流；停止消費亦會終止並回收。"""
    check_deadline(deadline, monotonic)
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                for key, _ in selector.select(min(0.2, check_deadline(deadline, monotonic))):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if data:
                        yield key.data, data
                    else:
                        selector.unregister(key.fileobj)
        try:
            code = process.wait(timeout=check_deadline(deadline, monotonic))
        except subprocess.TimeoutExpired as exc:
            raise BackupError("SSH 未在總期限內結束") from exc
        yield "exit", code
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        finally:
            process.stdout.close()
            process.stderr.close()


def observed_records(stderr):
    """保留遠端宣告；此步驟本身不代表其身分、摘要或成功狀態可信。"""
    return [safe.parse_manifest(line[len(PREFIX):]) for line in stderr.splitlines()
            if line.startswith(PREFIX)]


def remote_records(stderr, nonce, expected):
    records = observed_records(stderr)
    require(len(records) == 2 and all(type(record) is dict for record in records),
            "遠端缺少唯一的開始及完成紀錄")
    require([record.get("event") for record in records] == ["start", "complete"], "遠端狀態順序或結果不符")
    for record in records:
        require(type(record.get("schema")) is int and record["schema"] == 1
                and record.get("nonce") == nonce and isinstance(record.get("time_utc"), str),
                "遠端證據版本、識別碼或時間不符")
        identity = record.get("identity")
        require(type(identity) is dict and all(identity.get(key) == value for key, value in expected.items())
                and identity.get("type") == "MMC" and all(identity.get(key) is False for key in ("mounted", "swap", "holders"))
                and isinstance(identity.get("device"), str) and re.fullmatch(r"/dev/mmcblk[0-9]+", identity["device"]),
                "遠端媒體身分或唯讀前置狀態不符")
    require(records[0].get("read_only") is True and records[0]["identity"] == records[1]["identity"],
            "遠端前後核對不一致或不是唯讀開啟")
    raw = records[1].get("raw")
    require(type(raw) is dict and type(raw.get("bytes")) is int and raw["bytes"] == expected["bytes"]
            and isinstance(raw.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]),
            "遠端原始資料長度或 SHA-256 無效")
    return records


def verify_gzip(directory, name, expected_size, deadline, monotonic):
    """從落盤檔案重新讀取，只接受單一完整 gzip 串流與精確解壓長度。"""
    compressed, raw = hashlib.sha256(), hashlib.sha256()
    compressed_size = raw_size = 0
    decoder = zlib.decompressobj(31)
    with safe.open_file(directory, name) as stream:
        before = os.fstat(stream.fileno())
        try:
            while True:
                check_deadline(deadline, monotonic)
                data = stream.read(CHUNK)
                if not data:
                    break
                require(not decoder.eof, "gzip 有多餘尾端或多個串流")
                compressed.update(data)
                compressed_size += len(data)
                while data:
                    check_deadline(deadline, monotonic)
                    decoded = decoder.decompress(data, CHUNK)
                    data = decoder.unconsumed_tail
                    raw_size += len(decoded)
                    require(raw_size <= expected_size, "gzip 解壓長度超過指定容量")
                    raw.update(decoded)
                    require(not decoder.unused_data, "gzip 有多餘尾端或多個串流")
        except zlib.error as exc:
            raise BackupError("gzip 格式、CRC 或壓縮資料損壞") from exc
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and compressed_size == before.st_size,
                "回讀期間備份檔案變動")
    require(decoder.eof and raw_size == expected_size, "gzip 截斷或解壓容量不完全相等")
    check_deadline(deadline, monotonic)
    return {"compressed": {"bytes": compressed_size, "sha256": compressed.hexdigest()},
            "raw": {"bytes": raw_size, "sha256": raw.hexdigest()}, "file_identity": file_identity(after)}


def file_identity(info):
    return {name: getattr(info, name) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}


@contextmanager
def new_file(directory, name):
    """沿用安全建立 helper，例外離開時亦同步已接收的 .partial 證據。"""
    with safe.open_file(directory, name, create=True) as stream:
        os.fchmod(stream.fileno(), 0o600)
        try:
            yield stream
        finally:
            stream.flush()
            os.fsync(stream.fileno())
            os.fsync(directory)


def save_state(stream, record):
    blob = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    stream.seek(0)
    stream.truncate()
    stream.write(blob)
    stream.flush()
    os.fsync(stream.fileno())


@contextmanager
def publication_guard(out, record, started, monotonic):
    """涵蓋清理與所有 context 關閉；後置失敗不得留下本次正式成功標記。"""
    published = []
    directory_identity = os.stat(out, follow_symlinks=False)
    try:
        yield published
    except BaseException as exc:
        manifest_inode = dict(published).get("manifest.json")
        if manifest_inode is not None:
            with safe.open_root(out) as directory:
                current = os.fstat(directory)
                require((current.st_dev, current.st_ino) ==
                        (directory_identity.st_dev, directory_identity.st_ino), "回復時產物目錄已被替換")
                for name, inode in reversed(published):
                    try:
                        final = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISREG(final.st_mode) or final.st_ino != inode:
                        continue
                    try:
                        os.link(name, name + ".partial", src_dir_fd=directory, dst_dir_fd=directory,
                                follow_symlinks=False)
                    except FileExistsError:
                        partial = os.stat(name + ".partial", dir_fd=directory, follow_symlinks=False)
                        require(stat.S_ISREG(partial.st_mode) and partial.st_ino == inode,
                                "回復時 .partial 已被替換，拒絕覆寫")
                    os.unlink(name, dir_fd=directory)
                record.update(status="failed", ok=False, artifact="emmc-userarea.img.gz.partial",
                              stderr="ssh-stderr.log.partial", finished_utc=now(), elapsed_seconds=monotonic() - started,
                              error=str(exc) if isinstance(exc, BackupError) else "備份發布後同步或關閉失敗，已撤回成功標記")
                if record["phases"][-1]["status"] != "failed":
                    record["phases"].append({"status": "failed", "time_utc": record["finished_utc"]})
                # 重開由安全 helper 固定的一般檔案，不追隨可能被替換的路徑。
                with safe.open_file(directory, "manifest.json.partial") as pinned:
                    require(os.fstat(pinned.fileno()).st_ino == manifest_inode, "回復時工作清單已被替換")
                    fd = os.open(f"/proc/self/fd/{pinned.fileno()}", os.O_WRONLY | os.O_CLOEXEC)
                    with os.fdopen(fd, "wb") as manifest:
                        save_state(manifest, record)
                os.fsync(directory)
        raise


def backup(*, ssh_config, alias, expected_cid, expected_size, expected_controller,
           output_dir, timeout=21600, transport=ssh_stream, monotonic=time.monotonic):
    """建立一次新嘗試；transport 可注入只回傳 stdout/stderr/exit 的替身生成器。"""
    expected = validate_expected(expected_cid, expected_size, expected_controller)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 86400,
            "總期限須為有限正數且不超過 86400 秒")
    started = monotonic()
    deadline = started + timeout
    request = {"expected": expected, "nonce": secrets.token_hex(32), "timeout": timeout}
    argv = ssh_command(ssh_config, alias, request)
    config_digest = config_fingerprint(ssh_config)
    out = Path(output_dir).absolute()
    require(out.name not in ("", ".", ".."), "必須指定新的產物目錄")
    with safe.open_root(out.parent) as parent:
        os.mkdir(out.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    record = {"schema": 1, "kind": "bpi-h618-emmc-userarea-backup", "status": "started", "ok": False,
              "started_utc": now(), "finished_utc": None, "elapsed_seconds": None, "timeout_seconds": timeout,
              "expected": expected, "ssh_config": {"path": str(Path(ssh_config).absolute()), **config_digest},
              "ssh_alias": alias, "command_argv": argv, "nonce": request["nonce"],
              "remote_script_sha256": hashlib.sha256(REMOTE_SCRIPT.encode()).hexdigest(),
              "remote": None, "remote_observed_unvalidated": [],
              "host": {"received_compressed": {"bytes": 0, "sha256": hashlib.sha256().hexdigest()}},
              "ssh_exitcode": None, "error": None,
              "artifact": "emmc-userarea.img.gz.partial", "stderr": "ssh-stderr.log.partial",
              "compression": {"format": "gzip", "level": 1}, "media_written": False,
              "restore_verified": False, "write_authorized": False,
              "limits": "僅備份 userarea，不含 boot0、boot1、RPMB；未驗證復原，不能授權覆寫。"
                        "排他開啟與前後檢查不是快照，不能防止其他特權程序直接改寫媒體。",
              "phases": [{"status": "started", "time_utc": now()}]}
    with publication_guard(out, record, started, monotonic) as published, \
            safe.open_root(out) as directory, new_file(directory, "manifest.json.partial") as manifest:
        save_state(manifest, record)
        try:
            digest, count, stderr = hashlib.sha256(), 0, bytearray()
            next_checkpoint = started + 30
            with new_file(directory, record["artifact"]) as archive, \
                    new_file(directory, record["stderr"]) as diagnostic:
                check_deadline(deadline, monotonic)
                record["status"] = "streaming"
                record["phases"].append({"status": "streaming", "time_utc": now()})
                save_state(manifest, record)
                with closing(transport(argv, deadline, monotonic)) as events:
                    for kind, data in events:
                        check_deadline(deadline, monotonic)
                        require(record["ssh_exitcode"] is None, "SSH 結束後仍收到額外事件")
                        if kind == "stdout":
                            require(isinstance(data, bytes), "傳輸資料須為 bytes")
                            count += len(data)
                            require(count <= expected_size + expected_size // 100 + CHUNK,
                                    "壓縮串流超過容量容許上限")
                            archive.write(data)
                            digest.update(data)
                            record["host"]["received_compressed"] = {"bytes": count, "sha256": digest.hexdigest()}
                        elif kind == "stderr":
                            require(isinstance(data, bytes), "診斷資料須為 bytes")
                            require(len(stderr) + len(data) <= MAX_STDERR, "SSH 診斷超過上限")
                            stderr.extend(data)
                            diagnostic.write(data)
                        elif kind == "exit":
                            require(type(data) is int, "SSH 退出碼無效")
                            record["ssh_exitcode"] = data
                        else:
                            raise BackupError("不支援的傳輸事件")
                        if monotonic() >= next_checkpoint:
                            archive.flush()
                            diagnostic.flush()
                            record["last_progress_utc"] = now()
                            save_state(manifest, record)
                            next_checkpoint = monotonic() + 30
            record["remote_observed_unvalidated"] = observed_records(bytes(stderr))
            record["host"]["stderr"] = {"bytes": len(stderr), "sha256": hashlib.sha256(stderr).hexdigest()}
            require(record["ssh_exitcode"] == 0, "SSH 失敗或缺少正常退出碼")
            require(config_fingerprint(ssh_config) == config_digest, "SSH 設定在備份期間變更")
            record["remote"] = remote_records(bytes(stderr), request["nonce"], expected)
            record["status"] = "verifying"
            record["phases"].append({"status": "verifying", "time_utc": now()})
            save_state(manifest, record)
            verified = verify_gzip(directory, record["artifact"], expected_size, deadline, monotonic)
            require(verified["compressed"] == record["host"]["received_compressed"], "落盤 gzip 與接收摘要不符")
            require(verified["raw"] == record["remote"][1]["raw"], "主機解壓回讀與遠端原始 SHA-256 不符")
            record["host"].update(verified)
            check_deadline(deadline, monotonic)
            record.update(status="complete", ok=True, artifact="emmc-userarea.img.gz", stderr="ssh-stderr.log",
                          finished_utc=now(), elapsed_seconds=monotonic() - started)
            record["phases"].append({"status": "complete", "time_utc": record["finished_utc"]})
            save_state(manifest, record)
            identities = {record["artifact"]: verified["file_identity"],
                          record["stderr"]: file_identity(os.stat("ssh-stderr.log.partial", dir_fd=directory, follow_symlinks=False)),
                          "manifest.json": file_identity(os.fstat(manifest.fileno()))}
            # 以不覆寫的硬連結發布，manifest 最後出現；發布失敗只撤回本次建立的連結。
            for name in (record["artifact"], record["stderr"], "manifest.json"):
                source = os.stat(name + ".partial", dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISREG(source.st_mode) and file_identity(source) == identities[name],
                        "發布前產物被替換或變動")
                os.link(name + ".partial", name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
                published.append((name, linked.st_ino))
                require(stat.S_ISREG(linked.st_mode) and (linked.st_dev, linked.st_ino) == (source.st_dev, source.st_ino),
                        "發布期間產物被替換")
            os.fsync(directory)
        except BaseException as exc:
            for name, inode in reversed(published):
                try:
                    if os.stat(name, dir_fd=directory, follow_symlinks=False).st_ino == inode:
                        os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass
            try:
                record["remote_observed_unvalidated"] = observed_records(bytes(stderr))
            except BackupError:
                record["remote_observed_unvalidated"] = None
            record["host"]["stderr"] = {"bytes": len(stderr), "sha256": hashlib.sha256(stderr).hexdigest()}
            record.update(status="failed", ok=False, artifact="emmc-userarea.img.gz.partial",
                          stderr="ssh-stderr.log.partial", finished_utc=now(), elapsed_seconds=monotonic() - started,
                          error=str(exc) if isinstance(exc, BackupError) else "備份中斷或主機檔案／傳輸操作失敗")
            record["phases"].append({"status": "failed", "time_utc": record["finished_utc"]})
            save_state(manifest, record)
            os.fsync(directory)
            raise
        for name, _ in published:
            try:
                os.unlink(name + ".partial", dir_fd=directory)
            except OSError:
                pass
        os.fsync(directory)
    return record


def main(argv=None):
    parser = safe.JsonArgumentParser(description=__doc__)
    parser.add_argument("--ssh-config", required=True, help="既有專用 SSH 設定，不得為符號連結")
    parser.add_argument("--alias", required=True, help="設定內明確的 SSH alias")
    parser.add_argument("--expected-cid", required=True, help="可信紀錄中的完整 eMMC CID")
    parser.add_argument("--expected-size", required=True, type=int, help="精確 userarea 位元組數")
    parser.add_argument("--expected-controller", required=True, help="可信 /sys/devices 控制器絕對路徑")
    parser.add_argument("--output-dir", required=True, help="新的備份及證據目錄，拒絕覆寫")
    parser.add_argument("--timeout", type=float, default=21600, help="含解壓回讀的總期限秒數；預設 21600")
    try:
        args = parser.parse_args(argv)
        result = backup(**vars(args))
        print(json.dumps({"ok": True, "status": result["status"], "output_dir": args.output_dir,
                          "raw": result["host"]["raw"], "restore_verified": False}, ensure_ascii=False))
        return 0
    except (BackupError, OSError, KeyboardInterrupt, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc) if isinstance(exc, BackupError)
                          else "備份中斷或檔案／SSH 操作失敗；請檢查指定目錄的 .partial 證據"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
