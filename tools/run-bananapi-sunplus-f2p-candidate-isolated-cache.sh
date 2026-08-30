#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export ALLOW_INTERNAL_F2P_SD_CANDIDATE=yes
export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-sunplus-f2p-candidate.sh"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-sunplus-f2p-cache-overlay"

[[ -x "${runner}" ]] || {
	echo "找不到隔離快取執行器：${runner}" >&2
	exit 1
}

exec "${runner}" "$@"
