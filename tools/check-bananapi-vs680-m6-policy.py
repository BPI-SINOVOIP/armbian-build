#!/usr/bin/env python3
"""檢查 Banana Pi M6 固定來源、啟動鏈與證據狀態。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/validation/bananapi-vs680-m6-legacy.json"
BOARD = ROOT / "config/boards/bananapim6.wip"
STATUS = ROOT / "config/bananapi-optimization-status.json"
FAMILY = ROOT / "config/sources/families/vs680.conf"

LINUX_REVISION = "3229415e99a06edc972948c0a856cbcf7de7ce55"
UBOOT_REVISION = "ccca1c75bb6d06470b8a3f6104068b43763ee468"
FIRMWARE_REVISION = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
FINAL_KERNEL_CONFIG = "b67480db7854ea797a1813102b2ef1c7a1312c9291797912612368821b058786"
FINAL_UBOOT_CONFIG = "f31af0f1449901eb3834fd17e9c8c69034bd50b126a29108168683ba6b38c1f6"
COMPONENT_DTB = "52c58e8a1413fd644b812480215350410659371083afa9930684df5752625413"
TZK_SHA256 = "175e9b9313dffb70a97852ae21d855d3472916cc2af28f678ebcddc44828e411"
UBOOT_SHA256 = "4d8158b3ed44de9384fabb009a0639cbe2c83e964a32724b5c87ce9911f72bda"


def fail(message: str) -> None:
    raise SystemExit(f"BPI-M6 政策守門失敗：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_commit(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assignments(values: object, field: str) -> dict[str, str]:
    require(isinstance(values, list), f"{field} 必須是清單")
    parsed: dict[str, str] = {}
    for value in values:
        require(isinstance(value, str) and value.count("=") == 1, f"{field} 格式不符")
        name, expected = value.split("=", 1)
        require(name and expected and name not in parsed, f"{field} 含空值或重複名稱")
        parsed[name] = expected
    return parsed


def validate_candidate_state(data: dict[str, object], status: dict[str, object]) -> None:
    level = data.get("candidate_level")
    require(level in {"L1 元件候選", "L2 內部軟體候選"}, "候選層級只允許 L1 或內部 L2")
    expected = {
        "L1 元件候選": ("internal-component-only", "L1"),
        "L2 內部軟體候選": ("internal-l2", "L2"),
    }[level]
    require(data.get("candidate_scope") == expected[0], "候選層級與範圍不成對")
    require(data.get("current_evidence_level") == expected[1], "候選層級與證據等級不成對")
    require(status["evidence"]["bananapim6"]["level"] == expected[1], "中央證據等級與契約不一致")
    require(data.get("component_build_completed") is True, "固定來源元件證據必須保留")

    for key in (
        "candidate_public_release_approved",
        "public_release_allowed",
        "hardware_validation_complete",
        "hardware_claims_allowed",
        "firmware_redistribution_license_verified",
    ):
        require(data.get(key) is False, f"{key} 必須維持 false")
    require(
        data["license_policy"]["opaque_payload_redistribution_verified"] is False,
        "不透明載荷不得宣稱授權已閉合",
    )

    board = data["boards"]["bananapim6"]
    require(board.get("component_dtb_sha256") == COMPONENT_DTB, "元件 DTB 雜湊不符")
    if level == "L1 元件候選":
        for key in ("rootfs_image_built", "full_image_built", "full_rootfs_image_built"):
            require(data.get(key) is False, f"L1 的 {key} 必須為 false")
        require("image_build_evidence" not in data, "L1 不得夾帶完整映像證據")
        require(board.get("image_dtb_sha256") is None, "L1 不得宣稱映像 DTB 雜湊")
        require("dtb_sha256" not in board, "L1 不得把元件 DTB 冒充映像證據")
        require(
            board.get("dtb_sha256_evidence_scope") == "component-only-l1",
            "L1 DTB 證據範圍不符",
        )
        return

    for key in ("rootfs_image_built", "full_image_built", "full_rootfs_image_built"):
        require(data.get(key) is True, f"L2 的 {key} 必須為 true")
    evidence = data.get("image_build_evidence")
    require(isinstance(evidence, dict), "L2 缺少完整映像證據")
    require(evidence.get("status") == "complete", "L2 映像證據尚未完成")
    require(evidence.get("evidence_level") == "L2", "映像證據不是 L2")
    require(evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require(evidence.get("hardware_tested") is False, "內部 L2 不得冒充實機驗證")
    require(valid_commit(evidence.get("source_commit")), "L2 來源提交格式不符")
    require(valid_commit(evidence.get("verifier_commit")), "L2 驗證器提交格式不符")
    require(
        evidence["source_commit"] == evidence["verifier_commit"],
        "L2 來源與驗證器提交不一致",
    )
    for key in (
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(valid_sha256(evidence.get(key)), f"L2 {key} 格式不符")
    require(
        evidence["build_validation_config_sha256"]
        == evidence["verification_config_sha256"],
        "L2 建置與驗證契約雜湊不一致",
    )
    for name in ("image", "archive"):
        artifact = evidence.get(name, {})
        require(isinstance(artifact.get("size"), int) and artifact["size"] > 0, f"L2 {name} 大小無效")
        require(valid_sha256(artifact.get("sha256")), f"L2 {name} 雜湊格式不符")
    image_dtb = evidence.get("linux_dtb", {}).get("sha256")
    require(valid_sha256(image_dtb), "L2 缺少映像 DTB 雜湊")
    require(board.get("image_dtb_sha256") == image_dtb, "映像 DTB 欄位與證據不一致")
    require(board.get("dtb_sha256") == image_dtb, "映像 DTB 相容欄位與證據不一致")
    require(board.get("dtb_sha256_evidence_scope") == "full-image-l2", "L2 DTB 證據範圍不符")


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    data = json.loads(config_path.read_text(encoding="utf-8"))
    board_text = BOARD.read_text(encoding="utf-8")
    family_text = FAMILY.read_text(encoding="utf-8")
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    board = data["boards"]["bananapim6"]

    require(data.get("schema_version") == 1, "驗證契約版本不符")
    require(data.get("candidate_branch") == "legacy", "候選分支不是 legacy")
    require(data.get("kernel_family") == "vs680", "核心家族不符")
    require(data.get("target_evidence_level") == "L2", "目標證據等級不符")
    require(data.get("source_date_epoch") == 1717001894, "來源時間戳不符")
    validate_candidate_state(data, status)

    expected_sources = {
        "linux": ("https://github.com/BPI-SINOVOIP/pi-linux.git", LINUX_REVISION),
        "uboot": ("https://github.com/BPI-SINOVOIP/pi-u-boot.git", UBOOT_REVISION),
        "firmware": ("https://github.com/armbian/firmware", FIRMWARE_REVISION),
    }
    for component, (source, revision) in expected_sources.items():
        entry = data["source_commits"][component]
        require(entry.get("source") == source, f"{component} 來源不符")
        require(entry.get("ref") == f"commit:{revision}", f"{component} 引用未固定")
        require(entry.get("revision") == revision, f"{component} 提交不符")
    require(data.get("linux_source") == expected_sources["linux"][0], "Linux 頂層來源不符")
    require(data.get("linux_ref") == f"commit:{LINUX_REVISION}", "Linux 頂層引用不符")
    require(data.get("linux_commit") == LINUX_REVISION, "Linux 頂層提交不符")
    require(data.get("firmware_source") == expected_sources["firmware"][0], "韌體頂層來源不符")
    require(data.get("firmware_ref") == f"commit:{FIRMWARE_REVISION}", "韌體頂層引用不符")
    require(data.get("firmware_commit") == FIRMWARE_REVISION, "韌體頂層提交不符")
    require(data.get("verify_firmware_source_resolution") is True, "未啟用韌體來源解析守門")

    board_requirements = (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{FIRMWARE_REVISION}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
        f"declare -g BOOTBRANCH='commit:{UBOOT_REVISION}'",
        f"declare -g KERNELBRANCH='commit:{LINUX_REVISION}'",
    )
    for requirement in board_requirements:
        require(requirement in board_text, f"板檔缺少固定設定：{requirement}")
    require("branch:" not in board_text, "板檔仍含可移動來源分支")

    require(board.get("partition_table") == "msdos", "分割表不是 MBR")
    require(
        board.get("required_partitions")
        == ["1:*:204800:524288", "2:*:729088:*"],
        "雙分割區契約不符",
    )
    require(board.get("required_partition_types") == ["1:ea", "2:83"], "MBR 分割區類型不符")
    require(board.get("boot_partition_number") == 1, "boot 分割區編號不符")
    require(board.get("root_partition_number") == 2, "root 分割區編號不符")
    require(board.get("boot_partition_label") == "BPI-BOOT", "boot 標籤不符")
    require(board.get("root_partition_label") == "BPI-ROOT", "root 標籤不符")
    require(
        board.get("boot_partition_filesystem_type") == "vfat",
        "boot 分割區檔案系統類型不符",
    )
    require(
        board.get("root_partition_filesystem_type") == "ext4",
        "root 分割區檔案系統類型不符",
    )
    require(board.get("boot_configuration") == "separate_fat_armbian_env", "boot 模式不符")
    require(board.get("boot_script_source") == "config/bootscripts/boot-vs680.cmd", "boot script 來源不符")
    require(
        board.get("boot_script_source_sha256")
        == data["source_file_sha256"]["config/bootscripts/boot-vs680.cmd"],
        "boot script 契約雜湊不一致",
    )

    require(
        board.get("final_kernel_config_sha256") == FINAL_KERNEL_CONFIG,
        "最終核心設定雜湊不符",
    )
    require(
        board.get("final_uboot_config_sha256") == FINAL_UBOOT_CONFIG,
        "最終 U-Boot 設定雜湊不符",
    )
    require(
        board.get("uboot_payloads")
        == ["bpi-m6-tzk-4MB.bin@512", "u-boot.bin@2097152"],
        "payload 位移契約不符",
    )
    require(
        board.get("payload_write_order")
        == ["bpi-m6-tzk-4MB.bin", "u-boot.bin"],
        "payload 寫入順序不符",
    )
    tzk_write = 'dd if="${tzk_payload}" of="$2" bs=512 seek=1'
    uboot_write = 'dd if="$1/u-boot.bin" of="$2" bs=1k seek=2048'
    require(tzk_write in family_text and uboot_write in family_text, "VS680 family 缺少受控 payload 寫入")
    require(family_text.index(tzk_write) < family_text.index(uboot_write), "VS680 family 的 payload 寫入順序不符")
    overlap = board.get("payload_overlap_policy", {})
    require(overlap.get("allowed") is True, "未啟用受控 payload 重疊")
    require(overlap.get("earlier_payload") == "bpi-m6-tzk-4MB.bin", "先寫 payload 不符")
    require(overlap.get("later_payload") == "u-boot.bin", "後寫 payload 不符")
    require(overlap.get("overlap_starts_at_image_offset") == 2097152, "重疊起點不符")
    sizes = assignments(board.get("uboot_payload_sizes"), "payload 精確大小")
    hashes = assignments(board.get("uboot_payload_sha256"), "payload 雜湊")
    require(sizes == {"bpi-m6-tzk-4MB.bin": "4193792", "u-boot.bin": "616575"}, "payload 精確大小不符")
    require(hashes == {"bpi-m6-tzk-4MB.bin": TZK_SHA256, "u-boot.bin": UBOOT_SHA256}, "payload 雜湊不符")
    tzk = data["opaque_boot_payloads"]["packages/blobs/vs680/bpi-m6-tzk-4MB.bin"]
    require(tzk.get("size") == 4193792 and tzk.get("sha256") == TZK_SHA256, "TZK 契約不符")

    component = data["component_build_evidence"]
    require(valid_commit(component.get("source_commit")), "元件來源提交格式不符")
    for key in (
        "manifest_sha256",
        "verification_sha256",
        "uboot_log_sha256",
        "kernel_log_sha256",
        "dtb_sha256",
        "uboot_sha256",
    ):
        require(valid_sha256(component.get(key)), f"元件證據 {key} 格式不符")
    require(component.get("dtb_sha256") == COMPONENT_DTB, "元件 DTB 證據不符")
    require(component.get("uboot_sha256") == UBOOT_SHA256, "元件 U-Boot 證據不符")

    for relative, expected in data["source_file_sha256"].items():
        path = ROOT / relative
        require(path.is_file(), f"受控來源檔不存在：{relative}")
        require(file_sha256(path) == expected, f"受控來源檔雜湊不符：{relative}")

    print("BPI-M6 固定來源、啟動鏈與證據狀態檢查通過。")


if __name__ == "__main__":
    main()
