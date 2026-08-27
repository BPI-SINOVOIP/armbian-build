#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
component_root="${F2P_EVIDENCE_ROOT:-${repo_dir}/output/components/2026.08/bananapi-sunplus-f2p-legacy}"
manifest="${component_root}/COMPONENT_BUILD_MANIFEST.json"

for command in fdtget find grep jq sha256sum stat tar; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "F2P 元件驗證失敗：$*" >&2
	exit 1
}

[[ -f "${validation_config}" && -f "${manifest}" ]] || fail "找不到驗證契約或元件清單"
[[ ! -e "${component_root}/source" && ! -e "${component_root}/build" ]] ||
	fail "可攜證據不得包含 BSP 原始碼或建置樹"
[[ "$(jq -r '.status' "${manifest}")" == complete ]] || fail "元件建置狀態不完整"
[[ "$(jq -r '.rootfs_image_built' "${manifest}")" == false ]] || fail "本工具只接受未建立 rootfs 的元件證據"
expected_revision="$(jq -r '.source_commits.bsp.revision' "${validation_config}")"
[[ "$(jq -r '.revision' "${manifest}")" == "${expected_revision}" ]] || fail "BSP 提交不符"
expected_manifest="$(jq -r '.component_build_evidence.manifest_sha256' "${validation_config}")"
[[ "$(sha256sum "${manifest}" | cut -d' ' -f1)" == "${expected_manifest}" ]] ||
	fail "元件清單雜湊不符"
jq -e --slurpfile manifest "${manifest}" '
  .candidate_level == "L1 元件候選"
  and .component_build_completed == true
  and .rootfs_image_built == false
  and .public_release_allowed == false
  and .hardware_claims_allowed == false
  and (.component_build_evidence.artifacts == $manifest[0].artifacts)
  and (.boards.bananapif2p.candidate_boot_media == ["microSD"])
  and (.boards.bananapif2p.supported_boot_media == [])
' "${validation_config}" >/dev/null || fail "元件證據或發布邊界不符"

while IFS=$'\t' read -r name relative expected_size expected_sha256; do
	path="${component_root}/${relative}"
	[[ -f "${path}" ]] || fail "缺少 ${name}：${relative}"
	[[ "$(stat -c %s "${path}")" == "${expected_size}" ]] || fail "${name} 大小不符"
	actual_sha256="$(sha256sum "${path}" | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${expected_sha256}" ]] || fail "${name} 雜湊不符"
done < <(jq -r '.artifacts | to_entries[] | [.key, .value.path, .value.size, .value.sha256] | @tsv' "${manifest}")

linux_dtb="${component_root}/$(jq -r '.artifacts.linux_dtb.path' "${manifest}")"
[[ "$(fdtget -t s "${linux_dtb}" / model)" == "SP7021/CA7/BPI-F2P" ]] || fail "Linux DTB model 不符"
[[ "$(fdtget -t s "${linux_dtb}" / compatible)" == "sunplus,sp7021-achip" ]] || fail "Linux DTB compatible 不符"

uboot_image="${component_root}/$(jq -r '.artifacts.uboot_image.path' "${manifest}")"
while IFS= read -r required; do
	grep -aFq "${required}" "${uboot_image}" || fail "U-Boot 缺少身分字串：${required}"
done < <(jq -r '.boards.bananapif2p.uboot_required_binary_strings[]' "${validation_config}")

expected_ispboot="$(jq -r '.firmware_blobs["sp-pack/sp7021/common/bin/ISPBOOOT.BIN"]' "${validation_config}")"
actual_ispboot="$(jq -r '.artifacts.ispboot.sha256' "${manifest}")"
[[ "${actual_ispboot}" == "${expected_ispboot}" ]] || fail "ISPBOOOT.BIN 雜湊不符"

modules="${component_root}/$(jq -r '.artifacts.linux_modules.path' "${manifest}")"
module_count="$(tar -tf "${modules}" | grep -Ec '\.ko$')"
[[ "${module_count}" == "$(jq -r '.module_count' "${manifest}")" ]] ||
	fail "Linux 模組數量不符"

echo "F2P 固定來源元件唯讀驗證通過：${manifest}"
