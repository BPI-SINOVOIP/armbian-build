#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-spacemit-k3-sm10-policy.py"
source_verifier="${repo_dir}/tools/verify-bananapi-spacemit-k3-sm10-sources.sh"
verify_local_sdk="${VERIFY_LOCAL_SDK:-yes}"
fixed_output_dir="${repo_dir}/output/images/2026.08/bananapi-spacemit-k3-sm10-trixie-current-cli"
requested_output_dir="${OUTPUT_DIR:-}"

if [[ -n "${requested_output_dir}" &&
	"$(realpath -m -- "${requested_output_dir}")" != "$(realpath -m -- "${fixed_output_dir}")" ]]; then
	echo "BPI-SM10 只允許固定輸出目錄：${fixed_output_dir}" >&2
	exit 2
fi
case "${PUBLIC_RELEASE:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-SM10 內部候選禁止公開發布。" >&2; exit 2 ;;
esac
case "${HARDWARE_CLAIMS:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-SM10 內部候選禁止硬體通過聲明。" >&2; exit 2 ;;
esac

case "${verify_local_sdk}" in
	yes | no) ;;
	*) echo "VERIFY_LOCAL_SDK 只接受 yes 或 no" >&2; exit 2 ;;
esac

[[ -x "${builder}" ]] || { echo "找不到共用候選建置器：${builder}" >&2; exit 1; }
[[ -x "${policy_checker}" ]] || { echo "找不到 SM10 政策檢查器" >&2; exit 1; }
"${policy_checker}" "${config}"
if [[ "${verify_local_sdk}" == yes ]]; then
	SOURCE_EVIDENCE_ROOT="${repo_dir}/.tmp/bananapi-sm10-image-source-evidence" \
		"${source_verifier}"
fi

export VALIDATION_CONFIG="${config}"
export OUTPUT_DIR="${fixed_output_dir}"
export BOARDS="bananapism10"
export CANDIDATE_FAMILY_NAME="SpacemiT K3 SM10"
export CANDIDATE_LOCK_FILE=".bananapi-spacemit-k3-sm10-build.lock"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-80}"
export REQUIRE_ISOLATED_CACHE=yes
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
export SOURCE_DATE_EPOCH=1777390324
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no

exec "${builder}" "$@"
