#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json}"
board="${repo_dir}/config/boards/bananapir3mini.wip"

[[ -s "${policy}" ]] || {
	echo "找不到 R3 Mini 驗證政策：${policy}" >&2
	exit 1
}
command -v jq >/dev/null || {
	echo "缺少 R3 Mini 政策檢查所需的 jq" >&2
	exit 1
}
[[ -s "${board}" ]] || {
	echo "找不到 R3 Mini 板檔：${board}" >&2
	exit 1
}

jq -e '
  def sha256:
    type == "string" and test("^[0-9a-f]{64}$");
  def commit:
    type == "string" and test("^[0-9a-f]{40}$");
  def artifact:
    type == "object"
    and (.path | type == "string" and length > 0)
    and (.size | type == "number" and . > 0 and floor == .)
    and (.sha256 | sha256);
  def l2_image_evidence:
    type == "object"
    and .status == "complete"
    and .evidence_level == "L2"
    and .full_rootfs_image_built == true
    and .read_only_content_verified == true
    and .hardware_tested == false
    and (.source_commit | commit)
    and (.verifier_commit | commit)
    and .source_commit == .verifier_commit
    and (.build_validation_config_sha256 | sha256)
    and (.verification_config_sha256 | sha256)
    and .build_validation_config_sha256 == .verification_config_sha256
    and (.candidate_matrix_sha256 | sha256)
    and (.uboot_payload_manifest_sha256 | sha256)
    and (.final_config_manifest_sha256 | sha256)
    and (.image | artifact)
    and (.archive | artifact);

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
  and (.firmware_source == "https://github.com/armbian/firmware")
  and (.firmware_ref == "commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08")
  and (.firmware_commit == "f50a2a21bcdb77a562b3976930c5c6b521a1df08")
  and (.verify_firmware_source_resolution == true)
  and (
    if .candidate_level == "L1 元件候選" then
      .candidate_scope == "internal-component-only"
      and .full_rootfs_image_built == false
      and .release_gate.full_image_built == false
      and .release_gate.component_validation_only == true
      and (has("image_build_evidence") | not)
    elif .candidate_level == "L2 內部軟體候選" then
      .candidate_scope == "internal-l2"
      and .full_rootfs_image_built == true
      and .release_gate.full_image_built == true
      and .release_gate.component_validation_only == false
      and (.image_build_evidence | l2_image_evidence)
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

for required in \
	'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"' \
	'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"' \
	"declare -g ARMBIAN_FIRMWARE_GIT_SOURCE=\"\${ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD}\"" \
	"declare -g ARMBIAN_FIRMWARE_GIT_REF=\"\${ARMBIAN_FIRMWARE_GIT_REF_BOARD}\""; do
	grep -Fq "${required}" "${board}" || {
		echo "R3 Mini 板檔缺少固定韌體設定：${required}" >&2
		exit 1
	}
done

echo "R3 Mini L1/L2 狀態、發布阻擋與 eMMC boot0 政策通過"
