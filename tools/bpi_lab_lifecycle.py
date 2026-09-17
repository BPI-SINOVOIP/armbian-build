#!/usr/bin/env python3
"""已配對 mainline UART 的冷循環、原配引導及固定 SD 救援。

沒有 SRAM、FEL、ROM 或掃描裝置的捷徑；未知狀態只在獨立核定故障斷電
且提供同工作中斷意圖時允許 recovery。失敗不自動重試、不自動關閉電源。
"""

from contextlib import contextmanager
import copy
import hashlib
import os
from pathlib import Path
import re
import stat
import time

if __package__:
    from . import bpi_lab_console as console_api
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_linux as linux
    from . import bpi_lab_session as session
    from . import bpi_lab_station as station
    from . import bpi_lab_uboot as uboot
    from . import bpi_lab_original_entry as original_entry
    from . import bpi_lab_special_runtime as special_runtime
    from . import bpi_lab_realtek_rescue as realtek_rescue
    from . import bpi_lab_k3_runtime as k3_runtime
else:
    import bpi_lab_console as console_api
    import bpi_lab_deploy as deploy
    import bpi_lab_linux as linux
    import bpi_lab_session as session
    import bpi_lab_station as station
    import bpi_lab_uboot as uboot
    import bpi_lab_original_entry as original_entry
    import bpi_lab_special_runtime as special_runtime
    import bpi_lab_realtek_rescue as realtek_rescue
    import bpi_lab_k3_runtime as k3_runtime

require = deploy.require
ABI = "mainline-console-v2025.01"


def boot_driver(config):
    """只按固定 schema 選擇已知執行器，不接受外部命令或可呼叫物件。"""
    drivers = {uboot.SCHEMA: uboot, original_entry.SCHEMA: original_entry,
               special_runtime.SCHEMA: special_runtime, realtek_rescue.SCHEMA: realtek_rescue,
               k3_runtime.SCHEMA: k3_runtime}
    require(type(config) is dict and config.get("schema") in drivers, "未知客戶引導執行 ABI")
    return drivers[config["schema"]]


def customer_view(config):
    """僅投影生命週期核對欄位；執行仍須保留原配置並分派原執行器。"""
    driver = boot_driver(config)
    if driver in (special_runtime, realtek_rescue, original_entry, k3_runtime):
        return copy.deepcopy(driver.lifecycle_view(config))
    checked = driver.validate_config(config)
    return checked


def require_k3_api():
    require(all(callable(getattr(k3_runtime, name, None)) for name in
                ("validate_config", "lifecycle_view", "build_uboot_config", "validate_artifacts", "boot")),
            "K3 專用執行 API 尚未完整落盤；不可降級為共用或 original-entry 指令")


def bind_k3_rescue(original, rescue, config, pairing_sha256, contract):
    """兩個用途共用已核定韌體，但救援來源、RAM 身分與資格不可借用客戶根。"""
    require_k3_api()
    require(original["purpose"] == "original" and rescue["purpose"] == "sd-rescue"
            and original["board"] == rescue["board"] and original["board"] in k3_runtime.BOARDS
            and original["hardware_id"] == rescue["hardware_id"] == config["hardware_id"]
            and original["pairing"] == rescue["pairing"] and original["pairing"]["sha256"] == pairing_sha256
            and original["mmc"] == rescue["mmc"] == config["mmc"]
            and original["uboot"]["abi"] == rescue["uboot"]["abi"] == k3_runtime.ABI,
            "K3 客戶與救援未綁定本板、同配對、用途及 SDK MMC 編號")
    require(original["qualification"] != rescue["qualification"]
            and rescue["qualification"] == config["rescue_qualification"], "K3 SD RAM 救援缺少獨立資格")
    customer_q, rescue_q = (deploy.load(item["qualification"]) for item in (original, rescue))
    for key in ("binary", "config"):
        require(customer_q["firmware"][key] == rescue_q["firmware"][key],
                "K3 客戶與 SD RAM 救援不是同份核定 U-Boot 建置")
        deploy.checked_bytes(customer_q["firmware"][key], 64 * 1024**2)
    manifest = deploy.load(rescue["components"]["manifest"])
    require(manifest.get("schema") == k3_runtime.RESCUE_SCHEMA
            and manifest.get("rescue") == contract["rescue"] and manifest.get("sd_prefix") == contract["sd_prefix"],
            "K3 固定 SD／RAM 救援清單未綁定部署身分及受保護前綴")


