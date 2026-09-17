#!/usr/bin/env python3
"""外部 USB／NVMe 五階段入口；固定載入來源，不宣稱客戶原開機鏈資格。"""

from contextlib import closing
import argparse
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
import uuid
import zlib

if __package__:
    from . import bpi_lab_backend as core
    from . import bpi_lab_external as external
    from . import bpi_lab_media as media
    from . import bpi_lab_external_linux as linux
    from . import bpi_lab_external_guard as guard
else:
    import bpi_lab_backend as core
    import bpi_lab_external as external
    import bpi_lab_media as media
    import bpi_lab_external_linux as linux
    import bpi_lab_external_guard as guard

deploy, station, life, uboot = core.deploy, core.station, core.life, core.uboot
require = deploy.require
BINDINGS = core.session.BINDINGS
STAGES = station.STAGES
DEPENDENCIES = tuple(dict.fromkeys((*core.DEPENDENCIES, "bpi_lab_external_backend.py",
    "bpi_lab_external_qualify.py", "bpi_lab_external.py", "bpi_lab_media.py",
    "bpi_lab_external_linux.py", "bpi_lab_external_guard.py", "bpi_lab_qualify.py")))


def reference(path, value):
    return {"path": str(path), "sha256": hashlib.sha256(deploy.encode(value)).hexdigest()}


def scope_digest(config):
    return core.scope_digest(config)


def check_dependencies(config):
    dependencies = config["dependencies"]
    deploy.fields(dependencies, " ".join(DEPENDENCIES))
    for name in DEPENDENCIES:
        deploy.checked_bytes({"path": str(Path(__file__).absolute().parent / name),
                              "sha256": dependencies[name]}, 4 * 1024**2)


def check_pairing(config):
    pairing = deploy.load(config["pairing"])
    deploy.fields(pairing, "schema approved record hardware_id resources uart power target protected_sd rescue sd_device")
    require(pairing["schema"] == "bpi-lab-external-pairing-v1" and pairing["approved"] is True,
            "外部媒體實體配對未核定")
    deploy.identifier(pairing["record"])
    require(all(pairing[key] == config[key] for key in
                ("hardware_id", "resources", "target", "protected_sd", "rescue")), "配對與固定資產不符")
    uart, power = pairing["uart"], pairing["power"]
    deploy.fields(uart, "stable_path baud")
    require(type(uart["stable_path"]) is str and re.fullmatch(
        r"/dev/serial/by-id/[A-Za-z0-9_:+.-]+", uart["stable_path"])
        and type(uart["baud"]) is int and 9600 <= uart["baud"] <= 4000000, "UART 配對格式無效")
    deploy.fields(power, "driver name ip mac")
    deploy.identifier(power["name"])
    require(power["driver"] == "bpi-pw" and str(ipaddress.ip_address(power["ip"])) == power["ip"]
            and type(power["mac"]) is str and re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", power["mac"]),
            "只接受固定 bpi-pw 資產配對")
    require(config["resources"]["uart"] == uart["stable_path"]
            and config["resources"]["power"] == "power:" + power["name"]
            and type(pairing["sd_device"]) is int and 0 <= pairing["sd_device"] <= 255,
            "鎖定資源或固定 SD 編號不符")
    return pairing


def load_inputs(config_ref):
    """首次核定可使用尚無 qualification 的固定配置，不提供跳過配對的選項。"""
    config = deploy.load(config_ref)
    deploy.fields(config, "schema station_id hardware_id test_version resources dependencies pairing target "
                  "protected_sd rescue lifecycle rescue_source images output_root timeout_seconds" +
                  (" qualification" if "qualification" in config else ""))
    require(config["schema"] == "bpi-lab-external-backend-v1", "外部後端 schema 不符")
    for key in ("station_id", "hardware_id", "test_version"):
        deploy.identifier(config[key])
    require(config["hardware_id"] not in ("0845", "bpi-m4zero-0845")
            and config["rescue"]["schema"] != "bpi-h618-rescue-v1"
            and config["protected_sd"]["cid"] not in core.SRAM_PROTOTYPE_CIDS,
            "外部後端不得借用既有 SRAM 原型資產或授權")
    media.validate_expected(config["target"])
    media.validate_sd(config["protected_sd"])
    deploy.fields(config["rescue"], "schema kernel identity_sha256")
    require(deploy.core.hash_value(config["rescue"]["identity_sha256"]), "救援身分摘要無效")
    deploy.fields(config["resources"], "uart power media")
    for key, value in config["resources"].items():
        station._resource(value, key, "hardware")
    require(config["resources"]["media"] == media.media_identity(config["target"]), "外部媒體鎖鍵不符")
    require(type(config["timeout_seconds"]) is int and 60 <= config["timeout_seconds"] <= 86400,
            "總期限須為 60..86400 秒")
    deploy.path(config["output_root"])
    require(type(config["images"]) is dict and 1 <= len(config["images"]) <= 64
            and all(deploy.core.hash_value(key) for key in config["images"]), "映像索引無效")
    check_dependencies(config)
    pairing = check_pairing(config)
    validate_lifecycle(deploy.load(config["lifecycle"]), config, pairing)
    return config


