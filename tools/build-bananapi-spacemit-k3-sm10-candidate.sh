#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-spacemit-k3-sm10-policy.py"
source_verifier="${repo_dir}/tools/verify-bananapi-spacemit-k3-sm10-sources.sh"
verify_local_sdk="${VERIFY_LOCAL_SDK:-yes}"

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
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-spacemit-k3-sm10-trixie-current-cli"
export BOARDS="bananapism10"
export CANDIDATE_FAMILY_NAME="SpacemiT K3 SM10"
export CANDIDATE_LOCK_FILE=".bananapi-spacemit-k3-sm10-build.lock"

exec "${builder}" "$@"
