#!/usr/bin/env python3
"""0845 的有界冷循環與引導；不部署、不登入、不讀取 secret、不建立 SSH。

此為獨立 runtime，不是已接入佇列的完整適配器。客戶引導止於 login；
救援止於 UART 核對的 RAM 根與 SD 前綴，不宣稱網路或整機就緒。
"""

from contextlib import closing, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import time

if __package__:
    from . import bpi_h618_artifacts as safe
    from . import bpi_h618_customer_boot as customer
    from . import bpi_h618_rescue_boot as rescue
else:
    import bpi_h618_artifacts as safe
    import bpi_h618_customer_boot as customer
    import bpi_h618_rescue_boot as rescue

require = safe.require
HARDWARE = "bpi-m4zero-0845"
POWER = {"name": "bpi-pw-1", "ip": "192.168.50.245", "mac": "EC:B9:31:24:F7:D1"}
LOCK_ROOT = Path("/var/tmp/bpi-lab-locks")
STATE_FILE = "h618-0845-lifecycle-state.json"
PUBLICATION_FILE = "h618-0845-lifecycle-publication.pending"
ACTIONS = ("cold-cycle", "boot-customer", "recover-normal", "recover-fault")
SSH_POLICY = {"hostkey_source": "same-session-uart", "strict_host_key_checking": True,
              "reuse_previous_session": False}
DEPENDENCIES = (
    "bpi_lab_h618_lifecycle.py", "bpi_h618_artifacts.py", "bpi_h618_customer_boot.py",
    "bpi_h618_rescue_boot.py", "bpi_lab_console.py", "bpi_h618_emmc_deploy.py",
    "bpi_h618_emmc_backup.py", "bpi_sram_lab_uart.py", "bpi_sram_uart.py",
    "bpi_sram_package.py", "bpi_sram_ddr_package.py", "bpi_sram_lab_package.py",
)


def _fields(value, fields):
    require(type(value) is dict and set(value) == set(fields), "設定欄位缺少或含未知欄位")


def _identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value),
            "識別符格式不符；不得放入自由文字或 secret")
    return value


def _path(value):
    require(isinstance(value, (str, Path)), "路徑型別不符")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "路徑須為無上層跳轉的絕對路徑")
    return path


def _sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "須提供固定 SHA-256")
    return value


def checked_bytes(reference, maximum=65536):
    """只讀一般檔案；拒絕符號連結、裝置與讀取期間變動。"""
    _fields(reference, ("path", "sha256"))
    path = _path(reference["path"])
    _sha(reference["sha256"])
    with safe.open_root(path.parent) as directory:
        digest, blob = safe.fingerprint(directory, path.name, limit=maximum, keep=True)
    require(digest["sha256"] == reference["sha256"], "固定檔案摘要不符")
    return blob


def _save(directory, name, value):
    blob = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    with safe.open_root(directory) as parent, safe.open_file(parent, name, create=True) as stream:
        stream.write(blob)


def _new_directory(path):
    path = _path(path)
    with safe.open_root(path.parent) as parent:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    return path


