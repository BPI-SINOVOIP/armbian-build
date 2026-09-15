#!/usr/bin/env python3
"""第三版實驗封包：更新或開機負載，僅離線封裝與驗證，不執行或操作硬體。"""

import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

if __package__:
    from . import bpi_sram_package as base
else:
    import bpi_sram_package as base


VERSION = 3
KIND_UPDATE = 3
KIND_BOOT = 4


def build_package(payload: bytes, runtime_size: int, kind: int) -> bytes:
    """封裝第三版負載；kind 僅接受更新 3 或開機 4 的整數。"""
    if not isinstance(payload, (bytes, bytearray)):
        raise base.PackageError("實驗負載必須是位元組資料")
    if type(kind) is not int or kind not in (KIND_UPDATE, KIND_BOOT):
        raise base.PackageError("實驗封包 kind 必須是整數 3（更新）或 4（開機）")
    payload = bytes(payload)
    base._validate_sizes(len(payload), runtime_size)
    header = bytearray(base.HEADER_BYTES)
    struct.pack_into(
        "<8s8I", header, 0, base.MAGIC, VERSION, base.HEADER_BYTES,
        base.BOARD_ID, len(payload), runtime_size, base.ENTRY, 0, kind,
    )
    header[40:72] = hashlib.sha256(payload).digest()
    struct.pack_into("<I", header, 508, zlib.crc32(header[:508]))
    # 沿用既有規格：已對齊的負載仍保留一整個零填補區塊。
    padding = (len(payload) // 512 + 1) * 512 - len(payload)
    return bytes(header) + payload + bytes(padding)


def parse_package(blob: bytes) -> dict[str, int | str]:
    """完整驗證第三版格式並回傳中繼資料，不判斷負載能否安全執行。"""
    if not isinstance(blob, (bytes, bytearray)):
        raise base.PackageError("實驗封包必須是位元組資料")
    if not 1024 <= len(blob) <= base.MAX_PACKAGE_BYTES:
        raise base.PackageError("實驗封包總長超出範圍或遭截斷")
    blob = bytes(blob)
    magic, version, size, board, image, runtime, entry, flags, kind = struct.unpack_from(
        "<8s8I", blob,
    )
    if (magic, version, size, board, entry, flags) != (
        base.MAGIC, VERSION, base.HEADER_BYTES, base.BOARD_ID, base.ENTRY, 0,
    ) or kind not in (KIND_UPDATE, KIND_BOOT):
        raise base.PackageError("實驗封包識別、版本或 kind 不符；僅接受第三版更新或開機封包")
    base._validate_sizes(image, runtime)
    if any(blob[72:508]):
        raise base.PackageError("實驗標頭保留區必須全為零")
    if zlib.crc32(blob[:508]) != struct.unpack_from("<I", blob, 508)[0]:
        raise base.PackageError("實驗標頭 CRC 不符")
    expected = base.HEADER_BYTES + (image // 512 + 1) * 512
    if len(blob) != expected:
        raise base.PackageError("實驗封包長度不符；禁止截斷、附加或省略零填補")
    payload_end = base.HEADER_BYTES + image
    if any(blob[payload_end:]):
        raise base.PackageError("實驗封包填補位元組必須全為零")
    digest = hashlib.sha256(blob[base.HEADER_BYTES:payload_end]).digest()
    if digest != blob[40:72]:
        raise base.PackageError("實驗負載 SHA-256 不符")
    return {
        "version": VERSION, "kind": kind, "image_size": image,
        "runtime_size": runtime, "raw_size": len(blob), "hash": digest.hex(),
    }


def main(argv=None) -> int:
    parser = base.ChineseArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    pack = commands.add_parser("pack", help="產生第三版實驗封包，禁止覆寫輸出")
    pack.add_argument("--input", type=Path, required=True, metavar="檔案", help="負載檔案")
    pack.add_argument("--output", type=Path, required=True, metavar="檔案", help="新封包檔案")
    pack.add_argument(
        "--kind", choices=("update", "boot"), required=True,
        help="負載型別：update 為更新，boot 為開機",
    )
    pack.add_argument(
        "--runtime-size", type=base._runtime_size, required=True, metavar="位元組",
        help="執行期總長，接受十進位或 0x 十六進位，須以 16 位元組對齊",
    )
    inspect = commands.add_parser("inspect", help="只驗證封包格式與內容，輸出 JSON")
    inspect.add_argument("--input", type=Path, required=True, metavar="檔案", help="封包檔案")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            payload = base.read_regular_file(args.input, base.MAX_IMAGE_BYTES)
            kind = KIND_UPDATE if args.kind == "update" else KIND_BOOT
            blob = build_package(payload, args.runtime_size, kind)
        else:
            blob = base.read_regular_file(args.input, base.MAX_PACKAGE_BYTES)
        metadata = parse_package(blob)
        if args.command == "pack":
            base.write_new_regular_file(args.output, blob)
        print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        return 0
    except base.PackageError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"錯誤：一般檔案操作失敗或路徑不安全（errno={exc.errno}）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