class Deadline:
    def __init__(self, seconds, clock=time.monotonic, sleep=time.sleep):
        require(type(seconds) in (int, float) and 0 < seconds <= 86400, "生命週期期限無效")
        self.clock, self.sleep, self.end = clock, sleep, clock() + seconds

    def remaining(self, maximum=None):
        remaining = deploy.core.deadline_check(self.end, self.clock)
        return min(remaining, maximum) if maximum is not None else remaining

    def pause(self, seconds):
        require(self.remaining() > seconds, "期限不足以完成斷電間隔")
        self.sleep(seconds)
        self.remaining()


def literal(value, maximum=256):
    require(type(value) is str and 0 < len(value) <= maximum and all(32 <= ord(c) < 127 for c in value),
            "提示須為明示、有界且無控制字元的 ASCII 字串")
    return value


def password(reference):
    blob = deploy.checked_bytes(reference, 1024).removesuffix(b"\n")
    location = deploy.path(reference["path"])
    with deploy.safe.open_root(location.parent) as root, deploy.safe.open_file(root, location.name) as stream:
        info = os.fstat(stream.fileno())
        require(info.st_uid == os.geteuid() and info.st_nlink == 1 and info.st_mode & 0o077 == 0,
                "登入憑證必須由本使用者持有、私有且只有一個連結")
    require(1 <= len(blob) <= 256 and all(32 <= c < 127 for c in blob), "登入憑證格式不符")
    return blob


