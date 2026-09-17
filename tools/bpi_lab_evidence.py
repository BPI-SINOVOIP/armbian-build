#!/usr/bin/env python3
"""重播固定原生操作證據；不接受布林通過宣告代替身分、採樣與外部契約。"""

import hashlib
import re

if __package__:
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_session as session
    from . import bpi_lab_linux as linux
else:
    import bpi_lab_deploy as deploy
    import bpi_lab_session as session
    import bpi_lab_linux as linux

require = deploy.require
SCHEMAS = {"preflight": "bpi-lab-deploy-preflight-v1", "deploy": "bpi-h618-emmc-deploy-v1",
           "boot": "bpi-lab-lifecycle-result-v1", "recovery": "bpi-lab-lifecycle-result-v1",
           "smoke": "bpi-lab-linux-validation-v1"}


def validate_session(observed, mode, request, contract, bundle, config):
    require(type(observed) is dict and observed.get("schema") == "bpi-lab-session-v1"
            and observed.get("mode") == mode and observed.get("uart_verified") is True
            and observed.get("ssh_verified") is True and observed.get("hostkey_source") == "same-session-uart"
            and observed.get("binding") == {key: request[key] for key in session.BINDINGS},
            "操作缺少本階段同次 UART／SSH 證據")
    identity = observed.get("identity")
    require(type(identity) is dict and type(identity.get("nonce")) is str
            and re.fullmatch(r"[0-9a-f]{64}", identity["nonce"]), "缺少完整同次身分採樣")
    expected = (deploy.load(bundle["linux_expected"]) if mode == "customer"
                else deploy.load(config["lifecycle"])["rescue_expected"])
    session.validate_identity(identity, mode, contract, expected, identity["nonce"])
    require(identity["boot_id"] == observed["boot_id"], "UART 與操作 boot_id 不符")
    ssh = observed["ssh"]
    deploy.validate_ssh(ssh)
    wanted = bundle["customer_ssh"] if mode == "customer" else contract["ssh"]
    require(all(ssh[key] == wanted[key] for key in ("host", "port", "user", "identity")),
            "SSH 未綁定本次原配端點與金鑰")
    host = ssh["host"] if ssh["port"] == 22 else f"[{ssh['host']}]:{ssh['port']}"
    hosts = (host + " " + session.public_key(identity["host_key"]) + "\n").encode("ascii")
    require(deploy.checked_bytes(ssh["known_hosts"], 65536) == hosts, "SSH hostkey 不符同次 UART")
    return identity


def validate_collection(result, bundle, validation):
    collection = deploy.load(result["linux_collection"])
    actual = linux.validate(collection, deploy.load(bundle["linux_expected"]))
    require(actual["ok"] is True and actual["checks"] and all(row["status"] == "passed" for row in actual["checks"])
            and actual == validation, "Linux 結果不是完整原始採樣的重新驗證")
    identity, observation = result["session"]["identity"], collection["observation"]
    hosts = deploy.checked_bytes(result["session"]["ssh"]["known_hosts"], 65536)
    require(collection.get("alias") == "bpi-lab" and collection.get("known_hosts") ==
            {"bytes": len(hosts), "sha256": hashlib.sha256(hosts).hexdigest()}, "Linux 採樣未綁定本次 hostkey")
    require(observation["uname"]["value"]["machine"] == identity["machine"]
            and observation["uname"]["value"]["release"] == identity["kernel"]
            and observation["dt_compatible"]["value"] == identity["dt_compatible"],
            "Linux 與 UART 的核心、架構或 DT 不符")
    roots = [item for item in observation["mounts"]["value"] if item["mount_point"] == "/"]
    require(len(roots) == 1 and roots[0]["fs_type"] == identity["root"]["fs"], "Linux 與 UART 根掛載不符")
    for uart_key, linux_key in (("devnum", "major_minor"), ("sysfs", "sysfs_path"), ("parent", "parent_path"),
                                ("parent_devnum", "parent_major_minor"), ("uuid", "uuid")):
        require(identity["root"][uart_key] == observation["root"]["value"][linux_key], "Linux 與 UART 根身分不符")
    def key(item, uart):
        return tuple(item[name] for name in (("name", "cid", "type", "controller", "bytes", "sysfs", "devnum") if uart else
                     ("name", "cid", "type", "controller", "bytes", "sysfs_path", "major_minor")))
    require(sorted(key(item, True) for item in identity["media"]) ==
            sorted(key(item, False) for item in observation["media"]["value"]), "Linux 與 UART 雙媒體不符")


