#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-filogic-candidates.sh"
expected_source_date_epoch=1787793187
expected_cache_lower="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"
expected_cache_target="${repo_dir}/cache"
expected_overlay_root="${repo_dir}/.tmp/bananapi-filogic-r3mini-cache-overlay"
expected_upper_dir="${expected_overlay_root}/upper"
expected_work_dir="${expected_overlay_root}/work"

validate_fixed_overlay_mount() {
	local mount_json
	for command in findmnt python3 readlink; do
		command -v "${command}" >/dev/null || {
			echo "R3 Mini OverlayFS 身分檢查缺少命令：${command}" >&2
			exit 2
		}
	done
	[[ "$(readlink -f "${CACHE_LOWER:-/不存在}")" == "${expected_cache_lower}" &&
		"$(readlink -f "${CACHE_TARGET:-/不存在}")" == "${expected_cache_target}" &&
		"$(readlink -f "${CACHE_OVERLAY_ROOT:-/不存在}")" == "${expected_overlay_root}" ]] || {
		echo "R3 Mini OverlayFS 路徑身分不符。" >&2
		exit 2
	}
	mount_json="$(findmnt --json --mountpoint "${expected_cache_target}" \
		-o TARGET,FSTYPE,OPTIONS)" || {
		echo "R3 Mini cache 不是獨立掛載點。" >&2
		exit 2
	}
	python3 - "${mount_json}" "${expected_cache_target}" "${expected_cache_lower}" \
		"${expected_upper_dir}" "${expected_work_dir}" <<'PY'
import json
import os
import sys

data = json.loads(sys.argv[1]).get("filesystems", [])
if len(data) != 1:
    raise SystemExit("R3 Mini cache 掛載身分不唯一")
mount = data[0]
if os.path.realpath(mount.get("target", "")) != os.path.realpath(sys.argv[2]):
    raise SystemExit("R3 Mini cache 掛載點不符")
if mount.get("fstype") != "overlay":
    raise SystemExit("R3 Mini cache 不是 OverlayFS")
options = {}
for option in mount.get("options", "").split(","):
    key, separator, value = option.partition("=")
    if separator:
        options[key] = value.replace("\\040", " ")
for key, expected in zip(("lowerdir", "upperdir", "workdir"), sys.argv[3:]):
    actual = options.get(key, "").split(":")
    if len(actual) != 1 or os.path.realpath(actual[0]) != os.path.realpath(expected):
        raise SystemExit(f"R3 Mini OverlayFS {key} 身分不符")
PY
	[[ "$(findmnt -no FSTYPE -T "${expected_cache_lower}")" != overlay ]] || {
		echo "R3 Mini 固定 lowerdir 不得是 OverlayFS。" >&2
		exit 2
	}
}

[[ "${ALLOW_INTERNAL_R3MINI_CANDIDATE:-no}" == yes ]] || {
	echo "R3 Mini 只允許從專用 OverlayFS 入口建立內部候選" >&2
	exit 2
}
export REQUIRE_ISOLATED_CACHE=yes
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
if [[ -n "${SOURCE_DATE_EPOCH:-}" && "${SOURCE_DATE_EPOCH}" != "${expected_source_date_epoch}" ]]; then
	echo "R3 Mini SOURCE_DATE_EPOCH 與固定契約不符" >&2
	exit 2
fi
export SOURCE_DATE_EPOCH="${expected_source_date_epoch}"
validate_fixed_overlay_mount

[[ "${PUBLIC_RELEASE:-no}" == no ]] || {
	echo "R3 Mini 候選只允許內部建置，不得啟用公開發布" >&2
	exit 1
}
[[ "${HARDWARE_CLAIMS:-no}" == no ]] || {
	echo "R3 Mini 未完成實機驗證，不得啟用硬體通過聲明" >&2
	exit 1
}

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
export BOARDS="bananapir3mini"
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no

"${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.sh" --source-contract-only
"${builder}" "$@"
