#!/usr/bin/env python3
"""將固定來源的 CM6 藍牙啟動元件封裝成可移除的 Debian 修正套件。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config/spacemit-k1-connectivity"
PACKAGE = "bpi-cm6-bluetooth"
DEFAULT_VERSION = "0.1.0~20260922rc2"


class ChineseArgumentParser(argparse.ArgumentParser):
    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數不完整或無法辨識，請使用 --help 檢查用法。\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def regular_child(root, name):
    relative = Path(name)
    path = root / relative
    if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
        raise ValueError("來源清單包含不安全路徑")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("來源清單檔案不存在或離開受控目錄")
    return path


def verify_build(root):
    record = json.loads(regular_child(root, "build-manifest.json").read_text())
    lock = json.loads((CONFIG / "source-lock.json").read_text())
    if record.get("status") != "complete" or record.get("board") != "bpi-cm6":
        raise ValueError("需要已完成的 CM6 藍牙來源建置")
    if record.get("source_lock_sha256") != digest(CONFIG / "source-lock.json"):
        raise ValueError("藍牙來源鎖與建置不符")
    if record.get("source") != lock["source"]:
        raise ValueError("藍牙建置來源不是固定的官方版本")
    for name, item in record["files"].items():
        path = regular_child(root, name)
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError("藍牙建置檔案校驗失敗：" + name)
    for name in ("bin/rtk_hciattach", "source.tar.gz", "source-lock.json"):
        if name not in record["files"]:
            raise ValueError("建置清單缺少必要元件：" + name)
    if (record["files"]["source.tar.gz"]["sha256"] != lock["source"]["sha256"] or
            record["files"]["source.tar.gz"]["bytes"] != lock["source"]["bytes"] or
            digest(root / "source-lock.json") != digest(CONFIG / "source-lock.json")):
        raise ValueError("保存的原始碼或來源鎖與官方固定內容不符")
    if record.get("patches") != lock["patches"]:
        raise ValueError("建置補丁與來源鎖不符")
    for patch in lock["patches"]:
        name = "patches/" + Path(patch["path"]).name
        if name not in record["files"] or digest(regular_child(root, name)) != patch["sha256"]:
            raise ValueError("建置缺少固定補丁")
        if digest(regular_child(root, "source/" + patch["target"])) != patch["after_sha256"]:
            raise ValueError("補丁套用後原始碼不符")
    sources = record.get("source_files_after_patch", {})
    if not sources or any("source/" + name not in record["files"] or
                          digest(regular_child(root, "source/" + name)) != sha for name, sha in sources.items()):
        raise ValueError("缺少可追溯的完整修改後原始碼")
    header = regular_child(root, "bin/rtk_hciattach").read_bytes()[:64]
    if (len(header) < 64 or header[:6] != b"\x7fELF\x02\x01" or
            struct.unpack_from("<H", header, 18)[0] != 243):
        raise ValueError("藍牙工具不是小端序 ELF64 RISC-V")
    return record


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def copy(source, root, relative, mode=0o644):
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


PREINST = """#!/bin/sh
set -eu
if [ -r /proc/device-tree/compatible ]; then
    tr '\\000' '\\n' < /proc/device-tree/compatible | grep -Fxq 'bananapi,bpi-cm6' || {
        printf '%s\\n' '此修正套件只適用 BPI-CM6，板型不符。' >&2
        exit 1
    }
elif ! grep -qx 'BOARD=bananapicm6' /etc/armbian-release 2>/dev/null; then
    printf '%s\\n' '無法確認 BPI-CM6 根系統，停止安裝。' >&2
    exit 1
fi
"""

POSTINST = """#!/bin/sh
set -eu
case "$1" in
configure|abort-upgrade|abort-deconfigure|abort-remove)
    deb-systemd-helper unmask bpi-cm6-bluetooth.service >/dev/null || true
    if deb-systemd-helper --quiet was-enabled bpi-cm6-bluetooth.service; then
        deb-systemd-helper enable bpi-cm6-bluetooth.service >/dev/null || true
    else
        deb-systemd-helper update-state bpi-cm6-bluetooth.service >/dev/null || true
    fi
    if [ -z "${DPKG_ROOT:-}" ] && [ -d /run/systemd/system ]; then
        systemctl --system daemon-reload >/dev/null || true
        action=start
        if [ -n "${2:-}" ]; then action=restart; fi
        if ! deb-systemd-invoke "$action" bpi-cm6-bluetooth.service; then
            printf '%s\\n' '套件已安裝，藍牙服務尚未成功啟動；請查看服務日誌。' >&2
        fi
    fi
    ;;
esac
"""

PRERM = """#!/bin/sh
set -eu
if [ "$1" = remove ] || [ "$1" = deconfigure ]; then
    if [ -z "${DPKG_ROOT:-}" ] && [ -d /run/systemd/system ]; then
        deb-systemd-invoke stop bpi-cm6-bluetooth.service || true
    fi
fi
"""

POSTRM = """#!/bin/sh
set -eu
if [ "$1" = purge ]; then
    deb-systemd-helper purge bpi-cm6-bluetooth.service >/dev/null || true
