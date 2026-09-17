#!/usr/bin/env python3
"""0845 的階段適配器；接入真實部署與短測，不推論其他 H618 板的資格。

目前接入 preflight、deploy、smoke；boot、recovery 及中途續作明確阻擋。
尚不可啟用整套自動佇列。既有 SRAM／電源原型須另完成契約化與實板核定。
設定及來源相依項均固定摘要，但不是對惡意本機程式的執行沙箱。
"""

from contextlib import closing, ExitStack
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
    from . import bpi_h618_customer_boot as boot
    from . import bpi_h618_customer_smoke as smoke
    from . import bpi_h618_emmc_deploy as deploy
    from . import bpi_lab_station as station
else:
    import bpi_h618_artifacts as safe
    import bpi_h618_customer_boot as boot
    import bpi_h618_customer_smoke as smoke
    import bpi_h618_emmc_deploy as deploy
    import bpi_lab_station as station

backup = deploy.backup
require = safe.require
IMPLEMENTED = ("preflight", "deploy", "smoke")
DEPENDENCIES = (
    "bpi_lab_h618.py", "bpi_lab_station.py", "bpi_h618_artifacts.py",
    "bpi_h618_emmc_backup.py", "bpi_h618_emmc_deploy.py",
    "bpi_h618_customer_boot.py", "bpi_h618_customer_smoke.py",
    "bpi_h618_rescue_boot.py", "bpi_lab_console.py", "bpi_sram_lab_uart.py",
    "bpi_sram_uart.py", "bpi_sram_package.py", "bpi_sram_ddr_package.py", "bpi_sram_lab_package.py",
)


def checked_file(reference, limit=65536):
    require(type(reference) is dict and set(reference) == {"path", "sha256"}, "檔案參照欄位不符")
    path = Path(reference["path"])
    require(path.is_absolute() and ".." not in path.parts
            and deploy.hash_value(reference["sha256"]), "檔案參照不是絕對路徑及固定摘要")
    with safe.open_root(path.parent) as directory:
        digest, blob = safe.fingerprint(directory, path.name, limit=limit, keep=True)
    require(digest["sha256"] == reference["sha256"], "檔案摘要不符：" + path.name)
    return blob


