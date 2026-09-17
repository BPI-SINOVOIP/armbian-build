#!/usr/bin/env python3
"""共用五階段後端；以已配對 mainline UART 串接部署、冷循環及救援。

所有階段須有完整板級核定；中斷預設阻擋，故障斷電需要獨立授權。
短測限 Linux 唯讀檢查，不代表壓力測試或原生 ROM／SPL 開機鏈資格。
"""

from contextlib import contextmanager, ExitStack
import argparse
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

if __package__:
    from . import bpi_lab_allwinner as allwinner
    from . import bpi_lab_amlogic as amlogic
    from . import bpi_lab_extlinux as extlinux
    from . import bpi_lab_special as special
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_linux as linux
    from . import bpi_lab_lifecycle as life
    from . import bpi_lab_session as session
    from . import bpi_lab_station as station
    from . import bpi_lab_uboot as uboot
    from . import bpi_lab_evidence as evidence
else:
    import bpi_lab_allwinner as allwinner
    import bpi_lab_amlogic as amlogic
    import bpi_lab_extlinux as extlinux
    import bpi_lab_special as special
    import bpi_lab_deploy as deploy
    import bpi_lab_linux as linux
    import bpi_lab_lifecycle as life
    import bpi_lab_session as session
    import bpi_lab_station as station
    import bpi_lab_uboot as uboot
    import bpi_lab_evidence as evidence

require = deploy.require
IMPLEMENTED = station.STAGES
LOCK_ROOT = Path("/var/tmp/bpi-lab-shared-locks")
# 僅排除既有 SRAM 原型資產，不把 SoC 或共用控制器位址當成板級授權。
SRAM_PROTOTYPE_CIDS = frozenset(("d629034339413535311299942ee08c07", "03534453523634478697bc8c0701846b"))
H618_BOARDS = frozenset(("bpi-m4z", "bpi-m4b", "bpi-m4z-emac"))
DEPENDENCIES = (
    "bpi_lab_backend.py", "bpi_lab_deploy.py", "bpi_lab_linux.py", "bpi_lab_uboot.py",
    "bpi_lab_station.py", "bpi_h618_emmc_deploy.py", "bpi_h618_emmc_backup.py", "bpi_h618_artifacts.py",
    "bpi_lab_lifecycle.py", "bpi_lab_session.py", "bpi_lab_console.py", "bpi_sram_uart.py", "bpi_sram_package.py",
    "bpi_lab_allwinner.py", "bpi_lab_amlogic.py", "bpi_lab_extlinux.py", "bpi_lab_special.py", "bpi_lab_cma.py",
    "bpi_lab_original_entry.py", "bpi_lab_special_runtime.py", "bpi_lab_image.py", "bpi_lab_rockchip.py",
    "bpi_lab_realtek_rescue.py",
    "bpi_lab_evidence.py",
)


def scope_digest(config):
    """核定綁定設定本體；排除核定檔參照以避免自我摘要循環。"""
    return hashlib.sha256(deploy.encode({key: value for key, value in config.items()
                                       if key != "qualification"})).hexdigest()


def check_dependencies(dependencies):
    deploy.fields(dependencies, " ".join(DEPENDENCIES))
    root = Path(__file__).absolute().parent
    for name in DEPENDENCIES:
        deploy.checked_bytes({"path": str(root / name), "sha256": dependencies[name]}, 2 * 1024**2)


def check_pairing(reference, config, contract):
    pairing = deploy.load(reference)
    deploy.fields(pairing, "schema approved record hardware_id resources uart power emmc protected_sd rescue")
    require(pairing["schema"] == "bpi-lab-pairing-v1" and pairing["approved"] is True,
            "板級配對尚未核定")
    deploy.identifier(pairing["record"])
    require(pairing["hardware_id"] == config["hardware_id"]
            and pairing["resources"] == config["resources"] and pairing["emmc"] == contract["expected"]
            and pairing["protected_sd"] == contract["protected_sd"] and pairing["rescue"] == contract["rescue"],
            "配對未綁定本板、UART、電源、雙媒體及救援")
    uart, power = pairing["uart"], pairing["power"]
    deploy.fields(uart, "stable_path baud")
    require(type(uart["stable_path"]) is str and re.fullmatch(
        r"/dev/serial/by-(?:id|path)/[A-Za-z0-9_:+.-]+", uart["stable_path"])
        and type(uart["baud"]) is int and 9600 <= uart["baud"] <= 4000000, "UART 尚未明確配對")
    deploy.fields(power, "driver name ip mac")
    require(power["driver"] == "bpi-pw", "此版只保存 bpi-pw 配對；不推論其他電源契約")
    deploy.identifier(power["name"])
    require(type(power["ip"]) is str and str(ipaddress.ip_address(power["ip"])) == power["ip"]
            and type(power["mac"]) is str and re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", power["mac"]),
            "電源 IP 或 MAC 無效")
    require(config["resources"]["uart"] == uart["stable_path"]
            and config["resources"]["power"] == "power:" + power["name"], "資源與實體配對不符")
    return pairing


