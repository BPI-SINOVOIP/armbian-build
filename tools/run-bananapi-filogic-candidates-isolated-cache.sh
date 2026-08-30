#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
generic_runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

[[ -x "${generic_runner}" ]] || {
	echo "找不到隔離快取執行器：${generic_runner}" >&2
	exit 1
}

CANDIDATE_BUILDER="${CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-filogic-candidates.sh}" \
	CACHE_OVERLAY_ROOT="${CACHE_OVERLAY_ROOT:-${repo_dir}/.tmp/bananapi-filogic-r3-cache-overlay}" \
	"${generic_runner}" "$@"
