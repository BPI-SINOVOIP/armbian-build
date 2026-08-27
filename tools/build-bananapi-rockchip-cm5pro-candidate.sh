#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3576-cm5pro-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3576-cm5pro-trixie-vendor-cli"
export BOARDS="bananapicm5pro"
export CANDIDATE_FAMILY_NAME="Rockchip RK3576 CM5 Pro"
export CANDIDATE_LOCK_FILE=".bananapi-rockchip-cm5pro-build.lock"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-35}"

"${builder}" "$@"
