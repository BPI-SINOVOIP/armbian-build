#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli"
export BOARDS="bananapim6"
export CANDIDATE_FAMILY_NAME="VS680 M6"
export CANDIDATE_LOCK_FILE=".bananapi-vs680-m6-build.lock"

[[ -x "${builder}" ]] || {
	echo "找不到共用候選建置器：${builder}" >&2
	exit 1
}

exec "${builder}" "$@"
