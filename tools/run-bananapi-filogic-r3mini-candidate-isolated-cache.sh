#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-filogic-candidates-isolated-cache.sh"
minimum_free_gib="${MINIMUM_FREE_GIB:-80}"

[[ "${minimum_free_gib}" =~ ^[0-9]+$ ]] || {
	echo "R3 Mini 的 MINIMUM_FREE_GIB 必須是整數。" >&2
	exit 2
}
((minimum_free_gib >= 40)) || {
	echo "R3 Mini 隔離建置空間下限不得低於 40 GiB。" >&2
	exit 2
}

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
export BOARDS="bananapir3mini"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-filogic-r3mini-cache-overlay"
export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-filogic-r3mini-candidate.sh"
export MINIMUM_FREE_GIB="${minimum_free_gib}"

exec "${runner}" "$@"
