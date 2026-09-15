#!/usr/bin/env python3
"""從固定本機 DDR 來源建置獨立 SRAM ELF；不封包、不操作硬體。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

if __package__:
    from . import bpi_sram_package as package
else:
    import bpi_sram_package as package

try:
    import elftools
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection
except ImportError:
    elftools = None
    ELFError = ValueError


REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "patch/lab/u-boot/bananapim4zero/sram-ddr"
MANIFEST_SHA256 = "86ce3f8f0d58f80ab8c3f88bb907408c446c7fb7fee8c2c8fbc88c28edc93156"
BASE = 0x30000
END = 0x48000
MAX_INPUT_BYTES = 2 * 1024 * 1024
INPUT_NAMES = (
    "source-manifest.json", "compat.h", "payload.h", "entry.S", "payload.lds",
    "adaptation.patch", "lab_adapter.c", "protocol.c", "preflight.c", "runtime.c",
    "payload.c", "parser_harness.c",
)
SHIMS = (
    "config.h", "init.h", "log.h", "serial.h", "string.h", "time.h", "vsprintf.h",
    "asm/io.h", "asm/barriers.h", "linux/bitops.h", "linux/delay.h",
    "linux/kernel.h", "linux/types.h", "linux/ctype.h",
)
REQUIRED = {"_start", "ddr_main", "ddr_parse", "ddr_preflight", "ddr_lab_test",
            "mctl_core_init", "mctl_set_timing_params", "ddr_exception"}
FORBIDDEN = {"sunxi_dram_init", "sunxi_board_init", "board_init_f", "gd",
             "sunxi_h616_dram_lab_run", "sunxi_h616_dram_lab_bootstrap_begin",
             "sunxi_h616_dram_lab_set_bootstrap", "lab_reset", "lab_wdt_start",
             "malloc", "calloc", "realloc", "free", "mmc_init", "spl_mmc_load"}


class BuildError(ValueError):
    """固定來源或 SRAM 交付契約不符。"""


class ChineseParser(argparse.ArgumentParser):
    """固定以中文顯示命令介面。"""

    def __init__(self, **kwargs):
        super().__init__(add_help=False, allow_abbrev=False, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明並離開")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：")

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數錯誤：必填值缺漏、未知選項或格式不符，請查看 --help。\n")


def build_parser():
    parser = ChineseParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, metavar="來源目錄",
                        help="含固定套補丁內容的本機 U-Boot 工作樹，只讀")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄",
                        help="本專案 output 下的新目錄；父目錄須存在，不得位於既有 build-009")
    parser.add_argument("--cross-compile", default="aarch64-linux-gnu-", metavar="前綴",
                        help="交叉編譯工具前綴，預設 aarch64-linux-gnu-")
    return parser


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record(data: bytes) -> dict:
    return {"bytes": len(data), "sha256": digest(data)}


def plain_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise BuildError("路徑不得含 ..")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise BuildError(f"路徑不得經過符號連結：{parent}")
    return path


def read_regular(path: Path) -> bytes:
    path = plain_path(path)
    try:
        return package.read_regular_file(path, MAX_INPUT_BYTES)
    except package.PackageError as exc:
        raise BuildError(str(exc)) from exc


def new_output(path: Path, source: Path) -> Path:
    path = plain_path(path)
    protected = REPO / "output/evidence/bpi-sram-supervisor/build-009"
    if (not path.is_relative_to(REPO / "output") or path.is_relative_to(source)
            or path.is_relative_to(protected)):
        raise BuildError("輸出須在專案 output 內，且不得位於來源或 build-009")
    if path.exists() or not path.parent.is_dir():
        raise BuildError("輸出已存在或父目錄不存在，拒絕建置")
    return path


def environment(output: Path) -> dict:
    return {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(output),
        "TMPDIR": str(output / "tmp"), "LC_ALL": "C", "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "1789401600", "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CEILING_DIRECTORIES": str(output),
    }


def audit_elf(data: bytes, raw: bytes) -> dict:
    """以 pyelftools 解析並核對載入區段、符號與裸映像。"""
    if elftools is None:
        raise BuildError("缺少專案既有 pyelftools 依賴，不能稽核 ELF")
    try:
        return _audit_elf(ELFFile(io.BytesIO(data)), data, raw)
    except (ELFError, IndexError, KeyError, UnicodeError) as exc:
        raise BuildError("ELF 結構截斷或格式不符") from exc


def _audit_elf(elf, data: bytes, raw: bytes) -> dict:
    entry = elf.header["e_entry"]
    if (elf.elfclass != 64 or not elf.little_endian or elf.header["e_type"] != "ET_EXEC"
            or elf.header["e_machine"] != "EM_AARCH64" or entry != BASE):
        raise BuildError("ELF 必須是固定入口的 AArch64 小端執行檔")
    segments = []
    loads = []
    for segment in elf.iter_segments():
        if segment["p_type"] in ("PT_DYNAMIC", "PT_INTERP"):
            raise BuildError("ELF 不得依賴動態載入器")
        if segment["p_type"] != "PT_LOAD":
            continue
        flags, off, va, pa, filesz, memsz = (int(segment[key]) for key in
                                            ("p_flags", "p_offset", "p_vaddr", "p_paddr",
                                             "p_filesz", "p_memsz"))
        if (va != pa or not BASE <= va < va + memsz <= END or filesz > memsz
                or off + filesz > len(data) or flags & 3 == 3):
            raise BuildError("ELF 載入區段超界、截斷或同時可寫可執行")
        loads.append(segment)
        segments.append({"address": va, "file_bytes": filesz, "memory_bytes": memsz,
                         "flags": flags, "file_offset": off})
    if not segments or min(s["address"] for s in segments) != BASE:
        raise BuildError("ELF 缺少入口載入區段")
    ordered = sorted(segments, key=lambda s: s["address"])
    if any(a["address"] + a["memory_bytes"] > b["address"]
           for a, b in zip(ordered, ordered[1:])):
        raise BuildError("ELF 載入區段互相重疊")
    if not any(s["flags"] & 1 and s["address"] <= entry < s["address"] + s["file_bytes"]
               for s in segments):
        raise BuildError("ELF 入口不在可執行的檔案載入範圍")
    sections = list(elf.iter_sections())
    symbols = {}
    for section in sections:
        typ = section["sh_type"]
        flags, addr, size, offset = (int(section[key]) for key in
                                     ("sh_flags", "sh_addr", "sh_size", "sh_offset"))
        if flags & 2 and size:
            if not BASE <= addr < addr + size <= END:
                raise BuildError("ELF 執行期區段不在 SRAM")
            containers = [s for s in loads if s.section_in_segment(section)]
            if len(containers) != 1:
                raise BuildError(f"ALLOC 區段必須由唯一 PT_LOAD 涵蓋：{section.name}")
            segment = containers[0]
            if (flags & 4 and not segment["p_flags"] & 1 or
                    flags & 1 and not segment["p_flags"] & 2):
                raise BuildError(f"ALLOC 區段權限與 PT_LOAD 不符：{section.name}")
            if typ != "SHT_NOBITS":
                if (offset + size > len(data) or addr - segment["p_vaddr"] != offset - segment["p_offset"]
                        or len(section.data()) != size):
                    raise BuildError(f"ALLOC 區段與檔案載入位置不符：{section.name}")
        if typ in ("SHT_RELA", "SHT_REL") and size:
            raise BuildError("ELF 尚需重定位")
        if not isinstance(section, SymbolTableSection):
            continue
        for symbol in section.iter_symbols():
            text = symbol.name
            value, length = int(symbol["st_value"]), int(symbol["st_size"])
            if symbol["st_shndx"] == "SHN_UNDEF" and text:
                raise BuildError(f"ELF 有未解析符號：{text}")
            if text:
                symbols[text] = value
            if (symbol["st_info"]["type"] == "STT_FUNC" and length
                    and not BASE <= value < value + length <= 0x40000):
                raise BuildError("ELF 函式不在程式 SRAM 區域")
    if not REQUIRED <= symbols.keys() or FORBIDDEN & symbols.keys():
        raise BuildError("ELF 缺必要 DDR 符號或仍有舊啟動／配置相依")
    if (symbols.get("__runtime_end") != END or symbols.get("__stack_bottom") != 0x40000
            or not BASE < symbols.get("__image_end", 0) < 0x40000
            or not BASE < symbols.get("__bss_end", 0) <= 0x40000):
        raise BuildError("ELF 堆疊、BSS 或映像邊界不符")
    gate = symbols["ddr_preflight"]
    gate_bytes = b""
    for segment in segments:
        if segment["address"] <= gate <= segment["address"] + segment["file_bytes"] - 8:
            offset = segment["file_offset"] + gate - segment["address"]
            gate_bytes = data[offset:offset + 8]
    if gate_bytes != bytes.fromhex("00008052c0035fd6"):
        raise BuildError("ddr_preflight 函式入口必須直接返回零；此檢查不驗證呼叫點")
    load_end = max(s["p_paddr"] + s["p_filesz"] for s in loads if s["p_filesz"])
    expected = bytearray(load_end - BASE)
    for segment in loads:
        start, size = int(segment["p_paddr"]) - BASE, int(segment["p_filesz"])
        if size:
            expected[start:start + size] = segment.data()
    if raw != expected or len(raw) != symbols["__image_end"] - BASE:
        raise BuildError("裸映像與 ELF PT_LOAD 內容、零填補或映像邊界不符")
    return {"entry": entry, "runtime_bytes": END - BASE, "kind": 2, "context_abi": 2,
            "segments": segments, "symbols": symbols, "unresolved_symbols": [],
            "preflight_function": {"symbol": "ddr_preflight", "returns": 0,
                                   "scope": "function_bytes_only", "control_flow_verified": False},
            "raw_matches_load_segments": True,
            "parser": {"name": "pyelftools", "version": elftools.__version__}}


def write_new(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def build(args) -> Path:
    if elftools is None:
        raise BuildError("缺少專案既有 pyelftools 依賴")
    source = plain_path(args.source)
    output = new_output(args.output, source)
    if not re.fullmatch(r"[A-Za-z0-9_./+\-]+-", args.cross_compile):
        raise BuildError("交叉編譯前綴格式不符")
    inputs = {name: read_regular(INPUT / name) for name in INPUT_NAMES}
    builder_source = read_regular(Path(__file__))
    package_path = Path(package.__file__)
    package_source = read_regular(package_path)
    if digest(inputs["source-manifest.json"]) != MANIFEST_SHA256:
        raise BuildError("固定來源 manifest SHA-256 不符")
    manifest = json.loads(inputs["source-manifest.json"])
    sources = {}
    for name, expected in manifest["files"].items():
        sources[name] = read_regular(source / name)
        if digest(sources[name]) != expected:
            raise BuildError(f"來源 SHA-256 不符：{name}")
    tools = {}
    for name in ("git", "gcc", "objcopy", "readelf", "objdump", "nm", "size"):
        command = name if name == "git" else args.cross_compile + name
        found = shutil.which(command)
        if not found:
            raise BuildError(f"缺少工具：{command}")
        tools[name] = str(Path(found).resolve())
    output.mkdir(mode=0o700)
    (output / "tmp").mkdir()
    env = environment(output)
    report = {"status": "建置未完成", "hardware_status": manifest["hardware_status"],
              "source": str(source), "base_commit": manifest["base_commit"],
              "manifest_sha256": MANIFEST_SHA256, "commands": [], "artifacts": {},
              "builder": record(builder_source),
              "builder_dependencies": {"bpi_sram_package.py": record(package_source)},
              "inputs": {name: record(data) for name, data in inputs.items()},
              "sources": {name: record(data) for name, data in sources.items()}}
    with (output / "build.log").open("xb") as log:
        def run(argv, cwd=output):
            result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=120, check=False)
            report["commands"].append({"argv": list(map(str, argv)), "cwd": str(cwd),
                                       "returncode": result.returncode})
            log.write(("執行：" + json.dumps(list(map(str, argv)), ensure_ascii=False) + "\n").encode())
            log.write(result.stdout)
            log.flush()
            if result.returncode:
                raise BuildError(f"命令失敗，結束碼 {result.returncode}；請查看 {output / 'build.log'}")
            return result.stdout

        try:
            identity = run([tools["git"], "-c", f"safe.directory={source}", "-C", str(source),
                            "rev-parse", "HEAD", "HEAD^{tree}"])
            if identity.decode().splitlines() != [manifest["base_commit"], manifest["base_tree"]]:
                raise BuildError("來源 Git 基底提交或 tree 不符")
            report["tools"] = {name: {"path": path, "sha256": digest(Path(path).read_bytes()),
                                      "version": run([path, "--version"]).decode().splitlines()[0]}
                               for name, path in tools.items()}
            for name, data in inputs.items():
                write_new(output / "inputs" / name, data)
            write_new(output / "inputs/build_bpi_sram_ddr.py", builder_source)
            write_new(output / "inputs/bpi_sram_package.py", package_source)
            for name, data in sources.items():
                write_new(output / "pristine" / name, data)
                write_new(output / "source" / name, data)
            run([tools["git"], "apply", "--check", str(output / "inputs/adaptation.patch")], output / "source")
            run([tools["git"], "apply", str(output / "inputs/adaptation.patch")], output / "source")
            report["adapted_sources"] = {name: record((output / "source" / name).read_bytes())
                                         for name in sources}
            for name in SHIMS:
                write_new(output / "include" / name, b'#include "compat.h"\n')
            prefix = "arch/arm/include/asm/arch-sunxi/"
            for name, data in sources.items():
                if name.startswith(prefix):
                    write_new(output / "include/asm/arch" / name[len(prefix):], data)
            flags = ["-Os", "-g", "-std=gnu11", "-ffreestanding", "-fno-builtin",
                     "-fno-stack-protector", "-fno-pie", "-fno-pic", "-ffixed-x18",
                     "-mgeneral-regs-only", "-mstrict-align", "-fno-unwind-tables",
                     "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
                     "-fstack-usage", "-Werror=implicit-function-declaration",
                     f"-ffile-prefix-map={output}=/bpi-ddr", f"-fdebug-prefix-map={output}=/bpi-ddr",
                     "-I", str(output / "inputs"), "-I", str(output / "include"),
                     "-I", str(output / "source/arch/arm/mach-sunxi"),
                     "-include", str(output / "inputs/compat.h")]
            local = ("protocol.c", "preflight.c", "runtime.c", "payload.c", "lab_adapter.c")
            originals = ("arch/arm/mach-sunxi/dram_sun50i_h616.c",
                         "arch/arm/mach-sunxi/dram_dw_helpers.c",
                         "arch/arm/mach-sunxi/dram_helpers.c",
                         "arch/arm/mach-sunxi/dram_timings/h616_lpddr4_2133.c", "lib/tiny-printf.c")
            (output / "objects").mkdir()
            objects = []
            for path in ([output / "inputs" / name for name in local] +
                         [output / "source" / name for name in originals]):
                obj = output / "objects" / (path.stem + ".o")
                run([tools["gcc"], *flags, "-c", str(path), "-o", str(obj)])
                objects.append(str(obj))
            entry = output / "objects/entry.o"
            run([tools["gcc"], "-c", str(output / "inputs/entry.S"), "-o", str(entry)])
            elf = output / "spl2-ddr.elf"
            run([tools["gcc"], "-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none",
                 "-Wl,--gc-sections", "-Wl,--no-undefined", "-Wl,-z,max-page-size=4096",
                 f"-Wl,-T,{output / 'inputs/payload.lds'}", f"-Wl,-Map,{output / 'spl2-ddr.map'}",
                 str(entry), *objects, "-o", str(elf)])
            run([tools["objcopy"], "-O", "binary", str(elf), str(output / "spl2-ddr.bin")])
            report["elf_audit"] = audit_elf(elf.read_bytes(), (output / "spl2-ddr.bin").read_bytes())
            for tool, switches, suffix in (("readelf", ["-aW"], "readelf"),
                                           ("objdump", ["-d"], "objdump"),
                                           ("nm", ["-nS"], "nm"), ("size", [], "size")):
                write_new(output / f"spl2-ddr.{suffix}.txt", run([tools[tool], *switches, str(elf)]))
            # 同一解析物件以 Linux ABI 執行純邏輯測試，不執行負載或 MMIO。
            harness = output / "parser-harness"
            run([tools["gcc"], *flags, "-static", "-no-pie", "-Wl,--build-id=none",
                 "-Wl,--gc-sections", str(output / "inputs/parser_harness.c"),
                 *[str(output / "objects" / name) for name in
                   ("lab_adapter.o", "protocol.o", "preflight.o", "dram_dw_helpers.o", "tiny-printf.o")],
                 "-o", str(harness)])
            usage = []
            for path in sorted((output / "objects").glob("*.su")):
                for line in path.read_text().splitlines():
                    fields = line.split("\t")
                    if len(fields) != 3 or fields[2] != "static" or int(fields[1]) > 2048:
                        raise BuildError("函式堆疊非靜態或單框超過 2048 bytes")
                    usage.append({"function": fields[0], "bytes": int(fields[1])})
            if not usage:
                raise BuildError("缺少編譯器堆疊用量證據")
            report["stack_usage"] = usage
            report["stack_note"] = "單框大小已檢查；尚不代表完整呼叫鏈或實板堆疊驗證。"
            for name, data in sources.items():
                if read_regular(source / name) != data:
                    raise BuildError("建置期間原始來源已改變")
            for name, data in inputs.items():
                if read_regular(INPUT / name) != data:
                    raise BuildError("建置期間接合層輸入已改變")
            if read_regular(Path(__file__)) != builder_source:
                raise BuildError("建置期間工具本身已改變")
            if read_regular(package_path) != package_source:
                raise BuildError("建置期間安全讀檔模組已改變")
            for path in sorted(output.iterdir()):
                if path.is_file():
                    report["artifacts"][path.name] = record(path.read_bytes())
            report["status"] = "離線 ELF 建置與靜態稽核完成；僅核對前置函式返回零，未驗證整體控制流程"
            report["inputs_unchanged"] = True
            report["packaged"] = False
        finally:
            write_new(output / "build-report.json",
                      (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode())
    return output


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = build(args)
    except (BuildError, OSError, subprocess.SubprocessError) as exc:
        print(f"建置失敗：{exc}", file=sys.stderr)
        return 1
    print(f"離線建置完成：{output}；未封包、未上板，PMIC／幾何前置未核准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