def validate_lifecycle(value, config, pairing):
    deploy.fields(value, "schema hardware_id pairing_sha256 uart_device power_program power_dependencies "
                  "autoboot login shutdown_marker off_seconds authorization ssh_setup rescue_expected")
    require(value["schema"] == "bpi-lab-external-lifecycle-v1"
            and value["hardware_id"] == config["hardware_id"]
            and value["pairing_sha256"] == config["pairing"]["sha256"], "生命週期未綁定本配對")
    require(type(value["uart_device"]) is str and re.fullmatch(r"/dev/tty(?:USB|ACM|S|AMA)[0-9]+", value["uart_device"]),
            "須明示 UART 設備，不得探索")
    require(deploy.path(value["power_program"]["path"]).name == "bpi-pw", "只允許固定 bpi-pw 協定")
    deploy.checked_bytes(value["power_program"], 4 * 1024**2)
    require(type(value["power_dependencies"]) is list and len(value["power_dependencies"]) <= 64, "電源相依項無效")
    for item in value["power_dependencies"]:
        deploy.checked_bytes(item, 16 * 1024**2)
    deploy.fields(value["autoboot"], "stop_text stop_key_hex")
    life.literal(value["autoboot"]["stop_text"])
    require(type(value["autoboot"]["stop_key_hex"]) is str and re.fullmatch(
        r"(?:[0-9a-f]{2}){1,16}", value["autoboot"]["stop_key_hex"]), "autoboot 按鍵無效")
    life.literal(value["shutdown_marker"])
    require(type(value["off_seconds"]) is int and 10 <= value["off_seconds"] <= 60, "冷循環間隔無效")
    auth = value["authorization"]
    deploy.fields(auth, "record normal_shutdown cold_cycle customer_boot_may_write_target fault_poweroff "
                  "firstboot_account_changes install_test_ssh_key")
    deploy.identifier(auth["record"])
    require(all(type(auth[key]) is bool for key in auth if key != "record")
            and all(auth[key] for key in ("normal_shutdown", "cold_cycle", "customer_boot_may_write_target")),
            "缺少明示正常關機、冷循環與客戶根寫入授權")
    deploy.fields(value["login"], "customer rescue")
    for mode, login in value["login"].items():
        require(type(login) is dict and login.get("kind") in ("root-shell", "password", "initial-setup"), "未知登入 ABI")
        deploy.fields(login, "kind shell_prompt" if login["kind"] == "root-shell" else
                      "kind shell_prompt login_prompt password_prompt username password" +
                      (" new_password skip_user_creation" if login["kind"] == "initial-setup" else ""))
        life.literal(login["shell_prompt"])
        if login["kind"] != "root-shell":
            require(login["username"] == "root", "受限入口僅接受明示 root 帳號")
            life.literal(login["login_prompt"])
            life.literal(login["password_prompt"])
            life.password(login["password"])
        if login["kind"] == "initial-setup":
            require(mode == "customer" and auth["firstboot_account_changes"]
                    and type(login["skip_user_creation"]) is bool, "首次登入缺少客戶根授權")
            life.password(login["new_password"])
    deploy.fields(value["ssh_setup"], "customer rescue")
    for mode, setup in value["ssh_setup"].items():
        core.session.validate_setup(setup, auth["install_test_ssh_key"])
        require(setup["host_key_path"] == "/etc/ssh/ssh_host_ed25519_key.pub"
                and (setup["wait_for"] == "existing" or mode == "customer")
                and (not setup["install_key"] or mode == "customer" and setup["authorized_keys"] == "/root/.ssh/authorized_keys"),
                "公鑰只可明示安裝到已核對客戶根，固定救援須預先準備")
    deploy.fields(value["rescue_expected"], "architecture dt_compatible")
    require(value["rescue_expected"]["architecture"] in ("arm32", "arm64", "riscv64")
            and type(value["rescue_expected"]["dt_compatible"]) is list
            and value["rescue_expected"]["dt_compatible"], "缺少救援架構與 DT 身分")
    return value


def boot_source(ref, config, *, rescue=False):
    source = deploy.load(ref)
    deploy.fields(source, "schema kind uboot qualification artifact_root")
    require(source["schema"] == "bpi-lab-external-boot-source-v1"
            and source["kind"] in ("sd-prepositioned", "tftp-fixed"), "未支援的外部載入來源")
    boot = uboot.validate_config(deploy.load(source["uboot"]))
    deploy.checked_bytes(source["qualification"])
    require(boot["uboot"]["pairing_sha256"] == config["pairing"]["sha256"]
            and boot["uboot"]["qualification_sha256"] == source["qualification"]["sha256"], "U-Boot 缺少本板建置核定")
    require(boot["files"]["initrd"] is not None, "救援及客戶測試都必須有固定 initrd")
    pairing = deploy.load(config["pairing"])
    if source["kind"] == "sd-prepositioned":
        require(boot["source"]["type"] == "mmc" and boot["source"]["device"] == pairing["sd_device"],
                "載入來源不是配對固定 SD")
    else:
        require(boot["source"]["type"] == "tftp", "TFTP 載入來源不符")
    uboot.validate_artifacts(boot, deploy.path(source["artifact_root"]))
    if rescue:
        require([arg for arg in boot["bootargs"] if arg.startswith("root=")] in ([], ["root=/dev/ram0"])
                and boot["kernel_release"] == config["rescue"]["kernel"], "救援須使用固定獨立 RAM 根")
    return source, boot


def selected_image(config, request):
    require(request["image_sha256"] in config["images"], "映像未列入明確設定")
    bundle = deploy.load(config["images"][request["image_sha256"]])
    deploy.fields(bundle, "schema board image deployment preparation original_components boot_source guard linux_expected customer_ssh")
    require(bundle["schema"] == "bpi-lab-external-image-v1" and bundle["board"] == request["image"]["board"], "外部映像契約不符")
    contract = external.validate_contract(deploy.load(bundle["deployment"]))
    require(all(contract[key] == config[key] for key in ("hardware_id", "target", "protected_sd", "rescue")),
            "每映像部署契約與固定板／媒體／救援不同")
    source = external.validate_source(bundle["image"], config["target"]["bytes"])
    require(source["compressed"] == {"bytes": request["image"]["compressed_bytes"], "sha256": request["image_sha256"]}
            and Path(source["path"]) == deploy.path(request["image_root"]) / request["image"]["relative_path"],
            "未綁定佇列原始 XZ，禁止重建或換檔")
    require(contract["backup"] is not None and contract["authorization"]["write"] is True,
            "備份或完整映像寫入範圍未授權")
    external._source_binding(contract, source)
    prepared = deploy.load(bundle["preparation"])
    require(prepared.get("schema") == "bpi-lab-image-v1" and prepared.get("ok") is True
            and prepared.get("source_verified") is True and prepared["source_digest"] == source["compressed"]
            and prepared["raw"] == source["raw"] and prepared["filesystem_uuid"] == contract["root"]["uuid"]
            and prepared["partition"]["index"] == contract["root"]["partition_index"], "原始映像擷取或根分割證據不符")
    _, boot = boot_source(bundle["boot_source"], config)
    deploy.fields(bundle["original_components"], "kernel dtb initrd")
    for role, path in bundle["original_components"].items():
        if role == "initrd":
            continue
        require(prepared["files"][path]["digest"] == {key: boot["files"][role][key] for key in ("bytes", "sha256")},
                "核心或 DTB 不是原始映像的固定組件")
    require([arg for arg in boot["bootargs"] if arg.startswith("root=")] == ["root=UUID=" + contract["root"]["uuid"]],
            "客戶根僅允許原配 UUID，不接受 sdX、LABEL 或原入口猜測")
    expected = deploy.load(bundle["linux_expected"])
    if "root_growth" in expected:
        require(linux.partition_geometry(prepared["partitions"]) == expected["root_growth"]["partitions"],
                "擴根策略未綁定原始完整分割配置")
    validate_guard(bundle, config, contract, boot, expected)
    deploy.validate_ssh(bundle["customer_ssh"])
    boot_source(config["rescue_source"], config, rescue=True)
    return contract, bundle, boot, expected


