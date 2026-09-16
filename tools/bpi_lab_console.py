#!/usr/bin/env python3
"""借用既有串口的主控台；不登入、不控制電源、不解讀日誌為命令。

ConsoleSession(channel, log_path=..., monotonic=...) 不擁有 channel。
傳輸須提供 read(size)、write(data)，並遵守可設定的 timeout/write_timeout；
替身亦可採非阻塞讀寫。send 不自動補換行，所有 TX（包含密碼）均不記錄。
RX 日誌逐次原樣寫入；若遠端回顯密碼，原始 RX 仍會保留，須保護日誌。
交接後應透過本物件的 read/write 消耗預讀資料，不繞過未清空的 buffer。
不支援同時由多個讀取者使用串口，也不接管外部 sx 的描述符讀取。
"""

from __future__ import annotations

from contextlib import ExitStack
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import time
from typing import NamedTuple

if __package__:
    from . import bpi_sram_uart as uart
else:
    import bpi_sram_uart as uart


class ConsoleError(ValueError):
    """不洩露命令、密碼或原始 RX 的操作錯誤。"""


class ConsoleTimeout(ConsoleError, TimeoutError):
    """絕對截止時間已到；未消耗 RX 仍可取回。"""


class ExpectResult(NamedTuple):
    before: bytes
    matched: bytes
    groups: tuple[bytes | None, ...] = ()


class ShellResult(NamedTuple):
    output: bytes
    exitcode: int


def _seconds(value: float) -> float:
    if isinstance(value, bool):
        raise ConsoleError("逾時秒數必須為有限正數")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ConsoleError("逾時秒數必須為有限正數") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConsoleError("逾時秒數必須為有限正數")
    return value


def _bytes(value: bytes | str) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if not isinstance(value, bytes):
        raise ConsoleError("資料須為 bytes 或文字")
    return value


