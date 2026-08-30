#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${R4PRO_GENERIC_ISOLATED_RUNNER:-${repo_dir}/tools/run-bananapi-filogic-candidates-isolated-cache.sh}"

[[ -x "${runner}" ]] || {
	echo "找不到 Filogic 隔離快取執行器：${runner}" >&2
	exit 1
}

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-filogic-r4pro-candidate.sh"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-filogic-r4pro-cache-overlay"

"${runner}" "$@"
