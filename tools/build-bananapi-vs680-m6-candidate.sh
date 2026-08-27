#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"
validation_config="${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json"
expected_source_date_epoch="1717001894"

case "${PUBLIC_RELEASE:-no}" in
	1 | true | TRUE | yes | YES)
		echo "BPI-M6 內部候選禁止公開發布。" >&2
		exit 2
		;;
esac
case "${HARDWARE_CLAIMS:-no}" in
	1 | true | TRUE | yes | YES)
		echo "BPI-M6 內部候選禁止硬體通過聲明。" >&2
		exit 2
		;;
esac
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no

python3 "${repo_dir}/tools/check-bananapi-vs680-m6-policy.py" "${validation_config}"
"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"

[[ "${ALLOW_INTERNAL_M6_CANDIDATE:-no}" == yes ]] || {
	echo "BPI-M6 目前只允許專用 OverlayFS 入口建立內部候選。" >&2
	exit 2
}
[[ "${REQUIRE_ISOLATED_CACHE:-yes}" == yes ]] || {
	echo "BPI-M6 建置不得停用 OverlayFS 隔離快取守門。" >&2
	exit 2
}
export REQUIRE_ISOLATED_CACHE=yes

if [[ "${SOURCE_DATE_EPOCH:-${expected_source_date_epoch}}" != "${expected_source_date_epoch}" ]]; then
	echo "BPI-M6 建置拒絕：SOURCE_DATE_EPOCH 必須是 ${expected_source_date_epoch}。" >&2
	exit 2
fi
export SOURCE_DATE_EPOCH="${expected_source_date_epoch}"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli"
export BOARDS="bananapim6"
export CANDIDATE_FAMILY_NAME="VS680 M6"
export CANDIDATE_LOCK_FILE=".bananapi-vs680-m6-build.lock"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-40}"

[[ -x "${builder}" ]] || {
	echo "找不到共用候選建置器：${builder}" >&2
	exit 1
}

exec "${builder}" "$@"
