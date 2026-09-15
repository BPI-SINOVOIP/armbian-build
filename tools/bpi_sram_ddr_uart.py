#!/usr/bin/env python3
"""僅上載第二版 DDR 封包；明確交接後只驗證就緒，不操作 SD、電源或 DDR 參數。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Iterator, Sequence

import bpi_sram_ddr_package as package
import bpi_sram_package as base
import bpi_sram_uart as uart


class DdrSupervisorSession(uart.SupervisorSession):
    """只信任完整本地封包及同次韌體驗證，不提供舊 ABI 或 smoke 降格路徑。"""

    CONTROL_ABI = 2
    CAPABILITIES = "uart-ram,sd-read,smoke-run,ddr-run"

    def __init__(self, channel: object, timeout: float, blob: bytes, clock=None) -> None:
        self.metadata = package.parse_package(blob)
        self._expected_digest = self.metadata["hash"]
        self._package_bytes = self.metadata["raw_size"]
        super().__init__(channel, timeout, clock)
        self._invalidate_load()

    def _invalidate_load(self) -> None:
        self.loaded_nonce = None
        self.loading = False
        self._loaded_kind: int | None = None
        self._loaded_digest: str | None = None

    def _send(self, command: str, value: int | None = None) -> None:
        if command not in ("I", "U", "R"):
            self._invalidate_load()
            raise uart.UartError("DDR UART 工具不提供 SD 或其他命令")
        super()._send(command, value)

    def probe(self) -> dict[str, object]:
        self._invalidate_load()
        return super().probe()

    def begin_load(self, transport: str, value: int) -> None:
        self._invalidate_load()
        if transport != "U" or type(value) is not int or value != self._package_bytes:
            raise uart.UartError("只允許以 UART 載入已驗證本地 DDR 封包的完整長度")
        super().begin_load(transport, value)

    def finish_load(self) -> None:
        loading = self.loading
        self._invalidate_load()
        if not self.probed or not loading:
            raise uart.UartError("尚未完成本次 DDR 交握與載入要求")
        fields = self._wait("loaded")
        if fields.get("result") != "0":
            raise uart.UartError("韌體未成功驗證 DDR 封包，禁止交接")
        if fields.get("kind") != str(package.KIND_DDR):
            raise uart.UartError("韌體載入種類不是 DDR，禁止交接")
        if fields.get("sha256") != self._expected_digest:
            raise uart.UartError("韌體負載摘要與本地完整 DDR 封包不符，禁止交接")
        self._loaded_kind = package.KIND_DDR
        self._loaded_digest = self._expected_digest
        self.loaded_nonce = self.nonce

    def run_smoke(self) -> dict[str, object]:
        self._invalidate_load()
        raise uart.UartError("DDR UART 工具禁止以 smoke 契約執行")

    def run_ddr(self) -> dict[str, object]:
        permitted = (self.probed and not self.loading and self.loaded_nonce == self.nonce
                     and self._loaded_kind == package.KIND_DDR
                     and self._loaded_digest == self._expected_digest)
        self._invalidate_load()
        if not permitted:
            raise uart.UartError("沒有同一 nonce、種類與摘要均相符的 DDR 載入，禁止交接")
        # 交接結果未知也不能重送 R；下一輪必須重新交握並完整載入。
        self.probed = False
        self._send("R")
        if self._wait("handoff").get("entry") != "00030000":
            raise uart.UartError("DDR 交接入口不符")
        fields = self._wait("ddr-ready", "BPI-SPL2")
        if fields.get("abi") != "2":
            raise uart.UartError("DDR 就緒回應的 ABI 不符")
        if fields.get("kind") != "2" or fields.get("preflight") != "unverified":
            raise uart.UartError("DDR 就緒種類或前置檢查狀態不符合本階段契約")
        return {"event": "ddr-ready", "nonce_hex": f"{self.nonce:08x}", "abi": 2,
                "kind": 2, "preflight": "unverified"}


@contextmanager
def staged_package(path: Path) -> Iterator[tuple[Path, bytes]]:
    """開串口前驗證完整封包；sx 只讀取私有快照，不重新解析來源路徑。"""
    blob = base.read_regular_file(path, base.MAX_PACKAGE_BYTES)
    package.parse_package(blob)
    with tempfile.TemporaryDirectory(prefix="bpi-sram-ddr-uart-") as directory:
        staged = Path(directory) / "ddr.spl2"
        base.write_new_regular_file(staged, blob)
        os.chmod(staged, 0o400)
        yield staged, blob


def build_parser() -> argparse.ArgumentParser:
    parser = base.ChineseArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    upload = commands.add_parser("upload", help="完整驗證並上載第二版 DDR 封包")
    upload.add_argument("--port", type=uart._named_port, required=True,
                        metavar="串口", help="已核對的本機串口絕對路徑，固定 115200、8N1")
    upload.add_argument("--input", type=Path, required=True, metavar="封包",
                        help="第二版 DDR 封包的一般檔案")
    upload.add_argument("--timeout", type=uart._timeout, default=15.0, metavar="秒",
                        help="每個控制事件的等待上限")
    upload.add_argument("--transfer-timeout", type=uart._timeout, default=120.0,
                        metavar="秒", help="sx 傳輸等待上限")
    upload.add_argument("--run", action="store_true",
                        help="種類與摘要均相符才交接並等待 DDR 就緒，不代表 DDR 測試通過")
    return parser


def execute(args: argparse.Namespace) -> dict[str, object]:
    if args.command != "upload":
        raise uart.UartError("DDR UART 工具只提供明確上載")
    with ExitStack() as stack:
        staged, blob = stack.enter_context(staged_package(args.input))
        executable = shutil.which("sx")
        if executable is None:
            raise uart.UartError("找不到 lrzsz 的 sx，尚未開啟串口")
        channel = stack.enter_context(uart.open_serial(args.port, 115200, args.timeout))
        session = DdrSupervisorSession(channel, args.timeout, blob)
        result = session.probe()
        session.begin_load("U", session.metadata["raw_size"])
        stderr_bytes = uart.send_xmodem(channel, staged, executable, args.transfer_timeout)
        session.finish_load()
        result.update({"command": "upload", "event": "loaded", "result": 0,
                       "kind": package.KIND_DDR, "package": session.metadata,
                       "stderr_bytes": stderr_bytes, "ran": False,
                       "ddr_result_verified": False})
        if args.run:
            result["ddr_ready"] = session.run_ddr()
            result.update({"event": "ddr-ready", "ran": True})
        return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (uart.UartError, base.PackageError) as exc:
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