def require_independent_scope(config, contract):
    require(config["hardware_id"] not in ("0845", "bpi-m4zero-0845")
            and contract["hardware_id"] not in ("0845", "bpi-m4zero-0845")
            and contract["authorization"]["hardware_id"] not in ("0845", "bpi-m4zero-0845")
            and all(contract[key]["cid"] not in SRAM_PROTOTYPE_CIDS for key in ("expected", "protected_sd"))
            and contract["rescue"]["schema"] != "bpi-h618-rescue-v1",
            "共用後端不得混用 0845 資產、覆寫授權或既有 SRAM 救援身分；須獨立全新契約")


def load_inputs(reference):
    """載入完整固定輸入及單板配對；不檢查循環資格，供獨立首次核定入口使用。"""
    config = deploy.load(reference)
    deploy.fields(config, "schema station_id hardware_id test_version resources dependencies pairing "
                  "deploy images output_root timeout_seconds lifecycle" + (" qualification" if "qualification" in config else ""))
    require(config["schema"] == "bpi-lab-backend-v1", "共用後端 schema 不符")
    for key in ("station_id", "hardware_id", "test_version"):
        deploy.identifier(config[key])
    require(type(config["timeout_seconds"]) is int and 60 <= config["timeout_seconds"] <= 86400,
            "後端總期限須為 60..86400 秒")
    deploy.path(config["output_root"])
    check_dependencies(config["dependencies"])
    deploy.fields(config["resources"], "uart power media")
    for key, value in config["resources"].items():
        station._resource(value, key, "hardware")
    contract = deploy.validate_contract(deploy.load(config["deploy"]))
    require_independent_scope(config, contract)
    require(contract["hardware_id"] == config["hardware_id"]
            and config["resources"]["media"] == "cid:" + contract["expected"]["cid"], "部署契約屬於不同實板或媒體")
    check_pairing(config["pairing"], config, contract)
    require(type(config["images"]) is dict and 1 <= len(config["images"]) <= 64
            and all(deploy.core.hash_value(key) for key in config["images"]), "映像索引無效")
    return config, contract


def check_qualification(config):
    require("qualification" in config, "佇列缺少經審閱的完整循環資格；請使用獨立首次核定入口")
    qualification = deploy.load(config["qualification"])
    deploy.fields(qualification, "schema scope_sha256 approved_stages hardware_validated rescue_verified "
                  "single_image_cycle_verified source_evidence")
    require(qualification["schema"] == "bpi-lab-backend-qualification-v1"
            and qualification["scope_sha256"] == scope_digest(config)
            and all(qualification[key] is True for key in
                    ("hardware_validated", "rescue_verified", "single_image_cycle_verified")),
            "沒有綁定本次完整配置的板級核定")
    stages = qualification["approved_stages"]
    require(type(stages) is list and stages and len(stages) == len(set(stages))
            and all(stage in IMPLEMENTED for stage in stages), "核定含未實作階段或格式無效")
    evidence = qualification["source_evidence"]
    require(type(evidence) is list and 1 <= len(evidence) <= 32, "核定缺少原始證據")
    for item in evidence:
        deploy.checked_bytes(item, 16 * 1024**2)
    return qualification


def load_config(reference):
    """佇列入口永遠驗證資格，沒有跳過守門旗標。"""
    config, contract = load_inputs(reference)
    return config, contract, check_qualification(config)


def child_document(parent, record):
    deploy.fields(record, "path bytes sha256")
    deploy.safe.relative_parts(record["path"])
    deploy.digest_record({key: record[key] for key in ("bytes", "sha256")}, 4 * 1024**2)
    blob = deploy.checked_bytes({"path": str(parent / record["path"]), "sha256": record["sha256"]}, 4 * 1024**2)
    require(len(blob) == record["bytes"], "原配子清單長度不符")
    return station._json_loads(blob)


