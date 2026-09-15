#!/usr/bin/env python3
"""嚴格驗證 ABI2 SPL1 與固定 smoke；不控制電源、不寫入媒體、不執行 DDR。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Iterator, Sequence

if __package__:
    from . import bpi_sram_package as package
    from . import bpi_sram_uart as uart
else:
    import bpi_sram_package as package
    import bpi_sram_uart as uart


PACKAGE_BYTES = 1536
PACKAGE_SHA256 = "8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062"


def validate_smoke(blob: bytes) -> dict[str, int | str]:
    """只接受 build-009 完整封包；檔案摘要與負載摘要不可互換。"""
    if (not isinstance(blob, (bytes, bytearray)) or len(blob) != PACKAGE_BYTES
            or hashlib.sha256(blob).hexdigest() != PACKAGE_SHA256):
        raise uart.UartError("只接受已驗證的 build-009 固定 smoke 封包")
    return package.parse_package(blob)


class V2SupervisorSession(uart.SupervisorSession):
    """ABI2 控制介面只執行已驗證 smoke；交接後本次 session 永久失效。"""

    CONTROL_ABI = 2
    CAPABILITIES = "uart-ram,sd-read,smoke-run,ddr-run"

    def __init__(self, channel: object, timeout: float,
                 blob: bytes | None = None, clock=None) -> None:
        self.metadata = validate_smoke(blob) if blob is not None else None
        self._expected_digest = self.metadata["hash"] if self.metadata else None
        self._run_attempted = False
        super().__init__(channel, timeout, clock)
        self._invalidate_load()

    def _invalidate_load(self) -> None:
        self.loaded_nonce = None
        self.loading = False
        self._loaded_kind: int | None = None
        self._loaded_digest: str | None = None

    def _require_control(self) -> None:
        if self._run_attempted:
            raise uart.UartError("已嘗試單次 smoke 交接，禁止重用本次 session")

    def probe(self) -> dict[str, object]:
        self._require_control()
        self._invalidate_load()
        return super().probe()

    def begin_load(self, transport: str, value: int) -> None:
        self._require_control()
        self._invalidate_load()
        if self.metadata is None:
            raise uart.UartError("載入前必須提供本機已驗證的固定 smoke 封包")
        if transport == "U" and (type(value) is not int or value != PACKAGE_BYTES):
            raise uart.UartError("UART 只允許載入固定 smoke 封包的完整長度")
        super().begin_load(transport, value)

    def finish_load(self) -> None:
        self._require_control()
        loading = self.loading
        self._invalidate_load()
        if not self.probed or not loading or self.metadata is None:
            raise uart.UartError("尚未完成本次 smoke 交握與載入要求")
        fields = self._wait("loaded")
        if fields.get("result") != "0":
            raise uart.UartError("韌體未成功驗證 smoke 封包，禁止交接")
        if fields.get("kind") != str(package.KIND_SMOKE):
            raise uart.UartError("韌體載入種類不是 smoke，禁止交接")
        if fields.get("sha256") != self._expected_digest:
            raise uart.UartError("韌體負載摘要與本機固定 smoke 封包不符，禁止交接")
        self._loaded_kind = package.KIND_SMOKE
        self._loaded_digest = self._expected_digest
        self.loaded_nonce = self.nonce

    def run_smoke(self) -> dict[str, object]:
        self._require_control()
        permitted = (self.probed and not self.loading and self.metadata is not None
                     and self.loaded_nonce == self.nonce
                     and self._loaded_kind == package.KIND_SMOKE
                     and self._loaded_digest == self._expected_digest)
        if not permitted:
            self._invalidate_load()
            raise uart.UartError("沒有同次 nonce、種類及摘要均相符的 smoke 載入，禁止交接")
        # smoke 不會返回 SPL1；即使寫入或等待失敗，也不可再次探測或重送命令。
        self._run_attempted = True
        self.probed = False
        try:
            return super().run_smoke()
        finally:
            self._invalidate_load()


@contextmanager
def staged_package(path: Path) -> Iterator[tuple[Path, bytes]]:
    """串口開啟前固定完整本機封包；傳輸只讀私有快照，不重讀來源路徑。"""
    blob = package.read_regular_file(path, PACKAGE_BYTES)
    validate_smoke(blob)
    with tempfile.TemporaryDirectory(prefix="bpi-sram-v2-uart-") as directory:
        staged = Path(directory) / "smoke.spl2"
        package.write_new_regular_file(staged, blob)
        os.chmod(staged, 0o400)
        yield staged, blob


def build_parser() -> argparse.ArgumentParser:
    parser = package.ChineseArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    for name, description in (
        ("probe", "以新 nonce 核對 ABI2 SPL1 身分，不載入或執行"),
        ("upload", "以 sx 上載已驗證的 build-009 固定 smoke 封包"),
        ("load-slot", "要求 SPL1 唯讀載入 SD 槽位，並比對本機固定 smoke 負載摘要"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("--port", type=uart._named_port, required=True,
                             metavar="串口", help="本機串口絕對路徑，固定 115200、8N1")
        command.add_argument("--timeout", type=uart._timeout, default=15.0,
                             metavar="秒", help="每個控制事件的等待上限")
        if name != "probe":
            command.add_argument("--input", type=Path, required=True, metavar="封包",
                                 help="本機 build-009 固定 smoke 封包的一般檔案")
            command.add_argument("--run", action="store_true",
                                 help="載入驗證成功才執行單次 smoke；不會返回 SPL1")
        if name == "upload":
            command.add_argument("--transfer-timeout", type=uart._timeout, default=120.0,
                                 metavar="秒", help="sx 傳輸等待上限")
        if name == "load-slot":
            command.add_argument("--slot", type=int, choices=range(5), required=True,
                                 help="唯讀槽位編號，負載必須符合本機固定 smoke 封包")
    return parser


def execute(args: argparse.Namespace) -> dict[str, object]:
    if args.command not in ("probe", "upload", "load-slot"):
        raise uart.UartError("不支援的 ABI2 smoke 子命令")
    with ExitStack() as stack:
        staged = blob = executable = None
        if args.command != "probe":
            staged, blob = stack.enter_context(staged_package(args.input))
        if args.command == "upload":
            executable = shutil.which("sx")
            if executable is None:
                raise uart.UartError("找不到 lrzsz 的 sx，尚未開啟串口")
        channel = stack.enter_context(uart.open_serial(args.port, 115200, args.timeout))
        session = V2SupervisorSession(channel, args.timeout, blob)
        result = session.probe()
        result.update({"command": args.command, "event": "info", "ran": False})
        if args.command == "probe":
            return result
        if args.command == "upload":
            session.begin_load("U", PACKAGE_BYTES)
            result["stderr_bytes"] = uart.send_xmodem(channel, staged, executable,
                                                      args.transfer_timeout)
        else:
            session.begin_load("S", args.slot)
            result["slot"] = args.slot
        session.finish_load()
        result.update({"event": "loaded", "result": 0, "kind": package.KIND_SMOKE,
                       "package": session.metadata, "package_sha256": PACKAGE_SHA256,
                       "sha256": session.metadata["hash"]})
        if args.run:
            result["smoke"] = session.run_smoke()
            result.update({"event": "smoke", "ran": True})
        return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (uart.UartError, package.PackageError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except (OSError, uart.termios.error, uart.subprocess.SubprocessError):
        print("錯誤：主機檔案、串口或傳輸程序操作失敗；未輸出原始通訊內容", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("錯誤：操作已取消，未安排重試、重新上電或降格執行", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
