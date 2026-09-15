#!/usr/bin/env python3
"""固定 smoke 的 UART 故障注入；不操作電源、不讀寫 SD、不執行成功負載。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

import bpi_sram_package as package
import bpi_sram_uart as uart
import bpi_h618_artifacts as artifacts


PACKAGE_SHA256 = "8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062"
CASES = ("bad-header-crc", "bad-payload-sha256", "truncated-file",
         "cancel-before-payload", "no-payload-timeout")
MODEM_PREFIX = frozenset((0x43, 0x15, 0x18, 0x08, 0x06, 0x0D, 0x0A))


def fault_payload(blob, case):
    if case not in CASES:
        raise uart.UartError("故障案例不在固定清單")
    if len(blob) != 1536 or hashlib.sha256(blob).hexdigest() != PACKAGE_SHA256:
        raise uart.UartError("只接受已驗證的 build-009 固定 smoke 封包")
    package.parse_package(blob)
    changed = bytearray(blob)
    if case == "bad-header-crc":
        changed[508] ^= 1
    elif case == "bad-payload-sha256":
        changed[512] ^= 1
    elif case == "truncated-file":
        return blob[:1024]
    elif case in ("cancel-before-payload", "no-payload-timeout"):
        return None
    return bytes(changed)


def phase_line(channel, timeout=40, *, modem=False, clock=time.monotonic):
    deadline = clock() + timeout
    line = bytearray()
    skipped = 0
    while clock() < deadline:
        byte = channel.read(1)
        if not byte:
            continue
        if len(byte) != 1:
            raise uart.UartError("故障觀測讀取超過單位元組")
        if not line and modem and byte[0] in MODEM_PREFIX:
            skipped += 1
            if skipped > 4096:
                raise uart.UartError("調變解調器控制前綴超過上限")
            continue
        line.extend(byte)
        if len(line) > uart.MAX_LINE_BYTES:
            raise uart.UartError("故障觀測控制行超過上限")
        if byte == b"\n":
            parsed = uart.parse_control_line(bytes(line))
            if parsed is None:
                raise uart.UartError("收到非救援控制輸出，停止故障注入")
            return parsed
    raise uart.UartError("故障後未在觀測期限內返回控制行")


def run_case(channel, blob, case, sender, *, clock=time.monotonic):
    payload = fault_payload(blob, case)
    session = uart.SupervisorSession(channel, 15)
    initial = session.probe()
    session.begin_load("U", len(blob))
    started = clock()
    sender_error = None
    if payload is not None:
        try:
            sender(channel, payload)
        except uart.UartError:
            sender_error = "sx 回報傳輸失敗；只按後續韌體與交握證據判定"
    elif case == "cancel-before-payload":
        if channel.write(b"\x18" * 3) != 3:
            raise uart.UartError("取消命令未完整送出")
    identity, fields = phase_line(channel, modem=True, clock=clock)
    elapsed = clock() - started
    if (identity != "BPI-SUP1" or fields.get("event") != "loaded"
            or fields.get("nonce") != str(session.nonce) or fields.get("result") != "-1"):
        raise uart.UartError("未確認本次載入失敗，禁止發送執行命令")
    # 只有明確載入失敗才測狀態守門，絕不執行成功或未知負載。
    session._send("R")
    if phase_line(channel, clock=clock) != (
            "BPI-SUP1", {"event": "reject", "reason": "command_or_state"}):
        raise uart.UartError("失敗負載未收到預期執行拒絕，停止")
    final = session.probe()
    return {"case": case, "loaded_nonce": initial["nonce"],
            "loaded_result": -1, "run_rejected": True, "reprobe": final,
            "elapsed_seconds": round(elapsed, 3), "sender_error": sender_error,
            "return_within_15_seconds": elapsed <= 15,
            "fault_payload_sha256": hashlib.sha256(payload).hexdigest() if payload else None}


class TraceChannel:
    def __init__(self, channel, trace):
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "trace", trace)

    def __getattr__(self, key):
        return getattr(self.channel, key)

    def __setattr__(self, key, value):
        setattr(self.channel, key, value)

    def read(self, count):
        value = self.channel.read(count)
        if value:
            self.trace.append({"direction": "rx", "hex": value.hex()})
        return value

    def write(self, value):
        count = self.channel.write(value)
        self.trace.append({"direction": "tx", "hex": value[:count].hex()})
        return count


def main(argv=None):
    parser = package.ChineseArgumentParser(description=__doc__)
    parser.add_argument("--port", type=uart._named_port, required=True, help="已核對的本機串口")
    parser.add_argument("--input", type=Path, required=True, help="固定 build-009 smoke 封包")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的證據目錄")
    parser.add_argument("--case", choices=CASES, required=True, help="單一固定故障案例")
    parser.add_argument("--confirm-fault-injection", action="store_true", required=True,
                        help="明確執行本次 UART 故障注入，不代表寫卡授權")
    args = parser.parse_args(argv)
    trace, report = [], {"case": args.case, "media_written": False, "power_control": False}
    try:
        blob = package.read_regular_file(args.input, 1536)
        fault_payload(blob, args.case)
        executable = shutil.which("sx")
        if executable is None:
            raise uart.UartError("缺少 sx；未開啟串口")
        with artifacts.open_root(args.output.parent) as parent:
            artifacts.relative_parts(args.output.name)
            os.mkdir(args.output.name, 0o700, dir_fd=parent)
        with artifacts.open_root(args.output) as output:
            with tempfile.TemporaryDirectory(prefix="bpi-sram-fixed-fault-") as temporary:
                def sender(channel, payload):
                    path = Path(temporary) / "fault.sram"
                    package.write_new_regular_file(path, payload)
                    uart.send_xmodem(channel, path, executable, 35)
                try:
                    with uart.open_serial(args.port, 115200, 15) as channel:
                        report["result"] = run_case(TraceChannel(channel, trace), blob, args.case, sender)
                        report["recovery_observed"] = True
                except (OSError, uart.UartError) as exc:
                    report["recovery_observed"] = False
                    report["error"] = type(exc).__name__ + ": " + str(exc)
                finally:
                    report["trace_scope"] = "只有主機控制 RX／TX；sx 直接傳輸本體不在追蹤內"
                    for name, value in (("control.json", trace), ("result.json", report)):
                        with artifacts.open_file(output, name, create=True) as stream:
                            stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("recovery_observed") else 1
    except (OSError, ValueError) as exc:
        print("錯誤：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
