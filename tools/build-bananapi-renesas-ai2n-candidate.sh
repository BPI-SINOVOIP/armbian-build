#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-renesas-rzv2n-ai2n-trixie-legacy-cli}"
generic_builder="${GENERIC_CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-sunxi-candidates.sh}"
source_verifier="${repo_dir}/tools/verify-bananapi-renesas-ai2n-sources.sh"

PUBLIC_RELEASE="${PUBLIC_RELEASE:-no}" POLICY_ONLY=yes \
	VALIDATION_CONFIG="${validation_config}" "${source_verifier}"

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="bpi-ai2n" CANDIDATE_FAMILY_NAME="Renesas RZ/V2N" \
	CANDIDATE_LOCK_FILE=".bananapi-renesas-ai2n-build.lock" \
	MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-35}" \
	"${generic_builder}" "$@"

PUBLIC_RELEASE="${PUBLIC_RELEASE:-no}" EVIDENCE_DIR="${output_dir}" \
	VALIDATION_CONFIG="${validation_config}" "${source_verifier}"

echo "AI2N 來源證據與 L1 候選建置完成：${output_dir}"
