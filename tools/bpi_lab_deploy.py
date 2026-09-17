#!/usr/bin/env python3
"""共用受限 RAM 救援部署；沒有任何預設板號、媒體或覆寫授權。

沿用既有完整 userarea 備份格式與寫入核心；舊格式名稱不是 H618 授權。
只接受固定程式與結構化 SSH 設定，不提供任意外部命令入口。
"""

from contextlib import closing
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import time

if __package__:
    from . import bpi_h618_emmc_deploy as core
else:
    import bpi_h618_emmc_deploy as core

safe, backup, require = core.safe, core.backup, core.require


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), "欄位缺少或含未知欄位")


def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value),
            "識別符格式不符")
    return value


def path(value):
    require(isinstance(value, (str, Path)), "路徑型別不符")
    result = Path(value)
    require(result.is_absolute() and ".." not in result.parts, "須使用無上層跳轉的絕對路徑")
    return result


def checked_bytes(reference, maximum=1024**2):
    fields(reference, "path sha256")
    location = path(reference["path"])
    require(core.hash_value(reference["sha256"]), "檔案摘要格式不符")
    with safe.open_root(location.parent) as directory:
        digest, blob = safe.fingerprint(directory, location.name, limit=maximum, keep=True)
    require(digest["sha256"] == reference["sha256"], "固定檔案摘要不符：" + location.name)
    return blob


def load(reference):
    def constant(_):
        raise core.DeployError("JSON 不允許非有限數值")
    try:
        return json.loads(checked_bytes(reference), object_pairs_hook=safe.unique_object, parse_constant=constant)
    except (UnicodeError, RecursionError) as exc:
        raise core.DeployError("清單不是有效的有界 JSON") from exc


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode()


def save(directory, name, blob):
    with safe.open_root(directory) as root, safe.open_file(root, name, create=True) as stream:
        stream.write(blob)
        stream.flush()
        os.fsync(stream.fileno())
        os.fsync(root)


