#!/usr/bin/env python3
"""只為標準 CM6 Noble 根系統整合七個固定 GPU 套件，不切換桌面或媒體格式。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_k1_acceleration as acceleration
import bpi_k1_native_rootfs as native
import prepare_bpi_k1_vendor_rootfs as common

BOARD = "bpi-cm6"
LOCK = acceleration.DEFAULT_LOCK
GPU_IDS = (
    "img-gpu-powervr=23.2-6460340bb2",
    "libegl-mesa0=22.3.5-bb2",
    "libgbm-dev=22.3.5-bb2",
    "libgbm1=22.3.5-bb2",
    "libgl1-mesa-dri=22.3.5-bb2",
    "libglapi-mesa=22.3.5-bb2",
    "libglx-mesa0=22.3.5-bb2",
)
PAYLOADS = {
    "img-gpu-powervr": {
        "lib/firmware/rgx.fw.36.29.52.182": "firmware",
        "lib/firmware/rgx.sh.36.29.52.182": "firmware",
        "usr/lib/libpvr_dri_support.so": "elf",
        "usr/lib/libGLESv2_PVR_MESA.so": "elf",
        "usr/lib/libsrv_um.so": "elf",
        "usr/lib/libVK_IMG.so": "elf",
        "usr/lib/riscv64-linux-gnu/libpvr_mesa_wsi.so": "elf",
        "etc/vulkan/icd.d/powervr_icd.json": "configuration",
        "usr/share/X11/xorg.conf.d/00-noglamoregl.conf": "configuration",
    },
    "libegl-mesa0": {"usr/lib/riscv64-linux-gnu/libEGL_mesa.so.0.0.0": "elf"},
    "libgbm1": {"usr/lib/riscv64-linux-gnu/libgbm.so.1.0.0": "elf"},
    "libgl1-mesa-dri": {"usr/lib/riscv64-linux-gnu/dri/pvr_dri.so": "elf"},
    "libglapi-mesa": {"usr/lib/riscv64-linux-gnu/libglapi.so.0.0.0": "elf"},
    "libglx-mesa0": {"usr/lib/riscv64-linux-gnu/libGLX_mesa.so.0.0.0": "elf"},
}
SDL_LINE = "SDL_VIDEODRIVER=wayland"
MANIFEST = "usr/share/bpi-cm6-standard-gpu/manifest.json"
PREFERENCES = "etc/apt/preferences.d/bpi-cm6-standard-gpu"
LIMITATIONS = [
    "僅核對固定 GPU 套件、ELF、韌體及根系統相容條件；尚未實機驗證。",
    "保留官方 Xorg 停用 glamor 的設定；不宣稱 XFCE 桌面或 GLX 已硬體加速。",
    "不安裝 GNOME、GDM、相機、AI 或 VPU 配套，不更改媒體格式。",
]


def require(value, message):
    native.require(value, message)


def scoped_lock():
    """先核對完整來源鎖，再於記憶體中選取明列的七包；原契約不變。"""
    lock = acceleration.load_lock(LOCK)
    profile = lock["profiles"][BOARD]
    require(profile["kernel_release"] == "6.6.36-legacy-spacemit"
            and profile["kernel_revision"] == "0d0af0d895251383baee939d44e523699e31889f"
            and profile["kernel_pvr"] == "23.2@6460340", "CM6 核心配套已變更，須重新核對")
    require(all(key in profile["packages"] for key in GPU_IDS), "完整來源鎖缺少固定 GPU 套件")
    selected = copy.deepcopy(lock)
    selected["profiles"][BOARD]["packages"] = list(GPU_IDS)
    return lock, selected


def environment(root):
    path = native.writable_path(root, "etc/environment")
    require(not path.exists() or path.is_file(), "環境設定不是一般檔案")
    return path, path.read_text(encoding="utf-8") if path.exists() else ""


def elf_check(data, path):
    require(len(data) >= 64 and data[:6] == b"\x7fELF\x02\x01"
            and int.from_bytes(data[18:20], "little") == 243,
            "GPU 函式庫不是 RISC-V 64 位元小端序 ELF：" + path)


def package_record(path, item):
    fields = acceleration.paragraphs(subprocess.check_output(
        ["dpkg-deb", "--field", str(path)], text=True))
    require(len(fields) == 1, "DEB 控制資料不完整")
    for name in ("Package", "Version", "Architecture", "Depends", "Pre-Depends"):
        require(fields[0].get(name, "") == item.get(name, ""),
                "DEB 控制欄位與官方來源鎖不符：" + name)
    return native.deb_record(path,
        {name: item[name] for name in ("Package", "Version", "Architecture")},
        "gpu", {"url": "https://archive.spacemit.com/bianbu/" + item["Filename"],
                "index": item["index"], "signature_verified": True},
        item["Size"], item["SHA256"])


def payload_records(path, package):
    """只在記憶體讀取已驗證 DEB 的必要載荷，不解壓至主機檔案系統。"""
    wanted = PAYLOADS.get(package, {})
    if not wanted:
        return []
    data = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(path)])
    records = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = {member.name.removeprefix("./"): member for member in archive.getmembers()}
        for relative, kind in wanted.items():
            member = members.get(relative)
            require(member is not None and (member.isfile() or member.islnk()),
                    "官方 GPU 套件缺少必要載荷：" + relative)
            stream = archive.extractfile(member)
            require(stream is not None, "無法讀取官方 GPU 載荷：" + relative)
            content = stream.read()
            require(bool(content), "官方 GPU 載荷是空檔：" + relative)
            if kind == "elf":
                elf_check(content, relative)
            records.append({"path": "/" + relative, "kind": kind,
                            "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    return records


def check_root(root, selected):
    packages = native.validate_root(root, BOARD, selected)
    result = acceleration.preflight(selected, BOARD, root, "base")
    require(result["passed"], "GPU 根系統預檢失敗：" + "；".join(result["errors"]))
    return packages, result


def stage(root, work, deb_cache):
    root = native.root_directory(root)
    work = Path(work).absolute()
    require(not os.path.lexists(work), "GPU 工作目錄已存在，拒絕覆寫")
    require(work.parent.is_dir() and work.parent.resolve() == work.parent,
            "GPU 工作目錄的父目錄須存在且不可含符號連結")
    require(not work.is_relative_to(root), "GPU 工作紀錄不可放在根系統內")
    lock, selected = scoped_lock()
    lock_sha = common.sha256(LOCK)
    packages, preflight = check_root(root, selected)
    cache = Path(deb_cache).resolve(strict=True)
    paths = common.package_selection(selected, BOARD, cache)
    # 缺少快取直接失敗，避免這個安裝階段隱含下載或改寫既有來源快取。
    for source_id in {lock["packages"][key]["index"] for key in GPU_IDS}:
        for name in ("InRelease", "Packages.gz"):
            common.regular(cache / "indices" / source_id / name)
    verified = acceleration.verify_sources(lock, LOCK, cache, list(GPU_IDS))
    records, payloads = [], []
    for key, path in zip(GPU_IDS, paths):
        records.append(package_record(path, lock["packages"][key]))
        payloads.extend(payload_records(path, lock["packages"][key]["Package"]))
    require(common.sha256(LOCK) == lock_sha, "來源鎖在核對期間變更")
    _, previous_environment = environment(root)
    require(not native.writable_path(root, MANIFEST).exists(), "根系統已整合標準 CM6 GPU 配套")
    require(not native.writable_path(root, PREFERENCES).exists(), "標準 CM6 GPU 版本鎖已存在")
    relative = "var/tmp/bpi-cm6-standard-gpu-" + uuid.uuid4().hex
    target = native.writable_path(root, relative)
    require(not os.path.lexists(target), "GPU 暫存路徑已存在")
    state = {"schema_version": 1, "status": "staged", "board": "bananapicm6",
             "root_binding": native.binding(root), "stage_path": "/" + relative,
             "lock_sha256": lock_sha, "producer_sha256": common.sha256(__file__),
             "kernel": native.kernel_evidence(root, BOARD, selected, packages),
             "packages": records, "payloads": payloads, "verified_sources": verified,
             "base_preflight": preflight,
             "sdl_wayland_lines_before": previous_environment.splitlines().count(SDL_LINE),
             "hardware_validation": "pending", "limitations": LIMITATIONS}
    try:
        work.mkdir(mode=0o700)
        target.mkdir(mode=0o755, parents=True)
        for path, record in zip(paths, records):
            destination = target / record["filename"]
            shutil.copyfile(path, destination)
            destination.chmod(0o644)
            require(destination.stat().st_size == record["bytes"]
                    and common.sha256(destination) == record["sha256"], "GPU DEB 複製後不符來源鎖")
        (work / "acceleration.lock.json").write_bytes(LOCK.read_bytes())
        native.write_json(work / "stage.json", state)
        native.write_json(target / "transaction.json", {"stage_sha256": common.sha256(work / "stage.json")})
    except BaseException:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        if work.is_dir() and not work.is_symlink():
            shutil.rmtree(work)
        raise
    return {"status": "staged", "install_args": [state["stage_path"] + "/" + r["filename"] for r in records],
            "pending_dependencies": preflight["pending_dependencies"], "packages": list(GPU_IDS),
            "hardware_validation": "pending"}


def finish(root, work):
    root = native.root_directory(root)
    work = Path(work)
    require(work.is_dir() and not work.is_symlink() and work.absolute() == work.resolve(),
            "GPU 工作目錄不存在或含符號連結")
    work = work.resolve()
    require(not work.is_relative_to(root), "GPU 工作紀錄不可放在根系統內")
    state_file = common.regular(work / "stage.json")
    state = json.loads(state_file.read_text())
    require(state.get("schema_version") == 1 and state.get("status") == "staged"
            and state.get("board") == "bananapicm6", "GPU 暫存契約不符")
    require(state.get("root_binding") == native.binding(root), "GPU 暫存與完成階段不是同一根系統")
    require(state.get("producer_sha256") == common.sha256(__file__), "GPU helper 在整合期間變更")
    require(state.get("lock_sha256") == common.sha256(LOCK)
            == common.sha256(common.regular(work / "acceleration.lock.json")), "GPU 來源鎖在整合期間變更")
    lock, selected = scoped_lock()
    relative = state.get("stage_path", "").removeprefix("/")
    import re
    require(re.fullmatch(r"var/tmp/bpi-cm6-standard-gpu-[0-9a-f]{32}", relative) is not None,
            "GPU 暫存路徑不符")
    target = native.writable_path(root, relative)
    transaction = json.loads(common.regular(target / "transaction.json").read_text())
    require(transaction == {"stage_sha256": common.sha256(state_file)}, "GPU 暫存紀錄遭改動")
    packages, preflight = check_root(root, selected)
    require(native.kernel_evidence(root, BOARD, selected, packages) == state["kernel"],
            "GPU 整合期間核心或核心套件變更")
    installed = {row["package"]: row for row in packages}
    checked_records, checked_payloads = [], []
    for key in GPU_IDS:
        item = lock["packages"][key]
        actual = installed.get(item["Package"], {})
        require(actual.get("version") == item["Version"] and actual.get("architecture") == item["Architecture"],
                "GPU 套件尚未完成安裝或版本／架構不符：" + key)
        path = common.regular(target / Path(item["Filename"]).name)
        checked_records.append(package_record(path, item))
        checked_payloads.extend(payload_records(path, item["Package"]))
    require(checked_records == state["packages"] and checked_payloads == state["payloads"],
            "GPU 套件或載荷紀錄與暫存階段不符")
    require(not preflight["pending_dependencies"], "GPU 安裝後仍有未滿足的相依或反向相依條件")
    for record in checked_payloads:
        path = native.root_file(root, record["path"])
        require(path.stat().st_size == record["bytes"] and common.sha256(path) == record["sha256"],
                "GPU 已安裝載荷與官方 DEB 不符：" + record["path"])
        if record["kind"] == "elf":
            with path.open("rb") as stream:
                elf_check(stream.read(64), record["path"])
    env_path, original = environment(root)
    lines = original.splitlines(keepends=True)
    before_count = state["sdl_wayland_lines_before"]
    require(isinstance(before_count, int) and before_count >= 0, "SDL 環境快照不符")
    remove_count = max(0, sum(line.rstrip("\r\n") == SDL_LINE for line in lines) - before_count)
    remaining = remove_count
    for index in range(len(lines) - 1, -1, -1):
        if remaining and lines[index].rstrip("\r\n") == SDL_LINE:
            del lines[index]
            remaining -= 1
    lines = [line for line in lines if not acceleration.environment_values(line, "MESA_LOADER_DRIVER_OVERRIDE")]
    content = "".join(lines)
    content += ("" if not content or content.endswith("\n") else "\n") + "MESA_LOADER_DRIVER_OVERRIDE=pvr\n"
    manifest = native.writable_path(root, MANIFEST)
    preferences = native.writable_path(root, PREFERENCES)
    require(not manifest.exists() and not preferences.exists(), "GPU 完成紀錄已存在，拒絕重複寫入")
    result = {"schema_version": 1, "status": "installed", "board": "bananapicm6",
              "lock_sha256": state["lock_sha256"], "kernel": state["kernel"],
              "packages": checked_records, "payloads": checked_payloads,
              "verified_sources": state["verified_sources"],
              "removed_package_sdl_wayland_lines": remove_count,
              "hardware_validation": "pending", "limitations": LIMITATIONS}
    common.write(env_path, content, env_path.stat().st_mode & 0o777 if env_path.exists() else 0o644)
    common.write(preferences, acceleration.pin_preferences([lock["packages"][key] for key in GPU_IDS]))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    native.write_json(manifest, result)
    manifest.chmod(0o644)
    native.write_json(work / "finish.json", result)
    shutil.rmtree(target)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("-h", "--help", action="help", help="顯示使用說明")
    commands = parser.add_subparsers(dest="command", required=True, title="操作")
    for name, description in (("stage", "核對官方來源並準備七個 GPU 套件"), ("finish", "核對安裝結果並設定 PVR")):
        item = commands.add_parser(name, help=description, add_help=False)
        item.add_argument("-h", "--help", action="help", help="顯示使用說明")
        item.add_argument("--rootfs", required=True, type=Path, help="Armbian 建置中的根系統目錄")
        item.add_argument("--work-dir", required=True, type=Path, help="根系統以外的本次工作目錄")
        if name == "stage":
            item.add_argument("--deb-cache", required=True, type=Path, help="已有官方索引與七個 DEB 的快取")
    args = parser.parse_args()
    try:
        result = stage(args.rootfs, args.work_dir, args.deb_cache) if args.command == "stage" else finish(args.rootfs, args.work_dir)
    except (ValueError, OSError, KeyError, acceleration.AuditError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print("標準 CM6 GPU 整合失敗：" + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
