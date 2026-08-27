#!/usr/bin/env python3
"""檢查 R3 Mini 可重建來源契約與獨立物質證據。"""

from __future__ import annotations

import argparse
import csv
from copy import deepcopy
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/validation/bananapi-filogic-mt7986-r3mini-current.json"
DEFAULT_OUTPUT = ROOT / "output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
BOARD_FILE = ROOT / "config/boards/bananapir3mini.wip"
STATUS_FILE = ROOT / "config/bananapi-optimization-status.json"
BOARD_NAME = "bananapir3mini"
SOURCE_DATE_EPOCH = 1787793187
LINUX_COMMIT = "4a4506842b77b597f11e7fc53be1dcdbdc97eea9"
UBOOT_COMMIT = "34820924edbc4ec7803eb89d9852f4b870fa760a"
ATF_COMMIT = "c34e37802efaea356991a0811c8fc50f8a810f5b"
FIRMWARE_COMMIT = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
MT76_COMMIT = "c5a3bd91aa735b669618610d5f0ebfa5786845a6"
LINUX_FIRMWARE_COMMIT = "01205307636157a12c29e6a774bf83b218732050"
COMPONENT_DTB_SHA256 = "5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab"
PAYLOAD_HASHES = {
    "bl2.img": "44d4d6b1bdbfdc1f4d2b302047448788f0256b4d68568e9c9dd809005bccedfd",
    "gpt": "beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d",
    "u-boot.fip": "8f56c689f10b3aa2367f4290f940451e8d5b766cd3c0120e6aa2cc398db3ff67",
}
PAYLOAD_SIZES = {"bl2.img": 200793, "gpt": 17408, "u-boot.fip": 507953}
PARTITION_TYPES = [
    "1:0fc63daf-8483-4772-8e79-3d69d8477de4",
    "2:0fc63daf-8483-4772-8e79-3d69d8477de4",
    "3:0fc63daf-8483-4772-8e79-3d69d8477de4",
    "4:c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
    "5:0fc63daf-8483-4772-8e79-3d69d8477de4",
]
DYNAMIC_EVIDENCE_KEYS = {
    "component_build_evidence",
    "image_build_evidence",
    "source_contract_projection_sha256",
}


