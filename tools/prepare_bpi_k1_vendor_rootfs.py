#!/usr/bin/env python3
"""從既有 Armbian 建立隔離的 Noble 加速根檔案系統；不寫入實體媒體。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import bpi_k1_acceleration as acceleration
BOARDS = {"bpi-cm6": ("bananapicm6", "6.6.36-legacy-spacemit"),
          "bpi-f3": ("bananapif3", "6.18.37-current-spacemit")}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(argv, *, codes=(0,), **kwargs):
    print("執行：" + " ".join(map(str, argv)), flush=True)
    result = subprocess.run(list(map(str, argv)), **kwargs)
    if result.returncode not in codes:
        raise RuntimeError(f"命令失敗：{argv[0]}，退出碼 {result.returncode}")
    return result


def regular(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"只接受一般檔案：{path}")
    return path.resolve()


def partition(path):
    obj = json.loads(subprocess.check_output(["sfdisk", "--json", str(path)]))
    table = obj["partitiontable"]
    parts = table["partitions"]
    if (table["label"] != "dos" or table.get("unit") != "sectors" or
            table.get("sectorsize") != 512 or len(parts) != 1 or parts[0]["start"] != 8192):
        raise ValueError("來源不是受控的 Armbian 單根分區 MBR 映像")
    p = parts[0]
    if p["type"] != "83" or p["size"] <= 0 or (p["start"] + p["size"]) * 512 > path.stat().st_size:
        raise ValueError("來源分區種類或界線不符")
    return p


def copy_partition(source, destination, start, count):
    with source.open("rb") as src, destination.open("xb") as dst:
        src.seek(start)
        left = count
        while left:
            data = src.read(min(left, 8 * 1024 * 1024))
            if not data:
                raise ValueError("來源映像過短")
            dst.write(data)
            left -= len(data)


def package_selection(lock, board, cache):
    selected = []
    for key in lock["profiles"][board]["packages"]:
        item = lock["packages"][key]
        name = Path(item["Filename"]).name
        path = regular(cache / name)
        if path.stat().st_size != int(item["Size"]) or sha256(path) != item["SHA256"]:
            raise ValueError(f"加速套件雜湊或容量不符：{name}")
        selected.append(path)
    return selected


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def protect_media_tools(mount, chroot):
    """保護實際安裝引擎及套件更新入口；不只攔截相容命令名稱。"""
    message = "本映像使用官方格式，媒體重裝請使用隨附 SD 映像或 Titan eMMC 套件。"
    for entry, functions in (("/usr/lib/armbian-config/config.system.sh", ("module_partitioner",)),
                              ("/usr/lib/armbian-config/config.functions.sh", ("install_apply_partitions", "install_write_bootloader"))):
        path = mount / entry.lstrip("/")
        if not path.exists():
            raise ValueError("找不到須保護的安裝入口：" + entry)
        backup = path.with_name(path.name + ".standard-format")
        if not backup.exists():
            run([*chroot, "dpkg-divert", "--local", "--add", "--rename", "--divert", entry + ".standard-format", entry])
        content = backup.read_text()
        for name in functions:
            pattern = r"(?m)^(" + re.escape(name) + r"\s*\(\s*\)\s*\{)"
            content, count = re.subn(pattern, lambda m: m.group(0) + '\n\tprintf "%s\\n" "' + message + '" >&2\n\treturn 2\n', content)
            if count != 1:
                raise ValueError("安裝引擎結構已變更，拒絕未受保護的成品：" + name)
        write(path, content, backup.stat().st_mode & 0o777)
        run(["bash", "-n", path])
    entry = "/usr/lib/u-boot/platform_install.sh"
    path = mount / entry.lstrip("/")
    if path.exists() and not path.with_name(path.name + ".standard-format").exists():
        run([*chroot, "dpkg-divert", "--local", "--add", "--rename", "--divert", entry + ".standard-format", entry])
    write(path, '# 官方格式的原始磁區配置不可交由標準更新路徑覆寫。\nwrite_uboot_platform() {\n  printf "%s\\n" "' + message + '" >&2\n  return 2\n}\n')
    write(mount / "root/.no_rootfs_resize", "")
    service = mount / "etc/systemd/system/armbian-resize-filesystem.service"
    service.unlink(missing_ok=True)
    service.symlink_to("/dev/null")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--board", required=True, choices=BOARDS)
    p.add_argument("--image", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--deb-cache", required=True, type=Path)
    p.add_argument("--lock", type=Path, default=REPO / "config/spacemit-k1-acceleration/noble.lock.json")
    p.add_argument("--resume", action="store_true", help="僅續作相同來源的未完成候選")
    p.add_argument("--refresh-packages", action="store_true", help="搭配續作，明確更新同來源候選的固定套件配套")
    p.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("需要管理員權限建立隔離掛載；請使用 sudo")
    if not args.inside:
        os.execvp("unshare", ["unshare", "--mount", "--propagation", "private",
                             sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--inside"])
    source = regular(args.image)
    lock_path = regular(args.lock)
    lock_bytes = lock_path.read_bytes()
    lock = acceleration.load_lock(lock_path)
    packages = package_selection(lock, args.board, args.deb_cache.resolve())
    out = args.output.absolute()
    if out.is_symlink() or out == source.parent or out == Path("/"):
        raise ValueError("輸出必須是專用工作目錄")
    identity = {"board": args.board, "source_sha256": sha256(source),
                "acceleration_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(), "schema_version": 1}
    marker = out / "preparation.json"
    if out.exists():
        if not args.resume or not marker.exists():
            raise ValueError("輸出已存在；僅可明確續作原本的工作")
        prior = json.loads(marker.read_text())
        compare = dict(prior["identity"])
        if args.refresh_packages:
            compare["acceleration_lock_sha256"] = identity["acceleration_lock_sha256"]
        if compare != identity:
            raise ValueError("續作來源或套件鎖已變更")
        if prior["status"] == "complete" and not args.refresh_packages:
            raise ValueError("此候選已完成；請直接打包，避免重複建置")
        history = out / "history"
        history.mkdir(exist_ok=True)
        shutil.copyfile(marker, history / (hashlib.sha256(marker.read_bytes()).hexdigest() + ".json"))
    else:
        out.mkdir(parents=True)
    source_verification = acceleration.verify_sources(lock, lock_path, args.deb_cache.resolve(), lock["profiles"][args.board]["packages"])
    if lock_path.read_bytes() != lock_bytes:
        raise ValueError("套件鎖在驗證期間變更，請重試")
    state = {"identity": identity, "status": "preparing", "hardware_validation": "pending",
             "verified_sources": source_verification}
    write(marker, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    rootfs = out / "rootfs.ext4"
    if not rootfs.exists():
        part = partition(source)
        copy_partition(source, rootfs, part["start"] * 512, part["size"] * 512)
        with rootfs.open("r+b") as f:
            f.truncate(max(rootfs.stat().st_size, 6 * 1024**3))
        run(["e2fsck", "-pf", rootfs], codes=(0, 1))
        run(["resize2fs", rootfs])
    else:
        regular(rootfs)
        run(["e2fsck", "-pf", rootfs], codes=(0, 1))
    mount = out / "mount"
    mount.mkdir(exist_ok=True)
    attached = []
    policy = None
    policy_installed = False
    resolv_saved = None
    try:
        run(["mount", "-o", "loop,nodev,nosuid", rootfs, mount])
        attached.append(mount)
        armbian = (mount / "etc/armbian-release").read_text()
        if f"BOARD={BOARDS[args.board][0]}\n" not in armbian:
            raise ValueError("根檔案系統板型不符")
        osrelease = (mount / "usr/lib/os-release").read_text()
        if 'VERSION_CODENAME=noble' not in osrelease or 'ID=ubuntu' not in osrelease:
            raise ValueError("來源不是 Ubuntu Noble")
        if not (mount / "boot" / ("vmlinuz-" + BOARDS[args.board][1])).is_file():
            raise ValueError("根檔案系統核心版本不符")
        for name in ("proc", "run", "dev"):
            (mount / name).mkdir(exist_ok=True)
        run(["mount", "-t", "proc", "-o", "nosuid,nodev,noexec", "proc", mount / "proc"])
        attached.append(mount / "proc")
        run(["mount", "-t", "tmpfs", "-o", "nosuid,nodev", "tmpfs", mount / "run"])
        attached.append(mount / "run")
        run(["mount", "-t", "tmpfs", "-o", "nosuid", "tmpfs", mount / "dev"])
        attached.append(mount / "dev")
        for name in ("null", "zero", "random", "urandom"):
            target = mount / "dev" / name
            target.touch()
            run(["mount", "--bind", Path("/dev") / name, target])
            attached.append(target)
        for name, target in (("fd", "/proc/self/fd"), ("stdin", "/proc/self/fd/0"),
                             ("stdout", "/proc/self/fd/1"), ("stderr", "/proc/self/fd/2")):
            (mount / "dev" / name).symlink_to(target)
        policy_path = mount / "usr/sbin/policy-rc.d"
        if policy_path.exists():
            policy = policy_path.read_bytes(), policy_path.stat().st_mode & 0o777
        write(policy_path, "#!/bin/sh\n# 建置期間不啟動服務。\nexit 101\n", 0o755)
        policy_installed = True
        resolv = mount / "etc/resolv.conf"
        if not (out / "resolv-original.json").exists():
            # 來源檔案系統維持唯讀，從原映像抽取 DNS 設定，亦支援舊候選刷新。
            original_mount = out / "source-mount"
            original_mount.mkdir(exist_ok=True)
            part = partition(source)
            run(["mount", "-o", f"loop,ro,noload,offset={part['start']*512},sizelimit={part['size']*512}", source, original_mount])
            try:
                original = original_mount / "etc/resolv.conf"
                saved = {"link": str(original.readlink())} if original.is_symlink() else {"text": original.read_text(), "mode": original.stat().st_mode & 0o777}
                write(out / "resolv-original.json", json.dumps(saved, ensure_ascii=False))
            finally:
                run(["umount", original_mount])
        resolv_saved = json.loads((out / "resolv-original.json").read_text())
        if resolv.is_symlink():
            resolv.unlink()
        shutil.copyfile("/etc/resolv.conf", resolv)
        # 建置期間僅使用 Ubuntu 來源解決一般相依，供應商元件取自固定本機套件。
        sources = mount / "etc/apt/sources.list.d"
        for existing in sources.glob("*armbian*"):
            if existing.suffix in (".list", ".sources"):
                existing.rename(existing.with_name(existing.name + ".vendor-build-disabled"))
        debdest = mount / "var/tmp/bpi-k1-debs"
        debdest.mkdir(parents=True, exist_ok=True)
        for package in packages:
            target = debdest / package.name
            if not target.exists() or sha256(target) != sha256(package):
                shutil.copyfile(package, target)
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C.UTF-8")
        chroot = ["chroot", mount, "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive"]
        run([*chroot, "apt-get", "update"], env=env)
        desktop = ["gnome-session", "gnome-shell", "gdm3", "gnome-terminal", "nautilus",
                   "mesa-utils", "vulkan-tools", "python3-numpy", "python3-pil",
                   "cloud-guest-utils", "gdisk", "e2fsprogs"]
        run([*chroot, "apt-get", "install", "-y", "--no-remove", "--no-install-recommends", "--allow-downgrades",
             *desktop, *("/var/tmp/bpi-k1-debs/" + f.name for f in packages)], env=env)
        audit = run([*chroot, "dpkg", "--audit"], env=env, capture_output=True, text=True)
        if audit.stdout.strip():
            raise ValueError("dpkg 仍有未完成設定的套件：" + audit.stdout)
        # 保持官方加速元件與現有核心成套更新，禁止無意切回 MBR 安裝器。
        status_text = (mount / "var/lib/dpkg/status").read_text()
        pinned = {lock["packages"][k]["Package"]: lock["packages"][k]["Version"]
                  for k in lock["profiles"][args.board]["packages"]}
        for para in status_text.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in para.splitlines() if ": " in line and not line.startswith(" "))
            if fields.get("Package", "").startswith(("linux-image-", "linux-dtb-", "linux-u-boot-", "armbian-bsp-", "armbian-config")):
                pinned[fields["Package"]] = fields["Version"]
        pref = "# 核心與硬體加速配套需整組驗證後更新。\n"
        for name, version in sorted(pinned.items()):
            pref += f"Package: {name}\nPin: version {version}\nPin-Priority: 1001\n\n"
        write(mount / "etc/apt/preferences.d/bpi-k1-vendor", pref)
        guard = '#!/bin/sh\nprintf "%s\\n" "本映像使用官方格式，請依隨附說明使用 SD 映像或 Titan eMMC 套件。" >&2\nexit 2\n'
        for entry in ("/usr/bin/armbian-install", "/usr/sbin/armbian-install"):
            install_path = mount / entry.lstrip("/")
            if install_path.exists() and "官方格式" not in install_path.read_text(errors="replace"):
                run([*chroot, "dpkg-divert", "--local", "--add", "--rename", "--divert", entry + ".standard-format", entry])
            write(install_path, guard, 0o755)
        protect_media_tools(mount, chroot)
        shutil.copyfile(REPO / "tools/collect_bpi_k1_runtime.py", mount / "usr/local/sbin/collect_bpi_k1_runtime.py")
        (mount / "usr/local/sbin/collect_bpi_k1_runtime.py").chmod(0o755)
        write(mount / "etc/X11/default-display-manager", "/usr/sbin/gdm3\n")
        write(mount / "etc/gdm3/custom.conf", "[daemon]\nWaylandEnable=true\n[security]\n[xdmcp]\n[chooser]\n[debug]\n")
        display = mount / "etc/systemd/system/display-manager.service"
        display.unlink(missing_ok=True)
        display.symlink_to("/lib/systemd/system/gdm3.service")
        write(mount / "etc/bpi-k1-vendor.json", json.dumps({**identity, "desktop": "gnome-wayland", "hardware_validation": "pending"}, ensure_ascii=False, indent=2) + "\n")
        (mount / "usr/share/bpi-k1-acceleration.lock.json").write_bytes(lock_bytes)
        preflight = acceleration.preflight(lock, args.board, mount, "installed")
        write(out / "acceleration-preflight.json", json.dumps(preflight, ensure_ascii=False, indent=2) + "\n")
        if not preflight["passed"]:
            raise ValueError("加速配套預檢未通過：" + "; ".join(preflight["errors"]))
        for existing in sources.glob("*.vendor-build-disabled"):
            existing.rename(existing.with_name(existing.name.removesuffix(".vendor-build-disabled")))
        run([*chroot, "apt-get", "clean"], env=env)
        shutil.rmtree(debdest)
        shutil.copyfile(mount / "var/lib/dpkg/status", out / "dpkg-status")
        run(["rsync", "-aHAX", "--numeric-ids", str(mount / "boot") + "/", str(out / "boot-tree") + "/"])
        state.update(status="validating", packages=pinned, kernel=BOARDS[args.board][1], desktop="gnome-wayland")
    except BaseException:
        state["status"] = "failed"
        raise
    finally:
        if policy_installed:
            if policy:
                (mount / "usr/sbin/policy-rc.d").write_bytes(policy[0])
                (mount / "usr/sbin/policy-rc.d").chmod(policy[1])
            else:
                (mount / "usr/sbin/policy-rc.d").unlink()
        if resolv_saved is not None:
            resolv = mount / "etc/resolv.conf"
            resolv.unlink(missing_ok=True)
            if "link" in resolv_saved:
                resolv.symlink_to(resolv_saved["link"])
            else:
                write(resolv, resolv_saved["text"], resolv_saved["mode"])
        if attached:
            for existing in (mount / "etc/apt/sources.list.d").glob("*.vendor-build-disabled"):
                existing.rename(existing.with_name(existing.name.removesuffix(".vendor-build-disabled")))
        for target in reversed(attached):
            run(["umount", target])
        write(marker, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    run(["e2fsck", "-pf", rootfs], codes=(0, 1))
    state["rootfs_sha256"] = sha256(rootfs)
    state["status"] = "complete"
    write(marker, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(f"根檔案系統已完成：{out}；硬體驗證仍待測試者執行。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"準備失敗：{exc}", file=sys.stderr)
        sys.exit(1)
