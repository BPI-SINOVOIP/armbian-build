#!/usr/bin/env python3
"""以固定來源建立 CM6 riscv64 WiringPi 套件；不接觸硬體或安裝主機。"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/spacemit-k1-gpio/wiringpi"
COMMIT = "da58b589a3ca3e44f569850f07ee17de2e294b5f"
PACKAGE = "bpi-cm6-wiringpi"
VERSION = "3.19+gitda58b589.cm6.1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def fetch(cache, name, url, size, digest):
    require(Path(name).name == name, "快取檔名不合法")
    path = cache / name
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".download")
        require(not tmp.exists(), "已有尚未完成的下載，不覆寫")
        try:
            with urllib.request.urlopen(url, timeout=90) as response, tmp.open("xb") as out:
                shutil.copyfileobj(response, out)
            require(tmp.stat().st_size == size and sha256(tmp) == digest, "下載容量或 SHA-256 不符")
            tmp.rename(path)
        finally:
            if tmp.exists():
                tmp.unlink()
    require(path.is_file() and not path.is_symlink(), "快取必須為一般檔案")
    require(path.stat().st_size == size and sha256(path) == digest, "快取容量或 SHA-256 不符")
    return path


def copy(src, dst, mode=0o644):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(mode)


def run_build(args):
    lock = json.loads((CONFIG / "source-lock.json").read_text())
    require(lock["schema_version"] == 1 and lock["package"] == PACKAGE and
            lock["version"] == VERSION and lock["architecture"] == "riscv64" and
            lock["source"]["commit"] == COMMIT, "固定套件或來源識別不符")
    cache = Path(args.cache).resolve()
    output = Path(args.output).resolve()
    require(not output.exists() or not any(output.iterdir()), "輸出目錄已有內容，請另選目錄以保留證據")
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    commands = []
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = os.environ.copy()
    env.update({"SOURCE_DATE_EPOCH": str(lock["source_date_epoch"]), "LC_ALL": "C", "TZ": "UTC"})
    for key in ("CC", "CFLAGS", "CPPFLAGS", "LDFLAGS", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH", "MAKEFLAGS"):
        env.pop(key, None)
    log = (output / "build.log").open("w")

    def run(argv, cwd=None):
        result = subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        commands.append({"argv": [str(a) for a in argv], "cwd": str(cwd) if cwd else None,
                         "exit": result.returncode})
        log.write("\n" + json.dumps(commands[-1], ensure_ascii=False) + "\n" + result.stdout)
        log.flush()
        require(result.returncode == 0, "建置命令失敗，請查 build.log")
        return result.stdout

    try:
        required = ["riscv64-linux-gnu-gcc", "riscv64-linux-gnu-strip", "riscv64-linux-gnu-readelf",
                    "make", "patch", "dpkg-deb"]
        tools = {}
        for name in required:
            exe = shutil.which(name)
            require(exe is not None, "缺少建置工具：" + name)
            tools[name] = {"path": exe, "sha256": sha256(Path(exe).resolve()),
                           "version": run([exe, "--version"]).splitlines()[0]}
        require(run(["riscv64-linux-gnu-gcc", "-dumpmachine"]).strip() == "riscv64-linux-gnu",
                "交叉編譯器架構不符")
        source = lock["source"]
        archive = fetch(cache, source["archive_name"], source["archive_url"],
                        source["archive_bytes"], source["archive_sha256"])
        with tempfile.TemporaryDirectory(prefix="cm6-wiringpi-", dir=cache) as work_name:
            work = Path(work_name)
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar.getmembers():
                    p = PurePosixPath(member.name)
                    require(not p.is_absolute() and ".." not in p.parts and p.parts[0] == source["archive_root"],
                            "來源封存路徑不合法")
                    require(member.isfile() or member.isdir(), "來源封存含不允許的特殊項目")
                tar.extractall(work, filter="data")
            src = work / source["archive_root"]
            require((src / "VERSION").read_text().strip() == "3.19", "來源版本不符")
            for patch in source["patches"]:
                p = CONFIG / patch["path"]
                require(p.parent == CONFIG and p.is_file() and sha256(p) == patch["sha256"], "補丁 SHA-256 不符")
                run(["patch", "--batch", "--fuzz=0", "-p1", "-i", p], cwd=src)
            # 來源庫內的既有預編譯 ELF 不可進入套件；只使用本輪三個建置輸出。
            deps = work / "dependencies"
            deps.mkdir()
            for dep in lock["build_dependencies"]:
                deb = fetch(cache, dep["filename"], dep["url"], dep["bytes"], dep["sha256"])
                fields = run(["dpkg-deb", "-f", deb, "Package", "Version", "Architecture"])
                require("Package: " + dep["package"] + "\n" in fields and
                        "Version: " + dep["version"] + "\n" in fields and
                        "Architecture: riscv64\n" in fields, "交叉連結相依的 DEB 身分不符")
                run(["dpkg-deb", "-x", deb, deps])
            dep_lib = deps / "usr/lib/riscv64-linux-gnu"
            flags = "-march=rv64gc -mabi=lp64d -fstack-protector-strong -D_FORTIFY_SOURCE=2 " + \
                    "-ffile-prefix-map=" + str(work) + "=/usr/src/bpi-cm6-wiringpi"
            make_base = ["make", "-j2", "V=1", "CC=riscv64-linux-gnu-gcc", "WIRINGPI_SONAME_SUFFIX=.3",
                         "EXTRA_CFLAGS=" + flags]
            lib_wpi = src / "wiringPi"
            run(make_base + ["INCLUDE=-I. -I./board -I" + str(deps / "usr/include"),
                            "LIBS=-L" + str(dep_lib) + " -Wl,-z,relro,-z,now -lm -lpthread -lrt -lcrypt"], cwd=lib_wpi)
            (lib_wpi / "libwiringPi.so").symlink_to("libwiringPi.so.3.19")
            dev = src / "devLib"
            run(make_base + ["INCLUDE=-I. -I" + str(lib_wpi),
                            "LIBS=-L" + str(lib_wpi) + " -Wl,-rpath-link," + str(dep_lib) +
                            " -Wl,--no-undefined,-z,relro,-z,now -lwiringPi"], cwd=dev)
            (dev / "libwiringPiDev.so").symlink_to("libwiringPiDev.so.3.19")
            run(make_base + ["INCLUDE=-I" + str(lib_wpi) + " -I" + str(dev),
                            "LDFLAGS=-L" + str(lib_wpi) + " -L" + str(dev) + " -L" + str(dep_lib) +
                            " -Wl,-rpath-link," + str(dep_lib) + " -Wl,-z,relro,-z,now"], cwd=src / "gpio")
            stage = work / "package"
            copy(src / "gpio/gpio", stage / "usr/bin/gpio", 0o755)
            lib_dest = stage / "usr/lib/riscv64-linux-gnu"
            for name, directory in [("libwiringPi", lib_wpi), ("libwiringPiDev", dev)]:
                target = lib_dest / (name + ".so.3.19")
                copy(directory / target.name, target, 0o644)
                (lib_dest / (name + ".so.3")).symlink_to(target.name)
                (lib_dest / (name + ".so")).symlink_to(target.name)
            for header in sorted(lib_wpi.glob("*.h")):
                copy(header, stage / "usr/include" / header.name)
            for name in ["ds1302.h", "gertboard.h", "lcd128x64.h", "lcd.h", "maxdetect.h", "piFace.h", "piGlow.h", "piNes.h", "scrollPhat.h"]:
                copy(dev / name, stage / "usr/include" / name)
            man = stage / "usr/share/man/man1/gpio.1.gz"
            man.parent.mkdir(parents=True)
            man.write_bytes(gzip.compress((src / "gpio/gpio.1").read_bytes(), mtime=0))
            doc = stage / ("usr/share/doc/" + PACKAGE)
            copy(src / "COPYING.LESSER", doc / "COPYING.LESSER")
            copy(src / "debian/copyright", doc / "copyright")
            copy(CONFIG / "README.md", doc / "README.md")
            copy(CONFIG / "source-lock.json", doc / "source-lock.json")
            for patch in source["patches"]:
                copy(CONFIG / patch["path"], doc / patch["path"])
            control = stage / "DEBIAN"
            control.mkdir()
            (control / "control").write_text(
                "Package: " + PACKAGE + "\nVersion: " + VERSION +
                "\nArchitecture: riscv64\nSection: electronics\nPriority: optional\n" +
                "Maintainer: BPI CM6 <support@banana-pi.org>\n" +
                "Depends: " + ", ".join(lock["depends"]) + "\n" +
                "Conflicts: wiringpi, libwiringpi2, libwiringpi3, libwiringpi-dev\n" +
                "Provides: wiringpi, libwiringpi3, libwiringpi-dev\n" +
                "Homepage: https://github.com/BPI-SINOVOIP/BPI-WiringPi2\n" +
                "Description: CM6 的 WiringPi 函式庫與命令列工具\n" +
                " 由固定來源交叉編譯，包含 CM6 板型與接腳對應；版本查詢不操作 GPIO。\n")
            (control / "triggers").write_text("activate-noawait ldconfig\n")
            elfs = []
            for file in [stage / "usr/bin/gpio", lib_dest / "libwiringPi.so.3.19", lib_dest / "libwiringPiDev.so.3.19"]:
                run(["riscv64-linux-gnu-strip", "--strip-unneeded", file])
                hdr = run(["riscv64-linux-gnu-readelf", "-h", file])
                dynamic = run(["riscv64-linux-gnu-readelf", "-d", file])
                versions = run(["riscv64-linux-gnu-readelf", "-V", file])
                require("RISC-V" in hdr and "ELF64" in hdr, "套件 ELF 架構不符")
                require("(RPATH)" not in dynamic and "(RUNPATH)" not in dynamic, "不可帶入建置路徑")
                glibc = [tuple(map(int, s.split("."))) for s in re.findall(r"Name: GLIBC_(\d+\.\d+)", versions)]
                require(not glibc or max(glibc) <= (2, 38), "ELF 要求高於既定 libc6 相依")
                needed = re.findall(r"Shared library: \[(.*?)\]", dynamic)
                require(set(needed) <= {"libwiringPi.so.3", "libwiringPiDev.so.3", "libc.so.6", "libm.so.6", "libcrypt.so.1", "ld-linux-riscv64-lp64d.so.1"},
                        "ELF 出現未宣告的動態相依")
                elfs.append({"path": "/" + str(file.relative_to(stage)), "sha256": sha256(file),
                             "header": hdr, "dynamic": dynamic, "versions": versions, "needed": needed})
            payload = []
            for file in sorted(stage.rglob("*")):
                if file.is_dir() or file.is_relative_to(control):
                    continue
                entry = {"path": "/" + str(file.relative_to(stage)), "mode": oct(stat.S_IMODE(file.lstat().st_mode))}
                if file.is_symlink():
                    target = os.readlink(file)
                    require(not os.path.isabs(target) and ".." not in PurePosixPath(target).parts, "套件符號連結不合法")
                    entry.update({"type": "symlink", "target": target,
                                  "sha256": hashlib.sha256(target.encode()).hexdigest()})
                else:
                    entry.update({"type": "file", "bytes": file.stat().st_size, "sha256": sha256(file)})
                payload.append(entry)
            sums = [hashlib.md5(p.read_bytes()).hexdigest() + "  " + str(p.relative_to(stage))
                    for p in sorted(stage.rglob("*")) if p.is_file() and not p.is_symlink() and not p.is_relative_to(control)]
            (control / "md5sums").write_text("\n".join(sums) + "\n")
            epoch = lock["source_date_epoch"]
            for file in sorted(stage.rglob("*"), reverse=True):
                os.utime(file, (epoch, epoch), follow_symlinks=False)
            os.utime(stage, (epoch, epoch))
            artifact = output / (PACKAGE + "_" + VERSION + "_riscv64.deb")
            run(["dpkg-deb", "--root-owner-group", "-Zxz", "-z6", "--build", stage, artifact])
            fields = run(["dpkg-deb", "-f", artifact])
            files = run(["dpkg-deb", "-c", artifact])
            manifest = {"schema_version": 1, "package": PACKAGE, "version": VERSION, "architecture": "riscv64",
                        "artifact": artifact.name, "bytes": artifact.stat().st_size, "sha256": sha256(artifact),
                        "source": source, "source_lock_sha256": sha256(CONFIG / "source-lock.json"),
                        "lock_sha256": sha256(CONFIG / "source-lock.json"),
                        "builder_sha256": sha256(__file__), "build_dependencies": lock["build_dependencies"],
                        "depends": lock["depends"], "toolchain": tools, "payload_files": payload, "elfs": elfs,
                        "safe_probe": lock["safe_probe"], "source_date_epoch": epoch,
                        "started_at": started, "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "commands": commands, "deb_control": fields, "deb_files": files,
                        "hardware_validation": "未執行；交由主代理依租約在代表鏡像驗證。"}
            save(output / "package-manifest.json", manifest)
        print(json.dumps({"artifact": str(artifact), "sha256": manifest["sha256"], "manifest": str(output / "package-manifest.json")}, ensure_ascii=False))
    except Exception as exc:
        save(output / "failure.json", {"started_at": started, "failed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                       "error": str(exc), "commands": commands})
        raise
    finally:
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="建立固定 CM6 riscv64 套件")
    build.add_argument("--cache", required=True)
    build.add_argument("--output", required=True)
    args = parser.parse_args()
    run_build(args)


if __name__ == "__main__":
    main()
