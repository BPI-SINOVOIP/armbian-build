#!/usr/bin/env python3
"""唯讀重解析 R3 Mini 映像並產生可回填的校準證據。"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/validation/bananapi-filogic-mt7986-r3mini-current.json"
DEFAULT_OUTPUT = ROOT / "output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
BOARD = "bananapir3mini"


def fail(message: str) -> None:
    raise SystemExit(f"R3 Mini 物質證據失敗：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def run(arguments: list[str], *, text: bool = True) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(arguments, capture_output=True, text=text, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() if text else ""
        fail(f"命令失敗：{' '.join(arguments)}{f'：{stderr}' if stderr else ''}")
    return result


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_path(output: Path, relative_text: str, suffix: str) -> Path:
    relative = Path(relative_text)
    require(
        relative_text.endswith(suffix) and not relative.is_absolute() and ".." not in relative.parts,
        f"產物路徑不安全：{relative_text}",
    )
    resolved = (output / relative).resolve()
    require(
        os.path.commonpath((str(output.resolve()), str(resolved))) == str(output.resolve()),
        f"產物路徑離開輸出目錄：{relative_text}",
    )
    require(resolved.is_file(), f"找不到產物：{relative_text}")
    return resolved


def read_candidates(output: Path) -> tuple[dict[str, str], Path, Path]:
    matrix = output / "CANDIDATES.tsv"
    require(matrix.is_file(), "缺少 CANDIDATES.tsv")
    with matrix.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        expected = [
            "board", "release", "profile", "raw_size", "raw_sha256", "xz_size",
            "xz_sha256", "img_path", "xz_path", "source_commit", "uboot_tag",
        ]
        require(reader.fieldnames == expected, "CANDIDATES.tsv 欄位不符")
        rows = list(reader)
    require(len(rows) == 1 and rows[0]["board"] == BOARD, "候選矩陣必須只有 R3 Mini")
    row = rows[0]
    require(row["release"] == "trixie" and row["profile"] == "cli", "候選發行版或型態不符")
    image = safe_path(output, row["img_path"], ".img")
    archive = safe_path(output, row["xz_path"], ".img.xz")
    return row, image, archive


def verify_archive(row: dict[str, str], image: Path, archive: Path) -> None:
    require(image.stat().st_size == int(row["raw_size"]), "IMG 大小與矩陣不符")
    require(archive.stat().st_size == int(row["xz_size"]), "XZ 大小與矩陣不符")
    require(sha256_path(image) == row["raw_sha256"], "IMG 雜湊與矩陣不符")
    require(sha256_path(archive) == row["xz_sha256"], "XZ 雜湊與矩陣不符")
    run(["xz", "-t", "--", str(archive)])
    listing = run(["xz", "--robot", "--list", "--", str(archive)]).stdout.splitlines()
    totals = [line.split("\t") for line in listing if line.startswith("totals\t")]
    require(len(totals) == 1 and len(totals[0]) >= 5, "XZ 結構清單無效")
    require(int(totals[0][1]) == 1 and int(totals[0][2]) >= 1, "XZ 必須只有一個完整串流")
    require(int(totals[0][4]) == image.stat().st_size, "XZ 宣告解壓大小與 IMG 不符")
    digest = hashlib.sha256()
    size = 0
    try:
        with lzma.open(archive, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
    except (OSError, lzma.LZMAError) as error:
        fail(f"XZ 無法完整解壓：{error}")
    require(size == image.stat().st_size and digest.hexdigest() == row["raw_sha256"], "XZ 與 IMG 不同一")


def assignments(values: list[str], description: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        require(isinstance(value, str) and value.count("=") == 1, f"{description}格式不符")
        name, assigned = value.split("=", 1)
        require(name and assigned and name not in result, f"{description}有空值或重複")
        result[name] = assigned
    return result


def inspect_gpt(image: Path, board: dict[str, Any], formal: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    require(image.stat().st_size >= 32 * 1024 * 1024, "IMG 小於 32 MiB，不可能是完整 R3 Mini rootfs 映像")
    with image.open("rb") as stream:
        stream.seek(510)
        require(stream.read(2) == b"\x55\xaa", "IMG 缺少保護 MBR 簽章")
    run(["sgdisk", "-v", str(image)])
    table = json.loads(run(["sfdisk", "--json", str(image)]).stdout)["partitiontable"]
    require(table.get("label") == "gpt", "IMG 不是 GPT")
    sector_size = int(table.get("sectorsize", board.get("logical_sector_size", 512)))
    require(sector_size == int(board["logical_sector_size"]), "GPT 邏輯 sector 大小不符")
    partitions = table.get("partitions", [])
    specifications = board["required_partitions"]
    types = board["required_partition_types"]
    require(len(partitions) == len(specifications) == len(types) == 5, "GPT 必須恰有五個分割區")
    calibrated: list[dict[str, Any]] = []
    for index, (partition, specification, type_specification) in enumerate(zip(partitions, specifications, types)):
        number, name, start, size = specification.split(":", 3)
        type_number, expected_type = type_specification.split(":", 1)
        require(int(number) == int(type_number) == index + 1, "GPT 契約編號不連續")
        actual_name = str(partition.get("name", ""))
        actual_start = int(partition.get("start", -1))
        actual_size = int(partition.get("size", -1))
        actual_type = str(partition.get("type", "")).lower().removeprefix("0x")
        if formal:
            require("*" not in specification, "L2 正式契約不得保留萬用分割區值")
        for actual, expected, field in (
            (actual_name, name, "名稱"), (str(actual_start), start, "起點"),
            (str(actual_size), size, "大小"),
        ):
            require(expected == "*" or actual == expected, f"GPT 第 {number} 分割區{field}不符")
        require(actual_type == expected_type.lower(), f"GPT 第 {number} 分割區類型不符")
        require(actual_start >= 0 and actual_size > 0, f"GPT 第 {number} 分割區範圍無效")
        require((actual_start + actual_size) * sector_size <= image.stat().st_size, f"GPT 第 {number} 分割區超出 IMG")
        calibrated.append(
            {"number": index + 1, "name": actual_name, "start_sector": actual_start,
             "sector_count": actual_size, "type_guid": actual_type}
        )
    return table, calibrated


def verify_payload_offsets(image: Path, board: dict[str, Any]) -> list[dict[str, Any]]:
    expected_hashes = assignments(board["uboot_payload_sha256"], "載荷雜湊")
    expected_sizes = {name: int(value) for name, value in assignments(board["uboot_payload_sizes"], "載荷大小").items()}
    results: list[dict[str, Any]] = []
    with image.open("rb") as stream:
        for specification in board["uboot_payloads"]:
            name, offset_text = specification.rsplit("@", 1)
            offset = int(offset_text)
            size = expected_sizes[name]
            require(offset + size <= image.stat().st_size, f"{name} 超出 IMG")
            stream.seek(offset)
            digest = hashlib.sha256(stream.read(size)).hexdigest()
            require(digest == expected_hashes[name], f"IMG 實際偏移的 {name} 不符")
            results.append({"name": name, "offset_bytes": offset, "size": size, "sha256": digest})
    return results


def parse_packages(status_path: Path) -> tuple[set[str], set[str]]:
    installed: set[str] = set()
    provided: set[str] = set()
    for paragraph in status_path.read_text(encoding="utf-8", errors="strict").split("\n\n"):
        fields: dict[str, str] = {}
        current = ""
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")) and current:
                fields[current] += " " + line.strip()
            elif ": " in line:
                current, value = line.split(": ", 1)
                fields[current] = value
        if fields.get("Status") != "install ok installed":
            continue
        if fields.get("Package"):
            installed.add(fields["Package"])
        for value in fields.get("Provides", "").split(","):
            name = re.sub(r"\s*\(.*", "", value).strip()
            if name:
                provided.add(name)
    return installed, provided


def inspect_rootfs(
    image: Path, config: dict[str, Any], board: dict[str, Any], partitions: list[dict[str, Any]], formal: bool
) -> dict[str, Any]:
    run(["sudo", "-n", "true"])
    loop = ""
    with tempfile.TemporaryDirectory(prefix="r3mini-material-") as mount_text:
        mount_dir = Path(mount_text)
        try:
            loop = run(["sudo", "losetup", "--find", "--show", "--partscan", "--read-only", str(image)]).stdout.strip()
            run(["udevadm", "settle"])
            devices = json.loads(
                run(
                    [
                        "lsblk", "--json", "--bytes", "--list", "-o",
                        "PATH,NAME,TYPE,RO,SIZE", loop,
                    ]
                ).stdout
            )["blockdevices"]
            require(len(devices) >= 6 and devices[0].get("type") == "loop" and devices[0].get("ro") is True, "loop 裝置不是唯讀")
            root_number = int(board["root_partition_number"])
            root_start = partitions[root_number - 1]["start_sector"]
            partition_devices = [item for item in devices if item.get("type") == "part"]
            require(len(partition_devices) == len(partitions), "loop 分割區數量與 GPT 不同")
            root = partition_devices[root_number - 1]
            require(root.get("ro") is True, "根分割區不是唯讀")
            sysfs_start = Path("/sys/class/block") / str(root["name"]) / "start"
            require(sysfs_start.is_file() and int(sysfs_start.read_text().strip()) == root_start, "根分割區起點與 GPT 不同")
            root_device = root["path"]
            root_label = run(["sudo", "blkid", "-s", "LABEL", "-o", "value", root_device]).stdout.strip()
            root_fs = run(["sudo", "blkid", "-s", "TYPE", "-o", "value", root_device]).stdout.strip()
            require(root_label == board["root_partition_label"], "rootfs label 不符")
            require(root_fs == board["root_partition_filesystem_type"], "rootfs 類型不符")
            run(["sudo", "mount", "-o", "ro,noload,nosuid,nodev,noexec", root_device, str(mount_dir)])
            mount_options = run(["findmnt", "-no", "OPTIONS", "--mountpoint", str(mount_dir)]).stdout.split(",")
            require("ro" in mount_options, "rootfs 未以唯讀模式掛載")

            require(any((mount_dir / "boot" / name).is_file() for name in ("Image", "zImage", "uImage")), "rootfs 缺少核心映像")
            require((mount_dir / "boot/uInitrd").is_file(), "rootfs 缺少 initrd")
            extlinux = mount_dir / "boot/extlinux/extlinux.conf"
            require(extlinux.is_file() and f"  fdt {board['extlinux_fdt']}" in extlinux.read_text(), "extlinux DTB 選擇不符")

            dtb_relative = Path(board["dtb"])
            dtb = mount_dir / "boot/dtb" / dtb_relative
            if not dtb.is_file():
                dtb = mount_dir / "boot/dtb" / dtb_relative.name
            require(dtb.is_file() and dtb.stat().st_size > 0, "rootfs 缺少 DTB")
            dtb_hash = sha256_path(dtb)
            if formal:
                require(dtb_hash == board["image_dtb_sha256"], "映像 DTB 雜湊不符")
            require(run(["fdtget", "-t", "s", str(dtb), "/", "model"]).stdout.strip() == board["model"], "DTB model 不符")
            compatible = run(["fdtget", "-t", "s", str(dtb), "/", "compatible"]).stdout.split()
            require(all(value in compatible for value in board["compatible"]), "DTB compatible 不完整")

            kernel_configs = sorted((mount_dir / "boot").glob("config-*"))
            require(kernel_configs, "rootfs 缺少核心設定")
            kernel_hashes = {sha256_path(path) for path in kernel_configs}
            require(len(kernel_hashes) == 1, "rootfs 含多份不同核心設定")
            kernel_hash = kernel_hashes.pop()
            if formal:
                require(kernel_hash == board["final_kernel_config_sha256"], "最終核心設定不符")

            uboot_dir = mount_dir / f"usr/lib/linux-u-boot-current-{BOARD}"
            uboot_config = uboot_dir / f"u-boot-config-target-{board['uboot_target_index']}"
            require(uboot_config.is_file(), "rootfs 缺少最終 U-Boot 設定")
            uboot_hash = sha256_path(uboot_config)
            if formal:
                require(uboot_hash == board["final_uboot_config_sha256"], "最終 U-Boot 設定不符")

            payload_hashes = assignments(board["uboot_payload_sha256"], "載荷雜湊")
            payload_sizes = {name: int(value) for name, value in assignments(board["uboot_payload_sizes"], "載荷大小").items()}
            packaged_payloads: list[dict[str, Any]] = []
            for name in sorted(payload_hashes):
                payload = uboot_dir / name
                require(payload.is_file(), f"U-Boot 套件缺少 {name}")
                require(payload.stat().st_size == payload_sizes[name], f"U-Boot 套件 {name} 大小不符")
                digest = sha256_path(payload)
                require(digest == payload_hashes[name], f"U-Boot 套件 {name} 雜湊不符")
                packaged_payloads.append({"name": name, "size": payload.stat().st_size, "sha256": digest})

            status = mount_dir / "var/lib/dpkg/status"
            require(status.is_file(), "rootfs 缺少 dpkg 狀態")
            installed, provided = parse_packages(status)
            required_packages = list(config["common_packages"]) + [
                f"linux-image-current-{config['kernel_family']}",
                f"linux-dtb-current-{config['kernel_family']}",
                f"linux-u-boot-{BOARD}-current",
                f"armbian-bsp-cli-{BOARD}-current",
            ]
            missing = [name for name in required_packages if name not in installed and name not in provided]
            require(not missing, f"rootfs 缺少必要套件：{', '.join(missing)}")

            checked_files: dict[str, str] = {}
            for manifest in ("installed_firmware_blobs", "installed_file_sha256"):
                for relative, expected in config.get(manifest, {}).items():
                    path = mount_dir / relative.removeprefix("/")
                    require(path.is_file(), f"rootfs 缺少受控檔案 {relative}")
                    digest = sha256_path(path)
                    require(digest == expected, f"rootfs 受控檔案雜湊不符：{relative}")
                    checked_files[relative] = digest

            contract_path = mount_dir / f"usr/share/doc/armbian-bsp-{BOARD}/firmware-source-contract.tsv"
            with contract_path.open(newline="", encoding="utf-8") as stream:
                contract_rows = list(csv.DictReader(stream, delimiter="\t"))
            expected_sources = config["firmware_runtime_sources"]
            require(len(contract_rows) == len(expected_sources), "映像韌體來源契約筆數不符")
            for actual, expected in zip(contract_rows, expected_sources):
                require(
                    actual == {key: expected[key] for key in ("name", "source", "ref", "commit", "evidence_role")},
                    f"映像韌體來源契約不符：{expected['name']}",
                )

            return {
                "root_partition": {
                    "number": root_number, "start_sector": root_start,
                    "sector_count": partitions[root_number - 1]["sector_count"],
                    "type_guid": partitions[root_number - 1]["type_guid"],
                    "label": root_label, "filesystem": root_fs,
                },
                "dtb": {"path": str(dtb.relative_to(mount_dir)), "size": dtb.stat().st_size, "sha256": dtb_hash},
                "final_configs": {
                    "kernel": {"paths": [str(path.relative_to(mount_dir)) for path in kernel_configs], "sha256": kernel_hash},
                    "uboot": {"path": str(uboot_config.relative_to(mount_dir)), "sha256": uboot_hash},
                },
                "packaged_payloads": packaged_payloads,
                "required_packages": sorted(required_packages),
                "controlled_files": checked_files,
                "firmware_runtime_sources": expected_sources,
            }
        finally:
            subprocess.run(["sudo", "umount", str(mount_dir)], capture_output=True, check=False)
            if loop:
                subprocess.run(["sudo", "losetup", "-d", loop], capture_output=True, check=False)


def update_status(status_path: Path, manifest_path: Path, manifest: dict[str, Any], mode: str) -> None:
    require(status_path.is_file(), f"缺少共用驗證暫存狀態：{status_path}")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    require(status.get("status") == "complete", "共用驗證尚未完成")
    require(status.get("source_commit") == manifest["source_commit"], "校準與驗證來源提交不同")
    require(status.get("source_tree") == manifest["source_tree"], "校準與驗證來源 tree 不同")
    require(status.get("source_contract_projection_sha256") == manifest["source_contract_projection_sha256"], "校準與來源契約投影不同")
    status.update(
        {
            "material_reparsed": True,
            "calibration_mode": mode,
            "calibration_manifest": manifest_path.name,
            "calibration_manifest_sha256": sha256_path(manifest_path),
            "material_image_sha256": manifest["image"]["sha256"],
            "material_archive_sha256": manifest["archive"]["sha256"],
            "read_only_content_verified": True,
            "hardware_tested": False,
        }
    )
    temporary = status_path.with_name(status_path.name + ".material.partial")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, status_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", type=Path, default=Path(os.environ.get("VALIDATION_CONFIG", DEFAULT_CONFIG)))
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT)))
    parser.add_argument("--mode", choices=("calibration", "formal"), required=True)
    parser.add_argument("--status", type=Path)
    arguments = parser.parse_args()
    config_path = arguments.validation.resolve()
    output = arguments.output.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    board = config["boards"][BOARD]
    formal = arguments.mode == "formal"
    require(config["candidate_level"] == ("L2 內部軟體候選" if formal else "L1 元件候選"), "校準模式與候選層級不符")
    row, image, archive = read_candidates(output)
    verify_archive(row, image, archive)
    table, partitions = inspect_gpt(image, board, formal)
    image_payloads = verify_payload_offsets(image, board)
    rootfs = inspect_rootfs(image, config, board, partitions, formal)
    source_commit = row["source_commit"]
    require(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None, "來源提交格式不符")
    source_tree = run(["git", "-C", str(ROOT), "rev-parse", f"{source_commit}^{{tree}}"]).stdout.strip()
    projection = config.get("source_contract_projection_sha256", "")
    require(re.fullmatch(r"[0-9a-f]{64}", projection) is not None, "來源契約投影雜湊格式不符")
    manifest = {
        "schema_version": 1,
        "board": BOARD,
        "mode": arguments.mode,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": source_commit,
        "source_tree": source_tree,
        "validation_config_sha256": sha256_path(config_path),
        "source_contract_projection_sha256": projection,
        "image": {"path": row["img_path"], "size": image.stat().st_size, "sha256": row["raw_sha256"]},
        "archive": {"path": row["xz_path"], "size": archive.stat().st_size, "sha256": row["xz_sha256"], "single_xz_stream_verified": True},
        "partition_table": {
            "label": table["label"], "logical_sector_size": int(table.get("sectorsize", 512)),
            "partitions": partitions,
        },
        "image_payloads": image_payloads,
        **rootfs,
        "evidence_limits": {
            "hardware_tested": False,
            "blank_emmc_cold_boot_installer_proven": False,
            "emmc_boot0_separate_write_required": True,
        },
    }
    manifest_path = output / "R3MINI_CALIBRATION.json"
    temporary = output / "R3MINI_CALIBRATION.json.partial"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    status_path = arguments.status or output / "VERIFICATION_STATUS.json.partial"
    update_status(status_path.resolve(), manifest_path, manifest, arguments.mode)
    print(f"R3 Mini {arguments.mode} 唯讀物質證據與校準清單已完成：{manifest_path}")


if __name__ == "__main__":
    main()
