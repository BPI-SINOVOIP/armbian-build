#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-vs680-m6-policy.py"
validation_config="${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json"
fixed_output_dir="${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli"
expected_source_date_epoch=1717001894
expected_cache_lower="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"
expected_cache_target="${repo_dir}/cache"
expected_overlay_root="${repo_dir}/.tmp/bananapi-vs680-m6-candidate-cache-overlay"
expected_upper_dir="${expected_overlay_root}/upper"
expected_work_dir="${expected_overlay_root}/work"

requested_output_dir="${OUTPUT_DIR:-}"
if [[ -n "${requested_output_dir}" &&
	"$(realpath -m -- "${requested_output_dir}")" != "$(realpath -m -- "${fixed_output_dir}")" ]]; then
	echo "BPI-M6 只允許固定輸出目錄：${fixed_output_dir}" >&2
	exit 2
fi

validate_fixed_overlay_mount() {
	local mount_json
	for command in findmnt python3 readlink; do
		command -v "${command}" >/dev/null || {
			echo "BPI-M6 OverlayFS 身分檢查缺少命令：${command}" >&2
			exit 2
		}
	done
	[[ "$(readlink -f "${CACHE_LOWER:-/不存在}")" == "${expected_cache_lower}" &&
		"$(readlink -f "${CACHE_TARGET:-/不存在}")" == "${expected_cache_target}" &&
		"$(readlink -f "${CACHE_OVERLAY_ROOT:-/不存在}")" == "${expected_overlay_root}" ]] || {
		echo "BPI-M6 OverlayFS 路徑身分不符。" >&2
		exit 2
	}
	mount_json="$(findmnt --json --mountpoint "${expected_cache_target}" \
		-o TARGET,FSTYPE,OPTIONS)" || {
		echo "BPI-M6 cache 不是獨立掛載點。" >&2
		exit 2
	}
	python3 - "${mount_json}" "${expected_cache_target}" "${expected_cache_lower}" \
		"${expected_upper_dir}" "${expected_work_dir}" <<'PY'
import json
import os
import sys

filesystems = json.loads(sys.argv[1]).get("filesystems", [])
if len(filesystems) != 1:
    raise SystemExit("BPI-M6 cache 掛載身分不唯一")
mount = filesystems[0]
if os.path.realpath(mount.get("target", "")) != os.path.realpath(sys.argv[2]):
    raise SystemExit("BPI-M6 cache 掛載點不符")
if mount.get("fstype") != "overlay":
    raise SystemExit("BPI-M6 cache 不是 OverlayFS")
options = {}
for option in mount.get("options", "").split(","):
    key, separator, value = option.partition("=")
    if separator:
        options[key] = value.replace("\\040", " ")
for key, expected in zip(("lowerdir", "upperdir", "workdir"), sys.argv[3:]):
    actual = options.get(key, "").split(":")
    if len(actual) != 1 or os.path.realpath(actual[0]) != os.path.realpath(expected):
        raise SystemExit(f"BPI-M6 OverlayFS {key} 身分不符")
PY
	[[ "$(findmnt -no FSTYPE -T "${expected_cache_lower}")" != overlay ]] || {
		echo "BPI-M6 固定 lowerdir 不得是 OverlayFS。" >&2
		exit 2
	}
}

case "${PUBLIC_RELEASE:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M6 內部候選禁止公開發布。" >&2; exit 2 ;;
esac
case "${HARDWARE_CLAIMS:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M6 內部候選禁止硬體通過聲明。" >&2; exit 2 ;;
esac
[[ "${ALLOW_INTERNAL_M6_CANDIDATE:-no}" == yes ]] || {
	echo "BPI-M6 目前只允許專用 OverlayFS 入口建立內部候選。" >&2
	exit 2
}
[[ "${REQUIRE_ISOLATED_CACHE:-yes}" == yes ]] || {
	echo "BPI-M6 建置不得停用 OverlayFS 隔離快取守門。" >&2
	exit 2
}
if [[ -n "${SOURCE_DATE_EPOCH:-}" &&
	"${SOURCE_DATE_EPOCH}" != "${expected_source_date_epoch}" ]]; then
	echo "BPI-M6 建置拒絕：SOURCE_DATE_EPOCH 必須是 ${expected_source_date_epoch}。" >&2
	exit 2
fi
validate_fixed_overlay_mount

export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
export REQUIRE_ISOLATED_CACHE=yes
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
export SOURCE_DATE_EPOCH="${expected_source_date_epoch}"
export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${fixed_output_dir}"
export BOARDS="bananapim6"
export CANDIDATE_FAMILY_NAME="VS680 M6"
export CANDIDATE_LOCK_FILE=".bananapi-vs680-m6-build.lock"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-40}"

material_status="${OUTPUT_DIR}/M6_MATERIAL_STATUS.json"
material_evidence="${OUTPUT_DIR}/M6_MATERIAL_EVIDENCE.json"
calibration_evidence="${OUTPUT_DIR}/M6_CALIBRATION.json"

write_material_state() {
	python3 - "${material_status}" "$1" "$2" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail = sys.argv[1:]
temporary = path + ".build-entry.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "status": state,
            "detail": detail,
            "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        stream,
        ensure_ascii=False,
        indent=2,
    )
    stream.write("\n")
os.replace(temporary, path)
PY
}

[[ -x "${builder}" && -x "${policy_checker}" ]] || {
	echo "找不到 BPI-M6 候選建置器或政策守門器。" >&2
	exit 1
}
mkdir -p "${OUTPUT_DIR}"
rm -f "${material_evidence}" "${calibration_evidence}"
write_material_state in_progress "BPI-M6 建置前來源契約檢查執行中"
material_state_active=yes
finish_material_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${material_state_active}" == yes ]]; then
		rm -f "${material_evidence}" "${calibration_evidence}"
		write_material_state failed "BPI-M6 建置失敗或來源契約不符"
	fi
	exit "${exit_status}"
}
trap finish_material_state EXIT

"${policy_checker}" "${validation_config}" --phase source-contract
"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"
"${builder}" "$@"

write_material_state pending_verification "BPI-M6 映像已建置，尚待校準或正式物質驗證"
material_state_active=no
trap - EXIT