def validate_config(config, pairing, pairing_sha256, contract, customer):
    deploy.fields(config, "schema abi hardware_id pairing_sha256 uart_device power_program power_dependencies "
                  "autoboot login shutdown_marker off_seconds authorization mmc rescue_uboot "
                  "rescue_qualification rescue_artifact_root rescue_expected ssh_setup")
    require(config["schema"] == "bpi-lab-lifecycle-v1" and config["abi"] == ABI
            and config["hardware_id"] == contract["hardware_id"] == pairing["hardware_id"]
            and config["pairing_sha256"] == pairing_sha256 and pairing["approved"] is True,
            "生命週期未綁定已核定板號、ABI 與配對")
    require(type(config["uart_device"]) is str and re.fullmatch(r"/dev/tty(?:USB|ACM|S|AMA)[0-9]+", config["uart_device"]),
            "須明示目前配對的 UART 設備；不探索或猜測")
    require(deploy.path(config["power_program"]["path"]).name == "bpi-pw", "只接入固定 bpi-pw 電源協定")
    deploy.checked_bytes(config["power_program"], 4 * 1024**2)
    require(type(config["power_dependencies"]) is list and len(config["power_dependencies"]) <= 64,
            "電源相依項清單無效")
    for reference in config["power_dependencies"]:
        deploy.checked_bytes(reference, 16 * 1024**2)
    deploy.fields(config["autoboot"], "stop_text stop_key_hex")
    literal(config["autoboot"]["stop_text"])
    key = config["autoboot"]["stop_key_hex"]
    require(type(key) is str and re.fullmatch(r"(?:[0-9a-f]{2}){1,16}", key), "停止 autoboot 按鍵須為 1..16 位元組")
    deploy.fields(config["login"], "customer rescue")
    for login in config["login"].values():
        require(type(login) is dict and login.get("kind") in ("root-shell", "password", "initial-setup"), "未知登入 ABI")
        deploy.fields(login, "kind shell_prompt" if login["kind"] == "root-shell" else
                      "kind shell_prompt login_prompt password_prompt username password" +
                      (" new_password skip_user_creation" if login["kind"] == "initial-setup" else ""))
        literal(login["shell_prompt"])
        if login["kind"] != "root-shell":
            require(login["username"] == "root", "此受限實作只接受已存在的 root 帳號")
            literal(login["login_prompt"])
            literal(login["password_prompt"])
            password(login["password"])
        if login["kind"] == "initial-setup":
            password(login["new_password"])
            require(type(login["skip_user_creation"]) is bool, "首次登入略過帳戶建立必須明示布林值")
    literal(config["shutdown_marker"])
    require(type(config["off_seconds"]) is int and 10 <= config["off_seconds"] <= 60, "斷電間隔須為 10..60 秒")
    auth = config["authorization"]
    deploy.fields(auth, "record normal_shutdown cold_cycle customer_boot_may_write_emmc fault_poweroff "
                  "firstboot_account_changes install_test_ssh_key")
    deploy.identifier(auth["record"])
    require(all(type(auth[key]) is bool for key in auth if key != "record")
            and auth["normal_shutdown"] and auth["cold_cycle"] and auth["customer_boot_may_write_emmc"],
            "缺少正常關機、冷循環或客戶引導可能寫入 eMMC 的授權")
    require(config["login"]["rescue"]["kind"] != "initial-setup", "救援不接受持久帳戶初始化")
    require(config["login"]["customer"]["kind"] != "initial-setup" or auth["firstboot_account_changes"],
            "首次帳戶初始化尚未明確授權")
    deploy.fields(config["ssh_setup"], "customer rescue")
    for setup in config["ssh_setup"].values():
        session.validate_setup(setup, auth["install_test_ssh_key"])
    deploy.fields(config["mmc"], "emmc sd")
    require(all(type(v) is int and 0 <= v <= 255 for v in config["mmc"].values()),
            "須由配對明示 eMMC 與 SD U-Boot 編號")
    original = customer
    customer = customer_view(original)
    require(customer["uboot"]["pairing_sha256"] == pairing_sha256, "客戶 U-Boot 未綁定本配對")
    if boot_driver(original) in (original_entry, k3_runtime):
        require(original["mmc"] == config["mmc"] and original["hardware_id"] == config["hardware_id"],
                "原入口雙媒體編號或板號與生命週期不同")
    if boot_driver(original) is k3_runtime:
        require_k3_api()
        require(original["purpose"] == "original", "K3 客戶引導不得使用 SD RAM 救援用途")
    if boot_driver(original) is special_runtime:
        require(original["execution"]["transport"]["media"] == "emmc", "客戶原配載荷必須來自配對 eMMC")
    if customer["source"]["type"] == "mmc":
        require(customer["source"]["device"] == config["mmc"]["emmc"], "客戶載入來源不是已配對 eMMC")
    rescue_config = deploy.load(config["rescue_uboot"])
    rescue_driver = boot_driver(rescue_config)
    rescue = customer_view(rescue_config)
    require(rescue_driver in (uboot, special_runtime, realtek_rescue, k3_runtime), "固定 RAM 救援不接受客戶原入口腳本")
    if boot_driver(original) is k3_runtime:
        require(rescue_driver is k3_runtime, "K3 救援必須沿用專用 vendor ABI，不可假定主線救援相容")
        bind_k3_rescue(original, rescue_config, config, pairing_sha256, contract)
        k3_runtime.validate_artifacts(original, deploy.path(original["components"]["artifact_root"]))
    elif customer["uboot"]["abi"] == special_runtime.VENDOR_ABI:
        require(rescue_driver is realtek_rescue and rescue["uboot"]["abi"] == special_runtime.VENDOR_ABI,
                "缺少已核定的 realtek-lab-v1 固定 SD RAM 救援入口；不能假定 mainline 救援 ABI 相容")
        require(deploy.load(rescue_config["platform"]) == original
                and rescue["source"]["type"] == "vendor-sd" and config["mmc"]["sd"] == 0
                and rescue_config["sd"]["prefix"] == contract["sd_prefix"],
                "Realtek 救援未綁定同份 vendor 配置、獨立 SD 0 或受保護前綴")
        identity = deploy.load(rescue_config["identity"])
        require({**identity, "identity_sha256": rescue_config["identity"]["sha256"]} == contract["rescue"],
                "Realtek 救援 RAM 身分與部署契約不符")
    else:
        require(rescue_driver not in (realtek_rescue, k3_runtime) and config["mmc"]["emmc"] != config["mmc"]["sd"]
                and rescue["source"]["type"] == "mmc", "主線救援須使用不同的已配對 MMC 編號")
    deploy.checked_bytes(config["rescue_qualification"])
    require(rescue["source"]["device"] == config["mmc"]["sd"]
            and rescue["uboot"]["pairing_sha256"] == pairing_sha256
            and rescue["uboot"]["qualification_sha256"] == config["rescue_qualification"]["sha256"]
            and rescue["kernel_release"] == contract["rescue"]["kernel"], "救援配置不是固定 SD 上的已核定核心")
    require(rescue["files"]["initrd"] is not None, "RAM 救援必須有固定 initrd")
    roots = [arg for arg in rescue["bootargs"] if arg.startswith("root=")]
    require(roots in ([], ["root=/dev/ram0"]), "救援 bootargs 不得指定任何持久根媒體")
    rescue_driver.validate_artifacts(rescue_config, deploy.path(config["rescue_artifact_root"]))
    deploy.fields(config["rescue_expected"], "architecture dt_compatible")
    require(config["rescue_expected"]["architecture"] == rescue["arch"]
            and type(config["rescue_expected"]["dt_compatible"]) is list
            and config["rescue_expected"]["dt_compatible"]
            and all(type(value) is str and 0 < len(value) <= 128 for value in config["rescue_expected"]["dt_compatible"]),
            "救援架構或 DT compatible 尚未配對")
    return rescue if rescue_driver is uboot else rescue_config