def render_family(family, template, artifact_root, *, runtime_config=None):
    """必經家族正式核對入口；沒有直接執行 ABI 的結果不得升格。"""
    schema = family.get("schema")
    if schema == allwinner.SCHEMA:
        return allwinner.build_uboot_config(family, template=template, artifact_root=artifact_root)
    if schema == amlogic.SCHEMA:
        return amlogic.validate_boot_config(artifact_root, template=template)["config"]
    if schema == extlinux.SCHEMA:
        extlinux.validate_template(family, template=template, artifact_root=artifact_root)
        require(runtime_config is not None and runtime_config.get("schema") == life.original_entry.SCHEMA,
                "原入口證據需要獨立 original-entry 執行配置，不能當作共用 U-Boot 配置")
        require(template["pairing_sha256"] == runtime_config["pairing"]["sha256"]
                and template["firmware_review_sha256"] == runtime_config["qualification"]["sha256"],
                "原入口範本未綁定本次配對與前置韌體審閱")
        require(deploy.load(runtime_config["components"]["manifest"]) == family,
                "原入口執行器與 prepare 原配清單不同")
        return life.original_entry.build_uboot_config(runtime_config, artifact_root=artifact_root)
    elif schema == special.SCHEMA:
        placement = runtime_config["execution"].get("kernel_placement", "original") if runtime_config is not None else "original"
        result = life.special_runtime.bootconfig(artifact_root, template=template, kernel_placement=placement)
        require(runtime_config is not None and runtime_config.get("schema") == life.special_runtime.SCHEMA,
                "special 配方需要獨立 runtime 執行配置，不能丟棄韌體載荷")
        if result.get("vendor") is not None:
            require(runtime_config["board"] in life.special_runtime.VENDOR_BOARDS
                    and runtime_config["execution"]["uboot"]["abi"] == life.special_runtime.VENDOR_ABI,
                    "Realtek 配方必須交給已核定的 realtek-lab-v1，不可降級為主線 ABI")
        require(runtime_config["template"] == template, "special 執行器不是原家族核對範本")
        life.special_runtime.validate_artifacts(runtime_config, artifact_root)
        return life.special_runtime.validate_config(runtime_config)
    else:
        raise deploy.core.DeployError("未知原配家族清單")


def bind_runtime(bundle, boot, extraction, extraction_ref, config):
    """原入口與特殊載荷由各自執行器驗證路徑；必須綁定本次來源及同板核定。"""
    require(boot["board"] == bundle["board"] and boot["pairing"] == config["pairing"]
            and boot["qualification"] == bundle["uboot_qualification"], "執行器不是本板原配與引導核定")
    if life.boot_driver(boot) is life.special_runtime:
        transport = boot["execution"]["transport"]
        require(bundle["transport"] == transport and transport["extraction"] == extraction_ref
                and transport["media"] == "emmc", "special 傳輸未綁定同次擷取及客戶 eMMC")
        if boot["board"] in life.special_runtime.VENDOR_BOARDS:
            require(transport["root_preparation"] == bundle["preparation"], "Realtek 根識別不是本次 prepare 證據")
    else:
        require(boot["components"]["extraction"] == extraction_ref, "原入口不是本次原映像擷取")
        transport = bundle["transport"]
        deploy.fields(transport, "kind image_paths")
        deploy.fields(transport["image_paths"], "kernel initrd dtb")
        require(transport["kind"] == "mmc-original", "原入口只能讀取已核對原媒體，不可替換為 TFTP")
        manifest = deploy.load(boot["components"]["manifest"])
        for role, item in boot["files"].items():
            logical = manifest["files"][role]["path"]
            require(transport["image_paths"][role] == logical
                    and extraction["files"][logical]["digest"] == {key: item[key] for key in ("bytes", "sha256")},
                    "原入口載荷不是原映像核對過的原始路徑與位元組")
    return boot


def bind_transport(bundle, prepared, extraction):
    """本機擷取路徑不等於 MMC 路徑；必須核對原檔或使用獨立 TFTP 發布。"""
    transport = bundle["transport"]
    config = copy.deepcopy(prepared)
    if transport.get("kind") == "mmc-original":
        deploy.fields(transport, "kind image_paths")
        require(config["source"]["type"] == "mmc", "MMC 原檔映射與載入來源不同")
        deploy.fields(transport["image_paths"], " ".join(config["files"]))
        for role, item in config["files"].items():
            if item is None:
                require(transport["image_paths"][role] is None, "不存在的載荷不得指定路徑")
                continue
            logical = transport["image_paths"][role]
            require(type(logical) is str and logical.startswith("/"), "須明示原映像絕對路徑")
            record = extraction["files"].get(logical)
            require(type(record) is dict and record.get("digest") == {key: item[key] for key in ("bytes", "sha256")},
                    "MMC 只能載入原映像已有的完全相同位元組；衍生 DTB／解壓核心須用 TFTP")
            index = record.get("volume_index", extraction["partition"]["index"])
            partitions = extraction.get("partitions", [extraction["partition"]])
            selected = [row for row in partitions if row["index"] == index]
            require(len(selected) == 1 and config["source"]["partition"] == index
                    and config["source"]["partuuid"] == selected[0]["partuuid"], "MMC 原檔不在已核定載入分割區")
            item["path"] = record["resolved"]
    else:
        deploy.fields(transport, "kind root serverip")
        require(transport["kind"] == "tftp-published" and config["source"]["type"] == "tftp"
                and transport["serverip"] == config["source"]["serverip"], "TFTP 發布端與 U-Boot 配置不同")
        deploy.path(transport["root"])
        prefix = hashlib.sha256(deploy.encode(prepared["files"])).hexdigest()
        for role, item in config["files"].items():
            if item is not None:
                item["path"] = prefix + "/" + role + "." + item["format"]
    return uboot.validate_config(config)


