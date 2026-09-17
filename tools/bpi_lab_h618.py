#!/usr/bin/env python3
"""0845 的階段適配器；接入真實部署與短測，不推論其他 H618 板的資格。

五階段接線包含首次登入與同次 UART／SSH 重綁定；實板資格仍須另行核定。
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
    from . import bpi_lab_h618_lifecycle as life
    from . import bpi_lab_h618_session as session
else:
    import bpi_h618_artifacts as safe
    import bpi_h618_customer_boot as boot
    import bpi_h618_customer_smoke as smoke
    import bpi_h618_emmc_deploy as deploy
    import bpi_lab_station as station
    import bpi_lab_h618_lifecycle as life
    import bpi_lab_h618_session as session

backup = deploy.backup
require = safe.require
IMPLEMENTED = station.STAGES
STATE_FILE = "h618-0845-stage-state.json"
PUBLICATION_FILE = "h618-0845-stage-publication.pending"
DEPENDENCIES = (
    "bpi_lab_h618.py", "bpi_lab_station.py", "bpi_h618_artifacts.py",
    "bpi_h618_emmc_backup.py", "bpi_h618_emmc_deploy.py",
    "bpi_h618_customer_boot.py", "bpi_h618_customer_smoke.py",
    "bpi_h618_rescue_boot.py", "bpi_lab_console.py", "bpi_sram_lab_uart.py",
    "bpi_sram_uart.py", "bpi_sram_package.py", "bpi_sram_ddr_package.py", "bpi_sram_lab_package.py",
    "bpi_lab_h618_lifecycle.py", "bpi_lab_h618_session.py", "bpi_lab_network.py", "bpi_lab_linux.py",
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
    require(type(config) is dict, "H618 設定必須為物件")
    version2 = config.get("schema") == "bpi-lab-h618-v2"
    if version2:
        required = required - {"rescue_ssh", "customer_ssh"} | {"lifecycle", "session"}
    require(set(config) == required, "H618 設定欄位不完整或含未知欄位")
    require(config["schema"] in ("bpi-lab-h618-v1", "bpi-lab-h618-v2"), "H618 設定版本不符")
    for key in ("station_id", "hardware_id", "test_version"):
        require(type(config[key]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", config[key]),
                "設定識別無效：" + key)
    require(config["hardware_id"] == "bpi-m4zero-0845", "本適配器僅接入既有 0845 原型")
    require(type(config["timeout_seconds"]) is int and 60 <= config["timeout_seconds"] <= 86400,
            "總期限須為 60..86400 秒整數")
    path = Path(config["output_root"])
    require(path.is_absolute() and ".." not in path.parts, "證據根目錄須為無上層跳轉的絕對路徑")
    with safe.open_root(path) as directory:
        info = os.fstat(directory)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, "證據根目錄須為本使用者私有")
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
    for key in (() if version2 else ("rescue_ssh", "customer_ssh")):
        value = config[key]
        require(type(value) is dict and set(value) == {"config", "alias"}, "SSH 設定欄位不符")
        checked_file(value["config"])
        backup.ssh_command(value["config"]["path"], value["alias"], {})
    if version2:
        reference = config["lifecycle"]
        lifecycle, _, blob, _, _ = life.load_config(reference["path"], reference["sha256"])
        require(lifecycle["schema"] == "bpi-lab-h618-lifecycle-v2"
                and lifecycle["station_id"] == config["station_id"]
                and lifecycle["rescue_identity_sha256"] == config["rescue_identity_sha256"]
                and safe.parse_manifest(blob)["sd_prefix"] == config["sd_prefix"], "生命週期與 stage 設定不符")
        session.check_config(safe.parse_manifest(checked_file(config["session"])))
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
def no_media_writers():
    pids = list(Path('/proc').glob('[0-9]*'))
    require(len(pids) <= 4096, "程序盤點超界")
    for pid in pids:
        check()
        try:
            entries = list((pid/'fd').iterdir())
            require(len(entries) <= 4096, "描述符盤點超界")
            for entry in entries:
                try:
                    info = entry.stat()
                    if stat.S_ISBLK(info.st_mode) and os.major(info.st_rdev) == 179:
                        lines = (pid/'fdinfo'/entry.name).read_text().splitlines()
                        flags = [int(line.split()[1], 8) for line in lines if line.startswith('flags:')]
                        require(len(flags) == 1 and flags[0] & os.O_ACCMODE == os.O_RDONLY,
                                "媒體仍有可寫描述符，不能視為靜止救援")
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
no_media_writers()
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
if request.get("verify_deployed"):
    fd = os.open(identity["device"], os.O_RDONLY | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        checks["check_fd"](fd, identity)
        state["resume_readback"] = hash_range(fd, request["source"]["raw"]["bytes"], check)
        require(state["resume_readback"] == request["source"]["raw"], "續作完整部署範圍回讀不符")
        require(checks["inspect"](request["expected"]) == identity, "續作回讀期間媒體身分變動")
    finally:
        os.close(fd)
no_media_writers()
print(json.dumps({"nonce": request["nonce"], "state": state}))
'''))


def readonly_preflight(config, document, output, check, transport=None, *, verify_deployed=False):
    ssh = config["rescue_ssh"]
    request = {"nonce": secrets.token_hex(32), "expected": boot.EXPECTED,
               "protected_sd": boot.PROTECTED_SD,
               "source": {key: document["image"][key] for key in ("raw", "compressed")},
               "timeout": check(), "verify_deployed": verify_deployed}
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
    if verify_deployed:
        require(state.get("resume_readback") == document["image"]["raw"], "續作缺少即時完整回讀")
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


def _data_stage(config, document, reference, request, output, check, report):
    """沿用原部署與短測；SSH 只能來自呼叫端剛核對的 UART session。"""
    if request["stage"] in ("preflight", "deploy"):
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
                "rescue": {"kernel": boot.rescue.KERNEL, "identity_sha256": config["rescue_identity_sha256"]}})
        boot.validate_metadata(document, receipt)
        require(receipt["request"]["backup_manifest_sha256"] == config["backup"]["sha256"]
                and receipt["remote_state"]["sd_before"]["prefix"] == config["sd_prefix"],
                "部署收據與本次固定備份或 SD 證據不符")
        report.update(full_readback_verified=True, compressed_sha256_verified=True,
                      deploy_receipt=session.reference(output / "deploy" / "receipt.json"))
    elif request["stage"] == "smoke":
        ssh = config["customer_ssh"]
        observed = smoke.smoke(ssh_config=ssh["config"]["path"], alias=ssh["alias"],
            components=reference["path"], components_sha256=reference["sha256"],
            output=output / "smoke", duration=min(60, int(check())), file_mib=16)
        require(observed.get("ok") is True, "真實短測未全部通過；保留原失敗／缺測結果")
        smoke.validate_result(observed["remote"], observed["request"])
        checked_file(ssh["config"])
        report["checks"] = {key: True for key in ("root_identity", "cpu", "file_readback", "failed_services")}


def _read_state(root):
    with safe.open_root(root) as directory:
        try:
            os.stat(PUBLICATION_FILE, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("stage 最終發布未確認；保持持久隔離")
        try:
            _, blob = safe.fingerprint(directory, STATE_FILE, limit=65536, keep=True)
        except FileNotFoundError:
            return None
    return safe.parse_manifest(blob)


def _write_state(root, state):
    save_json(root, STATE_FILE + ".new", state)
    with safe.open_root(root) as directory:
        os.replace(STATE_FILE + ".new", STATE_FILE, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)


def _publish(root, output, state, report, request, deadline):
    def check():
        if report["status"] == "passed":
            deadline.remaining()
    station.validate_report(report, request)
    check()
    save_json(root, PUBLICATION_FILE, {"binding": state["binding"], "output": str(output)})
    check()
    save_json(output, "stage.json", report)
    check()
    reference = session.reference(output / "stage.json")
    check()
    state["report"] = reference
    state["status"] = report["status"]
    if report["status"] == "passed" and "resume" not in request:
        state["completed"][request["stage"]] = reference
        state["next_stage"] = (IMPLEMENTED[IMPLEMENTED.index(request["stage"]) + 1]
                               if request["stage"] != "recovery" else None)
    _write_state(root, state)
    check()
    with safe.open_root(root) as directory:
        check()
        os.unlink(PUBLICATION_FILE, dir_fd=directory)


def _previous(state, request, stage):
    require(state is not None and state.get("binding") == session.binding(request), "缺少同次 stage 租約")
    reference = state.get("completed", {}).get(stage)
    require(reference is not None, "缺少同次已發布階段：" + stage)
    report = safe.parse_manifest(checked_file(reference))
    station.validate_report(report, {key: value for key, value in {**request, "stage": stage}.items()
                                     if key != "resume"})
    require(report["status"] == "passed" and session.binding(report) == state["binding"], "舊階段未通過或跨嘗試")
    return report


def _resume_references(state, request):
    resume = request["resume"]
    require(state is not None and state.get("binding") == session.binding(request)
            and state.get("status") == "passed" and state.get("next_stage") == resume["next_stage"],
            "目前租約不支持續作；中斷中的階段只能先恢復，不得直接續寫")
    found = {}
    for reference in resume["previous_reports"]:
        report = safe.parse_manifest(checked_file({key: reference[key] for key in ("path", "sha256")}))
        expected = {key: value for key, value in {**request, "stage": reference["stage"]}.items() if key != "resume"}
        station.validate_report(report, expected)
        require(report["status"] == "passed", "續作歷史含未通過階段")
        if "resume_next_stage" not in report:
            require(reference["stage"] not in found, "續作歷史重複階段")
            found[reference["stage"]] = report
    needed = IMPLEMENTED[:IMPLEMENTED.index(resume["next_stage"])]
    require(set(found) == set(needed), "續作歷史缺少階段或含未完成的未來階段")
    for stage in needed:
        require(found[stage] == _previous(state, request, stage), "佇列與適配器同次歷史不一致")


def _lifecycle_session(state, bound):
    """只用完整 lifecycle 發布結果決定返回來源，不將它補算成遺失的 stage 成功。"""
    require(state.get("binding") == bound and state.get("simulated") is False,
            "生命週期租約不屬於同次真實工作")
    record = safe.parse_manifest(checked_file({"path": state["report_path"], "sha256": state["report_sha256"]}))
    require(record.get("schema") == "bpi-lab-h618-lifecycle-result-v1" and record.get("ok") is True
            and record.get("binding") == bound and record.get("simulated") is False
            and record.get("config_sha256") == state["config_sha256"]
            and record.get("session_id") == state["session_id"] and record.get("strict_ssh_verified") is True,
            "生命週期成功證據錯配或未完成 SSH 核對")
    result = record.get("session", {})
    require(result.get("binding") == bound and result.get("mode") == state["status"],
            "生命週期 session 來源不符")
    return result


def run_stage(config_path, config_sha256, request):
    """完整 stage 契約；所有成功均須持久化，未知發布結果保持隔離。"""
    station._validate_request(request)
    report = {"schema": "bpi-lab-stage-v1", **{key: request[key] for key in station.BINDINGS},
              "status": "blocked", "hardware_validated": False, "whole_adapter_ready": False,
              "original_boot_chain_verified": False, "hardware_qualification": "awaiting_hardware",
              "needs_recovery": False}
    output = None
    needs_recovery = False
    runtime = life.NativeRuntime()
    started = runtime.clock()
    try:
        require(request["mode"] == "hardware", "真實入口不接受合成模式")
        require(request["boot_config_sha256"] == config_sha256, "工作未綁定本次設定摘要")
        config = load_config(config_path, config_sha256)
        require(config["schema"] == "bpi-lab-h618-v2", "v1 只能離線檢查；執行須遷移至同次綁定的 v2")
        for key in ("station_id", "hardware_id", "test_version"):
            require(request[key] == config[key], "工作與設定不符：" + key)
        require(re.fullmatch(r"[0-9a-f]{64}", request["work_key"])
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request["attempt_id"]), "工作鍵或嘗試名稱無效")
        document, reference = selected_components(config, request)
        lifecycle_ref = config["lifecycle"]
        lifecycle, _, _, _, _ = life.load_config(lifecycle_ref["path"], lifecycle_ref["sha256"])
        login_config = session.check_config(safe.parse_manifest(checked_file(config["session"])))
        deadline = life.Deadline(config["timeout_seconds"], runtime.clock, runtime.sleep)
        deadline.end = started + config["timeout_seconds"]
        deadline.remaining()
        bound = session.binding(request)
        name = request["work_key"] + "-" + request["attempt_id"] + "-" + request["stage"] + "-" + secrets.token_hex(8)
        output = life._new_directory(Path(config["output_root"]) / name)
        save_json(output, "request.json", request)
        report["evidence_path"] = str(output)
        with life.resource_lock(lifecycle, life.LOCK_ROOT, bound) as guard:
            # pending 會令狀態讀取直接拒絕；先保留救援需求，但不讀取或解除其內容。
            with safe.open_root(life.LOCK_ROOT) as directory:
                for name in (PUBLICATION_FILE, life.PUBLICATION_FILE):
                    try:
                        os.stat(name, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        needs_recovery = True
            state = _read_state(life.LOCK_ROOT)
            needs_recovery = needs_recovery or (state is not None and state.get("binding") == bound
                and not (state.get("status") == "passed" and state.get("next_stage") is None))
            lifecycle_state = life._read_state(life.LOCK_ROOT)
            needs_recovery = needs_recovery or (lifecycle_state is not None
                and lifecycle_state.get("binding") == bound and lifecycle_state.get("status") != "rescue")
            stage = request["stage"]
            if "resume" in request:
                _resume_references(state, request)
            elif stage == "preflight":
                require(state is None or state.get("status") == "passed" and state.get("next_stage") is None,
                        "前次工作未完整返回救援；不得開始新映像")
                require(state is None or state.get("binding") != bound, "已結束的 attempt 不得重新使用")
                require(lifecycle_state is None or lifecycle_state.get("status") == "rescue",
                        "生命週期尚未返回救援")
                state = {"binding": bound, "completed": {}, "next_stage": "preflight"}
            else:
                require(state is not None and state.get("binding") == bound, "禁止跨工作或跨嘗試接續")
                require(stage == "recovery" or state.get("status") == "passed" and state.get("next_stage") == stage,
                        "階段順序不符或上次執行未完成")
            previous_inflight = state.get("inflight")
            previous_status = state.get("status")
            if stage == "recovery" and previous_inflight == "deploy" and previous_status == "running":
                raise ValueError("部署遭不可捕捉中斷，遠端寫入是否停止未知；保持隔離，須人工核對")
            state = {**state, "status": "running", "inflight": stage, "output": str(output)}
            try:
                _write_state(life.LOCK_ROOT, state)
            except (Exception, KeyboardInterrupt):
                # replace 後的 fsync 也可能失敗；只有確定未建立本次狀態才能免除救援。
                needs_recovery = True
                persisted = _read_state(life.LOCK_ROOT)
                needs_recovery = persisted is not None and persisted.get("binding") == bound
                raise
            needs_recovery = True
            try:
                def establish(console, mode, target, budget, at_login, **kwargs):
                    return session.establish(console, mode, target, budget, at_login,
                        config=login_config, components=document, request=request,
                        rescue_sha=config["rescue_identity_sha256"], **kwargs)

                def current(mode):
                    runtime.validate_uart(lifecycle["uart"])
                    with runtime.console(lifecycle["uart"], output, deadline) as raw:
                        console = life.BoundedConsole(raw, deadline)
                        if mode == "rescue":
                            life._rescue_identity(console, lifecycle)
                            life._sd_prefix(console, config["sd_prefix"])
                        old = state.get("session")
                        require(old is None or old.get("binding") == bound, "SSH session 不屬於同次嘗試")
                        observed = establish(console, mode, output, deadline, False,
                            expected_boot_id=old["boot_id"] if old and old["mode"] == mode else None,
                            initialize_network=not old or old["mode"] != mode)
                        require(observed.get("binding") == bound and observed.get("mode") == mode
                                and observed.get("strict_ssh_verified") is True, "即時 session 綁定或 SSH 未通過")
                        return observed

                def lifecycle_run(action, receipt=None, fault=None):
                    observed = life.run(config_path=lifecycle_ref["path"], config_sha256=lifecycle_ref["sha256"],
                        action=action, session_id=hashlib.sha256(station._json_bytes(bound)).hexdigest(),
                        output=output / "lifecycle", components_reference=reference, receipt_reference=receipt,
                        binding=bound, guard=guard, establish=establish, deadline=deadline, fault_evidence=fault)
                    require(observed.get("ok") is True and observed.get("strict_ssh_verified") is True
                            and observed.get("binding") == bound, "生命週期未完整完成本次登入與嚴格 SSH 核對")
                    report["lifecycle"] = session.reference(output / "lifecycle" / "report.json")
                    return observed["session"]

                wanted = request.get("resume", {}).get("next_stage", stage)
                if "resume" in request:
                    mode = "customer" if wanted in ("smoke", "recovery") else "rescue"
                    if mode == "customer":
                        _previous(state, request, "boot")
                    state["session"] = current(mode)
                    proof = deploy.validate_backup(config["backup"]["path"], boot.EXPECTED, deadline.remaining)
                    require(proof["sha256"] == config["backup"]["sha256"], "續作備份摘要不符")
                    if mode == "rescue":
                        observed = readonly_preflight({**config, "rescue_ssh": state["session"]["ssh"]},
                                                     document, output, deadline.remaining,
                                                     verify_deployed=wanted == "boot")
                        save_json(output, "preflight.json", {"backup": proof, **observed})
                    session.recheck(state["session"], request, deadline)
                    report.update(resume_state_verified=True, resume_next_stage=wanted,
                                  media_identity_verified=True, backup_verified=True)
                elif stage in ("preflight", "deploy", "smoke"):
                    mode = "customer" if stage == "smoke" else "rescue"
                    if stage == "smoke":
                        _previous(state, request, "boot")
                    state["session"] = current(mode)
                    _data_stage({**config, mode + "_ssh": state["session"]["ssh"]}, document, reference,
                                request, output, deadline.remaining, report)
                    session.recheck(state["session"], request, deadline)
                elif stage == "boot":
                    previous = _previous(state, request, "deploy")
                    receipt = previous["deploy_receipt"]
                    boot.validate_metadata(document, safe.parse_manifest(checked_file(receipt)))
                    state["session"] = current("rescue")
                    state["session"] = lifecycle_run("boot-customer", receipt)
                    report["customer_kernel_verified"] = True
                else:
                    if lifecycle_state and lifecycle_state.get("status") == "failed":
                        require(lifecycle_state.get("binding") == bound, "生命週期失敗屬於不同嘗試")
                        fault = {"path": lifecycle_state["report_path"], "sha256": lifecycle_state["report_sha256"]}
                        state["session"] = lifecycle_run("recover-fault", fault=fault)
                    elif lifecycle_state and lifecycle_state.get("status") == "running":
                        fault = life.reconcile_interrupted(lifecycle, lifecycle_ref["sha256"], bound, guard,
                                                           output / "interruption", deadline)
                        state["session"] = lifecycle_run("recover-fault", fault=fault)
                    elif lifecycle_state and lifecycle_state.get("status") == "customer":
                        state["session"] = _lifecycle_session(lifecycle_state, bound)
                        # 正常返回前重新綁定當前 Linux，不能借登入提示授權關機。
                        state["session"] = current("customer")
                        state["session"] = lifecycle_run("recover-normal")
                    else:
                        if lifecycle_state and lifecycle_state.get("binding") == bound:
                            state["session"] = _lifecycle_session(lifecycle_state, bound)
                        require(state.get("session", {}).get("mode") != "customer",
                                "客戶 session 缺少對應生命週期租約，不能猜測目前已在救援")
                        state["session"] = current("rescue")
                    observed = readonly_preflight({**config, "rescue_ssh": state["session"]["ssh"]},
                                                 document, output, deadline.remaining)
                    save_json(output, "recovery.json", observed)
                    session.recheck(state["session"], request, deadline)
                    report["rescue_verified"] = True
                report["session"] = state["session"]
                deadline.remaining()
                report.update(status="passed", hardware_validated=True)
            except (Exception, KeyboardInterrupt) as exc:
                report.update(status="failed", hardware_validated=False, needs_recovery=needs_recovery,
                              error_code=life._error_code(exc))
            _publish(life.LOCK_ROOT, output, state, report, request, deadline)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="blocked" if output is None else "failed", hardware_validated=False,
                      needs_recovery=needs_recovery, error_code=life._error_code(exc))
        if output is not None:
            try:
                save_json(output, "failure.json", report)
            except (Exception, KeyboardInterrupt):
                report["failure_publication_failed"] = True
    return station.validate_report(report, request)


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