def check_config(config):
    required = {"schema", "station_id", "hardware_id", "test_version", "dependencies",
                "backup", "authorization", "sd_prefix", "rescue_identity_sha256",
                "rescue_ssh", "customer_ssh", "components", "output_root", "timeout_seconds"}
    require(type(config) is dict and set(config) == required, "H618 設定欄位不完整或含未知欄位")
    require(config["schema"] == "bpi-lab-h618-v1", "H618 設定版本不符")
    for key in ("station_id", "hardware_id", "test_version"):
        require(type(config[key]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", config[key]),
                "設定識別無效：" + key)
    require(config["hardware_id"] == "bpi-m4zero-0845", "本適配器僅接入既有 0845 原型")
    require(type(config["timeout_seconds"]) is int and 60 <= config["timeout_seconds"] <= 86400,
            "總期限須為 60..86400 秒整數")
    path = Path(config["output_root"])
    require(path.is_absolute() and ".." not in path.parts, "證據根目錄須為無上層跳轉的絕對路徑")
    prefix = config["sd_prefix"]
    require(type(prefix) is dict and set(prefix) == {"bytes", "sha256"}
            and type(prefix["bytes"]) is int and prefix["bytes"] == 4194304
            and deploy.hash_value(prefix["sha256"]), "SD 保護前綴未固定")
    require(deploy.hash_value(config["rescue_identity_sha256"]), "救援身分檔摘要未固定")
    auth = config["authorization"]
    require(type(auth) is dict and set(auth) == {"userarea_write", "hardware_id", "media_identity", "record"}
            and auth["userarea_write"] is True and auth["hardware_id"] == config["hardware_id"]
            and auth["media_identity"] == "cid:" + boot.EXPECTED["cid"]
            and type(auth["record"]) is str and 0 < len(auth["record"].strip()) <= 4096,
            "覆寫授權未綁定 0845 及既有 eMMC；不能借用其他板的授權")
    deps = config["dependencies"]
    require(type(deps) is dict and set(deps) == set(DEPENDENCIES), "程式相依項未完整固定")
    for name, expected in deps.items():
        checked_file({"path": str(Path(__file__).absolute().parent / name), "sha256": expected}, 1024**2)
    checked_file(config["backup"])
    for key in ("rescue_ssh", "customer_ssh"):
        value = config[key]
        require(type(value) is dict and set(value) == {"config", "alias"}, "SSH 設定欄位不符")
        checked_file(value["config"])
        backup.ssh_command(value["config"]["path"], value["alias"], {})
    components = config["components"]
    require(type(components) is dict and 1 <= len(components) <= 10
            and all(deploy.hash_value(key) for key in components), "組件索引不是 1..10 個 XZ 摘要")
    for reference in components.values():
        checked_file(reference)
    return config


def load_config(path, sha256):
    return check_config(safe.parse_manifest(checked_file({"path": str(path), "sha256": sha256})))


def selected_components(config, request):
    image = request["image"]
    require(image["board"] == "bpi-m4z-emac", "目前只接入既有 M4 Zero EMAC 客戶組件格式")
    reference = config["components"].get(request["image_sha256"])
    require(reference is not None, "本次映像沒有固定的原配組件清單")
    document, _ = smoke.load_components(reference["path"], reference["sha256"])
    source = document.get("image", {})
    require(source.get("compressed", {}).get("sha256") == request["image_sha256"], "組件的 XZ 摘要不符")
    root = Path(request["image_root"])
    relative = image.get("relative_path")
    require(root.is_absolute() and ".." not in root.parts, "映像根目錄無效")
    safe.relative_parts(relative)
    require(source.get("path") == str(root / relative)
            and image.get("compressed_bytes") == source.get("compressed", {}).get("bytes"),
            "映像路徑或壓縮長度與原配清單不符")
    require(document.get("os") == image.get("release") and document.get("desktop") == image.get("variant"),
            "映像 OS 或角色與原配清單不符")
    for key in ("raw", "compressed"):
        boot.digest_record(source.get(key), boot.EXPECTED["bytes"])
    require(source["raw"]["bytes"] % 512 == 0, "原始映像不是完整磁區")
    return document, reference


def readonly_program():
    # 使用已測的媒體身分檢查函式；不呼叫其中任何寫入或部署入口。
    return "\n".join((
        "__name__ = '_bpi_readonly_preflight'",
        "BACKUP_SOURCE = " + repr(backup.REMOTE_SCRIPT),
        deploy.REMOTE_CORE,
        r'''
request = json.loads(sys.argv[1], object_pairs_hook=unique_object)
deadline = time.monotonic() + request["timeout"]
def check():
    require(time.monotonic() < deadline, "唯讀前置核對逾時")
rescue = rescue_identity()
identity = checks["inspect"](request["expected"])
sd = inspect_sd(request["protected_sd"])
fd = os.open(sd["device"], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    before = sd_evidence(fd, request["protected_sd"], check)
finally:
    os.close(fd)
state = {"range": {"start": 0, "end_exclusive": request["source"]["raw"]["bytes"]},
         "bootable": False, "boot_selected": False, "boot_verified": False,
         "identity": identity, "rescue": rescue, "sd_before": before,
         "bytes_written": 0, "attempted_end": 0}
print(json.dumps({"nonce": request["nonce"], "state": state}))
'''))


def readonly_preflight(config, document, output, check, transport=None):
    ssh = config["rescue_ssh"]
    request = {"nonce": secrets.token_hex(32), "expected": boot.EXPECTED,
               "protected_sd": boot.PROTECTED_SD,
               "source": {key: document["image"][key] for key in ("raw", "compressed")},
               "timeout": check()}
    argv = backup.ssh_command(ssh["config"]["path"], ssh["alias"], {})
    program = readonly_program()
    argv[-1] = shlex.join(["python3", "-B", "-c", program, json.dumps(request)])
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    code = None
    with ExitStack() as stack:
        directory = stack.enter_context(safe.open_root(output))
        streams = {key: stack.enter_context(safe.open_file(directory, "ssh-" + key + ".log", create=True))
                   for key in captured}
        events = stack.enter_context(closing((transport or backup.ssh_stream)(
            argv, time.monotonic() + check(), time.monotonic)))
        for kind, value in events:
            check()
            require(code is None, "SSH 退出後仍有事件")
            if kind == "exit":
                require(type(value) is int, "SSH 退出碼無效")
                code = value
            else:
                require(kind in captured and isinstance(value, bytes), "SSH 證據格式不符")
                remaining = 65536 - len(captured[kind])
                streams[kind].write(value[:remaining])
                streams[kind].flush()
                os.fsync(streams[kind].fileno())
                require(len(value) <= remaining, "唯讀證據超界")
                captured[kind].extend(value)
    require(code == 0, "唯讀 SSH 核對未成功")
    record = safe.parse_manifest(bytes(captured["stdout"]))
    require(type(record) is dict and record.get("nonce") == request["nonce"], "唯讀核對識別碼不符")
    deploy.validate_state(record.get("state"), request)
    state = record["state"]
    require(state["sd_before"]["prefix"] == config["sd_prefix"], "受保護 SD 前綴與核定值不同")
    require(state["rescue"].get("kernel") == boot.rescue.KERNEL
            and state["rescue"].get("identity_sha256") == config["rescue_identity_sha256"],
            "不是已核定的救援核心及身分檔")
    checked_file(ssh["config"])
    return {"remote": record, "program_sha256": hashlib.sha256(program.encode()).hexdigest()}


def save_bytes(output, name, blob):
    with safe.open_root(output) as directory, safe.open_file(directory, name, create=True) as stream:
        stream.write(blob)


def save_json(output, name, data):
    save_bytes(output, name, (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def run_stage(config_path, config_sha256, request):
    """stdout 由 CLI 統一輸出契約；原始證據另存，未接入階段一律阻擋。"""
    station._validate_request(request)
    report = {"schema": "bpi-lab-stage-v1", **{key: request[key] for key in station.BINDINGS},
              "status": "blocked", "hardware_validated": False,
              "original_boot_chain_verified": False, "whole_adapter_ready": False}
    output = None
    try:
        require(request["mode"] == "hardware", "此為真實 H618 適配器，不接受模擬模式")
        require(request["boot_config_sha256"] == config_sha256, "工作未綁定本次設定摘要")
        config = load_config(config_path, config_sha256)
        for key in ("station_id", "hardware_id", "test_version"):
            require(request[key] == config[key], "工作與設定不符：" + key)
        require(re.fullmatch(r"[0-9a-f]{64}", request["work_key"])
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request["attempt_id"]),
                "工作鍵或嘗試名稱不符")
        if request["stage"] not in IMPLEMENTED or "resume" in request:
            report["reason"] = "此版未接入電源／UART 引導、故障返回或續作狀態核對；禁止假通過"
            return station.validate_report(report, request)
        document, reference = selected_components(config, request)
        name = request["work_key"] + "-" + request["attempt_id"] + "-" + request["stage"]
        candidate = Path(config["output_root"]) / name
        with safe.open_root(candidate.parent) as directory:
            os.mkdir(name, 0o700, dir_fd=directory)
            os.fsync(directory)
        output = candidate
        save_json(output, "request.json", request)
        report["evidence_path"] = str(output)
        deadline = time.monotonic() + config["timeout_seconds"]
        def check():
            return deploy.deadline_check(deadline, time.monotonic)
        if request["stage"] in ("preflight", "deploy"):
            # 部署前重新核對，不把舊 preflight 視為目前仍在救援的證明。
            proof = deploy.validate_backup(config["backup"]["path"], boot.EXPECTED, check)
            require(proof["sha256"] == config["backup"]["sha256"], "備份清單變更")
            observed = readonly_preflight(config, document, output, check)
            save_json(output, "preflight.json", {"backup": proof, **observed})
            report.update(media_identity_verified=True, backup_verified=True)
        if request["stage"] == "deploy":
            ssh = config["rescue_ssh"]
            source = document["image"]
            receipt = deploy.deploy(confirm_overwrite=True,
                backup_manifest=config["backup"]["path"], source=source["path"],
                compressed_sha256=request["image_sha256"], raw_sha256=source["raw"]["sha256"],
                raw_size=source["raw"]["bytes"], expected_cid=boot.EXPECTED["cid"],
                expected_size=boot.EXPECTED["bytes"], expected_controller=boot.EXPECTED["controller"],
                protected_sd_cid=boot.PROTECTED_SD["cid"], protected_sd_controller=boot.PROTECTED_SD["controller"],
                ssh_config=ssh["config"]["path"], alias=ssh["alias"], output_dir=output / "deploy", timeout=check(),
                pinned_preflight={"backup_manifest_sha256": config["backup"]["sha256"],
                    "ssh_config_sha256": ssh["config"]["sha256"], "sd_prefix": config["sd_prefix"],
                    "rescue": {"kernel": boot.rescue.KERNEL,
                               "identity_sha256": config["rescue_identity_sha256"]}})
            boot.validate_metadata(document, receipt)
            require(receipt["request"]["backup_manifest_sha256"] == config["backup"]["sha256"]
                    and receipt["remote_state"]["sd_before"]["prefix"] == config["sd_prefix"],
                    "部署收據與本次固定備份或 SD 證據不符")
            report.update(full_readback_verified=True, compressed_sha256_verified=True)
        elif request["stage"] == "smoke":
            ssh = config["customer_ssh"]
            observed = smoke.smoke(ssh_config=ssh["config"]["path"], alias=ssh["alias"],
                components=reference["path"], components_sha256=reference["sha256"],
                output=output / "smoke", duration=min(60, int(check())), file_mib=16)
            require(observed.get("ok") is True, "真實短測未全部通過；保留原失敗／缺測結果")
            smoke.validate_result(observed["remote"], observed["request"])
            checked_file(ssh["config"])
            report["checks"] = {key: True for key in ("root_identity", "cpu", "file_readback", "failed_services")}
        check()
        report.update(status="passed", hardware_validated=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report.update(status="failed" if output else "blocked", hardware_validated=False, reason=str(exc))
    station.validate_report(report, request)
    if output is not None:
        save_json(output, "stage.json", report)
    return report


def main(argv=None):
    parser = safe.JsonArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="固定的本機私有設定")
    parser.add_argument("--config-sha256", required=True, help="外部提供的設定摘要")
    parser.add_argument("--inspect-config", action="store_true", help="只核對本機相依檔，不操作 SSH 或硬體")
    args = parser.parse_args(argv)
    try:
        if args.inspect_config:
            config = load_config(args.config, args.config_sha256)
            result = {"ok": True, "implemented_stages": IMPLEMENTED, "whole_adapter_ready": False,
                      "hardware_validated": False, "image_count": len(config["components"])}
        else:
            raw = sys.stdin.buffer.read(1024**2 + 1)
            require(len(raw) <= 1024**2, "請求超界")
            request = station._json_loads(raw)
            result = run_stage(args.config, args.config_sha256, request)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
