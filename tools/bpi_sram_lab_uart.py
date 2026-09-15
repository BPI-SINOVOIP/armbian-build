#!/usr/bin/env python3
"""ABI3 實驗主機工具；完整本機封包守門，明確同意才更新，不控制電源。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import tempfile
import time
from typing import Iterator, Sequence

if __package__:
    from . import bpi_sram_uart as base
    from . import bpi_sram_package as package
    from . import bpi_sram_ddr_package as ddr_package
    from . import bpi_sram_lab_package as lab_package
else:
    import bpi_sram_uart as base
    import bpi_sram_package as package
    import bpi_sram_ddr_package as ddr_package
    import bpi_sram_lab_package as lab_package


CONTROL_ABI = 3
CAPABILITIES = "uart-ram,sd-read,smoke-run,ddr-run,update-run,boot-run"
TRANSFER_TIMEOUT = 120.0


def capture_boot(channel, fd: int, seconds: int, clock=time.monotonic) -> dict[str, object]:
    """保留交接後原始位元組；不解析為命令，也不將輸出字串當成功證據。"""
    if type(seconds) is not int or not 1 <= seconds <= 300:
        raise base.UartError("開機紀錄時間必須介於 1 與 300 秒")
    end = clock() + seconds
    total, checksum = 0, hashlib.sha256()
    while (remaining := end - clock()) > 0:
        channel.timeout = min(0.1, remaining)
        data = channel.read(4096)
        if not data:
            continue
        if len(data) > 4096 or total + len(data) > 4 * 1024 * 1024:
            raise base.UartError("開機輸出超出紀錄上限，保留已收資料但不宣告啟動成功")
        position = 0
        while position < len(data):
            count = os.write(fd, data[position:])
            if not 0 < count <= len(data) - position:
                raise base.UartError("串口紀錄未完整寫入")
            position += count
        checksum.update(data)
        total += len(data)
    os.fsync(fd)
    return {"bytes": total, "sha256": checksum.hexdigest(), "seconds": seconds,
            "boot_verified": False}


@contextmanager
def new_capture(path: Path):
    with package._parent_directory(path.absolute()) as (parent, name):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            os.fsync(parent)
            yield fd
        finally:
            os.fsync(fd)
            os.close(fd)


def parse_package(blob: bytes) -> dict[str, int | str]:
    """依版本完整解析，不降格重試；將舊版文字種類正規化為整數。"""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < package.HEADER_BYTES:
        raise package.PackageError("必須提供完整本機封包，不能只提供中繼資料或摘要")
    version = struct.unpack_from("<I", blob, 8)[0]
    parsers = {1: package.parse_package, 2: ddr_package.parse_package,
               3: lab_package.parse_package}
    if version not in parsers:
        raise package.PackageError("只接受第一版 smoke、第二版 DDR 或第三版實驗封包")
    metadata = parsers[version](blob)
    metadata["kind"] = version if version < 3 else metadata["kind"]
    metadata["version"] = version
    return metadata


@contextmanager
def staged_package(path: Path) -> Iterator[tuple[Path, bytes]]:
    """開串口前固定完整一般檔案；傳輸只讀私有快照，不重讀來源。"""
    blob = package.read_regular_file(path, package.MAX_PACKAGE_BYTES)
    parse_package(blob)
    with tempfile.TemporaryDirectory(prefix="bpi-sram-lab-uart-") as directory:
        staged = Path(directory) / "package.spl2"
        package.write_new_regular_file(staged, blob)
        os.chmod(staged, 0o400)
        yield staged, blob


def validate_update(blob: bytes, slot: int, cid: str, sectors: int,
                    partition_sectors: int, confirm_write: bool) -> dict[str, int | str]:
    """在任何寫入命令前核對明確同意、候選種類及預期媒體範圍。"""
    if confirm_write is not True:
        raise base.UartError("更新必須明確指定 --confirm-write，未傳送寫入命令")
    if type(slot) is not int or slot not in (2, 3, 4):
        raise base.UartError("更新只允許固定槽位 2、3、4")
    metadata = parse_package(blob)
    if metadata["kind"] != (3 if slot == 2 else 4):
        raise base.UartError("槽位 2 必須使用更新封包；槽位 3、4 必須使用開機封包")
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{32}", cid):
        raise base.UartError("CID 必須是 32 位小寫十六進位字串")
    if type(sectors) is not int or not 131072 <= sectors <= 0xFFFFFFFF:
        raise base.UartError("媒體磁區數必須介於 131072 與 4294967295")
    if (type(partition_sectors) is not int
            or not 4096 <= partition_sectors <= sectors - 8192):
        raise base.UartError("分割區磁區數必須至少 4096 且不得超出媒體")
    return metadata


class LabSupervisorSession(base.SupervisorSession):
    """ABI3 嚴格控制狀態；交接嘗試後永久停用 SPL1 命令。"""

    CONTROL_ABI = CONTROL_ABI
    CAPABILITIES = CAPABILITIES

    def __init__(self, channel: object, timeout: float,
                 blob: bytes | None = None, clock=None) -> None:
        self.metadata = parse_package(blob) if blob is not None else None
        self._expected_digest = self.metadata["hash"] if self.metadata else None
        self._expected_kind = self.metadata["kind"] if self.metadata else None
        self._package_bytes = len(blob) if blob is not None else None
        self._run_attempted = False
        self._update_ready = False
        self._update_attempted = False
        super().__init__(channel, timeout, clock)
        self._invalidate_load()

    def _invalidate_load(self) -> None:
        self.loaded_nonce = None
        self.loading = False
        self._loaded_kind = None
        self._loaded_digest = None
        self._transport = None

    def _require_control(self) -> None:
        if self._run_attempted:
            raise base.UartError("已嘗試單次交接，禁止重用本次 SPL1 控制介面")

    def probe(self) -> dict[str, object]:
        self._require_control()
        self._invalidate_load()
        return super().probe()

    def begin_load(self, transport: str, value: int) -> None:
        self._require_control()
        self._invalidate_load()
        if self.metadata is None:
            raise base.UartError("所有載入都必須先提供完整本機 --input 封包")
        if transport == "U" and (type(value) is not int or value != self._package_bytes):
            raise base.UartError("UART 只能上載本機完整封包長度")
        super().begin_load(transport, value)
        self._transport = transport

    def finish_load(self) -> None:
        self._require_control()
        loading, transport = self.loading, self._transport
        self._invalidate_load()
        if not self.probed or not loading or self.metadata is None:
            raise base.UartError("尚未完成本次交握與載入要求")
        fields = self._wait("loaded")
        if (fields.get("result") != "0" or fields.get("kind") != str(self._expected_kind)
                or fields.get("sha256") != self._expected_digest):
            raise base.UartError("載入結果、種類或本機負載摘要不符，禁止交接")
        self._loaded_kind = self._expected_kind
        self._loaded_digest = self._expected_digest
        self.loaded_nonce = self.nonce
        self._transport = transport

    def run_smoke(self) -> dict[str, object]:
        if self._expected_kind != 1:
            self._invalidate_load()
            raise base.UartError("非 smoke 封包不得使用 smoke 執行契約")
        return self.run()

    def run(self) -> dict[str, object]:
        self._require_control()
        permitted = (self.probed and not self.loading and self.metadata is not None
                     and self.loaded_nonce == self.nonce
                     and self._loaded_kind == self._expected_kind
                     and self._loaded_digest == self._expected_digest)
        if not permitted:
            self._invalidate_load()
            raise base.UartError("沒有同次 nonce、種類與負載摘要均相符的載入，禁止交接")
        self._run_attempted = True
        self.probed = False
        transport = self._transport
        try:
            if self._expected_kind == 1:
                return {"event": "smoke", "kind": 1, **super().run_smoke()}
            self._send("R")
            if self._wait("handoff").get("entry") != "00030000":
                raise base.UartError("交接入口不符")
            result = {"nonce_hex": f"{self.nonce:08x}", "kind": self._expected_kind}
            if self._expected_kind == 4:
                return {**result, "event": "handoff", "entry": "00030000",
                        "boot_verified": False}
            if self._expected_kind == 2:
                fields = self._wait("ddr-ready", "BPI-SPL2")
                if (fields.get("abi") != "2" or fields.get("kind") != "2"
                        or fields.get("preflight") != "unverified"):
                    raise base.UartError("DDR 就緒回應不符合第二版負載契約")
                return {**result, "event": "ddr-ready", "abi": 2,
                        "preflight": "unverified", "ddr_result_verified": False}
            fields = self._wait("update-ready", "BPI-SPL2")
            if (fields.get("abi") != "3" or fields.get("kind") != "3"
                    or fields.get("ddr") != "off" or fields.get("write_slots") != "2,3,4"):
                raise base.UartError("更新器就緒回應不符合 ABI3、DDR 關閉及固定可寫槽位契約")
            self._update_ready = transport == "U"
            self._update_nonce = self.nonce
            return {**result, "event": "update-ready", "abi": 3,
                    "ddr": "off", "write_slots": [2, 3, 4]}
        finally:
            self._invalidate_load()

    def _update_send(self, command: str) -> None:
        data = (command + "\n").encode("ascii")
        if self.channel.write(data) != len(data):
            raise base.UartError("更新控制命令未完整寫入，禁止繼續")

    def _media(self, cid: str, sectors: int, partition_sectors: int) -> dict[str, object]:
        self._update_send(f"I {self.nonce}")
        fields = self._wait("media", "BPI-SPL2")
        expected = {"cid": cid, "sectors": str(sectors), "partition_start": "8192",
                    "partition_sectors": str(partition_sectors)}
        if any(fields.get(key) != value for key, value in expected.items()):
            raise base.UartError("媒體 CID、總磁區或分割區資料不符，禁止繼續")
        return {"cid": cid, "sectors": sectors, "partition_start": 8192,
                "partition_sectors": partition_sectors}

    def update_slot(self, staged: Path, blob: bytes, executable: str, *, slot: int,
                    cid: str, sectors: int, partition_sectors: int,
                    confirm_write: bool, transfer_timeout: float = TRANSFER_TIMEOUT
                    ) -> dict[str, object]:
        metadata = validate_update(blob, slot, cid, sectors, partition_sectors, confirm_write)
        if (not self._update_ready or self._update_attempted
                or self.nonce != self._update_nonce):
            raise base.UartError("必須先以本次 UART 上載更新器並核對就緒；禁止重試更新")
        transfer_timeout = base._timeout(str(transfer_timeout))
        snapshot = package.read_regular_file(staged, package.MAX_PACKAGE_BYTES)
        if (snapshot != blob or stat.S_IMODE(staged.stat().st_mode) != 0o400
                or stat.S_IMODE(staged.parent.stat().st_mode) != 0o700
                or staged.stat().st_uid != os.getuid()
                or staged.parent.stat().st_uid != os.getuid()):
            raise base.UartError("更新傳輸必須使用內容相符的私有唯讀完整封包快照")
        self._update_attempted = True
        self._update_ready = False
        digest = hashlib.sha256(blob).hexdigest()
        size = metadata["raw_size"]
        self._media(cid, sectors, partition_sectors)
        self._update_send(f"W {self.nonce} {slot} {size} {digest}")
        fields = self._wait("update-loading", "BPI-SPL2")
        if fields.get("slot") != str(slot) or fields.get("bytes") != str(size):
            raise base.UartError("更新載入回應的槽位或完整封包長度不符，未開始傳輸")
        # 不預讀 loading 後的 C；韌體 progress 守門為閒置 10 秒、總計 120 秒。
        stderr_bytes = base.send_xmodem(self.channel, staged, executable, transfer_timeout)
        fields = self._wait("update-result", "BPI-SPL2")
        if (fields.get("slot") != str(slot) or fields.get("result") != "0"
                or fields.get("committed") != "1" or fields.get("sha256") != digest):
            raise base.UartError("更新未取得同槽位、成功提交與整包摘要證據，不能宣告成功")
        media = self._media(cid, sectors, partition_sectors)
        self._update_send(f"H {self.nonce} {slot}")
        fields = self._wait("slot", "BPI-SPL2")
        if (fields.get("slot") != str(slot) or fields.get("result") != "0"
                or fields.get("bytes") != str(size) or fields.get("sha256") != digest):
            raise base.UartError("更新後槽位回讀的結果、長度或整包摘要不符，不能宣告成功")
        return {"event": "slot", "nonce_hex": f"{self.nonce:08x}", "slot": slot,
                "result": 0, "committed": 1, "bytes": size, "sha256": digest,
                "package_sha256": digest, "kind": metadata["kind"], "package": metadata,
                "media": media, "slot_verified": True, "stderr_bytes": stderr_bytes,
                "ran": False, "boot_verified": False, "powercycled": False}


def _cid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise argparse.ArgumentTypeError("CID 必須是完整 32 位十六進位字串")
    return value.lower()


def build_parser() -> argparse.ArgumentParser:
    parser = package.ChineseArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    for name, description in (
        ("probe", "只核對 ABI3 身分與完整能力清單"),
        ("upload", "將完整本機封包上載至 SRAM"),
        ("load-slot", "唯讀載入固定槽位並比對完整本機封包"),
        ("update-slot", "上載更新器後明確寫入固定槽位，回讀確認，不執行候選"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("--port", type=base._named_port, required=True,
                             metavar="串口", help="本機串口絕對路徑，固定 115200、8N1")
        command.add_argument("--timeout", type=base._timeout, default=15.0,
                             metavar="秒", help="每個控制事件的等待上限")
        if name != "probe":
            command.add_argument("--input", type=Path, required=True, metavar="封包",
                                 help="完整本機候選封包的一般檔案")
        if name in ("upload", "load-slot"):
            command.add_argument("--run", action="store_true",
                                 help="載入驗證後單次交接；開機負載只確認交接，不證明作業系統成功")
            command.add_argument("--capture-seconds", type=int, default=0, metavar="秒",
                                 help="開機交接後持續紀錄 1 至 300 秒，只適用 kind=4 且指定 --run")
            command.add_argument("--capture-log", type=Path, metavar="新檔案",
                                 help="開機原始串口紀錄，不覆寫既有檔案")
        if name in ("upload", "update-slot"):
            command.add_argument("--transfer-timeout", type=base._timeout,
                                 default=TRANSFER_TIMEOUT, metavar="秒",
                                 help="每次 sx 傳輸總上限，最多 120 秒")
        if name in ("load-slot", "update-slot"):
            command.add_argument("--slot", type=int, required=True,
                                 choices=range(5) if name == "load-slot" else (2, 3, 4),
                                 help="固定槽位編號")
        if name == "update-slot":
            command.add_argument("--updater", type=Path, required=True, metavar="封包",
                                 help="完整第三版 kind=3 更新器封包的一般檔案")
            command.add_argument("--cid", type=_cid, required=True, metavar="識別碼",
                                 help="事先核對的完整 SD CID")
            command.add_argument("--sectors", type=int, required=True, metavar="磁區數",
                                 help="事先核對的媒體總磁區數")
            command.add_argument("--partition-sectors", type=int, required=True,
                                 metavar="磁區數", help="起始於 8192 的分割區磁區數")
            command.add_argument("--confirm-write", action="store_true",
                                 help="明確同意更新指定槽位；缺少此選項即拒絕操作")
    return parser


def execute(args: argparse.Namespace) -> dict[str, object]:
    if args.command not in ("probe", "upload", "load-slot", "update-slot"):
        raise base.UartError("不支援的 ABI3 子命令")
    updating = args.command == "update-slot"
    if updating and args.confirm_write is not True:
        raise base.UartError("必須明確指定 --confirm-write，尚未開啟串口")
    with ExitStack() as stack:
        staged = blob = executable = None
        if args.command != "probe":
            staged, blob = stack.enter_context(staged_package(args.input))
        if updating:
            validate_update(blob, args.slot, args.cid, args.sectors,
                            args.partition_sectors, args.confirm_write)
            updater_staged, updater_blob = stack.enter_context(staged_package(args.updater))
            if parse_package(updater_blob)["kind"] != 3:
                raise base.UartError("--updater 必須是完整第三版 kind=3 更新器封包")
        if args.command == "upload" or updating:
            executable = shutil.which("sx")
            if executable is None:
                raise base.UartError("找不到 lrzsz 的 sx，尚未開啟串口")
        seconds, capture_path = getattr(args, "capture_seconds", 0), getattr(args, "capture_log", None)
        capture_fd = None
        if seconds or capture_path is not None:
            if (not getattr(args, "run", False) or blob is None or parse_package(blob)["kind"] != 4
                    or type(seconds) is not int or not 1 <= seconds <= 300 or capture_path is None):
                raise base.UartError("開機紀錄須同時指定 kind=4、--run、新紀錄檔及 1 至 300 秒")
            capture_fd = stack.enter_context(new_capture(capture_path))
        channel = stack.enter_context(base.open_serial(args.port, 115200, args.timeout))
        session = LabSupervisorSession(channel, args.timeout, updater_blob if updating else blob)
        result = session.probe()
        result.update({"command": args.command, "event": "info", "ran": False,
                       "boot_verified": False, "ddr_result_verified": False})
        if args.command == "probe":
            return result
        if args.command == "load-slot":
            session.begin_load("S", args.slot)
            result["slot"] = args.slot
        else:
            session.begin_load("U", session.metadata["raw_size"])
            result["stderr_bytes"] = base.send_xmodem(
                channel, updater_staged if updating else staged, executable, args.transfer_timeout)
        session.finish_load()
        if updating:
            result["updater_ready"] = session.run()
            result["updater_stderr_bytes"] = result.pop("stderr_bytes")
            result.update(session.update_slot(
                staged, blob, executable, slot=args.slot, cid=args.cid, sectors=args.sectors,
                partition_sectors=args.partition_sectors, confirm_write=args.confirm_write,
                transfer_timeout=args.transfer_timeout))
            return result
        result.update({"event": "loaded", "result": 0, "kind": session.metadata["kind"],
                       "package": session.metadata, "sha256": session.metadata["hash"],
                       "package_sha256": hashlib.sha256(blob).hexdigest()})
        if args.run:
            evidence = session.run()
            result.update({"event": evidence["event"], "ran": True, "handoff": evidence})
            if capture_fd is not None:
                result["capture"] = {"path": str(capture_path.absolute()),
                                     **capture_boot(channel, capture_fd, seconds)}
        return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (base.UartError, package.PackageError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except (OSError, base.termios.error, base.subprocess.SubprocessError):
        print("錯誤：主機檔案、串口或傳輸程序操作失敗；未輸出原始通訊內容", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("錯誤：操作已取消，未安排重試、重新上電或執行候選", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