def publish_artifacts(bundle, boot):
    """只向明示本機 TFTP 根目錄新增不可覆寫產物；板端仍逐載荷核對 SHA-256。"""
    if bundle["transport"]["kind"] != "tftp-published":
        return
    root = deploy.path(bundle["transport"]["root"])
    prefix = boot["files"]["kernel"]["path"].split("/")[0]
    destination = root / prefix
    try:
        deploy.new_directory(destination)
        created = True
    except FileExistsError:
        created = False
    with deploy.safe.open_root(destination) as out:
        for role, item in boot["files"].items():
            if item is None:
                continue
            name = Path(item["path"]).name
            wanted = {key: item[key] for key in ("bytes", "sha256")}
            if created:
                source = bundle["_prepared_boot"]["files"][role]
                with deploy.safe.open_root(bundle["artifact_root"]) as source_root:
                    with deploy.safe.open_file(source_root, source["path"].lstrip("/")) as stream:
                        before = deploy.backup.file_identity(os.fstat(stream.fileno()))
                        with deploy.safe.open_file(out, name, create=True) as target:
                            digest, count = hashlib.sha256(), 0
                            while block := stream.read(deploy.core.CHUNK):
                                count += len(block)
                                require(count <= item["bytes"], "TFTP 來源長度超界")
                                target.write(block)
                                digest.update(block)
                            require({"bytes": count, "sha256": digest.hexdigest()} == wanted, "TFTP 發布來源摘要不同")
                            deploy.core.unchanged(source_root, source["path"].lstrip("/"), stream, before)
                            target.flush()
                            os.fchmod(target.fileno(), 0o444)
                            os.fsync(target.fileno())
            actual, _ = deploy.safe.fingerprint(out, name, limit=item["bytes"])
            require(actual == wanted, "既有 TFTP 發布產物不同；不覆寫或刪除")
        os.fchmod(out, 0o755)
        os.fsync(out)


