#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-rockchip-candidates.sh"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3506-forge1-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3506-forge1-trixie-vendor-cli"
export BOARDS="bananapiforge1"

"${verifier}" "$@"