def load_config(path, sha256):
    """離線核對設定與固定輸入；不開 UART、不呼叫電源或 SSH。"""
    config = safe.parse_manifest(checked_bytes({"path": str(path), "sha256": sha256}))
    _fields(config, ("schema", "station_id", "hardware_id", "uart", "power", "pairing",
                     "authorization", "bridge", "rescue_inputs", "rescue_identity_sha256",
                     "components", "deploy_receipt", "dependencies", "timeout_seconds",
                     "off_seconds", "ssh_policy"))
    require(config["schema"] == "bpi-lab-h618-lifecycle-v1" and config["hardware_id"] == HARDWARE,
            "僅核定 0845 生命週期，不推論其他板")
    _identifier(config["station_id"])
    require(config["power"] == POWER, "電源必須明示 bpi-pw-1、.245 與核定 MAC")
    uart = config["uart"]
    _fields(uart, ("stable_path", "device", "baud"))
    require(uart["device"] == "/dev/ttyUSB0" and type(uart["baud"]) is int and uart["baud"] == 115200,
            "只核定 ttyUSB0／115200，明確拒絕 ttyUSB1")
    require(type(uart["stable_path"]) is str and re.fullmatch(
        r"/dev/serial/by-(?:id|path)/[A-Za-z0-9_:+.-]+", uart["stable_path"]), "須明示 UART 穩定路徑")
    pairing = config["pairing"]
    _fields(pairing, ("approved", "record", "hardware_id", "uart", "power", "emmc", "sd"))
    require(pairing["approved"] is True and pairing["hardware_id"] == HARDWARE
            and pairing["uart"] == uart and pairing["power"] == POWER
            and pairing["emmc"] == customer.EXPECTED and pairing["sd"] == customer.PROTECTED_SD,
            "配對核定未綁定本 UART、電源及兩片媒體")
    _identifier(pairing["record"])
    auth = config["authorization"]
    _fields(auth, ("record", "normal_shutdown", "fault_poweroff", "customer_boot_may_write_emmc"))
    _identifier(auth["record"])
    require(all(type(auth[key]) is bool for key in auth if key != "record"), "授權必須逐項明示布林值")
    require(type(config["timeout_seconds"]) is int and 60 <= config["timeout_seconds"] <= 1800,
            "總期限須為 60..1800 秒整數")
    require(type(config["off_seconds"]) is int and 10 <= config["off_seconds"] <= 60,
            "斷電間隔須為 10..60 秒整數")
    require(config["ssh_policy"] == SSH_POLICY
            and config["ssh_policy"]["strict_host_key_checking"] is True
            and config["ssh_policy"]["reuse_previous_session"] is False,
            "SSH 必須由本次 UART 重新綁定並嚴格核對；不接受舊設定")
    _fields(config["dependencies"], DEPENDENCIES)
    for name, digest in config["dependencies"].items():
        checked_bytes({"path": str(Path(__file__).absolute().parent / name), "sha256": digest}, 1024**2)
    bridge = checked_bytes(config["bridge"], 256 * 1024)
    require(hashlib.sha256(bridge).hexdigest() == rescue.BRIDGE_SHA, "橋接封包不是核定 A1 版本")
    # 後續只使用固定副本；既有清單解析器也只讀取此副本，避免校驗後重讀來源。
    rescue_blob = checked_bytes(config["rescue_inputs"])
    document = safe.parse_manifest(rescue_blob)
    require(type(document) is dict and type(document.get("inputs")) is list
            and len(document["inputs"]) == 4, "救援清單須恰有四個固定組件")
    _sha(config["rescue_identity_sha256"])
    components = safe.parse_manifest(checked_bytes(config["components"]))
    require(Path(config["deploy_receipt"]["path"]).name == "receipt.json", "只接受正式部署 receipt.json")
    receipt = safe.parse_manifest(checked_bytes(config["deploy_receipt"]))
    customer.validate_metadata(components, receipt)
    require(document.get("sd_prefix") == receipt["remote_state"]["sd_after"]["prefix"],
            "救援及部署收據的受保護 SD 前綴不同")
    return config, bridge, rescue_blob, components, receipt


