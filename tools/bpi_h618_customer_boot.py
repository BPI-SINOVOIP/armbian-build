#!/usr/bin/env python3
"""以已核定 A1 橋接一次性引導 EMAC 原配系統；不控制電源、不發送媒體寫入命令。"""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

if __package__:
    from . import bpi_h618_artifacts as safe
    from . import bpi_h618_emmc_deploy as deploy
    from . import bpi_h618_rescue_boot as rescue
else:
    import bpi_h618_artifacts as safe
    import bpi_h618_emmc_deploy as deploy
    import bpi_h618_rescue_boot as rescue


EXPECTED = {"cid": "d629034339413535311299942ee08c07", "bytes": 31289507840,
            "controller": "/sys/devices/platform/soc/4022000.mmc"}
PROTECTED_SD = {"cid": rescue.SD_CID, "controller": "/sys/devices/platform/soc/4020000.mmc"}
LOADS = (("kernel", 0x40080000, 64 * 1024**2), ("dtb", 0x4FA00000, 65536),
         ("overlay", 0x45000000, 65536), ("initrd", 0x4FF00000, 64 * 1024**2))
DTB = "sun50i-h618-bananapi-m4-zero-emac.dtb"
OVERLAY = "sun50i-h616-bananapi-m4-zero-emac-sdio-wifi-bt.dtbo"
MASKS = ("armbian-resize-filesystem.service", "apt-daily.service", "apt-daily-upgrade.service")
LIMITS = ("僅觀察原配核心版本與 login，不登入、不驗證根媒體實際 CID 或系統功能。"
          "使用共用 A1 SPL／DDR／TF-A／U-Boot，FIT LBA 2048，未驗證客戶原開機鏈。"
          "RAM DTB 停用 SD 控制器，未測原生 SD；不執行原 boot.scr 或 fixup。"
          "服務遮罩不是硬體防寫；客戶 Linux 啟動可能寫入 eMMC 根系統。")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def digest_record(item, maximum):
    require(type(item) is dict and set(item) == {"bytes", "sha256"}
            and type(item["bytes"]) is int and 0 < item["bytes"] <= maximum
            and matches(r"[0-9a-f]{64}", item["sha256"]), "長度或 SHA-256 格式不符")


def bootargs_for(document):
    """只轉譯已審核的原設定；不匯入或執行原環境內容。"""
    env = document.get("original_env")
    require(type(env) is dict and all(isinstance(k, str) and isinstance(v, str)
                                     for k, v in env.items()), "原環境必須是純字串物件")
    require(not any(k.startswith("param_") for k in env), "尚不支援 param_*，不能跳過其 fixup")
    defaults = {"verbosity": "1", "bootlogo": "false", "console": "both", "disp_mode": "1920x1080p60",
                "overlay_prefix": "sun50i-h616", "overlays": "bananapi-m4-zero-emac-sdio-wifi-bt",
                "fdtfile": DTB, "rootdev": "UUID=" + document["root_uuid"], "rootfstype": "ext4",
                "extraargs": "cma=256M", "docker_optimizations": "on", "user_overlays": "",
                "extraboardargs": "", "usbstoragequirks": ""}
    require(set(env) <= set(defaults), "原環境含尚未支援的設定，拒絕默默略過")
    selected = {**defaults, **env}
    for key, value in selected.items():
        if key == "verbosity":
            require(matches(r"[0-7]", value), "原 verbosity 格式不符")
        elif key == "bootlogo":
            require(value in ("true", "false"), "原 bootlogo 格式不符")
        elif key == "fdtfile":
            require(value in (DTB, "allwinner/" + DTB), "原 fdtfile 不是 EMAC DTB")
        elif key == "usbstoragequirks":
            require(value == "" or matches(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}:[a-z]+"
                                           r"(?:,[0-9a-fA-F]{4}:[0-9a-fA-F]{4}:[a-z]+)*", value),
                    "USB quirk 格式未支援")
        else:
            require(value == defaults[key], "原設定值未支援：" + key)
    splash = ("splash", "plymouth.ignore-serial-consoles") if selected["bootlogo"] == "true" else ("splash=verbose",)
    return " ".join(("root=UUID=" + document["root_uuid"], "rootwait", "rootfstype=ext4",
                     "console=ttyS0,115200", "console=tty1", "consoleblank=0", *splash,
                     "cma=256M", "cgroup_enable=memory", "usb-storage.quirks=" + selected["usbstoragequirks"],
                     "ubootpart=" + document["partuuid"], "loglevel=6", "panic=0",
                     *("systemd.mask=" + service for service in MASKS)))


