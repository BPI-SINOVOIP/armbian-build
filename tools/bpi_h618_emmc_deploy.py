#!/usr/bin/env python3
"""受限 eMMC userarea 部署；須另有實體覆寫授權，不選擇開機、不重啟或重試。

主機先完整驗證備份及來源，遠端只接受 RAM 根系統的 bpi-h618-rescue-v1。
成功只表示指定範圍寫入及回讀一致，不表示可開機、復原成功或量產驗證。
只有 receipt.json 的 verified 收據可採信；receipt.json.partial 一律不作成功證據。
"""

from contextlib import closing
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

if __package__:
    from . import bpi_h618_emmc_backup as backup
else:
    import bpi_h618_emmc_backup as backup

safe = backup.safe
DeployError = safe.ArtifactError
require = safe.require
CHUNK = 1024 * 1024
PREFIX = b"BPI_EMMC_DEPLOY_V1 "
MAX_LOG = 2 * CHUNK

# 只執行隨此倉庫交付的固定程式，不從日誌或遠端輸入取得程式碼。
REMOTE_CORE = r'''
from contextlib import ExitStack
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time

checks = {"__name__": "_bpi_backup_checks"}
exec(BACKUP_SOURCE, checks)
require = checks["require"]
CHUNK = 1024 * 1024
SD_PREFIX = 4 * CHUNK

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON 欄位重複")
        result[key] = value
    return result

def rescue_identity(path="/etc/bpi-rescue.json", mounts="/proc/self/mountinfo"):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "救援身分必須是一般檔案")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            blob = stream.read(65537)
        require(len(blob) <= 65536, "救援身分檔過大")
        identity = json.loads(blob, object_pairs_hook=unique_object)
    finally:
        os.close(fd)
    require(type(identity) is dict and identity.get("schema") == "bpi-h618-rescue-v1"
            and identity.get("kernel") == os.uname().release, "救援 schema 或執行核心不符")
    roots = []
    for line in Path(mounts).read_text().splitlines():
        head, separator, tail = line.partition(" - ")
        fields, fs = head.split(), tail.split()
        require(separator and len(fields) >= 6 and len(fs) >= 3, "掛載紀錄無法解析")
        if fields[4] == "/":
            roots.append((fields, fs))
    require(len(roots) == 1, "無法唯一核對救援根掛載")
    fields, fs = roots[0]
    device = os.stat("/").st_dev
    require(fs[0] in ("rootfs", "ramfs", "tmpfs") and fields[3] == "/"
            and os.major(device) == 0 and fields[2] == f"0:{os.minor(device)}",
            "根系統不是獨立 RAM；拒絕 overlay 或任何區塊裝置根系統")
    return {"schema": identity["schema"], "kernel": identity["kernel"], "root_fs": fs[0],
            "root_dev": fields[2], "root_ram": True, "identity_sha256": hashlib.sha256(blob).hexdigest()}

def inspect_sd(expected, sysroot="/sys/class/block", devroot="/dev"):
    matches = []
    for base in Path(sysroot).iterdir():
        if not re.fullmatch(r"mmcblk[0-9]+", base.name):
            continue
        if (checks["text"](base / "device/type") == "SD"
                and checks["text"](base / "device/cid").lower() == expected["cid"]
                and str((base / "device").resolve()).startswith(expected["controller"] + "/")):
            matches.append(base)
    require(len(matches) == 1, "無法唯一找到受保護 SD")
    base = matches[0]
    require(not (base / "partition").exists(), "受保護 SD 不是整碟")
    node = os.stat(str(Path(devroot) / base.name), follow_symlinks=False)
    number = checks["text"](base / "dev")
    require(stat.S_ISBLK(node.st_mode) and number == f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}",
            "受保護 SD 區塊裝置身分不符")
    size = int(checks["text"](base / "size")) * 512
    require(size >= SD_PREFIX, "受保護 SD 小於前綴核對範圍")
    return {**expected, "type": "SD", "device": str(Path(devroot) / base.name), "devnum": number, "bytes": size}

def process_xz(source, expected, capacity, check_deadline, write=None):
    require(0 < expected["raw"]["bytes"] <= capacity, "來源長度超過 userarea 容量")
    compressed_hash, raw_hash = hashlib.sha256(), hashlib.sha256()
    compressed_bytes = raw_bytes = streams = padding = 0
    decoder = None
    try:
        while True:
            check_deadline()
            chunk = source.read(CHUNK)
            require(isinstance(chunk, bytes) and len(chunk) <= CHUNK, "來源讀取未遵守有界 bytes 契約")
            if not chunk:
                break
            compressed_bytes += len(chunk)
            require(compressed_bytes <= expected["compressed"]["bytes"], "XZ 壓縮長度超出指定範圍")
            compressed_hash.update(chunk)
            pending = chunk
            while pending or (decoder is not None and not decoder.needs_input):
                check_deadline()
                if decoder is None:
                    if streams:
                        rest = pending.lstrip(b"\0")
                        padding += len(pending) - len(rest)
                        pending = rest
                        if not pending:
                            break
                    require(padding % 4 == 0, "XZ 串流填補必須是四位元組倍數")
                    padding = 0
                    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=256 * CHUNK)
                raw = decoder.decompress(pending, max_length=CHUNK)
                pending = b""
                require(raw_bytes + len(raw) <= min(expected["raw"]["bytes"], capacity),
                        "XZ 解壓即將越過來源或 userarea 範圍，停止寫入")
                if raw and write is not None:
                    write(raw)
                raw_hash.update(raw)
                raw_bytes += len(raw)
                if decoder.eof:
                    require(decoder.check != lzma.CHECK_NONE and lzma.is_check_supported(decoder.check),
                            "XZ 缺少支援的完整性檢查")
                    pending = decoder.unused_data
                    decoder = None
                    streams += 1
    except lzma.LZMAError as exc:
        raise ValueError("XZ 損壞或解碼記憶體超限") from exc
    require(streams > 0 and decoder is None and padding % 4 == 0, "XZ 截斷或填補不完整")
    result = {"compressed": {"bytes": compressed_bytes, "sha256": compressed_hash.hexdigest()},
              "raw": {"bytes": raw_bytes, "sha256": raw_hash.hexdigest()}}
    require(result == expected, "XZ 壓縮或原始長度／SHA-256 不符")
    check_deadline()
    return result

def write_chunk(fd, data, state, limit, check_deadline):
    offset = 0
    require(state["bytes_written"] + len(data) <= limit, "寫入範圍超界")
    while offset < len(data):
        check_deadline()
        position = state["bytes_written"]
        state["attempted_end"] = position + len(data) - offset
        state["write_started"] = True
        count = os.pwrite(fd, data[offset:], position)
        require(type(count) is int and 0 < count <= len(data) - offset, "eMMC 短寫沒有進度或回傳長度異常")
        state["bytes_written"] += count
        offset += count

def hash_range(fd, size, check_deadline):
    digest, position = hashlib.sha256(), 0
    while position < size:
        check_deadline()
        data = os.pread(fd, min(CHUNK, size - position), position)
        require(bool(data) and len(data) <= min(CHUNK, size - position), "完整範圍回讀截斷或長度異常")
        digest.update(data)
        position += len(data)
    check_deadline()
    return {"bytes": position, "sha256": digest.hexdigest()}

def sd_evidence(fd, protected, check_deadline):
    identity = inspect_sd(protected)
    checks["check_fd"](fd, identity)
    return {"identity": identity, "prefix": hash_range(fd, SD_PREFIX, check_deadline)}

def run(request, source, emit):
    state = {"status": "preflight", "bytes_written": 0, "attempted_end": 0, "write_started": False,
             "range": {"start": 0, "end_exclusive": request["source"]["raw"]["bytes"]},
             "bootable": False, "boot_selected": False, "boot_verified": False,
             "sd_before": None, "sd_after": None, "error": None}
    require(request.get("confirm_overwrite") is True and request.get("backup_verified") is True,
            "缺少明確覆寫確認或完整備份守門")
    require(request["source"]["raw"]["bytes"] <= request["expected"]["bytes"], "來源超過 userarea")
    require(request["expected"]["cid"] != request["protected_sd"]["cid"]
            and request["expected"]["controller"] != request["protected_sd"]["controller"], "目標與受保護媒體重疊")
    deadline = time.monotonic() + request["timeout"]
    def check_deadline(*args):
        require(not args and time.monotonic() < deadline, "遠端部署總期限已到")
    previous = signal.signal(signal.SIGALRM, check_deadline)
    signal.setitimer(signal.ITIMER_REAL, request["timeout"])
    sd_fd = None
    try:
        with ExitStack() as stack:
            rescue = rescue_identity()
            target = checks["inspect"](request["expected"])
            sd = inspect_sd(request["protected_sd"])
            require(target["devnum"] != sd["devnum"], "禁止對受保護 SD 開啟寫入描述符")
            sd_fd = os.open(sd["device"], os.O_RDONLY | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
            stack.callback(os.close, sd_fd)
            state["sd_before"] = sd_evidence(sd_fd, request["protected_sd"], check_deadline)
            require(state["sd_before"]["identity"] == sd, "SD 在開啟時已變更")
            require(checks["inspect"](request["expected"]) == target and rescue_identity() == rescue,
                    "寫入前媒體或救援身分變更")
            fd = os.open(target["device"], os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
            stack.callback(os.close, fd)
            checks["check_fd"](fd, target)
            require(checks["inspect"](request["expected"]) == target, "目標在開啟期間變更")
            state.update(status="writing", identity=target, rescue=rescue)
            emit("ready", state=state)
            checkpoint = 64 * CHUNK
            def write(data):
                nonlocal checkpoint
                write_chunk(fd, data, state, request["source"]["raw"]["bytes"], check_deadline)
                if state["bytes_written"] >= checkpoint:
                    emit("progress", bytes_written=state["bytes_written"], attempted_end=state["attempted_end"])
                    checkpoint += 64 * CHUNK
            try:
                state["source"] = process_xz(source, request["source"], target["bytes"], check_deadline, write)
                state["status"] = "readback"
                os.fsync(fd)
                # BLKFLSBUF 只刷新及丟棄目標快取；不重讀分割表，不修改任何 SD 狀態。
                checks["fcntl"].ioctl(fd, 0x1261)
                state["readback"] = hash_range(fd, state["bytes_written"], check_deadline)
                require(state["readback"] == request["source"]["raw"], "eMMC 完整寫入範圍回讀 SHA-256 不符")
                checks["check_fd"](fd, target)
                require(checks["inspect"](request["expected"]) == target and rescue_identity() == rescue,
                        "部署後媒體或救援身分變更")
            finally:
                try:
                    state["sd_after"] = sd_evidence(sd_fd, request["protected_sd"], check_deadline)
                except Exception:
                    state["sd_after_error"] = "無法在期限內完成 SD 後置核對"
            require(state["sd_before"] == state["sd_after"], "SD 前綴或媒體身分改變／未完成核對")
            check_deadline()
            state["status"] = "verified"
        check_deadline()
        emit("verified", state=state)
    except Exception as exc:
        state.update(status="failed", error=str(exc) if isinstance(exc, ValueError) else "遠端寫入、同步或回讀失敗")
        emit("failed", state=state)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    return state

def main():
    request = json.loads(sys.argv[1], object_pairs_hook=unique_object)
    def emit(event, **fields):
        record = {"schema": 1, "event": event, "nonce": request["nonce"], "time_utc": checks["now"](), **fields}
        print("BPI_EMMC_DEPLOY_V1 " + json.dumps(record, ensure_ascii=False), flush=True)
    try:
        run(request, sys.stdin.buffer, emit)
        return 0
    except Exception:
        return 1

if __name__ == "__main__":
    sys.exit(main())
'''