class BoundedConsole:
    def __init__(self, console, deadline):
        self.console, self.deadline = console, deadline

    def send(self, data, timeout=10, **kwargs):
        result = self.console.send(data, timeout=self.deadline.remaining(timeout), **kwargs)
        self.deadline.remaining()
        return result

    def expect_regex(self, pattern, timeout=30):
        result = self.console.expect_regex(pattern, timeout=self.deadline.remaining(timeout))
        self.deadline.remaining()
        return result

    def expect_literal(self, value, timeout=30):
        return self.expect_regex(re.escape(value.encode() if isinstance(value, str) else value), timeout)

    def run_shell(self, command, timeout=45):
        result = self.console.run_shell(command, timeout=self.deadline.remaining(timeout))
        self.deadline.remaining()
        return result


class NativeRuntime:
    clock = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    @contextmanager
    def console(self, config, pairing, output, deadline):
        device, stable = Path(config["uart_device"]), Path(pairing["uart"]["stable_path"])
        def identity():
            require(stable.resolve(strict=True) == device and not device.is_symlink(), "UART 穩定路徑與配對設備不同")
            info = device.stat(follow_symlinks=False)
            require(stat.S_ISCHR(info.st_mode), "UART 不是字元設備")
            return info.st_rdev
        expected = identity()
        with console_api.uart.open_serial(str(device), pairing["uart"]["baud"], deadline.remaining(10)) as port:
            require(os.fstat(port.fileno()).st_rdev == expected == identity(), "UART 在開啟期間重新配對")
            with console_api.ConsoleSession(port, log_path=output / "uart.bin") as console:
                yield BoundedConsole(console, deadline)

    def drain(self, console, deadline):
        count, end = 0, deadline.clock() + deadline.remaining(0.2)
        while deadline.clock() < end:
            console.console.timeout = 0.01
            data = console.console.read(4096)
            count += len(data)
            require(count <= 65536, "斷電後 UART 輸出未停止")
        deadline.remaining()

    def power(self, config, pairing, action, deadline):
        require(action in ("status", "off", "on"), "未知電源動作")
        for reference in config["power_dependencies"]:
            deploy.checked_bytes(reference, 16 * 1024**2)
        program = config["power_program"]
        adapter = {"argv": [program["path"], "--device", pairing["power"]["name"], action],
                   "sha256": program["sha256"]}
        out, _, problem = station._external(adapter, b"", deadline.remaining(60))
        require(problem is None, "固定電源程式未成功；不公開認證或子程序診斷")
        return station._json_loads(out)


