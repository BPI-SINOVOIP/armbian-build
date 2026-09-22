#!/usr/bin/env python3
"""從固定官方來源交叉編譯 CM6 藍牙工具，保留原始碼及離線建置證據。"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/spacemit-k1-connectivity/source-lock.json"
SOURCES = ("hciattach.c", "hciattach_rtk.c", "hciattach_h4.c", "rtb_fwc.c")
SOURCE_FILES = set(SOURCES) | {"Makefile", "hciattach.h", "hciattach_h4.h", "rtb_fwc.h"}
PATCH_TARGETS = {
    "0001-uart-failure-defer-to-service.patch": "hciattach.c",
    "0002-cm6-private-firmware.patch": "rtb_fwc.c",
}
FIRMWARE_NAMES = {"firmware": "rtl8852bs_fw", "firmware_config": "rtl8852bs_config"}
PRIVATE_FIRMWARE_DIRECTORY = "/usr/lib/bpi-cm6-bluetooth/firmware/"


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, **kwargs):
        super().__init__(add_help=False, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示此說明後結束")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數不完整或無法辨識，請使用 --help 檢查用法。\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_archive(data, source):
    require(len(data) == source["bytes"] and sha(data) == source["sha256"], "官方來源壓縮檔大小或 SHA-256 不符")
    prefix = "rtk_hciattach-" + source["commit"]
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and path.parts[0] == prefix,
                    "來源壓縮檔含不安全路徑")
            if member.isdir():
                require(path.as_posix() == prefix, "來源壓縮檔含非預期目錄")
                continue
            require(member.isfile() and len(path.parts) == 2 and member.size <= 1024 * 1024,
                    "來源壓縮檔含非預期檔案或連結")
            name = path.name
            require(name in SOURCE_FILES and name not in files, "來源壓縮檔含未知或重複檔案")
            files[name] = archive.extractfile(member).read()
    require(set(files) == SOURCE_FILES, "官方來源缺少必要編譯檔案")
    return files


def validate_elf(data):
    require(len(data) >= 64 and data[:4] == b"\x7fELF", "產物不是完整 ELF")
    require(data[4:6] == b"\x02\x01", "產物必須為 64 位元小端序 ELF")
    require(struct.unpack_from("<H", data, 18)[0] == 243, "產物不是 RISC-V ELF")
    flags = struct.unpack_from("<I", data, 48)[0]
    require(flags & 6 == 4, "產物不是 RISC-V 雙精度浮點 ABI")
    return {"class": "ELF64", "endianness": "little", "machine": "RISC-V", "abi": "lp64d", "flags": flags}


def validate_patches(record, patch_root):
    patches = record.get("patches", [])
    require([item.get("path") for item in patches] == list(PATCH_TARGETS),
            "必須提供依固定順序排列的 UART 恢復與 CM6 私有韌體補丁")
    result = []
    for item in patches:
        name = item["path"]
        require(Path(name).name == name and name.endswith(".patch"), "本機補丁路徑不合法")
        require(item["target"] == PATCH_TARGETS[name], "補丁目標不符受控範圍")
        path = patch_root / name
        require(not path.is_symlink(), "本機補丁不得為符號連結")
        data = path.read_bytes()
        require(sha(data) == item["sha256"], "本機補丁 SHA-256 不符：" + name)
        result.append((item, data))
    return result


def apply_patches(source_dir, output, patches):
    patch_dir = output / "patches"
    patch_dir.mkdir()
    for item, data in patches:
        target = source_dir / item["target"]
        untouched = {name: sha((source_dir / name).read_bytes())
                     for name in SOURCE_FILES if name != item["target"]}
        require(sha(target.read_bytes()) == item["before_sha256"], "套用補丁前的官方來源不符")
        path = patch_dir / item["path"]
        path.write_bytes(data)
        result = subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch",
                                 "-p1", "-d", str(source_dir), "--input", str(path)],
                                capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
                                env={**os.environ, "LC_ALL": "C"})
        path.with_name(path.name + ".stdout").write_bytes(result.stdout)
        path.with_name(path.name + ".stderr").write_bytes(result.stderr)
        require(result.returncode == 0, "本機補丁套用失敗，請查看個別補丁的 stderr")
        require(sha(target.read_bytes()) == item["after_sha256"], "套用補丁後的來源 SHA-256 不符")
        require(all(sha((source_dir / name).read_bytes()) == digest for name, digest in untouched.items()),
                "補丁改變了指定檔案以外的來源")
    text = (source_dir / "hciattach.c").read_text()
    require(all(value not in text for value in ("RFKILL_NODE", "reset_bluetooth", "/sys/class/rfkill/", "goto start;")),
            "藍牙 helper 仍含繞過選址的內部電源恢復流程")
    firmware_text = (source_dir / "rtb_fwc.c").read_text()
    for name in ("FIRMWARE_DIRECTORY", "BT_CONFIG_DIRECTORY"):
        require(f'#define {name}\t"{PRIVATE_FIRMWARE_DIRECTORY}"' in firmware_text,
                "CM6 helper 未使用固定私有韌體目錄")


def validate_firmware_archive(data, record):
    """僅讀取固定 tar 成員，不解開路徑、不接受所選檔案的連結。"""
    source = record["firmware_source"]
    require(len(data) == source["bytes"] and sha(data) == source["sha256"],
            "官方韌體來源壓縮檔大小或 SHA-256 不符")
    selected = {}
    for field, name in FIRMWARE_NAMES.items():
        item = record["hardware_contract"][field]
        require(item["path"] == PRIVATE_FIRMWARE_DIRECTORY + name, "韌體安裝路徑不是固定 CM6 私有目錄")
        member = source["selected_paths"][field]
        require(member == "spacemit-uart-bt/lib/firmware/rtlbt/" + name,
                "韌體來源成員路徑不符受控範圍")
        selected[member] = (name, item)
    copyright_record = source["copyright"]
    require(copyright_record["path"] == "spacemit-uart-bt/debian/copyright", "原授權追溯檔路徑不符")
    selected[copyright_record["path"]] = ("copyright", copyright_record)
    files = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
        for index, member in enumerate(archive, 1):
            total += member.size
            require(index <= 4096 and 0 <= member.size <= 64 * 1024 * 1024
                    and total <= 128 * 1024 * 1024, "韌體來源成員數量或解壓大小超出上限")
            if member.name not in selected:
                continue
            name, expected = selected[member.name]
            require(name not in files, "韌體來源含重複的指定成員")
            require(member.isfile(), "指定韌體來源成員必須是一般檔案，不能是連結")
            require(member.size == expected["bytes"] and member.size <= 1024 * 1024,
                    "指定韌體來源成員大小不符")
            content = archive.extractfile(member).read(expected["bytes"] + 1)
            require(len(content) == expected["bytes"] and sha(content) == expected["sha256"],
                    "指定韌體來源成員大小或 SHA-256 不符")
            files[name] = content
    require(set(files) == {*FIRMWARE_NAMES.values(), "copyright"}, "韌體來源缺少指定檔案或原授權追溯檔")
    return files


def load_archive(path, source):
    if path is None:
        with urllib.request.urlopen(source["url"], timeout=30) as response:
            return response.read(source["bytes"] + 1)
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "來源壓縮檔必須是一般檔案，不能是連結或裝置")
    require(path.stat().st_size == source["bytes"], "來源壓縮檔大小不符")
    with path.open("rb") as stream:
        return stream.read(source["bytes"] + 1)


def capture(argv, timeout=30):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, timeout=timeout, check=False,
                            env={**os.environ, "LC_ALL": "C", "SOURCE_DATE_EPOCH": "1713148266"})
    require(result.returncode == 0, f"命令失敗：{argv[0]}，退出碼 {result.returncode}；{result.stderr.decode(errors='replace')}")
    return result.stdout.decode()


def build(output, source_archive=None, firmware_archive=None):
    lock_data = LOCK.read_bytes()
    record = json.loads(lock_data)
    source = record["source"]
    expected = record["compiler"]
    compiler_path = shutil.which(expected["command"])
    require(compiler_path is not None, "缺少受控 RISC-V 交叉編譯器")
    compiler = Path(compiler_path).resolve()
    compiler_sha = sha(compiler.read_bytes())
    require(compiler_sha == expected["sha256"], "交叉編譯器 SHA-256 與來源鎖不符")
    version = capture([str(compiler), "-dumpfullversion"]).strip()
    target = capture([str(compiler), "-dumpmachine"]).strip()
    require(version == expected["version"] and target == expected["target"], "交叉編譯器版本或目標不符")
    version_text = capture([str(compiler), "--version"])
    require(not output.exists(), "輸出目錄已存在，請使用新的建置目錄")
    data = load_archive(source_archive, source)
    files = validate_archive(data, source)
    patches = validate_patches(record, LOCK.parent)
    firmware_data = load_archive(firmware_archive, record["firmware_source"])
    firmware_files = validate_firmware_archive(firmware_data, record)
    output.mkdir(parents=True)
    (output / "source.tar.gz").write_bytes(data)
    (output / "firmware-source.tar.xz").write_bytes(firmware_data)
    firmware_dir = output / "firmware"
    firmware_dir.mkdir()
    for name, content in firmware_files.items():
        (firmware_dir / name).write_bytes(content)
    source_dir = output / "source"
    source_dir.mkdir()
    for name, content in sorted(files.items()):
        (source_dir / name).write_bytes(content)
    apply_patches(source_dir, output, patches)
    (output / "source-lock.json").write_bytes(lock_data)
    binary_dir = output / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "rtk_hciattach"
    argv = [str(compiler), "-std=gnu11", "-O2", "-Wall", "-march=rv64gc", "-mabi=lp64d",
            "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-fPIE", "-pie",
            "-Wl,-z,relro,-z,now", f"-ffile-prefix-map={source_dir}=/usr/src/bpi-cm6-bluetooth",
            *[str(source_dir / name) for name in SOURCES], "-o", str(binary)]
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, timeout=120,
                            env={**os.environ, "LC_ALL": "C", "SOURCE_DATE_EPOCH": "1713148266"})
    (output / "build.stdout").write_bytes(result.stdout)
    (output / "build.stderr").write_bytes(result.stderr)
    require(result.returncode == 0, f"交叉編譯失敗，請讀取 {output / 'build.stderr'}")
    elf = validate_elf(binary.read_bytes())
    readelf = shutil.which("riscv64-linux-gnu-readelf")
    require(readelf is not None, "缺少 RISC-V ELF 檢查工具")
    elf_text = capture([readelf, "-h", "-l", "-d", "-V", str(binary)])
    require("/lib/ld-linux-riscv64-lp64d.so.1" in elf_text, "ELF 動態載入器不是 Noble RISC-V ABI")
    (output / "readelf.txt").write_text(elf_text)
    (output / "compiler.txt").write_text(version_text)
    evidence = {}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            evidence[path.relative_to(output).as_posix()] = {"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}
    manifest = {"schema_version": 1, "status": "complete", "scope": "source-cross-compile-elf",
                "board": "bpi-cm6", "hardware_validation": "pending", "source": source,
                "source_lock_sha256": sha(lock_data),
                "firmware_source": record["firmware_source"],
                "patches": record["patches"],
                "source_files_after_patch": {name: sha((source_dir / name).read_bytes()) for name in sorted(SOURCE_FILES)},
                "compiler": {"path": str(compiler), "sha256": compiler_sha, "version": version, "target": target},
                "readelf": {"path": readelf, "sha256": sha(Path(readelf).read_bytes())},
                "command": argv, "elf": elf, "files": evidence,
                "limitation": "只證明固定來源交叉編譯、ELF 格式與所選韌體完整性；未執行 UART、下載板上韌體或證明藍牙可用。"}
    (output / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = ChineseArgumentParser(description="編譯並留存 CM6 專用藍牙工具的來源與 ABI 證據")
    parser.add_argument("--output", type=Path, required=True, metavar="目錄", help="必須尚不存在的獨立輸出目錄")
    parser.add_argument("--source-archive", type=Path, metavar="來源壓縮檔", help="可選的既有官方壓縮檔，仍須通過固定 SHA-256")
    parser.add_argument("--firmware-archive", type=Path, metavar="韌體來源壓縮檔",
                        help="可選的官方韌體來源壓縮檔；未提供時下載已鎖定版本，仍核對大小與 SHA-256")
    args = parser.parse_args()
    try:
        manifest = build(args.output.resolve(), args.source_archive, args.firmware_archive)
        print(json.dumps({"status": manifest["status"], "output": str(args.output),
                          "binary_sha256": manifest["files"]["bin/rtk_hciattach"]["sha256"],
                          "hardware_validation": "pending"}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"CM6 藍牙建置失敗：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
