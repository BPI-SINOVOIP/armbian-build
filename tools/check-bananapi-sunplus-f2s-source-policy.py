#!/usr/bin/env python3
"""檢查 Banana Pi F2S 固定來源、啟動資產與發布邊界。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config/validation/bananapi-sunplus-sp7021-f2s-legacy.json"
)
BOARD = ROOT / "config/boards/bananapif2s.wip"
FAMILY = (
    ROOT
    / "config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc"
)
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/sunplus-sp7021-bpi-5.4"
    / "0001-dts-identify-bananapi-f2s.patch"
)
UBOOT_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy"
    / "0002-dts-identify-bananapi-f2s.patch"
)
UBOOT_REPRODUCIBLE_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy"
    / "0003-tools-quickboot-honor-source-date-epoch.patch"
)


def fail(message: str) -> None:
    raise SystemExit(f"F2S 來源政策拒絕：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    data = json.loads(config_path.read_text(encoding="utf-8"))
    board_text = BOARD.read_text(encoding="utf-8")
    family_text = FAMILY.read_text(encoding="utf-8")
    revision = "3eee97bd8fb7582c2d9942a533647c3d78222bb5"

    require(data["candidate_branch"] == "legacy", "候選分支不是 legacy")
    require(data["kernel_family"] == "sunplus-sp7021-bpi", "核心家族不符")
    for key in ("linux_commit", "uboot_commit"):
        require(data[key] == revision, f"{key} 未固定至已審查提交")
    for key in ("linux_ref", "uboot_ref"):
        require(data[key] == f"commit:{revision}", f"{key} 不是精確提交")
    require(data["atf_applicable"] is False, "SP7021 不應宣告 TF-A")
    for key in (
        "full_image_built",
        "hardware_validated",
        "public_release_allowed",
        "hardware_claims_allowed",
    ):
        require(data[key] is False, f"{key} 必須維持 false")
    require(data["component_build_completed"] is True,
            "元件編譯完成狀態必須為 true")
    component_evidence = data["component_build_evidence"]
    require(component_evidence["source_revision"] == revision,
            "元件證據來源提交不符")
    require(component_evidence["full_rootfs_image_built"] is False,
            "元件證據不得宣稱建立完整 rootfs")
    require(component_evidence["uboot_rebuild_hash_match"] is True,
            "U-Boot 重建雜湊守門沒有通過")
    toolchain = component_evidence["toolchain"]
    require(toolchain["gcc_size"] > 0, "固定工具鏈 GCC 大小無效")
    for key in ("gcc_sha256", "manifest_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", toolchain[key]) is not None,
                f"固定工具鏈 {key} 格式不符")
    require(toolchain["included_in_runtime_image"] is False,
            "固定工具鏈不得封裝至執行映像")
    require(toolchain["redistribution_license_verified"] is False,
            "固定工具鏈不得宣稱已確認再散布授權")
    for name, artifact in component_evidence["artifacts"].items():
        require(artifact["size"] > 0, f"{name} 的元件大小無效")
        require(re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is not None,
                f"{name} 的元件 SHA-256 格式不符")
    documentation_evidence = data["documentation_evidence"]
    require(len(documentation_evidence) == 4, "本機文件證據數量不符")
    for evidence in documentation_evidence:
        require(evidence["local_path"].startswith("/media/pi/SMCI/bpi/doc/"),
                "本機文件證據路徑不在受控文件樹")
        require(re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"]) is not None,
                "本機文件證據 SHA-256 格式不符")
        require(evidence["included_in_candidate"] is False,
                "本機文件證據不得封裝至候選")
        require(evidence["redistribution_license_verified"] is False,
                "本機文件證據不得宣稱已確認再散布授權")

    require('BOARD_MAINTAINER="BPI-SINOVOIP"' in board_text, "缺少維護者")
    require(
        f'SUNPLUS_BPI_BSP_BRANCH="commit:{revision}"' in board_text,
        "板檔沒有固定 BSP 提交",
    )
    require(
        'declare -g IMAGE_PARTITION_TABLE="msdos"' in board_text,
        "板檔沒有固定 MBR 分割表",
    )
    for option in (
        "USB_CONFIGFS",
        "USB_CONFIGFS_MASS_STORAGE",
        "SUNPLUS_SP7021_THERMAL",
        "SUNPLUS_WATCHDOG",
    ):
        require(option in board_text, f"板檔缺少核心診斷設定 {option}")

    assets = data["source_assets"]
    require(len(assets) == 2, "預建啟動資產數量不符")
    for path, asset in assets.items():
        require(re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) is not None,
                f"{path} 的 SHA-256 格式不符")
        require(asset["size"] > 0, f"{path} 的大小無效")
        require(asset["source_build_available"] is False,
                f"{path} 不得宣稱可由來源建置")
        require(asset["redistribution_license_verified"] is False,
                f"{path} 不得宣稱已確認再散布授權")
        require(path in board_text, f"板檔未列出資產 {path}")
        require(asset["sha256"] in board_text, f"板檔未固定資產雜湊 {path}")

    for required in (
        "sunplus_sp7021_bpi_verify_prebuilt_boot_asset",
        "ROOT_PART_UUID",
        "root=UUID=${ROOT_PART_UUID}",
    ):
        require(required in family_text, f"家族整合缺少 {required}")
    require("root /dev/mmcblk1p2" not in family_text,
            "產生的 uEnv 仍硬編碼 mmcblk 根裝置")

    for patch_path in (KERNEL_PATCH, UBOOT_PATCH):
        patch_text = patch_path.read_text(encoding="utf-8")
        require('model = "Banana Pi BPI-F2S";' in patch_text,
                f"{patch_path.name} 缺少板級 model")
        require('compatible = "sinovoip,bpi-f2s", "sunplus,sp7021-achip";'
                in patch_text, f"{patch_path.name} 缺少板級 compatible")
    reproducible_patch_text = UBOOT_REPRODUCIBLE_PATCH.read_text(
        encoding="utf-8"
    )
    require("imagetool_get_source_date" in reproducible_patch_text,
            "quickboot 映像沒有使用固定來源時間")

    policy = data["boards"]["bananapif2s"]
    require(policy["boot_configuration"] == "sunplus_uenv", "boot 契約不符")
    require(policy["boot_partition_number"] == 1, "boot 分割區編號不符")
    require(policy["root_partition_number"] == 2, "root 分割區編號不符")
    require(policy["partition_table"] == "msdos", "分割表契約不符")
    require(policy["uboot_payloads"] == ["u-boot.img@17408"],
            "U-Boot 原始偏移不符")
    require(policy["dtb_sha256"] == component_evidence["artifacts"]
            ["sp7021-bpi-f2s.dtb"]["sha256"], "DTB 證據雜湊不一致")
    require(policy["sd_node"] == "/soc@B/mmc@sdcard",
            "SD 節點沒有固定至實際 DTB 路徑")

    print("F2S 固定來源、啟動資產與發布邊界檢查通過。")


if __name__ == "__main__":
    main()