def validate_metadata(document, receipt):
    """只核對小型中繼資料，不開啟或重讀來源 XZ、備份或塊裝置。"""
    require(type(document) is dict and document.get("schema") == "bpi-h618-customer-components-v1"
            and document.get("board") == "bananapim4zeroemac", "組件 schema 或 EMAC 板型不符")
    require(matches(r"[a-z][a-z0-9-]{0,31}", document.get("os"))
            and document.get("desktop") in ("minimal", "xfce_desktop"), "系統或桌面識別無效")
    kernel = document.get("kernel_release")
    require(matches(r"[0-9]+\.[0-9]+\.[0-9]+-current-sunxi64", kernel), "核心版本未支援")
    require(matches(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", document.get("root_uuid"))
            and matches(r"[0-9a-f]{8}-01", document.get("partuuid")), "根 UUID 或第一分割區 PARTUUID 格式不符")
    preflight = document.get("preflight")
    require(type(preflight) is dict and all(preflight.get(k) is True for k in
            ("source_verified", "legacy_initrd_verified", "mmc_support_verified"))
            and document.get("hardware_validated") is False, "必要離線預檢未完成或混入實板通過聲明")
    image = document.get("image")
    require(type(image) is dict and isinstance(image.get("path"), str)
            and Path(image["path"]).is_absolute() and image["path"].endswith(".xz")
            and ".." not in Path(image["path"]).parts, "原 XZ 路徑不符")
    for key in ("raw", "compressed"):
        digest_record(image.get(key), EXPECTED["bytes"])
    require(image["raw"]["bytes"] % 512 == 0, "來源 raw 長度不是完整磁區")
    base = "/boot/dtb-" + kernel + "/allwinner/"
    paths = {"kernel": "/boot/vmlinuz-" + kernel, "initrd": "/boot/uInitrd-" + kernel,
             "dtb": base + DTB, "overlay": base + "overlay/" + OVERLAY,
             "fixup": base + "overlay/sun50i-h616-fixup.scr"}
    files = document.get("files")
    require(type(files) is dict and set(files) == set(paths), "必要原配組件缺少或含未知組件")
    sizes = {name: maximum for name, _, maximum in LOADS} | {"fixup": 65536}
    for name, path in paths.items():
        item = files[name]
        require(type(item) is dict and item.get("path") == path
                and matches(r"[0-9a-f]{8}", item.get("crc32")), "原配路徑或 CRC32 不符：" + name)
        digest_record({k: item.get(k) for k in ("bytes", "sha256")}, sizes[name])
    args = bootargs_for(document)
    require(type(receipt) is dict and receipt.get("schema") == "bpi-h618-emmc-deploy-v1"
            and receipt.get("status") == "verified" and receipt.get("ok") is True
            and receipt.get("confirm_overwrite") is True and type(receipt.get("ssh_exitcode")) is int
            and receipt["ssh_exitcode"] == 0, "部署收據未完整成功")
    request, source = receipt.get("request"), receipt.get("source")
    expected_source = {key: image[key] for key in ("raw", "compressed")}
    require(type(request) is dict and type(source) is dict
            and request.get("expected") == EXPECTED and request.get("protected_sd") == PROTECTED_SD
            and request.get("confirm_overwrite") is True and request.get("backup_verified") is True
            and matches(r"[0-9a-f]{64}", request.get("backup_manifest_sha256"))
            and request.get("source") == expected_source
            and all(source.get(k) == image[k] for k in ("path", "raw", "compressed")),
            "部署媒體、備份確認或來源摘要與 components 不符")
    require(receipt.get("compressed_bytes_sent") == image["compressed"]["bytes"]
            and receipt.get("range") == {"start": 0, "end_exclusive": image["raw"]["bytes"]},
            "部署範圍或傳送長度不符")
    deploy.validate_state(receipt.get("remote_state"), request, final=True)
    return args


def read_small(path, maximum=65536):
    path = Path(path).absolute()
    with safe.open_root(path.parent) as directory:
        return safe.fingerprint(directory, path.name, limit=maximum, keep=True)


def load_inputs(components, components_sha256, receipt):
    require(matches(r"[0-9a-f]{64}", components_sha256), "必須提供外部可信 components SHA-256")
    require(Path(receipt).name == "receipt.json", "只接受正式 receipt.json，不接受 .partial")
    digest, blob = read_small(components)
    require(digest["sha256"] == components_sha256, "components SHA-256 不符")
    document = safe.parse_manifest(blob)
    receipt_digest, blob = read_small(receipt)
    record = safe.parse_manifest(blob)
    validate_metadata(document, record)
    return document, record, {"components": digest, "deploy_receipt": receipt_digest}


class DeadlineConsole:
    """限制這次交接的全部串口等待；原始 RX 與緩衝仍由共用 console 保存。"""

    def __init__(self, console, timeout, monotonic):
        self.console, self.monotonic = console, monotonic
        self.deadline = monotonic() + timeout

    def remaining(self):
        remaining = self.deadline - self.monotonic()
        require(remaining > 0, "客戶引導總期限已到；不重試、不重啟")
        return remaining

    @property
    def timeout(self):
        return self.console.timeout

    @timeout.setter
    def timeout(self, value):
        self.console.timeout = min(value, self.remaining())

    def reset_input_buffer(self):
        self.remaining()
        self.console.reset_input_buffer()
        self.remaining()

    def read(self, size=1):
        self.console.timeout = min(self.console.timeout or 0.1, self.remaining())
        result = self.console.read(size)
        self.remaining()
        return result

    def write(self, data):
        return self.send(data)

    def send(self, data):
        result = self.console.send(data, timeout=min(10, self.remaining()))
        self.remaining()
        return result

    def expect_literal(self, pattern, timeout):
        result = self.console.expect_literal(pattern, timeout=min(timeout, self.remaining()))
        self.remaining()
        return result

    def expect_regex(self, pattern, timeout):
        result = self.console.expect_regex(pattern, timeout=min(timeout, self.remaining()))
        self.remaining()
        return result


def verify_mmc_list(listing):
    devices = re.findall(r"(?m)^mmc@([0-9a-f]+):\s*([0-9]+)(?:\s+\([^\r\n]*\))?\r?$", listing)
    require(sorted(devices) == [("4020000", "0"), ("4022000", "1")],
            "U-Boot MMC 清單不是已核定 SD 0／eMMC 1；不自動猜測")


def verify_mmc_info(info):
    require(re.findall(r"(?m)^Device:\s*(\S+)\s*\r?$", info) == ["mmc@4022000"]
            and len(re.findall(r"(?m)^MMC version [0-9.]+\r?$", info)) == 1
            and not re.search(r"(?m)^SD version\b", info), "U-Boot mmc info 未確認 eMMC 控制器及 MMC 類型")


def boot(console, bridge, components, receipt, records, *, timeout=300, monotonic=time.monotonic):
    """借用已開啟的 console；只跑一次，不關閉呼叫者的連線、不登入客戶系統。"""
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800,
            "引導總期限須為有限正數且不超過 1800 秒")
    args = validate_metadata(components, receipt)
    require(hashlib.sha256(bridge).hexdigest() == rescue.BRIDGE_SHA, "橋接封包不是已核定 A1 版本")
    console = DeadlineConsole(console, timeout, monotonic)
    supervisor = rescue.LabSupervisorSession(console, timeout=min(15, console.remaining()),
                                            blob=bridge, clock=monotonic)
    records.append({"supervisor": supervisor.probe()})
    supervisor.begin_load("S", 3)
    supervisor.finish_load()
    records.append({"handoff": supervisor.run(), "slot": 3, "fit_lba": 2048})
    console.expect_literal(b"Hit any key to stop autoboot:", timeout=40)
    console.send(b" ")
    console.expect_literal(b"=> ", timeout=5)
    uboot = rescue.UBoot(console, records)
    listing = uboot.command("mmc list")
    # 在選擇 1 前就拒絕錯誤映射；完整 info 隨後再核對。
    verify_mmc_list(listing)
    uboot.command("mmc dev 1")
    info = uboot.command("mmc info")
    verify_mmc_info(info)
    uuid = uboot.command("part uuid mmc 1:1")
    require(re.findall(r"(?m)^([0-9a-f]{8}-01)\r?$", uuid) == [components["partuuid"]],
            "eMMC 第一分割區 PARTUUID 不符")
    for name, address, _ in LOADS:
        uboot.load(components["files"][name], address, media="1:1")
    for command in ("fdt addr 4fa00000", "fdt resize 65536", "fdt apply 45000000",
                    "fdt set /soc/mmc@4020000 status disabled"):
        uboot.command(command)
    for node, expected in (("4020000", "disabled"), ("4021000", "okay"), ("4022000", "okay")):
        output = uboot.command(f"fdt print /soc/mmc@{node} status")
        require(re.findall(r'(?m)^\s*status = "([a-z]+)";?\r?$', output) == [expected],
                "RAM DTB 的 MMC 狀態不符：" + node)
    uboot.command("setenv bootargs " + args)
    command = "booti 40080000 4ff00000 4fa00000"
    records.append({"command": command, "legacy_initrd": True, "bootargs": args})
    console.send(command + "\n")
    kernel = console.expect_regex(rb"(?:^|\r?\n)(?:\[\s*[0-9.]+\]\s*)?Linux version ([^\s]+)[^\r\n]*\r?\n", timeout=90)
    observed = kernel.groups[0].decode("ascii")
    records.append({"kernel_release_observed": observed})
    require(observed == components["kernel_release"], "觀察到其他核心版本，拒絕視為客戶核心")
    login = console.expect_regex(rb"(?:^|\r?\n)([A-Za-z0-9][A-Za-z0-9._-]{0,63}) login: ?", timeout=180)
    records.append({"login_host_observed": login.groups[0].decode("ascii")})
    return {"status": "login_observed", "kernel_release_observed": observed, "login_observed": True,
            "root_cid_verified": False, "system_verified": False, "hardware_validated": False,
            "native_sd_verified": False, "original_boot_chain_verified": False}


