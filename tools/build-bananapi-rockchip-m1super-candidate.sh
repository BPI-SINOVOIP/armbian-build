#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-rockchip-m1super-policy.py"

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli"
export BOARDS="bananapim1super"
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes

material_status="${OUTPUT_DIR}/M1SUPER_MATERIAL_STATUS.json"
material_evidence="${OUTPUT_DIR}/M1SUPER_MATERIAL_EVIDENCE.json"

write_material_state() {
	python3 - "${material_status}" "$1" "$2" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail = sys.argv[1:]
temporary = path + ".build-entry.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "status": state,
            "detail": detail,
            "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        stream,
        ensure_ascii=False,
        indent=2,
    )
    stream.write("\n")
os.replace(temporary, path)
PY
}

[[ -x "${builder}" && -x "${policy_checker}" ]] || {
	echo "找不到 BPI-M1 Super 候選建置器或政策守門器" >&2
	exit 1
}

mkdir -p "${OUTPUT_DIR}"
rm -f "${material_evidence}"
write_material_state in_progress "BPI-M1 Super 建置前來源契約檢查執行中"
material_state_active=yes
finish_material_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${material_state_active}" == yes ]]; then
		rm -f "${material_evidence}"
		write_material_state failed "BPI-M1 Super 建置失敗或來源契約不符"
	fi
	exit "${exit_status}"
}
trap finish_material_state EXIT

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
"${builder}" "$@"

python3 - "${OUTPUT_DIR}/COMPLETION_STATUS.json" "${source_date_epoch}" <<'PY'
import json
import os
import sys

path, source_date_epoch = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    status = json.load(stream)
if status.get("status") != "complete":
    raise SystemExit("BPI-M1 Super 建置完成狀態不是 complete")
status["source_date_epoch"] = int(source_date_epoch)
temporary = path + ".m1super-source-date.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(temporary, path)
PY

write_material_state pending_verification "BPI-M1 Super 映像已建置，尚待正式物質驗證"
material_state_active=no
trap - EXIT
