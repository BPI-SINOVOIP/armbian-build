#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-filogic-candidates-isolated-cache.sh"
minimum_free_gib="${MINIMUM_FREE_GIB:-80}"
fixed_cache_lower="/media/pi/SMCI/armbian/bpi-v26.2.1/cache"
fixed_cache_target="${repo_dir}/cache"
fixed_overlay_root="${repo_dir}/.tmp/bananapi-filogic-r3mini-cache-overlay"

for variable in CACHE_LOWER CACHE_TARGET CACHE_OVERLAY_ROOT CANDIDATE_BUILDER; do
	if [[ -n "${!variable+x}" ]]; then
		echo "R3 Mini 專用入口禁止覆寫 ${variable}。" >&2
		exit 2
	fi
done

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
export CACHE_LOWER="${fixed_cache_lower}"
export CACHE_TARGET="${fixed_cache_target}"
export CACHE_OVERLAY_ROOT="${fixed_overlay_root}"
export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-filogic-r3mini-candidate.sh"
export MINIMUM_FREE_GIB="${minimum_free_gib}"
export ALLOW_INTERNAL_R3MINI_CANDIDATE=yes
export REQUIRE_ISOLATED_CACHE=yes

exec "${runner}" "$@"
