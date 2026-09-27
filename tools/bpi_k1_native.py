#!/usr/bin/env python3
"""核對 K1 原生建置配置、取得配套並封存本次 Armbian 根系統。"""

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
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import bpi_k1_acceleration as acceleration
import fetch_bpi_k1_vendor_reference as reference
import prepare_bpi_k1_vendor_rootfs as legacy
import bpi_k1_native_rootfs as integration
import bpi_k1_board_targets as targets
import bpi_cm6_gpio as gpio_packages

PROFILE_LOCK = "config/spacemit-k1-profiles/native.lock.json"
ACCELERATION_LOCK = "config/spacemit-k1-acceleration/noble.lock.json"
VENDOR_LOCK = "config/spacemit-k1-vendor/sources.lock.json"
BLUETOOTH_LOCK = "config/spacemit-k1-connectivity/source-lock.json"
CAMERA_LOCK = "config/spacemit-k1-camera/source-lock.json"


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return legacy.sha256(path)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def capture(argv, **kwargs):
    return subprocess.check_output(list(map(str, argv)), text=True, **kwargs)


def run(argv):
    subprocess.run(list(map(str, argv)), check=True, stdout=sys.stderr)


def configuration(board, branch, release, desktop, tier, outputs, release_id,
                  camera=None, card_device="", build_desktop="yes", build_minimal="no"):
    require(not card_device, "官方格式建置不得指定 CARD_DEVICE；燒錄須另行執行")
    lock = json.loads((ROOT / PROFILE_LOCK).read_text())
    target = targets.target_for(board)
    base_board = targets.resolve_board(board)
    require(base_board in lock["profiles"], "官方格式僅接受已登錄的 CM6／F3 板名")
    profile = lock["profiles"][base_board]
    camera = (target["camera_profile"] if target else "none") if camera is None else camera
    require(branch == profile["branch"], "核心分支與固定板型配套不符")
    require(release == lock["release"] == "noble", "目前只支援 Noble")
    require(desktop == lock["desktop"] == "gnome" and tier == lock["desktop_tier"] == "minimal",
            "目前只支援 GNOME minimal 配置")
    require(build_desktop == "yes" and build_minimal == "no", "必須明確選用桌面建置")
    require(camera in profile["camera_profiles"], "相機配置不適用此板型")
    require(re.fullmatch(r"[0-9]{8}-rc[1-9][0-9]*", release_id), "版本號須為 YYYYMMDD-rcN")
    selected = outputs.split(",")
    require(selected and len(selected) == len(set(selected)) and set(selected) <= {"sd", "emmc"},
            "輸出只能為 sd、emmc 或 sd,emmc，不能重複")
    if target:
        require(target["board"] == profile["board"] and branch == target["branch"],
                "官方格式別名與實體板、核心分支不符")
        require(selected == [target["storage"]], "官方格式別名只能產生其指定媒體")
        require(camera == target["camera_profile"], "官方格式別名的相機配套不符")
    accel = acceleration.load_lock(ROOT / ACCELERATION_LOCK)
    pairing = accel["profiles"][profile["board"]]
    for key, field in (("kernel_commit", "kernel_revision"), ("kernel_release", "kernel_release"),
                       ("kernel_pvr", "kernel_pvr")):
        require(profile[key] == pairing[field], "核心與加速來源鎖不一致：" + key)
    for name, item in lock["camera_packages"].items():
        require({k: item[k] for k in ("version", "size", "sha256")} == legacy.CM6_CAMERA_PACKAGES[name],
                "相機來源鎖與既有內容契約不符")
    source_locks = {name: digest(ROOT / name) for name in
                    (PROFILE_LOCK, ACCELERATION_LOCK, VENDOR_LOCK, BLUETOOTH_LOCK)}
    if camera == "dual-imx415":
        camera_lock = json.loads((ROOT / CAMERA_LOCK).read_text())
        require(camera_lock.get("board") == "bpi-cm6" and profile["board"] == "bpi-cm6" and
                camera_lock.get("camera_profile") == camera, "相機來源建置不適用此板型")
        for name, item in lock["camera_packages"].items():
            upstream = camera_lock["upstream_packages"][name]
            require(upstream["sha256"] == item["sha256"] and upstream["bytes"] == item["size"],
                    "相機建置上游 DEB 與固定官方套件不符")
        source_locks[CAMERA_LOCK] = digest(ROOT / CAMERA_LOCK)
    if target:
        source_locks[targets.REGISTRY] = digest(ROOT / targets.REGISTRY)
    if profile["board"] == "bpi-cm6":
        for path in sorted((ROOT / "config/spacemit-k1-gpio").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                source_locks[path.relative_to(ROOT).as_posix()] = digest(legacy.regular(path))
    profile_identity = {"armbian_board": board, "board": profile["board"], "branch": branch,
                        "release": release, "desktop": desktop, "tier": tier,
                        "camera_profile": camera, "profile": profile, "source_locks": source_locks}
    fingerprint = hashlib.sha256(canonical(profile_identity)).hexdigest()
    # 下載／編譯配套只綁定自己的生成輸入；封裝工具改版不用重新下載所有資產。
    asset_candidates = [ROOT / "tools" / name for name in
                        ("fetch_bpi_k1_vendor_reference.py", "bpi_k1_acceleration.py", "bpi_k1_board_targets.py")]
    asset_candidates.append(ROOT / targets.REGISTRY)
    if profile["board"] == "bpi-cm6":
        asset_candidates.extend(ROOT / "tools" / item[3] for item in gpio_packages.RECIPES.values())
        asset_candidates.append(ROOT / "tools/bpi_cm6_gpio.py")
        asset_candidates.extend(path for path in (ROOT / "config/spacemit-k1-gpio").rglob("*")
                                if path.is_file() and "__pycache__" not in path.parts)
        asset_candidates.extend(ROOT / "tools" / name for name in
                                ("build_bpi_cm6_bluetooth.py", "package_bpi_cm6_bluetooth.py"))
        for path in (ROOT / "config/spacemit-k1-connectivity").rglob("*"):
            if "__pycache__" in path.parts:
                continue
            require(not path.is_symlink() and (path.is_file() or path.is_dir()),
                    "連線配套輸入不得包含連結或特殊檔案")
            if path.is_file():
                asset_candidates.append(path)
    if camera == "dual-imx415":
        asset_candidates.append(ROOT / "tools/build_bpi_cm6_camera.py")
        asset_candidates.extend(path for path in (ROOT / "config/spacemit-k1-camera").rglob("*")
                                if path.is_file() and "__pycache__" not in path.parts)
    asset_inputs = {path.relative_to(ROOT).as_posix(): digest(legacy.regular(path))
                    for path in sorted(set(asset_candidates))}
    asset_fingerprint = hashlib.sha256(canonical({"profile_sha256": fingerprint,
                                                "inputs": asset_inputs})).hexdigest()
    # 舊的基礎 rootfs 可共用下載來源；整合程式與 DTS 變更必須另計快取身分。
    inputs = {}
    candidates = [ROOT / "tools" / name for name in
                  ("bpi_k1_native.py", "bpi_k1_native_rootfs.py", "bpi_k1_native_evidence.py", "bpi_k1_acceleration.py", "bpi_k1_board_targets.py",
                   "prepare_bpi_k1_vendor_rootfs.py", "bpi_k1_desktop.py", "bpi_k1_host_dependencies.py",
                   "package_bpi_k1_vendor.py", "bpi_k1_vendor_policy.py", "bpi_cm6_native_dtb.py",
                   "collect_bpi_k1_runtime.py", "build_bpi_cm6_bluetooth.py",
                   "package_bpi_cm6_bluetooth.py", "fetch_bpi_k1_vendor_reference.py")]
    candidates.extend(ROOT / name for name in
                      ("lib/functions/configuration/config-desktop.sh",
                       "lib/functions/main/config-prepare.sh",
                       "lib/functions/host/basic-deps.sh",
                       "lib/functions/artifacts/artifact-rootfs.sh",
                       "lib/functions/rootfs/rootfs-create.sh"))
    for directory in ("config/spacemit-k1-profiles", "config/spacemit-k1-connectivity", "extensions/bpi-k1-vendor"):
        candidates.extend((ROOT / directory).rglob("*"))
    # 別名的載入器、被引用的實體板與共用來源設定同屬建置輸入。
    candidates.extend((ROOT / "config/boards").glob("bananapicm6*"))
    candidates.extend((ROOT / "config/boards").glob("bananapif3*"))
    candidates.extend(ROOT / name for name in
                      ("config/boards/include/bpi-k1-board-targets.inc",
                       "config/sources/families/spacemit.conf"))
    if profile["board"] == "bpi-cm6":
        candidates.extend(ROOT / "tools" / item[3] for item in gpio_packages.RECIPES.values())
        candidates.append(ROOT / "tools/bpi_cm6_gpio.py")
        candidates.extend((ROOT / "config/spacemit-k1-gpio").rglob("*"))
    for directory in profile.get("kernel_patch_directories", []):
        candidates.extend((ROOT / "patch/kernel" / directory).rglob("*.patch"))
    if camera == "dual-imx415":
        candidates.append(ROOT / "tools/build_bpi_cm6_camera.py")
        candidates.extend((ROOT / "config/spacemit-k1-camera").rglob("*"))
        candidates.extend((ROOT / "patch/kernel" / profile["camera_patch_directory"]).rglob("*.patch"))
    for path in sorted(set(candidates)):
        if path.is_file() and "__pycache__" not in path.parts:
            inputs[path.relative_to(ROOT).as_posix()] = digest(path)
    cache_fingerprint = hashlib.sha256(canonical({"profile": profile_identity, "inputs": inputs})).hexdigest()
    return {"schema_version": 1, **profile_identity, "outputs": sorted(selected),
            "release_id": release_id, "profile_sha256": fingerprint,
            "asset_inputs": asset_inputs, "asset_sha256": asset_fingerprint,
            "build_inputs": inputs, "cache_sha256": cache_fingerprint,
            "source_kind": "armbian-native-rootfs", "hardware_validation": "pending"}


def read_configuration(path):
    saved = json.loads(Path(path).read_text())
    current = configuration(saved["armbian_board"], saved["branch"], saved["release"],
                            saved["desktop"], saved["tier"], ",".join(saved["outputs"]),
                            saved["release_id"], saved["camera_profile"])
    require(saved == current, "配置或來源鎖在建置期間變更，請重新開始")
    return current


def file_records(directory):
    records = {}
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode), "配套快取不得包含符號連結或特殊檔案")
        if stat.S_ISREG(mode) and path != directory / "assets.json":
            records[path.relative_to(directory).as_posix()] = {"size": path.stat().st_size, "sha256": digest(path)}
    return records


