#!/usr/bin/env python3
"""離線複製固定 A1 來源，重編 LBA2048 SPL 並保留其原配套 FIT；不部署。"""

import ctypes
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0,
                                UC_ARM64_REG_X1, UC_ARM64_REG_X4, UC_ARM64_REG_X30)

if __package__:
    from . import build_bpi_sram_boot as bridge
else:
    import build_bpi_sram_boot as bridge


ROOT = bridge.ROOT
A1_ROOT = ROOT.parent / "bpi-v26.2.1-m4zero-opi-ddr"
SOURCE = A1_ROOT / "cache/sources/u-boot-worktree/u-boot/v2026.01"
ARCHIVE = A1_ROOT / "output/evidence/bpi-m4zero-opi-ddr/A1-20260819-0845-candidate-6e05b3313"
COMMIT = "127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3"
MANIFEST_SHA = "c742450851f1ae034a51170e917b9937ce94c0c8551a497a81dd65733190f3ad"
COMBINED_SHA = "0b9333deac4a63353eb18442c9ef2f7ef269be1d7ef015cae3eee65f1b92a0cf"
SPL_SHA = "bb2a1cfcf9d0e3c953f3c86f36f9b48b560299e8172196129a703c941af8087e"
FIT_SHA = "7ebdbe800756c03a645aeafc9ba094e55df8af82a823a3e822ef120fef4be9a2"
COMPONENT_SHA = {
    "uboot": "584e64cf6a3ee64c8378cb8b66148c5d7d49c8055d878af8d8b4e7748be7dc35",
    "atf": "641bbce6b58d3e541e650946e7ce81c7c3cdb8e63244800457599c48947cb226",
    "fdt-1": "92e9f932fc4a3c17b4c8177408822473545da8a0b0781771a947c3e2ee6330cc",
}
SECTOR_KEY = "CONFIG_SYS_MMCSD_RAW_MODE_U_BOOT_SECTOR"
DATA_KEY = "CONFIG_SYS_MMCSD_RAW_MODE_U_BOOT_DATA_PART_OFFSET"
FIT_LBA, FIT_LIMIT, RAW_SECTOR = 2048, 0x100000, 0x7f0
EPOCH = "1786579200"
require, digest, base = bridge.require, bridge.digest, bridge.base


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def source_manifest(source):
    """包含工作樹補丁與未追蹤來源；不複製 Git 指標或舊編譯產物。"""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], env=env)
    require(git("rev-parse", "HEAD").decode().strip() == COMMIT, "A1 upstream 提交不符")
    names = {os.fsdecode(n) for n in git("ls-files", "-z", "--cached", "--others",
                                        "--exclude-standard").split(b"\0") if n}
    records = {}
    for name in sorted(names | {".config", ".scmversion"}):
        path = source / name
        require(not Path(name).is_absolute() and not {"..", ".git"} & set(Path(name).parts),
                "來源清單含不安全路徑")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            link = os.readlink(path)
            require(not Path(link).is_absolute() and path.resolve().is_relative_to(source),
                    "來源符號連結越界")
            records[name] = {"link": link}
        else:
            require(stat.S_ISREG(mode), "來源必須是一般檔案或內部符號連結")
            records[name] = {"sha256": digest(path.read_bytes()), "executable": bool(mode & 0o111)}
    require(digest(canonical(records)) == MANIFEST_SHA, "A1 完整來源清單 SHA 不符")
    return records


def copy_sources(source, target, records):
    target.mkdir()
    for name, record in records.items():
        if name == ".config" or "link" in record:
            continue
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, path)
        path.chmod(0o755 if record["executable"] else 0o644)
        require(digest(path.read_bytes()) == record["sha256"], "複製來源 SHA 不符")
    # 最後建立內部連結，避免複製時沿連結寫回原來源。
    for name, record in records.items():
        if "link" in record:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(record["link"])


def config_values(blob):
    return dict(line.split("=", 1) for line in blob.decode().splitlines() if line.startswith("CONFIG_"))


