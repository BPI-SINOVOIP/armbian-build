#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2s-legacy.json"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"
expected_source_date_epoch="1609074838"

if [[ "${SOURCE_DATE_EPOCH:-${expected_source_date_epoch}}" != "${expected_source_date_epoch}" ]]; then
	echo "F2S 建置拒絕：SOURCE_DATE_EPOCH 必須是 ${expected_source_date_epoch}。" >&2
	exit 2
fi
export SOURCE_DATE_EPOCH="${expected_source_date_epoch}"

python3 "${repo_dir}/tools/check-bananapi-sunplus-f2s-source-policy.py" \
	"${validation_config}"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-sunplus-sp7021-f2s-trixie-legacy-cli"
export BOARDS="bananapif2s"
export CANDIDATE_FAMILY_NAME="Sunplus SP7021"
export CANDIDATE_LOCK_FILE=".bananapi-sunplus-f2s-build.lock"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-40}"

"${builder}" "$@"
