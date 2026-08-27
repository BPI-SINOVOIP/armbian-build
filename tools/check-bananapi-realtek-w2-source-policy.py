#!/usr/bin/env python3
"""檢查 Banana Pi W2 固定來源、二進位資產與發布邊界。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config/validation/bananapi-realtek-rtd1296-w2-legacy.json"
)
BOARD = ROOT / "config/boards/bananapiw2.wip"
STATUS = ROOT / "config/bananapi-optimization-status.json"
FAMILY = (
    ROOT
    / "config/sources/families/include/realtek_bpi_legacy_common.inc"
)
UBOOT_TIMESTAMP_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-realtek-rtd129x-bpi-legacy"
    / "0003-build-use-source-date-epoch.patch"
)
KERNEL_IDENTITY_PATCH = (
    ROOT
    / "patch/kernel/archive/realtek-rtd129x-bpi-4.9"
    / "0002-dts-identify-bananapi-w2.patch"
)


def fail(message: str) -> None:
    raise SystemExit(f"W2 來源政策拒絕：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    data = json.loads(config_path.read_text(encoding="utf-8"))
    board_text = BOARD.read_text(encoding="utf-8")
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    family_text = FAMILY.read_text(encoding="utf-8")
    timestamp_patch = UBOOT_TIMESTAMP_PATCH.read_text(encoding="utf-8")
    identity_patch = KERNEL_IDENTITY_PATCH.read_text(encoding="utf-8")
    revision = "6e6aefc35dc50b1b8231cdb03a995d088f29eb21"
    firmware_revision = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"

    require(data["schema_version"] == 1, "驗證契約版本不符")
    require(data["candidate_branch"] == "legacy", "候選分支不是 legacy")
    require(data["candidate_level"] == "L1 元件候選", "候選層級不是 L1 元件候選")
    require(data["candidate_scope"] == "internal-component-only", "候選範圍不是內部元件")
    require(data["kernel_family"] == "realtek-rtd129x-bpi", "核心家族不符")
    require(data["source_date_epoch"] == 1571768256, "來源時間戳不符")
    for key in ("linux_commit", "uboot_commit"):
        require(data[key] == revision, f"{key} 未固定至已審查提交")
    for key in ("linux_ref", "uboot_ref"):
        require(data[key] == f"commit:{revision}", f"{key} 不是精確提交")
    require(data["firmware_commit"] == firmware_revision, "韌體提交不符")
    require(
        data["firmware_ref"] == f"commit:{firmware_revision}",
        "韌體 ref 不是精確提交",
    )
    require(data["atf_applicable"] is False, "不得宣稱此路徑建置 TF-A")
    require(status["evidence"]["bananapiw2"]["level"] == "L1", "全域 W2 等級不是 L1")
    require("bananapiw2" in status["open_findings"], "全域 W2 未結項目缺失")
    for key in (
        "full_image_built",
        "hardware_validated",
        "public_release_allowed",
        "hardware_claims_allowed",
    ):
        require(data[key] is False, f"{key} 必須維持 false")

    for key in ("linux_license_sha256", "uboot_license_sha256"):
        require(valid_sha256(data[key]), f"{key} 格式不符")
    require(data["linux_license_path"] == "linux-rtk/COPYING", "Linux 授權路徑不符")
    require(
        data["uboot_license_path"] == "u-boot-rtk/Licenses/README",
        "U-Boot 授權路徑不符",
    )

    toolchain = data["build_toolchain"]
    require(toolchain["size"] > 0, "工具鏈 GCC 大小無效")
    require(valid_sha256(toolchain["sha256"]), "工具鏈 GCC 雜湊無效")
    require(valid_sha256(toolchain["manifest_sha256"]), "工具鏈清單雜湊無效")
    require(toolchain["included_in_runtime_image"] is False, "工具鏈不得封裝至執行映像")
    require(
        toolchain["redistribution_license_verified"] is False,
        "工具鏈不得宣稱已確認再散布授權",
    )

    linked_assets = data["linked_prebuilt_assets"]
    expected_linked_assets = {
        "u-boot-rtk/static_lib/libefuse.a",
        "u-boot-rtk/static_lib/libsha1_util.a",
        "u-boot-rtk/static_lib/libsecurity.a",
        "u-boot-rtk/static_lib/libkeyset.a",
    }
    require(set(linked_assets) == expected_linked_assets, "U-Boot 無來源連結資產集合不符")
    for path, asset in linked_assets.items():
        require(asset["size"] > 0, f"{path} 大小無效")
        require(valid_sha256(asset["sha256"]), f"{path} 雜湊無效")
        require(asset["source_build_available"] is False, f"{path} 不得宣稱可重建")
        require(
            asset["redistribution_license_verified"] is False,
            f"{path} 不得宣稱已確認再散布授權",
        )

    runtime_assets = data["runtime_prebuilt_assets"]
    require(len(runtime_assets) == 1, "執行期預建資產數量不符")
    bluecore = runtime_assets[
        "rtk-pack/rtk/bpi-w2/configs/default/linux/bluecore.audio"
    ]
    require(bluecore["size"] == 3969840, "bluecore.audio 大小不符")
    require(valid_sha256(bluecore["sha256"]), "bluecore.audio 雜湊無效")
    require(bluecore["source_build_available"] is False, "bluecore.audio 不得宣稱可重建")
    require(
        bluecore["redistribution_license_verified"] is False,
        "bluecore.audio 不得宣稱已確認再散布授權",
    )

    for evidence in data["documentation_evidence"]:
        require(
            evidence["local_path"].startswith("/media/pi/SMCI/bpi/"),
            "文件證據不在本機受控樹",
        )
        require(valid_sha256(evidence["sha256"]), "文件證據雜湊格式不符")
        require(evidence["included_in_candidate"] is False, "文件證據不得封裝")
        require(
            evidence["redistribution_license_verified"] is False,
            "文件證據不得宣稱已確認再散布授權",
        )

    for expected in (
        'BOARD_MAINTAINER="BPI-SINOVOIP"',
        'ROOT_FS_LABEL="BPI-ROOT"',
        'REALTEK_BPI_ROOT_LABEL="BPI-ROOT"',
        f'REALTEK_BPI_BSP_BRANCH="commit:{revision}"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{firmware_revision}"',
        'declare -g IMAGE_PARTITION_TABLE="msdos"',
        "USB_CONFIGFS_MASS_STORAGE",
    ):
        require(expected in board_text, f"板級設定缺少 {expected}")
    require(
        "build_custom_uboot__realtek_bpi_legacy_bsp" in family_text,
        "Realtek 共用家族缺少 vendor U-Boot 建置入口",
    )

    require("REALTEK_BPI_ROOT_LABEL" in family_text, "Realtek 家族缺少根標籤契約")
    require("sed -i -E" in family_text, "Realtek 家族未正規化 U-Boot 根標籤")
    require("SOURCE_DATE_EPOCH" in timestamp_patch, "U-Boot 修補未固定建置時間")
    require("date -u" in timestamp_patch, "U-Boot 修補未固定時區")
    require('model = "Banana Pi BPI-W2";' in identity_patch, "核心修補缺少板級 model")
    require(
        'compatible = "bananapi,bpi-w2", "realtek,rtd1296";' in identity_patch,
        "核心修補缺少板級 compatible",
    )

    board = data["boards"]["bananapiw2"]
    require(board["partition_table"] == "msdos", "分割表契約不符")
    require(board["uboot_write_offset_bytes"] == 40960, "U-Boot 寫入偏移不符")
    require(board["root_filesystem_label"] == "BPI-ROOT", "根標籤契約不符")
    require(board["usb_contract"]["hardware_role_validated"] is False, "USB 角色不得宣稱實測")
    require(board["display_contract"]["hdmi_tx_status"] == "okay", "HDMI TX 狀態不符")
    require(board["display_contract"]["hdmi_rx_status"] == "disabled", "HDMI RX 狀態不符")
    require(
        board["display_contract"]["displayport_tx_status"] == "okay",
        "DisplayPort TX 狀態不符",
    )
    require(board["display_contract"]["acceleration_validated"] is False, "顯示加速不得宣稱實測")
    require(board["io_contract"]["pin_mapping_hardware_validated"] is False, "腳位不得宣稱實測")
    require(board["wireless_contract"]["onboard_wifi_present"] is False, "W2 不得宣稱板載 Wi-Fi")

    if data["component_build_completed"]:
        evidence = data.get("component_build_evidence")
        require(isinstance(evidence, dict), "元件完成但缺少元件證據")
        require(
            evidence["local_evidence_root"]
            == "output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy",
            "本機元件證據路徑不符",
        )
        require(evidence["source_revision"] == revision, "元件來源提交不符")
        require(evidence["full_rootfs_image_built"] is False, "元件證據不得宣稱完整 rootfs")
        require(evidence["uboot_rebuild_hash_match"] is True, "U-Boot 重建雜湊不一致")
        require(valid_sha256(evidence["uboot_rebuild_sha256"]), "U-Boot 重建雜湊無效")
        require(evidence["work_size_kib"] <= 10 * 1024 * 1024, "元件工作目錄超過上限")
        for name, artifact in evidence["artifacts"].items():
            require(artifact["size"] > 0, f"{name} 大小無效")
            require(valid_sha256(artifact["sha256"]), f"{name} 雜湊無效")
        require(
            evidence["artifacts"]["u-boot.bin"]["sha256"]
            == evidence["uboot_rebuild_sha256"],
            "U-Boot 產物與重建雜湊不符",
        )
        require(
            board["dtb_sha256"]
            == evidence["artifacts"][board["dtb"]]["sha256"],
            "DTB 板級雜湊與元件證據不符",
        )

    print("W2 固定來源、二進位資產與發布邊界檢查通過。")


if __name__ == "__main__":
    main()