def verify_assets(path, config):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink(), "配套快取必須為一般目錄")
    record = json.loads(legacy.regular(path / "assets.json").read_text())
    require(record.get("schema_version") == 1 and record.get("board") == config["board"],
            "配套快取格式或板型不符")
    require(record.get("profile_sha256") == config["profile_sha256"], "來源配套快取身分不符")
    require(record.get("asset_sha256") == config["asset_sha256"], "來源配套生成工具或輸入已變更，請使用新的資產快取")
    require(record.get("files") == file_records(path), "來源配套快取缺件或內容已變更")
    return record


def prepare_assets(config_path, output):
    config = read_configuration(config_path)
    output = Path(output).absolute()
    require(not output.is_symlink(), "配套目錄不能是符號連結")
    if output.exists():
        return verify_assets(output, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".k1-assets-", dir=output.parent) as temporary:
        work = Path(temporary) / "assets"
        work.mkdir()
        reference.fetch(ROOT / VENDOR_LOCK, work / "vendor-reference")
        accel = acceleration.load_lock(ROOT / ACCELERATION_LOCK)
        acceleration.prepare(accel, ROOT / ACCELERATION_LOCK, config["board"],
                             work / "deb-cache", work / "acceleration")
        if config["board"] == "bpi-cm6":
            for directory, recipe in gpio_packages.RECIPES.items():
                run([sys.executable, ROOT / "tools" / recipe[3], "build",
                     "--cache", ROOT / "cache/bpi-cm6-gpio" / directory,
                     "--output", work / "gpio-cache" / directory])
            gpio_packages.package_records(work / "gpio-cache")
            run([sys.executable, ROOT / "tools/build_bpi_cm6_bluetooth.py", "--output", work / "bluetooth-build"])
            run([sys.executable, ROOT / "tools/package_bpi_cm6_bluetooth.py",
                 "--build-root", work / "bluetooth-build", "--output", work / "bluetooth-package"])
            if config["camera_profile"] == "dual-imx415":
                camera_inputs = ROOT / "cache/bpi-cm6-camera-debs"
                run([sys.executable, ROOT / "tools/build_bpi_cm6_camera.py", "fetch", "--inputs", camera_inputs])
                run([sys.executable, ROOT / "tools/build_bpi_cm6_camera.py", "build",
                     "--board", "bpi-cm6", "--profile", "dual-imx415",
                     "--inputs", camera_inputs, "--output", work / "camera-cache"])
        record = {"schema_version": 1, "profile_sha256": config["profile_sha256"],
                  "asset_sha256": config["asset_sha256"],
                  "board": config["board"], "files": file_records(work),
                  "hardware_validation": "pending"}
        save(work / "assets.json", record)
        work.rename(output)
    return verify_assets(output, config)


