#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-realtek-m4-candidate.sh"
export CACHE_LOWER="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-realtek-m4-candidate-cache-overlay"
export CACHE_TARGET="${repo_dir}/cache"
export ALLOW_INTERNAL_M4_CANDIDATE=yes
export REQUIRE_ISOLATED_CACHE=yes

[[ -x "${runner}" ]] || {
	echo "找不到隔離快取執行器：${runner}" >&2
	exit 1
}

exec "${runner}" "$@"