def login(console, config, deadline, *, existing=False, initialize_authorized=False):
    if existing:
        console.send(b"\n", timeout=deadline.remaining(10))
    elif config["kind"] != "root-shell":
        initial = config["kind"] == "initial-setup"
        require(not initial or initialize_authorized is True, "首次改密碼及帳戶變更未授權")
        console.expect_literal(config["login_prompt"], timeout=deadline.remaining(180))
        console.send(config["username"] + "\n", timeout=deadline.remaining(10))
        old_sent = current_sent = new_sent = repeated = selected = skipped = False
        shell = re.escape(config["shell_prompt"].encode())
        pattern = (rb"(?:^|\r?\n)(?:" + shell + rb"$|" + re.escape(config["password_prompt"].encode()) +
                   rb"|Current password:|\(current\) UNIX password:|Create root password:|New password:|New UNIX password:|"
                   rb"Repeat root password:|Retype new password:|Retype new UNIX password:|"
                   rb"2\) [^\r\n]+\r?\n|Please provide a username[^\r\n]*: ?|Login incorrect|Authentication failure)")
        for _ in range(12):
            found = console.expect_regex(pattern, timeout=deadline.remaining(90))
            token = found.matched.strip()
            if token == config["shell_prompt"].strip().encode():
                require(not initial or new_sent and repeated, "首次改密碼未完成兩次確認")
                return {"initialized": initial, "user_creation_skipped": skipped}
            if token == config["password_prompt"].strip().encode():
                require(not old_sent and not new_sent, "重複登入密碼提示；不猜測、不重送")
                value, old_sent = password(config["password"]), True
            elif token in (b"Current password:", b"(current) UNIX password:"):
                require(initial and not current_sent and not new_sent, "未核定或重複舊密碼確認")
                value, current_sent = password(config["password"]), True
            elif token.startswith((b"Create root", b"New password", b"New UNIX")):
                require(initial and not new_sent, "未核定或重複新密碼提示")
                value, new_sent = password(config["new_password"]), True
            elif token.startswith((b"Repeat root", b"Retype new")):
                require(initial and new_sent and not repeated, "新密碼確認順序不符")
                value, repeated = password(config["new_password"]), True
            elif token.startswith(b"2)"):
                require(initial and repeated and not selected and b"1) bash" in found.before,
                        "首次 shell 選擇不在核定流程")
                console.send(b"1\n", timeout=deadline.remaining(10))
                selected = True
                continue
            elif token.startswith(b"Please provide"):
                require(initial and repeated and config["skip_user_creation"] and not skipped,
                        "未授權或重複略過一般帳戶建立")
                console.send(b"\x03", timeout=deadline.remaining(10))
                skipped = True
                continue
            else:
                raise deploy.core.DeployError("登入失敗；不重試或猜測帳密")
            console.send(value + b"\n", timeout=deadline.remaining(10), secret=True)
        raise deploy.core.DeployError("首次登入交握次數超界")
    console.expect_regex(rb"(?:^|\r?\n)" + re.escape(config["shell_prompt"].encode()) + rb"$",
                         timeout=deadline.remaining(180))
    return {"initialized": False, "user_creation_skipped": False}