def source_snapshot():
    """保存實際使用的受控來源與未提交程式，記錄只使用倉庫相對路徑。"""
    names = capture(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).split("\0")
    records = {}
    for name in sorted(set(names) - {""}):
        path = ROOT / name
        if path.is_symlink():
            records[name] = {"symlink": str(path.readlink())}
        elif path.is_file():
            records[name] = {"sha256": digest(path)}
    return {"git_commit": capture(["git", "rev-parse", "HEAD"], cwd=ROOT).strip(),
            "tree_sha256": hashlib.sha256(canonical(records)).hexdigest(), "files": records}


def tree_inventory(root):
    """以檔案內容、權限、所有權及連結識別共用樹；不跟隨符號連結。"""
    root = Path(root)
    records = {}
    hardlinks = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        entry = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
        if stat.S_ISLNK(info.st_mode):
            entry.update(type="symlink", target=str(path.readlink()))
        elif stat.S_ISDIR(info.st_mode):
            entry.update(type="directory")
        elif stat.S_ISREG(info.st_mode):
            entry.update(type="file", size=info.st_size, sha256=digest(path))
            key = (info.st_dev, info.st_ino)
            if info.st_nlink > 1:
                if key in hardlinks:
                    entry["hardlink"] = hardlinks[key]
                else:
                    hardlinks[key] = name
        else:
            raise ValueError("根系統封存不得含裝置、socket 或 FIFO：" + name)
        attributes = {}
        for key in sorted(os.listxattr(path, follow_symlinks=False)):
            attributes[key] = hashlib.sha256(os.getxattr(path, key, follow_symlinks=False)).hexdigest()
        if attributes:
            entry["xattrs"] = attributes
        records[name] = entry
    return records