class ConsoleSession:
    """持續保留 RX 尾端；close 只關閉新日誌，不關閉借用的連線。

    expect 接受 timeout 秒數或 monotonic 時基的 deadline，二者擇一。
    回傳 before、matched、groups；成功只消耗到匹配末端，逾時不消耗。
    run_shell 使用同一絕對期限涵蓋傳送及兩個標記，不自動重試或送中斷。
    max_buffer_bytes 預設 4 MiB；超界先保存整次 RX，再永久停止本 session。
    標記防止舊輸出與回顯誤判，不構成對惡意遠端的身分驗證。
    """

    def __init__(self, channel, *, log_path, monotonic=time.monotonic,
                 max_buffer_bytes=4 * 1024 * 1024):
        if type(max_buffer_bytes) is not int or max_buffer_bytes <= 0:
            raise ConsoleError("RX 緩衝上限須為正整數位元組數")
        self.channel = channel
        self.monotonic = monotonic
        self.max_buffer_bytes = max_buffer_bytes
        self._buffer = bytearray()
        self._fd = None
        self._failure = None
        try:
            with uart.package._parent_directory(Path(log_path)) as (parent, name):
                self._fd = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    | os.O_CLOEXEC, 0o600, dir_fd=parent,
                )
                os.fchmod(self._fd, 0o600)
                os.fsync(parent)
        except (OSError, uart.package.PackageError) as exc:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise ConsoleError("無法建立私有 RX 日誌；拒絕既有路徑與符號連結") from exc

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _ensure_open(self):
        if self._fd is None:
            raise ConsoleError("主控台日誌已關閉")
        if self._failure is not None:
            raise ConsoleError(self._failure)

    @property
    def buffered(self) -> bytes:
        """尚未消耗且已寫入日誌的原始 RX。"""
        return bytes(self._buffer)

    @property
    def timeout(self):
        return getattr(self.channel, "timeout", 0.0)

    @timeout.setter
    def timeout(self, value):
        self.channel.timeout = value

    def _receive(self, size: int) -> bytes:
        self._ensure_open()
        try:
            data = self.channel.read(size)
        except OSError as exc:
            raise ConsoleError("主控台讀取失敗，未自動重試") from exc
        if not isinstance(data, bytes):
            raise ConsoleError("傳輸 read 必須回傳 bytes")
        # 先保存整個讀取結果；超界後永久停止本 session，不能以截斷資料繼續判定。
        self._failure = "RX 日誌未完整寫入，停止本次連線操作"
        offset = 0
        try:
            while offset < len(data):
                count = os.write(self._fd, data[offset:])
                if not 0 < count <= len(data) - offset:
                    raise ConsoleError(self._failure)
                offset += count
        except OSError as exc:
            raise ConsoleError(self._failure) from exc
        available = self.max_buffer_bytes - len(self._buffer)
        self._buffer.extend(data[:available])
        if len(data) > available:
            self._failure = "RX 超過緩衝上限；本次完整原始 RX 已寫入日誌，停止本次連線操作"
            raise ConsoleError(self._failure)
        self._failure = None
        return data

    def read(self, size: int = 1) -> bytes:
        """供交接使用；先回傳已記錄的尾端，不重複寫入日誌。"""
        self._ensure_open()
        if type(size) is not int or size < 0:
            raise ConsoleError("讀取長度須為非負整數")
        if size and not self._buffer:
            self._receive(size)
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def _deadline(self, timeout, deadline):
        if deadline is not None:
            if (timeout is not None or isinstance(deadline, bool)
                    or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
                raise ConsoleError("請擇一提供有效的 timeout 或 deadline")
            return deadline
        return self.monotonic() + _seconds(10.0 if timeout is None else timeout)

    def _remaining(self, deadline):
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise ConsoleTimeout("主控台操作逾時；RX 日誌與未消耗尾端已保留")
        return remaining

    def _read_until(self, deadline):
        remaining = self._remaining(deadline)
        previous = self.timeout
        try:
            self.channel.timeout = min(0.1, remaining)
            self._receive(4096)
        finally:
            self.channel.timeout = previous
        self._remaining(deadline)

    def _send_until(self, data, deadline):
        self._ensure_open()
        previous = getattr(self.channel, "write_timeout", None)
        try:
            offset = 0
            while offset < len(data):
                self.channel.write_timeout = self._remaining(deadline)
                count = self.channel.write(data[offset:])
                if type(count) is not int or not 0 < count <= len(data) - offset:
                    raise ConsoleError("主控台命令未完整寫入；未自動重送")
                offset += count
                self._remaining(deadline)
        except OSError as exc:
            if isinstance(exc, ConsoleTimeout):
                raise
            self._remaining(deadline)
            raise ConsoleError("主控台傳送失敗，未自動重送") from exc
        finally:
            self.channel.write_timeout = previous
        return len(data)

    def send(self, data: bytes | str, *, timeout=10.0, secret=False) -> int:
        """只傳送明確提供的內容；secret 可標示密碼，所有 TX 一律不記錄。"""
        return self._send_until(_bytes(data), self._deadline(timeout, None))

    def write(self, data: bytes) -> int:
        """供同一連線交接使用的有界寫入。"""
        return self.send(data)

    def _expect(self, search, deadline):
        self._ensure_open()
        while True:
            self._remaining(deadline)
            found = search(bytes(self._buffer))
            if found is not None:
                start, end, groups = found
                self._remaining(deadline)
                result = ExpectResult(bytes(self._buffer[:start]),
                                      bytes(self._buffer[start:end]), groups)
                del self._buffer[:end]
                return result
            self._read_until(deadline)

    def expect_literal(self, literal: bytes | str, timeout=None, *, deadline=None):
        literal = _bytes(literal)
        if not literal:
            raise ConsoleError("literal 不得為空")

        def search(data):
            start = data.find(literal)
            return (start, start + len(literal), ()) if start >= 0 else None

        return self._expect(search, self._deadline(timeout, deadline))

    def expect_regex(self, pattern, timeout=None, *, deadline=None):
        """接受 bytes／文字模式或已編譯的 bytes 正規表示式。"""
        try:
            pattern = re.compile(_bytes(pattern) if isinstance(pattern, (str, bytes)) else pattern)
        except (TypeError, re.error) as exc:
            raise ConsoleError("正規表示式無效") from exc
        if not isinstance(pattern.pattern, bytes) or pattern.match(b"") is not None:
            raise ConsoleError("正規表示式須使用 bytes 且不得匹配空字串")

        def search(data):
            match = pattern.search(data)
            if match is None:
                return None
            if match.start() == match.end():
                raise ConsoleError("正規表示式不得產生零長度匹配")
            return match.start(), match.end(), match.groups()

        return self._expect(search, self._deadline(timeout, deadline))

    def run_shell(self, command: str, timeout=10.0) -> ShellResult:
        """在已登入的 POSIX shell 執行一行明確命令，回傳原始輸出與退出碼。

        命令在 sh 子行程執行且標準輸入關閉；不保留 cd 等 shell 狀態。
        逾時只停止主機等待，遠端命令可能仍在執行，呼叫者須自行判定後續。
        """
        if (not isinstance(command, str) or not command.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in command)):
            raise ConsoleError("命令須為非空單行文字，不得含控制字元")
        deadline = self._deadline(timeout, None)
        nonce = secrets.token_hex(32)
        begin = f"__BPI_CONSOLE_BEGIN_{nonce}__"
        end = f"__BPI_CONSOLE_END_{nonce}__"
        # 將標記拆為 printf 引數；即使 tty 換行回顯命令，也沒有完整標記行。
        wire = (f"printf '\\n%s%s\\n' '__BPI_CONSOLE_BEGIN_' '{nonce}__'; "
                f"sh -c {shlex.quote(command)} </dev/null; "
                f"printf '\\n%s%s:%d\\n' '__BPI_CONSOLE_END_' '{nonce}__' \"$?\"\n").encode()
        if len(wire) > 4095:
            raise ConsoleError("命令超過終端單行長度上限")
        self._send_until(wire, deadline)
        self.expect_regex(rb"(?m)^" + re.escape(begin.encode()) + rb"\r?\n", deadline=deadline)
        result = self.expect_regex(
            rb"\r?\n" + re.escape(end.encode())
            + rb":(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\r?\n",
            deadline=deadline,
        )
        return ShellResult(result.before, int(result.groups[0]))

    def capture(self, seconds=10.0) -> int:
        """在固定時間內只收 RX；資料保留於日誌及 buffer，不送出任何內容。"""
        deadline = self._deadline(seconds, None)
        count = 0
        while (remaining := deadline - self.monotonic()) > 0:
            previous = self.timeout
            try:
                self.channel.timeout = min(0.1, remaining)
                count += len(self._receive(4096))
            finally:
                self.channel.timeout = previous
        return count


