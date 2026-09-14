#!/usr/bin/env python3
"""離線產生及驗證 SPL1 第一版 ABI 的 SPL2 封包，不是 eGON 映像。

檔案操作使用 Linux 的 O_PATH 與 /proc/self/fd，避免輸入替換競態觸及裝置。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
from typing import Iterator, Sequence
import zlib


MAGIC = b"BPISRAM1"
VERSION = 1
HEADER_BYTES = 512
BOARD_ID = 0x06180001
ENTRY = 0x30000
MAX_RUNTIME_BYTES = 0x18000
MAX_IMAGE_BYTES = MAX_RUNTIME_BYTES - HEADER_BYTES
MAX_PACKAGE_BYTES = HEADER_BYTES + (MAX_IMAGE_BYTES // 512 + 1) * 512
KIND_SMOKE = 1


class PackageError(ValueError):
    """封包或檔案不符合離線工具的安全限制。"""


def _validate_sizes(image_size: int, runtime_size: int) -> None:
    if not 1 <= image_size <= MAX_IMAGE_BYTES:
        raise PackageError(f"映像大小必須介於 1 與 {MAX_IMAGE_BYTES} 位元組")
    if type(runtime_size) is not int:
        raise PackageError("執行期大小必須是整數")
    if not image_size <= runtime_size <= MAX_RUNTIME_BYTES:
        raise PackageError("執行期大小不得小於映像大小或超過 0x18000")
    if runtime_size % 16:
        raise PackageError("執行期大小必須以 16 位元組對齊")


def build_package(payload: bytes, runtime_size: int) -> bytes:
    """封裝 smoke 負載；即使映像已對齊，也保留完整的零填補區塊。"""
    if not isinstance(payload, (bytes, bytearray)):
        raise PackageError("負載必須是位元組資料")
    image_size = len(payload)
    _validate_sizes(image_size, runtime_size)
    payload = bytes(payload)
    header = bytearray(HEADER_BYTES)
    struct.pack_into(
        "<8s8I", header, 0, MAGIC, VERSION, HEADER_BYTES, BOARD_ID,
        image_size, runtime_size, ENTRY, 0, KIND_SMOKE,
    )
    header[40:72] = hashlib.sha256(payload).digest()
    struct.pack_into("<I", header, 508, zlib.crc32(header[:508]))
    # 尾端至少一個零，避免 XMODEM 函式庫移除負載末端的 0x1a。
    padding_size = (image_size // 512 + 1) * 512 - image_size
    return bytes(header) + payload + bytes(padding_size)


def parse_package(blob: bytes) -> dict[str, int | str]:
    """完整驗證封包；kind 為 smoke，hash 為負載雜湊，header_crc 為整數。"""
    if not isinstance(blob, (bytes, bytearray)):
        raise PackageError("封包必須是位元組資料")
    if not HEADER_BYTES <= len(blob) <= MAX_PACKAGE_BYTES:
        raise PackageError("封包總長超出範圍或標頭遭截斷")
    blob = bytes(blob)
    if blob[:8] != MAGIC:
        raise PackageError("封包識別碼錯誤；必須使用 BPISRAM1，不接受 eGON")
    header_crc = struct.unpack_from("<I", blob, 508)[0]
    if zlib.crc32(blob[:508]) != header_crc:
        raise PackageError("標頭 CRC 不符")
    version, header_bytes, board_id, image_size, runtime_size, entry, flags, kind = (
        struct.unpack_from("<8I", blob, 8)
    )
    for name, actual, expected in (
        ("version", version, VERSION),
        ("header_bytes", header_bytes, HEADER_BYTES),
        ("board_id", board_id, BOARD_ID),
        ("entry", entry, ENTRY),
        ("flags", flags, 0),
        ("kind", kind, KIND_SMOKE),
    ):
        if actual != expected:
            raise PackageError(f"標頭欄位 {name} 不符第一版 smoke 封包規格")
    if any(blob[72:508]):
        raise PackageError("標頭保留欄位必須全為零")
    _validate_sizes(image_size, runtime_size)
    expected_size = HEADER_BYTES + (image_size // 512 + 1) * 512
    if len(blob) != expected_size:
        raise PackageError("封包總長不符；禁止截斷、附加或省略零填補")
    payload_end = HEADER_BYTES + image_size
    digest = hashlib.sha256(blob[HEADER_BYTES:payload_end]).digest()
    if digest != blob[40:72]:
        raise PackageError("負載 SHA-256 雜湊不符")
    if any(blob[payload_end:]):
        raise PackageError("封包填補位元組必須全為零")
    return {
        "raw_size": len(blob),
        "image_size": image_size,
        "runtime_size": runtime_size,
        "kind": "smoke",
        "hash": digest.hex(),
        "header_crc": header_crc,
    }


@contextmanager
def _parent_directory(path: Path) -> Iterator[tuple[int, str]]:
    """逐層固定目錄描述符，拒絕任何路徑元件中的符號連結。"""
    if not path.name or path.name in (".", ".."):
        raise PackageError("必須指定一般檔案路徑")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(path.anchor or ".", flags)
    try:
        parts = path.parts[1:-1] if path.is_absolute() else path.parts[:-1]
        for part in parts:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory, path.name
    finally:
        os.close(directory)


def read_regular_file(path: Path, limit: int) -> bytes:
    """只讀取一般檔案，並限制讀取量，避免特殊檔案或無界輸入。"""
    with _parent_directory(path) as (directory, name):
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise PackageError("輸入必須是一般檔案，拒絕符號連結與裝置")
        if before.st_size > limit:
            raise PackageError(f"輸入檔案超過 {limit} 位元組上限")
        # O_PATH 不開啟裝置的資料通道，檢查後遭換成裝置也不會觸及硬體。
        descriptor = os.open(
            name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory,
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise PackageError("輸入必須是一般檔案")
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise PackageError("輸入檔案在開啟期間已被替換")
            # 僅重開已驗證且固定的描述符，不重新解析使用者提供的路徑。
            data_descriptor = os.open(
                f"/proc/self/fd/{descriptor}", os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC,
            )
            try:
                with os.fdopen(data_descriptor, "rb", closefd=False) as source:
                    blob = source.read(limit + 1)
                after = os.fstat(data_descriptor)
            finally:
                os.close(data_descriptor)
            if len(blob) > limit:
                raise PackageError(f"輸入檔案超過 {limit} 位元組上限")
            if (
                len(blob) != opened.st_size
                or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                raise PackageError("輸入檔案在讀取期間已變動")
            return blob
        finally:
            os.close(descriptor)


def write_new_regular_file(path: Path, blob: bytes) -> None:
    """以排他建立避免覆寫任何既有路徑，包括失效符號連結。"""
    with _parent_directory(path) as (directory, name):
        try:
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                | os.O_CLOEXEC, 0o600, dir_fd=directory,
            )
        except FileExistsError as exc:
            raise PackageError("輸出路徑已存在，禁止覆寫") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise PackageError("輸出必須是一般檔案")
            with os.fdopen(descriptor, "wb", closefd=False) as destination:
                destination.write(blob)
        finally:
            os.close(descriptor)


class ChineseArgumentParser(argparse.ArgumentParser):
    """讓說明與參數錯誤維持繁體中文，不回傳 argparse 的外語敘述。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["add_help"] = False
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明並離開")

    def format_usage(self) -> str:
        return super().format_usage().replace("usage: ", "用法：")

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", "用法：")

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "參數錯誤：參數缺漏、格式無效或不受支援；請以 --help 查看說明。\n")