class Deadline:
    def __init__(self, seconds, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.end = clock() + seconds

    def remaining(self, maximum=None):
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise TimeoutError("生命週期總期限已到，停止且不自動斷電或重試")
        return min(remaining, maximum) if maximum is not None else remaining

    def pause(self, seconds):
        require(self.remaining() > seconds, "總期限不足以完成必要斷電間隔")
        self.sleep(seconds)
        self.remaining()


class BoundedConsole(customer.DeadlineConsole):
    """所有 UART 交握、讀寫與 shell 共用同一絕對期限。"""

    def __init__(self, console, deadline):
        self.console, self.budget = console, deadline
        self.monotonic, self.deadline = deadline.clock, deadline.end

    def remaining(self):
        return self.budget.remaining()

    def send(self, data, timeout=10):
        result = self.console.send(data, timeout=self.budget.remaining(timeout))
        self.remaining()
        return result

    def run_shell(self, command, timeout=10):
        result = self.console.run_shell(command, timeout=self.budget.remaining(timeout))
        self.remaining()
        if command == "bpi-rescue inventory" and result.exitcode == 0:
            # 破碎盤點即停止，避免舊 rescue 的重試 sleep 超出共同期限。
            rescue.parse_inventory(result.output)
        return result

    def reset_input_buffer(self):
        end, count = min(self.deadline, self.monotonic() + 0.2), 0
        while self.monotonic() < end:
            self.console.timeout = min(0.01, end - self.monotonic())
            count += len(self.console.read(4096))
            require(count <= 65536, "交握前 UART 持續輸出，停止交接")
        self.remaining()


class NativeRuntime:
    """只有 run 明確選擇真實模式後，才會實際開啟 UART／呼叫 bpi-pw。"""

    clock = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def validate_uart(self, uart):
        stable, device = Path(uart["stable_path"]), Path(uart["device"])
        require(stable.resolve(strict=True) == device and not device.is_symlink(),
                "UART 穩定路徑目前不是 ttyUSB0；拒絕重新猜測配對")
        require(stat.S_ISCHR(device.stat().st_mode), "核定 UART 不是字元裝置")
        return device.stat().st_rdev

    @contextmanager
    def console(self, uart, output, deadline):
        expected = self.validate_uart(uart)
        with rescue.open_serial(uart["device"], uart["baud"], deadline.remaining(10)) as port:
            require(os.fstat(port.fileno()).st_rdev == expected and self.validate_uart(uart) == expected,
                    "UART 在開啟期間重新配對，拒絕傳送")
            with rescue.RecordedChannel(port, log_path=output / "uart.bin") as console:
                yield console

    def power(self, action, deadline):
        require(action in ("status", "off", "on"), "電源命令未核定")
        argv = ["bpi-pw", "--device", POWER["name"], action]
        end = deadline.clock() + deadline.remaining(60)
        buffers, code = {"stdout": bytearray(), "stderr": bytearray()}, None
        with closing(customer.deploy.backup.ssh_stream(argv, end, deadline.clock)) as events:
            for kind, value in events:
                deadline.remaining()
                require(code is None, "電源子程序退出後仍有輸出")
                if kind == "exit":
                    require(type(value) is int, "電源退出碼格式不符")
                    code = value
                else:
                    require(kind in buffers and isinstance(value, bytes), "電源回覆格式不符")
                    require(len(buffers[kind]) + len(value) <= 65536, "電源回覆超界")
                    buffers[kind].extend(value)
        # 不保存 stdout/stderr；錯誤回覆或 CLI 診斷可能含認證資料。
        require(code == 0, "電源命令失敗；不公開原始子程序診斷")
        return safe.parse_manifest(bytes(buffers["stdout"]))


class Journal:
    def __init__(self, output, report):
        self.output, self.report, self.number = output, report, 0

    def event(self, step, state):
        self.number += 1
        entry = {"sequence": self.number, "step": step, "state": state,
                 "session_id": self.report["session_id"], "unix": time.time()}
        _save(self.output, f"event-{self.number:03d}.json", entry)
        self.report["last_step"] = step


@contextmanager
def resource_lock(config, root=LOCK_ROOT):
    """沿用佇列的鎖目錄與 hardware 鍵；持有父佇列鎖時不可重入。"""
    try:
        _new_directory(root)
    except FileExistsError:
        pass
    descriptors = []
    with safe.open_root(root) as directory:
        info = os.fstat(directory)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, "鎖目錄必須為本使用者私有")
        keys = {"hardware:" + HARDWARE, "station:" + config["station_id"],
                "resource:" + config["uart"]["stable_path"], "resource:uart:/dev/ttyUSB0",
                "resource:power:bpi-pw-1", "resource:cid:" + customer.EXPECTED["cid"]}
        try:
            for key in sorted(keys):
                fd = os.open(hashlib.sha256(key.encode()).hexdigest() + ".lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
                descriptors.append(fd)
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                        and info.st_mode & 0o077 == 0 and info.st_nlink == 1, "鎖檔型別或權限不符")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            for fd in reversed(descriptors):
                os.close(fd)


