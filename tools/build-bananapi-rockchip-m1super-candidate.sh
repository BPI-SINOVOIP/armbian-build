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
candidate_level="$(python3 - "${VALIDATION_CONFIG}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["candidate_level"])
PY
)"
case "${candidate_level}" in
	"L1 元件候選" | "L2 內部軟體候選") ;;
	*)
		echo "BPI-M1 Super 候選層級不在允許的狀態機內：${candidate_level}" >&2
		exit 1
		;;
esac
echo "開始建置 BPI-M1 Super ${candidate_level} 的內部完整映像。"
exec "${builder}" "$@"