def power(runtime, config, pairing, action, expected, deadline, events):
    reply = runtime.power(config, pairing, action, deadline)
    require(type(reply) is dict and reply.get("ok") is True and reply.get("verified") is True, "電源操作沒有完成回讀")
    device = reply.get("device", {})
    require(type(device) is dict and device.get("identity_verified") is True and type(device.get("on")) is bool
            and (expected is None or device["on"] is expected)
            and all(device.get(key) == pairing["power"][key] for key in ("name", "ip", "mac")), "電源資產或回讀狀態不符")
    events.append({"action": action, "on": device["on"], "identity_verified": True})
    deadline.remaining()


def observe(config, pairing, contract, expected, ssh, output, request, *, mode, timeout,
            previous_boot_id=None, runtime=None):
    """不切電；重新以同次 UART 建立 SSH，可供預檢、短測與續作核對。"""
    output = deploy.new_directory(output)
    runtime = NativeRuntime() if runtime is None else runtime
    deadline = Deadline(timeout, runtime.clock, runtime.sleep)
    with runtime.console(config, pairing, output, deadline) as console:
        login(console, config["login"][mode], deadline, existing=True)
        result = session.establish(console, mode, contract, expected, ssh, output / "session", deadline,
                                   request, previous_boot_id=previous_boot_id, setup=config["ssh_setup"][mode],
                                   key_authorized=config["authorization"]["install_test_ssh_key"])
    return result