def remote_namespace():
    namespace = {"__name__": "_bpi_deploy_checks", "BACKUP_SOURCE": backup.REMOTE_SCRIPT}
    exec(REMOTE_CORE, namespace)
    return namespace


def remote_program():
    return "BACKUP_SOURCE = " + repr(backup.REMOTE_SCRIPT) + "\n" + REMOTE_CORE


def deadline_check(deadline, clock):
    remaining = deadline - clock()
    require(remaining > 0, "部署總期限已到；不允許產生 verified 收據")
    return remaining


def hash_value(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def unchanged(directory, name, stream, original):
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(current.st_mode) and backup.file_identity(current) == original
            and backup.file_identity(os.fstat(stream.fileno())) == original, "來源路徑、描述符或檔案內容身分已變更")


def hash_stream(stream, limit, check):
    digest, count = hashlib.sha256(), 0
    while True:
        check()
        data = stream.read(CHUNK)
        if not data:
            break
        count += len(data)
        require(count <= limit, "一般檔案超過允許大小")
        digest.update(data)
    check()
    return {"bytes": count, "sha256": digest.hexdigest()}


def validate_backup(path, expected, check):
    path = Path(path).absolute()
    require(path.name == "manifest.json", "只接受已發布的完整備份 manifest.json，不接受 .partial")
    with safe.open_root(path.parent) as directory:
        meta, blob = safe.fingerprint(directory, path.name, limit=safe.MAX_MANIFEST_BYTES, keep=True)
        record = safe.parse_manifest(blob)
        require(type(record) is dict and type(record.get("schema")) is int and record["schema"] == 1
                and record.get("kind") == "bpi-h618-emmc-userarea-backup"
                and record.get("status") == "complete" and record.get("ok") is True
                and record.get("expected") == expected and type(record.get("ssh_exitcode")) is int
                and record["ssh_exitcode"] == 0,
                "備份不是完整成功紀錄，或 CID／容量／控制器不符")
        try:
            raw = record["host"]["raw"]
            compressed = record["host"]["compressed"]
            remote = record["remote"]
            require(raw["bytes"] == expected["bytes"] and hash_value(raw["sha256"])
                    and compressed == record["host"]["received_compressed"]
                    and type(compressed["bytes"]) is int and 0 < compressed["bytes"] <= safe.MAX_ARTIFACT_BYTES,
                    "備份主機摘要無效")
            backup.remote_records(b"\n".join(backup.PREFIX + json.dumps(item).encode() for item in remote),
                                  record["nonce"], expected)
            require(remote[1]["raw"] == raw and hash_value(compressed["sha256"]), "備份兩端原始摘要不符")
            artifact = record["artifact"]
            require(artifact == "emmc-userarea.img.gz", "備份產物不是完整 userarea gzip")
        except (KeyError, TypeError, IndexError) as exc:
            raise DeployError("備份清單缺少完整兩端摘要或產物證據") from exc
        with safe.open_file(directory, artifact) as stream:
            original = backup.file_identity(os.fstat(stream.fileno()))
            require(hash_stream(stream, compressed["bytes"], check) == compressed, "備份 gzip 不存在完整對應內容或摘要不符")
            unchanged(directory, artifact, stream, original)
    return {"path": str(path), **meta, "raw": raw, "compressed": compressed,
            "restore_verified": False, "trust": "操作者明確指定的可信備份；已重新核對 gzip 壓縮摘要"}


