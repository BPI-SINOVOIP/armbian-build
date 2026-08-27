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

"${policy_checker}" --phase source-contract
mapfile -t build_policy < <(python3 - "${VALIDATION_CONFIG}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    policy = json.load(stream)
print(policy["candidate_level"])
print(policy["source_date_epoch"])
PY
)
candidate_level="${build_policy[0]:-}"
source_date_epoch="${build_policy[1]:-}"
case "${candidate_level}" in
	"L1 元件候選" | "L2 內部軟體候選") ;;
	*)
		echo "BPI-M1 Super 候選層級不在允許的狀態機內：${candidate_level}" >&2
		exit 1
		;;
esac
[[ "${source_date_epoch}" =~ ^[1-9][0-9]*$ ]] || {
	echo "BPI-M1 Super 缺少有效的可重現建置時間戳" >&2
	exit 1
}
if [[ -n "${SOURCE_DATE_EPOCH:-}" && "${SOURCE_DATE_EPOCH}" != "${source_date_epoch}" ]]; then
	echo "BPI-M1 Super SOURCE_DATE_EPOCH 與固定契約不符" >&2
	exit 1
fi
export SOURCE_DATE_EPOCH="${source_date_epoch}"
echo "開始建置 BPI-M1 Super ${candidate_level} 的內部完整映像。"
exec "${builder}" "$@"