INVENTORY_COMMAND = "uname -a; id; cat /proc/cmdline; cat /proc/mounts; cat /proc/partitions"


def main(argv=None) -> int:
    parser = uart.package.ChineseArgumentParser(
        description="明確指定串口的 RX 擷取與已登入 shell 操作；不探帳密、不重設 root。",
    )
    parser.add_argument("action", choices=("capture", "probe-shell", "inventory", "command"),
                        help="擷取、shell 探測、固定唯讀盤點或明確命令")
    parser.add_argument("--port", required=True, metavar="串口", help="明確指定串口，不自動探索")
    parser.add_argument("--log", required=True, type=Path, metavar="新檔", help="私有原始 RX 日誌，禁止覆寫")
    parser.add_argument("--timeout", type=float, default=10.0, metavar="秒", help="操作總期限；預設 10 秒")
    parser.add_argument("--command", metavar="命令", help="僅 command 模式接受；須已登入")
    args = parser.parse_args(argv)
    if (args.action == "command") != (args.command is not None):
        parser.error("明確命令只供 command 模式使用")
    try:
        timeout = _seconds(args.timeout)
        with ExitStack() as stack:
            # 先拒絕不安全的日誌路徑，再開啟已關閉 DTR/RTS 的排他串口。
            session = stack.enter_context(ConsoleSession(None, log_path=args.log))
            session.channel = stack.enter_context(uart.open_serial(args.port, 115200, timeout))
            if args.action == "capture":
                print(f"已擷取 {session.capture(timeout)} 位元組；未判定啟動成功。")
                return 0
            command = {"probe-shell": ":", "inventory": INVENTORY_COMMAND}.get(args.action, args.command)
            result = session.run_shell(command, timeout)
            # 只報告退出碼；原始內容留在私有日誌，不將 RX 控制序列送到主機終端。
            print(f"命令退出碼：{result.exitcode}；輸出位元組數：{len(result.output)}")
            return result.exitcode
    except (ConsoleError, uart.UartError, OSError) as exc:
        message = str(exc) if isinstance(exc, (ConsoleError, uart.UartError)) else "串口或日誌存取失敗"
        print(f"錯誤：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