def validate_guard(bundle, config, contract, boot, expected):
    linux.validate_expected(expected)
    require(all(expected[key] == contract[key] for key in ("target", "protected_sd", "root"))
            and expected["kernel_release"] == boot["kernel_release"]
            and expected["architecture"] == ("arm" if boot["arch"] == "arm32" else boot["arch"]),
            "Linux 預期與根媒體、原核心或架構不同")
    manifest = deploy.load(bundle["guard"])
    guard.validate_manifest(manifest, expected)
    prepared = deploy.load(bundle["preparation"])
    original = prepared["files"][bundle["original_components"]["initrd"]]["digest"]
    raw = deploy.checked_bytes(manifest["original_initrd"], guard.MAX_ARCHIVE)
    require(original == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, "guard 原 initrd 不是原映像組件")
    require(boot["files"]["initrd"]["format"] == "raw" and
            all(boot["files"]["initrd"][key] == manifest["derived_initrd"][key] for key in ("bytes", "sha256")),
            "U-Boot 未載入已重驗的衍生 guard initrd")
    return manifest


def validate_media_receipt(receipt, contract, source, stage):
    fixed = receipt["contract"]
    require({key: value for key, value in fixed.items() if key != "ssh"} ==
            {key: value for key, value in contract.items() if key != "ssh"}, "外部收據不是同一部署契約")
    require(all(fixed["ssh"][key] == contract["ssh"][key] for key in ("host", "port", "user", "identity")),
            "外部收據 SSH 端點漂移")
    require(receipt.get("operation") == stage and all(receipt.get(key, False) is False for key in
            ("test_only", "synthetic", "simulated")), "外部收據操作錯誤或來自合成證據")
    return external.validate_result(receipt, fixed, source)


def operation_binding(request, contract, bundle):
    return {**{key: request[key] for key in BINDINGS}, "deployment": bundle["deployment"],
            "source_sha256": external.source_digest(bundle["image"]), "guard": bundle["guard"],
            "target": media.media_identity(contract["target"])}


def validation_checks(mode, stage, has_media):
    names = ["same_attempt_uart_ssh", "target_identity", "sd_full_readback", "root_identity"]
    names.append("guard" if mode == "customer" else "rescue_identity")
    if has_media:
        names.append("backup_and_source")
    if stage == "deploy":
        names.append("full_readback")
    if stage in ("boot", "recovery"):
        names.append("cold_cycle")
    return {"schema": "bpi-lab-external-stage-validation-v1",
            "checks": [{"check": name, "status": "passed"} for name in names]}


RESCUE_OBSERVER = r'''
import hashlib,json,time
ops=m.NativeOps(); deadline=time.monotonic()+timeout
def check():
    e.require(time.monotonic()<deadline,'救援採樣逾時')
    watch.poll()
    for fd,identity in opened:
        m.check_fd(fd,identity,ops=ops)
def text(path): return ops.read(path).decode().strip()
started=time.monotonic_ns(); boot_id=text('/proc/sys/kernel/random/boot_id')
opened=[]
with ops.watch() as watch:
    rescue=e._rescue(contract,ops,'/proc')
    target=m.inspect(contract['target'],ops=ops)
    sd=m.inspect_sd(contract['protected_sd'],ops=ops)
    try:
        for item in (target,sd): opened.append((ops.open_device(item['device']),item))
        check()
        full=e._hash_range(opened[1][0],sd['bytes'],check,ops)
        host_key=' '.join(text('/etc/ssh/ssh_host_ed25519_key.pub').split()[:2])
        result={'schema':'bpi-lab-external-rescue-observation-v1','hardware_validated':False,
                'nonce':nonce,'boot_id':boot_id,'kernel':ops.uname().release,'machine':ops.uname().machine,
                'dt_compatible':ops.read('/sys/firmware/devicetree/base/compatible').rstrip(b'\0').decode().split('\0'),
                'host_key':host_key,'rescue':rescue,'target':target,'protected_sd':sd,'sd_readback':full,
                'sampling_started_ns':started}
        e.require(e._rescue(contract,ops,'/proc')==rescue and m.inspect(contract['target'],ops=ops)==target
                  and m.inspect_sd(contract['protected_sd'],ops=ops)==sd
                  and text('/proc/sys/kernel/random/boot_id')==boot_id,'救援採樣期間身分漂移')
        check()
    finally:
        for fd,_ in opened: ops.close(fd)
result['sampling_finished_ns']=time.monotonic_ns()
print(json.dumps(result,ensure_ascii=True))
'''