def upload_stream(argv, chunks, deadline, clock):
    """有界雙向 SSH；只從固定來源生成器送 stdin，同時排空兩個輸出管線。"""
    deadline_check(deadline, clock)
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    pending = b""
    try:
        os.set_blocking(process.stdin.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                for key, _ in selector.select(min(0.2, deadline_check(deadline, clock))):
                    deadline_check(deadline, clock)
                    if key.data == "stdin":
                        if not pending:
                            pending = next(chunks, b"")
                            if not pending:
                                selector.unregister(process.stdin)
                                process.stdin.close()
                                continue
                        try:
                            count = os.write(process.stdin.fileno(), pending[:65536])
                        except BlockingIOError:
                            continue
                        require(count > 0, "SSH stdin 寫入未前進")
                        pending = pending[count:]
                        yield "sent", count
                    else:
                        data = os.read(key.fileobj.fileno(), 65536)
                        if data:
                            yield key.data, data
                        else:
                            selector.unregister(key.fileobj)
        try:
            code = process.wait(timeout=deadline_check(deadline, clock))
        except subprocess.TimeoutExpired as exc:
            raise DeployError("SSH 未在總期限內結束") from exc
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
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def write_all(fd, data):
    position = 0
    while position < len(data):
        count = os.write(fd, data[position:])
        require(type(count) is int and 0 < count <= len(data) - position, "主機證據未完整寫入")
        position += count


def save_receipt(fd, record):
    data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    write_all(fd, data)
    os.fsync(fd)


def validate_state(state, request, *, final=False):
    require(type(state) is dict and state.get("range") == {"start": 0, "end_exclusive": request["source"]["raw"]["bytes"]}
            and all(state.get(key) is False for key in ("bootable", "boot_selected", "boot_verified")),
            "遠端範圍或開機狀態不符")
    identity, rescue, before = state.get("identity", {}), state.get("rescue", {}), state.get("sd_before", {})
    require(all(type(value) is dict for value in (identity, rescue, before)), "遠端前置證據必須為結構化物件")
    require(all(identity.get(key) == value for key, value in request["expected"].items())
            and identity.get("type") == "MMC" and re.fullmatch(r"/dev/mmcblk[0-9]+", identity.get("device", ""))
            and all(identity.get(key) is False for key in ("mounted", "swap", "holders")), "遠端 eMMC 身分不符")
    require(rescue.get("schema") == "bpi-h618-rescue-v1" and rescue.get("root_ram") is True
            and rescue.get("root_fs") in ("rootfs", "ramfs", "tmpfs"), "遠端未驗證 RAM 救援身分")
    sd = before.get("identity", {})
    require(type(sd) is dict and type(before.get("prefix")) is dict
            and sd.get("type") == "SD" and sd.get("devnum") != identity.get("devnum")
            and all(sd.get(key) == value for key, value in request["protected_sd"].items())
            and before.get("prefix", {}).get("bytes") == 4 * CHUNK
            and hash_value(before.get("prefix", {}).get("sha256")), "受保護 SD 前置證據不符")
    require(type(state.get("bytes_written")) is int and type(state.get("attempted_end")) is int
            and 0 <= state["bytes_written"] <= state["attempted_end"] <= request["source"]["raw"]["bytes"],
            "遠端寫入計數越界")
    if final:
        require(state.get("status") == "verified" and state["bytes_written"] == request["source"]["raw"]["bytes"]
                and state.get("source") == request["source"] and state.get("readback") == request["source"]["raw"]
                and state.get("sd_after") == before, "遠端完整回讀、來源或 SD 後置證據不符")


def deploy(*, confirm_overwrite=False, backup_manifest, source, compressed_sha256, raw_sha256, raw_size,
           expected_cid, expected_size, expected_controller, protected_sd_cid, protected_sd_controller,
           ssh_config, alias, output_dir, timeout=21600, transport=upload_stream, monotonic=time.monotonic):
    require(confirm_overwrite is True, "缺少 --confirm-overwrite；未啟動 SSH 或任何媒體寫入")
    expected = backup.validate_expected(expected_cid, expected_size, expected_controller)
    protected = backup.validate_expected(protected_sd_cid, 512, protected_sd_controller)
    del protected["bytes"]
    require(expected["cid"] != protected["cid"] and expected["controller"] != protected["controller"], "目標與受保護 SD 不得相同")
    require(hash_value(compressed_sha256) and hash_value(raw_sha256), "必須提供可信壓縮及原始 SHA-256")
    require(type(raw_size) is int and 0 < raw_size <= expected_size and raw_size % 512 == 0, "來源 rawsize 須為磁區倍數且不超過 eMMC")
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 86400, "總期限須為有限正數且不超過 86400 秒")
    start = monotonic()
    deadline = start + timeout
    def check():
        return deadline_check(deadline, monotonic)
    # 參數、備份及來源全部通過後才允許建立任何 SSH 子行程。
    base_argv = backup.ssh_command(ssh_config, alias, {})
    config_digest = backup.config_fingerprint(ssh_config)
    backup_proof = validate_backup(backup_manifest, expected, check)
    source_path = Path(source).absolute()
    require(source_path.suffix.lower() == ".xz", "來源必須是明確的普通 XZ 檔")
    with safe.open_root(source_path.parent) as source_dir, safe.open_file(source_dir, source_path.name) as stream:
        original = backup.file_identity(os.fstat(stream.fileno()))
        require(0 < original["st_size"] <= safe.MAX_ARTIFACT_BYTES, "來源 XZ 大小無效")
        expected_source = {"compressed": {"bytes": original["st_size"], "sha256": compressed_sha256},
                           "raw": {"bytes": raw_size, "sha256": raw_sha256}}
        core = remote_namespace()
        try:
            core["process_xz"](stream, expected_source, expected_size, check)
        except ValueError as exc:
            raise DeployError(str(exc)) from exc
        unchanged(source_dir, source_path.name, stream, original)
        stream.seek(0)
        request = {"confirm_overwrite": True, "backup_verified": True, "backup_manifest_sha256": backup_proof["sha256"],
                   "expected": expected, "protected_sd": protected, "source": expected_source,
                   "nonce": secrets.token_hex(32), "timeout": check()}
        program = remote_program()
        base_argv[-1] = shlex.join(["python3", "-B", "-c", program, json.dumps(request, separators=(",", ":"))])
        record = {"schema": "bpi-h618-emmc-deploy-v1", "status": "prepared", "ok": False, "bootable": False,
                  "boot_selected": False, "boot_verified": False, "restore_verified": False,
                  "confirm_overwrite": True, "backup": backup_proof, "request": request,
                  "source": {"path": str(source_path), "identity": original, **expected_source},
                  "command_argv": base_argv, "remote_program_sha256": hashlib.sha256(program.encode()).hexdigest(),
                  "backup_program_sha256": hashlib.sha256(backup.REMOTE_SCRIPT.encode()).hexdigest(),
                  "started_utc": backup.now(), "finished_utc": None, "ssh_exitcode": None, "error": None,
                  "write_possible": False, "remote_state": None, "compressed_bytes_sent": 0,
                  "range": {"start": 0, "end_exclusive": raw_size},
                  "limits": "只改寫 [0, rawsize)；不清除舊尾端或 GPT，不重讀分割表，不操作 boot0/boot1/RPMB。"
                            "失聯時寫入進度可能超過最後回報；沒有自動復原、重試、電源或開機選擇。"}
        out = Path(output_dir).absolute()
        with safe.open_root(out.parent) as parent:
            os.mkdir(out.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        with safe.open_root(out) as directory:
            fds, published_inode = {}, None
            try:
                for name in ("receipt.json.partial", "remote.jsonl", "ssh-stderr.log"):
                    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
                    fds[name] = fd
                    os.fchmod(fd, 0o600)
                receipt = fds["receipt.json.partial"]
                save_receipt(receipt, record)
                os.fsync(directory)
                def chunks():
                    digest, count = hashlib.sha256(), 0
                    while True:
                        check()
                        unchanged(source_dir, source_path.name, stream, original)
                        data = stream.read(CHUNK)
                        unchanged(source_dir, source_path.name, stream, original)
                        if not data:
                            break
                        digest.update(data)
                        count += len(data)
                        yield data
                    require({"bytes": count, "sha256": digest.hexdigest()} == expected_source["compressed"],
                            "實際傳送的來源摘要不符")
                seen_ready = seen_verified = False
                buffer = bytearray()
                totals = {"stdout": 0, "stderr": 0}
                record.update(status="running", write_possible=True)
                save_receipt(receipt, record)
                with closing(transport(base_argv, chunks(), deadline, monotonic)) as events:
                    for kind, data in events:
                        check()
                        require(record["ssh_exitcode"] is None, "SSH 退出後仍有事件")
                        if kind == "sent":
                            require(type(data) is int and data > 0, "傳送計數無效")
                            record["compressed_bytes_sent"] += data
                            require(record["compressed_bytes_sent"] <= original["st_size"], "傳送範圍超界")
                        elif kind == "exit":
                            require(type(data) is int, "SSH 退出碼無效")
                            record["ssh_exitcode"] = data
                        else:
                            require(kind in totals and isinstance(data, bytes), "傳輸輸出類型無效")
                            totals[kind] += len(data)
                            require(totals[kind] <= (MAX_LOG if kind == "stdout" else backup.MAX_STDERR), "遠端證據超過大小上限")
                            write_all(fds["remote.jsonl" if kind == "stdout" else "ssh-stderr.log"], data)
                            if kind == "stderr":
                                continue
                            buffer.extend(data)
                            while b"\n" in buffer:
                                line, _, tail = buffer.partition(b"\n")
                                buffer[:] = tail
                                if not line.startswith(PREFIX):
                                    continue
                                event = safe.parse_manifest(line[len(PREFIX):])
                                require(type(event) is dict and type(event.get("schema")) is int and event["schema"] == 1
                                        and event.get("nonce") == request["nonce"] and not seen_verified, "遠端證據識別碼或順序不符")
                                action = event.get("event")
                                if action == "failed":
                                    record["remote_state"] = event.get("state")
                                    raise DeployError("遠端部署失敗；保留部分寫入及 SD 核對證據")
                                if action == "ready":
                                    require(not seen_ready, "遠端 ready 重複")
                                    validate_state(event.get("state"), request)
                                    seen_ready = True
                                elif action == "verified":
                                    require(seen_ready, "缺少遠端前置核對")
                                    validate_state(event.get("state"), request, final=True)
                                    seen_verified = True
                                elif action == "progress":
                                    require(seen_ready and type(event.get("bytes_written")) is int
                                            and 0 <= event["bytes_written"] <= raw_size, "遠端進度超界或缺少 ready")
                                    record["last_progress"] = event
                                else:
                                    raise DeployError("未知遠端狀態")
                                if action in ("ready", "verified"):
                                    record["remote_state"] = event["state"]
                                save_receipt(receipt, record)
                            require(len(buffer) <= safe.MAX_MANIFEST_BYTES, "遠端證據單行超長")
                require(record["ssh_exitcode"] == 0 and seen_verified and not buffer
                        and record["compressed_bytes_sent"] == original["st_size"], "SSH、來源傳輸或 verified 證據未完整結束")
                unchanged(source_dir, source_path.name, stream, original)
                require(backup.config_fingerprint(ssh_config) == config_digest, "SSH 設定在部署期間改變")
                check()
                record.update(status="verified", ok=True, finished_utc=backup.now(), elapsed_seconds=monotonic() - start)
                save_receipt(receipt, record)
                for fd in fds.values():
                    os.fsync(fd)
                os.fsync(directory)
                current = os.stat("receipt.json.partial", dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) ==
                        (os.fstat(receipt).st_dev, os.fstat(receipt).st_ino), "收據路徑被替換")
                check()
                os.link("receipt.json.partial", "receipt.json", src_dir_fd=directory,
                        dst_dir_fd=directory, follow_symlinks=False)
                published_inode = current.st_ino
                linked = os.stat("receipt.json", dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISREG(linked.st_mode) and linked.st_ino == current.st_ino, "發布期間收據被替換")
                os.fsync(directory)
                check()
                # 保留同一 inode 的工作檔，不在成功發布後另做可能失敗的清理或 fsync。
            except BaseException as exc:
                if published_inode is not None:
                    try:
                        if os.stat("receipt.json", dir_fd=directory, follow_symlinks=False).st_ino == published_inode:
                            os.unlink("receipt.json", dir_fd=directory)
                    except FileNotFoundError:
                        pass
                record.update(status="failed", ok=False, finished_utc=backup.now(), elapsed_seconds=monotonic() - start,
                              error=str(exc) if isinstance(exc, DeployError) else "部署中斷或本機傳輸／發布／同步失敗")
                if "receipt.json.partial" in fds:
                    try:
                        save_receipt(fds["receipt.json.partial"], record)
                    except OSError:
                        pass
                for fd in (*fds.values(), directory):
                    try:
                        os.fsync(fd)
                    except OSError:
                        pass
                raise
            finally:
                for fd in fds.values():
                    os.close(fd)
    return record