def selected_image(config, contract, request):
    """消費 prepare 的持久清單，不執行原映像或其引導腳本。"""
    reference = config["images"].get(request["image_sha256"])
    require(reference is not None, "映像沒有已核定的原配配置")
    bundle = deploy.load(reference)
    deploy.fields(bundle, "schema board image preparation uboot uboot_template uboot_qualification linux_expected artifact_root customer_ssh transport")
    require(bundle["schema"] == "bpi-lab-backend-image-v1" and bundle["board"] == request["image"]["board"],
            "映像配置與工作板型不符")
    require(bundle["board"] not in ("bpi-m4zero", "bpi-m4berry"), "H618 舊別名不能代替正式原配板名")
    require_independent_scope(config, contract)
    source = bundle["image"]
    deploy.validate_source(source, contract["expected"]["bytes"])
    relative = request["image"].get("relative_path")
    deploy.safe.relative_parts(relative)
    require(source["path"] == str(deploy.path(request["image_root"]) / relative)
            and source["compressed"]["sha256"] == request["image_sha256"]
            and source["compressed"]["bytes"] == request["image"].get("compressed_bytes"),
            "映像路徑、壓縮長度或完整摘要不符")
    prepared = deploy.load(bundle["preparation"])
    require(type(prepared) is dict and prepared.get("schema") == "bpi-lab-prepare-v1"
            and prepared.get("status") == "prepared" and prepared.get("hardware_validated") is False
            and prepared.get("board") == bundle["board"]
            and prepared.get("source") == source["compressed"] and prepared.get("raw") == source["raw"],
            "prepare 清單未完整通過或不是原映像")
    parent = deploy.path(bundle["preparation"]["path"]).parent
    extraction = child_document(parent, prepared["extraction"])
    require(extraction.get("ok") is True and extraction.get("source_verified") is True
            and extraction.get("source_digest") == source["compressed"] and extraction.get("raw") == source["raw"]
            and extraction.get("filesystem_uuid") == prepared["root_uuid"], "原始擷取證據與原配清單不符")
    family = child_document(parent, prepared["components"])
    if bundle["board"] in H618_BOARDS:
        require(family.get("schema") == allwinner.SCHEMA and family.get("board") == bundle["board"]
                and family.get("profile", {}).get("board") == bundle["board"],
                "H618 必須重新核對正式板名的 Allwinner 原配組件，不得挪用其他板型")
    require(family.get("status") == "prepared" and family.get("hardware_validated") is False
            and family.get("blockers") == []
            and family.get("kernel_release") == prepared["kernel_release"], "原配家族證據不完整")
    binding = prepared.get("root_binding", {"method": "uuid", "uuid": prepared["root_uuid"]})
    if binding.get("method") == "label":
        deploy.fields(binding, "method label uuid unique_in_image unique_on_hardware")
        require(bundle["board"] in life.special_runtime.VENDOR_BOARDS and family.get("schema") == special.SCHEMA
                and prepared.get("root_identity_verified") is True and prepared.get("root_uuid_verified") is False
                and family.get("root_uuid") is None and binding["label"] == "BPI-ROOT"
                and binding["uuid"] == prepared["root_uuid"] and binding["unique_in_image"] is True
                and binding["unique_on_hardware"] is False and family.get("root_label") == binding["label"]
                and family.get("root_target") == "LABEL=" + binding["label"]
                and extraction.get("filesystem_label") == binding["label"]
                and extraction.get("filesystem_label_unique") is True
                and extraction.get("filesystem_labels_complete") is True,
                "Realtek LABEL 未綁定原配唯一標籤、實際 UUID 或完整映像盤點")
    else:
        require(prepared.get("root_uuid_verified") is True and family.get("root_uuid") == prepared["root_uuid"]
                and binding == {"method": "uuid", "uuid": prepared["root_uuid"]}, "原配根 UUID 未完整核對")
    bundle["_root_binding"] = binding
    artifact_root = deploy.path(bundle["artifact_root"])
    if family.get("schema") in (amlogic.SCHEMA, extlinux.SCHEMA, special.SCHEMA):
        with deploy.safe.open_root(artifact_root) as root:
            _, manifest = deploy.safe.fingerprint(root, "manifest.json", limit=4 * 1024**2, keep=True)
        require(station._json_loads(manifest) == family, "artifact_root 原配 manifest 與 prepare 家族結果不同")
    boot = deploy.load(bundle["uboot"])
    driver = life.boot_driver(boot)
    view = life.customer_view(boot)
    if driver is uboot:
        boot = view
    elif driver is life.original_entry:
        boot = driver.validate_config(boot)
    require(view["uboot"]["pairing_sha256"] == config["pairing"]["sha256"]
            and view["uboot"]["qualification_sha256"] == bundle["uboot_qualification"]["sha256"],
            "U-Boot 配置未綁定本板配對與建置核定")
    deploy.checked_bytes(bundle["uboot_qualification"])
    require(view["kernel_release"] == prepared["kernel_release"], "U-Boot 不是原配核心")
    rendered = render_family(family, deploy.load(bundle["uboot_template"]), artifact_root, runtime_config=boot)
    if driver is uboot:
        uboot.validate_artifacts(rendered, artifact_root)
        rendered_transport = bind_transport(bundle, rendered, extraction)
    else:
        require(life.boot_driver(rendered) is driver, "家族與執行器 ABI 不同")
        extraction_ref = {"path": str(parent / prepared["extraction"]["path"]),
                          "sha256": prepared["extraction"]["sha256"]}
        rendered_transport = bind_runtime(bundle, rendered, extraction, extraction_ref, config)
    require(rendered_transport == boot, "固定引導配置與家族核對、傳輸映射後的完整範本不同")
    bundle["_prepared_boot"] = rendered
    expected = linux.validate_expected(deploy.load(bundle["linux_expected"]))
    require(expected["architecture"] == view["arch"] and expected["kernel_release"] == view["kernel_release"]
            and expected["root"]["uuid"] == prepared["root_uuid"] and expected["root"]["media_type"] == "MMC"
            and all(expected["root"][key] == value for key, value in contract["expected"].items()),
            "Linux 預期配置不是本板 eMMC 上的原配系統")
    deploy.validate_ssh(bundle["customer_ssh"], require_hosts=False)
    lifecycle = deploy.load(config["lifecycle"])
    life.validate_config(lifecycle, deploy.load(config["pairing"]), config["pairing"]["sha256"], contract, boot)
    if bundle["board"] in H618_BOARDS:
        require(driver is uboot and life.boot_driver(deploy.load(lifecycle["rescue_uboot"])) is uboot,
                "共用 H618 只接全新主線 U-Boot 客戶與 SD RAM 配置，不接 0845／SRAM 流程")
    bundle["_validation_config"] = config
    return bundle, boot, expected