def rescue_program(contract, nonce, timeout):
    external.validate_contract(contract)
    linux.nonce_value(nonce)
    payload = {"media": Path(media.__file__).read_text(), "external": Path(external.__file__).read_text(),
               "contract": contract, "nonce": nonce, "timeout": timeout}
    packed = base64.b64encode(zlib.compress(deploy.encode(payload), 9)).decode()
    return ("import base64,json,sys,types,zlib\n"
            f"p=json.loads(zlib.decompress(base64.b64decode({packed!r})))\n"
            "m=types.ModuleType('bpi_lab_media');sys.modules[m.__name__]=m\n"
            "exec(compile(p['media'],'<bpi_lab_media>','exec'),m.__dict__)\n"
            "e=types.ModuleType('bpi_lab_external');e.__package__=None\n"
            "exec(compile(p['external'],'<bpi_lab_external>','exec'),e.__dict__)\n"
            "contract,nonce,timeout=p['contract'],p['nonce'],p['timeout']\n" + RESCUE_OBSERVER)


def validate_rescue(value, config, contract, nonce):
    deploy.fields(value, "schema hardware_validated nonce boot_id kernel machine dt_compatible host_key rescue target "
                  "protected_sd sd_readback sampling_started_ns sampling_finished_ns")
    expected = deploy.load(config["lifecycle"])["rescue_expected"]
    architecture = "arm" if expected["architecture"] == "arm32" else expected["architecture"]
    require(value["schema"] == "bpi-lab-external-rescue-observation-v1" and value["hardware_validated"] is False
            and value["nonce"] == nonce and str(uuid.UUID(value["boot_id"])) == value["boot_id"]
            and value["kernel"] == contract["rescue"]["kernel"] and value["machine"] in linux.ARCHITECTURES[architecture]
            and value["dt_compatible"] == expected["dt_compatible"], "救援核心、DT、boot_id 或 nonce 不符")
    linux.public_key(value["host_key"])
    require(type(value["sampling_started_ns"]) is int and type(value["sampling_finished_ns"]) is int
            and 0 <= value["sampling_started_ns"] <= value["sampling_finished_ns"], "救援採樣時間不完整")
    rescue = value["rescue"]
    require(all(rescue[key] == item for key, item in contract["rescue"].items()) and rescue["root_ram"] is True
            and rescue["root_fs"] in ("tmpfs", "rootfs", "ramfs")
            and re.fullmatch(r"0:[0-9]+", rescue["root_dev"]), "救援不是固定獨立 RAM 根")
    for key in ("target", "protected_sd"):
        require(all(value[key][field] == wanted for field, wanted in contract[key].items())
                and all(value[key][field] is False for field in ("mounted", "swap", "holders", "slaves")),
                "救援媒體未核對完整配對及閒置狀態")
    require(value["target"]["devnum"] != value["protected_sd"]["devnum"] and value["sd_readback"] == {
        "bytes": contract["protected_sd"]["bytes"], "sha256": contract["protected_sd"]["full_sha256"]}, "救援 SD 全碟摘要不符")


def uart_program(console, program, deadline):
    """以短行 here-document 傳送固定程式，避免 TTY canonical 單行截斷；不落遠端檔案。"""
    token = secrets.token_hex(32)
    packed = base64.b64encode(program.encode()).decode()
    parts = "\n".join(repr(packed[index:index + 384]) for index in range(0, len(packed), 384))
    wrapper = ("import base64,contextlib,io,json\n"
               "buffer=io.StringIO()\ntry:\n with contextlib.redirect_stdout(buffer):\n"
               "  exec(compile(base64.b64decode(\n" + parts + "\n),'<external-uart>','exec'))\n"
               " value=json.loads(buffer.getvalue())\nexcept BaseException:\n value={'status':'blocked'}\n"
               f"print('BPI_EXTERNAL_{token} '+base64.b64encode(json.dumps(value).encode()).decode())\n")
    delimiter = "BPI_EOF_" + token
    console.send("/usr/bin/python3 -I -B - <<'" + delimiter + "'\n" + wrapper + delimiter + "\n",
                 timeout=deadline.remaining())
    found = console.expect_regex(rb"(?:^|\r?\n)BPI_EXTERNAL_" + token.encode() + rb" ([A-Za-z0-9+/=]+)\r?\n",
                                 timeout=deadline.remaining())
    return linux.parse_json(base64.b64decode(found.groups[0], validate=True))


def ssh_rescue(program, ssh, output, deadline):
    output = deploy.new_directory(output)
    fixed = deploy.snapshot_ssh(ssh, output)
    argv = deploy.backup.ssh_command(fixed["path"], "bpi-lab", {})
    argv[-1] = "/usr/bin/python3 -I -B -"
    data, buffers, sent, code = program.encode(), {"stdout": bytearray(), "stderr": bytearray()}, 0, None
    with closing(deploy.core.upload_stream(argv, iter([data]), deadline.end, deadline.clock)) as events:
        for kind, value in events:
            deadline.remaining()
            require(code is None, "SSH 退出後仍有事件")
            if kind == "sent":
                require(type(value) is int and value > 0, "SSH 傳送計數無效")
                sent += value
                require(sent <= len(data), "SSH 傳送超界")
            elif kind == "exit":
                require(type(value) is int, "SSH 退出碼無效")
                code = value
            else:
                require(kind in buffers and type(value) is bytes
                        and sum(len(item) for item in buffers.values()) + len(value) <= linux.MAX_OUTPUT, "SSH 採樣超界")
                buffers[kind].extend(value)
    require(code == 0 and sent == len(data), "固定救援採樣未完整執行")
    return linux.parse_json(bytes(buffers["stdout"])), fixed


def validate_snapshot(ref, ssh):
    path = deploy.path(ref["path"])
    parsed = {}
    for line in deploy.checked_bytes(ref, 65536).decode("ascii").splitlines():
        pair = shlex.split(line)
        require(len(pair) == 2 and pair[0].lower() not in parsed, "SSH 副本包含未知、重複或非固定設定")
        parsed[pair[0].lower()] = pair[1]
    require(path.name == "ssh-config" and parsed == {
        "host": "bpi-lab", "hostname": ssh["host"], "port": str(ssh["port"]), "user": ssh["user"],
        "identityfile": str(path.parent / "identity"), "userknownhostsfile": str(path.parent / "known_hosts"),
        "globalknownhostsfile": "/dev/null", "knownhostscommand": "none", "verifyhostkeydns": "no",
        "proxycommand": "none", "proxyjump": "none", "stricthostkeychecking": "yes"}, "SSH 副本未綁定嚴格端點")
    for key in ("identity", "known_hosts"):
        require(deploy.checked_bytes({"path": str(path.parent / key), "sha256": ssh[key]["sha256"]}, 65536)
                == deploy.checked_bytes(ssh[key], 65536), "SSH 金鑰副本與本次綁定不同")


