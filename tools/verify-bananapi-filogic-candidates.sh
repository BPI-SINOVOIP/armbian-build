#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3-trixie-current-cli}"
boards_text="${BOARDS:-bananapir3}"
generic_verifier="${GENERIC_CANDIDATE_VERIFIER:-${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh}"

[[ -x "${generic_verifier}" ]] || {
	echo "找不到共用候選驗證器：${generic_verifier}" >&2
	exit 1
}

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="${boards_text}" CANDIDATE_FAMILY_NAME="Filogic" \
	VERIFY_TMP_PREFIX="filogic-verify" \
	"${generic_verifier}" "$@"
