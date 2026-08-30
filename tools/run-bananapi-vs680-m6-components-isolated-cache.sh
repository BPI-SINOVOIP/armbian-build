#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-vs680-m6-components.sh"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-vs680-m6-components-cache-overlay"

[[ -x "${runner}" ]] || {
	echo "找不到隔離快取執行器：${runner}" >&2
	exit 1
}

exec "${runner}" "$@"