def export_rootfs(config_path, rootfs, integration_dir, output):
    """擷取本次框架完成的樹，產生既有封裝器可驗證的新來源契約。"""
    require(os.geteuid() == 0, "保留根系統所有權需管理員權限")
    config = read_configuration(config_path)
    rootfs = integration.root_directory(rootfs)
    require(not Path(output).is_symlink(), "封存輸出不能是符號連結")
    output = Path(output).resolve()
    require(rootfs != Path("/") and (rootfs / "etc/armbian-release").is_file(), "必須指定本次 Armbian 根系統")
    require(not output.exists() and not output.is_symlink() and not output.is_relative_to(rootfs),
            "封存輸出必須為根系統外的全新目錄")
    integration_dir = integration.root_directory(integration_dir)
    integrated = json.loads(legacy.regular(integration_dir / "integration.json").read_text())
    require(integrated.get("schema_version") == 1 and
            integrated.get("source_kind") == "armbian-native-rootfs" and
            integrated.get("status") == "complete", "原生根系統整合格式不符或尚未完成")
    require(integrated["identity"]["board"] == config["board"], "整合記錄板型不符")
    require(integrated["identity"].get("armbian_board", legacy.BOARDS[config["board"]][0]) ==
            config["armbian_board"], "整合記錄的完整 Armbian 板名與配置不符")
    require(integrated["identity"]["acceleration_lock_sha256"] == config["source_locks"][ACCELERATION_LOCK],
            "整合與封存的配套鎖不同")
    accel = acceleration.load_lock(ROOT / ACCELERATION_LOCK)
    marker = json.loads(integration.root_file(rootfs, "etc/bpi-k1-native.json").read_text())
    require(marker == {**integrated["identity"], "desktop": "gnome-wayland", "hardware_validation": "pending"},
            "根系統身分與本次整合記錄不符")
    require(integrated["identity"].get("source_kind") == "armbian-native-rootfs" and
            integrated["identity"].get("build_id"), "整合記錄缺少原生建置識別")
    inventory_path = legacy.regular(integration_dir / "installed-packages.json")
    recorded_inventory = integrated["installed_packages"]
    require(recorded_inventory.get("file") == "installed-packages.json" and
            recorded_inventory.get("sha256") == digest(inventory_path), "已安裝套件清單遭到變更")
    packages = integration.validate_root(rootfs, config["board"], accel, config["armbian_board"])
    expected_inventory = {"schema_version": 1, "count": len(packages), "packages": packages,
                          "dpkg_status_sha256": digest(integration.root_file(rootfs, "var/lib/dpkg/status"))}
    require(json.loads(inventory_path.read_text()) == expected_inventory and
            recorded_inventory.get("count") == len(packages), "根系統套件已偏離本次整合記錄")
    require(integration.kernel_evidence(rootfs, config["board"], accel, packages) == integrated["kernel"],
            "根系統核心已偏離本次整合記錄")
    preflight = acceleration.preflight(accel, config["board"], rootfs, "installed", config["armbian_board"])
    require(preflight["passed"], "封存前預檢失敗：" + "; ".join(preflight["errors"]))
    output.mkdir(parents=True)
    state = {"schema_version": 2, "status": "preparing", "hardware_validation": "pending"}
    save(output / "preparation.json", state)
    try:
        tree = output / "root-tree"
        tree.mkdir()
        excluded = ["/dev/*", "/proc/*", "/sys/*", "/run/*", "/tmp/*"]
        run(["rsync", "-aHAX", "--numeric-ids", *("--exclude=" + item for item in excluded),
             str(rootfs) + "/", str(tree) + "/"])
        run(["rsync", "-aHAX", "--numeric-ids", str(tree / "boot") + "/", str(output / "boot-tree") + "/"])
        for path in (output / "boot-tree").rglob("*"):
            if path.is_symlink() and str(path.readlink()).startswith("/boot/"):
                target = str(path.readlink())[6:]
                path.unlink()
                path.symlink_to(os.path.relpath(output / "boot-tree" / target, path.parent))
        dtb = output / "boot-tree" / "dtb" / config["profile"]["dtb"]
        require(dtb.resolve().is_relative_to(output / "boot-tree") and dtb.is_file(), "根系統缺少板型 DTB")
        native_dtb = {"sha256": digest(dtb), "path": "/dtb/" + config["profile"]["dtb"]}
        if config["board"] == "bpi-cm6":
            import bpi_cm6_native_dtb
            native_dtb["validation"] = bpi_cm6_native_dtb.validate(dtb, config["camera_profile"])
        inventory = tree_inventory(tree)
        snapshot = source_snapshot()
        root_identity = {"source_kind": "armbian-native-rootfs", "schema_version": 2,
                         "board": config["board"], "camera_profile": config["camera_profile"],
                         "profile_sha256": config["profile_sha256"],
                         "rootfs_tree_sha256": hashlib.sha256(canonical(inventory)).hexdigest(),
                         "build_source_sha256": snapshot["tree_sha256"],
                         "source_commit": snapshot["git_commit"],
                         "acceleration_lock_sha256": config["source_locks"][ACCELERATION_LOCK],
                         "native_dtb": native_dtb}
        if targets.target_for(config["armbian_board"]):
            root_identity["armbian_board"] = config["armbian_board"]
        for field in ("cm6_bluetooth_package_sha256", "cm6_camera_packages", "cm6_gpio_packages"):
            if field in integrated["identity"]:
                root_identity[field] = integrated["identity"][field]
        size = int(capture(["du", "--apparent-size", "--block-size=1", "-s", tree]).split()[0])
        size_mib = max(2048, (size * 13 // 10 + 1024**3 + 1024**2 - 1) // 1024**2)
        require(size_mib <= 65536, "根系統大小超過目前受控的 64 GiB 上限")
        image = output / "rootfs.ext4"
        with image.open("xb") as stream:
            stream.truncate(size_mib * 1024**2)
        run(["mkfs.ext4", "-q", "-F", "-L", "armbian-native", "-d", tree, image])
        run(["e2fsck", "-fn", image])
        save(output / "rootfs-content.json", inventory)
        save(output / "source-snapshot.json", snapshot)
        save(output / "native-config.json", config)
        save(output / "acceleration-preflight.json", preflight)
        shutil.copy2(tree / "var/lib/dpkg/status", output / "dpkg-status")
        shutil.copy2(integration_dir / "integration.json", output / "integration.json")
        shutil.copy2(integration_dir / "installed-packages.json", output / "installed-packages.json")
        state.update(status="complete", identity=root_identity, rootfs_sha256=digest(image),
                     kernel=config["profile"]["kernel_release"], desktop="gnome-wayland",
                     boot_tree=tree_inventory(output / "boot-tree"))
        save(output / "preparation.json", state)
        return state
    except BaseException:
        state["status"] = "failed"
        save(output / "preparation.json", state)
        raise


class ChineseParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "參數不完整或無法辨識，請使用 --help 核對。\n")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1).replace("options:", "選項：")