def validate_media(result, contract, bundle, *, final=False):
    proof = result["request"]
    require(proof["expected"] == contract["expected"]
            and proof["source"] == {key: bundle["image"][key] for key in ("raw", "compressed")}
            and proof["protected_sd"] == {key: contract["protected_sd"][key] for key in ("cid", "controller")}
            and proof["rescue_schema"] == contract["rescue"]["schema"]
            and proof["rescue_expected"] == {key: contract["rescue"][key] for key in ("kernel", "identity_sha256")}
            and proof["backup_manifest_sha256"] == contract["backup"]["sha256"],
            "媒體操作未綁定外部備份、來源、救援及雙媒體契約")
    state = result["remote_state"] if final else result["state"]
    deploy.core.validate_state(state, proof, final=final)
    require(state["sd_before"]["prefix"] == contract["sd_prefix"]
            and all(state["sd_before"]["identity"][key] == value for key, value in contract["protected_sd"].items()),
            "原始操作的 SD 前綴或身分與外部契約不同")
    if final:
        require(result.get("ok") is True and proof.get("confirm_overwrite") is True
                and proof.get("backup_verified") is True, "部署缺少明示覆寫及完整備份")
    else:
        require(result.get("schema") == SCHEMAS["preflight"] and result.get("status") == "verified"
                and state["bytes_written"] == state["attempted_end"] == 0, "唯讀預檢格式不符或含寫入")


def validate(stage, result, request, contract, bundle, config, current=None):
    require(type(config) is dict, "嚴格驗證需要固定 backend config")
    require(type(result) is dict and all(result.get(key, False) is False for key in ("simulated", "synthetic", "test_only")),
            "合成操作不得冒充原生證據")
    resume = request.get("resume")
    mode = ("rescue" if resume["next_stage"] in ("deploy", "boot") else "customer") if resume else (
        "customer" if stage in ("boot", "smoke") else "rescue")
    if resume:
        require(stage == "preflight" and current is not None and current["phase"] == mode,
                "續作階段、目前系統或下一階段不符")
        schema = SCHEMAS["preflight"] if mode == "rescue" else "bpi-lab-resume-observation-v1"
    else:
        schema = SCHEMAS[stage]
    require(result.get("schema") == schema, "原生操作 schema 與階段不符")
    require(result.get("action") == stage if stage in ("boot", "recovery") else "action" not in result,
            "原生操作 action 與階段不符")
    require(result.get("status") == ("passed" if stage == "smoke" else "verified"), "原生操作尚未完成")
    identity = validate_session(result.get("session"), mode, request, contract, bundle, config)
    if current and current.get("boot_id"):
        if not resume and stage in ("boot", "recovery"):
            require(identity["boot_id"] != current["boot_id"], "冷循環沒有新的 boot_id")
        else:
            require(identity["boot_id"] == current["boot_id"], "觀測或續作期間發生重啟")
            if current.get("operation"):
                prior = deploy.load(current["operation"])["session"]["identity"]
                require(all(prior[key] == identity[key] for key in ("root", "media", "host_key")),
                        "同次階段之間根、媒體或 hostkey 改變")
    if stage == "deploy":
        validate_media(result, contract, bundle, final=True)
    elif stage == "preflight" and mode == "rescue":
        validate_media(result, contract, bundle)
    elif stage == "recovery":
        require(result.get("rescue_verified") is True, "救援未完整通過")
        validate_media(result["rescue_proof"], contract, bundle)
    if stage == "smoke":
        validation = {key: value for key, value in result.items() if key not in ("session", "linux_collection")}
        names = [row["check"] for row in validation["checks"]]
        require(len(names) == len(set(names)), "短測檢查重複")
        validate_collection(result, bundle, validation)
    elif stage == "boot" or resume and mode == "customer":
        if stage == "boot":
            require(result.get("customer_kernel_verified") is True, "客戶引導未完整通過")
        validate_collection(result, bundle, result["linux"])
