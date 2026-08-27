#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json}"

[[ -s "${policy}" ]] || {
	echo "找不到 R3 Mini 驗證政策：${policy}" >&2
	exit 1
}
command -v jq >/dev/null || {
	echo "缺少 R3 Mini 政策檢查所需的 jq" >&2
	exit 1
}

jq -e '
  .public_release_authorized == false
  and .hardware_claims_allowed == false
  and .hardware_validation_completed == false
  and .release_gate.status == "blocked"
  and .release_gate.public_release_authorized == false
  and .release_gate.hardware_claims_allowed == false
  and (.release_gate.required_blockers == .public_release_blockers)
  and (.public_release_blockers | index("atf_mt7986_dram_object_redistribution_scope_unverified") != null)
  and (.public_release_blockers | index("emmc_boot0_installation_not_hardware_validated") != null)
  and (.atf_prebuilt_objects["plat/mediatek/mt7986/drivers/dram/release/dram.o"].redistribution_authorized == false)
  and (.allowed_evidence_levels == ["L1", "L2"])
  and (.component_build_completed == true)
  and (
    if .candidate_level == "L1 元件候選" then
      .candidate_scope == "internal-component-only"
      and .full_rootfs_image_built == false
      and .release_gate.full_image_built == false
      and .release_gate.component_validation_only == true
    elif .candidate_level == "L2 內部軟體候選" then
      .candidate_scope == "internal-l2"
      and .full_rootfs_image_built == true
      and .release_gate.full_image_built == true
      and .release_gate.component_validation_only == false
    else
      false
    end
  )
  and (.boards.bananapir3mini.candidate_boot_media == ["emmc"])
  and (.boards.bananapir3mini.supported_boot_media == [])
  and (.boards.bananapir3mini.unsupported_boot_media | index("sd") != null)
  and (.boards.bananapir3mini.boot_media_contract.cold_boot_source == "emmc_boot0")
  and (.boards.bananapir3mini.boot_media_contract.user_area_contains_gpt == true)
  and (.boards.bananapir3mini.boot_media_contract.user_area_image_is_complete_cold_boot_installer == false)
  and (.boards.bananapir3mini.boot_media_contract.boot0_payload_requires_separate_write == true)
  and (.boards.bananapir3mini.boot_media_contract.boot0_hardware_validated == false)
  and (.boards.bananapir3mini.boot_media_contract.sd_boot_supported == false)
  and (.boards.bananapir3mini.emmc_user_area_target == "/dev/mmcblk0")
  and (.boards.bananapir3mini.emmc_boot0_target == "/dev/mmcblk0boot0")
  and (.boards.bananapir3mini.emmc_boot0_payload == "bl2.img")
  and (.boards.bananapir3mini.emmc_boot0_offset_bytes == 0)
  and (.boards.bananapir3mini.emmc_boot0_force_ro_required == true)
  and (.boards.bananapir3mini.emmc_boot_partition_enable == "1 1")
  and (.boards.bananapir3mini.automatic_emmc_install_authorized == false)
  and (.boards.bananapir3mini.uboot_payloads == ["bl2.img@17408", "u-boot.fip@6815744"])
  and (.boards.bananapir3mini.uboot_package_only_payloads == ["gpt"])
  and (.boards.bananapir3mini.uboot_payload_minimum_sizes == ["bl2.img=180000", "gpt=17408", "u-boot.fip=400000"])
  and (.boards.bananapir3mini.uboot_payload_maximum_sizes == ["bl2.img=4176896", "gpt=17408", "u-boot.fip=4194304"])
  and (.boards.bananapir3mini.uboot_payload_sha256 == [
    "bl2.img=44d4d6b1bdbfdc1f4d2b302047448788f0256b4d68568e9c9dd809005bccedfd",
    "gpt=beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d",
    "u-boot.fip=8f56c689f10b3aa2367f4290f940451e8d5b766cd3c0120e6aa2cc398db3ff67"
  ])
' "${policy}" >/dev/null || {
	echo "R3 Mini 發布或 eMMC boot0 政策不符" >&2
	exit 1
}

echo "R3 Mini L1/L2 狀態、發布阻擋與 eMMC boot0 政策通過"
