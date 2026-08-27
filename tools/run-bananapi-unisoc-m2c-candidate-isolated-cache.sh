#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ISOLATION_PARENT="${ISOLATION_PARENT:-${REPO_ROOT}/.tmp}"

mkdir -p "${ISOLATION_PARENT}"
isolation_root="$(mktemp -d "${ISOLATION_PARENT}/bananapi-unisoc-m2c-cache.XXXXXX")"

cleanup() {
	find "${isolation_root}" -xdev -depth -delete 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "${isolation_root}/cache" "${isolation_root}/home" "${isolation_root}/tmp"

HOME="${isolation_root}/home" \
TMPDIR="${isolation_root}/tmp" \
XDG_CACHE_HOME="${isolation_root}/cache" \
	"${SCRIPT_DIR}/build-bananapi-unisoc-m2c-candidate.sh" "$@"