def run(*, port, bridge, components, components_sha256, deploy_receipt, output, timeout=300):
    require(port == "/dev/ttyUSB0", "本版只核定 /dev/ttyUSB0，不自動尋找串口")
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 1800,
            "引導總期限須為有限正數且不超過 1800 秒")
    document, receipt, evidence = load_inputs(components, components_sha256, deploy_receipt)
    digest, blob = read_small(bridge, 256 * 1024)
    require(digest["sha256"] == rescue.BRIDGE_SHA, "橋接封包不是已核定 A1 版本")
    out = Path(output).absolute()
    with safe.open_root(out.parent) as parent:
        os.mkdir(out.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    report = {"schema": "bpi-h618-customer-boot-v1", "ok": False, "status": "prepared",
              "components": document, "evidence": evidence, "bridge_sha256": digest["sha256"],
              "expected_emmc": EXPECTED, "deploy_sd_before": receipt["remote_state"]["sd_before"],
              "deploy_sd_after": receipt["remote_state"]["sd_after"], "sd_after_boot_verified": False,
              "root_cid_verified": False, "system_verified": False, "hardware_validated": False,
              "original_boot_chain_verified": False, "native_sd_verified": False,
              "block_write_commands_sent": False, "power_controlled": False, "login_observed": False,
              "ram_dtb_changes": [{"path": "/soc/mmc@4020000", "property": "status", "value": "disabled"}],
              "skipped_scripts": ["/boot/boot.scr", document["files"]["fixup"]["path"]],
              "service_masks": list(MASKS), "commands": [], "limits": LIMITS,
              "started_unix": time.time(), "uart_released": False}
    try:
        with rescue.open_serial(port, 115200, 0.1) as channel:
            with rescue.RecordedChannel(channel, log_path=out / "uart.bin") as console:
                report.update(boot(console, blob, document, receipt, report["commands"], timeout=timeout))
        report["uart_released"] = True
        report["ok"] = True
    except Exception as exc:
        report.update(status="failed", error=str(exc))
    finally:
        report["finished_unix"] = time.time()
        # 只有工作證據，沒有可被誤當成系統通過的正式成功收據。
        with safe.open_root(out) as directory, safe.open_file(directory, "report.json.partial", create=True) as stream:
            stream.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def main(argv=None):
    parser = safe.JsonArgumentParser(description=__doc__)
    for name, help_text in (("port", "明確的 /dev/ttyUSB0；操作者須先停在 SRAM 等待狀態"),
                            ("bridge", "已核定 A1 橋接封包"), ("components", "原配組件 JSON"),
                            ("components-sha256", "外部可信組件清單 SHA-256"),
                            ("deploy-receipt", "成功部署的正式 receipt.json"),
                            ("output", "新的私有證據目錄，拒絕覆寫及符號連結")):
        parser.add_argument("--" + name, required=True, help=help_text)
    parser.add_argument("--timeout", type=float, default=300, help="串口交接總期限秒數；最多 1800")
    try:
        result = run(**vars(parser.parse_args(argv)))
        print(json.dumps({key: result.get(key) for key in
                          ("ok", "status", "error", "system_verified", "uart_released")}, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except (ValueError, OSError, KeyboardInterrupt) as exc:
        print(json.dumps({"ok": False, "system_verified": False,
                          "error": str(exc) if isinstance(exc, ValueError) else "引導中斷或主機檔案操作失敗"},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
