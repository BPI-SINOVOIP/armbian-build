#!/usr/bin/env python3
"""從固定本機 U-Boot 建置及封裝第三版 SRAM 更新器，不部署或操作硬體。"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
from typing import Sequence

if __package__:
    from . import audit_bpi_sram_build as audit
    from . import bpi_sram_lab_package as package
    from . import build_bpi_sram_supervisor as base
else:
    import audit_bpi_sram_build as audit
    import bpi_sram_lab_package as package
    import build_bpi_sram_supervisor as base


BuildError = base.BuildError
UPDATE = base.REPO / "patch/lab/u-boot/bananapim4zero/sram-update"
BASE = 0x30000
CODE_END = 0x40000
BSS_END = 0x43000
END = 0x48000
DEFCONFIG = "update_defconfig"
REQUIRED_SYMBOLS = {
    "_start", "board_init_f", "board_init_f_alloc_reserve", "board_init_f_init_reserve",
    "bpi_update_run", "sup_prepare", "sup_io_live", "sup_io_progress", "sup_sd_live",
    "sup_crc32", "mmc_init", "mmc_bread", "mmc_bwrite", "mmc_poll_for_busy",
    "bpi_update_mmc_poll_live", "xyzModem_stream_open", "xyzModem_stream_read",
    "sha256_starts", "sha256_update", "sha256_finish",
}
LAYOUT = {
    "_start": BASE, "__image_copy_start": BASE, "__bss_start": CODE_END,
    "__guard_start": 0x43FF0, "__guard_end": 0x44000, "__stack_bottom": 0x44000,
    "__initial_sp": END, "__malloc_start": 0x46000, "__malloc_end": END,
    "__gd_end": 0x46000, "__runtime_end": END,
}


def require(condition, message: str) -> None:
    if not condition:
        raise BuildError(message)


def build_parser():
    parser = base.ChineseArgumentParser(description=__doc__)
    parser.add_argument("--source-git", type=Path, required=True, metavar="目錄",
                        help="含固定提交的本機 Git 資料目錄；禁止下載與來源寫入")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄",
                        help="父目錄須存在；拒絕覆寫、符號連結及既有建置目錄內的輸出")
    parser.add_argument("--jobs", type=base.jobs_value, default=4, metavar="數量",
                        help="平行工作數，預設 4，上限 16")
    parser.add_argument("--cross-compile", type=base.cross_value,
                        default="aarch64-linux-gnu-", metavar="前綴",
                        help="交叉工具前綴，預設 aarch64-linux-gnu-")
    return parser


def input_paths() -> dict[str, Path]:
    paths = {name: base.OVERLAY / name for name in
             ("overlay.patch", "supervisor.h", "supervisor_core.c", "supervisor_entry.S")}
    paths.update({name: UPDATE / name for name in
                  ("update.c", "update_entry.S", "update.lds", "update-driver.patch", DEFCONFIG)})
    for module in (base, audit, package, package.base):
        path = Path(module.__file__).resolve()
        paths[path.name] = path
    paths[Path(__file__).name] = Path(__file__).resolve()
    return paths


def inspect_config(requested: bytes, actual: bytes) -> dict:
    wanted, config = audit.parse_config(requested), audit.parse_config(actual)
    for key, expected in wanted.items():
        require(audit.config_value(config.get(key, "n")) == audit.config_value(expected),
                f"更新器組態與快照不符：{key}")
    enabled = set(audit.REQUIRED_Y) | {
        "BPI_SRAM_UPDATE", "BPI_SRAM_LAB_V3", "BPI_SRAM_DDR_V2", "SPL_MMC_WRITE",
        "SPL_YMODEM_SUPPORT", "SPL_SYS_MALLOC_F", "MMC_SUNXI",
    }
    disabled = (set(audit.REQUIRED_N) - {"SPL_MMC_WRITE"}) | {"SPL_MMC_TINY", "SPL_DMA"}
    for key in enabled:
        require(config.get("CONFIG_" + key) == "y", f"更新器缺少必要組態：CONFIG_{key}")
    for key in disabled:
        require(config.get("CONFIG_" + key, "n") == "n", f"更新器啟用禁止組態：CONFIG_{key}")
    require(not any(key.startswith("CONFIG_DRAM_") and value in ("y", "m")
                    for key, value in config.items()), "更新器仍有 DRAM 初始化組態")
    for key, expected in {
        "SPL_TEXT_BASE": BASE, "SPL_STACK": END, "SPL_MAX_SIZE": 0x10000,
        "SPL_BSS_START_ADDR": CODE_END, "SPL_BSS_MAX_SIZE": 0x3000,
        "SPL_SYS_MALLOC_F_LEN": 0x2000, "SYS_MALLOC_F_LEN": 0x2000,
        "MMC_SUNXI_SLOT_EXTRA": "-1",
        "SPL_LDSCRIPT": '"arch/arm/mach-sunxi/update.lds"',
    }.items():
        require(audit.config_value(config.get("CONFIG_" + key, "n")) == expected,
                f"更新器固定配置不符：CONFIG_{key}")
    return {"required_enabled": sorted(enabled), "required_disabled": sorted(disabled),
            "snapshot_fields": len(wanted), "config_fields": len(config)}


def inspect_elf(blob: bytes, raw: bytes) -> dict:
    require(audit.elftools is not None, "缺少既有 pyelftools 依賴，不能稽核或封裝")
    elf = audit.ELFFile(io.BytesIO(blob))
    require(elf.elfclass == 64 and elf.little_endian and elf["e_machine"] == "EM_AARCH64"
            and elf["e_type"] == "ET_EXEC" and elf["e_entry"] == BASE,
            "更新器必須是入口 0x30000 的小端序 AArch64 ELF")
    loads = []
    for segment in elf.iter_segments():
        require(segment["p_type"] not in ("PT_INTERP", "PT_DYNAMIC"), "更新器不得依賴動態載入器")
        if segment["p_type"] != "PT_LOAD":
            continue
        p = {key: int(segment[key]) for key in
             ("p_vaddr", "p_paddr", "p_offset", "p_filesz", "p_memsz", "p_flags", "p_align")}
        start, end = p["p_paddr"], p["p_paddr"] + p["p_memsz"]
        require(p["p_vaddr"] == start and 0 <= p["p_filesz"] <= p["p_memsz"]
                and p["p_offset"] + p["p_filesz"] <= len(blob), "更新器 ELF 載入區段無效")
        code = BASE <= start < end <= CODE_END and p["p_filesz"] == p["p_memsz"]
        state = CODE_END <= start < end <= BSS_END and p["p_filesz"] == 0 and not p["p_flags"] & 1
        require(code or state, f"更新器載入區段超出程式或 BSS 區域：{start:#x}..{end:#x}")
        align = p["p_align"]
        require(align in (0, 1) or (align & (align - 1) == 0 and
                p["p_offset"] % align == start % align), "更新器 ELF 區段對齊無效")
        loads.append(p)
    require(loads, "更新器 ELF 缺少載入區段")
    ordered = sorted(loads, key=lambda p: p["p_paddr"])
    require(all(a["p_paddr"] + a["p_memsz"] <= b["p_paddr"]
                for a, b in zip(ordered, ordered[1:])), "更新器 ELF 載入區段重疊")
    symbols, allocated = {}, []
    for section in elf.iter_sections():
        require(not (section["sh_type"] in ("SHT_REL", "SHT_RELA", "SHT_DYNAMIC")
                     and section["sh_size"]), "更新器 ELF 仍需重定位")
        if isinstance(section, audit.SymbolTableSection):
            for symbol in section.iter_symbols():
                if not symbol.name:
                    continue
                require(symbol["st_shndx"] != "SHN_UNDEF", f"更新器有未解析符號：{symbol.name}")
                value, size = int(symbol["st_value"]), int(symbol["st_size"])
                symbols.setdefault(symbol.name, set()).add(value)
                if symbol["st_info"]["type"] == "STT_FUNC" and size:
                    require(BASE <= value < value + size <= CODE_END,
                            f"更新器函式不在程式 SRAM：{symbol.name}")
        if not section["sh_flags"] & 2 or not section["sh_size"]:
            continue
        start, size = int(section["sh_addr"]), int(section["sh_size"])
        end = start + size
        containers = [p for p in loads if p["p_paddr"] <= start and
                      end <= p["p_paddr"] + p["p_memsz"]]
        require(len(containers) == 1, f"更新器區段沒有唯一載入範圍：{section.name}")
        p = containers[0]
        if section["sh_type"] == "SHT_NOBITS":
            require(CODE_END <= start < end <= BSS_END and not section["sh_flags"] & 4,
                    "更新器 BSS 區段超界或可執行")
        else:
            require(section["sh_type"] == "SHT_PROGBITS" and BASE <= start < end <= CODE_END
                    and end <= p["p_paddr"] + p["p_filesz"]
                    and start - p["p_paddr"] + p["p_offset"] == section["sh_offset"]
                    and len(section.data()) == size, f"更新器載入區段內容不符：{section.name}")
        require(not section["sh_flags"] & 4 or p["p_flags"] & 1, "更新器執行區段權限不符")
        require(not section["sh_flags"] & 1 or p["p_flags"] & 2, "更新器資料區段權限不符")
        allocated.append((start, end))
    allocated.sort()
    require(all(a[1] <= b[0] for a, b in zip(allocated, allocated[1:])), "更新器配置區段重疊")
    banned = audit.forbidden_symbols(symbols) + sorted({"_main", "bpi_supervisor_run"} & symbols.keys())
    require(not banned, "更新器含正常開機或 DRAM 符號：" + ", ".join(banned))
    needed = REQUIRED_SYMBOLS | LAYOUT.keys() | {
        "__bss_end", "__stack_top", "__gd_start", "__image_end", "_image_binary_end",
    }
    missing = needed - symbols.keys()
    require(not missing, "更新器缺少必要符號：" + ", ".join(sorted(missing)))
    require(all(len(symbols[name]) == 1 for name in needed), "更新器必要符號位址具有歧義")
    values = {name: next(iter(symbols[name])) for name in needed}
    for name, expected in LAYOUT.items():
        require(values[name] == expected, f"更新器連結邊界不符：{name}")
    require(CODE_END < values["__bss_end"] <= BSS_END, "更新器 BSS 結束位址不符")
    require(values["__stack_top"] == values["__gd_start"] and
            values["__stack_bottom"] + 0x1800 <= values["__stack_top"] < values["__gd_end"]
            and values["__stack_top"] % 16 == 0, "更新器堆疊或 gd 配置不符")
    file_loads = [p for p in loads if p["p_filesz"]]
    require(file_loads and min(p["p_paddr"] for p in file_loads) == BASE,
            "更新器裸映像未由入口開始")
    require(any(p["p_flags"] & 1 and p["p_paddr"] <= BASE < p["p_paddr"] + p["p_filesz"]
                for p in file_loads), "更新器入口不在可執行載入範圍")
    end = max(p["p_paddr"] + p["p_filesz"] for p in file_loads)
    require(end == values["__image_end"] == values["_image_binary_end"], "更新器映像尾端不符")
    expected = bytearray(end - BASE)
    for p in file_loads:
        offset = p["p_paddr"] - BASE
        expected[offset:offset + p["p_filesz"]] = blob[p["p_offset"]:p["p_offset"] + p["p_filesz"]]
    require(raw == expected, "更新器裸映像與 ELF 載入內容或填補不符")
    dwarf = inspect_dwarf(elf)
    gd_bytes = (dwarf["global_data"]["bytes"] + 15) & ~15
    require(values["__gd_end"] - values["__gd_start"] == gd_bytes,
            "連結腳本 gd 大小與 SPL 實際 DWARF 不符，可能誤用主程式 offsets")
    return {"entry": BASE, "runtime_bytes": END - BASE, "segments": loads,
            "symbols": values, "raw_matches_load_segments": True, "unresolved_symbols": [],
            "stack_bytes": values["__stack_top"] - values["__stack_bottom"],
            "bss_bytes": values["__bss_end"] - CODE_END,
            "dwarf_structures": dwarf,
            "parser": {"name": "pyelftools", "version": audit.elftools.__version__}}


def inspect_dwarf(elf) -> dict:
    debug_info = elf.get_section_by_name(".debug_info")
    require(debug_info is not None and debug_info["sh_size"] > 0 and elf.has_dwarf_info(),
            "更新器 ELF 缺少 DWARF，不得剝除除錯資料")
    wanted = {"mmc", "blk_desc", "global_data"}
    layouts = {}
    for unit in elf.get_dwarf_info().iter_CUs():
        for die in unit.iter_DIEs():
            if die.tag != "DW_TAG_structure_type":
                continue
            attributes = die.attributes
            if "DW_AT_name" not in attributes or "DW_AT_byte_size" not in attributes:
                continue
            name = attributes["DW_AT_name"].value.decode("ascii")
            if name not in wanted:
                continue
            members = {}
            for child in die.iter_children():
                if child.tag != "DW_TAG_member" or "DW_AT_name" not in child.attributes:
                    continue
                member = child.attributes["DW_AT_name"].value.decode("ascii")
                location = child.attributes.get("DW_AT_data_member_location")
                require(location is not None and type(location.value) is int,
                        f"DWARF 成員位移不是固定整數：{name}.{member}")
                members[member] = location.value
            layout = {"bytes": attributes["DW_AT_byte_size"].value, "members": members}
            require(name not in layouts or layouts[name] == layout, f"DWARF 結構定義衝突：{name}")
            layouts[name] = layout
    require(wanted <= layouts.keys(), "DWARF 缺少完整 mmc、blk_desc 或 global_data 定義")
    return layouts


def execute_build(runner, args, source_git: Path, paths: dict, inputs: dict) -> None:
    output = runner.output
    snapshot = output / "inputs"
    snapshot.mkdir()
    for name, data in inputs.items():
        path = snapshot / name
        with path.open("xb") as stream:
            stream.write(data)
        os.utime(path, (int(base.SOURCE_DATE_EPOCH), int(base.SOURCE_DATE_EPOCH)))
        runner.report["inputs"][name] = {"source": str(paths[name]), **base.file_record(path)}
    tools = {name: base.find_tool(name) for name in ("git", "make", "cc")}
    tools.update({name: base.find_tool(args.cross_compile + name) for name in
                  ("gcc", "ld", "as", "ar", "strip", "objcopy", "objdump", "readelf", "nm")})
    cross = tools["gcc"][:-3]
    if shutil.which(cross + "ld.bfd"):
        tools["ld.bfd"] = base.find_tool(cross + "ld.bfd")
    for name, path in tools.items():
        base.record_tool(runner, name, path)
    base.record_tool(runner, "python", sys.executable)
    machine = runner.run([tools["gcc"], "-dumpmachine"], capture=True)
    require(machine.startswith("aarch64"), "交叉編譯器目標不是 aarch64")
    runner.report["compiler_target"] = machine
    runner.report["compiler_version"] = runner.run([tools["gcc"], "-dumpfullversion"], capture=True)
    driver_ld = runner.run([tools["gcc"], "-print-prog-name=ld"], capture=True)
    base.record_tool(runner, "gcc_driver_ld", base.find_tool(driver_ld))
    git_options = [tools["git"], "-c", "protocol.allow=never", "-c", "gc.auto=0",
                   "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null",
                   "-c", "core.attributesFile=/dev/null"]
    git = [*git_options, "-c", f"safe.directory={source_git}", f"--git-dir={source_git}"]
    runner.report["source"].update(base.source_identity(runner, git))
    archive = output / "u-boot-original.tar"
    runner.run([*git, "archive", "--format=tar", base.COMMIT], stdout=archive)
    runner.report["source"]["archive"] = base.file_record(archive)
    source = output / "source"
    base.extract_archive(archive, source)
    archive.unlink()
    runner.report["source"]["archive_retained"] = False
    for name in ("overlay.patch", "update-driver.patch"):
        runner.run([*git_options, "apply", "--check", str(snapshot / name)], cwd=source)
        runner.run([*git_options, "apply", str(snapshot / name)], cwd=source)
    destinations = {name: source / "arch/arm/mach-sunxi" / name for name in
                    ("supervisor.h", "supervisor_core.c", "supervisor_entry.S", "update.lds")}
    destinations.update({"update.c": source / "arch/arm/mach-sunxi/supervisor.c",
                         "update_entry.S": source / "arch/arm/cpu/armv8/start.S",
                         DEFCONFIG: source / "configs" / DEFCONFIG})
    for name, destination in destinations.items():
        with destination.open("wb" if name == "update_entry.S" else "xb") as stream:
            stream.write(inputs[name])
        os.utime(destination, (int(base.SOURCE_DATE_EPOCH), int(base.SOURCE_DATE_EPOCH)))
        runner.report["adapted_sources"][str(destination.relative_to(source))] = base.file_record(destination)
    for name in ("arch/arm/mach-sunxi/board.c", "arch/arm/mach-sunxi/Kconfig",
                 "drivers/mmc/mmc.c", "drivers/mmc/sunxi_mmc.c"):
        runner.report["adapted_sources"][name] = base.file_record(source / name)
    build = output / "build"
    build.mkdir()
    make = [tools["make"], "-C", str(source), f"O={build}", "ARCH=arm",
            f"CROSS_COMPILE={cross}", f"HOSTCC={tools['cc']}",
            f"LD={tools.get('ld.bfd', tools['ld'])}", "KCFLAGS=-g -fstack-usage", "V=1"]
    runner.run([*make, DEFCONFIG])
    config = build / ".config"
    runner.report["config_audit"] = inspect_config(inputs[DEFCONFIG], base.read_input(config))
    runner.run([*make, f"-j{args.jobs}", "spl/u-boot-spl.bin"])
    require(not (build / "spl/sunxi-spl.bin").exists(), "更新器不得產生 eGON 部署映像")
    runner.report["config_audit"] = inspect_config(inputs[DEFCONFIG], base.read_input(config))
    runner.report["config"] = {**base.file_record(config), "text": config.read_text(encoding="utf-8")}
    for name, destination in (("u-boot-spl", "update.elf"), ("u-boot-spl.bin", "update.bin"),
                              ("u-boot-spl.map", "update.map")):
        base.copy_artifact(runner, build / "spl" / name, output / destination)
    for suffix in ("elf", "bin"):
        base.copy_artifact(runner, output / f"update.{suffix}", output / f"spl2-update.{suffix}")
    base.copy_artifact(runner, config, output / "update.config")
    base.audit_elf(runner, tools, output / "update.elf", "update")
    base.copy_artifact(runner, build / "spl/include/generated/generic-asm-offsets.h",
                       output / "spl-generic-asm-offsets.h")
    base.copy_artifact(runner, build / "spl/u-boot-spl.lds", output / "update-linked.lds")
    raw = base.read_input(output / "update.bin")
    runner.report["elf_audit"] = inspect_elf(base.read_input(output / "update.elf"), raw)
    usage = sorted((build / "spl").rglob("*.su"))
    require(usage, "未產生更新器堆疊用量紀錄")
    for path in usage:
        base.copy_artifact(runner, path, output / "stack-usage" / path.relative_to(build / "spl"))
    blob = package.build_package(raw, END - BASE, package.KIND_UPDATE)
    metadata = package.parse_package(blob)
    require(metadata["version"] == 3 and metadata["kind"] == 3 and
            metadata["runtime_size"] == END - BASE, "更新器封包契約不符")
    with (output / "update.pkg").open("xb") as stream:
        stream.write(blob)
    require(package.parse_package(base.read_input(output / "update.pkg")) == metadata,
            "更新器封包寫入後驗證不符")
    runner.report["package"] = metadata
    runner.report["artifacts"]["update.pkg"] = base.file_record(output / "update.pkg")
    require(base.source_identity(runner, git) == {"commit": base.COMMIT, "tree": base.TREE},
            "來源 Git 基準在建置期間改變")
    for name, data in inputs.items():
        require(base.read_input(paths[name]) == data and base.read_input(snapshot / name) == data,
                f"建置期間輸入或快照已改變：{name}")
    for name, expected in runner.report["adapted_sources"].items():
        require(base.file_record(source / name) == expected, f"建置期間套用來源已改變：{name}")
    require(not (build / "spl/sunxi-spl.bin").exists(), "更新器不得產生 eGON 部署映像")
    runner.report["inputs_unchanged"] = True
    runner.report["payload_packaged"] = True


def build(args, invocation: Sequence[str]) -> Path:
    require(audit.elftools is not None, "缺少既有 pyelftools 依賴，拒絕未稽核建置")
    source_git = args.source_git.expanduser().resolve(strict=True)
    require(source_git.is_dir(), "--source-git 必須是 Git 資料目錄")
    output = base.new_output_path(args.output, source_git)
    require(not output.is_relative_to(UPDATE), "輸出不得位於更新器來源目錄內")
    require(not any((parent / "build-report.json").exists() for parent in output.parents),
            "輸出不得位於既有建置目錄內")
    paths = input_paths()
    inputs = {}
    for name, path in paths.items():
        try:
            inputs[name] = base.read_input(path)
        except FileNotFoundError as exc:
            raise BuildError(f"缺少建置輸入：{path}；未建立輸出，也不會補造介面") from exc
    output.mkdir(mode=0o700)
    report = {
        "status": "失敗", "hardware_validation": "尚未執行", "payload_packaged": False,
        "lab_v3": True, "kind": 3, "runtime_bytes": END - BASE, "egon_generated": False,
        "builder": {"path": str(Path(__file__).resolve()), **base.file_record(Path(__file__))},
        "invocation": list(invocation), "commands": [], "tools": {}, "inputs": {}, "artifacts": {},
        "adapted_sources": {},
        "source": {"git_dir": str(source_git), "expected_commit": base.COMMIT,
                   "expected_tree": base.TREE, "expected_archive_sha256": base.ARCHIVE_SHA256},
        "environment": base.build_environment(output),
        "scope": "僅離線建置、SRAM 邊界稽核與封包驗證；不是交易、媒體或實板驗證",
    }
    try:
        (output / "tmp").mkdir()
        with (output / "build.log").open("xb") as log:
            execute_build(base.Runner(output, report["environment"], report, log),
                          args, source_git, paths, inputs)
        report["status"] = "離線建置與封包驗證完成，尚未實板驗證"
    except BaseException as exc:
        report["error"] = str(exc) if isinstance(exc, (BuildError, audit.AuditError, package.base.PackageError)) else (
            f"建置中止（類型={type(exc).__name__}，errno={getattr(exc, 'errno', None)}）"
        )
        raise
    finally:
        if (output / "build.log").is_file():
            report["artifacts"]["build.log"] = base.file_record(output / "build.log")
        with (output / "build-report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    try:
        output = build(args, [sys.executable, str(Path(__file__).resolve()), *arguments])
    except (BuildError, audit.AuditError, package.base.PackageError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"錯誤：檔案或路徑操作失敗（errno={exc.errno}，路徑={exc.filename}）", file=sys.stderr)
        return 1
    except (tarfile.TarError, audit.ELFError):
        print("錯誤：來源封存或 ELF 結構無效，拒絕交付", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("建置已中止；保留證據，不可原地重跑。", file=sys.stderr)
        return 130
    print(f"離線建置完成：{output / 'build-report.json'}；封包為 update.pkg，尚未實板驗證。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
