#!/usr/bin/env python3
"""檢查 BPI-R3 Mini 固定來源、映像證據與 eMMC 邊界。"""

from __future__ import annotations

import csv
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/validation/bananapi-filogic-mt7986-r3mini-current.json"
OUTPUT_DIR = ROOT / "output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
BOARD_FILE = ROOT / "config/boards/bananapir3mini.wip"
STATUS_FILE = ROOT / "config/bananapi-optimization-status.json"
SOURCE_DATE_EPOCH = 1787793187
LINUX_COMMIT = "4a4506842b77b597f11e7fc53be1dcdbdc97eea9"
UBOOT_COMMIT = "34820924edbc4ec7803eb89d9852f4b870fa760a"
ATF_COMMIT = "c34e37802efaea356991a0811c8fc50f8a810f5b"
FIRMWARE_COMMIT = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
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


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    require(result.returncode == 0, f"Git 證據查詢失敗：{' '.join(arguments)}")
    return result.stdout.strip()


def read_json(path: Path, description: str) -> dict[str, object]:
    require(path.is_file(), f"缺少真實{description}：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"無法讀取{description}：{error}")
    require(isinstance(value, dict), f"{description}不是 JSON 物件")
    return value


def parse_assignments(values: object, description: str) -> dict[str, str]:
    require(isinstance(values, list), f"{description}必須是清單")
    result: dict[str, str] = {}
    for value in values:
        require(isinstance(value, str) and value.count("=") == 1, f"{description}格式不符")
        name, assigned = value.split("=", 1)
        require(name and assigned and name not in result, f"{description}含空值或重複項目")
        result[name] = assigned
    return result


def read_metadata(path: Path) -> dict[str, str]:
    require(path.is_file(), f"缺少真實產物中繼資料：{path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        require("=" in line, "產物中繼資料含無效列")
        key, value = line.split("=", 1)
        require(key and key not in values, f"產物中繼資料欄位重複：{key}")
        values[key] = value
    return values


def safe_artifact(output_dir: Path, value: object, suffix: str) -> Path:
    require(isinstance(value, str) and value.endswith(suffix), "L2 產物路徑格式不符")
    relative = Path(value)
    require(not relative.is_absolute() and ".." not in relative.parts, "L2 產物路徑不安全")
    resolved = (output_dir / relative).resolve()
    require(
        os.path.commonpath((str(output_dir.resolve()), str(resolved))) == str(output_dir.resolve()),
        "L2 產物路徑離開固定輸出目錄",
    )
    require(resolved.is_file(), f"L2 產物不存在：{value}")
    return resolved


def validate_static_contract(data: dict[str, object]) -> dict[str, object]:
    require(BOARD_FILE.is_file(), "板檔必須維持 .wip")
    require(not (BOARD_FILE.parent / "bananapir3mini.conf").exists(), "不得提前升級為正式板檔")
    require(data.get("schema_version") == 1, "schema_version 不符")
    require(data.get("candidate_branch") == "current", "候選分支不符")
    require(data.get("allowed_evidence_levels") == ["L1", "L2"], "允許證據層級不符")
    require(data.get("target_evidence_level") == "L2", "目標證據層級不符")
    require(data.get("source_date_epoch") == SOURCE_DATE_EPOCH, "固定建置時間戳不符")
    require(data.get("component_build_completed") is True, "元件建置證據必須保留")
    for field in ("public_release_authorized", "hardware_claims_allowed", "hardware_validation_completed"):
        require(data.get(field) is False, f"{field} 必須維持 false")
    require(
        data.get("linux_commit") == LINUX_COMMIT
        and data.get("firmware_source") == "https://github.com/armbian/firmware"
        and data.get("firmware_commit") == FIRMWARE_COMMIT
        and data.get("firmware_ref") == f"commit:{FIRMWARE_COMMIT}"
        and data.get("verify_firmware_source_resolution") is True,
        "Linux 或 Armbian firmware 固定來源不符",
    )
    dram = data.get("atf_prebuilt_objects", {}).get(
        "plat/mediatek/mt7986/drivers/dram/release/dram.o", {}
    )
    require(
        isinstance(dram, dict)
        and dram.get("sha256") == "45acf44f2fe576991d7c0b13862cb41d1ffd37b37e1607e27ca4ddb31820fa79"
        and dram.get("redistribution_authorized") is False,
        "ATF DRAM 預編譯物件授權邊界不符",
    )
    release = data.get("release_gate", {})
    require(
        isinstance(release, dict)
        and release.get("status") == "blocked"
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
    board = data.get("boards", {}).get("bananapir3mini")
    require(isinstance(board, dict), "缺少 R3 Mini 板級契約")
    require(
        board.get("uboot_revision") == UBOOT_COMMIT
        and board.get("uboot_git_ref") == f"commit:{UBOOT_COMMIT}"
        and board.get("atf_revision") == ATF_COMMIT
        and board.get("atf_git_ref") == f"commit:{ATF_COMMIT}",
        "U-Boot 或 ATF 固定來源不符",
    )
    require(board.get("component_dtb_sha256") == COMPONENT_DTB_SHA256, "元件 DTB 雜湊不符")
    require(parse_assignments(board.get("uboot_payload_sha256"), "載荷雜湊") == PAYLOAD_HASHES, "載荷雜湊不符")
    require(
        {key: int(value) for key, value in parse_assignments(board.get("uboot_payload_sizes"), "載荷大小").items()}
        == PAYLOAD_SIZES,
        "載荷精確大小不符",
    )
    require(board.get("required_partition_types") == PARTITION_TYPES, "GPT 分割區類型契約不符")
    require(board.get("root_partition_number") == 5, "根分割區編號不符")
    require(board.get("root_partition_start_sector") == 32768, "根分割區起點不符")
    require(board.get("root_partition_label") == "armbi_root", "根檔案系統標籤不符")
    require(board.get("root_partition_filesystem_type") == "ext4", "根檔案系統類型不符")
    media = board.get("boot_media_contract", {})
    require(
        board.get("candidate_boot_media") == ["emmc"]
        and board.get("supported_boot_media") == []
        and "sd" in board.get("unsupported_boot_media", [])
        and media.get("cold_boot_source") == "emmc_boot0"
        and media.get("user_area_image_is_complete_cold_boot_installer") is False
        and media.get("boot0_payload_requires_separate_write") is True
        and media.get("boot0_hardware_validated") is False
        and board.get("automatic_emmc_install_authorized") is False,
        "eMMC user area、boot0 或 SD 邊界不符",
    )
    return board


def validate_l2_evidence(
    data: dict[str, object],
    board: dict[str, object],
    config_path: Path = DEFAULT_CONFIG,
    output_dir: Path = OUTPUT_DIR,
) -> None:
    evidence = data.get("image_build_evidence")
    require(isinstance(evidence, dict), "L2 缺少完整映像證據")
    require(evidence.get("status") == "complete" and evidence.get("evidence_level") == "L2", "L2 狀態不完整")
    require(evidence.get("full_rootfs_image_built") is True, "L2 未確認完整 rootfs")
    require(evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require(evidence.get("hardware_tested") is False, "內部 L2 不得冒充實機驗證")
    require(evidence.get("public_release_authorized") is False, "內部 L2 不得冒充公開發布核准")
    source_commit = evidence.get("source_commit")
    source_tree = evidence.get("source_tree")
    require(valid_commit(source_commit) and valid_commit(source_tree), "L2 來源提交或 tree 格式不符")
    require(evidence.get("verifier_commit") == source_commit, "L2 來源與驗證器提交不一致")
    require(git_output("cat-file", "-t", str(source_commit)) == "commit", "L2 來源提交不存在")
    require(git_output("rev-parse", f"{source_commit}^{{tree}}") == source_tree, "L2 來源 tree 不符")

    relative_config = config_path.resolve().relative_to(ROOT.resolve())
    committed_config = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{source_commit}:{relative_config.as_posix()}"],
        capture_output=True,
        check=False,
    )
    require(committed_config.returncode == 0, "L2 來源提交缺少 validation")
    committed_hash = hashlib.sha256(committed_config.stdout).hexdigest()
    require(
        evidence.get("build_validation_config_sha256") == committed_hash
        and evidence.get("verification_config_sha256") == committed_hash,
        "L2 建置、驗證與來源 validation 未原子綁定",
    )
    for field in (
        "candidate_matrix_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(valid_sha256(evidence.get(field)), f"L2 {field} 格式不符")

    matrix = output_dir / "CANDIDATES.tsv"
    payload_manifest = output_dir / "UBOOT_PAYLOAD_EVIDENCE.tsv"
    config_manifest = output_dir / "FINAL_CONFIG_EVIDENCE.tsv"
    completion = read_json(output_dir / "COMPLETION_STATUS.json", "建置完成狀態")
    verification = read_json(output_dir / "VERIFICATION_STATUS.json", "驗證完成狀態")
    for path, expected, description in (
        (matrix, evidence["candidate_matrix_sha256"], "候選矩陣"),
        (payload_manifest, evidence["uboot_payload_manifest_sha256"], "載荷清單"),
        (config_manifest, evidence["final_config_manifest_sha256"], "最終設定清單"),
    ):
        require(path.is_file(), f"缺少真實{description}")
        require(sha256_path(path) == expected, f"{description}雜湊不符")

    build_expected = {
        "status": "complete",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "validation_config_sha256": committed_hash,
        "candidates_sha256": evidence["candidate_matrix_sha256"],
    }
    require(all(completion.get(key) == value for key, value in build_expected.items()), "建置狀態未綁定來源與矩陣")
    verify_expected = {
        "status": "complete",
        "evidence_level": "L2",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "verifier_commit": source_commit,
        "build_validation_config_sha256": committed_hash,
        "verification_config_sha256": committed_hash,
        "candidate_matrix_sha256": evidence["candidate_matrix_sha256"],
        "uboot_payload_manifest_sha256": evidence["uboot_payload_manifest_sha256"],
        "final_config_manifest_sha256": evidence["final_config_manifest_sha256"],
    }
    require(all(verification.get(key) == value for key, value in verify_expected.items()), "驗證狀態未閉合真實證據")
    emmc = verification.get("emmc_image_contract", {})
    require(
        isinstance(emmc, dict)
        and emmc.get("user_area", {}).get("image_is_complete_cold_boot_installer") is False
        and emmc.get("boot0", {}).get("requires_separate_write") is True
        and emmc.get("boot0", {}).get("hardware_validated") is False,
        "驗證狀態錯誤宣稱 eMMC 冷啟動能力",
    )

    lines = matrix.read_text(encoding="utf-8").splitlines()
    header = "board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_path\txz_path\tsource_commit\tuboot_tag"
    require(len(lines) == 2 and lines[0] == header, "候選矩陣欄位或筆數不符")
    row = dict(zip(header.split("\t"), lines[1].split("\t"), strict=True))
    require(
        row["board"] == "bananapir3mini"
        and row["release"] == "trixie"
        and row["profile"] == "cli"
        and row["source_commit"] == source_commit
        and row["uboot_tag"] == "v2025.04",
        "候選矩陣身分不符",
    )
    image_info = evidence.get("image", {})
    archive_info = evidence.get("archive", {})
    require(isinstance(image_info, dict) and isinstance(archive_info, dict), "L2 產物證據格式不符")
    image = safe_artifact(output_dir, image_info.get("path"), ".img")
    archive = safe_artifact(output_dir, archive_info.get("path"), ".img.xz")
    for name, info, path, size_key, hash_key in (
        ("IMG", image_info, image, "raw_size", "raw_sha256"),
        ("XZ", archive_info, archive, "xz_size", "xz_sha256"),
    ):
        require(isinstance(info.get("size"), int) and info["size"] > 0, f"{name} 大小無效")
        require(valid_sha256(info.get("sha256")), f"{name} 雜湊格式不符")
        require(path.stat().st_size == info["size"] == int(row[size_key]), f"{name} 大小與實檔不符")
        require(sha256_path(path) == info["sha256"] == row[hash_key], f"{name} 雜湊與實檔不符")
    require(row["img_path"] == image_info["path"] and row["xz_path"] == archive_info["path"], "產物路徑與矩陣不符")
    decompressed = hashlib.sha256()
    try:
        with lzma.open(archive, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                decompressed.update(block)
    except (OSError, lzma.LZMAError) as error:
        fail(f"XZ 串流無法解壓：{error}")
    require(decompressed.hexdigest() == image_info["sha256"], "XZ 解壓資料與 IMG 不一致")

    with payload_manifest.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        require(
            reader.fieldnames
            == ["board", "payload", "placement", "offset", "size", "sha256"],
            "載荷清單欄位不符",
        )
        rows = list(reader)
    require(len(rows) == 3, "載荷清單筆數不符")
    actual_payloads = {
        row["payload"]: (int(row["size"]), row["sha256"], row["placement"], row["offset"])
        for row in rows
        if row.get("board") == "bananapir3mini"
    }
    expected_payloads = {
        "bl2.img": (PAYLOAD_SIZES["bl2.img"], PAYLOAD_HASHES["bl2.img"], "image", "17408"),
        "gpt": (PAYLOAD_SIZES["gpt"], PAYLOAD_HASHES["gpt"], "package-only", "-"),
        "u-boot.fip": (PAYLOAD_SIZES["u-boot.fip"], PAYLOAD_HASHES["u-boot.fip"], "image", "6815744"),
    }
    require(actual_payloads == expected_payloads, "U-Boot／ATF／GPT 載荷證據不符")

    with config_manifest.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        require(
            reader.fieldnames == ["board", "component", "path", "sha256"],
            "最終設定清單欄位不符",
        )
        config_rows = list(reader)
    actual_configs = {
        row["component"]: row["sha256"]
        for row in config_rows
        if row.get("board") == "bananapir3mini"
    }
    require(
        actual_configs
        == {
            "kernel": board.get("final_kernel_config_sha256"),
            "uboot": board.get("final_uboot_config_sha256"),
        },
        "最終核心或 U-Boot 設定證據不符",
    )
    require(evidence.get("linux_dtb", {}).get("sha256") == board.get("image_dtb_sha256"), "映像 DTB 證據不符")

    metadata = read_metadata(output_dir / "bananapir3mini/artifact.metadata.txt")
    build_parameters = (
        "BOARD=bananapir3mini BRANCH=current RELEASE=trixie BUILD_DESKTOP=no "
        "BUILD_MINIMAL=yes KERNEL_CONFIGURE=no EXPERT=yes ARTIFACT_IGNORE_CACHE=yes "
        "COMPRESS_OUTPUTIMAGE=sha,img SOURCE_DATE_EPOCH=1787793187 "
        "CLEAN_LEVEL=make-kernel,make-uboot,make-atf,make-crust"
    )
    metadata_expected = {
        "source_commit": str(source_commit),
        "source_tree": str(source_tree),
        "validation_config_sha256": committed_hash,
        "source_date_epoch": str(SOURCE_DATE_EPOCH),
        "build_parameters_sha256": hashlib.sha256(f"{build_parameters}\n".encode()).hexdigest(),
        "artifact_ignore_cache": "yes",
        "raw_size": str(image_info["size"]),
        "raw_sha256": str(image_info["sha256"]),
        "xz_size": str(archive_info["size"]),
        "xz_sha256": str(archive_info["sha256"]),
        "firmware_revision": FIRMWARE_COMMIT,
    }
    require(all(metadata.get(key) == value for key, value in metadata_expected.items()), "產物中繼資料未綁定來源、時間戳或實檔")


def validate_state(data: dict[str, object], board: dict[str, object], config_path: Path) -> None:
    level = data.get("candidate_level")
    require(level in {"L1 元件候選", "L2 內部軟體候選"}, "候選層級只接受 L1 或內部 L2")
    expected_scope, expected_evidence = {
        "L1 元件候選": ("internal-component-only", "L1"),
        "L2 內部軟體候選": ("internal-l2", "L2"),
    }[level]
    require(data.get("candidate_scope") == expected_scope, "候選範圍與層級不一致")
    require(data.get("current_evidence_level") == expected_evidence, "證據層級與候選層級不一致")
    release = data["release_gate"]
    if level == "L1 元件候選":
        require(data.get("full_rootfs_image_built") is False, "L1 不得宣稱完整映像完成")
        require(release.get("full_image_built") is False and release.get("component_validation_only") is True, "L1 發布狀態不符")
        require(data.get("l2_contract_calibration_required") is True, "L1 必須保留首次映像校準門檻")
        require("image_build_evidence" not in data, "L1 不得夾帶完整映像證據")
        require(board.get("image_dtb_sha256") is None, "L1 不得宣稱映像 DTB 雜湊")
        require(board.get("dtb_sha256") == COMPONENT_DTB_SHA256, "L1 元件 DTB 相容欄位不符")
        require(board.get("dtb_sha256_evidence_scope") == "component-only-l1", "L1 DTB 證據範圍不符")
        require("final_kernel_config_sha256" not in board and "final_uboot_config_sha256" not in board, "L1 不得冒充最終設定證據")
        require(board.get("required_partitions", [])[-1] == "5:*:32768:*", "L1 根分割區校準標記不符")
        return
    require(data.get("full_rootfs_image_built") is True, "L2 必須確認完整映像")
    require(release.get("full_image_built") is True and release.get("component_validation_only") is False, "L2 發布狀態不符")
    require(data.get("l2_contract_calibration_required") is False, "L2 不得保留未完成校準標記")
    partitions = board.get("required_partitions", [])
    require(isinstance(partitions, list) and len(partitions) == 5 and all("*" not in item for item in partitions), "L2 分割區大小或名稱尚未精確固定")
    for field in ("final_kernel_config_sha256", "final_uboot_config_sha256", "image_dtb_sha256"):
        require(valid_sha256(board.get(field)), f"L2 缺少 {field}")
    require(board.get("dtb_sha256") == board.get("image_dtb_sha256"), "L2 DTB 相容欄位未切換至映像證據")
    require(board.get("dtb_sha256_evidence_scope") == "full-image-l2", "L2 DTB 證據範圍不符")
    validate_l2_evidence(data, board, config_path=config_path)


def main() -> None:
    config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(os.environ.get("VALIDATION_CONFIG", DEFAULT_CONFIG)).resolve()
    require(config_path.is_file(), f"找不到驗證政策：{config_path}")
    data = read_json(config_path, "驗證政策")
    board = validate_static_contract(data)
    validate_state(data, board, config_path)
    if config_path == DEFAULT_CONFIG.resolve():
        status = read_json(STATUS_FILE, "全域盤點狀態")
        expected = "L1" if data["candidate_level"] == "L1 元件候選" else "L2"
        require(status.get("evidence", {}).get("bananapir3mini", {}).get("level") == expected, "全域證據等級不一致")
    board_text = BOARD_FILE.read_text(encoding="utf-8")
    for required in (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{FIRMWARE_COMMIT}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
    ):
        require(required in board_text, f"板檔缺少固定韌體設定：{required}")
    print("R3 Mini 固定來源、L1/L2 證據與 eMMC 邊界政策通過")


if __name__ == "__main__":
    main()
