#!/usr/bin/env python3
"""檢查 Banana Pi M4 固定來源與證據邊界。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


EXPECTED_BSP = "25f5b88ec4ba34029f964693dc34028b26e6c67c"
EXPECTED_FIRMWARE = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
EXPECTED_OUTPUT = "output/components/2026.08/bananapi-realtek-rtd1395-m4-legacy"
EXPECTED_CONDITIONAL_UNLINKED = {
    "u-boot-rtk/static_lib/libefuse.a.32",
    "u-boot-rtk/static_lib/libsha1_util.a.32",
    "u-boot-rtk/static_lib/libsecurity.a.32",
    "u-boot-rtk/static_lib/libkeyset.a.32",
}
EXPECTED_LINKED_UNREBUILT = {
    "u-boot-rtk/image/rtd1395/a_entry.img",
    "u-boot-rtk/image/rtd1395/exc_dispatch.img",
    "u-boot-rtk/image/rtd1395/exc_redirect.img",
    "u-boot-rtk/image/rtd1395/isr_video.img",
    "u-boot-rtk/image/rtd1395/ros_bootvector.img",
    "u-boot-rtk/image/rtd1395/v_entry.img",
}
EXPECTED_COMPONENTS = {
    "u-boot.bin",
    "uEnv.txt",
    "bluecore.audio",
    "Image",
    "rtd-1395-bananapi-m4-1GB.dtb",
    "rtd-1395-bananapi-m4-2GB.dtb",
    "linux.config",
    "linux-modules.tar.xz",
    "u-boot-link-command.txt",
}
CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL = {
    "candidate_level",
    "candidate_scope",
    "component_build_completed",
    "component_build_evidence",
    "current_evidence_level",
    "full_image_built",
    "full_rootfs_image_built",
    "image_build_evidence",
    "rootfs_image_built",
    "source_contract_projection_sha256",
}
CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS = {
    "dtb_sha256_evidence_scope",
    "image_dtb_sha256",
}


def fail(message: str) -> None:
    raise SystemExit(f"M4 來源政策拒絕：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def contract_projection(config: dict[str, object]) -> dict[str, object]:
    """建立排除候選狀態與實體映像證據的穩定來源契約。"""
    projection = deepcopy(config)
    for key in CONTRACT_PROJECTION_EXCLUDED_TOP_LEVEL:
        projection.pop(key, None)
    boards = projection.get("boards", {})
    if isinstance(boards, dict):
        for board in boards.values():
            if isinstance(board, dict):
                for key in CONTRACT_PROJECTION_EXCLUDED_BOARD_FIELDS:
                    board.pop(key, None)
    return projection


def contract_projection_sha256(config: dict[str, object]) -> str:
    encoded = json.dumps(
        contract_projection(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_asset(source: Path, relative: str, metadata: dict[str, object]) -> None:
    path = source / relative
    require(path.is_file(), f"固定來源缺少資產：{relative}")
    require(path.stat().st_size == metadata["size"], f"資產大小不符：{relative}")
    require(digest(path) == metadata["sha256"], f"資產雜湊不符：{relative}")


def verify_source_tree(config: dict[str, object], source: Path) -> None:
    require((source / ".git").exists(), f"來源不是 Git 工作樹：{source}")
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(revision == EXPECTED_BSP, "來源提交不符")

    verify_asset(
        source,
        str(config["linux_license_path"]),
        {"size": 18693, "sha256": config["linux_license_sha256"]},
    )
    verify_asset(
        source,
        str(config["uboot_license_path"]),
        {"size": (source / str(config["uboot_license_path"])).stat().st_size,
         "sha256": config["uboot_license_sha256"]},
    )
    toolchain = config["build_toolchain"]
    verify_asset(source, str(toolchain["path"]), toolchain)
    verify_asset(
        source,
        str(toolchain["manifest_path"]),
        {
            "size": (source / str(toolchain["manifest_path"])).stat().st_size,
            "sha256": toolchain["manifest_sha256"],
        },
    )
    for section in (
        "conditional_unlinked_prebuilt_assets",
        "linked_unrebuilt_source_assets",
        "runtime_prebuilt_assets",
        "excluded_source_assets",
    ):
        for relative, metadata in config[section].items():
            verify_asset(source, relative, metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("契約", type=Path)
    parser.add_argument("固定來源目錄", nargs="?", type=Path)
    parser.add_argument(
        "--print-source-contract-projection-sha256",
        action="store_true",
        help="輸出排除狀態與映像證據後的來源契約投影雜湊",
    )
    arguments = parser.parse_args()
    config_path = arguments.契約.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))

    require(config["schema_version"] == 1, "契約版本不支援")
    require(config["candidate_branch"] == "legacy", "候選分支必須為 legacy")
    require(config["kernel_family"] == "realtek-rtd139x-bpi", "核心家族不符")
    require(config["candidate_level"] == "L2 內部軟體候選", "M4 完整映像契約必須是內部 L2")
    require(config["candidate_scope"] == "internal-l2", "M4 候選範圍不符")
    require(config["current_evidence_level"] == "L2", "M4 候選證據層級不符")
    require(config["target_evidence_level"] == "L2", "M4 目標證據層級不符")
    family = config["realtek_family_inventory"]
    require(
        family["shared_legacy_include"] == "config/sources/families/include/realtek_bpi_legacy_common.inc",
        "Realtek legacy 共用入口不符",
    )
    require(family["shared_legacy_include_modified"] is False, "本候選不得修改 Realtek 共用入口")
    require(family["legacy_boards"]["bananapim4"]["soc"] == "RTD1395", "M4 SoC 盤點不符")
    require(family["legacy_boards"]["bananapiw2"]["soc"] == "RTD1296", "W2 SoC 盤點不符")
    require(family["separate_family_boards"]["xpressreal-t3"]["soc"] == "RTD1619B", "RTD1619B 盤點不符")
    require(config["source_date_epoch"] == 1711071187, "來源時間基準不符")
    for prefix in ("linux", "uboot"):
        require(config[f"{prefix}_commit"] == EXPECTED_BSP, f"{prefix} 提交不符")
        require(config[f"{prefix}_ref"] == f"commit:{EXPECTED_BSP}", f"{prefix} 不是精確提交")
        require(config[f"{prefix}_source"] == "https://github.com/BPI-SINOVOIP/BPI-M4-bsp.git", f"{prefix} 來源不符")
    require(config["firmware_commit"] == EXPECTED_FIRMWARE, "韌體提交不符")
    require(config["firmware_ref"] == f"commit:{EXPECTED_FIRMWARE}", "韌體不是精確提交")
    require(config["atf_applicable"] is False, "不得虛構獨立 TF-A 建置")
    require(config["verify_firmware_source_resolution"] is True, "完整映像必須核對韌體提交")
    projection = contract_projection_sha256(config)
    declared_projection = config.get("source_contract_projection_sha256")
    if declared_projection is not None:
        require(declared_projection == projection, "來源契約投影雜湊不符")

    require(
        set(config["conditional_unlinked_prebuilt_assets"]) == EXPECTED_CONDITIONAL_UNLINKED,
        "U-Boot 條件式未連結資產集合不符",
    )
    for metadata in config["conditional_unlinked_prebuilt_assets"].values():
        require(metadata["source_build_available"] is False, "條件式預建資產不得誤標可重建")
        require(metadata["included_in_candidate_binary"] is False, "未連結資產不得誤標進入候選")
        require(metadata["redistribution_license_verified"] is False, "條件式預建資產授權不得誤標已確認")
    require(
        set(config["linked_unrebuilt_source_assets"]) == EXPECTED_LINKED_UNREBUILT,
        "U-Boot 已嵌入但未重建資產集合不符",
    )
    for metadata in config["linked_unrebuilt_source_assets"].values():
        require(metadata["source_files_available"] is True, "已嵌入啟動段必須可追到來源檔")
        require(metadata["build_toolchain_pinned"] is False, "不得誤標輔助處理器工具鏈已固定")
        require(metadata["rebuilt_in_candidate"] is False, "不得誤標啟動段已由來源重建")
        require(metadata["included_in_candidate_binary"] is True, "實際嵌入資產不得誤標排除")
        require(metadata["redistribution_license_verified"] is False, "啟動段授權不得誤標已確認")
    for metadata in config["runtime_prebuilt_assets"].values():
        require(metadata["source_build_available"] is False, "執行期預建資產不得誤標可重建")
        require(metadata["redistribution_license_verified"] is False, "執行期預建資產授權不得誤標已確認")
    for metadata in config["excluded_source_assets"].values():
        require(metadata["included_in_candidate"] is False, "排除資產不得進入候選")
        require(metadata["redistribution_license_verified"] is False, "排除資產授權不得誤標已確認")

    require(config["public_release_allowed"] is False, "目前不得允許公開發布")
    require(config["candidate_public_release_approved"] is False, "目前不得標記發布審核通過")
    require(config["hardware_validation_complete"] is False, "目前沒有完整實機驗證")
    require(config["firmware_redistribution_license_verified"] is False, "韌體再散布授權不得誤標閉合")
    require(config["license_policy"]["opaque_payload_redistribution_verified"] is False, "不透明載荷授權不得誤標閉合")
    require(config["license_policy"]["toolchain_redistribution_verified"] is False, "工具鏈再散布授權不得誤標閉合")
    require(config["hardware_validated"] is False, "目前沒有實機證據")
    require(config["hardware_claims_allowed"] is False, "目前不得宣稱硬體通過")
    if config["full_image_built"]:
        require(config["rootfs_image_built"] is True, "完整映像缺少 rootfs 建置狀態")
        require(config["full_rootfs_image_built"] is True, "完整映像缺少 rootfs 證據")
        require(isinstance(config.get("image_build_evidence"), dict), "完整映像缺少機器證據")
    else:
        require(config["rootfs_image_built"] is False, "過渡契約不得誤標 rootfs 已建置")
        require(config["full_rootfs_image_built"] is False, "過渡契約不得誤標完整 rootfs")
        require("image_build_evidence" not in config, "過渡契約不得夾帶舊映像證據")
    require(os.environ.get("PUBLIC_RELEASE", "no") != "yes", "未完成授權前禁止公開發布")

    board = config["boards"]["bananapim4"]
    require(board["compatible"] == ["bananapi,bpi-m4", "realtek,rtd1395"], "板卡相容字串不符")
    require(board["memory_variants_mib"] == [1024, 2048], "記憶體變體契約不符")
    require(board["uboot_write_offset_bytes"] == 40960, "U-Boot 寫入位置不符")
    require(board["uboot_offset"] == 40960, "U-Boot 映像偏移不符")
    require(board["uboot_payload"] == "u-boot.bin", "U-Boot 載荷契約不符")
    require(board["partition_table"] == "msdos", "40 KiB 載荷必須搭配 MBR 契約")
    require(board["partition_start_sector"] == 8192, "FAT 開機分割區起點不符")
    require(board["root_partition_start_sector"] == 532480, "根分割區起點不符")
    require(board["root_filesystem_label"] == "BPI-ROOT", "根檔案系統標籤不符")
    require(board["root_partition_label"] == "BPI-ROOT", "根分割區標籤不符")
    require(board["boot_partition_label"] == "BPI-BOOT", "開機分割區標籤不符")
    require(board["boot_configuration"] == "realtek_bpi_uenv", "Realtek 開機設定類型不符")
    require(len(board["dtbs"]) == 2, "必須保留 1 GiB 與 2 GiB DTB")
    require(board["vendor_boot_dtbs"] == board["dtbs"], "vendor boot 的雙 DTB 契約不符")
    require(board["storage_contract"]["pcie_node"] == "/pcie@98060000", "PCIe DT 節點不符")
    for section in ("storage_contract", "network_contract", "usb_contract", "display_contract", "io_contract", "wireless_contract"):
        require(section in board, f"缺少板級介面契約：{section}")
    require(board["display_contract"]["kernel_gpu_driver_verified"] is False, "不得以 DT 節點宣稱 GPU 驅動成立")
    require(board["display_contract"]["userspace_acceleration_verified"] is False, "不得宣稱使用者空間 GPU 加速成立")
    require(board["display_contract"]["video_decode_verified"] is False, "不得宣稱視訊硬解成立")

    for item in config["documentation_evidence"]:
        require(item["local_path"].startswith("/media/pi/SMCI/bpi/"), "文件證據必須是已知本機路徑")
        require(item["included_in_candidate"] is False, "外部文件不得封裝進候選")
        require(item["redistribution_license_verified"] is False, "外部文件授權不得誤標已確認")

    if config["component_build_completed"]:
        require(config["candidate_level"] == "L2 內部軟體候選", "L2 必須保留既有元件證據")
        require(config["candidate_scope"] == "internal-l2", "L2 候選範圍不符")
        evidence = config["component_build_evidence"]
        require(evidence["local_evidence_root"] == EXPECTED_OUTPUT, "可攜元件證據路徑不符")
        require(evidence["full_rootfs_image_built"] is False, "元件證據不得冒充完整映像")
        require(evidence["uboot_rebuild_hash_match"] is True, "U-Boot 重建雜湊必須一致")
        require(set(evidence["artifacts"]) == EXPECTED_COMPONENTS, "元件集合不完整")
        require(evidence["uboot_rebuild_sha256"] == evidence["artifacts"]["u-boot.bin"]["sha256"], "U-Boot 證據互相矛盾")
        require(evidence["source_revision"] == EXPECTED_BSP, "元件證據來源提交不符")
        require(evidence["work_size_kib"] <= 10 * 1024 * 1024, "元件工作目錄超過契約上限")
        require(evidence["uboot_warning_count"] >= 0, "U-Boot 警告數無效")
        require(evidence["linux_warning_count"] >= 0, "Linux 警告數無效")
        require(evidence["linked_unrebuilt_source_asset_count"] == 6, "已嵌入未重建資產數量不符")
        require(evidence["conditional_unlinked_prebuilt_asset_count"] == 4, "條件式未連結資產數量不符")
    else:
        require(config["candidate_level"] == "L0 來源契約", "未保存元件不得標示 L1")
        require(config["candidate_scope"] == "internal-source-only", "L0 候選範圍不符")
        require("component_build_evidence" not in config, "未完成建置不得保留元件證據")

    source_argument = arguments.固定來源目錄.resolve() if arguments.固定來源目錄 else None
    source_environment = os.environ.get("M4_SOURCE_DIR")
    source = source_argument or (Path(source_environment).resolve() if source_environment else None)
    if source is not None:
        verify_source_tree(config, source)

    if arguments.print_source_contract_projection_sha256:
        print(projection)
    else:
        print("M4 固定來源、授權邊界與證據等級檢查通過。")


if __name__ == "__main__":
    main()