def absolute_lba(config, spl_size):
    require(config[DATA_KEY] == "0x10" and spl_size == 40960, "SPL 長度或 DATA_PART_OFFSET 不符")
    return max(int(config[SECTOR_KEY], 0), spl_size // 512) + int(config[DATA_KEY], 0)


class Fdt:
    """由系統 libfdt 讀取結構化屬性，不自行解析 DTB token。"""

    def __init__(self, blob):
        self.lib = ctypes.CDLL("libfdt.so.1")
        self.buffer = ctypes.create_string_buffer(blob)
        self.lib.fdt_check_header.argtypes = [ctypes.c_void_p]
        self.lib.fdt_path_offset.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.lib.fdt_getprop.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p,
                                        ctypes.POINTER(ctypes.c_int)]
        self.lib.fdt_getprop.restype = ctypes.c_void_p
        require(self.lib.fdt_check_header(self.buffer) == 0, "FIT／DTB 標頭不符")

    def prop(self, path, key):
        node = self.lib.fdt_path_offset(self.buffer, path.encode())
        require(node >= 0, "FIT／DTB 節點缺漏")
        size = ctypes.c_int()
        pointer = self.lib.fdt_getprop(self.buffer, node, key.encode(), ctypes.byref(size))
        require(pointer and size.value >= 0, "FIT／DTB 屬性缺漏")
        return ctypes.string_at(pointer, size.value)


def extract_fit(combined):
    require(len(combined) == 873977 and digest(combined) == COMBINED_SHA, "A1 歸檔長度或 SHA 不符")
    bridge.validate_spl(combined[:40960], SPL_SHA)
    fit = combined[40960:]
    require(len(fit) <= FIT_LIMIT and struct.unpack_from(">II", fit) == (0xd00dfeed, len(fit)),
            "配套 FIT 長度超界、截斷或含外部資料")
    require(digest(fit) == FIT_SHA, "A1 配套 FIT SHA 不符")
    tree = Fdt(fit)
    require(tree.prop("/configurations", "default") == b"config-1\0", "FIT 預設設定不符")
    for key, value in {"firmware": b"atf\0", "loadables": b"uboot\0", "fdt": b"fdt-1\0",
                       "description": b"allwinner/sun50i-h618-bananapi-m4-zero\0"}.items():
        require(tree.prop("/configurations/config-1", key) == value, "FIT 配套設定不符")
    components = {}
    for name, sha in COMPONENT_SHA.items():
        node = "/images/" + name
        data = tree.prop(node, "data")
        require(digest(data) == sha and tree.prop(node, "compression") == b"none\0", "FIT 組件不符")
        components[name] = data
    require(tree.prop("/images/uboot", "load") == struct.pack(">I", 0x4a000000) and
            tree.prop("/images/atf", "load") == tree.prop("/images/atf", "entry") ==
            struct.pack(">I", 0x40000000), "FIT 載入或跳轉位址不符")
    require(b"v2.12.9(debug):armbian" in components["atf"], "配套 TF-A 版本不符")
    return fit, components