@contextmanager
def resource_lock(config):
    """跨證據目錄使用相同資源鎖；不把排他鎖當作實體配對證明。"""
    try:
        deploy.new_directory(LOCK_ROOT)
    except FileExistsError:
        pass
    with ExitStack() as stack:
        root = stack.enter_context(deploy.safe.open_root(LOCK_ROOT))
        for value in sorted({config["hardware_id"], *config["resources"].values()}):
            name = hashlib.sha256(value.encode()).hexdigest() + ".lock"
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=root)
            stack.callback(os.close, fd)
            require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, "資源鎖不是唯一一般檔案")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class NativeRuntime:
    """所有實際操作都走固定的 UART、電源、部署與唯讀採樣入口。"""

    def execute(self, context, current, output, timeout):
        config, contract, bundle, boot, expected, request = context
        lifecycle, pairing = deploy.load(config["lifecycle"]), deploy.load(config["pairing"])
        stage = request["stage"]
        if stage == "recovery":
            require_recovery_safe(current)
        deadline = life.Deadline(timeout)
        output = deploy.new_directory(output)
        previous = current.get("boot_id") if current else None
        if stage in ("boot", "recovery"):
            if stage == "boot":
                publish_artifacts(bundle, boot)
            interrupted = current if current["status"] in ("running", "failed") else None
            return life.cycle(lifecycle, pairing, contract, bundle["image"], boot, expected,
                              bundle["customer_ssh"], output / "cycle", request, action=stage,
                              timeout=deadline.remaining(), previous_boot_id=previous,
                              interrupted=interrupted, origin_mode=current["phase"], root_binding=bundle.get("_root_binding"))
        resume = request.get("resume")
        mode = current["phase"] if resume else "customer" if stage == "smoke" else "rescue"
        observed = life.observe(lifecycle, pairing, contract,
                                lifecycle["rescue_expected"] if mode == "rescue" else expected,
                                contract["ssh"] if mode == "rescue" else bundle["customer_ssh"],
                                output / "observe", request, mode=mode, timeout=deadline.remaining(),
                                previous_boot_id=previous)
        fixed_contract = {**contract, "ssh": observed["ssh"]}
        if stage == "preflight" and mode == "rescue":
            result = deploy.preflight(fixed_contract, bundle["image"], output / "preflight", timeout=deadline.remaining())
        elif stage == "deploy":
            result = deploy.deploy(fixed_contract, bundle["image"], output / "deploy", timeout=deadline.remaining(),
                                   confirm_overwrite=True)
        else:
            if resume:
                inputs = deploy.verify_inputs(contract, bundle["image"], timeout=deadline.remaining())
            directory = deploy.new_directory(output / "linux")
            fixed = deploy.snapshot_ssh(observed["ssh"], directory)
            collection = linux.collect(ssh_config=fixed["path"], alias="bpi-lab",
                                       known_hosts=directory / "known_hosts", timeout=deadline.remaining(60))
            deploy.save(directory, "collection.json", deploy.encode(collection))
            result = linux.validate(collection, expected)
            if resume:
                require(result["ok"] is True, "續作目前的客戶 Linux 不符")
                result = {"schema": "bpi-lab-resume-observation-v1", "status": "verified", "linux": result, "inputs": inputs}
            result["linux_collection"] = {"path": str(directory / "collection.json"),
                                          "sha256": hashlib.sha256(deploy.encode(collection)).hexdigest()}
        after = life.observe(lifecycle, pairing, contract,
                             lifecycle["rescue_expected"] if mode == "rescue" else expected,
                             observed["ssh"], output / "after", request, mode=mode,
                             timeout=deadline.remaining(), previous_boot_id=observed["boot_id"])
        require(after["identity"]["root"] == observed["identity"]["root"], "階段執行期間根媒體改變")
        return {**result, "session": after}


