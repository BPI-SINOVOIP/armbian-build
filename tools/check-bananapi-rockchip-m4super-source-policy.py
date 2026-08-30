#!/usr/bin/env python3
"""檢查 BPI-M4 Super L0 donor-only 契約與發布邊界。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise ValueError(message)


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"無法讀取驗證契約 {path}：{error}")
    if not isinstance(value, dict):
        fail("驗證契約根節點必須是物件")
    return value


def require_false(config: dict[str, object], key: str) -> None:
    if config.get(key) is not False:
        fail(f"{key} 必須是 false")


def require_fixed_source(
    sources: dict[str, object], name: str, expected_url: str, expected_commit: str
) -> dict[str, object]:
    source = sources.get(name)
    if not isinstance(source, dict):
        fail(f"缺少固定來源：{name}")
    if source.get("source") != expected_url:
        fail(f"{name} 來源網址不符")
    commit = source.get("commit")
    if commit != expected_commit or not COMMIT_PATTERN.fullmatch(str(commit)):
        fail(f"{name} 必須固定為指定完整提交碼")
    if source.get("ref") != f"commit:{expected_commit}":
        fail(f"{name} ref 必須與提交碼一致")
    return source


def reject_artifact_digests(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if "sha256" in key.lower():
                fail(f"L0 契約不得記錄產物雜湊欄位：{path}.{key}")
            reject_artifact_digests(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_artifact_digests(child, f"{path}[{index}]")
    elif isinstance(value, str) and DIGEST_PATTERN.fullmatch(value):
        fail(f"L0 契約不得記錄未保留產物的雜湊：{path}")


def validate(config: dict[str, object]) -> None:
    if config.get("schema_version") != 2:
        fail("契約版本必須是 2")
    if config.get("evidence_level") != "L0":
        fail("M4 Super 目前只能是 L0")
    if config.get("allowed_evidence_levels") != ["L0"]:
        fail("允許的證據層級只能包含 L0")
    if config.get("contract_scope") != "donor_only":
        fail("契約範圍必須是 donor_only")
    if config.get("donor_only") is not True:
        fail("必須明示 donor_only=true")
    if config.get("candidate_branch") != "vendor":
        fail("候選分支必須是 vendor")
    if config.get("kernel_family") != "rk35xx":
        fail("核心家族必須是 rk35xx")

    for key in (
        "donor_hardware_equivalence_verified",
        "component_build_completed",
        "full_image_built",
        "hardware_validated",
        "public_release_allowed",
    ):
        require_false(config, key)

    prohibited = config.get("prohibited_claims")
    required_prohibitions = {
        "L1",
        "L2",
        "BPI-M4 Super 專屬 DTS 或 U-Boot defconfig 已完成",
        "BPI-M4 Super 元件已建置",
        "BPI-M4 Super 完整映像已建置",
        "BPI-M4 Super 硬體相容或周邊已通過",
    }
    if not isinstance(prohibited, list) or set(prohibited) != required_prohibitions:
        fail("禁止聲明集合不完整")

    sources = config.get("fixed_source_references")
    if not isinstance(sources, dict):
        fail("缺少固定來源參考")
    require_fixed_source(
        sources,
        "linux",
        "https://github.com/armbian/linux-rockchip.git",
        "c6157104418d012823413c02f9222f3fe123dd25",
    )
    require_fixed_source(
        sources,
        "uboot",
        "https://github.com/radxa/u-boot.git",
        "39cd993e5d6296635438e84f4576b3a9bf76f86e",
    )
    rkbin = require_fixed_source(
        sources,
        "rkbin",
        "https://github.com/armbian/rkbin",
        "1d3c61008fa823936ae7a59615393f8294b64456",
    )
    require_fixed_source(
        sources,
        "firmware",
        "https://github.com/armbian/firmware",
        "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
    )
    if rkbin.get("reference_paths") != [
        "LICENSE.TXT",
        "rk35/rk3568_bl31_v1.44.elf",
        "rk35/rk3568_ddr_1560MHz_v1.21.bin",
        "rk35/rk356x_spl_loader_v1.21.113.bin",
    ]:
        fail("RKBin donor 參考路徑不符")

    boundaries = config.get("distribution_boundaries")
    if not isinstance(boundaries, dict):
        fail("缺少散布邊界")
    expected_boundaries = {
        "rkbin_platform_distribution_authorized": True,
        "rkbin_standalone_distribution_authorized": False,
        "rkbin_license_must_accompany_distribution": True,
        "rkbin_distribution_review_required": True,
        "firmware_distribution_review_required": True,
        "actual_firmware_files_in_image_known": False,
    }
    if boundaries != expected_boundaries:
        fail("散布邊界不符")

    if config.get("artifact_evidence") != {
        "component_outputs_recorded": False,
        "full_image_outputs_recorded": False,
        "artifact_hashes_recorded": False,
    }:
        fail("L0 產物證據狀態不符")

    conflicts = config.get("hardware_identity_conflicts")
    if not isinstance(conflicts, dict):
        fail("缺少硬體身分矛盾記錄")
    wireless = conflicts.get("wireless_module")
    if not isinstance(wireless, dict):
        fail("缺少無線模組矛盾記錄")
    if wireless.get("official_value") != "SYN43752":
        fail("官方無線模組必須記錄為 SYN43752")
    if wireless.get("donor_normalized_value") != "AP6275S":
        fail("donor 無線模組必須記錄為 AP6275S")
    if wireless.get("resolved") is not False:
        fail("無線模組矛盾尚未解決")
    pcie = conflicts.get("pcie_lane_count")
    if not isinstance(pcie, dict):
        fail("缺少 PCIe lane 矛盾記錄")
    if pcie.get("official_page_values") != [
        "硬體規格表：PCIe 3.0 x1",
        "同頁產品比較表：PCIe 3.0 x2",
    ]:
        fail("官方 PCIe lane 矛盾記錄不完整")
    if pcie.get("resolved") is not False:
        fail("PCIe lane 矛盾尚未解決")

    boards = config.get("boards")
    if not isinstance(boards, dict) or set(boards) != {"bananapim4super"}:
        fail("驗證契約只能包含 bananapim4super")
    board = boards["bananapim4super"]
    if not isinstance(board, dict):
        fail("bananapim4super 契約格式錯誤")
    if board.get("donor_board") != "ArmSoM Sige3":
        fail("donor 板卡必須是 ArmSoM Sige3")
    if board.get("donor_dtb") != "rockchip/rk3568-armsom-sige3.dtb":
        fail("必須保留 ArmSoM Sige3 donor DTB 身分")
    if board.get("donor_uboot_defconfig") != "armsom-sige3-rk3568_defconfig":
        fail("必須保留 ArmSoM Sige3 donor U-Boot 身分")
    if board.get("candidate_dtb") is not None:
        fail("L0 不得宣稱 M4 Super 專屬 DTB")
    if board.get("candidate_uboot_defconfig") is not None:
        fail("L0 不得宣稱 M4 Super 專屬 U-Boot defconfig")
    if board.get("candidate_overlays") != []:
        fail("L0 不得納入未驗證 overlay")

    reject_artifact_digests(config)


def main() -> int:
    parser = argparse.ArgumentParser(description="檢查 BPI-M4 Super L0 donor-only 契約")
    parser.add_argument("config", type=Path, help="驗證契約 JSON")
    args = parser.parse_args()
    try:
        validate(load_json(args.config))
    except ValueError as error:
        print(f"M4 Super L0 契約拒絕：{error}", file=sys.stderr)
        return 1
    print("M4 Super L0 donor-only 契約與發布邊界通過。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