def probe_first_read(elf_blob, spl, raw_override=None, boot_media=0):
    """執行實際 MMC 入口、max 與加法；只替代裝置初始化，在 mmc_bread 前停止。"""
    elf = ELFFile(BytesIO(elf_blob))
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0x20000, 0x40000)
    uc.mem_map(0x4ff80000, 0x1000)
    uc.mem_map(0x50000000, 0x10000)
    uc.mem_write(0x20000, spl)
    uc.mem_write(0x20028, bytes([boot_media]))
    uc.mem_write(0x50000100, struct.pack("<I", 1))
    uc.mem_write(0x50000320, struct.pack("<QI", 512, 9))
    # 固定來源的 legacy blk_desc.block_read；也涵蓋 blk_dread 被內聯的情況。
    uc.mem_write(0x50000388, struct.pack("<Q", symbols["mmc_bread"]))
    uc.reg_write(UC_ARM64_REG_X0, 0x50000000)
    uc.reg_write(UC_ARM64_REG_X1, 0x50000100)
    uc.reg_write(UC_ARM64_REG_SP, 0x5000fff0)
    stubs = {symbols[name]: value for name, value in {
        "mmc_initialize": 0, "find_mmc_device": 0x50000200, "mmc_init": 0,
        "spl_mmc_boot_mode": 1, "mmc_get_blk_desc": 0x50000300,
        "spl_get_load_buffer": 0x50001000,
    }.items()}
    observed = {}
    def instruction(machine, address, size, unused):
        require(0x20060 <= address < 0x2a000, "MMC 模型執行越界")
        if address == symbols["spl_mmc_load"]:
            if raw_override is not None:
                machine.reg_write(UC_ARM64_REG_X4, raw_override)
            observed["raw_sector"] = machine.reg_read(UC_ARM64_REG_X4)
        if address == symbols["board_spl_mmc_get_uboot_raw_sector"]:
            observed["board_return_pc"] = machine.reg_read(UC_ARM64_REG_X30)
        if address == observed.get("board_return_pc"):
            observed["after_max_sector"] = machine.reg_read(UC_ARM64_REG_X0)
        if address == symbols["mmc_bread"]:
            observed["absolute_lba"] = machine.reg_read(UC_ARM64_REG_X1)
            machine.emu_stop()
        elif address in stubs:
            machine.reg_write(UC_ARM64_REG_X0, stubs[address])
            machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_X30))
    uc.hook_add(UC_HOOK_CODE, instruction)
    uc.emu_start(symbols["spl_mmc_load_image"], 0, timeout=2_000_000, count=10000)
    require("absolute_lba" in observed, "MMC 模型未抵達第一次讀取")
    observed.pop("board_return_pc")
    return observed


