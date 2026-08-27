#!/usr/bin/env python3
"""檢查 Banana Pi F2P 固定來源、SD-only 啟動鏈與證據狀態。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
)
BOARD = ROOT / "config/boards/bananapif2p.wip"
STATUS = ROOT / "config/bananapi-optimization-status.json"
FAMILY = (
    ROOT
    / "config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc"
)
UBOOT_REPRODUCIBLE_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy"
    / "0003-tools-quickboot-honor-source-date-epoch.patch"
)


def fail(message: str) -> None:
    raise SystemExit(f"F2P 來源政策拒絕：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_commit(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def validate_image_state(data: dict[str, object], global_status: dict[str, object]) -> None:
    level = data["candidate_level"]
    global_level = global_status["evidence"]["bananapif2p"]["level"]
    if level == "L1 元件候選":
        require(global_level == "L1", "全域證據等級與 L1 契約不一致")
        require(data["full_image_built"] is False, "L1 不得宣稱完整映像已建置")
        require(data["rootfs_image_built"] is False, "L1 不得宣稱 rootfs 已建置")
        require("image_build_evidence" not in data, "L1 不得夾帶未受控映像證據")
        return

    require(level == "L2 內部軟體候選", "候選層級不受支援")
    require(global_level == "L2", "全域證據等級與 L2 契約不一致")
    require(data["full_image_built"] is True, "L2 缺少完整映像建置狀態")
    require(data["rootfs_image_built"] is True, "L2 缺少 rootfs 建置狀態")
    evidence = data.get("image_build_evidence")
    require(isinstance(evidence, dict), "L2 缺少映像建置證據")
    require(evidence["evidence_level"] == "L2", "映像證據不是 L2")
    require(valid_commit(evidence["source_commit"]), "映像來源提交格式不符")
    require(valid_commit(evidence["verifier_commit"]), "映像驗證器提交格式不符")
    require(
        evidence["source_commit"] == evidence["verifier_commit"],
        "L2 映像來源與驗證器提交不一致",
    )
    require(evidence["read_only_content_verified"] is True, "缺少唯讀內容驗證")
    for key in (
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require(valid_sha256(evidence[key]), f"{key} 格式不符")
    require(
        evidence["build_validation_config_sha256"]
        == evidence["verification_config_sha256"],
        "建置與驗證契約雜湊不一致",
    )
    for name in ("image", "archive"):
        artifact = evidence[name]
        require(artifact["size"] > 0, f"{name} 大小無效")
        require(valid_sha256(artifact["sha256"]), f"{name} 雜湊格式不符")


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    data = json.loads(config_path.read_text(encoding="utf-8"))
    board_text = BOARD.read_text(encoding="utf-8")
    family_text = FAMILY.read_text(encoding="utf-8")
    global_status = json.loads(STATUS.read_text(encoding="utf-8"))
    revision = "3eee97bd8fb7582c2d9942a533647c3d78222bb5"
    ispboot_sha256 = "e01081a92b55156868b9df7918e0d5f503d1dda3af94335ed24637786124964a"
    foreign_asset = "BPI-F2S-xboot-emmc-boot0-0k.img.gz"

    require(data["schema_version"] == 1, "驗證契約版本不符")
    require(data["candidate_branch"] == "legacy", "候選分支不是 legacy")
    require(data["kernel_family"] == "sunplus-sp7021-bpi", "核心家族不符")
    require(data["candidate_scope"] == "internal-sd-only", "候選範圍不是內部 SD-only")
    require(data["source_date_epoch"] == 1609074838, "來源時間戳不符")
    for key in ("linux_commit", "uboot_commit"):
        require(data[key] == revision, f"{key} 未固定至已審查提交")
    for key in ("linux_ref", "uboot_ref"):
        require(data[key] == f"commit:{revision}", f"{key} 不是精確提交")
    require(data["linux_source"] == data["uboot_source"], "Linux 與 U-Boot BSP 來源不一致")
    require(data["atf_applicable"] is False, "SP7021 不應宣稱 TF-A")
    require(data["trusted_firmware_a"]["applicable"] is False, "TF-A 相容欄位不一致")
    for key in ("hardware_validated", "public_release_allowed", "hardware_claims_allowed"):
        require(data[key] is False, f"{key} 必須維持 false")
    validate_image_state(data, global_status)

    require(data["linux_license_path"] == "linux-sp/COPYING", "Linux 授權路徑不符")
    require(data["uboot_license_path"] == "u-boot-sp/Licenses/README", "U-Boot 授權路徑不符")
    for key in ("linux_license_sha256", "uboot_license_sha256"):
        require(valid_sha256(data[key]), f"{key} 格式不符")

    toolchain = data["toolchain"]
    require(toolchain["gcc_size"] > 0, "固定工具鏈 GCC 大小無效")
    for key in ("gcc_sha256", "manifest_sha256"):
        require(valid_sha256(toolchain[key]), f"固定工具鏈 {key} 格式不符")
    require(toolchain["included_in_runtime_image"] is False, "工具鏈不得封裝至映像")
    require(
        toolchain["redistribution_license_verified"] is False,
        "工具鏈不得宣稱已完成再散布授權稽核",
    )
    require(toolchain["separate_redistribution_audit_complete"] is False, "工具鏈授權狀態不得開放")

    source = data["source_commits"]["bsp"]
    require(source["revision"] == revision, "BSP revision 不符")
    require(source["ref"] == f"commit:{revision}", "BSP ref 不符")
    require(set(source["contains"]) >= {"linux-sp", "u-boot-sp", "sp-pack"}, "BSP 內容契約不完整")
    assets = data["source_assets"]
    require(len(assets) == 2, "啟動資產集合不符")
    ispboot = assets["sp-pack/sp7021/common/bin/ISPBOOOT.BIN"]
    require(ispboot["size"] == 65536, "ISPBOOOT.BIN 大小不符")
    require(ispboot["sha256"] == ispboot_sha256, "ISPBOOOT.BIN 雜湊不符")
    for path, asset in assets.items():
        require(asset["size"] > 0, f"{path} 大小無效")
        require(valid_sha256(asset["sha256"]), f"{path} 雜湊格式不符")
        require(asset["source_build_available"] is False, f"{path} 不得宣稱可由來源建置")
        require(
            asset["redistribution_license_verified"] is False,
            f"{path} 不得宣稱已確認再散布授權",
        )
    require(data["firmware_redistribution_license_verified"] is False, "韌體授權狀態不得開放")

    require(data["component_build_completed"] is True, "元件建置狀態不是完成")
    component = data["component_build_evidence"]
    require(component["artifacts"]["ispboot"]["sha256"] == ispboot_sha256, "元件 xboot 雜湊不一致")
    require(
        component["artifacts"]["linux_dtb"]["sha256"]
        == data["boards"]["bananapif2p"]["dtb_sha256"],
        "元件 DTB 與板級契約不一致",
    )
    for name, artifact in component["artifacts"].items():
        require(artifact["size"] > 0, f"{name} 元件大小無效")
        require(valid_sha256(artifact["sha256"]), f"{name} 元件雜湊格式不符")

    require(len(data["documentation_evidence"]) == 4, "文件證據數量不符")
    for evidence in data["documentation_evidence"]:
        require(evidence["local_path"].startswith("/media/pi/SMCI/bpi/doc/"), "文件證據路徑不受控")
        require(valid_sha256(evidence["sha256"]), "文件證據雜湊格式不符")
        require(evidence["included_in_candidate"] is False, "文件證據不得封裝")
        require(evidence["redistribution_license_verified"] is False, "文件證據不得開放授權結論")

    for expected in (
        'BOARD_MAINTAINER="BPI-SINOVOIP"',
        f'SUNPLUS_BPI_BSP_BRANCH="commit:{revision}"',
        'SUNPLUS_BPI_SD_XBOOT_ASSET="sp-pack/sp7021/common/bin/ISPBOOOT.BIN"',
        f'SUNPLUS_BPI_SD_XBOOT_SHA256="{ispboot_sha256}"',
        'SUNPLUS_BPI_EMMC_XBOOT_ASSET=""',
        'SUNPLUS_BPI_CANDIDATE_MEDIA="sd-only"',
        'declare -g IMAGE_PARTITION_TABLE="msdos"',
        "USB_CONFIGFS_MASS_STORAGE",
    ):
        require(expected in board_text, f"板檔缺少 {expected}")
    require(foreign_asset not in board_text, "F2P 板檔不得引用 F2S eMMC xboot")
    for expected in (
        "sunplus_sp7021_bpi_verify_prebuilt_boot_asset",
        "ROOT_PART_UUID",
        "root=UUID=${ROOT_PART_UUID}",
        'if [[ -n "${SUNPLUS_BPI_EMMC_XBOOT_ASSET}" ]]',
    ):
        require(expected in family_text, f"家族整合缺少 {expected}")
    require("root /dev/mmcblk1p2" not in family_text, "uEnv 仍硬編碼 mmcblk 根裝置")
    require("imagetool_get_source_date" in UBOOT_REPRODUCIBLE_PATCH.read_text(encoding="utf-8"), "U-Boot 未固定時間")

    policy = data["boards"]["bananapif2p"]
    require(policy["partition_table"] == "msdos", "分割表契約不符")
    require(policy["required_partitions"] == ["1:*:8192:*", "2:*:*:*"], "分割區集合不符")
    require(policy["boot_partition_number"] == 1, "boot 分割區編號不符")
    require(policy["root_partition_number"] == 2, "root 分割區編號不符")
    require(policy["boot_configuration"] == "sunplus_uenv", "boot 契約不符")
    require(policy["uboot_payloads"] == ["u-boot.img@17408"], "U-Boot 寫入偏移不符")
    require(policy["uboot_package_only_payloads"] == ["ISPBOOOT.BIN"], "xboot 封裝契約不符")
    require(foreign_asset in policy["forbidden_packaged_assets"], "未禁止 F2S eMMC xboot")
    require(any(foreign_asset in item for item in policy["uboot_target_make_forbidden"]), "U-Boot target 未禁止 F2S 資產")
    require(policy["candidate_boot_media"] == ["microSD"], "候選媒體不是 microSD")
    require(policy["supported_boot_media"] == [], "未實測前不得列出已支援媒體")
    require(policy["emmc_install_allowed"] is False, "F2P 不得開放 eMMC 安裝")
    require(policy["sd_node"] == "/soc@B/mmc@sdcard", "SD 節點不符")
    require(policy["sd_bus_width"] == 4, "SD 匯流排寬度不符")

    print("F2P 固定來源、SD-only 啟動鏈與證據狀態檢查通過。")


if __name__ == "__main__":
    main()
