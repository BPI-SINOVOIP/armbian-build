#!/usr/bin/env python3
"""離線建置固定 FIT 的 SRAM 橋接；僅接受明確 SHA 核對的 40 KiB eGON SPL。"""

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile

if __package__:
    from . import bpi_sram_lab_package as package
else:
    import bpi_sram_lab_package as package


base = package.base
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "patch/lab/u-boot/bananapim4zero/sram-boot"
SPL_BYTES = 0xa000
RUNTIME_BYTES = 0x18000
N2_SPL_SHA256 = "913232bfc5fb5f1b4388cbfd8add42bd5f9b65f458cb634568a03b520cfaa50d"
N2_BACKUP = ROOT / "output/evidence/bpi-h618-recovery-network/N2-m4zero-0845-20260915/deployment/before.bin"
N2_BACKUP_SHA256 = "cf21a379c169725ecdc89681f7508e426a60ffaa9bc700e08b6051b98cf58a0d"


class BuildError(ValueError):
    """輸入或產物不符合固定橋接契約。"""


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def require(condition, message):
    if not condition:
        raise BuildError(message)


def validate_spl(blob, expected_sha256):
    """核對磁碟原始標頭，不接受已被 BootROM／SPL 改寫的 RAM 標頭。"""
    require(len(blob) == SPL_BYTES, "SPL 長度必須恰為 40 KiB")
    require(isinstance(expected_sha256, str) and
            re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256), "必須明確提供 64 碼 SPL SHA-256")
    require(digest(blob) == expected_sha256.lower(), "SPL SHA-256 不符")
    require(blob[:4] == struct.pack("<I", 0xea000016), "eGON 首指令必須跳過 96 位元組標頭")
    require(blob[4:12] == b"eGON.BT0", "eGON magic 不符")
    require(struct.unpack_from("<I", blob, 16)[0] == SPL_BYTES, "eGON length 不符")
    require(blob[20:24] == b"SPL\x02", "僅接受 mainline SPL 第二版磁碟標頭")
    require(blob[24:32] == bytes(8) and blob[36:44] == bytes(8),
            "FEL、DRAM 與 boot_media 欄位必須為零；僅支援 SD0 原固定位置")
    require(struct.unpack_from("<I", blob, 32)[0] == 44, "裝置樹名稱必須位於固定標頭字串區")
    name, separator, padding = blob[44:96].partition(b"\0")
    require(separator and not any(padding) and name in (
        b"sun50i-h618-bananapi-m4-zero", b"allwinner/sun50i-h618-bananapi-m4-zero",
    ), "標頭板型或字串填補不符 M4 Zero")
    words = struct.unpack("<10240I", blob)
    checksum = (sum(words) - words[3] + 0x5f0a6c39) & 0xffffffff
    require(checksum == words[3], "eGON checksum 不符")
    entry, branch = struct.unpack_from("<II", blob, 96)
    require(entry == 0xea00001f and branch & 0xfc000000 == 0x14000000,
            "不支援此 SPL 的 AArch64 雙模式入口")
    immediate = branch & 0x3ffffff
    if immediate & 0x2000000:
        immediate -= 0x4000000
    reset = 0x20064 + immediate * 4
    require(0x20068 <= reset < 0x2a000, "SPL reset 分支超出原始載入區")
    return {"bytes": len(blob), "sha256": digest(blob), "checksum": checksum,
            "entry": 0x20060, "reset": reset, "board": name.decode("ascii"),
            "known_diagnostic": digest(blob) == N2_SPL_SHA256}


