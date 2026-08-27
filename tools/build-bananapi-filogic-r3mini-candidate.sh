#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-filogic-candidates.sh"
expected_source_date_epoch=1787793187

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

"${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.sh"
"${builder}" "$@"
