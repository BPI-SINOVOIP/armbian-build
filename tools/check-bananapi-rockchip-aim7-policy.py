#!/usr/bin/env python3
"""檢查 Banana Pi AIM7 固定來源、授權邊界與候選證據狀態。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
)
BOARD = ROOT / "config/boards/bananapiaim7.wip"

LINUX_REVISION = "c6157104418d012823413c02f9222f3fe123dd25"
UBOOT_REVISION = "39cd993e5d6296635438e84f4576b3a9bf76f86e"
RKBIN_REVISION = "1d3c61008fa823936ae7a59615393f8294b64456"
FIRMWARE_REVISION = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
COMPONENT_DTB = "fdf3d029773c5374411a08edc6fcfe65532c5fa94d7845b05e28988f338e796f"


def fail(message: str) -> None:
    raise SystemExit(f"BPI-AIM7 政策守門失敗：{message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_commit(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def valid_artifact_path(value: object, suffix: str) -> bool:
    if not isinstance(value, str) or not value.endswith(suffix):
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and len(path.parts) >= 2


def assignments(values: object, field: str) -> dict[str, str]:
    require(isinstance(values, list), f"{field} 必須是清單")
    parsed: dict[str, str] = {}
    for value in values:
        require(
            isinstance(value, str) and value.count("=") == 1,
            f"{field} 格式不符",
        )
        name, expected = value.split("=", 1)
        require(name and expected and name not in parsed, f"{field} 含空值或重複名稱")
        parsed[name] = expected
    return parsed


def validate_l2_evidence(data: dict[str, object], board: dict[str, object]) -> None:
    evidence = data.get("image_build_evidence")
    require(isinstance(evidence, dict), "L2 缺少完整映像證據")
    require(evidence.get("status") == "complete", "L2 映像證據尚未完成")
    require(evidence.get("evidence_level") == "L2", "映像證據不是 L2")
    require(evidence.get("read_only_content_verified") is True, "L2 缺少唯讀內容驗證")
    require(evidence.get("hardware_tested") is False, "內部 L2 不得冒充實機驗證")
    require(evidence.get("public_release_authorized") is False, "內部 L2 不得冒充公開發布核准")

    require(valid_commit(evidence.get("source_commit")), "L2 來源提交格式不符")
    require(valid_commit(evidence.get("verifier_commit")), "L2 驗證提交格式不符")
    require(
        evidence["source_commit"] == evidence["verifier_commit"],
        "L2 來源與驗證提交不一致",
    )
    for key in (
        "build_validation_config_sha256",
        "verification_config_sha256",
        "candidate_matrix_sha256",
        "uboot_payload_manifest_sha256",
        "final_config_manifest_sha256",
        "final_kernel_config_sha256",
        "final_uboot_config_sha256",
        "linux_dtb_sha256",
    ):
        require(valid_sha256(evidence.get(key)), f"L2 {key} 格式不符")
    require(
        evidence["build_validation_config_sha256"]
        == evidence["verification_config_sha256"],
        "L2 建置與驗證契約雜湊不一致",
    )

    for name, suffix in (("image", ".img"), ("archive", ".img.xz")):
        artifact = evidence.get(name)
        require(isinstance(artifact, dict), f"L2 缺少 {name} 證據")
        require(
            isinstance(artifact.get("size"), int) and artifact["size"] > 0,
            f"L2 {name} 大小無效",
        )
        require(valid_sha256(artifact.get("sha256")), f"L2 {name} 雜湊格式不符")
        require(valid_artifact_path(artifact.get("path"), suffix), f"L2 {name} 路徑不安全")

    require(
        board.get("final_kernel_config_sha256")
        == evidence["final_kernel_config_sha256"],
        "L2 最終核心設定與映像證據不一致",
    )
    require(
        board.get("final_uboot_config_sha256")
        == evidence["final_uboot_config_sha256"],
        "L2 最終 U-Boot 設定與映像證據不一致",
    )
    require(
        board.get("image_dtb_sha256") == evidence["linux_dtb_sha256"],
        "L2 映像 DTB 與映像證據不一致",
    )
    require(
        board.get("dtb_sha256") == evidence["linux_dtb_sha256"],
        "L2 DTB 相容欄位與映像證據不一致",
    )
    require(
        board.get("dtb_sha256_evidence_scope") == "full-image-l2",
        "L2 DTB 證據範圍不符",
    )
    payloads = assignments(board.get("uboot_payload_sha256"), "L2 payload 雜湊")
    require(
        set(payloads) == {"idbloader.img", "u-boot.itb"},
        "L2 payload 雜湊項目不完整",
    )
    require(all(valid_sha256(value) for value in payloads.values()), "L2 payload 雜湊格式不符")


def validate_candidate_state(data: dict[str, object], board: dict[str, object]) -> None:
    level = data.get("candidate_level")
    require(level in {"L1 元件候選", "L2 內部軟體候選"}, "候選層級只允許 L1 或內部 L2")
    expected_scope, expected_evidence = {
        "L1 元件候選": ("internal-component-only", "L1"),
        "L2 內部軟體候選": ("internal-l2", "L2"),
    }[level]
    require(data.get("candidate_scope") == expected_scope, "候選層級與範圍不成對")
    require(data.get("current_evidence_level") == expected_evidence, "候選層級與證據等級不成對")
    require(data.get("allowed_evidence_levels") == ["L1", "L2"], "允許證據層級不符")
    require(data.get("target_evidence_level") == "L2", "目標證據層級不符")
    require(data.get("component_build_completed") is True, "L1 元件證據必須保留")

    for key in (
        "candidate_public_release_approved",
        "public_release_allowed",
        "hardware_claims_allowed",
        "firmware_redistribution_audit_complete",
        "firmware_redistribution_license_verified",
    ):
        require(data.get(key) is False, f"{key} 必須維持 false")
    require(board.get("hardware_validation_completed") is False, "不得冒充實機驗證")
    require(board.get("static_topology_only") is True, "靜態拓撲限制必須保留")

    if level == "L1 元件候選":
        for key in ("rootfs_image_built", "full_image_built", "full_rootfs_image_built"):
            require(data.get(key) is False, f"L1 的 {key} 必須為 false")
        require("image_build_evidence" not in data, "L1 不得夾帶完整映像證據")
        require(board.get("image_dtb_sha256") is None, "L1 不得宣稱映像 DTB 雜湊")
        require("dtb_sha256" not in board, "L1 不得把元件 DTB 冒充映像證據")
        require("final_kernel_config_sha256" not in board, "L1 不得冒充最終核心設定證據")
        require("final_uboot_config_sha256" not in board, "L1 不得冒充最終 U-Boot 設定證據")
        require("uboot_payload_sha256" not in board, "L1 不得冒充完整映像 payload 證據")
        require(
            board.get("dtb_sha256_evidence_scope") == "component-only-l1",
            "L1 DTB 證據範圍不符",
        )
        return

    for key in ("rootfs_image_built", "full_image_built", "full_rootfs_image_built"):
        require(data.get(key) is True, f"L2 的 {key} 必須為 true")
    validate_l2_evidence(data, board)


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    data = json.loads(config_path.read_text(encoding="utf-8"))
    board = data["boards"]["bananapiaim7"]
    board_text = BOARD.read_text(encoding="utf-8")

    require(data.get("schema_version") == 1, "驗證契約版本不符")
    require(data.get("candidate_branch") == "vendor", "候選分支不是 vendor")
    require(data.get("kernel_family") == "rk35xx", "核心家族不符")
    require(data.get("source_date_epoch") == 1777288768, "固定來源時間戳不符")
    validate_candidate_state(data, board)

    expected_sources = {
        "linux": ("https://github.com/armbian/linux-rockchip.git", LINUX_REVISION),
        "rkbin": ("https://github.com/armbian/rkbin", RKBIN_REVISION),
        "firmware": ("https://github.com/armbian/firmware", FIRMWARE_REVISION),
    }
    for component, (source, revision) in expected_sources.items():
        require(data.get(f"{component}_source") == source, f"{component} 來源不符")
        require(data.get(f"{component}_ref") == f"commit:{revision}", f"{component} 引用未固定")
        require(data.get(f"{component}_commit") == revision, f"{component} 提交不符")
    require(data.get("verify_firmware_source_resolution") is True, "未啟用韌體來源解析守門")

    board_requirements = (
        'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
        f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{FIRMWARE_REVISION}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_SOURCE="${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}"',
        'declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"',
        f'BOOTBRANCH_BOARD="commit:{UBOOT_REVISION}"',
        f'KERNELBRANCH_BOARD="commit:{LINUX_REVISION}"',
        f'RKBIN_GIT_REF="commit:{RKBIN_REVISION}"',
    )
    for requirement in board_requirements:
        require(requirement in board_text, f"板檔缺少固定設定：{requirement}")

    require(board.get("uboot_revision") == UBOOT_REVISION, "U-Boot 提交不符")
    require(board.get("component_dtb_sha256") == COMPONENT_DTB, "元件 DTB 雜湊不符")
    require(
        board.get("uboot_payloads")
        == ["idbloader.img@32768", "u-boot.itb@8388608"],
        "啟動 payload 與偏移不符",
    )
    require(board.get("partition_table") == "gpt", "分割表不是 GPT")
    require(board.get("partition_start_sector") == 32768, "根分割區起點不符")

    require(data.get("rkbin_copy_and_distribution_grant_present") is True, "RKBin 散布條款證據缺失")
    require(data.get("rkbin_standalone_distribution_authorized") is False, "不得允許 RKBin 獨立散布")
    require(data.get("rkbin_binary_modification_authorized") is False, "不得允許修改 RKBin")
    require(data.get("rkbin_license_must_accompany_distribution") is True, "RKBin 授權檔必須隨附")
    require(data.get("candidate_public_release_approved") is False, "不得核准公開發布")

    required_accelerators = (
        "CONFIG_MALI_BIFROST",
        "CONFIG_ROCKCHIP_MPP_SERVICE",
        "CONFIG_ROCKCHIP_MULTI_RGA",
        "CONFIG_ROCKCHIP_RKNPU",
    )
    for option in required_accelerators:
        require(data["common_kernel_options"].get(option) == "y", f"缺少加速器核心契約：{option}")
    limitations = "\n".join(board.get("known_static_limitations", []))
    require("GPU、VPU、RGA 與 NPU" in limitations, "缺少加速器使用者空間限制")
    require("不代表" in limitations, "缺少靜態設定不等於實機通過的限制")

    component = data.get("component_build_evidence", {})
    require(component.get("source_date_epoch") == 1777288768, "元件時間戳證據不符")
    require(component.get("linux_dtb_sha256") == COMPONENT_DTB, "元件 DTB 證據不符")
    for key, value in component.items():
        if key.endswith("_sha256"):
            require(valid_sha256(value), f"元件證據 {key} 格式不符")

    print("BPI-AIM7 固定來源、授權邊界與候選證據狀態檢查通過。")


if __name__ == "__main__":
    main()