def validate_session(observed, request, mode, config, contract, bundle, expected):
    deploy.fields(observed, "schema binding mode boot_id nonce uart_observation ssh_observation ssh hostkey_source uart_verified ssh_verified")
    require(observed["schema"] == "bpi-lab-external-session-v1" and observed["mode"] == mode
            and observed["binding"] == {key: request[key] for key in BINDINGS}
            and observed["uart_verified"] is True and observed["ssh_verified"] is True
            and observed["hostkey_source"] == "same-session-uart", "缺少同 attempt UART→SSH 綁定")
    nonce = observed["nonce"]
    linux.nonce_value(nonce)
    uart, collection = deploy.load(observed["uart_observation"]), deploy.load(observed["ssh_observation"])
    remote = collection["observation"]
    wanted = bundle["customer_ssh"] if mode == "customer" else contract["ssh"]
    ssh = observed["ssh"]
    deploy.validate_ssh(ssh)
    require(all(ssh[key] == wanted[key] for key in ("host", "port", "user", "identity")), "SSH 端點或私鑰不符配對")
    host = ssh["host"] if ssh["port"] == 22 else f"[{ssh['host']}]:{ssh['port']}"
    require(deploy.checked_bytes(ssh["known_hosts"], 65536) == (host + " " + linux.public_key(uart["host_key"]) + "\n").encode(),
            "SSH known_hosts 不是本次 UART 公鑰")
    require(collection["known_hosts"] == ssh["known_hosts"] and collection["status"] == "collected"
            and collection["hardware_validated"] is False and uart["boot_id"] == remote["boot_id"] == observed["boot_id"],
            "collection 或 boot_id 未綁定本次 SSH")
    validate_snapshot(collection["ssh_config"], ssh)
    if mode == "customer":
        require(collection["schema"] == linux.COLLECTION_SCHEMA and collection["nonce"] == nonce
                and collection["expected_sha256"] == linux.digest(expected)
                and collection["uart_observation_sha256"] == linux.digest(uart)
                and collection["collector_sha256"] == hashlib.sha256(linux.program(expected, nonce).encode()).hexdigest(),
                "客戶採樣程式、預期或 UART 參照不符")
        validation = linux.validate_observation(remote, expected, nonce, uart_observation=uart)
        require(validation == collection["validation"], "客戶採樣不是完整重驗結果")
    else:
        validate_rescue(uart, config, contract, nonce)
        validate_rescue(remote, config, contract, nonce)
        require(collection["schema"] == "bpi-lab-external-rescue-collection-v1"
                and collection["uart_observation_sha256"] == media.digest(uart)
                and all(uart[key] == remote[key] for key in uart if key not in
                        ("sampling_started_ns", "sampling_finished_ns"))
                and remote["sampling_started_ns"] >= uart["sampling_finished_ns"], "救援 UART 與 SSH 不是同次採樣")


def establish(console, mode, context, output, deadline, previous_boot_id=None):
    config, contract, bundle, _, expected, request = context
    output = deploy.new_directory(output)
    lifecycle = deploy.load(config["lifecycle"])
    setup = lifecycle["ssh_setup"][mode]
    core.session.validate_setup(setup, lifecycle["authorization"]["install_test_ssh_key"])
    if setup["wait_for"] == "armbian-firstrun":
        require(mode == "customer", "固定救援不可執行客戶首次啟動流程")
        for _ in range(90):
            ready = console.run_shell(
                'case "$(systemctl show armbian-firstrun -p SubState --value)" in exited|dead) '
                'systemctl is-active --quiet ssh;; *) false;; esac', timeout=deadline.remaining(10))
            if ready.exitcode == 0:
                break
            deadline.pause(2)
        else:
            raise deploy.core.DeployError("首次啟動 SSH 未在期限內就緒")
    nonce = secrets.token_hex(32)
    program = linux.program(expected, nonce) if mode == "customer" else rescue_program(contract, nonce, deadline.remaining())
    uart = uart_program(console, program, deadline)
    if mode == "customer":
        linux.validate_observation(uart, expected, nonce)
    else:
        validate_rescue(uart, config, contract, nonce)
    require(previous_boot_id is None or uart["boot_id"] == previous_boot_id, "本次 UART 已重啟，禁止借用舊狀態")
    if setup["install_key"]:
        require(mode == "customer" and setup["authorized_keys"] == "/root/.ssh/authorized_keys",
                "只能寫入已核對客戶根的測試公鑰")
        require(not any(row["mount_point"] != "/" and
                        (setup["authorized_keys"] == row["mount_point"] or
                         setup["authorized_keys"].startswith(row["mount_point"].rstrip("/") + "/"))
                        for row in uart["mounts"]), "公鑰路徑落在另一個掛載，不得寫入")
        before = {"boot_id": uart["boot_id"], "root": {"devnum": uart["root"]["stat"]["devnum"]}}
        core.session.install_key(console, setup, before, deadline)
        fresh = uart_program(console, program, deadline)
        linux.validate_observation(fresh, expected, nonce, uart_observation=uart)
        deploy.save(output, "ssh-setup.json", deploy.encode({"schema": "bpi-lab-external-ssh-setup-v1",
            "boot_id": uart["boot_id"], "public_key": setup["public_key"], "authorized_keys": setup["authorized_keys"],
            "before_sha256": linux.digest(uart), "after_sha256": linux.digest(fresh)}))
        uart = fresh
    wanted = bundle["customer_ssh"] if mode == "customer" else contract["ssh"]
    host = wanted["host"] if wanted["port"] == 22 else f"[{wanted['host']}]:{wanted['port']}"
    hosts = (host + " " + linux.public_key(uart["host_key"]) + "\n").encode()
    deploy.save(output, "known_hosts", hosts)
    ssh = {**wanted, "known_hosts": {"path": str(output / "known_hosts"), "sha256": hashlib.sha256(hosts).hexdigest()}}
    deploy.save(output, "uart-observation.json", deploy.encode(uart))
    if mode == "customer":
        collection = linux.collect(expected, ssh, output / "ssh", nonce=nonce, uart_observation=uart, timeout=deadline.remaining())
    else:
        remote, fixed = ssh_rescue(program, ssh, output / "ssh", deadline)
        collection = {"schema": "bpi-lab-external-rescue-collection-v1", "status": "collected", "hardware_validated": False,
                      "uart_observation_sha256": media.digest(uart), "observation": remote, "known_hosts": ssh["known_hosts"],
                      "ssh_config": fixed}
    deploy.save(output, "collection.json", deploy.encode(collection))
    record = {"schema": "bpi-lab-external-session-v1", "binding": {key: request[key] for key in BINDINGS},
              "mode": mode, "boot_id": uart["boot_id"], "nonce": nonce, "ssh": ssh,
              "uart_observation": reference(output / "uart-observation.json", uart),
              "ssh_observation": reference(output / "collection.json", collection),
              "hostkey_source": "same-session-uart", "uart_verified": True, "ssh_verified": True}
    validate_session(record, request, mode, config, contract, bundle, expected)
    deploy.save(output, "session.json", deploy.encode(record))
    deadline.remaining()
    return record


