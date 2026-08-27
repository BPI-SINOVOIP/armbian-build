#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli}"
evidence="${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv"
status_file="${1:-}"
temporary=""

trap '[[ -z "${temporary}" ]] || rm -f "${temporary}"' EXIT

[[ "${status_file}" == "${output_dir}/VERIFICATION_STATUS.json.partial" ]] || {
	echo "R3 Mini 收尾器只接受共用驗證器的暫存狀態" >&2
	exit 1
}
[[ -s "${evidence}" && -s "${status_file}" ]] || {
	echo "R3 Mini 缺少 U-Boot 載荷或驗證狀態證據" >&2
	exit 1
}

while IFS='=' read -r payload maximum; do
	[[ "${maximum}" =~ ^[1-9][0-9]*$ ]] || {
		echo "R3 Mini 載荷上限格式不符：${payload}" >&2
		exit 1
	}
	mapfile -t sizes < <(awk -F '\t' -v board=bananapir3mini -v payload="${payload}" \
	  'NR > 1 && $1 == board && $2 == payload { print $5 }' "${evidence}")
	[[ ${#sizes[@]} -eq 1 && "${sizes[0]}" =~ ^[1-9][0-9]*$ ]] || {
		echo "R3 Mini 載荷證據不唯一或大小無效：${payload}" >&2
		exit 1
	}
	(( sizes[0] <= maximum )) || {
		echo "R3 Mini 載荷超出保留分割區：${payload}" >&2
		exit 1
	}
done < <(jq -r '.boards.bananapir3mini.uboot_payload_maximum_sizes[]' "${policy}")

temporary="$(mktemp "${status_file}.XXXXXX")"
jq --slurpfile policy "${policy}" '
  .public_release_authorized = false
  | .hardware_validation_completed = false
  | .release_gate = {
      status: "blocked",
      blockers: $policy[0].public_release_blockers,
      emmc_user_area_image_is_complete_cold_boot_installer:
        $policy[0].boards.bananapir3mini.boot_media_contract.user_area_image_is_complete_cold_boot_installer
    }
' "${status_file}" >"${temporary}"
mv "${temporary}" "${status_file}"
temporary=""

echo "R3 Mini 載荷邊界與機器可讀發布阻擋已寫入"