def main():
    parser = ChineseParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, title="命令")
    cfg = sub.add_parser("config", help="唯讀核對原生配置")
    for name in ("board", "branch", "release", "desktop", "tier", "outputs", "release-id"):
        cfg.add_argument("--" + name, required=True)
    cfg.add_argument("--camera", default=None)
    cfg.add_argument("--card-device", default="")
    cfg.add_argument("--build-desktop", default="yes")
    cfg.add_argument("--build-minimal", default="no")
    cfg.add_argument("--output", type=Path)
    assets = sub.add_parser("assets", help="取得並校驗固定配套")
    assets.add_argument("--config", type=Path, required=True)
    assets.add_argument("--output", type=Path, required=True)
    export = sub.add_parser("export", help="封存本次根系統，產生原生來源契約")
    for name in ("config", "rootfs", "integration-dir", "output"):
        export.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "config":
            value = configuration(args.board, args.branch, args.release, args.desktop, args.tier,
                                  args.outputs, args.release_id, args.camera, args.card_device,
                                  args.build_desktop, args.build_minimal)
            if args.output:
                save(args.output, value)
        elif args.command == "assets":
            value = prepare_assets(args.config, args.output)
        else:
            value = export_rootfs(args.config, args.rootfs, args.integration_dir, args.output)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, acceleration.AuditError) as exc:
        print("原生建置處理失敗：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