def validate_boot_receipt(receipt, stage, config, bundle, boot):
    selected_ref = bundle["boot_source"] if stage == "boot" else config["rescue_source"]
    _, selected = boot_source(selected_ref, config, rescue=stage == "recovery")
    require(receipt["source"] == selected_ref and receipt["pairing"] == config["pairing"]
            and receipt["marker"]["schema"] == "bpi-lab-uboot-result-v1"
            and receipt["marker"]["status"] == "kernel-marker-observed"
            and receipt["marker"]["config_sha256"] == uboot._digest(selected)
            and receipt["marker"]["kernel_release"] == selected["kernel_release"], "引導收據與固定載荷不符")
    rows, steps = deploy.load(receipt["steps"]), uboot._steps(selected)
    require(type(rows) is list and len(rows) == len(steps), "U-Boot 操作序列不完整")
    initial_lmb = None
    for row, step in zip(rows, steps):
        require(all(row[key] == value for key, value in step.items()), "U-Boot 步驟不能省略、調換或另加命令")
        if step["check"] == "kernel-marker":
            require(row["status"] == "kernel-marker-observed" and re.search(
                r"Linux version " + re.escape(selected["kernel_release"]) + r"(?:\s|$)", row["marker"]), "核心標記不符")
        else:
            require(row["status"] == "verified", "U-Boot 步驟未通過")
            raw = row["output"].encode("ascii")
            if step["check"] == "memory":
                actual = uboot._memory(raw, selected, step.get("loaded", ()), initial_lmb=initial_lmb)
                if initial_lmb is None:
                    initial_lmb = actual
            else:
                uboot._check(step, raw, selected)
    power = receipt["power"]
    require(type(power) is list and [row["action"] for row in power] == ["status", "off", "status", "on"]
            and all(row["identity_verified"] is True for row in power)
            and [row["on"] for row in power[1:]] == [False, False, True], "缺少完整電源回讀順序")
    lifecycle = deploy.load(config["lifecycle"])
    require(type(receipt["forced_poweroff"]) is bool and receipt["off_seconds"] == lifecycle["off_seconds"]
            and (receipt["forced_poweroff"] and stage == "recovery" and lifecycle["authorization"]["fault_poweroff"]
                 or not receipt["forced_poweroff"] and receipt["normal_shutdown_verified"] is True), "冷循環授權或正常關機證據不符")


