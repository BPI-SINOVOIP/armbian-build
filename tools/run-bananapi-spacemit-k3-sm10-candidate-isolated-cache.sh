#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"
cache_lower="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"
expected_identity="66306 96224797"

[[ "$(realpath -e -- "${cache_lower}")" == "${cache_lower}" ]] || {
	echo "BPI-SM10 快取 lower 路徑不符：${cache_lower}" >&2
	exit 1
}
[[ "$(stat -Lc '%d %i' "${cache_lower}")" == "${expected_identity}" ]] || {
	echo "BPI-SM10 快取 lower 裝置或 inode 已改變，拒絕啟動。" >&2
	exit 1
}

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-spacemit-k3-sm10-candidate.sh"
export CACHE_LOWER="${cache_lower}"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-spacemit-k3-sm10-cache-overlay"
export CACHE_TARGET="${repo_dir}/cache"
export REQUIRE_ISOLATED_CACHE=yes
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-80}"

[[ -x "${runner}" ]] || {
	echo "找不到隔離快取執行器：${runner}" >&2
	exit 1
}

exec "${runner}" "$@"