fi
if [ "$1" = remove ] && [ -z "${DPKG_ROOT:-}" ] && [ -d /run/systemd/system ]; then
    systemctl --system daemon-reload >/dev/null || true
fi
"""


def build(build_root, output, version=DEFAULT_VERSION):
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~\-]*", version):
        raise ValueError("套件版本格式不符")
    source_record = verify_build(build_root)
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "package-root"
    stage.mkdir()
    entries = (
        (build_root / "bin/rtk_hciattach", "usr/lib/bpi-cm6-bluetooth/rtk_hciattach", 0o755),
        (CONFIG / "bpi-cm6-bluetooth", "usr/sbin/bpi-cm6-bluetooth", 0o755),
        (CONFIG / "bpi-cm6-bluetooth.service", "usr/lib/systemd/system/bpi-cm6-bluetooth.service", 0o644),
        (CONFIG / "source-lock.json", "usr/share/bpi-cm6-bluetooth/source-lock.json", 0o644),
        (build_root / "source.tar.gz", "usr/share/doc/bpi-cm6-bluetooth/rtk_hciattach-source.tar.gz", 0o644),
        (build_root / "build-manifest.json", "usr/share/doc/bpi-cm6-bluetooth/build-manifest.json", 0o644),
    )
    for source, relative, mode in entries:
        copy(source, stage, relative, mode)
    for name in source_record["files"]:
        if name.startswith(("source/", "patches/")) or name in (
                "build.stdout", "build.stderr", "compiler.txt", "readelf.txt", "patch.stdout", "patch.stderr"):
            copy(regular_child(build_root, name), stage, "usr/share/doc/bpi-cm6-bluetooth/" + name)
    write(stage / "usr/share/doc/bpi-cm6-bluetooth/來源與驗證界線.txt",
          "本套件補上 BPI-CM6 的 Realtek UART 藍牙啟動程序。\n"
          "來源鎖、官方原始碼封存檔、本機補丁、修改後完整原始碼與交叉編譯紀錄隨套件保存。\n"
          "本機補丁使 UART 異常直接退出，由服務依裝置樹重新選擇正確的藍牙電源節點。\n"
          "第三方來源內的授權與著作權原文供法律追溯，原樣保留。\n"
          "安裝與服務啟動成功不能代替 HCI、掃描、配對及冷開機實測。\n"
          "移除套件可使用 sudo apt remove bpi-cm6-bluetooth。\n")
    installed_size = (sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) + 1023) // 1024
    write(stage / "DEBIAN/control", f"Package: {PACKAGE}\nVersion: {version}\nArchitecture: riscv64\n"
          "Maintainer: BPI-CM6 映像工具 <noreply@localhost>\nSection: admin\nPriority: optional\n"
          "Depends: libc6 (>= 2.38), bluez, python3 (>= 3.10), kmod, init-system-helpers\n"
          f"Installed-Size: {installed_size}\n"
          "Description: BPI-CM6 板載藍牙啟動修正候選\n"
          " 補上固定來源的 Realtek H5 工具與依裝置樹選址的啟動服務。\n"
          " 硬體連線功能仍需實機驗證。\n")
    for name, content in (("preinst", PREINST), ("postinst", POSTINST), ("prerm", PRERM), ("postrm", POSTRM)):
        write(stage / "DEBIAN" / name, content, 0o755)
        subprocess.run(["sh", "-n", str(stage / "DEBIAN" / name)], check=True)
    package = output / f"{PACKAGE}_{version}_riscv64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", "-Zxz", str(stage), str(package)], check=True)
    file_records = {str(p.relative_to(stage)): {"bytes": p.stat().st_size, "sha256": digest(p),
                    "mode": oct(p.stat().st_mode & 0o777)} for p in sorted(stage.rglob("*")) if p.is_file()}
    record = {"schema_version": 1, "board": "bpi-cm6", "status": "candidate_unverified",
              "package": PACKAGE, "version": version, "architecture": "riscv64",
              "artifact": package.name, "bytes": package.stat().st_size, "sha256": digest(package),
              "source_build_manifest_sha256": digest(build_root / "build-manifest.json"),
              "source": source_record["source"], "patches": source_record["patches"],
              "source_lock_sha256": source_record["source_lock_sha256"], "files": file_records,
              "bluetooth_hardware_validation": "pending", "ethernet_fix": "not_included"}
    write(output / "package-manifest.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    write(Path(str(package) + ".sha256"), record["sha256"] + "  " + package.name + "\n")
    return record


def main():
    parser = ChineseArgumentParser(description=__doc__, add_help=False, usage="%(prog)s --build-root 建置目錄 --output 新輸出目錄 [--version 版本]")
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明並結束")
    parser.add_argument("--build-root", type=Path, required=True, help="已校驗的來源建置目錄")
    parser.add_argument("--output", type=Path, required=True, help="尚未存在的輸出目錄")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Debian 套件版本")
    args = parser.parse_args()
    print(json.dumps(build(args.build_root, args.output, args.version), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
