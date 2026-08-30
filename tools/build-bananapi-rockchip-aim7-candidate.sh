#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"
validation_config="${repo_dir}/config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
expected_source_date_epoch="1777288768"

[[ "${ALLOW_INTERNAL_AIM7_CANDIDATE:-no}" == yes ]] || {
	echo "BPI-AIM7 目前只允許專用 OverlayFS 入口建立內部候選。" >&2
	exit 2
}
export REQUIRE_ISOLATED_CACHE=yes
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes

if [[ "${SOURCE_DATE_EPOCH:-${expected_source_date_epoch}}" != "${expected_source_date_epoch}" ]]; then
	echo "BPI-AIM7 建置拒絕：SOURCE_DATE_EPOCH 必須是 ${expected_source_date_epoch}。" >&2
	exit 2
fi
export SOURCE_DATE_EPOCH="${expected_source_date_epoch}"

python3 "${repo_dir}/tools/check-bananapi-rockchip-aim7-policy.py" "${validation_config}"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3588-aim7-trixie-vendor-cli"
export BOARDS="bananapiaim7"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-80}"

[[ -x "${builder}" ]] || {
	echo "找不到 Rockchip 共用候選建置器：${builder}" >&2
	exit 1
}

exec "${builder}" "$@"
