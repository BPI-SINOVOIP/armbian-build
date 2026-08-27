#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-sunxi-candidates.sh"

[[ "${ALLOW_INTERNAL_F2P_SD_CANDIDATE:-no}" == yes ]] || {
	echo "F2P 目前只允許內部 SD-only 候選；請透過專用 OverlayFS 入口執行。" >&2
	exit 2
}

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-sunplus-sp7021-f2p-trixie-legacy-cli"
export BOARDS="bananapif2p"
export CANDIDATE_FAMILY_NAME="Sunplus SP7021 F2P"
export CANDIDATE_LOCK_FILE=".bananapi-sunplus-f2p-build.lock"

[[ -x "${builder}" ]] || {
	echo "找不到共用候選建置器：${builder}" >&2
	exit 1
}

exec "${builder}" "$@"
