#!/usr/bin/env python3
"""明確指定串口才執行的 SPL1 主機工具；不探索裝置、不控制電源。

UART 使用 pyserial 排他開啟，XMODEM 使用本機 lrzsz sx；不實作傳輸協定。
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import subprocess
import sys
import tempfile
import termios
import time
from typing import Iterator, Sequence

if __package__:
    from . import bpi_sram_package as package
else:
    import bpi_sram_package as package


MAX_LINE_BYTES = 512
MAX_CONTROL_BYTES = 65536
MAX_STDERR_BYTES = 65536
CAPABILITIES = "uart-ram,sd-read,smoke-run"


class UartError(ValueError):
    """可公開顯示且不含原始 UART 資料的錯誤。"""


def parse_control_line(raw: bytes) -> tuple[str, dict[str, str]] | None:
    """只接受行首的固定識別碼；一般開機輸出不構成成功證據。"""
    if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
        raise UartError("控制行超長或不完整")
    body = raw[:-1]
    if body.endswith(b"\r"):
        body = body[:-1]
    if any(byte < 32 or byte > 126 for byte in body):
        raise UartError("控制行含不允許的原始位元組")
    line = body.decode("ascii")
    if not line.startswith(("BPI-SUP1 ", "BPI-SPL2 ")):
        if line.startswith("BPI-"):
            raise UartError("控制行識別碼不符")
        return None
    prefix, *tokens = line.split(" ")
    fields: dict[str, str] = {}
    for token in tokens:
        match = re.fullmatch(r"([a-z][a-z0-9_]*)=([A-Za-z0-9_,.-]+)", token)
        if not match or match[1] in fields:
            raise UartError("控制欄位格式錯誤或重複")
        fields[match[1]] = match[2]
    if "event" not in fields:
        raise UartError("控制行缺少事件")
    return prefix, fields


class SupervisorSession:
    """只從已驗證的協定狀態產生固定命令，不執行裝置輸出的文字。"""

    def __init__(self, channel: object, timeout: float, clock=None) -> None:
        self.channel = channel
        self.timeout = timeout
        self.clock = clock or time.monotonic
        self.nonce = 0
        self.probed = False
        self.loading = False
        self.loaded_nonce: int | None = None

    def _send(self, command: str, value: int | None = None) -> None:
        if command not in ("I", "U", "S", "R"):
            raise UartError("拒絕未知命令")
        text = f"{command} {self.nonce}"
        if value is not None:
            text += f" {value}"
        data = (text + "\n").encode("ascii")
        if self.channel.write(data) != len(data):
            raise UartError("UART 命令未完整寫入")

    def _wait(self, event: str, prefix: str = "BPI-SUP1") -> dict[str, str]:
        deadline = self.clock() + self.timeout
        consumed = 0
        line = bytearray()
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise UartError(f"等待 {event} 事件逾時")
            read_timeout = min(0.1, remaining)
            if self.channel.timeout != read_timeout:
                self.channel.timeout = read_timeout
            # 不使用預讀緩衝或 read_until，loading 換行後的 C 必須留給 sx。
            byte = self.channel.read(1)
            if not byte:
                continue
            if len(byte) != 1:
                raise UartError("UART 讀取未遵守單位元組界限")
            consumed += 1
            line.extend(byte)
            if len(line) > MAX_LINE_BYTES or consumed > MAX_CONTROL_BYTES:
                raise UartError("控制輸出超過允許上限")
            if byte != b"\n":
                continue
            parsed = parse_control_line(bytes(line))
            line.clear()
            if parsed is None:
                continue
            identity, fields = parsed
            if identity == "BPI-SUP1" and fields["event"] == "ready" and event == "info":
                if fields.get("abi") != "1" or fields.get("board") != "06180001":
                    raise UartError("ready 的 ABI 或板型不符")
                continue
            if identity != prefix or fields["event"] != event:
                raise UartError("收到非預期識別碼或事件，拒絕繼續")
            if prefix == "BPI-SUP1":
                if fields.get("nonce") != str(self.nonce):
                    raise UartError("回應 nonce 不符本次交握")
            elif fields.get("nonce_hex") != f"{self.nonce:08x}":
                raise UartError("smoke 的 nonce_hex 不符本次載入")
            return fields

    def probe(self) -> dict[str, object]:
        self.loaded_nonce = None
        self.probed = False
        self.loading = False
        self.nonce = secrets.randbits(32)
        self.channel.reset_input_buffer()
        self._send("I")
        fields = self._wait("info")
        if fields.get("abi") != "1" or fields.get("board") != "06180001":
            raise UartError("交握 ABI 或板型不符")
        if fields.get("capabilities") != CAPABILITIES:
            raise UartError("交握能力清單不符")
        self.probed = True
        return {"nonce": self.nonce, "abi": 1, "board": "06180001",
                "capabilities": CAPABILITIES.split(",")}

    def begin_load(self, transport: str, value: int) -> None:
        if not self.probed:
            raise UartError("尚未完成本次交握")
        self.loaded_nonce = None
        self.loading = False
        if type(value) is not int:
            raise UartError("載入參數必須是整數")
        if transport == "U":
            if not 1024 <= value <= package.MAX_PACKAGE_BYTES or value % 512:
                raise UartError("UART 封包總長不符")
        elif transport == "S":
            if not 0 <= value <= 4:
                raise UartError("槽位必須介於 0 與 4")
        else:
            raise UartError("不支援的載入方式")
        self._send(transport, value)
        fields = self._wait("loading")
        if fields.get("transport") != transport:
            raise UartError("載入方式回應不符")
        self.loading = True

    def finish_load(self) -> None:
        if not self.loading:
            raise UartError("尚未收到本次 loading，不允許接受載入結果")
        self.loading = False
        self.loaded_nonce = None
        fields = self._wait("loaded")
        if fields.get("result") != "0":
            raise UartError("韌體未成功驗證載入內容")
        self.loaded_nonce = self.nonce

    def run_smoke(self) -> dict[str, object]:
        if self.loaded_nonce != self.nonce:
            raise UartError("沒有同一 nonce 的成功載入，不允許執行")
        self.loaded_nonce = None
        self._send("R")
        if self._wait("handoff").get("entry") != "00030000":
            raise UartError("交接入口不符")
        fields = self._wait("smoke", "BPI-SPL2")
        stack = fields.get("sp", "")
        if (fields.get("result") != "pass" or fields.get("el") != "3"
                or fields.get("ddr") != "off" or not re.fullmatch(r"[0-9a-f]{8}", stack)
                or not 0x40000 <= int(stack, 16) < 0x48000):
            raise UartError("smoke 執行證據不符合 SRAM 契約")
        return {"nonce_hex": fields["nonce_hex"], "sp": stack, "el": 3,
                "ddr": "off", "result": "pass"}


@contextmanager
def staged_package(path: Path) -> Iterator[tuple[Path, dict[str, int | str]]]:
    """先驗證一般檔案，再將固定內容寫入私有目錄，傳輸不重讀原路徑。"""
    blob = package.read_regular_file(path, package.MAX_PACKAGE_BYTES)
    metadata = package.parse_package(blob)
    with tempfile.TemporaryDirectory(prefix="bpi-sram-uart-") as directory:
        staged = Path(directory) / "package.spl2"
        package.write_new_regular_file(staged, blob)
        os.chmod(staged, 0o400)
        yield staged, metadata


@contextmanager
def open_serial(port: str, baud: int, timeout: float) -> Iterator[object]:
    """只開啟明確指定的本機串口，匯入模組本身不接觸串口。"""
    try:
        import serial
    except ImportError as exc:
        raise UartError("缺少 pyserial，請先安裝主機相依套件") from exc
    channel = serial.Serial(
        port=None, baudrate=baud, timeout=min(timeout, 0.1), write_timeout=timeout,
        exclusive=True, xonxoff=False, rtscts=False, dsrdtr=False,
    )
    try:
        channel.dtr = False
        channel.rts = False
        channel.port = port
        channel.open()
        yield channel
    finally:
        channel.close()


def send_xmodem(channel: object, staged: Path, executable: str, timeout: float) -> int:
    """sx 使用同一串口描述符；診斷只受限擷取，不公開原始內容。"""
    descriptor = channel.fileno()
    saved_attributes = termios.tcgetattr(descriptor)
    saved_blocking = os.get_blocking(descriptor)
    process = None
    captured = bytearray()
    deadline = time.monotonic() + timeout
    try:
        os.set_blocking(descriptor, True)
        process = subprocess.Popen(
            [executable, "-b", "-X", "-q", "--", str(staged)],
            stdin=descriptor, stdout=descriptor, stderr=subprocess.PIPE,
            close_fds=True, shell=False,
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            eof = False
            while not eof or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UartError("sx 傳輸逾時，已停止本次操作")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        eof = True
                    else:
                        if len(captured) + len(chunk) > MAX_STDERR_BYTES:
                            raise UartError("sx 診斷輸出超過允許上限")
                        captured.extend(chunk)
            if process.returncode != 0:
                raise UartError(f"sx 傳輸失敗（exit_code={process.returncode}）")
        return len(captured)
    finally:
        try:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=2)
                finally:
                    process.stderr.close()
        finally:
            try:
                termios.tcsetattr(descriptor, termios.TCSANOW, saved_attributes)
            finally:
                os.set_blocking(descriptor, saved_blocking)


def _named_port(value: str) -> str:
    if (not value or not Path(value).is_absolute() or "://" in value
            or any(character in value for character in "*?[]")
            or any(ord(character) < 32 for character in value)):
        raise argparse.ArgumentTypeError("必須指定本機串口絕對路徑，不接受探索模式或網址")
    return value


def _timeout(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("逾時秒數格式無效") from exc
    if not math.isfinite(result) or not 0 < result <= 120:
        raise argparse.ArgumentTypeError("逾時秒數必須大於零且不超過 120")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = package.ChineseArgumentParser(
        description="明確指定 UART 才執行 SPL1 操作；不探索設備、不控制電源、不寫入 SD。",
    )
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    for name, description in (
        ("probe", "以新 nonce 驗證指定串口的 SPL1 身分"),
        ("upload", "驗證封包後以 sx 的 XMODEM 傳入 SRAM"),
        ("load-slot", "要求 SPL1 唯讀載入指定 SD 槽位"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("--port", type=_named_port, required=True, metavar="串口", help="本機串口絕對路徑")
        command.add_argument("--baud", type=int, choices=(9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600),
                             default=115200, help="串口速率，預設 115200")
        command.add_argument("--timeout", type=_timeout, default=10.0, metavar="秒", help="每個控制事件的等待上限")
        if name != "probe":
            command.add_argument("--run", action="store_true", help="成功載入後才交接執行，並驗證 smoke 證據")
        if name == "upload":
            command.add_argument("--input", type=Path, required=True, metavar="封包", help="已封裝的 SPL2 一般檔案")
            command.add_argument("--transfer-timeout", type=_timeout, default=120.0, metavar="秒", help="sx 傳輸等待上限")
        if name == "load-slot":
            command.add_argument("--slot", type=int, choices=range(5), required=True, help="唯讀槽位編號")
    return parser


def execute(args: argparse.Namespace) -> dict[str, object]:
    with ExitStack() as stack:
        staged = metadata = executable = None
        if args.command == "upload":
            staged, metadata = stack.enter_context(staged_package(args.input))
            executable = shutil.which("sx")
            if executable is None:
                raise UartError("找不到 lrzsz 的 sx，尚未開啟串口")
        channel = stack.enter_context(open_serial(args.port, args.baud, args.timeout))
        session = SupervisorSession(channel, args.timeout)
        result = session.probe()
        result.update({"command": args.command, "event": "info", "ran": False})
        if args.command == "probe":
            return result
        if args.command == "upload":
            session.begin_load("U", metadata["raw_size"])
            result["stderr_bytes"] = send_xmodem(channel, staged, executable, args.transfer_timeout)
            result["package"] = metadata
        else:
            session.begin_load("S", args.slot)
            result["slot"] = args.slot
        session.finish_load()
        result.update({"event": "loaded", "result": 0})
        if args.run:
            result["smoke"] = session.run_smoke()
            result.update({"event": "smoke", "ran": True})
        return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (UartError, package.PackageError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except (OSError, termios.error, subprocess.SubprocessError):
        print("錯誤：主機檔案、串口或傳輸程序操作失敗；未輸出原始通訊內容", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("錯誤：操作已取消，未安排重試或重新上電", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
