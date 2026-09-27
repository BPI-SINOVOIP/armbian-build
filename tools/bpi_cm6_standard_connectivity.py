#!/usr/bin/env python3
"""整合標準 CM6 Noble 的固定藍牙套件；不掛載、不執行 APT、不操作硬體。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_k1_native_rootfs as native
import bpi_cm6_gpio as gpio

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "config/spacemit-k1-connectivity/source-lock.json"
KERNEL = "6.6.36-legacy-spacemit"
PACKAGE = "bpi-cm6-bluetooth"
SERVICE = PACKAGE + ".service"
require = native.require
digest = native.digest


def validate_root(root):
    """只接受標準板名與 Noble ABI，不因最小系統缺少桌面而拒絕。"""
    text = native.root_file(root, "etc/os-release").read_text()
    values = native.acceleration.environment_values
    require(values(text, "ID") == ["ubuntu"] and values(text, "VERSION_CODENAME") == ["noble"],
            "固定藍牙二進位只支援 Ubuntu Noble；Jammy 等版本須另行驗證 ABI")
    text = native.root_file(root, "etc/armbian-release").read_text()
    require(values(text, "BOARD") == ["bananapicm6"], "只接受標準 bananapicm6 板型")
    packages = native.installed_packages(root)
    libc = [item for item in packages if item["package"] == "libc6"]
    require(len(libc) == 1 and libc[0]["architecture"] == "riscv64", "根系統 libc6 架構不符")
    require(subprocess.run(["dpkg", "--compare-versions", libc[0]["version"], "ge", "2.38"],
                           capture_output=True, check=False).returncode == 0, "根系統 libc6 ABI 過舊")
    files = {name: digest(native.root_file(root, "boot/" + name))
             for name in ("vmlinuz-" + KERNEL, "config-" + KERNEL)}
    require(any(item["package"] == "linux-image-legacy-spacemit" and
                item["architecture"] == "riscv64" for item in packages), "缺少 CM6 RISC-V 核心套件")
    return packages, files


def checked_package(path):
    package, manifest = native.common.bluetooth_package("bpi-cm6", path)
    # 套件內的選址與服務必須對應本次受控來源，不接受舊包搭配新來源。
    for relative, source in {
        "usr/sbin/bpi-cm6-bluetooth": LOCK.parent / "bpi-cm6-bluetooth",
        "usr/lib/systemd/system/" + SERVICE: LOCK.parent / SERVICE,
        "usr/share/bpi-cm6-bluetooth/source-lock.json": LOCK,
    }.items():
        require(manifest["files"][relative]["sha256"] == digest(source), "藍牙套件與目前啟動來源不符")
    for relative, item in manifest["files"].items():
        parts = Path(relative).parts
        require(parts and not Path(relative).is_absolute() and ".." not in parts and
                len(item["sha256"]) == 64, "套件檔案清單不合法")
    return package, manifest


def stage(root, work, bt_package, gpio_cache):
    root = native.root_directory(root)
    work = Path(work).absolute()
    require(not os.path.lexists(work) and not work.resolve().is_relative_to(root),
            "工作目錄須尚未存在且位於根系統外")
    _, kernel = validate_root(root)
    package, manifest = checked_package(bt_package)
    gpio_records = gpio.package_records(gpio_cache)
    gpio_debs = gpio.paths(gpio_cache)
    gpio_manifests = {name: json.loads((Path(gpio_cache) / name / "package-manifest.json").read_text())
                      for name in gpio.RECIPES}
    relative = "var/tmp/bpi-cm6-connectivity-" + manifest["sha256"][:16]
    staged = native.writable_path(root, relative)
    require(not os.path.lexists(staged), "根系統暫存目錄已存在")
    identity = {"board": "bpi-cm6", "armbian_board": "bananapicm6",
                "cm6_bluetooth_package_sha256": manifest["sha256"],
                "connectivity_lock_sha256": digest(LOCK)}
    state = {"schema_version": 1, "status": "staged", "identity": identity,
             "root_binding": native.binding(root), "kernel": kernel,
             "producer_sha256": digest(Path(__file__)), "native_tool_sha256": digest(Path(native.__file__)),
             "manifest": manifest, "stage_path": relative,
             "gpio_records": gpio_records, "gpio_manifests": gpio_manifests,
             "install_args": ["/" + relative + "/" + p.name for p in [package, *gpio_debs]]}
    work.mkdir(parents=True)
    try:
        staged.mkdir(parents=True)
        for deb in [package, *gpio_debs]:
            shutil.copyfile(deb, staged / deb.name)
        require(digest(staged / package.name) == manifest["sha256"], "複製後藍牙套件雜湊不符")
        native.write_json(work / "stage.json", state)
        native.write_json(staged / "transaction.json", {"stage_sha256": digest(work / "stage.json")})
    except BaseException:
        if staged.is_dir():
            shutil.rmtree(staged)
        shutil.rmtree(work)
        raise
    return {"status": "staged", "install_args": state["install_args"], "identity": identity,
            "hardware_validation": "pending"}


def finish(root, work):
    root = native.root_directory(root)
    work = native.root_directory(work)
    state_path = native.common.regular(work / "stage.json")
    state = json.loads(state_path.read_text())
    require(state.get("schema_version") == 1 and state.get("status") == "staged", "暫存紀錄格式不符")
    require(state["root_binding"] == native.binding(root), "兩階段根系統不是同一目錄")
    require(state["producer_sha256"] == digest(Path(__file__)) and
            state["native_tool_sha256"] == digest(Path(native.__file__)), "兩階段工具版本不同")
    require(state["identity"]["connectivity_lock_sha256"] == digest(LOCK), "兩階段來源鎖不同")
    manifest = state["manifest"]
    require(state["stage_path"] == "var/tmp/bpi-cm6-connectivity-" + manifest["sha256"][:16],
            "暫存目錄與套件身分不符")
    staged = native.writable_path(root, state["stage_path"])
    token = json.loads(native.common.regular(staged / "transaction.json").read_text())
    require(token == {"stage_sha256": digest(state_path)}, "暫存紀錄遭變更")
    require({path.name for path in staged.iterdir()} == {manifest["artifact"], "transaction.json", *[r["filename"] for r in state["gpio_records"].values()]},
            "暫存目錄內容不符")
    require(digest(staged / manifest["artifact"]) == manifest["sha256"], "暫存套件遭變更")
    require(not os.path.lexists(work / "integration.json"), "此整合已完成，拒絕重複執行")
    packages, kernel = validate_root(root)
    require(kernel == state["kernel"], "安裝藍牙套件期間核心檔案變更")
    require(any(item["package"] == PACKAGE and item["version"] == manifest["version"] and
                item["architecture"] == "riscv64" for item in packages), "指定藍牙套件尚未安裝完成")
    checked = []
    for relative, item in manifest["files"].items():
        if relative.startswith("DEBIAN/"):
            continue
        path = native.root_file(root, relative)
        require(path.stat().st_size == item["bytes"] and digest(path) == item["sha256"] and
                path.stat().st_mode & 0o777 == int(item["mode"], 8), "已安裝套件檔案不符：" + relative)
        checked.append(relative)
    service = native.root_file(root, "usr/lib/systemd/system/" + SERVICE)
    link = native.writable_path(root, "etc/systemd/system/multi-user.target.wants/" + SERVICE, leaf_link=True)
    require(link.is_symlink() and native.acceleration.rooted_path(root, "/" + str(link.relative_to(root))) == service,
            "藍牙服務尚未啟用或指向錯誤服務")
    for record in state["gpio_records"].values():
        require(digest(staged / record["filename"]) == record["sha256"], "GPIO 暫存套件遭變更")
        require(any(p["package"] == record["package"] and p["version"] == record["version"] and
                    p["architecture"] == "riscv64" for p in packages), "GPIO 尚未由 dpkg 安裝")
    gpio_result = gpio.verify_installed(root, state["gpio_records"])
    policy = native.seed_bluetooth_initial_policy(root, "bpi-cm6", state["identity"])
    result = {"schema_version": 1, "status": "complete", "identity": state["identity"],
              "kernel": kernel, "verified_files": checked, "service_enabled": True,
              "bluetooth_initial_policy": policy, "gpio": gpio_result,
              "gpio_package_manifests": state["gpio_manifests"], "hardware_validation": "pending",
              "scope": "只核對標準 Noble 根系統的板級藍牙配套；不判定實機驗收。"}
    native.write_json(work / "integration.json", result)
    receipt = native.writable_path(root, "usr/share/bpi-cm6-standard/integration.json")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    native.write_json(receipt, result)
    shutil.rmtree(staged)
    return result


def main():
    parser = native.ChineseParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("stage", "finish"):
        child = commands.add_parser(command, help="暫存套件" if command == "stage" else "核對安裝並預置首次偏好")
        child.add_argument("--rootfs", required=True, type=Path, help="框架根系統目錄")
        child.add_argument("--work-dir", required=True, type=Path, help="本次兩階段紀錄目錄")
        if command == "stage":
            child.add_argument("--gpio-cache", required=True, type=Path, help="固定 GPIO 套件目錄")
            child.add_argument("--cm6-bluetooth-package", required=True, type=Path, help="附固定來源清單的 CM6 藍牙 DEB")
    args = parser.parse_args()
    try:
        result = (stage(args.rootfs, args.work_dir, args.cm6_bluetooth_package, args.gpio_cache) if args.command == "stage"
                  else finish(args.rootfs, args.work_dir))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("CM6 標準藍牙整合已停止：" + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