def plain_path(path):
    path = Path(path).expanduser().absolute()
    require(".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            "路徑不得含 .. 或符號連結")
    return path


def audit_elf(blob, raw, spl):
    elf = ELFFile(BytesIO(blob))
    require(elf["e_machine"] == "EM_AARCH64" and elf["e_type"] == "ET_EXEC" and
            elf["e_entry"] == 0x30000 and elf.little_endian, "ELF 架構或入口不符")
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
    expected = {"_start": 0x30000, "bridge_spl": 0x32000, "bridge_spl_end": 0x3c000,
                "bridge_context": 0x3f000, "bridge_context_end": 0x3f040,
                "bridge_vectors": 0x40000, "bridge_vectors_end": 0x40800,
                "__image_end": 0x40800, "__image_bytes": 0x10800,
                "__stack_bottom": 0x42000, "__stack_top": 0x47ff0}
    require(all(symbols.get(k) == v for k, v in expected.items()), "ELF 記憶體契約不符")
    for symbol in elf.get_section_by_name(".symtab").iter_symbols():
        require(not symbol.name or symbol["st_shndx"] != "SHN_UNDEF", "ELF 含未解析的外部相依")
    ranges = []
    for segment in elf.iter_segments():
        if segment["p_type"] != "PT_LOAD":
            continue
        start, end = segment["p_vaddr"], segment["p_vaddr"] + segment["p_memsz"]
        require(0x30000 <= start < end <= 0x48000 and segment["p_paddr"] == start,
                "ELF 執行區段超出橋接 SRAM")
        require(all(end <= lo or start >= hi for lo, hi in ranges), "ELF 載入區段重疊")
        ranges.append((start, end))
    for section in elf.iter_sections():
        if not section["sh_flags"] & 2 or not section["sh_size"]:
            continue
        require(section.name in (".text", ".spl", ".context", ".vectors", ".stack"),
                "ELF 含非預期執行期區段")
        if section["sh_type"] != "SHT_NOBITS":
            offset = section["sh_addr"] - 0x30000
            require(raw[offset:offset + section["sh_size"]] == section.data(), "ELF 與裸映像不一致")
    require(len(raw) == 0x10800 and raw[0x2000:0xc000] == spl, "裸映像中的 SPL 位置或內容不符")
    return {name: symbols[name] for name in (*expected, "bridge_copy_start", "bridge_handoff", "bridge_stop")}


def build(spl_path, expected_sha256, output):
    spl_path, output = plain_path(spl_path), plain_path(output)
    spl = base.read_regular_file(spl_path, SPL_BYTES)
    metadata = validate_spl(spl, expected_sha256)
    inputs = {"bridge.S": SOURCE / "bridge.S", "bridge.lds": SOURCE / "bridge.lds",
              "build_bpi_sram_boot.py": Path(__file__),
              "bpi_sram_lab_package.py": Path(package.__file__), "bpi_sram_package.py": Path(base.__file__)}
    snapshots = {name: base.read_regular_file(path, 1024 * 1024) for name, path in inputs.items()}
    toolchain = {}
    for name in ("gcc", "ld", "objcopy"):
        tool = shutil.which("aarch64-linux-gnu-" + name)
        require(tool is not None, f"缺少交叉工具：aarch64-linux-gnu-{name}")
        toolchain[name] = tool
    require(not output.exists(), "輸出目錄已存在，禁止覆寫")
    with base._parent_directory(output) as (parent, name):
        os.mkdir(name, mode=0o700, dir_fd=parent)
    (output / "inputs").mkdir(mode=0o700)
    for name, content in {**snapshots, "spl.bin": spl}.items():
        base.write_new_regular_file(output / "inputs" / name, content)
    commands = [
        [toolchain["gcc"], "-c", "-x", "assembler-with-cpp", "-march=armv8-a",
         "-nostdlib", "-o", "../bridge.o", "bridge.S"],
        [toolchain["ld"], "--no-undefined", "--fatal-warnings", "--build-id=none",
         "-T", "bridge.lds", "-Map=../bridge.map", "-o", "../bridge.elf", "../bridge.o"],
        [toolchain["objcopy"], "-O", "binary", "../bridge.elf", "../bridge.bin"],
    ]
    report = {"status": "未通過", "scope": "僅主機建置；未執行 SD、UART、電源或 DDR 操作",
              "fit_selection": "沿用原 SPL 固定位置；不支援槽 FIT LBA 或多 FIT",
              "hardware_validation": "未執行", "runtime_bytes": RUNTIME_BYTES,
              "spl_path": str(spl_path), "spl": metadata,
              "source_sha256": {k: digest(v) for k, v in snapshots.items()}, "commands": []}
    if metadata["known_diagnostic"]:
        report["limitation"] = "N2 備份為 D1 診斷 SPL；後段轉入診斷，不能代表正常 U-Boot／Linux 啟動"
    else:
        report["limitation"] = "標頭相容與 SHA 核對不代表任意 SPL 的完整初始化及 OS 相容性已通過"
    try:
        environment = dict(os.environ, LC_ALL="C", SOURCE_DATE_EPOCH="1789401600")
        for argv in commands:
            result = subprocess.run(argv, cwd=output / "inputs", env=environment,
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
            report["commands"].append({"argv": argv, "returncode": result.returncode,
                                       "stdout": result.stdout.decode(errors="replace"),
                                       "stderr": result.stderr.decode(errors="replace")})
            require(result.returncode == 0, "交叉工具執行失敗；詳見建置報告")
        elf = base.read_regular_file(output / "bridge.elf", 1024 * 1024)
        raw = base.read_regular_file(output / "bridge.bin", base.MAX_IMAGE_BYTES)
        report["symbols"] = audit_elf(elf, raw, spl)
        packaged = package.build_package(raw, RUNTIME_BYTES, package.KIND_BOOT)
        report["package"] = package.parse_package(packaged)
        require(base.read_regular_file(spl_path, SPL_BYTES) == spl and all(
            base.read_regular_file(inputs[name], 1024 * 1024) == value for name, value in snapshots.items()),
            "建置期間輸入或來源變動，禁止交付")
        report["inputs_unchanged"] = True
        base.write_new_regular_file(output / "bridge-package.bin", packaged)
        report["artifacts"] = {name: {"bytes": len(content), "sha256": digest(content)} for name, content in
                               (("bridge.elf", elf), ("bridge.bin", raw), ("bridge-package.bin", packaged))}
        report["status"] = "離線建置與靜態稽核通過，尚未實板驗證"
    finally:
        base.write_new_regular_file(output / "build-report.json",
                                    (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def build_parser():
    parser = base.ChineseArgumentParser(description=__doc__)
    parser.add_argument("--spl", type=Path, required=True, metavar="檔案", help="原始 40 KiB eGON SPL 一般檔案")
    parser.add_argument("--spl-sha256", required=True, metavar="雜湊", help="獨立核對的 SPL SHA-256")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄", help="父目錄須存在；禁止覆寫")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = build(args.spl, args.spl_sha256, args.output)
        print(json.dumps({"status": report["status"], "limitation": report["limitation"],
                          "output": str(args.output)}, ensure_ascii=False))
        return 0
    except (BuildError, base.PackageError, OSError, subprocess.TimeoutExpired) as exc:
        detail = f"一般檔案操作失敗（errno={exc.errno}）" if isinstance(exc, OSError) else (
            "交叉工具逾時" if isinstance(exc, subprocess.TimeoutExpired) else str(exc))
        print(f"錯誤：{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
