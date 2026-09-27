#!/usr/bin/env python3
"""官方格式副本的媒體保護與同核心 initramfs 更新；不修改共用 prepared。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib


MESSAGE = "本映像使用官方格式，媒體重裝請使用隨附 SD 映像或 Titan eMMC 套件。"
BOOT_PACKAGES = ("linux-image-", "linux-dtb-", "linux-u-boot-", "armbian-bsp-")
FUNCTIONS = {"config.system.sh": ("module_partitioner",),
             "config.functions.sh": ("install_apply_partitions", "install_write_bootloader")}
INSTALLED_TOOL = "/usr/local/lib/bpi-k1-vendor-policy.py"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def writable(root, relative, leaf_link=False):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "媒體政策路徑不合法")
    path = root
    for index, part in enumerate(relative.parts):
        path /= part
        require((leaf_link and index == len(relative.parts) - 1) or not path.is_symlink(), "媒體政策路徑包含連結：" + str(relative))
    return path


def write(root, relative, text, mode=0o644):
    path = writable(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def guard_functions(content, names):
    for name in names:
        pattern = r"(?m)^(?:function\s+)?" + re.escape(name) + r"\s*\(\s*\)\s*\{"
        content, count = re.subn(pattern, lambda match: match.group(0) + '\n\tprintf "%s\\n" "' + MESSAGE + '" >&2\n\treturn 2\n', content)
        require(count == 1, "安裝引擎結構未知，拒絕未受保護的成品：" + name)
    return content


def divert(root, entry):
    target = writable(root, entry.lstrip("/"), leaf_link=True)
    backup = writable(root, entry.lstrip("/") + ".standard-format", leaf_link=True)
    require(not os.path.lexists(backup), "原生副本已有媒體 diversion，拒絕重複套用：" + entry)
    subprocess.run(["dpkg-divert", "--root=" + str(root), "--local", "--add", "--rename", "--divert", entry + ".standard-format", entry], check=True, capture_output=True)
    require(not os.path.lexists(target), "媒體 diversion 未移走原入口：" + entry)


def boot_file(boot, relative):
    path = boot / relative.lstrip("/")
    require(path.resolve().is_relative_to(boot.resolve()) and path.is_file(), "開機元件不存在或越界：" + relative)
    return path


def boot_record(boot, kernel, dtb_path):
    require(re.fullmatch(r"[A-Za-z0-9_.+-]+", kernel) is not None, "核心版本不合法")
    source = boot_file(boot, "vmlinuz-" + kernel)
    return {"kernel_release": kernel, "vmlinuz_path": "/" + source.relative_to(boot).as_posix(),
            "vmlinuz_sha256": digest(source), "image_sha256": digest(boot_file(boot, "Image")),
            "dtb_path": dtb_path, "dtb_sha256": digest(boot_file(boot, dtb_path)),
            "initial_uinitrd_sha256": digest(boot_file(boot, "uInitrd"))}


def apply(root, marker, boot):
    root = Path(root).resolve()
    require(root != Path("/") and (root / "var/lib/dpkg/status").is_file(), "媒體政策只能套用獨立的已安裝根系統")
    require(marker.get("source_kind") == "armbian-native-rootfs" and marker.get("storage") in ("sd", "emmc"), "媒體政策缺少原生媒體身分")
    changes = {}
    # 先核對全部已知入口，未知布局在建立任何 diversion 前停止。
    for prefix in ("usr/lib/armbian-config", "usr/lib/bpi-k1-configng/lib/armbian-config"):
        if prefix.startswith("usr/lib/bpi-k1-") and not (root / prefix).exists():
            continue
        for filename, functions in FUNCTIONS.items():
            relative = prefix + "/" + filename
            path = writable(root, relative)
            require(path.is_file(), "找不到須保護的安裝入口：" + relative)
            changes[relative] = (guard_functions(path.read_text(), functions), path.stat().st_mode & 0o777)
    platform = writable(root, "usr/lib/u-boot/platform_install.sh")
    require(platform.is_file() and re.search(r"(?m)^(?:function\s+)?write_uboot_platform\s*\(\s*\)\s*\{", platform.read_text()), "未知的 U-Boot 平台安裝布局")
    initrd = writable(root, "etc/initramfs/post-update.d/99-uboot")
    require(initrd.is_file() and "mkimage " in initrd.read_text() and 'tempname="/boot/uInitrd-$1"' in initrd.read_text(), "未知的 initramfs 更新布局")
    records = {}
    for relative, (content, mode) in changes.items():
        records[relative] = {"original_sha256": digest(root / relative)}
        divert(root, "/" + relative)
        write(root, relative, content, mode)
        subprocess.run(["bash", "-n", str(root / relative)], check=True, capture_output=True)
        records[relative]["policy_sha256"] = digest(root / relative)
    guard = '#!/bin/sh\nprintf "%s\\n" "' + MESSAGE + '" >&2\nexit 2\n'
    for relative in ("usr/bin/armbian-install", "usr/sbin/armbian-install"):
        divert(root, "/" + relative)
        write(root, relative, guard, 0o755)
    divert(root, "/usr/lib/u-boot/platform_install.sh")
    write(root, "usr/lib/u-boot/platform_install.sh", 'write_uboot_platform() {\n  printf "%s\\n" "' + MESSAGE + '" >&2\n  return 2\n}\n')
    for relative, command in (("etc/initramfs/post-update.d/99-uboot", "initramfs"),
                              ("etc/kernel/preinst.d/00-bpi-k1-vendor", "kernel-guard"),
                              ("etc/kernel/postinst.d/zz-bpi-k1-vendor", "check-boot")):
        if relative.endswith("99-uboot"):
            divert(root, "/" + relative)
        write(root, relative, '#!/bin/sh\nexec /usr/bin/python3 ' + INSTALLED_TOOL + ' ' + command + ' "$@"\n', 0o755)
    write(root, "usr/local/sbin/bpi-k1-apt-guard", '#!/bin/sh\nexec /usr/bin/python3 ' + INSTALLED_TOOL + ' apt-guard\n', 0o755)
    write(root, "etc/apt/apt.conf.d/99-bpi-k1-vendor-boot", 'DPkg::Pre-Install-Pkgs {"/usr/local/sbin/bpi-k1-apt-guard";};\nDPkg::Tools::Options::/usr/local/sbin/bpi-k1-apt-guard::Version "1";\n')
    write(root, "etc/apt/preferences.d/bpi-k1-vendor-boot", "# 官方格式的開機配套與媒體安裝引擎須整組驗證後更新。\nPackage: linux-image-* linux-dtb-* linux-u-boot-* armbian-bsp-* armbian-config\nPin: version *\nPin-Priority: -1\n")
    write(root, "root/.no_rootfs_resize", "")
    service = writable(root, "etc/systemd/system/armbian-resize-filesystem.service", leaf_link=True)
    service.parent.mkdir(parents=True, exist_ok=True)
    service.unlink(missing_ok=True)
    service.symlink_to("/dev/null")
    destination = writable(root, INSTALLED_TOOL.lstrip("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(__file__, destination)
    destination.chmod(0o755)
    return {"schema_version": 1, "source_sha256": digest(Path(__file__)), "protected_config": records,
            "update_policy": "拒絕未驗證的整套開機套件更新；允許同核心、同 DTB 的受控 initramfs 重建。",
            "validation": "候選離線政策；受控整套升級與回復仍待實作及驗收。"}


def read_marker(root):
    return json.loads((root / "etc/bpi-k1-vendor.json").read_text())


def check_boot(root, version=None):
    marker = read_marker(root)
    record = marker["native_boot"]
    require(marker.get("source_kind") == "armbian-native-rootfs", "缺少原生媒體身分")
    if version is not None:
        require(version == record["kernel_release"], "核心版本改變，須重新驗證整套映像")
    boot = root / "boot"
    for name, expected in ((record["vmlinuz_path"], record["vmlinuz_sha256"]),
                           ("Image", record["image_sha256"]), (record["dtb_path"], record["dtb_sha256"])):
        require(digest(boot_file(boot, name)) == expected, "核心或 DTB 已改變，停止更新開機入口：" + name)
    return marker


def check_boot_mount(marker):
    result = subprocess.check_output(["findmnt", "--json", "--target", "/boot", "--output", "TARGET,FSTYPE,UUID"], text=True)
    rows = json.loads(result)["filesystems"]
    require(len(rows) == 1 and rows[0].get("target") == "/boot" and rows[0].get("fstype") == "ext4"
            and rows[0].get("uuid") == marker["boot_uuid"], "獨立 bootfs 尚未按指定 UUID 掛載")


def verify_uinitrd(path):
    with path.open("rb") as stream:
        header = bytearray(stream.read(64))
        require(len(header) == 64, "uInitrd 標頭過短")
        magic, header_crc, _, size, _, _, data_crc, system, arch, kind, compression = struct.unpack_from(">7I4B", header)
        require(magic == 0x27051956 and (system, arch, kind, compression) == (5, 26, 3, 1), "uInitrd 格式不符 RISC-V 契約")
        header[4:8] = b"\0" * 4
        require(zlib.crc32(header) == header_crc, "uInitrd 標頭 CRC 不符")
        actual, count = 0, 0
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            actual = zlib.crc32(block, actual)
            count += len(block)
        require(count == size and actual == data_crc and size > 0, "uInitrd 資料長度或 CRC 不符")


def update_initramfs(root, version, source):
    marker = check_boot(root, version)
    check_boot_mount(marker)
    boot = root / "boot"
    source = Path(source)
    require(source.resolve().is_relative_to(boot.resolve()) and source.name == "initrd.img-" + version and source.is_file(), "initramfs 來源位置或版本不符")
    with tempfile.TemporaryDirectory(prefix=".bpi-k1-initrd-", dir=boot) as temporary:
        candidate = Path(temporary) / "uInitrd"
        subprocess.run(["mkimage", "-A", "riscv", "-O", "linux", "-T", "ramdisk", "-C", "gzip", "-n", "uInitrd", "-d", str(source), str(candidate)], check=True, capture_output=True)
        verify_uinitrd(candidate)
        check_boot(root, version)
        # 驗證全部通過後，才原子替換啟動使用的 ramdisk。
        candidate.chmod(0o644)
        os.replace(candidate, boot / ("uInitrd-" + version))
        link = Path(temporary) / "link"
        link.symlink_to("uInitrd-" + version)
        os.replace(link, boot / "uInitrd")


def apt_guard(paths):
    for value in paths:
        path = value.strip()
        if not path:
            continue
        require(Path(path).is_absolute() and Path(path).is_file(), "APT 套件清單格式不符")
        name = subprocess.check_output(["dpkg-deb", "--field", path, "Package"], text=True).strip()
        require(not name.startswith(BOOT_PACKAGES) and name != "armbian-config", "開機套件或媒體安裝引擎須整套驗證後重新發布，停止安裝：" + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apt-guard", "kernel-guard", "check-boot", "initramfs"))
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    try:
        if args.command == "apt-guard":
            apt_guard(sys.stdin)
        elif args.command == "kernel-guard":
            raise ValueError("官方格式的核心須整套驗證後重新發布，停止獨立安裝")
        elif args.command == "check-boot":
            check_boot(Path("/"), args.arguments[0] if args.arguments else None)
        else:
            require(len(args.arguments) == 2, "initramfs 更新需要核心版本與來源檔")
            update_initramfs(Path("/"), *args.arguments)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print("官方格式媒體保護已停止操作：" + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
