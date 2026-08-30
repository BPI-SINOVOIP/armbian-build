#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2s-legacy.json"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"

python3 "${repo_dir}/tools/check-bananapi-sunplus-f2s-source-policy.py" \
	"${validation_config}"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-sunplus-sp7021-f2s-trixie-legacy-cli"
export BOARDS="bananapif2s"
export CANDIDATE_FAMILY_NAME="Sunplus SP7021"
export VERIFY_TMP_PREFIX="bananapi-sunplus-f2s-verify"

"${verifier}" "$@"
