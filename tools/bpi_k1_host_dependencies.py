#!/usr/bin/env python3
"""依既有來源鎖規劃 K1 主機相依並核對安裝結果；本工具不安裝套件。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RPI_LOCK = "config/spacemit-k1-gpio/rpi-gpio/source-lock.json"
CAMERA_LOCK = "config/spacemit-k1-camera/source-lock.json"
BT_LOCK = "config/spacemit-k1-connectivity/source-lock.json"
REGISTRY = "config/spacemit-k1-profiles/board-targets.json"
FIXED_TOOLCHAIN = {"gcc-13-riscv64-linux-gnu", "libc6-dev-riscv64-cross", "libc6-riscv64-cross",
                   "binutils-riscv64-linux-gnu", "linux-libc-dev-riscv64-cross"}
BASE_PACKAGES = ("python3-yaml", "device-tree-compiler", "u-boot-tools", "gdisk", "unzip",
                 "make", "patch", "binutils")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(relative):
    return json.loads((ROOT / relative).read_text())


def supported_host(release, architecture):
    require(release == "noble" and architecture == "amd64",
            "K1 官方格式建置目前只支援 Ubuntu 24.04 Noble amd64 主機或同版本容器")


def package_name(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", value), "來源鎖套件名稱不合法")
    return value


def version_string(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+:~_-]*", value), "來源鎖版本不合法")
    return value


def plan(board, camera, release, architecture):
    supported_host(release, architecture)
    targets = load(REGISTRY)["targets"]
    base = targets.get(board, {}).get("base_board", board)
    require(base in ("bananapicm6", "bananapif3"), "K1 主機相依不適用指定板型")
    require(camera in ("none", "dual-imx415") and (base == "bananapicm6" or camera == "none"),
            "相機主機相依不適用指定板型")
    exact = {}
    minimum = {}
    locks = [REGISTRY]
    if base == "bananapicm6":
        rpi = load(RPI_LOCK)
        for item in rpi["toolchain_packages"]:
            name = package_name(item["package"])
            require(name not in exact, "GPIO 工具鏈來源鎖有重複套件")
            exact[name] = version_string(item["version"])
        require(set(exact) == FIXED_TOOLCHAIN, "GPIO 工具鏈來源鎖必須提供固定五個套件")
        locks += [RPI_LOCK, BT_LOCK]
    if camera == "dual-imx415":
        minimum = {package_name(k): version_string(v)
                   for k, v in load(CAMERA_LOCK)["toolchain"]["host_requirements"].items()}
        require(minimum and not (set(minimum) & set(exact)), "相機最低版本與固定工具鏈有衝突")
        locks.append(CAMERA_LOCK)
    # 保留 APT 的 pkg=version 語法；框架只移除 group:: 前綴。
    packages = ["build-tools::" + name for name in BASE_PACKAGES]
    packages += ["native-toolchain::gcc-riscv64-linux-gnu"]
    packages += ["native-toolchain::" + name + "=" + version for name, version in sorted(exact.items())]
    packages += ["build-tools::" + name for name in sorted(minimum)]
    return {"schema_version": 1, "board": board, "base_board": base, "camera": camera,
            "supported_host": {"distribution": "ubuntu", "release": release, "architecture": architecture},
            "packages": packages, "exact": exact, "minimum": minimum,
            "source_locks": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in locks}}


def capture(argv):
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    require(completed.returncode == 0, "主機相依查核命令失敗：" + " ".join(argv) + "\n" + completed.stderr)
    return completed.stdout.strip()


def version_at_least(actual, required):
    result = subprocess.run(["dpkg", "--compare-versions", actual, "ge", required], timeout=10)
    require(result.returncode in (0, 1), "dpkg 版本比較失敗")
    return result.returncode == 0


def check_installed(required, installed):
    for name, version in required["exact"].items():
        require(installed.get(name) == version,
                "GPIO 固定工具鏈版本不符：" + name + "；需要 " + version + "，實際 " + str(installed.get(name)))
    for name, version in required["minimum"].items():
        require(name in installed and version_at_least(installed[name], version),
                "相機主機相依版本不足：" + name + "；最低 " + version)


def installed_version(output, name):
    # 多架構主機查詢裸套件名可能回傳多行；只接受本機 amd64 或架構無關套件。
    matches = []
    for line in output.splitlines():
        fields = line.split("\t")
        require(len(fields) == 4 and fields[0] == name, "dpkg 套件查核欄位不符：" + name)
        if fields[1] in ("amd64", "all"):
            require(fields[2] == "installed", "主機相依尚未完成安裝：" + name)
            matches.append(fields[3])
    require(len(matches) == 1, "主機相依缺少唯一 amd64／all 安裝項：" + name)
    return matches[0]


def verify(required):
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
    require(os_release.get("ID", "").strip('"') == "ubuntu", "K1 官方格式須使用 Ubuntu 主機或 Ubuntu 容器")
    supported_host(os_release.get("VERSION_CODENAME", "").strip('"'), capture(["dpkg", "--print-architecture"]))
    installed = {}
    names = set(BASE_PACKAGES) | {"gcc-riscv64-linux-gnu"} | set(required["exact"]) | set(required["minimum"])
    for name in sorted(names):
        value = capture(["dpkg-query", "-W", "-f=${Package}\t${Architecture}\t${db:Status-Status}\t${Version}\n", name])
        installed[name] = installed_version(value, name)
    check_installed(required, installed)
    result = {**required, "installed": installed, "status": "passed"}
    if required["base_board"] == "bananapicm6":
        expected = load(BT_LOCK)["compiler"]
        require(expected["command"] == "riscv64-linux-gnu-gcc" and expected["target"] == "riscv64-linux-gnu" and
                re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]), "藍牙編譯器來源鎖欄位不符")
        executable = shutil.which(expected["command"])
        require(executable is not None, "固定交叉編譯器未安裝")
        compiler = Path(executable).resolve()
        actual_sha = hashlib.sha256(compiler.read_bytes()).hexdigest()
        require(actual_sha == expected["sha256"], "藍牙編譯器 SHA-256 不符；拒絕使用未受來源鎖約束的工具鏈")
        actual_version = capture([str(compiler), "-dumpfullversion"])
        actual_target = capture([str(compiler), "-dumpmachine"])
        require(actual_version == expected["version"] and actual_target == expected["target"], "藍牙編譯器版本或目標不符")
        result["bluetooth_compiler"] = {"command": expected["command"], "sha256": actual_sha,
                                         "version": actual_version, "target": actual_target}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("packages", "verify"))
    parser.add_argument("--board", required=True)
    parser.add_argument("--camera", choices=("none", "dual-imx415"), default="none")
    parser.add_argument("--host-release", required=True)
    parser.add_argument("--host-arch", required=True)
    args = parser.parse_args()
    try:
        required = plan(args.board, args.camera, args.host_release, args.host_arch)
        if args.action == "packages":
            print("\n".join(required["packages"]))
        else:
            print(json.dumps(verify(required), ensure_ascii=False, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
