#!/usr/bin/env python3
"""第二版 DDR SPL2 封包；與舊 smoke 型別分離，不連線或部署。"""

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


VERSION = 2
KIND_DDR = 2


def build_package(payload, runtime_size):
    if not isinstance(payload, (bytes, bytearray)):
        raise base.PackageError("DDR 負載必須是位元組資料")
    payload = bytes(payload)
    base._validate_sizes(len(payload), runtime_size)
    header = bytearray(base.HEADER_BYTES)
    struct.pack_into("<8s8I", header, 0, base.MAGIC, VERSION, base.HEADER_BYTES,
                     base.BOARD_ID, len(payload), runtime_size, base.ENTRY, 0, KIND_DDR)
    header[40:72] = hashlib.sha256(payload).digest()
    struct.pack_into("<I", header, 508, zlib.crc32(header[:508]))
    padding = (len(payload) // 512 + 1) * 512 - len(payload)
    return bytes(header) + payload + bytes(padding)


def parse_package(blob):
    if not isinstance(blob, (bytes, bytearray)):
        raise base.PackageError("DDR 封包必須是位元組資料")
    if not 1024 <= len(blob) <= base.MAX_PACKAGE_BYTES:
        raise base.PackageError("DDR 封包總長超出範圍")
    blob = bytes(blob)
    magic, version, size, board, image, runtime, entry, flags, kind = struct.unpack_from("<8s8I", blob)
    if (magic, version, size, board, entry, flags, kind) != (
            base.MAGIC, VERSION, 512, base.BOARD_ID, base.ENTRY, 0, KIND_DDR):
        raise base.PackageError("DDR 封包識別或型別不符；不接受舊 smoke")
    base._validate_sizes(image, runtime)
    if any(blob[72:508]) or zlib.crc32(blob[:508]) != struct.unpack_from("<I", blob, 508)[0]:
        raise base.PackageError("DDR 標頭保留區或 CRC 不符")
    expected = 512 + (image // 512 + 1) * 512
    if len(blob) != expected or any(blob[512 + image:]):
        raise base.PackageError("DDR 封包長度或填補不符")
    digest = hashlib.sha256(blob[512:512 + image]).digest()
    if digest != blob[40:72]:
        raise base.PackageError("DDR 負載 SHA-256 不符")
    return {"version": VERSION, "kind": "ddr", "image_size": image,
            "runtime_size": runtime, "raw_size": len(blob), "hash": digest.hex()}


def main(argv=None):
    parser = base.ChineseArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, title="子命令")
    pack = commands.add_parser("pack", help="封裝已獨立稽核的 DDR 負載")
    pack.add_argument("--input", type=Path, required=True, help="負載檔案")
    pack.add_argument("--output", type=Path, required=True, help="新封包檔案")
    pack.add_argument("--runtime-size", type=base._runtime_size, required=True, help="執行期總長")
    inspect = commands.add_parser("inspect", help="只驗證封包格式與內容")
    inspect.add_argument("--input", type=Path, required=True, help="封包檔案")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            payload = base.read_regular_file(args.input, base.MAX_IMAGE_BYTES)
            blob = build_package(payload, args.runtime_size)
            base.write_new_regular_file(args.output, blob)
        else:
            blob = base.read_regular_file(args.input, base.MAX_PACKAGE_BYTES)
        print(json.dumps(parse_package(blob), ensure_ascii=False))
        return 0
    except (OSError, base.PackageError) as exc:
        print("錯誤：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
