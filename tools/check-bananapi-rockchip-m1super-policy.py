#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
BOARD = ROOT / "config/boards/bananapim1super.wip"
STATUS = ROOT / "config/bananapi-optimization-status.json"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3528-bananapi-m1-super.dts"
UBOOT_DTS = ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3528-bananapi-m1-super.dts"
UBOOT_CONFIG = ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-m1-super-rk3528_defconfig"

EXPECTED_COMPONENT_DTB_SHA256 = (
    "68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6"
)
EXPECTED_SOURCE_DATE_EPOCH = 1787082913
EXPECTED_UBOOT_PAYLOAD_SIZES = [
    "idbloader.img=311296",
    "u-boot.itb=1320960",
]
EXPECTED_PARTITIONS = ["1:*:32768:4691968"]
EXPECTED_PARTITION_TYPES = ["1:b921b045-1df0-41c3-af44-4c6f280d3fae"]
EXPECTED_FINAL_KERNEL_CONFIG_SHA256 = (
    "24edbbaabf1bd7960e7c2647ec7e96c25e2e9bf4de5a440c30827eb15b162e9e"
)
EXPECTED_FINAL_UBOOT_CONFIG_SHA256 = (
    "c56f7986bc9d636d51439509c4ad43b8adc247b97783717de61553bba8c7bf60"
)
EXPECTED_FIRMWARE_BLOBS = {
    "/lib/firmware/brcm/brcmfmac43752-sdio.bin": (
        "46f62076768e50938d0e29b306b24d4663de20b07b474c4759d5801fcbf0bdde"
    ),
    "/lib/firmware/brcm/brcmfmac43752-sdio.clm_blob": (
        "5143146e1923f87f7aab8df043abcf89a657fa9fdc3b22a38806399730d9a97a"
    ),
    "/lib/firmware/brcm/brcmfmac43752-sdio.txt": (
        "2d2723101fe9c66c853ddb1e2d715851ba100a4390f8ac72fc84dd35736cc66f"
    ),
}
EXPECTED_MODULE_PATHS = {
    "kernel/drivers/bluetooth/hci_uart.ko",
    "kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac/brcmfmac.ko",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"BPI-M1 Super 政策守門失敗：{message}")


def require_sha256(value: object, message: str) -> None:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        message,
    )


def require_commit(value: object, message: str) -> None:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
        message,
    )


def validate_payload_hashes(value: object) -> None:
    require(isinstance(value, list), "U-Boot 載荷雜湊必須是清單")
    hashes = {}
    for item in value:
        require(isinstance(item, str) and "=" in item, "U-Boot 載荷雜湊格式不符")
        name, digest = item.split("=", 1)
        require(name not in hashes, f"U-Boot 載荷雜湊重複：{name}")
        require_sha256(digest, f"U-Boot 載荷雜湊格式不符：{name}")
        hashes[name] = digest
    require(
        set(hashes) == {"idbloader.img", "u-boot.itb"},
        "U-Boot 載荷雜湊檔名集合不符",
    )