class NativeRuntime:
    """只調度固定 D2、D3、UART、U-Boot 與 bpi-pw；沒有配置命令回呼。"""

    def execute(self, context, current, output, timeout):
        config, contract, bundle, boot, expected, request = context
        stage, lifecycle = request["stage"], deploy.load(config["lifecycle"])
        pairing = deploy.load(config["pairing"])
        output = deploy.new_directory(output)
        runtime = life.NativeRuntime()
        deadline = life.Deadline(timeout, runtime.clock, runtime.sleep)
        if stage == "recovery":
            core.require_recovery_safe(current)
        mode = current["phase"] if "resume" in request else "customer" if stage in ("boot", "smoke") else "rescue"
        receipt, boot_receipt = None, None
        with runtime.console(lifecycle, pairing, output, deadline) as console:
            if stage in ("boot", "recovery"):
                failed = current["status"] in ("running", "failed")
                require(not failed or stage == "recovery" and lifecycle["authorization"]["fault_poweroff"],
                        "故障斷電需要獨立明示授權")
                boot_receipt = {"source": bundle["boot_source"] if stage == "boot" else config["rescue_source"],
                                "pairing": config["pairing"], "power": [], "forced_poweroff": failed,
                                "normal_shutdown_verified": False, "off_seconds": lifecycle["off_seconds"]}
                if not failed:
                    life.login(console, lifecycle["login"][current["phase"]], deadline, existing=True)
                    establish(console, current["phase"], context, output / "before", deadline, current["boot_id"])
                    life.power(runtime, lifecycle, pairing, "status", True, deadline, boot_receipt["power"])
                    console.send("/bin/busybox poweroff -f\n" if current["phase"] == "rescue" else "systemctl poweroff\n",
                                 timeout=deadline.remaining(10))
                    console.expect_regex(rb"(?:^|\r?\n)(?:\[\s*[0-9.]+\]\s*)?" +
                                         re.escape(lifecycle["shutdown_marker"].encode()) + rb"\r?\n", timeout=deadline.remaining(180))
                    boot_receipt["normal_shutdown_verified"] = True
                else:
                    life.power(runtime, lifecycle, pairing, "status", None, deadline, boot_receipt["power"])
                deploy.save(output, "shutdown.json", deploy.encode(boot_receipt))
                life.power(runtime, lifecycle, pairing, "off", False, deadline, boot_receipt["power"])
                deadline.pause(lifecycle["off_seconds"])
                life.power(runtime, lifecycle, pairing, "status", False, deadline, boot_receipt["power"])
                runtime.drain(console, deadline)
                life.power(runtime, lifecycle, pairing, "on", True, deadline, boot_receipt["power"])
                console.expect_literal(lifecycle["autoboot"]["stop_text"], timeout=deadline.remaining(60))
                console.send(bytes.fromhex(lifecycle["autoboot"]["stop_key_hex"]), timeout=deadline.remaining(5))
                _, selected = boot_source(boot_receipt["source"], config, rescue=stage == "recovery")
                steps = []
                try:
                    boot_receipt["marker"] = uboot.boot(console, selected, steps, timeout=deadline.remaining(1800), monotonic=deadline.clock)
                finally:
                    deploy.save(output, "uboot-steps.json", deploy.encode(steps))
                boot_receipt["steps"] = reference(output / "uboot-steps.json", steps)
                life.login(console, lifecycle["login"][mode], deadline,
                           initialize_authorized=lifecycle["authorization"]["firstboot_account_changes"])
                observed = establish(console, mode, context, output / "session", deadline)
                require(current.get("boot_id") is None or observed["boot_id"] != current["boot_id"], "冷循環未產生新 boot_id")
            else:
                life.login(console, lifecycle["login"][mode], deadline, existing=True)
                observed = establish(console, mode, context, output / "session", deadline, current.get("boot_id") if current else None)
                if stage == "deploy" or stage == "preflight" and "resume" not in request:
                    fixed = {**contract, "ssh": observed["ssh"]}
                    if stage == "deploy":
                        raw = external.deploy(fixed, bundle["image"], output / "media", confirm_overwrite=True, timeout=deadline.remaining())
                    else:
                        raw = external.preflight(fixed, bundle["image"], output / "media", timeout=deadline.remaining())
                    validate_media_receipt(raw, contract, bundle["image"], stage)
                    deploy.save(output, "media.json", deploy.encode(raw))
                    receipt = reference(output / "media.json", raw)
                    after = establish(console, mode, context, output / "after", deadline, observed["boot_id"])
                    require(after["ssh"]["known_hosts"]["sha256"] == observed["ssh"]["known_hosts"]["sha256"], "操作期間 hostkey 改變")
            result = {"schema": "bpi-lab-external-operation-v1", "action": stage, "status": "verified", "simulated": False,
                      "binding": operation_binding(request, contract, bundle), "session": observed, "media_receipt": receipt,
                      "boot": boot_receipt, "validation": validation_checks(mode, stage, receipt is not None)}
            validate_result(stage, result, request, config, contract, bundle, boot, expected, current=current)
        deadline.remaining()
        deploy.save(output, "result.json", deploy.encode(result))
        return result


def validate_result(stage, result, request, config, contract, bundle, boot, expected, *, current=None):
    try:
        deploy.fields(result, "schema action status simulated binding session media_receipt validation boot")
        require(result["schema"] == "bpi-lab-external-operation-v1" and result["action"] == stage
                and result["status"] == "verified" and result["simulated"] is False
                and result["binding"] == operation_binding(request, contract, bundle), "原生外部操作未綁定完整輸入")
        resume = request.get("resume")
        mode = ("rescue" if resume["next_stage"] in ("deploy", "boot") else "customer") if resume else (
            "customer" if stage in ("boot", "smoke") else "rescue")
        observed = result["session"]
        validate_session(observed, request, mode, config, contract, bundle, expected)
        if current and current.get("boot_id"):
            if stage in ("boot", "recovery"):
                require(observed["boot_id"] != current["boot_id"], "冷循環沒有新的 boot_id")
            else:
                require(observed["boot_id"] == current["boot_id"], "同工作觀測期間重啟")
                if current.get("operation"):
                    before = deploy.load(deploy.load(current["operation"])["session"]["uart_observation"])
                    after = deploy.load(observed["uart_observation"])
                    if mode == "customer" and "root_growth" in expected:
                        linux.validate_media_transition(before, after, expected)
                        require(before["host_key"] == after["host_key"], "同次階段 SSH 公鑰漂移")
                    else:
                        require(all(before[key] == after[key] for key in ("target", "protected_sd", "host_key")),
                                "同次階段之間媒體或 SSH 公鑰漂移")
        has_media = stage == "deploy" or stage == "preflight" and not resume
        if has_media:
            receipt = deploy.load(result["media_receipt"])
            validate_media_receipt(receipt, contract, bundle["image"], stage)
            if "root_growth" in expected:
                layout = receipt["source_layout"]
                require(layout["table"] == "dos" and layout["sector_size"] == 512
                        and linux.partition_geometry(layout["partitions"]) == expected["root_growth"]["partitions"],
                        "實讀來源 MBR 與擴根策略不同")
            require(receipt["contract"]["ssh"] == observed["ssh"], "部署收據未綁定本次 UART 建立的 SSH")
        else:
            require(result["media_receipt"] is None, "觀測或復原不能借用其他媒體操作收據")
        if stage in ("boot", "recovery"):
            validate_boot_receipt(result["boot"], stage, config, bundle, boot)
        else:
            require(result["boot"] is None, "觀測階段不得包含未授權開機")
        require(result["validation"] == validation_checks(mode, stage, has_media), "外部階段檢查不完整或重複")
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise deploy.core.DeployError("外部操作缺少完整結構化證據") from exc
    return result


class StateStore(core.StateStore):
    """沿用共用持久狀態與鎖鍵，只以真正外部部署收據清除 writer 隔離。"""

    def __init__(self, config, contract, source):
        super().__init__(config)
        self.contract, self.source = contract, source

    def validate_deploy_receipt(self, receipt):
        require(receipt.get("schema") == "bpi-lab-external-operation-v1" and receipt.get("action") == "deploy",
                "外部狀態不能接受 MMC 或其他階段收據")
        validate_media_receipt(deploy.load(receipt["media_receipt"]), self.contract, self.source, "deploy")