def fail(message: str) -> None:
    raise SystemExit(f"R3 Mini 政策守門失敗：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_commit(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_contract_projection(data: dict[str, Any]) -> dict[str, Any]:
    projection = deepcopy(data)
    for key in DYNAMIC_EVIDENCE_KEYS:
        projection.pop(key, None)
    return projection


def source_contract_projection_sha256(data: dict[str, Any]) -> str:
    return canonical_sha256(source_contract_projection(data))


def read_json(path: Path, description: str) -> dict[str, Any]:
    require(path.is_file(), f"缺少{description}：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"無法讀取{description}：{error}")
    require(isinstance(value, dict), f"{description}不是 JSON 物件")
    return value


def git_output(*arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *arguments], capture_output=True, text=True, check=False)
    require(result.returncode == 0, f"Git 證據查詢失敗：{' '.join(arguments)}")
    return result.stdout.strip()


def parse_assignments(values: object, description: str) -> dict[str, str]:
    require(isinstance(values, list), f"{description}必須是清單")
    result: dict[str, str] = {}
    for value in values:
        require(isinstance(value, str) and value.count("=") == 1, f"{description}格式不符")
        name, assigned = value.split("=", 1)
        require(name and assigned and name not in result, f"{description}有空值或重複")
        result[name] = assigned
    return result


def safe_artifact(output: Path, value: object, suffix: str) -> Path:
    require(isinstance(value, str) and value.endswith(suffix), "L2 產物路徑格式不符")
    relative = Path(value)
    require(not relative.is_absolute() and ".." not in relative.parts, "L2 產物路徑不安全")
    resolved = (output / relative).resolve()
    require(os.path.commonpath((str(output.resolve()), str(resolved))) == str(output.resolve()), "L2 產物離開輸出目錄")
    require(resolved.is_file(), f"L2 產物不存在：{value}")
    return resolved


def validate_runtime_sources(data: dict[str, Any]) -> None:
    expected = [
        {
            "name": "mt76", "source": "https://github.com/openwrt/mt76.git",
            "ref": f"commit:{MT76_COMMIT}", "commit": MT76_COMMIT,
            "evidence_role": "installed-content", "log_marker": "R3 Mini MT76 韌體固定來源",
        },
        {
            "name": "linux-firmware", "source": "https://gitlab.com/kernel-firmware/linux-firmware.git",
            "ref": f"commit:{LINUX_FIRMWARE_COMMIT}", "commit": LINUX_FIRMWARE_COMMIT,
            "evidence_role": "local-content-provenance", "log_marker": "R3 Mini Linux firmware 固定來源",
        },
    ]
    require(data.get("firmware_runtime_sources") == expected, "MT76/Linux firmware 執行期來源集合不符")
    require(
        data.get("mt76_firmware_source") == expected[0]["source"]
        and data.get("mt76_firmware_ref") == expected[0]["ref"]
        and data.get("mt76_firmware_commit") == MT76_COMMIT
        and data.get("verify_mt76_firmware_source_resolution") is True,
        "MT76 固定來源守門不完整",
    )
    require(
        data.get("linux_firmware_source") == expected[1]["source"]
        and data.get("linux_firmware_ref") == expected[1]["ref"]
        and data.get("linux_firmware_commit") == LINUX_FIRMWARE_COMMIT
        and data.get("verify_linux_firmware_source_contract") is True,
        "Linux firmware 固定來源守門不完整",
    )


def validate_static_contract(data: dict[str, Any]) -> dict[str, Any]:
    require(BOARD_FILE.is_file(), "板檔必須維持 .wip")
    require(not (BOARD_FILE.parent / "bananapir3mini.conf").exists(), "不得提前升級正式板檔")
    require(data.get("schema_version") == 1 and data.get("candidate_branch") == "current", "基礎契約不符")
    require(data.get("allowed_evidence_levels") == ["L1", "L2"] and data.get("target_evidence_level") == "L2", "證據層級契約不符")
    require(data.get("source_date_epoch") == SOURCE_DATE_EPOCH, "固定建置時間戳不符")
    expected_projection = source_contract_projection_sha256(data)
    require(data.get("source_contract_projection_sha256") == expected_projection, "來源契約規範投影雜湊不符")
    require(data.get("component_build_completed") is True, "元件證據狀態不符")
    require("image_build_evidence" not in data, "來源契約不得內嵌動態完整映像證據")
    for field in ("public_release_authorized", "hardware_claims_allowed", "hardware_validation_completed"):
        require(data.get(field) is False, f"{field} 必須維持 false")
    require(
        data.get("linux_commit") == LINUX_COMMIT
        and data.get("firmware_source") == "https://github.com/armbian/firmware"
        and data.get("firmware_ref") == f"commit:{FIRMWARE_COMMIT}"
        and data.get("firmware_commit") == FIRMWARE_COMMIT
        and data.get("verify_firmware_source_resolution") is True,
        "Linux 或 Armbian firmware 固定來源不符",
    )
    validate_runtime_sources(data)
    dram = data.get("atf_prebuilt_objects", {}).get("plat/mediatek/mt7986/drivers/dram/release/dram.o", {})
    require(
        isinstance(dram, dict)
        and dram.get("sha256") == "45acf44f2fe576991d7c0b13862cb41d1ffd37b37e1607e27ca4ddb31820fa79"
        and dram.get("redistribution_authorized") is False,
        "ATF DRAM 物件授權邊界不符",
    )
    release = data.get("release_gate", {})
    require(
        isinstance(release, dict) and release.get("status") == "blocked"
        and release.get("public_release_authorized") is False
        and release.get("hardware_claims_allowed") is False
        and release.get("required_blockers") == data.get("public_release_blockers"),
        "發布阻擋不完整",
    )
    blockers = data.get("public_release_blockers", [])
    require(
        isinstance(blockers, list)
        and "atf_mt7986_dram_object_redistribution_scope_unverified" in blockers
        and "emmc_boot0_installation_not_hardware_validated" in blockers,
        "ATF 或 eMMC boot0 阻擋遺失",
    )
    board = data.get("boards", {}).get(BOARD_NAME)
    require(isinstance(board, dict), "缺少 R3 Mini 板級契約")
    require(
        board.get("uboot_revision") == UBOOT_COMMIT and board.get("uboot_git_ref") == f"commit:{UBOOT_COMMIT}"
        and board.get("atf_revision") == ATF_COMMIT and board.get("atf_git_ref") == f"commit:{ATF_COMMIT}",
        "U-Boot 或 ATF 固定來源不符",
    )
    require(board.get("component_dtb_sha256") == COMPONENT_DTB_SHA256, "元件 DTB 雜湊不符")
    require(parse_assignments(board.get("uboot_payload_sha256"), "載荷雜湊") == PAYLOAD_HASHES, "載荷雜湊不符")
    require({key: int(value) for key, value in parse_assignments(board.get("uboot_payload_sizes"), "載荷大小").items()} == PAYLOAD_SIZES, "載荷大小不符")
    require(board.get("required_partition_types") == PARTITION_TYPES, "GPT 類型契約不符")
    require(
        board.get("root_partition_number") == 5 and board.get("root_partition_start_sector") == 32768
        and board.get("root_partition_label") == "armbi_root" and board.get("root_partition_filesystem_type") == "ext4",
        "rootfs 契約不符",
    )
    media = board.get("boot_media_contract", {})
    require(
        board.get("candidate_boot_media") == ["emmc"] and board.get("supported_boot_media") == []
        and "sd" in board.get("unsupported_boot_media", []) and media.get("cold_boot_source") == "emmc_boot0"
        and media.get("user_area_image_is_complete_cold_boot_installer") is False
        and media.get("boot0_payload_requires_separate_write") is True
        and media.get("boot0_hardware_validated") is False
        and board.get("automatic_emmc_install_authorized") is False,
        "eMMC user-area、boot0 或 SD 邊界不符",
    )
    return board


def validate_state(data: dict[str, Any], board: dict[str, Any]) -> str:
    level = data.get("candidate_level")
    states = {
        "L1 元件候選": ("internal-component-only", "L1", False, True),
        "L2 內部軟體候選": ("internal-l2", "L2", True, False),
    }
    require(level in states, "候選層級只接受 L1 或內部 L2")
    scope, evidence, full_image, calibration = states[level]
    require(data.get("candidate_scope") == scope and data.get("current_evidence_level") == evidence, "候選層級與範圍不一致")
    require(data.get("full_rootfs_image_built") is full_image, "完整映像狀態不符")
    require(data.get("l2_contract_calibration_required") is calibration, "校準狀態不符")
    release = data["release_gate"]
    require(release.get("full_image_built") is full_image and release.get("component_validation_only") is (not full_image), "發布映像狀態不符")
    if evidence == "L1":
        require(board.get("image_dtb_sha256") is None, "L1 不得宣稱映像 DTB")
        require(board.get("dtb_sha256") == COMPONENT_DTB_SHA256 and board.get("dtb_sha256_evidence_scope") == "component-only-l1", "L1 DTB 範圍不符")
        require("final_kernel_config_sha256" not in board and "final_uboot_config_sha256" not in board, "L1 不得固定最終設定")
        require(board.get("required_partitions", [])[-1] == "5:*:32768:*", "L1 rootfs 校準標記不符")
    else:
        partitions = board.get("required_partitions", [])
        require(isinstance(partitions, list) and len(partitions) == 5 and all("*" not in item for item in partitions), "L2 分割區含虛構或未校準值")
        for field in ("final_kernel_config_sha256", "final_uboot_config_sha256", "image_dtb_sha256"):
            require(valid_sha256(board.get(field)), f"L2 缺少 {field}")
        require(board.get("dtb_sha256") == board.get("image_dtb_sha256") and board.get("dtb_sha256_evidence_scope") == "full-image-l2", "L2 DTB 範圍不符")
    return evidence


def validate_direct_image(image: Path, archive: Path, row: dict[str, str], board: dict[str, Any]) -> list[dict[str, Any]]:
    require(image.stat().st_size >= 32 * 1024 * 1024, "IMG 過小，不是完整 rootfs 映像")
    require(image.stat().st_size == int(row["raw_size"]) and sha256_path(image) == row["raw_sha256"], "IMG 實檔不符")
    require(archive.stat().st_size == int(row["xz_size"]) and sha256_path(archive) == row["xz_sha256"], "XZ 實檔不符")
    checked = subprocess.run(["xz", "-t", "--", str(archive)], capture_output=True, check=False)
    require(checked.returncode == 0, "XZ 嚴格測試失敗")
    digest = hashlib.sha256()
    decompressed_size = 0
    try:
        with lzma.open(archive, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                decompressed_size += len(block)
    except (OSError, lzma.LZMAError) as error:
        fail(f"XZ 解壓失敗：{error}")
    require(decompressed_size == image.stat().st_size and digest.hexdigest() == row["raw_sha256"], "XZ 解壓內容與 IMG 不同")
    require(subprocess.run(["sgdisk", "-v", str(image)], capture_output=True, check=False).returncode == 0, "GPT 結構或 CRC 不完整")
    result = subprocess.run(["sfdisk", "--json", str(image)], capture_output=True, text=True, check=False)
    require(result.returncode == 0, "無法重新解析 GPT")
    table = json.loads(result.stdout)["partitiontable"]
    require(table.get("label") == "gpt", "IMG 不是 GPT")
    partitions = table.get("partitions", [])
    require(len(partitions) == 5, "GPT 分割區數量不符")
    expected_types = [item.split(":", 1)[1] for item in board["required_partition_types"]]
    calibrated: list[dict[str, Any]] = []
    for index, (partition, specification, expected_type) in enumerate(zip(partitions, board["required_partitions"], expected_types)):
        number, name, start, size = specification.split(":", 3)
        require("*" not in specification, "L2 GPT 契約不得含萬用值")
        actual = (str(partition.get("name", "")), str(partition.get("start", "")), str(partition.get("size", "")))
        require(actual == (name, start, size), f"GPT 第 {number} 分割區尺寸或名稱不符")
        actual_type = str(partition.get("type", "")).lower().removeprefix("0x")
        require(actual_type == expected_type, f"GPT 第 {number} 分割區類型不符")
        calibrated.append({"number": index + 1, "name": actual[0], "start_sector": int(actual[1]), "sector_count": int(actual[2]), "type_guid": actual_type})
    hashes = PAYLOAD_HASHES
    sizes = PAYLOAD_SIZES
    with image.open("rb") as stream:
        for specification in board["uboot_payloads"]:
            name, offset_text = specification.rsplit("@", 1)
            stream.seek(int(offset_text))
            require(hashlib.sha256(stream.read(sizes[name])).hexdigest() == hashes[name], f"IMG 的 {name} 實際偏移內容不符")
    return calibrated


def validate_l2_material(data: dict[str, Any], board: dict[str, Any], config_path: Path, output: Path, status_path: Path) -> None:
    matrix = output / "CANDIDATES.tsv"
    require(matrix.is_file(), "缺少 CANDIDATES.tsv")
    with matrix.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        require(reader.fieldnames == ["board", "release", "profile", "raw_size", "raw_sha256", "xz_size", "xz_sha256", "img_path", "xz_path", "source_commit", "uboot_tag"], "候選矩陣欄位不符")
        rows = list(reader)
    require(len(rows) == 1 and rows[0]["board"] == BOARD_NAME, "候選矩陣必須只有 R3 Mini")
    row = rows[0]
    image = safe_artifact(output, row["img_path"], ".img")
    archive = safe_artifact(output, row["xz_path"], ".img.xz")
    partitions = validate_direct_image(image, archive, row, board)

    completion = read_json(output / "COMPLETION_STATUS.json", "建置狀態")
    verification = read_json(status_path, "驗證狀態")
    source_commit = row["source_commit"]
    require(valid_commit(source_commit) and git_output("cat-file", "-t", source_commit) == "commit", "候選來源提交無效")
    source_tree = git_output("rev-parse", f"{source_commit}^{{tree}}")
    relative = config_path.resolve().relative_to(ROOT.resolve())
    committed = subprocess.run(["git", "-C", str(ROOT), "show", f"{source_commit}:{relative.as_posix()}"], capture_output=True, check=False)
    require(committed.returncode == 0, "來源提交缺少 validation")
    config_hash = hashlib.sha256(committed.stdout).hexdigest()
    projection_hash = data["source_contract_projection_sha256"]
    runtime_hash = canonical_sha256(data["firmware_runtime_sources"])
    expected_completion = {
        "status": "complete", "source_commit": source_commit, "source_tree": source_tree,
        "validation_config_sha256": config_hash, "candidates_sha256": sha256_path(matrix),
        "source_contract_projection_sha256": projection_hash,
        "firmware_runtime_sources_sha256": runtime_hash,
    }
    require(all(completion.get(key) == value for key, value in expected_completion.items()), "建置狀態未原子綁定來源契約")
    expected_verification = {
        "status": "complete", "evidence_level": "L2", "source_commit": source_commit,
        "source_tree": source_tree, "verifier_commit": source_commit,
        "build_validation_config_sha256": config_hash, "verification_config_sha256": config_hash,
        "candidate_matrix_sha256": sha256_path(matrix),
        "source_contract_projection_sha256": projection_hash,
        "firmware_runtime_sources_sha256": runtime_hash,
        "material_reparsed": True, "calibration_mode": "formal",
        "material_image_sha256": row["raw_sha256"], "material_archive_sha256": row["xz_sha256"],
        "read_only_content_verified": True, "hardware_tested": False,
    }
    require(all(verification.get(key) == value for key, value in expected_verification.items()), "驗證狀態未閉合正式物質證據")
    for filename, field in (("UBOOT_PAYLOAD_EVIDENCE.tsv", "uboot_payload_manifest_sha256"), ("FINAL_CONFIG_EVIDENCE.tsv", "final_config_manifest_sha256")):
        path = output / filename
        require(path.is_file() and sha256_path(path) == verification.get(field), f"{filename} 未與驗證狀態綁定")

    calibration_path = output / "R3MINI_CALIBRATION.json"
    require(calibration_path.is_file() and sha256_path(calibration_path) == verification.get("calibration_manifest_sha256"), "校準清單未與驗證狀態綁定")
    calibration = read_json(calibration_path, "校準清單")
    require(
        calibration.get("mode") == "formal" and calibration.get("source_commit") == source_commit
        and calibration.get("source_tree") == source_tree
        and calibration.get("validation_config_sha256") == config_hash
        and calibration.get("source_contract_projection_sha256") == projection_hash
        and calibration.get("partition_table", {}).get("partitions") == partitions,
        "校準清單來源或 GPT 與重新解析結果不符",
    )
    require(calibration.get("root_partition") == {
        "number": board["root_partition_number"],
        "start_sector": int(board["required_partitions"][4].split(":")[2]),
        "sector_count": int(board["required_partitions"][4].split(":")[3]),
        "type_guid": board["required_partition_types"][4].split(":", 1)[1],
        "label": board["root_partition_label"], "filesystem": board["root_partition_filesystem_type"],
    }, "校準 rootfs 證據不符")
    require(calibration.get("dtb", {}).get("sha256") == board["image_dtb_sha256"], "校準 DTB 證據不符")
    require(calibration.get("final_configs", {}).get("kernel", {}).get("sha256") == board["final_kernel_config_sha256"], "校準核心設定不符")
    require(calibration.get("final_configs", {}).get("uboot", {}).get("sha256") == board["final_uboot_config_sha256"], "校準 U-Boot 設定不符")
    require(calibration.get("firmware_runtime_sources") == data["firmware_runtime_sources"], "校準韌體來源證據不符")
    limits = calibration.get("evidence_limits", {})
    require(limits.get("hardware_tested") is False and limits.get("blank_emmc_cold_boot_installer_proven") is False and limits.get("emmc_boot0_separate_write_required") is True, "校準清單錯誤宣稱實機或空白 eMMC")
    emmc = verification.get("emmc_image_contract", {})
    require(
        emmc.get("user_area", {}).get("image_is_complete_cold_boot_installer") is False
        and emmc.get("boot0", {}).get("requires_separate_write") is True
        and emmc.get("boot0", {}).get("hardware_validated") is False,
        "驗證狀態錯誤宣稱 eMMC 冷啟動能力",
    )


def reparse_l2_material(config_path: Path, output: Path, status_path: Path) -> None:
    inspector = ROOT / "tools/inspect-bananapi-filogic-r3mini-material.py"
    result = subprocess.run(
        [
            "python3", str(inspector), "--validation", str(config_path),
            "--output", str(output), "--mode", "formal", "--status", str(status_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    require(
        result.returncode == 0,
        f"正式 L2 IMG 唯讀重解析失敗：{result.stderr.strip() or result.stdout.strip()}",
    )


def validate_board_source_text() -> None:
    text = BOARD_FILE.read_text(encoding="utf-8")
    for required in (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{FIRMWARE_COMMIT}"',
        f'MT76_FIRMWARE_GIT_REF_BOARD="commit:{MT76_COMMIT}"',
        f'LINUX_FIRMWARE_GIT_REF_BOARD="commit:{LINUX_FIRMWARE_COMMIT}"',
        "R3 Mini MT76 韌體固定來源", "R3 Mini Linux firmware 固定來源",
        "firmware-source-contract.tsv",
    ):
        require(required in text, f"板檔缺少固定來源執行期設定：{required}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=Path(os.environ.get("VALIDATION_CONFIG", DEFAULT_CONFIG)))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--source-contract-only", action="store_true")
    mode.add_argument("--material-evidence-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT)))
    parser.add_argument("--status", type=Path)
    parser.add_argument("--print-source-contract-projection-sha256", action="store_true")
    arguments = parser.parse_args()
    config_path = arguments.config.resolve()
    data = read_json(config_path, "驗證政策")
    if arguments.print_source_contract_projection_sha256:
        print(source_contract_projection_sha256(data))
        return
    board = validate_static_contract(data)
    evidence = validate_state(data, board)
    validate_board_source_text()
    if config_path == DEFAULT_CONFIG.resolve():
        status = read_json(STATUS_FILE, "全域盤點狀態")
        require(status.get("evidence", {}).get(BOARD_NAME, {}).get("level") == evidence, "全域證據等級不一致")
    if arguments.material_evidence_only:
        require(evidence == "L2", "只有 L2 可閉合正式物質證據")
        status_path = arguments.status or arguments.output / "VERIFICATION_STATUS.json.partial"
        reparse_l2_material(config_path, arguments.output.resolve(), status_path.resolve())
        validate_l2_material(data, board, config_path, arguments.output.resolve(), status_path.resolve())
    elif not arguments.source_contract_only and evidence == "L2":
        status_path = arguments.status or arguments.output / "VERIFICATION_STATUS.json"
        reparse_l2_material(config_path, arguments.output.resolve(), status_path.resolve())
        validate_l2_material(data, board, config_path, arguments.output.resolve(), status_path.resolve())
    print(f"R3 Mini {evidence} 來源契約與所選證據政策通過")


if __name__ == "__main__":
    main()
