#!/usr/bin/env python3
"""檢查 BPI-R4 Pro 8X 內部 SD 候選的不可越界條件。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


EXPECTED_COMMITS = {
    "linux_commit": "20fb2a966dcea69df6987463ae1fe1c67cff36b6",
    "firmware_commit": "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
    "mt76_firmware_commit": "c5a3bd91aa735b669618610d5f0ebfa5786845a6",
    "linux_firmware_commit": "01205307636157a12c29e6a774bf83b218732050",
}
EXPECTED_BOARD_COMMITS = {
    "uboot_revision": "34820924edbc4ec7803eb89d9852f4b870fa760a",
    "atf_revision": "c34e37802efaea356991a0811c8fc50f8a810f5b",
}
EXPECTED_DTB_SHA256 = (
    "a35e5c81d74d0dcce2174058e87c58287744b273ae895fbb0b9d0eeccb9fac34"
)
EXPECTED_UBOOT_PAYLOAD_SHA256 = {
    "bl2.img=1ebbdb9380e048e1e736dc9f5e735be906eb7ab13ecc5495226c6d417d60d1de",
    "gpt=beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d",
    "u-boot.fip=96267b3ad65315dabed7543783b5562bfe9911ba98a8d90fcb085682c12e6c51",
}
EXPECTED_EXCLUDED_MEDIA = {"emmc", "spi-nand", "spi-nor", "nvme", "usb"}
EXPECTED_ATF_OBJECTS = {
    "plat/mediatek/mt7988/drivers/dram/release/dram.o",
    "plat/mediatek/mt7988/drivers/efuse/release/efuse_cmd.o",
    "plat/mediatek/mt7988/drivers/efuse/release/plat_efuse.o",
}
EXPECTED_UBOOT_PATCHES = {
    "patch/u-boot/u-boot-filogic-r4pro/0001-BPI-R4-Pro-8X-SD.patch": (
        "696039c706293e393888ab164a8a8412c9ac6fbfbd311d9262b21fa86a6bc5a7"
    ),
    "patch/u-boot/u-boot-filogic-r4pro/0002-R4-Pro-SD.patch": (
        "10ecafc1603463f2114cb0349b1791bfd2126ba3e208e52fab040d95d9c56a4a"
    ),
}
EXPECTED_DISABLED_UBOOT_OPTIONS = {
    "# CONFIG_AUTOBOOT_KEYED is not set",
    "# CONFIG_AUTOBOOT_MENU_SHOW is not set",
    "# CONFIG_BOARD_LATE_INIT is not set",
    "# CONFIG_CMD_UBI is not set",
    "# CONFIG_MMC_HS200_SUPPORT is not set",
    "# CONFIG_MTD_SPI_NAND is not set",
    "# CONFIG_OF_SYSTEM_SETUP is not set",
    "# CONFIG_SUPPORT_EMMC_BOOT is not set",
    "# CONFIG_USE_DEFAULT_ENV_FILE is not set",
}
EXPECTED_FORBIDDEN_UBOOT_STRINGS = {
    "root=/dev/fit0",
    "part_default=production",
    "emmc_write_bl2=",
    "ubi_init=",
}


def fail(message: str) -> None:
    print(f"錯誤：{message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="驗證契約 JSON 路徑")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"無法讀取驗證契約：{error}")

    if set(config.get("boards", {})) != {"bananapir4pro"}:
        fail("契約必須只包含 bananapir4pro")
    board = config["boards"]["bananapir4pro"]

    for field, expected in EXPECTED_COMMITS.items():
        if config.get(field) != expected:
            fail(f"{field} 未固定為 {expected}")
    for field, expected in EXPECTED_BOARD_COMMITS.items():
        if board.get(field) != expected:
            fail(f"{field} 未固定為 {expected}")

    if config.get("kernel_version") != "6.19.0-rc1":
        fail("核心必須明確標示為 6.19.0-rc1")
    if config.get("candidate_scope") != "僅限內部驗證":
        fail("候選範圍必須維持僅限內部驗證")
    if config.get("public_distribution_approved") is not False:
        fail("不得核准公開散布")
    if len(config.get("public_distribution_blockers", [])) < 3:
        fail("公開散布阻擋條件不完整")
    if config.get("supported_boot_media") != ["sd"]:
        fail("唯一支援的啟動媒體必須是 SD")
    if set(config.get("excluded_boot_media", [])) != EXPECTED_EXCLUDED_MEDIA:
        fail("未完整排除非 SD 啟動媒體")
    if config.get("atf_prebuilt_object_license_status") != "未釐清":
        fail("ATF 預編譯物件授權不得標示為已釐清")
    if set(config.get("atf_prebuilt_objects", {})) != EXPECTED_ATF_OBJECTS:
        fail("ATF 預編譯 DRAM／eFuse 物件清單不完整")
    if not all(
        len(digest) == 64
        for digest in config.get("atf_prebuilt_objects", {}).values()
    ):
        fail("ATF 預編譯物件雜湊格式不正確")
    if config.get("uboot_patch_directory") != "u-boot-filogic-r4pro":
        fail("U-Boot 未使用 R4 Pro 專用修補目錄")
    if config.get("uboot_candidate_patches") != EXPECTED_UBOOT_PATCHES:
        fail("U-Boot 專用修補清單或雜湊不符")

    if board.get("uboot_defconfig") != (
        "mt7988a_bananapi_bpi-r4-pro-8x-sdmmc_defconfig"
    ):
        fail("U-Boot 必須只選用 R4 Pro 8X SDMMC defconfig")
    if board.get("dtb") != (
        "mediatek/mt7988a-bananapi-bpi-r4-pro-8x-sd.dtb"
    ):
        fail("DTB 必須是 R4 Pro 8X 與 SD overlay 的合併產物")
    if board.get("dtb_sha256") != EXPECTED_DTB_SHA256:
        fail("正式候選 DTB 雜湊不符")
    if set(board.get("uboot_payload_sha256", [])) != EXPECTED_UBOOT_PAYLOAD_SHA256:
        fail("正式候選 U-Boot payload 雜湊不符")
    required_options = set(board.get("uboot_required_config_options", []))
    if not EXPECTED_DISABLED_UBOOT_OPTIONS <= required_options:
        fail("U-Boot 未完整停用供應商環境、eMMC 或 NAND 路徑")
    forbidden_strings = set(board.get("uboot_forbidden_binary_strings", []))
    if not EXPECTED_FORBIDDEN_UBOOT_STRINGS <= forbidden_strings:
        fail("U-Boot 禁止字串契約不完整")

    print("BPI-R4 Pro 8X 內部 SD 候選政策檢查通過")


if __name__ == "__main__":
    main()
