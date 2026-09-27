#!/usr/bin/env python3
"""為 CM6 標準 rootfs 準備固定來源，並核對目標內建置結果；不操作硬體。"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import urllib.request

import bpi_k1_native_rootfs as native
import build_bpi_cm6_bluetooth as bt
import package_bpi_cm6_bluetooth as bt_package

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/spacemit-k1-standard"
EXPECTED = {"jammy": ("ubuntu", "3.10"), "noble": ("ubuntu", "3.12"), "trixie": ("debian", "3.13")}
require = native.require
digest = native.digest


def release_info(release):
    require(release != "resolute", "CM6 K1 為 RVA22；Ubuntu Resolute 要求 RVA23，拒絕產生不相容成品")
    require(release in EXPECTED, "CM6 標準配套未核定此發行版")
    return EXPECTED[release]


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "來源準備目錄不得含符號連結")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return result


def fetch(cache, url, expected):
    require(url.startswith("https://") and len(expected) == 64, "來源網址或 SHA 不合法")
    path = cache / expected
    if not path.exists():
        pending = path.with_suffix(".pending")
        with urllib.request.urlopen(url, timeout=120) as response:
            pending.write_bytes(response.read())
        require(digest(pending) == expected, "固定來源下載 SHA 不符")
        pending.replace(path)
    require(path.is_file() and not path.is_symlink() and digest(path) == expected, "來源快取 SHA 不符")
    return path


def extract(archive, destination):
    destination.mkdir()
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        require(len(members) < 10000, "來源封存項目過多")
        seen = set()
        for item in members:
            name = PurePosixPath(item.name)
            require(not name.is_absolute() and ".." not in name.parts and name.parts,
                    "來源封存路徑越界")
            require(item.isdir() or item.isfile(), "來源封存含連結或特殊檔案")
            require(item.name not in seen and 0 <= item.size <= 32 * 1024 * 1024, "來源封存重複或過大")
            seen.add(item.name)
        stream.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    require(len(roots) == 1 and roots[0].is_dir(), "來源封存根目錄不唯一")
    return roots[0]


def patch(source, config, record):
    path = config / record["path"]
    require(path.is_file() and digest(path) == record["sha256"], "補丁 SHA 不符")
    subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(path)],
                   cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def prepare(release, cache, output):
    release_info(release)
    require(release in {"jammy", "trixie"}, "此工具只為非 Noble 目標準備原生建置來源")
    require(not output.exists(), "來源輸出已存在，拒絕覆寫")
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True)
    recipe = json.loads((CONFIG / "native-build.lock.json").read_text())
    locks = {}
    for name in ("wiringpi", "rpi-gpio"):
        config = ROOT / "config/spacemit-k1-gpio" / name
        lock_path = config / "source-lock.json"
        lock = json.loads(lock_path.read_text())
        expected_commit = recipe["source_commits"][name]
        require(lock["source"]["commit"] == expected_commit, "GPIO 固定來源提交不符")
        src = lock["source"]
        archive = fetch(cache, src.get("url", src.get("archive_url")), src["archive_sha256"])
        temporary = output / (name + "-extract")
        tree = extract(archive, temporary)
        tree.rename(output / name)
        temporary.rmdir()
        patches = lock.get("patches", src.get("patches", []))
        patch_destination = output / "source-patches" / name
        patch_destination.mkdir(parents=True)
        for item in patches:
            patch(output / name, config, item)
            shutil.copyfile(config / item["path"], patch_destination / item["path"])
        if name == "rpi-gpio":
            for item in recipe["python_patches"]:
                patch(output / name, CONFIG, item)
                shutil.copyfile(CONFIG / item["path"], patch_destination / item["path"])
            patches = patches + recipe["python_patches"]
        locks[name] = {"upstream_lock_sha256": digest(lock_path), "source": src,
                       "patches": patches, "version": recipe["versions"][name] + "+" + release}
        shutil.copyfile(archive, output / (name + "-source.tar.gz"))
        shutil.copyfile(lock_path, output / (name + "-source-lock.json"))
    lock_path = ROOT / "config/spacemit-k1-connectivity/source-lock.json"
    lock = json.loads(lock_path.read_text())
    require(lock["source"]["commit"] == recipe["source_commits"]["bluetooth"], "藍牙固定來源提交不符")
    archive = fetch(cache, lock["source"]["url"], lock["source"]["sha256"])
    firmware = fetch(cache, lock["firmware_source"]["url"], lock["firmware_source"]["sha256"])
    source = output / "bluetooth"
    source.mkdir()
    for name, data in bt.validate_archive(archive.read_bytes(), lock["source"]).items():
        (source / name).write_bytes(data)
    bt.apply_patches(source, output, bt.validate_patches(lock, lock_path.parent))
    (output / "firmware").mkdir()
    for name, data in bt.validate_firmware_archive(firmware.read_bytes(), lock).items():
        (output / "firmware" / name).write_bytes(data)
    shutil.copyfile(archive, output / "bluetooth-source.tar.gz")
    shutil.copyfile(firmware, output / "firmware-source.tar.xz")
    shutil.copyfile(lock_path, output / "bluetooth-source-lock.json")
    for name in ("bpi-cm6-bluetooth", "bpi-cm6-bluetooth.service"):
        shutil.copyfile(lock_path.parent / name, output / name)
    scripts = output / "bluetooth-maintainer"
    scripts.mkdir()
    for name in ("PREINST", "POSTINST", "PRERM", "POSTRM"):
        (scripts / name.lower()).write_text(getattr(bt_package, name))
    locks["bluetooth"] = {"upstream_lock_sha256": digest(lock_path), "source": lock["source"],
                          "patches": lock["patches"], "firmware_source": lock["firmware_source"],
                          "version": recipe["versions"]["bluetooth"] + "+" + release}
    builder = ROOT / "tools/bpi_cm6_standard_target.py"
    shutil.copyfile(builder, output / builder.name)
    shutil.copyfile(CONFIG / "native-build.lock.json", output / "native-build.lock.json")
    state = {"schema_version": 1, "release": release, "python_abi": EXPECTED[release][1],
             "board": "bananapicm6", "architecture": "riscv64", "sources": locks,
             "builder_sha256": digest(builder), "host_builder_sha256": digest(__file__),
             "lock_sha256": digest(CONFIG / "native-build.lock.json"), "inputs": inventory(output)}
    (output / "source-manifest.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {"status": "prepared", "release": release, "source_manifest_sha256": digest(output / "source-manifest.json")}


def validate_root(root, release):
    system, abi = release_info(release)
    values = native.acceleration.environment_values
    text = native.root_file(root, "etc/os-release").read_text()
    require(values(text, "ID") == [system] and values(text, "VERSION_CODENAME") == [release], "目標發行版不符")
    require(values(native.root_file(root, "etc/armbian-release").read_text(), "BOARD") == ["bananapicm6"],
            "僅支援標準 bananapicm6，拒絕改動官方媒體別名")
    packages = native.installed_packages(root)
    require(any(p["package"] == "libc6" and p["architecture"] == "riscv64" for p in packages), "根系統架構不符")
    return abi


def stage(root, release, prepared, work):
    root = native.root_directory(root)
    validate_root(root, release)
    require(not work.exists() and not work.resolve().is_relative_to(root), "階段紀錄須位於 rootfs 外且不存在")
    state = json.loads((prepared / "source-manifest.json").read_text())
    require(state["release"] == release, "準備來源發行版不符")
    relative = "var/tmp/cm6-standard-" + digest(prepared / "source-manifest.json")[:16]
    target = native.writable_path(root, relative)
    require(not target.exists(), "目標暫存目錄已存在")
    work.mkdir(parents=True)
    shutil.copytree(prepared, target)
    require(inventory(target) == inventory(prepared), "複製後來源清單不同")
    record = {"root_binding": native.binding(root), "release": release, "relative": relative,
              "source_manifest_sha256": digest(prepared / "source-manifest.json"),
              "host_builder_sha256": digest(__file__), "lock_sha256": digest(CONFIG / "native-build.lock.json")}
    native.write_json(work / "stage.json", record)
    return {"argv": ["python3", "/" + relative + "/bpi_cm6_standard_target.py", "/" + relative]}


def finish(root, work):
    root = native.root_directory(root)
    stage = json.loads((work / "stage.json").read_text())
    require(stage["root_binding"] == native.binding(root) and stage["host_builder_sha256"] == digest(__file__),
            "兩階段根系統或工具不同")
    validate_root(root, stage["release"])
    target = native.writable_path(root, stage["relative"])
    require(digest(target / "source-manifest.json") == stage["source_manifest_sha256"], "階段來源紀錄遭變更")
    require(stage["lock_sha256"] == digest(CONFIG / "native-build.lock.json"), "建置配方遭變更")
    source = json.loads((target / "source-manifest.json").read_text())
    require(source["builder_sha256"] == digest(ROOT / "tools/bpi_cm6_standard_target.py"), "目標建置工具不同")
    result = json.loads((target / "output/result.json").read_text())
    require(result["status"] == "complete" and result["source_manifest_sha256"] == stage["source_manifest_sha256"],
            "目標內建置未完整完成")
    installed = native.installed_packages(root)
    require({p["package"] for p in result["packages"]} == {"bpi-cm6-wiringpi", "python3-bpi-cm6-gpio", "bpi-cm6-bluetooth"}
            and len(result["packages"]) == 3, "目標產生的套件集合不符")
    for record in result["packages"]:
        deb = target / "output" / record["artifact"]
        require(digest(deb) == record["sha256"], "DEB SHA 不符")
        require(record["builder_sha256"] == source["builder_sha256"] and
                record["lock_sha256"] == source["lock_sha256"], "DEB 建置配方追溯不符")
        require(any(p["package"] == record["package"] and p["version"] == record["version"] and
                    p["architecture"] == "riscv64" for p in installed), "DEB 未正常安裝")
        for item in record["payload_files"]:
            path = native.writable_path(root, item["path"].lstrip("/"), leaf_link=True)
            if item.get("type") == "symlink":
                require(path.is_symlink() and os.readlink(path) == item["target"], "已安裝連結不符")
            else:
                require(path.is_file() and not path.is_symlink() and digest(path) == item["sha256"] and
                        path.stat().st_size == item["bytes"] and path.stat().st_mode & 0o777 == int(item["mode"], 8),
                        "已安裝內容或模式不符")
    bluetooth = next(p for p in result["packages"] if p["package"] == "bpi-cm6-bluetooth")
    lock = ROOT / "config/spacemit-k1-connectivity/source-lock.json"
    identity = {"board": "bpi-cm6", "cm6_bluetooth_package_sha256": bluetooth["sha256"],
                "connectivity_lock_sha256": digest(lock)}
    result["bluetooth_initial_policy"] = native.seed_bluetooth_initial_policy(root, "bpi-cm6", identity)
    link = native.writable_path(root, "etc/systemd/system/multi-user.target.wants/bpi-cm6-bluetooth.service", leaf_link=True)
    require(link.is_symlink() and native.acceleration.rooted_path(root, "/" + str(link.relative_to(root))) ==
            native.root_file(root, "usr/lib/systemd/system/bpi-cm6-bluetooth.service"), "藍牙服務未啟用或目標錯誤")
    shutil.copytree(target / "output", work / "output")
    receipt = native.writable_path(root, "usr/share/bpi-cm6-standard/integration.json")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    native.write_json(receipt, result)
    shutil.rmtree(target)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "stage", "finish", "check-release"))
    parser.add_argument("--release")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.release, args.cache, args.output)
    elif args.command == "stage":
        result = stage(args.rootfs, args.release, args.prepared, args.work)
    elif args.command == "finish":
        result = finish(args.rootfs, args.work)
    else:
        result = release_info(args.release)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