def build(source, archive, output, jobs=4):
    source, archive, output = (bridge.plain_path(p) for p in (source, archive, output))
    require(not output.exists() and output.parent.is_dir(), "輸出必須是父目錄已存在的新目錄")
    require(not output.is_relative_to(source) and not output.is_relative_to(archive), "輸出不得位於原來源內")
    require(re.fullmatch(r"[A-Za-z0-9_./+-]+", str(output)), "make 輸出路徑含不支援字元")
    require(type(jobs) is int and 1 <= jobs <= 16, "工作數必須為 1 至 16")
    records = source_manifest(source)
    combined = base.read_regular_file(archive / "u-boot-sunxi-with-spl.bin", 2 * FIT_LIMIT)
    fit, components = extract_fit(combined)
    config = base.read_regular_file(source / ".config", FIT_LIMIT)
    require(config == base.read_regular_file(archive / "u-boot.config", FIT_LIMIT), "A1 存檔設定不符")
    require(components["atf"] == base.read_regular_file(archive / "bl31.bin", FIT_LIMIT), "配套 TF-A 歸檔不符")
    output.mkdir(mode=0o700)
    (output / "tmp").mkdir()
    (output / "inputs").mkdir()
    (output / "uboot-build").mkdir()
    base.write_new_regular_file(output / "source-manifest.json", canonical(records))
    base.write_new_regular_file(output / "inputs/original.config", config)
    base.write_new_regular_file(output / "inputs/build_bpi_sram_a1_fit.py", Path(__file__).read_bytes())
    base.write_new_regular_file(output / "a1-fit.itb", fit)
    base.write_new_regular_file(output / "a1-fit-region.bin", fit + bytes(FIT_LIMIT - len(fit)))
    for name, blob in components.items():
        base.write_new_regular_file(output / "inputs" / (name + ".bin"), blob)
    copy_sources(source, output / "source", records)
    base.write_new_regular_file(output / "uboot-build/.config", config)
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(output),
           "TMPDIR": str(output / "tmp"), "LC_ALL": "C", "TZ": "UTC",
           "ARCH": "arm", "CROSS_COMPILE": "aarch64-linux-gnu-",
           "SOURCE_DATE_EPOCH": EPOCH, "KBUILD_BUILD_USER": "bpi", "KBUILD_BUILD_HOST": "bpi",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
           "GIT_CEILING_DIRECTORIES": str(output), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
    report = {"status": "未通過", "hardware_validation": "未執行", "commands": [],
              "source": str(source), "archive": str(archive), "source_manifest_sha256": MANIFEST_SHA,
              "combined_sha256": COMBINED_SHA, "environment": env,
              "fit": {"bytes": len(fit), "sha256": digest(fit), "absolute_lba": FIT_LBA,
                      "region_bytes": FIT_LIMIT, "region_sha256": digest(fit + bytes(FIT_LIMIT - len(fit))),
                      "components": {n: {"bytes": len(d), "sha256": digest(d)} for n, d in components.items()}},
              "scope": "新 SPL 與原 A1 完整 FIT 配套；只讀固定 LBA2048，不實作多 FIT；保留原 FIT@96 與 rootfs，未部署"}
    def run(argv):
        result = subprocess.run(argv, cwd=output / "source", env=env, capture_output=True, timeout=600)
        log = f"command-{len(report['commands'])}.log"
        base.write_new_regular_file(output / log, result.stdout + result.stderr)
        report["commands"].append({"argv": argv, "returncode": result.returncode, "log": log})
        require(result.returncode == 0, f"建置命令失敗；詳見 {output / log}")
    try:
        run(["bash", "scripts/config", "--file", str(output / "uboot-build/.config"),
             "--set-val", SECTOR_KEY, hex(RAW_SECTOR)])
        make = ["make", f"O={output / 'uboot-build'}", f"-j{jobs}"]
        run([*make, "olddefconfig"])
        changed = config_values((output / "uboot-build/.config").read_bytes())
        original = config_values(config)
        delta = {k: [original.get(k), changed.get(k)] for k in original.keys() | changed.keys()
                 if original.get(k) != changed.get(k)}
        require(set(delta) <= {SECTOR_KEY, "CONFIG_GCC_VERSION"} and changed[SECTOR_KEY] == "0x7f0",
                "除了 sector 與編譯器版本外，設定另有變動")
        report["config_changes"] = delta
        run([*make, "spl/sunxi-spl.bin"])
        spl = base.read_regular_file(output / "uboot-build/spl/sunxi-spl.bin", 65536)
        report["spl"] = bridge.validate_spl(spl, digest(spl))
        require(absolute_lba(changed, len(spl)) == FIT_LBA, "max＋DATA_PART_OFFSET 不等於 LBA2048")
        report["mmc_model"] = probe_first_read((output / "uboot-build/spl/u-boot-spl").read_bytes(), spl)
        require(report["mmc_model"] == {"raw_sector": RAW_SECTOR, "after_max_sector": RAW_SECTOR,
                                        "absolute_lba": FIT_LBA}, "重編 SPL 實際 MMC 讀取 LBA 不符")
        bridge_report = bridge.build(output / "uboot-build/spl/sunxi-spl.bin", digest(spl), output / "bridge")
        report["bridge"] = bridge_report["artifacts"]
        report["compiler"] = subprocess.check_output(["aarch64-linux-gnu-gcc", "-dumpfullversion"], env=env, text=True).strip()
        require(source_manifest(source) == records and
                base.read_regular_file(archive / "u-boot-sunxi-with-spl.bin", 2 * FIT_LIMIT) == combined,
                "建置期間原來源或歸檔改變")
        report["originals_unchanged"] = True
        report["status"] = "配套候選建置與實際 SPL 首次讀取 LBA2048 模型通過；尚未實板驗證"
    finally:
        base.write_new_regular_file(output / "build-report.json",
                                    (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return report


def main(argv=None):
    parser = base.ChineseArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE, metavar="目錄", help="已固定 SHA 的 A1 工作樹")
    parser.add_argument("--archive", type=Path, default=ARCHIVE, metavar="目錄", help="A1 歸檔目錄")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄", help="禁止覆寫")
    parser.add_argument("--jobs", type=int, default=4, metavar="數量", help="編譯工作數，預設 4")
    args = parser.parse_args(argv)
    try:
        report = build(args.source, args.archive, args.output, args.jobs)
        print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
