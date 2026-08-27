#!/usr/bin/env python3
"""檢查 Banana Pi SM10 候選的來源、授權與發布邊界。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config/validation/bananapi-spacemit-k3-sm10-current.json"
)
BOARD = ROOT / "config/boards/bananapism10.wip"
FAMILY = ROOT / "config/sources/families/spacemit-k3-bpi.conf"
LINUX_DTS = (
    ROOT
    / "patch/kernel/archive/spacemit-k3-bpi-6.18/dt/"
    "k3-bananapi-sm10.dts"
)
BOOT_ENV = (
    ROOT / "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env_k3.txt"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    config = json.loads(config_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(config_path.is_file(), f"找不到驗證契約：{config_path}")
    require(BOARD.is_file(), "板級設定必須維持 .wip")
    require(config.get("public_release_allowed") is False, "不得核准公開發布")
    require(
        config.get("public_distribution_approved") is False,
        "不得核准公開散布",
    )
    require(config.get("hardware_claims_allowed") is False, "不得核准硬體聲明")
    require(
        config.get("secure_boot_claim_allowed") is False,
        "不得核准安全開機聲明",
    )

    sdk = config.get("sdk", {})
    require(
        sdk.get("manifest_commit")
        == "6d767b42fdbd759dc9511b8a13523c3de42aaa5a",
        "manifest 提交不符",
    )
    require(sdk.get("project_count") == 20, "SDK 專案數量不是 20")
    require(
        sdk.get("resolved_manifest_sha256")
        == "6aa7ec0fe51fae1359552efb46ba92007b432e1b8530cf8a8872f663fc2b2b39",
        "固定 revision manifest 雜湊不符",
    )

    source_commits = config.get("source_commits", {})
    require(len(source_commits) == 20, "來源契約未完整固定 20 個專案")
    for path, revision in source_commits.items():
        require(bool(path), "來源專案路徑不可為空")
        require(
            bool(re.fullmatch(r"[0-9a-f]{40}", revision)),
            f"來源提交格式不符：{path}",
        )

    for component in ("linux", "uboot", "opensbi", "esos"):
        source = config.get("component_sources", {}).get(component, {})
        revision = source.get("revision", "")
        require(source.get("ref") == f"commit:{revision}", f"{component} 未固定提交")
        require(revision in source_commits.values(), f"{component} 不在 manifest 中")

    require(
        config.get("trusted_firmware_a", {}).get("applicable") is False,
        "RISC-V K3 不得宣稱使用 TF-A",
    )
    require(
        len(config.get("private_signing_keys_in_sdk", [])) >= 6,
        "未記錄 SDK 私鑰風險",
    )
    require(
        len(config.get("public_distribution_blockers", [])) >= 6,
        "公開散布阻擋記錄不足",
    )
    require(
        config.get("candidate_boot_media") == ["sd"],
        "候選媒體必須只記錄 SD 設計目標",
    )
    require(
        config.get("supported_boot_media") == [],
        "沒有實機證據時不得登錄已支援開機媒體",
    )

    for relative, expected in config.get("bootloader_blobs", {}).items():
        path = ROOT / relative
        require(path.is_file(), f"缺少受控檔案：{relative}")
        if path.is_file():
            require(digest(path) == expected, f"受控檔案雜湊不符：{relative}")

    board_text = BOARD.read_text(encoding="utf-8")
    family_text = FAMILY.read_text(encoding="utf-8")
    dts_text = LINUX_DTS.read_text(encoding="utf-8")
    env_text = BOOT_ENV.read_text(encoding="utf-8")
    policy = config["boards"]["bananapism10"]

    for revision in (
        sdk["manifest_commit"],
        config["linux_commit"],
        policy["uboot_revision"],
        config["component_sources"]["opensbi"]["revision"],
        config["component_sources"]["esos"]["revision"],
    ):
        require(revision in board_text, f"板檔缺少固定提交：{revision}")
    require(
        'KERNELPATCHDIR="archive/spacemit-k3-bpi-6.18"' in board_text,
        "板檔未固定專屬核心修補目錄",
    )
    require(config["linux_commit"] in family_text, "family 未固定 Linux 提交")
    require(policy["uboot_revision"] in family_text, "family 未固定 U-Boot 提交")
    require('#include "k3_com260.dts"' in dts_text, "專屬 DTS 未保守繼承 donor")
    require('model = "BananaPi BPI-SM10";' in dts_text, "專屬 DTS model 不符")
    require(
        'compatible = "bananapi,bpi-sm10", "spacemit,k3-com260";'
        in dts_text,
        "專屬 DTS compatible 不符",
    )
    require(
        "dtb_name=dtb/spacemit/k3-bananapi-sm10.dtb" in env_text,
        "env_k3 未固定專屬 Linux DTB",
    )
    require(
        policy.get("topology_equivalence_verified") is False,
        "不得把 donor 拓撲標成已驗證",
    )
    require(
        policy.get("uboot_control_dtb_identity_is_bananapi_specific") is False,
        "不得把 U-Boot donor 控制 DT 標成 Banana Pi 專屬",
    )

    if failures:
        for failure in failures:
            print(f"SM10 政策拒絕：{failure}", file=sys.stderr)
        return 1

    print(f"SM10 政策檢查通過：{config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
