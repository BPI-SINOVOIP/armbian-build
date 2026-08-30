#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-rockchip-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3588-w3-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3588-w3-trixie-vendor-cli"
export BOARDS="bananapiw3"

[[ -x "${verifier}" ]] || {
	echo "找不到 Rockchip 共用候選驗證器：${verifier}" >&2
	exit 1
}

exec "${verifier}" "$@"
