#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"
checker="${repo_dir}/tools/check-bananapi-realtek-m4-source-policy.py"
validation_config="${repo_dir}/config/validation/bananapi-realtek-rtd1395-m4-legacy.json"
fixed_output_dir="${repo_dir}/output/images/2026.08/bananapi-realtek-rtd1395-m4-trixie-legacy-cli"
requested_output_dir="${OUTPUT_DIR:-}"

if [[ -n "${requested_output_dir}" &&
	"$(realpath -m -- "${requested_output_dir}")" != "$(realpath -m -- "${fixed_output_dir}")" ]]; then
	echo "BPI-M4 只允許固定輸出目錄：${fixed_output_dir}" >&2
	exit 2
fi
case "${PUBLIC_RELEASE:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M4 內部候選禁止公開發布。" >&2; exit 2 ;;
esac
case "${HARDWARE_CLAIMS:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M4 內部候選禁止硬體通過聲明。" >&2; exit 2 ;;
esac
[[ -x "${verifier}" && -x "${checker}" ]] || {
	echo "找不到 BPI-M4 候選驗證器或政策守門器。" >&2
	exit 1
}
[[ -d "${fixed_output_dir}" ]] || {
	echo "找不到 BPI-M4 候選輸出目錄：${fixed_output_dir}" >&2
	exit 1
}

export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
"${checker}" "${validation_config}"

mkdir -p "${repo_dir}/.tmp"
extra_status_file="$(mktemp "${repo_dir}/.tmp/bananapi-realtek-m4-status.XXXXXX.json")"
cleanup_extra_status() {
	[[ ! -e "${extra_status_file}" ]] || rm -f "${extra_status_file}"
}
trap cleanup_extra_status EXIT
cat >"${extra_status_file}" <<'JSON'
{
  "public_release_allowed": false,
  "hardware_claims_allowed": false,
  "hardware_validated": false,
  "opaque_payload_redistribution_verified": false,
  "toolchain_redistribution_verified": false
}
JSON

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${fixed_output_dir}"
export BOARDS="bananapim4"
export CANDIDATE_FAMILY_NAME="Realtek RTD1395 M4"
export VERIFY_TMP_PREFIX="bananapi-realtek-m4-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL=L2
export VERIFICATION_EXTRA_STATUS_JSON="${extra_status_file}"
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
export REQUIRE_BUILD_VERIFIER_IDENTITY=yes

"${verifier}" "$@"
trap - EXIT
cleanup_extra_status
echo "BPI-M4 L2 內部候選映像通過唯讀守門。"
