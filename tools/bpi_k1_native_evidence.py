#!/usr/bin/env python3
"""將原生建置證據與已封裝成品綁定，原子發布至板型輸出目錄。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpi_k1_board_targets as targets

FILES = ("preparation.json", "source-snapshot.json", "native-config.json", "rootfs-content.json",
         "installed-packages.json", "integration.json", "acceleration-preflight.json", "dpkg-status")
PROFILE_FIELDS = ("armbian_board", "board", "branch", "release", "desktop", "tier",
                  "camera_profile", "profile", "source_locks")
SOURCE_KIND = "armbian-native-rootfs"
ACCELERATION_LOCK = "config/spacemit-k1-acceleration/noble.lock.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def checked_path(value, directory=False):
    """包含父目錄在內一律拒絕連結，避免白名單經中間目錄越界。"""
    path = Path(value)
    require(".." not in path.parts, "路徑不可包含上層目錄")
    path = Path(os.path.abspath(path))
    require(path != Path("/"), "拒絕使用主機根目錄")
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        mode = current.lstat().st_mode
        expected_directory = index < len(path.parts) - 2 or directory
        require(stat.S_ISDIR(mode) if expected_directory else stat.S_ISREG(mode),
                "路徑含連結、特殊檔案或錯誤類型：" + str(current))
    return path


def file_record(path):
    path = checked_path(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), "只接受一般檔案")
        digest = hashlib.sha256()
        size = 0
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and size == after.st_size,
                "檔案於雜湊核對期間變更")
    return {"size": size, "sha256": digest.hexdigest()}


def read_file(path):
    path = checked_path(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        return stream.read()


def json_object(data, name):
    value = json.loads(data)
    require(isinstance(value, dict), name + " 必須為 JSON 物件")
    return value


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def packages_from_status(data):
    packages = []
    for block in re.split(r"\n\s*\n", data.decode().strip()):
        fields = dict(line.split(": ", 1) for line in block.splitlines()
                      if ": " in line and not line.startswith((" ", "\t")))
        status = fields.get("Status", "")
        require(not status.startswith("install ") or status == "install ok installed",
                "dpkg 證據含未完成安裝的套件")
        if status == "install ok installed":
            packages.append({"package": fields["Package"], "version": fields["Version"],
                             "architecture": fields.get("Architecture", ""),
                             "source": fields.get("Source", fields["Package"])})
    return sorted(packages, key=lambda row: (row["package"], row["architecture"]))


def validate_prepared(blobs, supplied_config):
    docs = {name: json_object(data, name) for name, data in blobs.items() if name.endswith(".json")}
    config = docs["native-config.json"]
    require(config == supplied_config, "指定配置與封存配置不同")
    require(config.get("schema_version") == 1 and config.get("source_kind") == SOURCE_KIND,
            "配置不是原生建置格式")
    board = config["board"]
    require(board in ("bpi-cm6", "bpi-f3") and config["profile"]["board"] == board and
            targets.expected_board(config["armbian_board"], board),
            "配置板型不一致")
    require(config["release"] == "noble" and config["desktop"] == "gnome" and config["tier"] == "minimal" and
            config["branch"] == config["profile"]["branch"] and
            config["camera_profile"] in config["profile"]["camera_profiles"], "配置配套不一致")
    require(re.fullmatch(r"[0-9]{8}-rc[1-9][0-9]*", config["release_id"]) is not None, "候選識別格式不符")
    outputs = config["outputs"]
    require(isinstance(outputs, list) and outputs and all(item in ("sd", "emmc") for item in outputs) and
            len(outputs) == len(set(outputs)), "配置媒體清單不符")
    target = targets.target_for(config["armbian_board"])
    if target:
        require(outputs == [target["storage"]] and config["branch"] == target["branch"] and
                config["camera_profile"] == target["camera_profile"], "官方格式別名的媒體或配套不符")
        require(valid_sha(config["source_locks"].get(targets.REGISTRY)), "官方格式別名缺少板名來源鎖")
    require(config["profile_sha256"] == sha(canonical({key: config[key] for key in PROFILE_FIELDS})),
            "配置配套摘要不符")
    prep = docs["preparation.json"]
    identity = prep["identity"]
    require(prep.get("schema_version") == 2 and prep.get("status") == "complete" and
            identity.get("schema_version") == 2 and identity.get("source_kind") == SOURCE_KIND,
            "封存必須為已完成的原生 schema 2")
    for key in ("board", "camera_profile", "profile_sha256"):
        require(identity.get(key) == config[key], "封存與配置身分不一致：" + key)
    base_board = {"bpi-cm6": "bananapicm6", "bpi-f3": "bananapif3"}[board]
    require(identity.get("armbian_board", base_board) == config["armbian_board"],
            "封存的完整 Armbian 板名與配置不符")
    kernel = config["profile"]["kernel_release"]
    require(prep.get("kernel") == kernel and prep.get("desktop") == "gnome-wayland" and
            valid_sha(prep.get("rootfs_sha256")), "封存核心或根檔案系統身分不符")
    require(identity.get("acceleration_lock_sha256") == config["source_locks"][ACCELERATION_LOCK],
            "加速配套來源鎖不一致")
    snapshot = docs["source-snapshot.json"]
    require(re.fullmatch(r"[0-9a-f]{40}", snapshot["git_commit"]) is not None and
            snapshot["git_commit"] == identity.get("source_commit") and
            sha(canonical(snapshot["files"])) == snapshot["tree_sha256"] == identity.get("build_source_sha256"),
            "來源清單、提交碼或摘要不一致")
    content = docs["rootfs-content.json"]
    require(sha(canonical(content)) == identity.get("rootfs_tree_sha256"), "根檔案系統內容摘要不符")
    integrated = docs["integration.json"]
    require(integrated.get("schema_version") == 1 and integrated.get("source_kind") == SOURCE_KIND and
            integrated.get("status") == "complete" and integrated.get("desktop") == "gnome-wayland",
            "根系統整合尚未完成或格式不符")
    integration_identity = integrated["identity"]
    require(integration_identity.get("armbian_board", base_board) == config["armbian_board"],
            "整合的完整 Armbian 板名與配置不符")
    require(integration_identity.get("source_kind") == SOURCE_KIND and integration_identity.get("build_id") and
            integration_identity.get("board") == board and
            integration_identity.get("acceleration_lock_sha256") == identity["acceleration_lock_sha256"],
            "根系統整合身分不符")
    for key in ("cm6_bluetooth_package_sha256", "cm6_camera_packages"):
        require(integration_identity.get(key) == identity.get(key), "根系統板級配套身分不一致：" + key)
    inventory = docs["installed-packages.json"]
    record = integrated["installed_packages"]
    require(inventory.get("schema_version") == 1 and inventory.get("count") == len(inventory["packages"]) and
            record == {"file": "installed-packages.json", "count": inventory["count"],
                       "sha256": sha(blobs["installed-packages.json"])} and
            inventory.get("dpkg_status_sha256") == sha(blobs["dpkg-status"]), "套件清單或 dpkg 證據不符")
    require(inventory["packages"] == packages_from_status(blobs["dpkg-status"]),
            "套件清單內容與 dpkg 證據不同")
    status_entry = content.get("var/lib/dpkg/status", {})
    require(status_entry.get("type") == "file" and status_entry.get("size") == len(blobs["dpkg-status"]) and
            status_entry.get("sha256") == inventory["dpkg_status_sha256"],
            "dpkg 證據與根系統內容摘要不符")
    kernel_record = integrated["kernel"]
    require(kernel_record.get("release") == kernel and kernel_record.get("files"), "整合核心版本或檔案證據缺少")
    expected_files = {"/boot/vmlinuz-" + kernel, "/boot/config-" + kernel}
    require(set(kernel_record["files"]) == expected_files, "核心檔案證據清單不符")
    for name, item in kernel_record["files"].items():
        actual = content.get(name.lstrip("/"), {})
        require(actual.get("type") == "file" and actual.get("size") == item["bytes"] and
                actual.get("sha256") == item["sha256"], "核心檔案與根系統摘要不符：" + name)
    expected_packages = [row for row in inventory["packages"]
                         if row["package"].startswith(("linux-image-", "linux-dtb-", "linux-headers-"))]
    require(kernel_record.get("packages") == expected_packages and expected_packages,
            "核心套件清單與整合紀錄不符")
    preflight = docs["acceleration-preflight.json"]
    require(preflight.get("armbian_board", base_board) == config["armbian_board"],
            "加速預檢的完整 Armbian 板名與配置不符")
    require(preflight.get("passed") is True and preflight.get("board") == board and
            preflight.get("stage") == "installed", "加速配套安裝預檢尚未通過")
    require(all(item.get("hardware_validation") == "pending" for item in (config, prep, integrated)),
            "來源證據不可混入其他硬體驗收狀態")
    return config, prep


def media_records(output_root, config, prep):
    result = {}
    for medium in config["outputs"]:
        require(targets.expected_board(config["armbian_board"], config["board"], medium),
                "官方格式別名與封裝媒體不符")
        directory = checked_path(output_root / medium, directory=True)
        manifest_path = checked_path(directory / "manifest.json")
        raw = read_file(manifest_path)
        manifest = json_object(raw, medium + "/manifest.json")
        require(manifest.get("schema_version") == 2 and manifest.get("board") == config["board"] and
                manifest.get("storage") == medium and manifest.get("kernel") == prep["kernel"] and
                manifest.get("release_id") == config["release_id"] and manifest.get("release") == "noble" and
                manifest.get("desktop") == "gnome-wayland" and manifest.get("hardware_validation") == "pending",
                "媒體封裝紀錄與本次配置不同：" + medium)
        identity = manifest["identity"]
        require(all(identity.get(key) == value for key, value in prep["identity"].items()) and
                identity.get("storage") == medium and identity.get("release_id") == config["release_id"],
                "媒體封裝來源並非本次根系統：" + medium)
        artifact = manifest["artifact"]
        suffix = "vendor-sd" if medium == "sd" else "titan-emmc"
        extension = ".img.zip" if medium == "sd" else ".zip"
        expected_name = f"Armbian_Noble_{config['board']}_gnome_{suffix}_{config['release_id']}{extension}"
        require(artifact.get("name") == expected_name, "媒體成品檔名不符合配置：" + medium)
        artifact_path = checked_path(directory / expected_name)
        actual = file_record(artifact_path)
        require(actual == {"size": artifact.get("size"), "sha256": artifact.get("sha256")},
                "媒體成品大小或 SHA-256 不符：" + medium)
        verification_path = checked_path(directory / "verification.json")
        verification_data = read_file(verification_path)
        verification = json_object(verification_data, medium + "/verification.json")
        require(verification.get("status") == "passed" and verification.get("scope") == "offline-archive-integrity" and
                verification.get("board") == config["board"] and verification.get("storage") == medium and
                verification.get("hardware_validation") == "pending" and
                verification.get("release_status") == manifest.get("release_status") == "candidate_unverified",
                "媒體封裝完整性核對尚未完成：" + medium)
        require(read_file(manifest_path) == raw and read_file(verification_path) == verification_data,
                "媒體紀錄於成品核對期間變更")
        result[medium] = {
            "manifest": {"path": medium + "/manifest.json", "size": len(raw), "sha256": sha(raw)},
            "artifact": {"path": medium + "/" + expected_name, **actual},
            "verification": {"path": medium + "/verification.json", "size": len(verification_data),
                             "sha256": sha(verification_data)},
        }
    return result


def publish(prepared, output_root, config_path=None):
    prepared = checked_path(prepared, directory=True)
    output_root = checked_path(output_root, directory=True)
    require(not output_root.is_relative_to(prepared) and not prepared.is_relative_to(output_root),
            "來源與交付目錄不可互相包含")
    destination = output_root / "evidence"
    require(not os.path.lexists(destination), "證據目錄已存在，拒絕覆寫")
    blobs = {name: read_file(prepared / name) for name in FILES}
    supplied = json_object(read_file(config_path or prepared / "native-config.json"), "指定配置")
    config, prep = validate_prepared(blobs, supplied)
    media = media_records(output_root, config, prep)
    files = {name: {"size": len(data), "sha256": sha(data)} for name, data in blobs.items()}
    record = {"schema_version": 1, "status": "complete", "source_kind": SOURCE_KIND,
              "board": config["board"], "release_id": config["release_id"], "outputs": config["outputs"],
              "profile_sha256": config["profile_sha256"], "source_commit": prep["identity"]["source_commit"],
              "build_source_sha256": prep["identity"]["build_source_sha256"],
              "rootfs_tree_sha256": prep["identity"]["rootfs_tree_sha256"],
              "prepared_rootfs_sha256": prep["rootfs_sha256"], "kernel": prep["kernel"],
              "files": files, "media": media, "hardware_validation": "pending",
              "scope": "來源及套件紀錄一致性與交付壓縮檔雜湊；不代表硬體驗收，未重新解壓核對封裝內部。"}
    if targets.target_for(config["armbian_board"]):
        record["armbian_board"] = config["armbian_board"]
    with tempfile.TemporaryDirectory(prefix=".evidence-", dir=output_root) as scratch:
        stage = Path(scratch) / "evidence"
        stage.mkdir(mode=0o755)
        for name, data in blobs.items():
            (stage / name).write_bytes(data)
        (stage / "evidence.json").write_bytes(encoded(record))
        sums = {name: item["sha256"] for name, item in files.items()}
        sums["evidence.json"] = sha((stage / "evidence.json").read_bytes())
        for item in media.values():
            for entry in item.values():
                sums["../" + entry["path"]] = entry["sha256"]
        (stage / "SHA256SUMS").write_text("".join(value + "  " + name + "\n" for name, value in sorted(sums.items())))
        for path in stage.iterdir():
            path.chmod(0o644)
        require(not os.path.lexists(destination), "證據目錄在發布前已存在")
        stage.rename(destination)
    return record


class ChineseParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "參數不完整或無法辨識，請使用 --help 核對。\n")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1).replace("options:", "選項：")


def main():
    parser = ChineseParser(description=__doc__, add_help=False)
    parser.add_argument("-h", "--help", action="help", help="顯示使用說明並結束")
    parser.add_argument("--prepared", required=True, type=Path, help="本次原生封存目錄")
    parser.add_argument("--output-root", required=True, type=Path, help="包含所選媒體成品的板型輸出目錄")
    parser.add_argument("--config", type=Path, help="本次配置；預設使用封存內 native-config.json")
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.prepared, args.output_root, args.config), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("證據發布失敗：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
