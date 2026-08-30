#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"
checker="${repo_dir}/tools/check-bananapi-spacemit-k3-sm10-policy.py"
finalizer="${repo_dir}/tools/finalize-bananapi-spacemit-k3-sm10-verification.py"
validation_config="${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json"
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
[[ -x "${verifier}" && -x "${checker}" && -x "${finalizer}" ]] || {
	echo "找不到 BPI-SM10 候選驗證工具。" >&2
	exit 1
}
[[ -d "${fixed_output_dir}" ]] || {
	echo "找不到 BPI-SM10 候選輸出目錄：${fixed_output_dir}" >&2
	exit 1
}

evidence_level="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["current_evidence_level"])
PY
)"
[[ "${evidence_level}" == L1 || "${evidence_level}" == L2 ]] || {
	echo "BPI-SM10 證據層級只接受 L1 或 L2。" >&2
	exit 2
}

export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
"${checker}" "${validation_config}"
mkdir -p "${repo_dir}/.tmp"
extra_status_file="$(mktemp "${repo_dir}/.tmp/bananapi-sm10-status.XXXXXX.json")"
cleanup_extra_status() {
	[[ ! -e "${extra_status_file}" ]] || rm -f "${extra_status_file}"
}
trap cleanup_extra_status EXIT
cat >"${extra_status_file}" <<'JSON'
{
  "public_release_allowed": false,
  "hardware_claims_allowed": false,
  "hardware_validated": false,
  "public_distribution_approved": false
}
JSON

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${fixed_output_dir}"
export BOARDS="bananapism10"
export CANDIDATE_FAMILY_NAME="SpacemiT K3 SM10"
export VERIFY_TMP_PREFIX="bananapi-spacemit-k3-sm10-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL="${evidence_level}"
export VERIFICATION_EXTRA_STATUS_JSON="${extra_status_file}"
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
export REQUIRE_BUILD_VERIFIER_IDENTITY=yes
if [[ "${evidence_level}" == L2 ]]; then
	export VERIFICATION_DEFER_STATUS_PROMOTION=yes
fi

"${verifier}" "$@"
"${finalizer}" --level "${evidence_level}" --repo "${repo_dir}" \
	--config "${validation_config}" --output "${fixed_output_dir}"
trap - EXIT
cleanup_extra_status
echo "BPI-SM10 ${evidence_level} 內部候選映像通過唯讀守門。"