def main(argv=None):
    parser = safe.JsonArgumentParser(description=__doc__)
    parser.add_argument("--confirm-overwrite", action="store_true", help="已取得本次 eMMC userarea 覆寫授權才可指定")
    for name, help_text in (
        ("backup-manifest", "可信完整備份 manifest.json"), ("source", "普通 XZ 來源檔"),
        ("compressed-sha256", "可信 XZ 壓縮摘要"), ("raw-sha256", "可信原始映像摘要"),
        ("expected-cid", "目標 eMMC CID"), ("expected-controller", "目標 /sys/devices 控制器路徑"),
        ("protected-sd-cid", "受保護 SD CID"), ("protected-sd-controller", "受保護 SD 控制器路徑"),
        ("ssh-config", "既有專用嚴格 SSH 設定"), ("alias", "SSH 設定內明確 alias"),
        ("output-dir", "新的私有嘗試及證據目錄"),
    ):
        parser.add_argument("--" + name, required=True, help=help_text)
    parser.add_argument("--raw-size", required=True, type=int, help="可信來源解壓位元組數")
    parser.add_argument("--expected-size", required=True, type=int, help="精確 eMMC userarea 容量")
    parser.add_argument("--timeout", type=float, default=21600, help="含所有本機驗證與遠端操作的總期限秒數")
    try:
        result = deploy(**vars(parser.parse_args(argv)))
        print(json.dumps({"ok": True, "status": result["status"], "bootable": False, "restore_verified": False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyboardInterrupt, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "bootable": False,
                          "error": str(exc) if isinstance(exc, DeployError) else "部署拒絕或中斷；請檢查私有 .partial 證據"},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