def new_directory(value):
    location = path(value)
    with safe.open_root(location.parent) as parent:
        os.mkdir(location.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    return location


def digest_record(value, maximum):
    fields(value, "bytes sha256")
    require(type(value["bytes"]) is int and 0 < value["bytes"] <= maximum
            and core.hash_value(value["sha256"]), "長度或 SHA-256 無效")


def validate_ssh(value, *, require_hosts=True):
    fields(value, "host port user identity known_hosts")
    require(type(value["host"]) is str and str(ipaddress.ip_address(value["host"])) == value["host"],
            "SSH 主機須為明示且正規化的 IP")
    require(type(value["port"]) is int and 1 <= value["port"] <= 65535, "SSH 埠無效")
    identifier(value["user"])
    for key in ("identity", "known_hosts"):
        if key == "known_hosts" and not require_hosts and value[key] is None:
            continue
        checked_bytes(value[key], 65536)


def snapshot_ssh(value, directory):
    """固定金鑰與 hostkey 副本；不接受 Include、代理程式或 shell 設定。"""
    validate_ssh(value)
    directory = path(directory)
    require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", str(directory)), "SSH 副本路徑含展開字元")
    for key in ("identity", "known_hosts"):
        save(directory, key, checked_bytes(value[key], 65536))
    config = ("Host bpi-lab\n"
              f" HostName {value['host']}\n Port {value['port']}\n User {value['user']}\n"
              f" IdentityFile {directory / 'identity'}\n UserKnownHostsFile {directory / 'known_hosts'}\n"
              " GlobalKnownHostsFile /dev/null\n KnownHostsCommand none\n VerifyHostKeyDNS no\n"
              " ProxyCommand none\n ProxyJump none\n StrictHostKeyChecking yes\n")
    save(directory, "ssh-config", config.encode("ascii"))
    with safe.open_root(directory) as root:
        for name in ("identity", "known_hosts", "ssh-config"):
            with safe.open_file(root, name) as stream:
                os.fchmod(stream.fileno(), 0o400)
    return {"path": str(directory / "ssh-config"), "sha256": hashlib.sha256(config.encode()).hexdigest()}


def validate_contract(contract):
    fields(contract, "schema hardware_id expected protected_sd sd_prefix rescue backup authorization ssh")
    require(contract["schema"] == "bpi-lab-deploy-v1", "部署契約 schema 不符")
    identifier(contract["hardware_id"])
    for key in ("expected", "protected_sd"):
        value = contract[key]
        fields(value, "cid bytes controller")
        require(value == backup.validate_expected(value["cid"], value["bytes"], value["controller"]),
                "媒體身分須正規化")
    target, sd = contract["expected"], contract["protected_sd"]
    require(target["cid"] != sd["cid"] and target["controller"] != sd["controller"], "目標與受保護 SD 重疊")
    digest_record(contract["sd_prefix"], 4 * core.CHUNK)
    require(contract["sd_prefix"]["bytes"] == 4 * core.CHUNK <= sd["bytes"], "SD 前綴必須為 4 MiB")
    rescue = contract["rescue"]
    fields(rescue, "schema kernel identity_sha256")
    core.validate_rescue(rescue["schema"], {key: rescue[key] for key in ("kernel", "identity_sha256")})
    auth = contract["authorization"]
    fields(auth, "userarea_write hardware_id media_identity backup_sha256 record")
    require(auth["userarea_write"] is True and auth["hardware_id"] == contract["hardware_id"]
            and auth["media_identity"] == "cid:" + target["cid"]
            and auth["backup_sha256"] == contract["backup"]["sha256"], "授權未綁定本板、媒體及完整備份")
    identifier(auth["record"])
    checked_bytes(contract["backup"])
    validate_ssh(contract["ssh"], require_hosts=False)
    return contract


def validate_source(source, capacity):
    fields(source, "path compressed raw")
    require(path(source["path"]).suffix == ".xz", "來源須為普通 XZ 檔案")
    digest_record(source["compressed"], safe.MAX_ARTIFACT_BYTES)
    digest_record(source["raw"], capacity)
    require(source["raw"]["bytes"] % 512 == 0, "原始來源不是完整磁區")


def verify_inputs(contract, source, *, timeout=21600, monotonic=time.monotonic):
    """離線完整解壓備份及來源；不開 SSH、UART 或區塊設備。"""
    require(type(timeout) in (int, float) and 0 < timeout <= 86400, "期限無效")
    deadline = monotonic() + timeout
    def check():
        return core.deadline_check(deadline, monotonic)
    validate_contract(contract)
    validate_source(source, contract["expected"]["bytes"])
    proof = core.validate_backup(contract["backup"]["path"], contract["expected"], check)
    require(proof["sha256"] == contract["backup"]["sha256"], "備份清單摘要變動")
    with safe.open_root(path(proof["path"]).parent) as root:
        restored = backup.verify_gzip(root, "emmc-userarea.img.gz", contract["expected"]["bytes"], deadline, monotonic)
    require(all(restored[key] == proof[key] for key in ("compressed", "raw"))
            and restored["file_identity"] == proof["artifact_identity"], "備份解壓結果或檔案身分不符")
    location = path(source["path"])
    with safe.open_root(location.parent) as root, safe.open_file(root, location.name) as stream:
        before = backup.file_identity(os.fstat(stream.fileno()))
        result = core.remote_namespace()["process_xz"](
            stream, {key: source[key] for key in ("raw", "compressed")}, contract["expected"]["bytes"], check)
        core.unchanged(root, location.name, stream, before)
    core.revalidate_backup(proof)
    check()
    return {"backup": proof, "source": result, "source_identity": before, "hardware_validated": False}


def readonly_program():
    return "\n".join(("__name__ = '_bpi_lab_preflight'", "BACKUP_SOURCE = " + repr(backup.REMOTE_SCRIPT),
                       core.REMOTE_CORE, r'''
request = json.loads(sys.argv[1], object_pairs_hook=unique_object)
deadline = time.monotonic() + request["timeout"]
def check(*args):
    require(not args and time.monotonic() < deadline, "唯讀預檢期限已到")
def rescue():
    return rescue_identity(expected_schema=request["rescue_schema"], expected_identity=request["rescue_expected"])
signal.signal(signal.SIGALRM, check)
signal.setitimer(signal.ITIMER_REAL, request["timeout"])
identity = checks["inspect"](request["expected"])
ram = rescue()
sd = inspect_sd(request["protected_sd"])
require(sd["bytes"] == request["sd_bytes"] and sd["devnum"] != identity["devnum"], "SD 容量或身分不符")
fd = os.open(sd["device"], os.O_RDONLY | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    before = sd_evidence(fd, request["protected_sd"], check)
    require(before["identity"] == sd and before["prefix"] == request["pinned_preflight"]["sd_prefix"], "SD 固定身分不符")
    require(checks["inspect"](request["expected"]) == identity and rescue() == ram, "唯讀預檢期間身分變更")
finally:
    os.close(fd)
check()
state = {"range": {"start": 0, "end_exclusive": request["source"]["raw"]["bytes"]},
         "bootable": False, "boot_selected": False, "boot_verified": False,
         "identity": identity, "rescue": ram, "sd_before": before,
         "bytes_written": 0, "attempted_end": 0}
print(json.dumps({"nonce": request["nonce"], "state": state}))
'''))


def pins(contract, ssh):
    return {"backup_manifest_sha256": contract["backup"]["sha256"], "ssh_config_sha256": ssh["sha256"],
            "sd_prefix": contract["sd_prefix"],
            "rescue": {key: contract["rescue"][key] for key in ("kernel", "identity_sha256")}}


def preflight(contract, source, output, *, timeout=21600, transport=None, monotonic=time.monotonic):
    """完整離線檢查後執行固定的遠端唯讀預檢；沒有寫入描述符。"""
    deadline = monotonic() + timeout
    def check():
        return core.deadline_check(deadline, monotonic)
    proof = verify_inputs(contract, source, timeout=check(), monotonic=monotonic)
    output = new_directory(output)
    ssh = snapshot_ssh(contract["ssh"], output)
    request = {"nonce": secrets.token_hex(32), "expected": contract["expected"],
               "protected_sd": {key: contract["protected_sd"][key] for key in ("cid", "controller")},
               "sd_bytes": contract["protected_sd"]["bytes"], "source": proof["source"],
               "rescue_schema": contract["rescue"]["schema"], "rescue_expected": pins(contract, ssh)["rescue"],
               "backup_manifest_sha256": contract["backup"]["sha256"], "pinned_preflight": pins(contract, ssh),
               "timeout": check()}
    argv = backup.ssh_command(ssh["path"], "bpi-lab", {})
    program = readonly_program()
    argv[-1] = shlex.join(["python3", "-B", "-c", program, json.dumps(request)])
    streams, code = {"stdout": bytearray(), "stderr": bytearray()}, None
    with closing((transport or backup.ssh_stream)(argv, deadline, monotonic)) as events:
        for kind, data in events:
            check()
            require(code is None, "SSH 退出後仍有事件")
            if kind == "exit":
                require(type(data) is int, "退出碼格式不符")
                code = data
            else:
                require(kind in streams and type(data) is bytes and len(streams[kind]) + len(data) <= 65536,
                        "唯讀預檢輸出格式或上限不符")
                streams[kind].extend(data)
    # 不發布可能含敏感診斷的 stderr；只保存固定結構的成功證據。
    require(code == 0, "唯讀預檢 SSH 未成功")
    record = safe.parse_manifest(bytes(streams["stdout"]))
    fields(record, "nonce state")
    require(record["nonce"] == request["nonce"], "唯讀預檢識別碼不符")
    core.validate_state(record["state"], request)
    require(record["state"]["bytes_written"] == record["state"]["attempted_end"] == 0,
            "唯讀預檢不得包含寫入進度")
    require(record["state"]["sd_before"]["identity"]["bytes"] == request["sd_bytes"], "SD 容量不符")
    core.revalidate_backup(proof["backup"])
    source_path = path(source["path"])
    with safe.open_root(source_path.parent) as root, safe.open_file(root, source_path.name) as stream:
        core.unchanged(root, source_path.name, stream, proof["source_identity"])
    checked_bytes(ssh)
    check()
    result = {"schema": "bpi-lab-deploy-preflight-v1", "status": "verified", "request": request,
              "state": record["state"], "inputs": proof, "program_sha256": hashlib.sha256(program.encode()).hexdigest()}
    save(output, "preflight.json", encode(result))
    return result


def deploy(contract, source, output, *, confirm_overwrite=False, timeout=21600,
           transport=None, preflight_transport=None, monotonic=time.monotonic):
    """必須明確確認；每次覆寫均重做完整前檢，不沿用舊階段的通過狀態。"""
    require(confirm_overwrite is True, "沒有明確覆寫確認；不開 SSH")
    deadline = monotonic() + timeout
    def check():
        return core.deadline_check(deadline, monotonic)
    validate_contract(contract)
    output = new_directory(output)
    preflight(contract, source, output / "preflight", timeout=check(),
              transport=preflight_transport, monotonic=monotonic)
    ssh = snapshot_ssh(contract["ssh"], output)
    target, sd = contract["expected"], contract["protected_sd"]
    result = core.deploy(
        confirm_overwrite=True, backup_manifest=contract["backup"]["path"], source=source["path"],
        compressed_sha256=source["compressed"]["sha256"], raw_sha256=source["raw"]["sha256"],
        raw_size=source["raw"]["bytes"], expected_cid=target["cid"], expected_size=target["bytes"],
        expected_controller=target["controller"], protected_sd_cid=sd["cid"], protected_sd_controller=sd["controller"],
        ssh_config=ssh["path"], alias="bpi-lab", output_dir=output / "write", timeout=check(),
        transport=transport or core.upload_stream, monotonic=monotonic, pinned_preflight=pins(contract, ssh),
        rescue_schema=contract["rescue"]["schema"], rescue_expected=pins(contract, ssh)["rescue"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="共用受限 RAM 救援部署；不自動取得硬體授權")
    parser.add_argument("action", choices=("verify", "preflight", "deploy"))
    parser.add_argument("--contract", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output")
    parser.add_argument("--confirm-overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        contract = load({"path": args.contract, "sha256": args.contract_sha256})
        source = load({"path": args.source, "sha256": args.source_sha256})
        if args.action == "verify":
            result = verify_inputs(contract, source)
        else:
            require(args.output is not None, "遠端操作必須指定新的證據目錄")
            result = (deploy(contract, source, args.output, confirm_overwrite=args.confirm_overwrite)
                      if args.action == "deploy" else preflight(contract, source, args.output))
        print(json.dumps({"status": "verified", "result": result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
