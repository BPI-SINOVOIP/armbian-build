#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"
minimum_free_gib="${MINIMUM_FREE_GIB:-80}"

[[ "${minimum_free_gib}" =~ ^[0-9]+$ ]] || {
	echo "BPI-AIM7 的 MINIMUM_FREE_GIB 必須是整數。" >&2
	exit 2
}
(( minimum_free_gib >= 40 )) || {
	echo "BPI-AIM7 的 MINIMUM_FREE_GIB 不得低於 40 GiB。" >&2
	exit 2
}

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-rockchip-aim7-candidate.sh"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-rockchip-aim7-cache-overlay"
export ALLOW_INTERNAL_AIM7_CANDIDATE=yes
export REQUIRE_ISOLATED_CACHE=yes
export MINIMUM_FREE_GIB="${minimum_free_gib}"

[[ -x "${runner}" ]] || {
	echo "找不到隔離快取執行器：${runner}" >&2
	exit 1
}

exec "${runner}" "$@"