class StateStore:
    """以媒體身分索引持久意圖；成功報告落盤前不得發布可續作狀態。"""

    def __init__(self, config):
        name = hashlib.sha256(config["resources"]["media"].encode()).hexdigest()
        self.name, self.pending = "state-" + name + ".json", "state-" + name + ".pending"

    def read(self):
        with deploy.safe.open_root(LOCK_ROOT) as root:
            try:
                _, blob = deploy.safe.fingerprint(root, self.name, limit=station.MAX_OUTPUT_BYTES, keep=True)
            except FileNotFoundError:
                try:
                    os.stat(self.pending, dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    require(False, "持久狀態遺失但仍有 pending；禁止新工作覆蓋隔離")
                return None
            value = station._json_loads(blob)
            require(type(value) is dict and value.get("schema") == "bpi-lab-backend-state-v1", "持久狀態格式不符")
            try:
                os.stat(self.pending, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                value["status"] = "failed"
            return value

    def validate_deploy_receipt(self, receipt):
        """預設核對 MMC 完整收據；其他媒體須由獨立子類別提供嚴格驗證。"""
        require(receipt.get("status") == "verified" and receipt.get("ok") is True,
                "不能以未完整部署收據清除遠端寫入隔離")
        deploy.core.validate_state(receipt["remote_state"], receipt["request"], final=True)

    def write(self, value):
        previous = self.read()
        unresolved = bool(previous and (previous.get("writer_unresolved") is True or
                          previous.get("stage") == "deploy" and previous.get("status") != "verified"))
        if value["stage"] == "deploy":
            unresolved = True
            if value["status"] == "verified":
                receipt = deploy.load(value["operation"])
                self.validate_deploy_receipt(receipt)
                unresolved = False
        value["writer_unresolved"] = unresolved
        temporary = self.name + ".new-" + secrets.token_hex(8)
        deploy.save(LOCK_ROOT, temporary, deploy.encode(value))
        with deploy.safe.open_root(LOCK_ROOT) as root:
            os.replace(temporary, self.name, src_dir_fd=root, dst_dir_fd=root)
            os.fsync(root)

    def publish(self, value, output, report):
        # 舊 pending 只允許經同工作的核定 recovery 取代，其他路徑已在 transition 阻擋。
        with deploy.safe.open_root(LOCK_ROOT) as root:
            try:
                os.unlink(self.pending, dir_fd=root)
            except FileNotFoundError:
                pass
        deploy.save(LOCK_ROOT, self.pending, deploy.encode(value))
        deploy.save(output, "report.json", deploy.encode(report))
        reference = {"path": str(output / "report.json"), "sha256": hashlib.sha256(deploy.encode(report)).hexdigest()}
        if not value.get("resume_guard"):
            value["report"] = reference
        else:
            value["resume_report"] = reference
        self.write(value)
        with deploy.safe.open_root(LOCK_ROOT) as root:
            os.unlink(self.pending, dir_fd=root)


def require_recovery_safe(current):
    require(current is not None and current.get("writer_unresolved") is not True
            and not (current.get("stage") == "deploy" and current.get("status") != "verified"),
            "部署未完整確認遠端 writer 已停止；禁止切電或自動救援，須人工核對遠端寫入狀態")


def transition(current, request):
    binding = {key: request[key] for key in session.BINDINGS}
    stage = request["stage"]
    if current is None:
        require(stage == "preflight" and "resume" not in request, "必須先以全新預檢建立狀態")
        return
    if current["binding"] != binding:
        require(stage == "preflight" and "resume" not in request and current["status"] == "verified"
                and current["stage"] == "recovery" and current["phase"] == "rescue",
                "前工作尚未完成救援；不可借用另一工作狀態")
        return
    if stage == "recovery" and "resume" not in request:
        require_recovery_safe(current)
        require(current["stage"] != "recovery" or current["status"] != "verified", "救援已完成；不可無條件重做")
        return
    require(current["status"] == "verified", "前操作未完整發布；只允許另行核定的 recovery")
    previous = deploy.load(current["report"])
    previous_request = {**request, "stage": current["stage"]}
    previous_request.pop("resume", None)
    station.validate_report(previous, previous_request)
    require(previous["status"] == "passed", "前階段沒有完整通過報告")
    if "resume" in request:
        resume = request["resume"]
        index = station.STAGES.index(resume["next_stage"])
        require(index > 0 and current["stage"] == station.STAGES[index - 1], "續作游標與本機已完成階段不同")
        references = resume["previous_reports"]
        require([item["stage"] for item in references] == list(station.STAGES[:index]), "續作歷史不是完整有序階段前綴")
        for reference in references:
            report = deploy.load({key: reference[key] for key in ("path", "sha256")})
            prior = {**request, "stage": reference["stage"]}
            prior.pop("resume")
            station.validate_report(report, prior)
            require(report["status"] == "passed", "續作歷史含未通過階段")
        wanted = "rescue" if resume["next_stage"] in ("deploy", "boot") else "customer"
        require(current["phase"] == wanted, "續作目前階段不在期望系統")
    else:
        require(station.STAGES.index(stage) == station.STAGES.index(current["stage"]) + 1,
                "階段順序錯誤或重複執行")


def validate_result(stage, result, request, contract, bundle, *, config=None, current=None):
    fixed = bundle.get("_validation_config") if config is None else config
    try:
        evidence.validate(stage, result, request, contract, bundle, fixed, current)
    except (KeyError, TypeError, IndexError, StopIteration) as exc:
        raise deploy.core.DeployError("原生證據缺項或巢狀格式錯誤；拒絕發布") from exc


def run_stage(config_path, config_sha256, request, *, runtime=None):
    station._validate_request(request)
    report = {"schema": "bpi-lab-stage-v1", **{key: request[key] for key in station.BINDINGS},
              "status": "blocked", "hardware_validated": False, "whole_backend_ready": False,
              "original_boot_chain_verified": False, "supported_stages": list(IMPLEMENTED), "needs_recovery": False}
    output, started, published, store, active = None, False, False, None, None
    intent_uncertain, synthetic = False, runtime is not None
    try:
        require(request["mode"] == "hardware", "此為硬體後端，不接受合成工作")
        require(request["boot_config_sha256"] == config_sha256, "工作未綁定本次完整後端設定")
        require(deploy.core.hash_value(request["work_key"]), "工作鍵不是 SHA-256")
        deploy.identifier(request["attempt_id"])
        config, contract, qualification = load_config({"path": str(config_path), "sha256": config_sha256})
        for key in ("station_id", "hardware_id", "test_version"):
            require(request[key] == config[key], "工作與核定設定不符：" + key)
        stage = request["stage"]
        require(stage in qualification["approved_stages"], "此階段未取得板級核定")
        bundle, boot, expected = selected_image(config, contract, request)
        suffix = ("-resume-" if "resume" in request else "-recovery-") + secrets.token_hex(8) \
            if "resume" in request or stage == "recovery" else ""
        output = deploy.path(config["output_root"]) / (request["work_key"] + "-" + request["attempt_id"] + "-" + stage + suffix)
        # 測試 runtime 永遠只能產生模擬證據，不能把替身升格為實板通過。
        runtime = NativeRuntime() if runtime is None else runtime
        with resource_lock(config):
            store = StateStore(config)
            current = store.read()
            transition(current, request)
            if stage == "boot":
                previous = deploy.load(current["operation"])
                validate_result("deploy", previous, {**request, "stage": "deploy"}, contract, bundle, config=config)
            output = deploy.new_directory(output)
            deploy.save(output, "request.json", deploy.encode(request))
            report["evidence_path"] = str(output)
            end = time.monotonic() + config["timeout_seconds"]
            def remaining():
                return deploy.core.deadline_check(end, time.monotonic)
            check_dependencies(config["dependencies"])
            active = {"schema": "bpi-lab-backend-state-v1", "binding": {key: request[key] for key in session.BINDINGS},
                      "status": "running", "stage": stage, "phase": current["phase"] if current else "rescue",
                      "boot_id": current.get("boot_id") if current else None, "output": str(output)}
            if not synthetic:
                intent_uncertain = True
                try:
                    store.write(active)
                except (ValueError, OSError, KeyError, TypeError):
                    # rename 可能已完成而 fsync 失敗；仍持鎖時確認，無法讀取則保守隔離。
                    try:
                        intent_uncertain = store.read() != current
                    except (ValueError, OSError, KeyError, TypeError):
                        pass
                    raise
                intent_uncertain = False
            started = True
            try:
                result = runtime.execute((config, contract, bundle, boot, expected, request), current, output / "operation", remaining())
                validate_result(stage, result, request, contract, bundle, config=config, current=current)
                deploy.save(output, "operation.json", deploy.encode(result))
                remaining()
                check_dependencies(config["dependencies"])
                if stage == "preflight":
                    report.update(media_identity_verified=True, backup_verified=True)
                elif stage == "deploy":
                    report.update(full_readback_verified=True, compressed_sha256_verified=True)
                elif stage == "boot":
                    report.update(customer_kernel_verified=True)
                elif stage == "recovery":
                    report.update(rescue_verified=True)
                else:
                    report.update(checks={item["check"]: True for item in result["checks"]},
                                  smoke_scope="linux-read-only", stress_tested=False)
                if "resume" in request:
                    report.update(resume_state_verified=True, resume_next_stage=request["resume"]["next_stage"])
                if synthetic:
                    report.update(reason="替身只驗證程式流程，不能宣稱硬體通過", test_only=True)
                else:
                    report.update(status="passed", hardware_validated=True)
                    observed = result["session"]
                    completed = {**active, "status": "verified", "phase": observed["mode"], "boot_id": observed["boot_id"],
                                 "operation": {"path": str(output / "operation.json"),
                                               "sha256": hashlib.sha256(deploy.encode(result)).hexdigest()}}
                    if "resume" in request:
                        completed.update(stage=current["stage"], report=current["report"], operation=current["operation"], resume_guard=True)
                    station.validate_report(report, request)
                    remaining()
                    store.publish(completed, output, report)
                    remaining()
                    published = True
            except BaseException:
                if not synthetic:
                    store.write({**active, "status": "failed"})
                raise
    except (ValueError, OSError, KeyError, TypeError, IndexError, TimeoutError) as exc:
        uncertain = intent_uncertain or started and not synthetic
        report.update(status="failed" if started or uncertain else "blocked", hardware_validated=False,
                      needs_recovery=bool(uncertain),
                      reason=str(exc) if isinstance(exc, deploy.core.DeployError) else
                      "必要契約、核定、原配證據或階段操作未通過；不得自動重試")
    station.validate_report(report, request)
    if output is not None and "evidence_path" in report and not published:
        try:
            try:
                deploy.save(output, "report.json", deploy.encode(report))
            except FileExistsError:
                deploy.save(output, "report.failure.json", deploy.encode(report))
        except (OSError, ValueError):
            # 操作失敗的隔離訊號仍須透過 stdout 回傳，不讓磁碟故障將它降成泛用 CLI 錯誤。
            report["report_persistence_failed"] = True
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="共用有界階段後端；stdin 接收站點工作 JSON")
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
        print(json.dumps({"schema": "bpi-lab-adapter-error-v1", "reason": "工作或 CLI 輸入無效"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
