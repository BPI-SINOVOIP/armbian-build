#!/usr/bin/env python3
"""以固定來源與 Noble 標頭建置 CM6 的 RPi.GPIO 套件。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/spacemit-k1-gpio/rpi-gpio"
SOURCES = ["py_gpio.c", "c_gpio.c", "cpuinfo.c", "event_gpio.c", "soft_pwm.c",
           "py_pwm.c", "common.c", "constants.c", "c_gpio_bpi.c"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(test, message):
    if not test:
        raise RuntimeError(message)


def run(argv, *, cwd=None, env=None):
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                            text=True, capture_output=True, check=False)
    record = {"argv": [str(x) for x in argv], "exit": result.returncode,
              "stdout": result.stdout, "stderr": result.stderr}
    require(result.returncode == 0,
            "命令失敗：" + json.dumps(record, ensure_ascii=False))
    return record


def fetch(cache, name, url, expected):
    path = cache / name
    if not path.exists():
        pending = path.with_suffix(path.suffix + ".pending")
        with urllib.request.urlopen(url, timeout=90) as response:
            pending.write_bytes(response.read())
        require(digest(pending) == expected, "下載內容雜湊不符：" + name)
        pending.replace(path)
    require(path.is_file() and digest(path) == expected, "快取內容雜湊不符：" + name)
    return path


def extract_source(archive, destination):
    with tarfile.open(archive, "r:gz") as stream:
        for item in stream.getmembers():
            require(item.isfile() or item.isdir(), "來源封存檔含非一般項目")
            require(not item.name.startswith("/") and ".." not in Path(item.name).parts,
                    "來源封存檔路徑不安全")
        stream.extractall(destination)
    roots = list(destination.iterdir())
    require(len(roots) == 1 and roots[0].is_dir(), "來源封存檔根目錄不唯一")
    return roots[0]


def build(args):
    lock_path = CONFIG / "source-lock.json"
    lock = json.loads(lock_path.read_text())
    cache, output = Path(args.cache).resolve(), Path(args.output).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    artifact = f"{lock['package']}_{lock['version']}_{lock['architecture']}.deb"
    require(not (output / artifact).exists() and not (output / "package-manifest.json").exists(),
            "輸出已存在；請保留舊成品並改用空白輸出目錄")
    source = lock["source"]
    archive = fetch(cache, source["archive_name"], source["url"], source["archive_sha256"])
    dependencies = [(item, fetch(cache, item["filename"], item["url"], item["sha256"]))
                    for item in lock["build_dependencies"]]
    compiler = shutil.which(args.cc)
    require(compiler is not None, "找不到 RISC-V 交叉編譯器")
    target = run([compiler, "-dumpmachine"])["stdout"].strip()
    require(target == "riscv64-linux-gnu", "交叉編譯器目標錯誤")
    records = [run([compiler, "--version"]), run([compiler, "-print-search-dirs"])]
    toolchain_packages = []
    for item in lock["toolchain_packages"]:
        result = run(["dpkg-query", "-W", "-f=${Version}", item["package"]])
        require(result["stdout"] == item["version"], "工具鏈套件版本不符：" + item["package"])
        toolchain_packages.append(item)
        records.append(result)
    toolchain_files = []
    for name in ["cc1", "libc.so.6", "libgcc.a"]:
        option = "-print-prog-name=" if name == "cc1" else "-print-file-name="
        path = Path(run([compiler, option + name])["stdout"].strip()).resolve()
        require(path.is_file(), "工具鏈檔案缺失：" + name)
        toolchain_files.append({"name": name, "path": str(path), "sha256": digest(path)})
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(source["source_date_epoch"]),
               LC_ALL="C.UTF-8", TZ="UTC")
    with tempfile.TemporaryDirectory(prefix="rpi-gpio-build-", dir=cache) as temporary:
        work = Path(temporary)
        extracted = work / "source"
        extracted.mkdir()
        src = extract_source(archive, extracted)
        for patch in lock["patches"]:
            patch_path = CONFIG / patch["path"]
            require(digest(patch_path) == patch["sha256"], "修補檔雜湊不符")
            records.append(run(["patch", "--batch", "--forward", "-p1", "-i", patch_path], cwd=src))
        headers = work / "headers"
        headers.mkdir()
        for item, deb in dependencies:
            metadata = run(["dpkg-deb", "-f", deb, "Package", "Version", "Architecture"])
            require(metadata["stdout"].splitlines() == [f"Package: {item['package']}",
                    f"Version: {item['version']}", f"Architecture: {item['architecture']}"],
                    "標頭套件識別不符")
            records.extend([metadata, run(["dpkg-deb", "-x", deb, headers])])
        pkg = work / "package"
        dist = pkg / "usr/lib/python3/dist-packages"
        (dist / "RPi/GPIO").mkdir(parents=True)
        for name in ["RPi/__init__.py", "RPi/GPIO/__init__.py"]:
            shutil.copyfile(src / name, dist / name)
        shared = dist / "RPi/_GPIO.cpython-312-riscv64-linux-gnu.so"
        command = [compiler, "-shared", "-fPIC", "-O2", "-g0", "-pthread",
                   "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2",
                   "-Wl,-z,relro,-z,now", "-Wl,-z,noexecstack", "-Wl,--build-id=sha1",
                   "-ffile-prefix-map=" + str(work) + "=/build/rpi-gpio",
                   "-I" + str(headers / "usr/include"),
                   "-I" + str(headers / "usr/include/python3.12"),
                   "-I" + str(headers / "usr/include/riscv64-linux-gnu/python3.12"),
                   "-o", shared] + [src / "source" / name for name in SOURCES]
        records.append(run(command, env=env))
        elf = run(["readelf", "-h", "-d", "-V", "-W", shared])
        require("RISC-V" in elf["stdout"] and "ELF64" in elf["stdout"], "ELF 架構不符")
        versions = set(re.findall(r"GLIBC_(\d+\.\d+)", elf["stdout"]))
        require(versions and all(tuple(map(int, x.split("."))) <= (2, 34) for x in versions),
                "ELF 的 libc 需求超出已宣告相依")
        require(set(re.findall(r"\(NEEDED\).*?\[(.*?)\]", elf["stdout"])) ==
                {"libc.so.6", "ld-linux-riscv64-lp64d.so.1"},
                "ELF 出現未宣告共享函式庫相依：" + elf["stdout"])
        records.append(elf)
        doc = pkg / ("usr/share/doc/" + lock["package"])
        doc.mkdir(parents=True)
        shutil.copyfile(src / "LICENCE.txt", doc / "copyright")
        shutil.copyfile(lock_path, doc / "source-lock.json")
        shutil.copyfile(CONFIG / "README.md", doc / "README.md")
        control = pkg / "DEBIAN"
        control.mkdir()
        (control / "control").write_text(
            f"Package: {lock['package']}\nVersion: {lock['version']}\nArchitecture: riscv64\n"
            "Section: python\nPriority: optional\nMaintainer: BPI CM6 <support@banana-pi.org>\n"
            f"Depends: {', '.join(lock['runtime_dependencies'])}\n"
            "Provides: python3-rpi.gpio (= 0.7.1)\nConflicts: python3-rpi.gpio\n"
            "Description: Banana Pi CM6 的 Python GPIO 相容套件\n"
            " 固定 CM6 來源與 Noble Python 3.12 ABI，透過 dpkg 管理。\n")
        payload = [{"path": "/" + str(p.relative_to(pkg)), "bytes": p.stat().st_size,
                    "sha256": digest(p)} for p in sorted(pkg.rglob("*"))
                   if p.is_file() and control not in p.parents]
        for item in sorted(pkg.rglob("*"), reverse=True):
            os.chmod(item, 0o755 if item.is_dir() else 0o644)
            os.utime(item, (source["source_date_epoch"], source["source_date_epoch"]))
        os.utime(pkg, (source["source_date_epoch"], source["source_date_epoch"]))
        records.append(run(["dpkg-deb", "--root-owner-group", "-Zxz", "-z9", "--build", pkg,
                            output / artifact], env=env))
        manifest = {"schema_version": 1, "package": lock["package"], "version": lock["version"],
                    "architecture": "riscv64", "artifact": artifact,
                    "bytes": (output / artifact).stat().st_size, "sha256": digest(output / artifact),
                    "source": {**source, "patches": lock["patches"]}, "payload_files": payload,
                    "build_dependencies": lock["build_dependencies"],
                    "runtime_dependencies": lock["runtime_dependencies"],
                    "lock_sha256": digest(lock_path), "builder_sha256": digest(__file__),
                    "compiler": {"path": compiler, "sha256": digest(Path(compiler).resolve()),
                                 "target": target, "version": records[0]["stdout"]},
                    "toolchain_packages": toolchain_packages, "toolchain_files": toolchain_files,
                    "build_started_at": started, "build_finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "elf": {"path": str(shared.relative_to(pkg)), "glibc_versions": sorted(versions),
                            "sha256": digest(shared)},
                    "hardware_validation": "尚未實機驗證"}
        (output / "package-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        (output / "build-commands.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"artifact": str(output / artifact), "sha256": manifest["sha256"],
                      "manifest": str(output / "package-manifest.json")}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    command = sub.add_parser("build", help="交叉編譯並封裝")
    command.add_argument("--cache", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--cc", default="riscv64-linux-gnu-gcc")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
