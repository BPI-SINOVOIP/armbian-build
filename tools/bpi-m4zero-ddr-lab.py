#!/usr/bin/env python3
"""BPI-M4 Zero M4ZLAB2 DDR 實驗器主機端控制工具。"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import select
import shlex
import subprocess
import sys
import termios
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO


PROTOCOL = "M4ZLAB2"
TIMER_HZ = 24_000_000
MAX_COMMAND_BYTES = 255
EVENT_TYPES = {
    "READY",
    "START",
    "TEST",
    "FINAL",
    "BENCH",
    "ERROR",
    "REJECT",
    "BOOT_ERROR",
}
PROFILE_FIELDS = (
    "id",
    "clk",
    "dx_odt",
    "dx_dri",
    "ca_dri",
    "odt_en",
    "tpr0",
    "tpr2",
    "tpr6",
    "tpr10",
    "tpr11",
    "tpr12",
    "level",
    "passes",
    "window",
)
REGISTER_FIELDS = {
    "dx_odt",
    "dx_dri",
    "ca_dri",
    "odt_en",
    "tpr0",
    "tpr2",
    "tpr6",
    "tpr10",
    "tpr11",
    "tpr12",
}
PACKED_SCAN_FIELDS = {
    "tpr6.b3": ("tpr6", 24, 0xFF),
    **{
        f"{register}.b{lane}": (register, lane * 8, 0x3F)
        for register in ("tpr11", "tpr12")
        for lane in range(4)
    },
}
SCAN_FIELDS = set(PROFILE_FIELDS) | set(PACKED_SCAN_FIELDS)
LEVELS = {"M0", "M1", "M2"}
LEVEL_VALUES = {"M0": 0, "M1": 1, "M2": 2}
EVENT_RE = re.compile(
    r"(?:^|\s)M4ZLAB2_(READY|START|TEST|FINAL|BENCH|ERROR|REJECT|BOOT_ERROR)"
    r"(?:\s+(.*))?$"
)


class LabError(Exception):
    """可直接顯示給使用者的工具錯誤。"""


class ChineseArgumentParser(argparse.ArgumentParser):
    """將 argparse 的固定介面與常見錯誤改成繁體中文。"""

    def __init__(self, *args: Any, **kwargs: Any):
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        if add_help:
            self.add_argument("-h", "--help", action="help", help="顯示這份說明並離開")

    def format_help(self) -> str:
        return (
            super()
            .format_help()
            .replace("usage: ", "用法：")
            .replace("options:", "選項：")
            .replace("positional arguments:", "位置參數：")
            .replace("位置參數:", "位置參數：")
            .replace("選項:", "選項：")
        )

    def error(self, message: str) -> None:
        message = re.sub(
            r"the following arguments are required: (.*)",
            r"缺少必要參數：\1",
            message,
        )
        message = re.sub(
            r"unrecognized arguments: (.*)", r"無法識別的參數：\1", message
        )
        message = re.sub(r"invalid choice: ([^ ]+)", r"無效選項：\1", message)
        message = re.sub(
            r"argument ([^:]+): expected one argument",
            r"參數 \1 需要一個值",
            message,
        )
        sys.stderr.write(self.format_usage().replace("usage: ", "用法："))
        sys.stderr.write(f"參數錯誤：{message}\n")
        raise SystemExit(2)


def now_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def parse_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise LabError(f"欄位 {field} 不接受布林值")
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.strip():
        raise LabError(f"欄位 {field} 必須是整數")
    try:
        return int(value.strip(), 0)
    except ValueError as exc:
        raise LabError(f"欄位 {field} 的整數格式無效：{value}") from exc


def normalize_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in PROFILE_FIELDS if field not in raw]
    unknown = sorted(set(raw) - set(PROFILE_FIELDS))
    if missing:
        raise LabError(f"設定檔缺少完整欄位：{', '.join(missing)}")
    if unknown:
        raise LabError(f"設定檔含有未知欄位：{', '.join(unknown)}")

    profile: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        if field == "level":
            continue
        profile[field] = parse_integer(raw[field], field)

    level = str(raw["level"]).upper()
    if level not in LEVELS:
        raise LabError("欄位 level 必須是 M0、M1 或 M2")
    profile["level"] = level

    if not 1 <= profile["id"] <= 0xFFFFFFFF:
        raise LabError("欄位 id 必須介於 1 與 0xffffffff")
    if not 240 <= profile["clk"] <= 900 or profile["clk"] % 12:
        raise LabError("欄位 clk 必須介於 240 與 900 MHz，且為 12 MHz 的倍數")
    if not 1 <= profile["passes"] <= 1_000:
        raise LabError("欄位 passes 必須介於 1 與 1000")
    if not 1 <= profile["window"] <= 64:
        raise LabError("欄位 window 必須介於 1 與 64 MiB")
    for field in REGISTER_FIELDS:
        if not 0 <= profile[field] <= 0xFFFFFFFF:
            raise LabError(f"欄位 {field} 必須介於 0 與 0xffffffff")

    return {field: profile[field] for field in PROFILE_FIELDS}


def load_profile(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except OSError as exc:
        raise LabError(f"無法讀取設定檔 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise LabError(f"設定檔 {path} 不是有效 JSON：第 {exc.lineno} 行") from exc
    if isinstance(raw, dict) and "profile" in raw and isinstance(raw["profile"], dict):
        raw = raw["profile"]
    if not isinstance(raw, dict):
        raise LabError("設定檔根節點必須是物件")
    return normalize_profile(raw)


def parse_assignment(
    spec: str,
    option: str,
    allowed_fields: Iterable[str] = PROFILE_FIELDS,
) -> tuple[str, str]:
    if "=" not in spec:
        raise LabError(f"{option} 必須使用 欄位=值 格式：{spec}")
    field, value = spec.split("=", 1)
    field = field.strip()
    value = value.strip()
    if field not in allowed_fields:
        raise LabError(f"{option} 指定未知欄位：{field}")
    if not value:
        raise LabError(f"{option} 的欄位 {field} 缺少值")
    return field, value


def apply_overrides(profile: Mapping[str, Any], specs: Sequence[str]) -> dict[str, Any]:
    updated = dict(profile)
    for spec in specs:
        field, value = parse_assignment(spec, "--set")
        updated[field] = value
    return normalize_profile(updated)


def profile_value_text(field: str, value: Any) -> str:
    if field == "level":
        return str(LEVEL_VALUES[str(value)])
    if field in REGISTER_FIELDS:
        return f"0x{int(value):08x}"
    return str(value)


def build_run_command(profile: Mapping[str, Any]) -> str:
    normalized = normalize_profile(profile)
    command = (
        "R "
        + " ".join(
            f"{field}={profile_value_text(field, normalized[field])}"
            for field in PROFILE_FIELDS
        )
        + "\n"
    )
    length = len(command.encode("ascii"))
    if length > MAX_COMMAND_BYTES:
        raise LabError(f"M4ZLAB2 R 命令長度為 {length} bytes，必須小於 256 bytes")
    return command


def profile_key(profile: Mapping[str, Any]) -> str:
    normalized = normalize_profile(profile)
    normalized.pop("id")
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    return hashlib.sha256(payload).hexdigest()[:20]


def parse_scan_values(field: str, expression: str) -> list[Any]:
    values: list[Any] = []
    for part in expression.split(","):
        part = part.strip()
        if not part:
            raise LabError(f"--field 的欄位 {field} 含有空值")
        if field == "level":
            if ":" in part:
                raise LabError(f"欄位 {field} 不支援數值範圍語法")
            value: Any = part
        else:
            pieces = part.split(":")
            if len(pieces) == 1:
                value = parse_integer(part, field)
                if value not in values:
                    values.append(value)
                continue
            if len(pieces) not in {2, 3}:
                raise LabError(f"欄位 {field} 的範圍格式無效：{part}")
            start = parse_integer(pieces[0], field)
            stop = parse_integer(pieces[1], field)
            step = (
                parse_integer(pieces[2], field)
                if len(pieces) == 3
                else (1 if stop >= start else -1)
            )
            if step == 0 or (stop - start) * step < 0:
                raise LabError(f"欄位 {field} 的範圍步進方向無效：{part}")
            end = stop + (1 if step > 0 else -1)
            for ranged_value in range(start, end, step):
                if ranged_value not in values:
                    values.append(ranged_value)
            continue
        if value not in values:
            values.append(value)
    if not values:
        raise LabError(f"欄位 {field} 沒有可掃描的值")
    return values


def parse_scan_fields(specs: Sequence[str]) -> dict[str, list[Any]]:
    fields: dict[str, list[Any]] = {}
    for spec in specs:
        field, expression = parse_assignment(spec, "--field", SCAN_FIELDS)
        if field == "id":
            raise LabError("欄位 id 由工具管理，不可作為掃描參數")
        if field in fields:
            raise LabError(f"--field 重複指定欄位：{field}")
        fields[field] = parse_scan_values(field, expression)
    return fields


def scan_field_value(profile: Mapping[str, Any], field: str) -> Any:
    if field in PROFILE_FIELDS:
        return profile[field]
    register, shift, mask = PACKED_SCAN_FIELDS[field]
    return (int(profile[register]) >> shift) & mask


def apply_scan_value(profile: dict[str, Any], field: str, value: Any) -> None:
    if field in PROFILE_FIELDS:
        profile[field] = value
        return
    register, shift, mask = PACKED_SCAN_FIELDS[field]
    parsed = parse_integer(value, field)
    if not 0 <= parsed <= mask:
        raise LabError(f"欄位 {field} 必須介於 0 與 {mask:#x}")
    profile[register] = (int(profile[register]) & ~(mask << shift)) | (parsed << shift)


def expand_matrix(
    base_profile: Mapping[str, Any],
    scan_fields: Mapping[str, Sequence[Any]],
    max_candidates: int = 10_000,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    base = normalize_profile(base_profile)
    if not scan_fields:
        raise LabError("scan 至少需要一個 --field")
    count = 1
    for values in scan_fields.values():
        count *= len(values)
    if count > max_candidates:
        raise LabError(f"掃描矩陣共有 {count} 組，超過上限 {max_candidates}")

    field_names = list(scan_fields)
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, combination in enumerate(
        itertools.product(*(scan_fields[name] for name in field_names)), 1
    ):
        candidate = dict(base)
        coordinates = dict(zip(field_names, combination))
        for field, value in coordinates.items():
            apply_scan_value(candidate, field, value)
        if "id" not in scan_fields:
            candidate["id"] = base["id"] + index - 1
        candidate = normalize_profile(candidate)
        build_run_command(candidate)
        result.append((candidate, coordinates))
    return result


def repeat_profiles(
    profiles: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    repeat: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not 1 <= repeat <= 1_000:
        raise LabError("重複次數必須介於 1 與 1000")
    if not profiles:
        return []
    next_id = normalize_profile(profiles[0][0])["id"]
    repeated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for attempt in range(1, repeat + 1):
        for profile, coordinates in profiles:
            if next_id > 0xFFFFFFFF:
                raise LabError("交易 id 超過 0xffffffff")
            candidate = dict(profile)
            candidate["id"] = next_id
            candidate = normalize_profile(candidate)
            candidate_coordinates = dict(coordinates)
            candidate_coordinates["repetition"] = attempt
            repeated.append((candidate, candidate_coordinates))
            next_id += 1
    return repeated


def parse_event(line: str) -> dict[str, Any] | None:
    match = EVENT_RE.search(line.rstrip("\r\n"))
    if not match:
        return None
    event_type, raw_fields = match.groups()
    fields: dict[str, str] = {}
    for token in (raw_fields or "").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key:
            fields[key] = value
    return {"type": event_type, "fields": fields, "raw": line.rstrip("\r\n")}


class RawUartLog:
    def __init__(self, path: Path | None):
        self.path = path
        self.stream: TextIO | None = None

    def __enter__(self) -> "RawUartLog":
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.path.open("a", encoding="utf-8", buffering=1)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.stream is not None:
            self.stream.close()

    def write(self, direction: str, line: str) -> None:
        if self.stream is None:
            return
        clean = line.rstrip("\r\n")
        self.stream.write(f"{now_timestamp()}\t{direction}\t{clean}\n")
        self.stream.flush()


class PosixSerial:
    """不依賴外部套件的 POSIX UART 行傳輸。"""

    BAUD_RATES = {
        9_600: termios.B9600,
        19_200: termios.B19200,
        38_400: termios.B38400,
        57_600: termios.B57600,
        115_200: termios.B115200,
        230_400: termios.B230400,
    }

    def __init__(self, device: str, baud: int = 115_200):
        if baud not in self.BAUD_RATES:
            raise LabError(f"不支援的 UART baud rate：{baud}")
        self.device = device
        self.baud = baud
        self.fd: int | None = None
        self.buffer = bytearray()

    def __enter__(self) -> "PosixSerial":
        try:
            self.fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            raise LabError(f"無法開啟 UART {self.device}：{exc}") from exc
        try:
            attributes = termios.tcgetattr(self.fd)
            attributes[0] = 0
            attributes[1] = 0
            attributes[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
            attributes[3] = 0
            attributes[4] = self.BAUD_RATES[self.baud]
            attributes[5] = self.BAUD_RATES[self.baud]
            attributes[6][termios.VMIN] = 0
            attributes[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except (OSError, termios.error) as exc:
            os.close(self.fd)
            self.fd = None
            raise LabError(f"無法設定 UART {self.device}：{exc}") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def send_line(self, line: str) -> None:
        if self.fd is None:
            raise LabError("UART 尚未開啟")
        payload = line.encode("ascii")
        deadline = time.monotonic() + 2.0
        written = 0
        while written < len(payload):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LabError("UART 寫入逾時")
            _, writable, _ = select.select([], [self.fd], [], remaining)
            if not writable:
                continue
            try:
                written += os.write(self.fd, payload[written:])
            except BlockingIOError:
                continue

    def read_line(self, timeout: float) -> str | None:
        if self.fd is None:
            raise LabError("UART 尚未開啟")
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                payload = bytes(self.buffer[: newline + 1])
                del self.buffer[: newline + 1]
                return payload.decode("utf-8", errors="replace").rstrip("\r\n")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            readable, _, _ = select.select([self.fd], [], [], remaining)
            if not readable:
                return None
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                continue
            self.buffer.extend(chunk)
            if len(self.buffer) > 65_536:
                del self.buffer[:-4096]


def send_logged(channel: Any, line: str, raw_log: RawUartLog | None) -> None:
    channel.send_line(line)
    if raw_log is not None:
        raw_log.write("TX", line)


def read_logged(channel: Any, timeout: float, raw_log: RawUartLog | None) -> str | None:
    line = channel.read_line(timeout)
    if line is not None and raw_log is not None:
        raw_log.write("RX", line)
    return line


def wait_ready(
    channel: Any,
    command: str,
    timeout: float,
    raw_log: RawUartLog | None = None,
) -> dict[str, Any]:
    send_logged(channel, command + "\n", raw_log)
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    while True:
        line = read_logged(channel, deadline - time.monotonic(), raw_log)
        if line is None:
            raise LabError(f"送出 {command} 後等待 READY 逾時")
        event = parse_event(line)
        if event is None:
            continue
        events.append(event)
        if event["type"] == "READY":
            return {"ready": event, "events": events}
        if event["type"] in {"ERROR", "REJECT", "BOOT_ERROR"}:
            raise LabError(f"裝置在同步期間回報 {event['type']}：{event['raw']}")


def _event_matches_profile(
    event: Mapping[str, Any], profile: Mapping[str, Any]
) -> bool:
    event_id = event.get("fields", {}).get("id")
    return event_id is None or event_id == str(profile["id"])


def _reported_all_passed(fields: Mapping[str, str], requested_passes: int) -> bool:
    result = fields.get("result", fields.get("status", "")).upper()
    if result not in {"PASS", "OK"}:
        return False
    if fields.get("recovered", "pass").upper() not in {"PASS", "OK"}:
        return False
    try:
        if "passed" in fields and int(fields["passed"], 0) != requested_passes:
            return False
        if "passes" in fields and int(fields["passes"], 0) != requested_passes:
            return False
        if "total" in fields and int(fields["total"], 0) != requested_passes:
            return False
    except ValueError:
        return False
    return True


def execute_profile(
    channel: Any,
    profile: Mapping[str, Any],
    timeout: float,
    raw_log: RawUartLog | None = None,
) -> dict[str, Any]:
    normalized = normalize_profile(profile)
    command = build_run_command(normalized)
    send_logged(channel, command, raw_log)
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    bench: list[dict[str, Any]] = []
    start_seen = False
    started_at: str | None = None
    final_event: dict[str, Any] | None = None
    terminal_error: dict[str, Any] | None = None

    while True:
        line = read_logged(channel, deadline - time.monotonic(), raw_log)
        if line is None:
            status = "recovery_timeout" if final_event is not None else "timeout"
            return {
                "status": status,
                "all_passed": False,
                "start_seen": start_seen,
                "events": events,
                "bench": bench,
                "final": final_event,
                "terminal": terminal_error,
                "started_at": started_at,
                "finished_at": now_timestamp(),
            }
        event = parse_event(line)
        if event is None:
            continue
        events.append(event)
        event_type = event["type"]
        if event_type == "READY":
            if final_event is not None:
                all_passed = _reported_all_passed(
                    final_event["fields"], normalized["passes"]
                )
                return {
                    "status": "pass" if all_passed else "fail",
                    "all_passed": all_passed,
                    "start_seen": start_seen,
                    "events": events,
                    "bench": bench,
                    "final": final_event,
                    "terminal": terminal_error,
                    "recovery_ready": event,
                    "started_at": started_at,
                    "finished_at": now_timestamp(),
                }
            if start_seen:
                return {
                    "status": "watchdog_reset",
                    "all_passed": False,
                    "start_seen": True,
                    "events": events,
                    "bench": bench,
                    "terminal": terminal_error,
                    "started_at": started_at,
                    "finished_at": now_timestamp(),
                }
            continue
        if event_type == "BOOT_ERROR":
            return {
                "status": "boot_error",
                "all_passed": False,
                "start_seen": start_seen,
                "events": events,
                "bench": bench,
                "terminal": event,
                "started_at": started_at,
                "finished_at": now_timestamp(),
            }
        if not _event_matches_profile(event, normalized):
            continue
        if event_type == "START":
            start_seen = True
            started_at = now_timestamp()
            continue
        if event_type == "BENCH":
            fields = event["fields"]
            try:
                byte_count = int(fields["bytes"], 0)
                timer_hz = int(fields["timer_hz"], 0)
            except (KeyError, ValueError):
                continue
            for operation in ("write", "read", "copy"):
                try:
                    ticks = int(fields[f"{operation}_ticks"], 0)
                except (KeyError, ValueError):
                    continue
                if byte_count > 0 and ticks > 0:
                    bench.append(
                        {
                            "op": operation,
                            "bytes": byte_count,
                            "ticks": ticks,
                            "timer_hz": timer_hz,
                            "bytes_per_second": byte_count * timer_hz / ticks,
                        }
                    )
            continue
        if event_type == "FINAL":
            if start_seen:
                final_event = event
            continue
        if event_type == "REJECT":
            return {
                "status": "reject",
                "all_passed": False,
                "start_seen": start_seen,
                "events": events,
                "bench": bench,
                "terminal": event,
                "started_at": started_at,
                "finished_at": now_timestamp(),
            }
        if event_type == "ERROR":
            terminal_error = event


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.stream: TextIO | None = None

    def __enter__(self) -> "JsonlWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a", encoding="utf-8")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.stream is not None:
            self.stream.close()

    def append(self, record: Mapping[str, Any]) -> None:
        if self.stream is None:
            raise LabError("JSONL 輸出尚未開啟")
        json.dump(record, self.stream, ensure_ascii=False, sort_keys=True)
        self.stream.write("\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())


def load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError as exc:
            raise LabError(f"無法讀取 JSONL {path}：{exc}") from exc
        with stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LabError(f"JSONL {path} 第 {line_number} 行格式無效") from exc
                if not isinstance(record, dict):
                    raise LabError(f"JSONL {path} 第 {line_number} 行不是物件")
                records.append(record)
    return records


def completed_profile_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(record["key"])
        for record in load_jsonl([path])
        if "key" in record
        and record.get("status")
        in {
            "pass",
            "fail",
            "timeout",
            "recovery_timeout",
            "watchdog_reset",
            "reject",
            "error",
            "boot_error",
            "sync_error",
        }
    }


def completed_profile_counts(path: Path) -> Counter[str]:
    if not path.exists():
        return Counter()
    return Counter(
        str(record["key"])
        for record in load_jsonl([path])
        if "key" in record
        and record.get("status")
        in {
            "pass",
            "fail",
            "timeout",
            "recovery_timeout",
            "watchdog_reset",
            "reject",
            "error",
            "boot_error",
            "sync_error",
        }
    )


def make_record(
    profile: Mapping[str, Any],
    result: Mapping[str, Any],
    scan_fields: Mapping[str, Sequence[Any]] | None = None,
    coordinates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "protocol": PROTOCOL,
        "recorded_at": now_timestamp(),
        "key": profile_key(profile),
        "profile": normalize_profile(profile),
        **dict(result),
    }
    if scan_fields is not None:
        record["scan"] = {
            "fields": {name: list(values) for name, values in scan_fields.items()},
            "coordinates": dict(coordinates or {}),
        }
    return record


def run_external_reset(
    command: str, timeout: float, raw_log: RawUartLog | None = None
) -> None:
    try:
        arguments = shlex.split(command)
    except ValueError as exc:
        raise LabError(f"外部重設命令格式無效：{exc}") from exc
    if not arguments:
        raise LabError("外部重設命令不可為空")
    if raw_log is not None:
        raw_log.write("RESET", " ".join(arguments))
    try:
        result = subprocess.run(
            arguments,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabError(f"外部重設命令執行失敗：{exc}") from exc
    if raw_log is not None and result.stdout:
        for line in result.stdout.splitlines():
            raw_log.write("RESET-OUT", line)
    if result.returncode != 0:
        raise LabError(f"外部重設命令回傳 {result.returncode}")


def _is_m2_candidate(record: Mapping[str, Any]) -> bool:
    profile = record.get("profile")
    return (
        isinstance(profile, dict)
        and profile.get("level") == "M2"
        and record.get("status") == "pass"
        and record.get("all_passed") is True
    )


def _bench_score(record: Mapping[str, Any]) -> dict[str, Any] | None:
    by_operation: dict[str, list[float]] = {"read": [], "write": [], "copy": []}
    timer_rates: set[int] = set()
    for item in record.get("bench", []):
        if not isinstance(item, dict):
            continue
        op = str(item.get("op", "")).lower()
        if op not in by_operation:
            continue
        try:
            timer_hz = int(item.get("timer_hz", TIMER_HZ))
            timer_rates.add(timer_hz)
            if "bytes_per_second" in item:
                rate = float(item["bytes_per_second"])
            else:
                rate = int(item["bytes"]) * timer_hz / int(item["ticks"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            by_operation[op].append(rate)
    if any(not rates for rates in by_operation.values()):
        return None
    worst_by_operation = {name: min(rates) for name, rates in by_operation.items()}
    worst = min(worst_by_operation.values())
    return {
        "timer_hz": sorted(timer_rates),
        "bytes_per_second": worst_by_operation,
        "worst_bytes_per_second": worst,
        "worst_mib_per_second": worst / (1024 * 1024),
    }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _margin_for_candidate(
    candidate: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    profile = candidate["profile"]
    scan = candidate.get("scan", {})
    domains = scan.get("fields", {}) if isinstance(scan, dict) else {}
    if not isinstance(domains, dict) or not domains:
        return {
            "fields": {},
            "minimum_radius_steps": 0,
            "total_span_steps": 0,
            "boundary_truncated": True,
        }

    field_results: dict[str, Any] = {}
    for field, raw_domain in domains.items():
        if (
            field not in SCAN_FIELDS
            or not isinstance(raw_domain, list)
            or not raw_domain
        ):
            continue
        domain = list(dict.fromkeys(raw_domain))
        if all(isinstance(item, int) and not isinstance(item, bool) for item in domain):
            domain.sort()
        try:
            center_value = scan_field_value(profile, field)
            center = domain.index(center_value)
        except (KeyError, ValueError):
            continue
        packed = PACKED_SCAN_FIELDS.get(field)

        def signature(item: Mapping[str, Any]) -> tuple[Any, ...]:
            result: list[tuple[str, Any]] = []
            for name in PROFILE_FIELDS:
                if name == "id" or name == field:
                    continue
                value = item[name]
                if packed is not None and name == packed[0]:
                    _, shift, mask = packed
                    value = int(value) & ~(mask << shift)
                result.append((name, _freeze(value)))
            return tuple(result)

        fixed_signature = signature(profile)
        pass_by_value: dict[Any, bool] = {}
        for record in records:
            other = record.get("profile")
            if not isinstance(other, dict):
                continue
            other_signature = signature(other)
            if other_signature == fixed_signature:
                pass_by_value[_freeze(scan_field_value(other, field))] = (
                    _is_m2_candidate(record)
                )

        left = center
        while left > 0 and pass_by_value.get(_freeze(domain[left - 1]), False):
            left -= 1
        right = center
        while right + 1 < len(domain) and pass_by_value.get(
            _freeze(domain[right + 1]), False
        ):
            right += 1
        left_steps = center - left
        right_steps = right - center
        left_truncated = left == 0
        right_truncated = right == len(domain) - 1
        field_result: dict[str, Any] = {
            "left_steps": left_steps,
            "right_steps": right_steps,
            "radius_steps": min(left_steps, right_steps),
            "span_steps": right - left + 1,
            "left_value": domain[left],
            "right_value": domain[right],
            "left_boundary_truncated": left_truncated,
            "right_boundary_truncated": right_truncated,
            "boundary_truncated": left_truncated or right_truncated,
        }
        if all(isinstance(item, int) for item in domain):
            field_result["radius_value"] = min(
                center_value - domain[left], domain[right] - center_value
            )
        field_results[field] = field_result

    if not field_results:
        return {
            "fields": {},
            "minimum_radius_steps": 0,
            "total_span_steps": 0,
            "boundary_truncated": True,
        }
    return {
        "fields": field_results,
        "minimum_radius_steps": min(
            item["radius_steps"] for item in field_results.values()
        ),
        "total_span_steps": sum(item["span_steps"] for item in field_results.values()),
        "boundary_truncated": any(
            item["boundary_truncated"] for item in field_results.values()
        ),
    }


def _candidate_summary(
    record: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    summary = {
        "key": record.get("key", profile_key(record["profile"])),
        "profile": record["profile"],
        "margin": _margin_for_candidate(record, records),
        "performance": _bench_score(record),
    }
    if "observations" in record:
        summary["observations"] = record["observations"]
    return summary


def aggregate_records(
    records: Sequence[Mapping[str, Any]],
    min_samples: int,
) -> list[dict[str, Any]]:
    if min_samples < 1:
        raise LabError("最少樣本數必須大於 0")
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        profile = record.get("profile")
        if not isinstance(profile, dict):
            continue
        try:
            key = profile_key(profile)
        except LabError:
            continue
        groups.setdefault(key, []).append(record)

    aggregated: list[dict[str, Any]] = []
    for key, observations in groups.items():
        representative = dict(observations[-1])
        profile = normalize_profile(representative["profile"])
        passed = sum(1 for item in observations if _is_m2_candidate(item))
        stable = len(observations) >= min_samples and passed == len(observations)
        bench: list[Any] = []
        for item in observations:
            if isinstance(item.get("bench"), list):
                bench.extend(item["bench"])
        representative.update(
            {
                "key": key,
                "profile": profile,
                "status": "pass" if stable else "fail",
                "all_passed": stable,
                "bench": bench,
                "observations": {
                    "total": len(observations),
                    "passed": passed,
                    "failed": len(observations) - passed,
                    "pass_rate": passed / len(observations),
                    "minimum_required": min_samples,
                },
            }
        )
        aggregated.append(representative)
    return aggregated


def build_ranking(
    records: Sequence[Mapping[str, Any]],
    min_samples: int = 1,
) -> dict[str, Any]:
    all_records = aggregate_records(records, min_samples)
    candidates = [
        _candidate_summary(record, all_records)
        for record in all_records
        if _is_m2_candidate(record)
    ]

    def margin_tuple(candidate: Mapping[str, Any]) -> tuple[int, int, int]:
        margin = candidate["margin"]
        return (
            int(margin["minimum_radius_steps"]),
            int(margin["total_span_steps"]),
            0 if margin["boundary_truncated"] else 1,
        )

    def performance_value(candidate: Mapping[str, Any]) -> float:
        performance = candidate.get("performance")
        return float(performance["worst_bytes_per_second"]) if performance else -1.0

    safe = (
        min(
            candidates,
            key=lambda candidate: (
                int(candidate["profile"]["clk"]),
                -margin_tuple(candidate)[0],
                -margin_tuple(candidate)[1],
                -margin_tuple(candidate)[2],
                -performance_value(candidate),
            ),
        )
        if candidates
        else None
    )
    performance_candidates = [
        candidate for candidate in candidates if candidate["performance"] is not None
    ]
    best = (
        max(
            performance_candidates,
            key=lambda candidate: (
                performance_value(candidate),
                margin_tuple(candidate),
            ),
        )
        if performance_candidates
        else None
    )
    observed_margin_candidates = [
        candidate for candidate in candidates if candidate["margin"]["fields"]
    ]
    complete_margin_candidates = [
        candidate
        for candidate in observed_margin_candidates
        if not candidate["margin"]["boundary_truncated"]
    ]

    def maximum_margin_from(
        margin_candidates: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        return (
            max(
                margin_candidates,
                key=lambda candidate: (
                    margin_tuple(candidate),
                    performance_value(candidate),
                    -int(candidate["profile"]["clk"]),
                ),
            )
            if margin_candidates
            else None
        )

    maximum_margin = maximum_margin_from(complete_margin_candidates)
    widest_observed = maximum_margin_from(observed_margin_candidates)

    return {
        "protocol": PROTOCOL,
        "minimum_samples": min_samples,
        "parameter_groups": len(all_records),
        "m2_all_passed_candidates": len(candidates),
        "safe_candidate": safe,
        "best_performance_candidate": best,
        "maximum_margin_candidate": maximum_margin,
        "widest_observed_candidate": widest_observed,
        "definitions": {
            "sample_gate": "相同參數的全部 M2 樣本皆通過，且樣本數達門檻，才可列入候選",
            "safe_candidate": "通過樣本門檻後，時脈最低者優先，同時脈再依連續通過半徑排序",
            "best_performance_candidate": "讀、寫、複製三者最差吞吐量最高者",
            "maximum_margin_candidate": "左右失敗邊界完整時，各掃描欄位最小連續通過半徑最高者",
            "widest_observed_candidate": "包含邊界截尾資料的最寬已觀察候選，不得當成最大容錯結論",
        },
    }


def print_progress(record: Mapping[str, Any]) -> None:
    profile = record["profile"]
    status_text = {
        "pass": "通過",
        "fail": "失敗",
        "timeout": "逾時",
        "recovery_timeout": "安全設定恢復逾時",
        "watchdog_reset": "watchdog 重啟",
        "reject": "拒絕",
        "error": "錯誤",
        "boot_error": "安全啟動失敗",
        "sync_error": "同步錯誤",
    }.get(str(record["status"]), str(record["status"]))
    print(
        f"{profile['id']} clk={profile['clk']} level={profile['level']}：{status_text}",
        flush=True,
    )


def recover_and_sync(
    channel: Any,
    command: str,
    args: argparse.Namespace,
    raw_log: RawUartLog,
) -> None:
    try:
        wait_ready(channel, command, args.sync_timeout, raw_log)
        return
    except LabError:
        if not args.reset_command:
            raise
    run_external_reset(args.reset_command, args.reset_timeout, raw_log)
    time.sleep(args.reset_delay)
    wait_ready(channel, "I", args.sync_timeout, raw_log)


def run_profiles(
    profiles: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    scan_fields: Mapping[str, Sequence[Any]] | None,
    args: argparse.Namespace,
) -> int:
    remaining = completed_profile_counts(args.jsonl) if args.resume else Counter()
    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for profile, coordinates in profiles:
        key = profile_key(profile)
        if remaining[key]:
            remaining[key] -= 1
            continue
        pending.append((profile, coordinates))
    if not pending:
        print("沒有待執行的設定；JSONL 已包含全部候選。")
        return 0

    with (
        RawUartLog(args.uart_log) as raw_log,
        JsonlWriter(args.jsonl) as writer,
        PosixSerial(args.tty, args.baud) as channel,
    ):
        first = True
        for profile, coordinates in pending:
            try:
                recover_and_sync(channel, "I" if first else "Z", args, raw_log)
            except LabError as exc:
                result = {
                    "status": "sync_error",
                    "all_passed": False,
                    "error": str(exc),
                    "finished_at": now_timestamp(),
                }
            else:
                result = execute_profile(channel, profile, args.timeout, raw_log)
            record = make_record(profile, result, scan_fields, coordinates)
            writer.append(record)
            print_progress(record)
            if (
                result["status"] in {"timeout", "recovery_timeout"}
                and args.reset_command
            ):
                run_external_reset(args.reset_command, args.reset_timeout, raw_log)
                time.sleep(args.reset_delay)
                first = True
            else:
                first = result["status"] == "sync_error"
    return 0


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tty", required=True, help="UART 裝置，例如 /dev/ttyUSB0")
    parser.add_argument(
        "--baud", type=int, default=115200, help="UART 速率，預設 115200"
    )
    parser.add_argument(
        "--sync-timeout", type=float, default=5.0, help="等待 READY 的秒數"
    )
    parser.add_argument(
        "--reset-command", help="同步失敗時執行的外部重設命令；不經 shell"
    )
    parser.add_argument(
        "--reset-timeout", type=float, default=10.0, help="外部重設命令逾時秒數"
    )
    parser.add_argument(
        "--reset-delay", type=float, default=1.0, help="外部重設後等待秒數"
    )
    parser.add_argument(
        "--uart-log",
        type=Path,
        default=Path("m4zlab2-uart.log"),
        help="原始 UART 時戳日誌",
    )


def add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile", required=True, type=Path, help="完整 M4ZLAB2 JSON 設定檔"
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="欄位=值",
        help="覆寫單一設定，可重複",
    )


def build_parser() -> ChineseArgumentParser:
    parser = ChineseArgumentParser(
        description="BPI-M4 Zero M4ZLAB2 DDR 實驗器主機端工具"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="命令",
        parser_class=ChineseArgumentParser,
    )

    run_parser = subparsers.add_parser("run", help="執行單一完整設定")
    add_connection_arguments(run_parser)
    add_profile_arguments(run_parser)
    run_parser.add_argument(
        "--timeout", type=float, default=30.0, help="START 至 FINAL 的逾時秒數"
    )
    run_parser.add_argument(
        "--jsonl",
        type=Path,
        default=Path("m4zlab2-results.jsonl"),
        help="增量結果 JSONL",
    )
    run_parser.add_argument(
        "--resume", action="store_true", help="略過 JSONL 已完成的同一設定"
    )
    run_parser.add_argument(
        "--repeat", type=int, default=1, help="同一參數重複 watchdog 重啟測試次數"
    )

    scan_parser = subparsers.add_parser("scan", help="執行多欄位笛卡兒掃描")
    add_connection_arguments(scan_parser)
    add_profile_arguments(scan_parser)
    scan_parser.add_argument(
        "--field",
        action="append",
        required=True,
        metavar="欄位=值",
        help="掃描值或含尾端範圍，可重複",
    )
    scan_parser.add_argument(
        "--max-candidates", type=int, default=10_000, help="候選組合數上限"
    )
    scan_parser.add_argument(
        "--timeout", type=float, default=30.0, help="每組 START 至 FINAL 的逾時秒數"
    )
    scan_parser.add_argument(
        "--jsonl",
        type=Path,
        default=Path("m4zlab2-results.jsonl"),
        help="增量結果 JSONL",
    )
    scan_parser.add_argument(
        "--resume", action="store_true", help="略過 JSONL 已完成的候選"
    )
    scan_parser.add_argument(
        "--repeat", type=int, default=1, help="每組參數重複 watchdog 重啟測試次數"
    )

    rank_parser = subparsers.add_parser(
        "rank", help="由 JSONL 選出保險、效能與容錯候選"
    )
    rank_parser.add_argument(
        "jsonl", nargs="+", type=Path, metavar="JSONL", help="一個或多個掃描結果"
    )
    rank_parser.add_argument(
        "--output", type=Path, help="另存排名 JSON；預設輸出至終端"
    )
    rank_parser.add_argument(
        "--min-samples", type=int, default=3, help="候選所需全數通過樣本數，預設 3"
    )

    info_parser = subparsers.add_parser("info", help="讀取 SPL READY 資訊")
    add_connection_arguments(info_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("缺少命令；請使用 run、scan、rank 或 info")
    try:
        if args.command == "rank":
            ranking = build_ranking(load_jsonl(args.jsonl), args.min_samples)
            payload = json.dumps(ranking, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload, encoding="utf-8")
            else:
                sys.stdout.write(payload)
            return 0

        if args.command == "info":
            with (
                RawUartLog(args.uart_log) as raw_log,
                PosixSerial(args.tty, args.baud) as channel,
            ):
                try:
                    result = wait_ready(channel, "I", args.sync_timeout, raw_log)
                except LabError:
                    if not args.reset_command:
                        raise
                    run_external_reset(args.reset_command, args.reset_timeout, raw_log)
                    time.sleep(args.reset_delay)
                    result = wait_ready(channel, "I", args.sync_timeout, raw_log)
            print(
                json.dumps(
                    {"協定": PROTOCOL, "裝置資訊": result}, ensure_ascii=False, indent=2
                )
            )
            return 0

        base = apply_overrides(load_profile(args.profile), args.set)
        if args.command == "run":
            build_run_command(base)
            profiles = repeat_profiles([(base, {})], args.repeat)
            return run_profiles(profiles, None, args)
        if args.command == "scan":
            fields = parse_scan_fields(args.field)
            matrix = expand_matrix(base, fields, args.max_candidates)
            return run_profiles(repeat_profiles(matrix, args.repeat), fields, args)
        raise LabError(f"不支援的命令：{args.command}")
    except LabError as exc:
        sys.stderr.write(f"錯誤：{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
