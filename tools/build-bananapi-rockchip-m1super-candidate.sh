#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-rockchip-m1super-policy.py"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli"
export BOARDS="bananapim1super"

[[ -x "${builder}" && -x "${policy_checker}" ]] || {
	echo "找不到 BPI-M1 Super 候選建置器或政策守門器" >&2
	exit 1
}

"${policy_checker}"
exec "${builder}" "$@"
