#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-filogic-mt7986-r3-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3-trixie-current-cli}"
boards_text="${BOARDS:-bananapir3}"
generic_builder="${GENERIC_CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-sunxi-candidates.sh}"

[[ -x "${generic_builder}" ]] || {
	echo "找不到共用候選建置器：${generic_builder}" >&2
	exit 1
}

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="${boards_text}" CANDIDATE_FAMILY_NAME="Filogic" \
	CANDIDATE_LOCK_FILE=".bananapi-filogic-build.lock" \
	"${generic_builder}" "$@"
