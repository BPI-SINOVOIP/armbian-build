#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3588-w3-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3588-w3-trixie-vendor-cli"
export BOARDS="bananapiw3"

[[ -x "${builder}" ]] || {
	echo "找不到 Rockchip 共用候選建置器：${builder}" >&2
	exit 1
}

exec "${builder}" "$@"
