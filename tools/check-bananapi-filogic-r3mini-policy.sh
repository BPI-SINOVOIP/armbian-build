#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json}"

[[ -s "${policy}" ]] || {
	echo "找不到 R3 Mini 驗證政策：${policy}" >&2
	exit 1
}

jq -e '
  .public_release_authorized == false
  and .hardware_validation_completed == false
  and .release_gate.status == "blocked"
  and .release_gate.full_image_built == false
  and .release_gate.component_validation_only == true
  and (.release_gate.required_blockers == .public_release_blockers)
  and (.public_release_blockers | index("atf_mt7986_dram_object_redistribution_scope_unverified") != null)
  and (.public_release_blockers | index("emmc_boot0_installation_not_hardware_validated") != null)
  and (.atf_prebuilt_objects["plat/mediatek/mt7986/drivers/dram/release/dram.o"].redistribution_authorized == false)
  and (.candidate_level == "L1 元件候選")
  and (.component_build_completed == true)
  and (.full_rootfs_image_built == false)
  and (.boards.bananapir3mini.candidate_boot_media == ["emmc"])
  and (.boards.bananapir3mini.supported_boot_media == [])
  and (.boards.bananapir3mini.unsupported_boot_media | index("sd") != null)
  and (.boards.bananapir3mini.boot_media_contract.cold_boot_source == "emmc_boot0")
  and (.boards.bananapir3mini.boot_media_contract.user_area_image_is_complete_cold_boot_installer == false)
  and (.boards.bananapir3mini.emmc_boot0_target == "/dev/mmcblk0boot0")
  and (.boards.bananapir3mini.emmc_boot0_payload == "bl2.img")
  and (.boards.bananapir3mini.automatic_emmc_install_authorized == false)
' "${policy}" >/dev/null || {
	echo "R3 Mini 發布或 eMMC boot0 政策不符" >&2
	exit 1
}

echo "R3 Mini 發布與 eMMC boot0 政策通過"
