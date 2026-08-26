#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${repo_dir}/tools/run-bananapi-candidates-isolated-cache.sh"

export CANDIDATE_BUILDER="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"
export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3506-forge1-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3506-forge1-trixie-vendor-cli"
export BOARDS="bananapiforge1"
export CACHE_OVERLAY_ROOT="${repo_dir}/.tmp/bananapi-rockchip-forge1-cache-overlay"

"${runner}" "$@"
