#!/usr/bin/env python3
"""由既有 ABI3 槽一次性啟動 0845 的救援 Linux；不控制電源、不寫媒體。"""

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

sys.dont_write_bytecode = True

if __package__:
    from .bpi_lab_console import ConsoleSession
    from .bpi_sram_lab_uart import LabSupervisorSession, staged_package
    from .bpi_sram_uart import open_serial
else:
    from bpi_lab_console import ConsoleSession
    from bpi_sram_lab_uart import LabSupervisorSession, staged_package
    from bpi_sram_uart import open_serial


KERNEL = "6.6.75-current-sunxi64"
BRIDGE_SHA = "a9e654e9395576ba57de7c54a4f5df70081c50b0c0c355637408f4e0682debcb"
SD_CID = "03534453523634478697bc8c0701846b"
BASE = "/boot/dtb-" + KERNEL + "/allwinner/"
PATHS = (
    "/boot/vmlinuz-" + KERNEL,
    BASE + "sun50i-h618-bananapi-m4-zero.dtb",
    BASE + "overlay/sun50i-h616-bananapi-m4-sdio-wifi-bt.dtbo",
    "/root/bpi-lab/rescue-build-20260916-001/rescue-initramfs.img",
)
ADDRESSES = (0x40080000, 0x4FA00000, 0x45000000, 0x4FF00000)
LIMITS = (64 * 1024 * 1024, 65536, 65536, 64 * 1024 * 1024)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def input_manifest(path):
    require(path.is_file() and path.stat().st_size < 65536, "輸入清單不存在或過大")
    document = json.loads(path.read_text())
    entries = document["inputs"]
    result = []
    for name, maximum in zip(PATHS, LIMITS):
        if name == PATHS[3]:
            candidates = [item for item in entries if re.fullmatch(
                r"/root/bpi-lab/rescue-build-20260916-[0-9]{3}/rescue-initramfs\.img", item.get("path", ""))]
        else:
            candidates = [item for item in entries if item.get("path") == name]
        require(len(candidates) == 1, "必要救援輸入缺少或重複")
        item = candidates[0]
        require(type(item.get("bytes")) is int and 0 < item["bytes"] <= maximum, "輸入大小超界")
        require(re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")), "SHA-256 不符")
        require(re.fullmatch(r"[0-9a-f]{8}", item.get("crc32", "")), "CRC32 不符")
        result.append(dict(item))
    require(document["sd_prefix"]["bytes"] == 4194304
            and re.fullmatch(r"[0-9a-f]{64}", document["sd_prefix"]["sha256"]), "SD 前綴證據不符")
    return result, document["sd_prefix"]


class RecordedChannel(ConsoleSession):
    def reset_input_buffer(self):
        # 不丟失原始 UART；只消耗已保存的舊資料，再以新 nonce 交握。
        end = time.monotonic() + 0.2
        consumed = 0
        while time.monotonic() < end:
            self.timeout = 0.01
            consumed += len(self.read(4096))
            require(consumed <= 65536, "交握前有持續輸出，拒絕當作 SRAM 等待狀態")


class UBoot:
    def __init__(self, console, records):
        self.console = console
        self.records = records

    def command(self, command, timeout=45):
        nonce = secrets.token_hex(8)
        # 拆開標記以排除終端機回顯，成功／失敗皆須完整行邊界。
        prefix = "BPI_" + nonce
        wire = f"if {command}; then echo BPI_'{nonce}'_OK; else echo BPI_'{nonce}'_FAIL; fi\n"
        require(len(wire) < 1000 and "\n" not in command and "\r" not in command, "U-Boot 指令超界")
        self.console.send(wire)
        match = self.console.expect_regex(rb"(?:^|\r?\n)" + prefix.encode()
                                          + rb"_(OK|FAIL)\r?\n", timeout=timeout)
        self.console.expect_literal(b"=> ", timeout=10)
        record = {"command": command, "output": match.before.decode(errors="replace"),
                  "ok": match.groups[0] == b"OK"}
        self.records.append(record)
        require(record["ok"], "U-Boot 指令失敗，未繼續交接")
        return record["output"]

    def load(self, item, address, *, media="0:1"):
        require(media in ("0:1", "1:1"), "本版只接受明確的 MMC 0／1 第一分割區")
        output = self.command(f"load mmc {media} {address:x} {item['path']}")
        sizes = re.findall(r"(?:^|\r?\n)([0-9]+) bytes read\b", output)
        require(sizes == [str(item["bytes"])], "U-Boot 實收長度不符")
        output = self.command(f"crc32 {address:x} {item['bytes']:x}")
        observed = re.findall(r"(?i)crc32 for [0-9a-f]+ \.\.\. [0-9a-f]+ ==> ([0-9a-f]{8})", output)
        require([value.lower() for value in observed] == [item["crc32"]], "U-Boot RAM CRC32 不符")


def boot(console, bridge, inputs, records):
    supervisor = LabSupervisorSession(console, timeout=15, blob=bridge)
    info = supervisor.probe()
    records.append({"supervisor": info})
    supervisor.begin_load("S", 3)
    supervisor.finish_load()
    records.append(supervisor.run())
    console.expect_literal(b"Hit any key to stop autoboot:", timeout=40)
    console.send(b" ")
    console.expect_literal(b"=> ", timeout=5)
    uboot = UBoot(console, records)
    uboot.command("mmc dev 0")
    uboot.command("mmc info")
    uboot.command("part list mmc 0")
    for item, address in zip(inputs, ADDRESSES):
        uboot.load(item, address)
    uboot.command("fdt addr 4fa00000")
    uboot.command("fdt resize 65536")
    uboot.command("fdt apply 45000000")
    uboot.command("fdt print /soc/mmc@4021000 status")
    uboot.command("setenv bootargs console=ttyS0,115200 earlycon loglevel=6 rdinit=/init panic=0")
    command = f"booti 40080000 4ff00000:{inputs[3]['bytes']:x} 4fa00000"
    records.append({"command": command})
    console.send(command + "\n")
    ready = console.expect_regex(rb"(?:^|\r?\n)BPI_RESCUE_READY (\{[^\r\n]+\})\r?\n", timeout=90)
    identity = json.loads(ready.groups[0])
    require(identity.get("schema") == "bpi-h618-rescue-v1" and identity.get("kernel") == KERNEL,
            "救援就緒身分不符")
    inventory = read_inventory(console, records)
    matching = [item for item in inventory["devices"] if item.get("device/cid") == SD_CID]
    require(len(matching) == 1 and matching[0]["device/type"] == "SD", "救援 SD 身分不符")
    rows = [line.split(" - ") for line in inventory["mountinfo"].splitlines()]
    roots = [parts[1].split()[0] for parts in rows if parts[0].split()[4] == "/"]
    require(roots in (["rootfs"], ["ramfs"], ["tmpfs"]), "救援根不在 RAM")
    require(not any("/dev/mmcblk" in parts[1] for parts in rows), "救援仍掛載 MMC 媒體")
    require(len(inventory["swaps"].splitlines()) <= 1, "救援仍啟用 swap")
    return {"identity": identity, "inventory": inventory, "shell_verified": True,
            "independent_root_ram_verified": True, "network_verified": False,
            "memory_stress_verified": False}


def read_inventory(console, records):
    # 開機背景訊息可能切斷 JSON；只重做唯讀盤點，絕不拼接破碎輸出或重啟板子。
    for attempt in range(1, 4):
        probe = console.run_shell("bpi-rescue inventory", timeout=30)
        require(probe.exitcode == 0, "救援 shell 或盤點失敗")
        try:
            inventory = parse_inventory(probe.output)
        except ValueError:
            records.append({"inventory_attempt": attempt, "ok": False,
                            "reason": "UART 未收到完整 JSON；原始輸出保留"})
            if attempt == 3:
                raise
            time.sleep(1)
        else:
            records.append({"inventory_attempt": attempt, "ok": True})
            return inventory


def parse_inventory(output):
    candidates = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(item, dict) and item.get("schema") == "bpi-h618-rescue-v1" and "devices" in item:
            candidates.append(item)
    require(len(candidates) == 1, "未取得唯一完整救援盤點 JSON")
    return candidates[0]


def main():
    from bpi_h618_rescue.bpi_rescue_cli import ChineseArgumentParser
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="明確核對的 UART，不自動尋找")
    parser.add_argument("--bridge", type=Path, required=True, help="既有 A1 配套橋接封包")
    parser.add_argument("--inputs", type=Path, required=True, help="經 SSH 核對的固定救援輸入清單")
    parser.add_argument("--output", type=Path, required=True, help="新的私有證據目錄")
    args = parser.parse_args()
    inputs, sd_prefix = input_manifest(args.inputs)
    require(args.port == "/dev/ttyUSB0", "本版只核定 0845 的 ttyUSB0，不推論其他串口")
    output = args.output.absolute()
    require(output.parent.resolve() == output.parent and not output.exists(), "輸出必須是無符號連結的新目錄")
    output.mkdir(mode=0o700)
    report = {"board": "0845", "ok": False, "inputs": inputs, "sd_prefix_before": sd_prefix,
              "media_written": False, "power_controlled": False, "commands": [],
              "started_unix": time.time(), "uart_released": False}
    try:
        with staged_package(args.bridge) as (_, blob):
            require(hashlib.sha256(blob).hexdigest() == BRIDGE_SHA, "橋接封包不是已核定版本")
            with open_serial(args.port, 115200, 10) as channel:
                with RecordedChannel(channel, log_path=output / "uart.bin") as console:
                    report.update(boot(console, blob, inputs, report["commands"]))
        report["ok"] = True
    except Exception as error:
        report["error"] = str(error)
    finally:
        report["finished_unix"] = time.time()
        report["uart_released"] = True
        with (output / "report.json").open("x") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    print(json.dumps({key: report.get(key) for key in ("ok", "error", "uart_released")}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
