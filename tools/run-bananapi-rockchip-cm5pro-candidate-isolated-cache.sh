#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-rockchip-cm5pro-candidate.sh"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-rockchip-cm5pro-cache-overlay"

"${runner}" "$@"