def _runtime_size(value: str) -> int:
    try:
        return int(value, 16 if value.lower().startswith("0x") else 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("執行期大小須為十進位或 0x 十六進位整數") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="離線 SPL2 封包工具：SPL1 第一版 ABI，非 eGON；僅接受一般檔案。",
    )
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    pack = commands.add_parser("pack", help="產生 smoke 封包，禁止覆寫輸出")
    pack.add_argument("--input", type=Path, required=True, metavar="檔案", help="負載檔案")
    pack.add_argument("--output", type=Path, required=True, metavar="檔案", help="新封包檔案")
    pack.add_argument(
        "--runtime-size", type=_runtime_size, required=True, metavar="位元組",
        help="含執行期空間的大小，接受十進位或 0x 十六進位，須以 16 位元組對齊",
    )
    inspect = commands.add_parser("inspect", help="驗證完整封包並輸出 JSON")
    inspect.add_argument("--input", type=Path, required=True, metavar="檔案", help="封包檔案")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "pack":
            payload = read_regular_file(args.input, MAX_IMAGE_BYTES)
            blob = build_package(payload, args.runtime_size)
            write_new_regular_file(args.output, blob)
        else:
            blob = read_regular_file(args.input, MAX_PACKAGE_BYTES)
            print(json.dumps(parse_package(blob), ensure_ascii=False, sort_keys=True))
    except PackageError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"錯誤：一般檔案操作失敗或路徑不安全（errno={exc.errno}）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
