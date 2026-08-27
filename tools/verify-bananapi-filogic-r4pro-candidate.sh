#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-filogic-mt7988-r4pro-current.json"
policy_checker="${repo_dir}/tools/check-bananapi-filogic-r4pro-policy.py"
verifier="${R4PRO_GENERIC_VERIFIER:-${repo_dir}/tools/verify-bananapi-filogic-candidates.sh}"

[[ -x "${policy_checker}" ]] || {
	echo "找不到 R4 Pro 政策檢查器：${policy_checker}" >&2
	exit 1
}
[[ -x "${verifier}" ]] || {
	echo "找不到 Filogic 候選驗證器：${verifier}" >&2
	exit 1
}

"${policy_checker}" "${validation_config}"

VALIDATION_CONFIG="${validation_config}" \
	OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7988-r4pro-trixie-current-cli" \
	BOARDS="bananapir4pro" VERIFY_TMP_PREFIX="filogic-r4pro-verify" \
	"${verifier}" "$@"