def _read_state(root):
    with safe.open_root(root) as directory:
        try:
            os.stat(PUBLICATION_FILE, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            require(False, "最終結果發布未確認，須人工核對；不能依可見 rescue 狀態放行")
        try:
            _, blob = safe.fingerprint(directory, STATE_FILE, limit=65536, keep=True)
        except FileNotFoundError:
            return None
    return safe.parse_manifest(blob)


def _write_state(root, state):
    temporary = STATE_FILE + ".new"
    _save(root, temporary, state)
    with safe.open_root(root) as directory:
        os.replace(temporary, STATE_FILE, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)


def _publish_result(root, output, report, lease, deadline):
    """阻擋標記先落盤；任何不確定的最終發布都不能跨工作放行。"""
    def check():
        # 逾時後仍須保存失敗證據，但失敗狀態從不釋放給其他工作。
        if report["ok"]:
            deadline.remaining()

    check()
    _save(root, PUBLICATION_FILE, lease)
    check()
    report["finished_unix"] = time.time()
    _save(output, "report.json", report)
    check()
    digest = _report_sha(output)
    check()
    lease.update(status=("customer" if report["action"] == "boot-customer" else "rescue")
                 if report["ok"] else "failed", report_sha256=digest)
    _write_state(root, lease)
    check()
    with safe.open_root(root) as directory:
        check()
        # 刪除是最後一步，刻意不再 fsync：崩潰至多讓舊阻擋重現，不能形成
        # 「標記已移除但其後同步失敗」的放行缺口；之後不再做發布操作。
        os.unlink(PUBLICATION_FILE, dir_fd=directory)


def _shell(console, command, expected=None, timeout=15):
    result = console.run_shell(command, timeout=timeout)
    require(result.exitcode == 0, "UART 唯讀身分核對失敗")
    if expected is not None:
        require(result.output.strip() == expected, "UART 即時身分與核定值不同")
    return result.output


def _rescue_identity(console, config):
    _shell(console, "uname -r", rescue.KERNEL.encode())
    digest = _shell(console, "sha256sum /etc/bpi-rescue.json").split()
    require(len(digest) == 2 and digest[0].decode("ascii") == config["rescue_identity_sha256"]
            and digest[1] == b"/etc/bpi-rescue.json", "救援身分檔摘要不符")
    inventory = rescue.parse_inventory(_shell(console, "bpi-rescue inventory", timeout=30))
    rows = [line.split(" - ") for line in inventory["mountinfo"].splitlines()]
    roots = [row[1].split()[0] for row in rows if row[0].split()[4] == "/"]
    volatile = {"rootfs", "ramfs", "tmpfs", "proc", "sysfs", "devtmpfs", "devpts"}
    require(roots in (["rootfs"], ["ramfs"], ["tmpfs"])
            and all(row[1].split()[0] in volatile and not row[0].split()[2].startswith("179:")
                    and "/dev/mmcblk" not in row[1] for row in rows)
            and len(inventory["swaps"].splitlines()) <= 1, "救援根不在 RAM 或媒體仍掛載／啟用 swap")
    for cid, kind in ((rescue.SD_CID, "SD"), (customer.EXPECTED["cid"], "MMC")):
        matched = [item for item in inventory["devices"] if item.get("device/cid") == cid]
        require(len(matched) == 1 and matched[0].get("device/type") == kind, "救援媒體 CID 或型別不符")


def _customer_identity(console, components):
    _shell(console, "uname -r", components["kernel_release"].encode())
    _shell(console, "cat /sys/class/block/mmcblk*/device/cid", customer.EXPECTED["cid"].encode())
    root = _shell(console, "findmnt -n -o SOURCE /").strip().decode("ascii")
    require(re.fullmatch(r"/dev/mmcblk[0-9]+p1", root), "客戶根不是直接 MMC 第一分割區")
    _shell(console, "findmnt -n -o UUID /", components["root_uuid"].encode())
    device = Path(root).name[:-2]
    controller = _shell(console, "readlink -f /sys/class/block/" + device).strip().decode("ascii")
    require(controller.startswith(customer.EXPECTED["controller"] + "/mmc_host/"), "客戶根控制器不符")


def _shutdown(console, source, config, components, journal):
    require(config["authorization"]["normal_shutdown"] is True, "未核定正常關機")
    journal.event("shutdown", "started")
    if source == "rescue":
        _rescue_identity(console, config)
        command = "/bin/busybox poweroff -f\n"
    else:
        _customer_identity(console, components)
        command = "systemctl poweroff\n"
    for check in ("pgrep -x stress-ng", 'pgrep -f "^/root/bpi-lab-memtester"'):
        require(console.run_shell(check, timeout=10).exitcode == 1, "壓測仍執行或狀態不明，不關機")
    console.send(command)
    console.expect_regex(rb"(?:^|\r?\n)(?:\[\s*[0-9.]+\]\s*)?reboot: Power down\r?\n", timeout=180)
    journal.report["normal_shutdown_verified"] = True
    journal.event("shutdown", "verified")


def _power(runtime, action, expected, deadline, journal):
    journal.event("power-" + action, "started")
    reply = runtime.power(action, deadline)
    device = reply.get("device", {})
    require(reply.get("ok") is True and reply.get("verified") is True
            and device.get("identity_verified") is True and type(device.get("on")) is bool
            and (expected is None or device["on"] is expected)
            and all(device.get(key) == value for key, value in POWER.items()), "電源資產身分或回讀不符")
    deadline.remaining()
    journal.report["power"].append({"action": action, "on": device["on"], "identity_verified": True})
    journal.event("power-" + action, "verified")


def _sd_prefix(console, expected):
    # 只讀固定 SD；不掛載、不寫入，不接受呼叫者提供遠端程式。
    script = (
        "import os,stat,json,hashlib; from pathlib import Path; "
        "p=Path('/sys/class/block/mmcblk0'); "
        "assert (p/'device/cid').read_text().strip()==" + repr(rescue.SD_CID) + "; "
        "assert str(p.resolve()).startswith('/sys/devices/platform/soc/4020000.mmc/mmc_host/'); "
        "fd=os.open('/dev/mmcblk0',os.O_RDONLY|os.O_NOFOLLOW); "
        "assert stat.S_ISBLK(os.fstat(fd).st_mode); "
        "assert os.fstat(fd).st_rdev==os.makedev(*map(int,(p/'dev').read_text().split(':'))); "
        "f=os.fdopen(fd,'rb'); b=f.read(4194304); f.close(); "
        "print(json.dumps({'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}))"
    )
    observed = safe.parse_manifest(_shell(console, "python3 -B -c " + shlex.quote(script), timeout=30))
    require(observed == expected, "SD 前綴已變動或讀取不完整")


def _fault_proof(reference, config_sha256, session_id, simulated, state):
    require(reference is not None and state is not None, "故障返回需要本次持久失敗證據")
    record = safe.parse_manifest(checked_bytes(reference))
    require(record.get("schema") == "bpi-lab-h618-lifecycle-result-v1"
            and record.get("ok") is False and record.get("status") == "failed"
            and record.get("hardware_id") == HARDWARE and record.get("session_id") == session_id
            and record.get("config_sha256") == config_sha256 and record.get("simulated") is simulated
            and record.get("hardware_access_started") is True and record.get("finished_unix")
            and record.get("action") in ACTIONS and record.get("error_code")
            and state.get("status") == "failed" and state.get("report_path") == reference["path"]
            and state.get("report_sha256") == reference["sha256"], "失敗證據過期、錯配、未結束或未持久化")


def run(*, config_path, config_sha256, action, session_id, output, fault_evidence=None,
        runtime=None, simulated=False):
    """一次執行一個動作；替身只能配合 simulated=True，不提供自動重試或解鎖。

    cold-cycle／boot-customer 從已登入 RAM 救援開始；recover-normal 從已登入
    客戶 shell 開始；recover-fault 只接受目前同 session 的固定失敗報告。
    """
    require(type(simulated) is bool and (runtime is None if not simulated else runtime is not None),
            "真實模式禁止注入 runtime；模擬必須提供替身")
    require(not simulated or not isinstance(runtime, NativeRuntime), "模擬禁止使用真實 runtime")
    require(action in ACTIONS, "尚未接入首次登入、網路設定或 SSH 重綁定")
    _identifier(session_id)
    _sha(config_sha256)
    output = _new_directory(output)
    report = {"schema": "bpi-lab-h618-lifecycle-result-v1", "hardware_id": HARDWARE,
              "session_id": session_id, "config_sha256": config_sha256, "action": action,
              "simulated": simulated, "ok": False, "status": "blocked", "power": [],
              "hardware_access_started": False, "hardware_validated": False,
              "whole_adapter_ready": False, "system_verified": False, "login_observed": False,
              "first_login_initialized": False, "strict_ssh_verified": False,
              "network_verified": False, "recovery_verified": False,
              "normal_shutdown_verified": False, "forced_poweroff": False,
              "filesystem_integrity_after_powerloss_verified": False,
              "block_write_commands_sent": False, "original_boot_chain_verified": False,
              "uart_released": False, "started_unix": time.time(), "last_step": "preflight"}
    journal = Journal(output, report)
    runtime = NativeRuntime() if runtime is None else runtime
    started = runtime.clock()
    try:
        lock_root = _path(runtime.lock_root) if simulated else LOCK_ROOT
        require(not simulated or lock_root.resolve() != LOCK_ROOT.resolve(), "模擬不可使用真實資源鎖目錄")
        journal.event("preflight", "started")
        config, bridge, rescue_blob, components, receipt = load_config(config_path, config_sha256)
        deadline = Deadline(config["timeout_seconds"], runtime.clock, runtime.sleep)
        deadline.end = started + config["timeout_seconds"]
        deadline.remaining()
        if action != "recover-fault":
            require(fault_evidence is None, "正常動作不可附帶故障斷電證據")
            require(config["authorization"]["normal_shutdown"] is True, "未核定正常關機")
        else:
            require(config["authorization"]["fault_poweroff"] is True, "未另行核定故障斷電")
        if action == "boot-customer":
            require(config["authorization"]["customer_boot_may_write_emmc"] is True,
                    "客戶 Linux 可能寫入 eMMC，須另行核定；服務遮罩不是防寫")
        snapshot = safe.parse_manifest(rescue_blob)
        _save(output, "rescue-inputs.json", snapshot)
        inputs, prefix = rescue.input_manifest(output / "rescue-inputs.json")
        report["inputs"] = {key: config[key]["sha256"] for key in
                            ("bridge", "rescue_inputs", "components", "deploy_receipt")}
        report["pairing_record"] = config["pairing"]["record"]
        report["authorization_record"] = config["authorization"]["record"]
        report["image_sha256"] = components["image"]["compressed"]["sha256"]
        with resource_lock(config, lock_root):
            state = _read_state(lock_root)
            if state is not None and state.get("status") != "rescue":
                require(state.get("session_id") == session_id
                        and state.get("config_sha256") == config_sha256
                        and state.get("simulated") is simulated, "站點仍由其他工作占用，禁止接續")
                require((action == "recover-normal" and state.get("status") == "customer")
                        or (action == "recover-fault" and state.get("status") == "failed"),
                        "前次未安全結束，需核定恢復；不能直接開始下一套")
            if action == "recover-fault":
                _fault_proof(fault_evidence, config_sha256, session_id, simulated, state)
                report["fault_evidence"] = dict(fault_evidence)
            lease = {"session_id": session_id, "config_sha256": config_sha256,
                     "simulated": simulated, "status": "running", "report_path": str(output / "report.json")}
            _write_state(lock_root, lease)
            try:
                deadline.remaining()
                runtime.validate_uart(config["uart"])
                journal.event("uart", "started")
                report["hardware_access_started"] = True
                report["status"] = "running"
                with runtime.console(config["uart"], output, deadline) as raw_console:
                    console = BoundedConsole(raw_console, deadline)
                    _power(runtime, "status", None if action == "recover-fault" else True, deadline, journal)
                    if action == "recover-fault":
                        report["fault_poweroff_authorized"] = True
                        journal.event("fault-poweroff-authorized", "verified")
                    else:
                        _shutdown(console, "customer" if action == "recover-normal" else "rescue",
                                  config, components, journal)
                    _power(runtime, "off", False, deadline, journal)
                    report["forced_poweroff"] = action == "recover-fault"
                    off = runtime.clock()
                    _power(runtime, "status", False, deadline, journal)
                    deadline.pause(max(0, config["off_seconds"] - (runtime.clock() - off)))
                    report["off_seconds"] = runtime.clock() - off
                    console.reset_input_buffer()
                    _power(runtime, "on", True, deadline, journal)
                    console.expect_regex(rb"(?:^|\r?\n)BPI-SUP1 [^\r\n]*event=ready[^\r\n]*\r?\n", timeout=20)
                    journal.event("boot", "started")
                    commands, observed = [], None
                    try:
                        if action == "boot-customer":
                            observed = customer.boot(console, bridge, components, receipt, commands,
                                                     timeout=deadline.remaining(), monotonic=runtime.clock)
                            require(observed.get("login_observed") is True
                                    and observed.get("kernel_release_observed") == components["kernel_release"],
                                    "客戶核心或登入提示未確認")
                            report["login_observed"] = True
                            report["kernel_release_observed"] = observed["kernel_release_observed"]
                        else:
                            observed = rescue.boot(console, bridge, inputs, commands)
                            require(observed.get("independent_root_ram_verified") is True, "救援未確認 RAM 根")
                            _rescue_identity(console, config)
                            _sd_prefix(console, prefix)
                            report.update(ram_rescue_verified=True, sd_prefix_unchanged=True)
                    finally:
                        # 不複製任意 RX；只保存由既有工具產生的命令及布林結果。
                        _save(output, "boot-trace.json", {"records": len(commands), "completed": bool(observed),
                              "commands": [{key: item[key] for key in ("command", "ok") if key in item}
                                           for item in commands if "command" in item]})
                report["uart_released"] = True
                deadline.remaining()
                journal.event("complete", "verified")
                deadline.remaining()
                report.update(ok=True, status="login_observed" if action == "boot-customer" else "ram_rescue_verified")
            except (Exception, KeyboardInterrupt) as exc:
                report.update(status="failed", error_code=_error_code(exc), ok=False)
            finally:
                _publish_result(lock_root, output, report, lease, deadline)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(ok=False, status="failed" if report["hardware_access_started"] else "blocked",
                      error_code=_error_code(exc), finished_unix=time.time())
        # 已保存的成功報告不能掩蓋後續發布失敗；pending 標記保持阻擋。
        _save(output, "failure.json", report)
    return report


def _report_sha(output):
    with safe.open_root(output) as directory:
        digest, _ = safe.fingerprint(directory, "report.json", limit=65536)
    return digest["sha256"]


def _error_code(error):
    # 不印出例外文字，避免底層工具把 secret、命令或完整回覆帶入摘要。
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, TimeoutError):
        return "deadline"
    if isinstance(error, OSError):
        return "io-or-lock"
    return "validation-or-runtime"


def cold_cycle(**kwargs):
    return run(action="cold-cycle", **kwargs)


def boot_customer(**kwargs):
    return run(action="boot-customer", **kwargs)


def recover_normal(**kwargs):
    return run(action="recover-normal", **kwargs)


def recover_fault(**kwargs):
    return run(action="recover-fault", **kwargs)