def cycle(config, pairing, contract, source, customer, expected, customer_ssh, output, request, *, action,
          timeout, previous_boot_id=None, interrupted=None, origin_mode=None, runtime=None, root_binding=None):
    require(action in ("boot", "recovery"), "生命週期只接受 boot 或 recovery")
    rescue = validate_config(config, pairing, config["pairing_sha256"], contract, customer)
    if action == "boot":
        labels = [arg[11:] for arg in customer_view(customer)["bootargs"] if arg.startswith("root=LABEL=")]
        if labels:
            require(len(labels) == 1 and root_binding is not None and root_binding.get("method") == "label"
                    and root_binding.get("label") == labels[0], "LABEL 引導缺少原配根綁定；不可略過跨媒體盤點")
    if interrupted is not None:
        require(action == "recovery" and config["authorization"]["fault_poweroff"] is True
                and interrupted.get("status") in ("running", "failed")
                and interrupted.get("stage") in station.STAGES and interrupted["stage"] != "deploy"
                and interrupted.get("writer_unresolved") is not True
                and interrupted.get("binding") == {key: request[key] for key in session.BINDINGS},
                "故障斷電缺少獨立授權、同工作意圖，或部署 writer 尚未停止核對；禁止切電")
    output = deploy.new_directory(output)
    simulated = runtime is not None
    runtime = NativeRuntime() if runtime is None else runtime
    deadline = Deadline(timeout, runtime.clock, runtime.sleep)
    report = {"schema": "bpi-lab-lifecycle-result-v1", "action": action, "status": "running",
              "simulated": simulated, "power": [], "forced_poweroff": interrupted is not None,
              "whole_image_boot_chain_verified": False}
    target = "customer" if action == "boot" else "rescue"
    origin = origin_mode or ("rescue" if action == "boot" else "customer")
    require(origin in ("rescue", "customer") and (action != "boot" or origin == "rescue"), "來源階段狀態不符")
    origin_expected = config["rescue_expected"] if origin == "rescue" else expected
    selected = customer if target == "customer" else rescue
    try:
        with runtime.console(config, pairing, output, deadline) as console:
            if interrupted is None:
                login(console, config["login"][origin], deadline, existing=True)
                before = session.uart_identity(console, origin, contract, origin_expected, deadline,
                                                host_key_path=config["ssh_setup"][origin]["host_key_path"])
                require(previous_boot_id is not None and before["boot_id"] == previous_boot_id,
                        "關機前開機身分與前一階段不符")
                if action == "boot":
                    report["root_uuid_preboot"] = session.check_boot_root(console, expected, before, deadline,
                                                                         root_binding=root_binding)
                power(runtime, config, pairing, "status", True, deadline, report["power"])
                command = "/bin/busybox poweroff -f\n" if origin == "rescue" else "systemctl poweroff\n"
                console.send(command, timeout=deadline.remaining(10))
                console.expect_regex(rb"(?:^|\r?\n)(?:\[\s*[0-9.]+\]\s*)?" +
                                     re.escape(config["shutdown_marker"].encode()) + rb"\r?\n",
                                     timeout=deadline.remaining(180))
                report["normal_shutdown_verified"] = True
            else:
                power(runtime, config, pairing, "status", None, deadline, report["power"])
            deploy.save(output, "shutdown.json", deploy.encode(report))
            power(runtime, config, pairing, "off", False, deadline, report["power"])
            deadline.pause(config["off_seconds"])
            power(runtime, config, pairing, "status", False, deadline, report["power"])
            runtime.drain(console, deadline)
            power(runtime, config, pairing, "on", True, deadline, report["power"])
            console.expect_literal(config["autoboot"]["stop_text"], timeout=deadline.remaining(60))
            console.send(bytes.fromhex(config["autoboot"]["stop_key_hex"]), timeout=deadline.remaining(5))
            steps = []
            try:
                marker = boot_driver(selected).boot(console, selected, steps,
                                                    timeout=deadline.remaining(1800), monotonic=deadline.clock)
            finally:
                deploy.save(output, "uboot-steps.json", deploy.encode(steps))
            report["login"] = login(console, config["login"][target], deadline,
                                     initialize_authorized=config["authorization"]["firstboot_account_changes"])
            current = session.establish(console, target, contract,
                                        expected if target == "customer" else config["rescue_expected"],
                                        customer_ssh if target == "customer" else contract["ssh"],
                                        output / "session", deadline, request, setup=config["ssh_setup"][target],
                                        key_authorized=config["authorization"]["install_test_ssh_key"])
            require(previous_boot_id is None or current["boot_id"] != previous_boot_id, "冷循環後 boot_id 未改變")
            if target == "rescue":
                fixed_contract = copy.deepcopy(contract)
                fixed_contract["ssh"] = current["ssh"]
                proof = deploy.preflight(fixed_contract, source, output / "rescue-preflight", timeout=deadline.remaining())
                report["rescue_proof"] = proof
            else:
                config_dir = deploy.new_directory(output / "linux")
                fixed = deploy.snapshot_ssh(current["ssh"], config_dir)
                collection = linux.collect(ssh_config=fixed["path"], alias="bpi-lab",
                                           known_hosts=config_dir / "known_hosts", timeout=deadline.remaining(60))
                deploy.save(config_dir, "collection.json", deploy.encode(collection))
                validation = linux.validate(collection, expected)
                require(validation["ok"] is True, "客戶 Linux 預檢未完整通過")
                report["linux"] = validation
                report["linux_collection"] = {"path": str(config_dir / "collection.json"),
                                               "sha256": hashlib.sha256(deploy.encode(collection)).hexdigest()}
            after = session.uart_identity(console, target, contract,
                                          expected if target == "customer" else config["rescue_expected"], deadline,
                                          host_key_path=config["ssh_setup"][target]["host_key_path"])
            require(after["boot_id"] == current["boot_id"] and after["root"] == current["identity"]["root"],
                    "後置核對期間重啟或根媒體改變")
            deadline.remaining()
            report.update(status="verified", session=current, uboot=marker,
                          customer_kernel_verified=target == "customer", rescue_verified=target == "rescue")
        deploy.save(output, "lifecycle.json", deploy.encode(report))
        return report
    except BaseException:
        report.update(status="failed", reason="生命週期中斷；不重試、不自動復原或斷電")
        deploy.save(output, "lifecycle.partial.json", deploy.encode(report))
        raise