def validate_candidate_state(policy: dict) -> None:
    level = policy.get("candidate_level")
    require(level in {"L1 元件候選", "L2 內部軟體候選"}, "候選層級只允許 L1 或內部 L2")

    expected_scope = {
        "L1 元件候選": "internal-component-only",
        "L2 內部軟體候選": "internal-l2",
    }[level]
    require(policy.get("candidate_scope") == expected_scope, "候選層級與範圍不成對")
    require(policy.get("component_build_completed") is True, "固定來源元件建置證據必須保留")
    require(policy.get("candidate_public_release_approved") is False, "不得核准公開發布")
    require(policy.get("public_release_allowed") is False, "不得允許公開發布")
    require(policy.get("hardware_validation_complete") is False, "不得宣稱實機驗證完成")
    require(policy.get("hardware_claims_allowed") is False, "不得允許硬體功能聲明")
    require(
        policy.get("source_date_epoch") == EXPECTED_SOURCE_DATE_EPOCH,
        "可重現建置時間戳不符",
    )

    board = policy["boards"]["bananapim1super"]
    require(
        board.get("component_dtb_sha256") == EXPECTED_COMPONENT_DTB_SHA256,
        "元件 DTB 雜湊不符",
    )
    require(board.get("uboot_payload_sizes") == EXPECTED_UBOOT_PAYLOAD_SIZES, "U-Boot 載荷大小契約不符")
    payload_hashes = board.get("uboot_payload_sha256")
    if payload_hashes is not None:
        validate_payload_hashes(payload_hashes)
    require(board.get("required_partitions") == EXPECTED_PARTITIONS, "GPT 分割區契約不符")
    require(board.get("required_partition_types") == EXPECTED_PARTITION_TYPES, "GPT 類型契約不符")
    require(board.get("root_partition_start_sector") == 32768, "根分割區起始磁區不符")
    require(board.get("root_partition_label") == "armbi_root", "根分割區標籤不符")
    require(board.get("root_partition_filesystem_type") == "ext4", "根檔案系統型別不符")
    require(
        board.get("final_kernel_config_sha256") == EXPECTED_FINAL_KERNEL_CONFIG_SHA256,
        "最終核心設定雜湊不符",
    )
    require(
        board.get("final_uboot_config_sha256") == EXPECTED_FINAL_UBOOT_CONFIG_SHA256,
        "最終 U-Boot 設定雜湊不符",
    )

    if level == "L1 元件候選":
        require(policy.get("rootfs_image_built") is False, "L1 不得宣稱完整映像已建置")
        require("image_build_evidence" not in policy, "L1 不得攜帶正式完整映像證據")
        require(board.get("image_dtb_sha256") is None, "L1 不得宣稱已有完整映像 DTB 雜湊")
        require(board.get("dtb_sha256") == EXPECTED_COMPONENT_DTB_SHA256, "L1 預檢 DTB 契約不符")
        require(
            board.get("dtb_sha256_evidence_scope") == "preflight-contract-l1",
            "L1 DTB 欄位缺少預檢契約範圍標記",
        )
        return

    require(payload_hashes is not None, "L2 必須固定 U-Boot 載荷雜湊")
    require(policy.get("rootfs_image_built") is True, "L2 必須有完整映像建置證據")
    image_evidence = policy.get("image_build_evidence")
    require(isinstance(image_evidence, dict), "L2 缺少完整映像證據")
    require(image_evidence.get("status") == "complete", "L2 完整映像證據尚未完成")
    require(image_evidence.get("evidence_level") == "L2", "L2 映像證據層級不符")
    require(
        image_evidence.get("full_rootfs_image_built") is True,
        "L2 完整映像證據未確認根檔案系統",
    )
    require(image_evidence.get("hardware_tested") is False, "內部 L2 不得冒充實機驗證")
    require(image_evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require_commit(image_evidence.get("source_commit"), "L2 來源提交格式不符")
    require_commit(image_evidence.get("verifier_commit"), "L2 驗證器提交格式不符")
    require(
        image_evidence["source_commit"] == image_evidence["verifier_commit"],
        "L2 來源與驗證器提交不一致",
    )
    for key in (
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
    ):
        require_sha256(image_evidence.get(key), f"L2 {key} 格式不符")
    require(
        image_evidence["build_validation_config_sha256"]
        == image_evidence["verification_config_sha256"],
        "L2 建置與驗證契約雜湊不一致",
    )
    for name in ("image", "archive"):
        artifact = image_evidence.get(name, {})
        require(isinstance(artifact.get("size"), int) and artifact["size"] > 0, f"L2 {name} 大小無效")
        require_sha256(artifact.get("sha256"), f"L2 {name} 雜湊格式不符")
    image_dtb = image_evidence.get("linux_dtb", {}).get("sha256")
    require_sha256(image_dtb, "L2 缺少完整映像 DTB 雜湊")
    require(board.get("image_dtb_sha256") == image_dtb, "完整映像 DTB 專用欄位與證據不一致")
    require(board.get("dtb_sha256") == image_dtb, "完整映像 DTB 契約與證據不一致")
    require(
        board.get("dtb_sha256_evidence_scope") == "full-image-l2",
        "L2 相容 DTB 欄位未標示完整映像證據範圍",
    )


def validate_firmware_contract(policy: dict, board_text: str) -> None:
    require(
        policy.get("firmware_source") == "https://github.com/armbian/firmware",
        "Armbian 韌體來源不符",
    )
    require(
        policy.get("firmware_ref")
        == "commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
        "Armbian 韌體引用未固定",
    )
    require(
        policy.get("firmware_commit")
        == "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
        "Armbian 韌體提交未固定",
    )
    require(policy.get("verify_firmware_source_resolution") is True, "未啟用韌體來源解析守門")
    for required in (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
        'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
    ):
        require(required in board_text, f"板檔缺少韌體固定設定：{required}")

    contract = policy.get("provisional_wireless_contract", {})
    require(contract.get("contract_id") == "provisional-ap6275s", "暫定無線契約識別不符")
    require(contract.get("bom_identity_confirmed") is False, "量產無線料號仍不得標為已確認")
    require(contract.get("wifi_driver") == "brcmfmac", "暫定 Wi-Fi 驅動不符")
    require(contract.get("wifi_bus") == "SDIO", "暫定 Wi-Fi 匯流排不符")
    require(contract.get("bluetooth_driver") == "hci_uart", "暫定藍牙驅動不符")
    require(contract.get("bluetooth_bus") == "UART", "暫定藍牙匯流排不符")
    require(
        contract.get("bluetooth_firmware_identity_confirmed") is False,
        "藍牙韌體身分尚未確認",
    )
    require(contract.get("runtime_hardware_validated") is False, "暫定無線契約不得標為實機通過")
    require(
        contract.get("required_wifi_firmware_blobs") == EXPECTED_FIRMWARE_BLOBS,
        "暫定 AP6275S 韌體集合或雜湊不符",
    )
    installed = policy.get("installed_firmware_blobs", {})
    for path, expected in EXPECTED_FIRMWARE_BLOBS.items():
        require(installed.get(path) == expected, f"映像韌體契約缺少固定檔案：{path}")
    require(set(policy.get("required_kernel_module_paths", [])) == EXPECTED_MODULE_PATHS, "無線模組路徑契約不符")
    options = policy.get("common_kernel_options", {})
    for option, expected in {
        "CONFIG_BRCMFMAC": "m",
        "CONFIG_BRCMFMAC_SDIO": "y",
        "CONFIG_BT_HCIUART": "m",
        "CONFIG_BT_HCIUART_BCM": "y",
    }.items():
        require(options.get(option) == expected, f"無線核心設定不符：{option}")


def main() -> None:
    with CONFIG.open(encoding="utf-8") as stream:
        policy = json.load(stream)

    require(BOARD.is_file(), "板檔必須維持 .wip")
    require(not (BOARD.parent / "bananapim1super.conf").exists(), "不得提前升級為正式板檔")
    require(not (BOARD.parent / "bananapim1super.csc").exists(), "不得建立未核准的社群板檔")
    require(
        os.environ.get("PUBLIC_RELEASE", "no").lower() not in {"1", "true", "yes"},
        "此候選禁止公開發布",
    )
    require(
        os.environ.get("HARDWARE_CLAIMS", "no").lower() not in {"1", "true", "yes"},
        "此候選禁止硬體通過聲明",
    )
    validate_candidate_state(policy)
    with STATUS.open(encoding="utf-8") as stream:
        global_level = json.load(stream)["evidence"]["bananapim1super"]["level"]
    expected_global_level = "L1" if policy["candidate_level"] == "L1 元件候選" else "L2"
    require(global_level == expected_global_level, "全域證據等級與 M1 Super 契約不一致")
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
    require(component_evidence["full_rootfs_image_built"] is False, "元件證據不得冒充完整映像")
    require(component_evidence["hardware_tested"] is False, "不得宣稱已完成實機測試")
    require(component_evidence["armbian_uboot_patch_stack_complete"] is False, "不得宣稱完整 U-Boot 修補佇列已通過")

    expected_component_hashes = {
        "linux_dtb": EXPECTED_COMPONENT_DTB_SHA256,
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
    for forbidden in ('source "${SRC}/config/boards/armsom-sige1.csc"', "hinlink_rk3528_defconfig"):
        require(forbidden not in board_text, f"板檔仍含舊繼承：{forbidden}")
    validate_firmware_contract(policy, board_text)

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

    print(f"BPI-M1 Super {policy['candidate_level']} 固定來源、授權與發布政策守門通過。")


if __name__ == "__main__":
    main()