def report_for(request, *, status="blocked"):
    return {"schema": "bpi-lab-stage-v1", **{key: request[key] for key in station.BINDINGS},
            "status": status, "hardware_validated": False, "whole_backend_ready": False,
            "original_boot_chain_verified": False, "derived_initramfs": True,
            "supported_stages": list(STAGES), "needs_recovery": False}


def passed_report(request, result, operation, previous, *, synthetic=False):
    report = report_for(request, status="blocked" if synthetic else "passed")
    report.update(hardware_validated=not synthetic, operation=operation, previous_report=previous)
    flags = {"preflight": ("media_identity_verified", "backup_verified"),
             "deploy": ("full_readback_verified", "compressed_sha256_verified"),
             "boot": ("customer_kernel_verified",), "recovery": ("rescue_verified",)}
    if request["stage"] == "smoke":
        report.update(checks={row["check"]: True for row in result["validation"]["checks"]},
                      smoke_scope="linux-read-only", stress_tested=False)
    else:
        report.update({key: True for key in flags[request["stage"]]})
    if "resume" in request:
        report.update(resume_state_verified=True, resume_next_stage=request["resume"]["next_stage"])
    if synthetic:
        report.update(test_only=True, reason="替身僅驗證軟體，不產生實板資格")
    station.validate_report(report, request)
    return report


def run_stage(config_path, config_sha256, request, *, runtime=None):
    station._validate_request(request)
    report = report_for(request)
    synthetic, started, uncertain, published = runtime is not None, False, False, False
    output = None
    try:
        require(request["mode"] == "hardware", "外部後端不接受模擬站點冒充硬體")
        require(deploy.core.hash_value(request["work_key"]), "工作鍵不是 SHA-256")
        deploy.identifier(request["attempt_id"])
        config_ref = {"path": str(config_path), "sha256": config_sha256}
        config = load_inputs(config_ref)
        require(request["boot_config_sha256"] == config_sha256 and all(config[key] == request[key] for key in
                ("station_id", "hardware_id", "test_version")), "請求未綁定完整配置")
        qualification = check_qualification(config)
        require(request["image_sha256"] in qualification["approved_images"], "本映像不在人工核定的批次範圍")
        contract, bundle, boot, expected = selected_image(config, request)
        stage = request["stage"]
        suffix = "-" + secrets.token_hex(8) if "resume" in request or stage == "recovery" else ""
        output = deploy.path(config["output_root"]) / (request["work_key"] + "-" + request["attempt_id"] + "-" + stage + suffix)
        runner = NativeRuntime() if runtime is None else runtime
        deadline = time.monotonic() + config["timeout_seconds"]
        with core.resource_lock(config):
            store = StateStore(config, contract, bundle["image"])
            current = store.read()
            core.transition(current, request)
            if stage == "boot":
                validate_result("deploy", deploy.load(current["operation"]), {**request, "stage": "deploy"},
                                config, contract, bundle, boot, expected)
            output = deploy.new_directory(output)
            report["evidence_path"] = str(output)
            deploy.save(output, "request.json", deploy.encode(request))
            active = {"schema": "bpi-lab-backend-state-v1", "binding": {key: request[key] for key in BINDINGS},
                      "status": "running", "stage": stage, "phase": current["phase"] if current else "rescue",
                      "boot_id": current.get("boot_id") if current else None, "output": str(output)}
            try:
                if not synthetic:
                    uncertain = True
                    try:
                        store.write(active)
                    except (ValueError, OSError, KeyError, TypeError):
                        try:
                            uncertain = store.read() != current
                        except (ValueError, OSError, KeyError, TypeError):
                            pass
                        raise
                    uncertain = False
                started = True
                remaining = deploy.core.deadline_check(deadline, time.monotonic)
                result = runner.execute((config, contract, bundle, boot, expected, request), current, output / "operation", remaining)
                validate_result(stage, result, request, config, contract, bundle, boot, expected, current=current)
                deploy.save(output, "operation.json", deploy.encode(result))
                deploy.core.deadline_check(deadline, time.monotonic)
                check_dependencies(config)
                operation = reference(output / "operation.json", result)
                report = {**passed_report(request, result, operation, current.get("report") if current else None,
                                         synthetic=synthetic), "evidence_path": str(output)}
                observed = result["session"]
                completed = {**active, "status": "verified", "phase": observed["mode"], "boot_id": observed["boot_id"],
                             "operation": operation}
                if "resume" in request:
                    completed.update(stage=current["stage"], report=current["report"], operation=current["operation"], resume_guard=True)
                if not synthetic:
                    store.publish(completed, output, report)
                    deploy.core.deadline_check(deadline, time.monotonic)
                    published = True
            except BaseException:
                if started and not synthetic:
                    store.write({**active, "status": "failed"})
                raise
    except (ValueError, OSError, KeyError, TypeError, IndexError, TimeoutError):
        uncertain = uncertain or started and not synthetic
        report.update(status="failed" if started or uncertain else "blocked", hardware_validated=False,
                      needs_recovery=bool(uncertain), reason="外部媒體契約、操作或發布未完成；不自動重刷或切電")
    station.validate_report(report, request)
    if output is not None and "evidence_path" in report and not published:
        try:
            try:
                deploy.save(output, "report.json", deploy.encode(report))
            except FileExistsError:
                deploy.save(output, "report.failure.json", deploy.encode(report))
        except (ValueError, OSError):
            report["report_persistence_failed"] = True
    return report


def check_qualification(config):
    require("qualification" in config, "批次缺少已審閱資格；首次單套試驗請使用 external_qualify")
    if __package__:
        from . import bpi_lab_external_qualify as qualify
    else:
        import bpi_lab_external_qualify as qualify
    return qualify.check_qualification(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description="外部媒體固定五階段入口；stdin 接收 external-v1 工作")
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(station.MAX_OUTPUT_BYTES + 1)
        require(len(raw) <= station.MAX_OUTPUT_BYTES, "工作輸入超界")
        report = run_stage(args.config, args.config_sha256, station._json_loads(raw))
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({"schema": "bpi-lab-adapter-error-v1", "reason": "外部工作或 CLI 輸入無效"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
