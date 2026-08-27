#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
BOARD = ROOT / "config/boards/bananapim1super.wip"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3528-bananapi-m1-super.dts"
UBOOT_DTS = ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3528-bananapi-m1-super.dts"
UBOOT_CONFIG = ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-m1-super-rk3528_defconfig"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"BPI-M1 Super 政策守門失敗：{message}")


with CONFIG.open(encoding="utf-8") as stream:
    policy = json.load(stream)

require(BOARD.is_file(), "板檔必須維持 .wip")
require(not (BOARD.parent / "bananapim1super.conf").exists(), "不得提前升級為正式板檔")
require(not (BOARD.parent / "bananapim1super.csc").exists(), "不得建立未核准的社群板檔")
require(policy["candidate_level"] == "L1 元件候選", "候選層級必須是 L1 元件候選")
require(policy["candidate_scope"] == "internal-component-only", "候選範圍必須限制為內部元件")
require(policy["candidate_public_release_approved"] is False, "不得核准公開發布")
require(policy["public_release_allowed"] is False, "不得允許公開發布")
require(policy["hardware_validation_complete"] is False, "不得宣稱實機驗證完成")
require(policy["hardware_claims_allowed"] is False, "不得允許硬體功能聲明")
require(policy["component_build_completed"] is True, "元件建置必須明確完成")
require(policy["rootfs_image_built"] is False, "不得宣稱完整根檔案系統映像已建置")
require(policy["firmware_redistribution_audit_complete"] is False, "韌體授權稽核不得標為完成")
require(policy["atf_source_build_available"] is False, "不得宣稱 RK3528 TF-A 可由固定來源建置")
require(policy["identity_evidence"]["wifi_bom_conflict_resolved"] is False, "Wi-Fi 料號矛盾不得標為已解決")
component_evidence = policy["component_build_evidence"]
require(
    component_evidence["portable_manifest_sha256"]
    == "ef452fbc47115ffc34359c44a202733217ff32e95d946c160f8e4ea1ebc3b22a",
    "可攜元件清單雜湊不符",
)
require(component_evidence["portable_artifact_count"] == 6, "可攜元件數量不符")
require(component_evidence["full_rootfs_image_built"] is False, "不得宣稱完整根檔案系統映像已建置")
require(component_evidence["hardware_tested"] is False, "不得宣稱已完成實機測試")
require(component_evidence["armbian_uboot_patch_stack_complete"] is False, "不得宣稱完整 U-Boot 修補佇列已通過")

expected_component_hashes = {
    "linux_dtb": "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6",
    "uboot_spl": "43c518cf0f5c98c7228d22920c47d5d22e151536fa8e8a984b3522d76b2430be",
    "uboot_dtb": "b5bdc6143f8a3d2462e12a5a943c0953e85bb7beb9ac499b3d9552540dce9a81",
    "uboot_fit": "7d095910efac37607dbb65389603aa672b77492c4557f5637ab4ad5a68272f6c",
    "idbloader": "513c843f4cb97c3a62508d5b1238b676e29a997eaeeb382a61b808a3198e2c3c",
}
for artifact, expected in expected_component_hashes.items():
    require(
        component_evidence[artifact]["sha256"] == expected,
        f"{artifact} 元件雜湊與固定證據不一致",
    )

expected_commits = {
    "linux_commit": "c6157104418d012823413c02f9222f3fe123dd25",
    "firmware_commit": "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
    "atf_commit": "c17351450c8a513ca3f30f936e26a71db693a145",
    "rkbin_commit": "1d3c61008fa823936ae7a59615393f8294b64456",
}
for field, expected in expected_commits.items():
    require(policy[field] == expected, f"{field} 未固定至核准提交")

require(policy["rkbin_copy_and_distribution_grant_present"] is True, "RKBin 必須存在二進位散布授權")
require(policy["rkbin_standalone_distribution_authorized"] is False, "RKBin 不得獨立散布")
require(policy["rkbin_binary_modification_authorized"] is False, "RKBin 不得修改")
require(policy["rkbin_license_must_accompany_distribution"] is True, "RKBin 授權檔必須隨附")

board_text = BOARD.read_text(encoding="utf-8")
for required in (
    'BOOTCONFIG="bananapi-m1-super-rk3528_defconfig"',
    'BOOT_FDT_FILE="rockchip/rk3528-bananapi-m1-super.dtb"',
    'BOOTBRANCH_BOARD="commit:39cd993e5d6296635438e84f4576b3a9bf76f86e"',
    'KERNELBRANCH_BOARD="commit:c6157104418d012823413c02f9222f3fe123dd25"',
    'declare -g BOOTPATCHDIR="legacy/u-boot-radxa-rk35xx"',
    'declare -g KERNELPATCHDIR="rk35xx-vendor-6.1"',
    'declare -g LINUXCONFIG="linux-rk35xx-vendor"',
    'declare -g ATF_COMPILE="no"',
    'declare -g ATFSOURCE=""',
):
    require(required in board_text, f"板檔缺少固定設定：{required}")
for forbidden in ("source \"${SRC}/config/boards/armsom-sige1.csc\"", "hinlink_rk3528_defconfig"):
    require(forbidden not in board_text, f"板檔仍含舊繼承：{forbidden}")

linux_text = LINUX_DTS.read_text(encoding="utf-8")
for required in (
    '#include "rk3528-armsom-sige1.dts"',
    'model = "Banana Pi M1 Super";',
    'compatible = "bananapi,bpi-m1-super", "armsom,sige1", "rockchip,rk3528";',
    "&i2c0",
    "&i2c1",
    "&spi0",
    'wifi_chip_type = "ap6275s";',
):
    require(required in linux_text, f"Linux DTS 缺少契約：{required}")

uboot_text = UBOOT_DTS.read_text(encoding="utf-8")
require('model = "Banana Pi M1 Super";' in uboot_text, "U-Boot DTS 缺少專屬 model")
require('"bananapi,bpi-m1-super"' in uboot_text, "U-Boot DTS 缺少專屬 compatible")
require("Hinlink H28K" not in uboot_text, "U-Boot DTS 不得保留 H28K 身分")

defconfig_text = UBOOT_CONFIG.read_text(encoding="utf-8")
require('CONFIG_DEFAULT_DEVICE_TREE="rk3528-bananapi-m1-super"' in defconfig_text, "U-Boot defconfig 未使用專屬 DTS")
require('CONFIG_DEFAULT_FDT_FILE="rk3528-bananapi-m1-super"' in defconfig_text, "U-Boot 預設 FDT 未固定")
require("rk3528-hinlink-h28k" not in defconfig_text, "U-Boot defconfig 不得保留 H28K DTS")

print("BPI-M1 Super 固定來源、授權與發布政策守門通過。")
