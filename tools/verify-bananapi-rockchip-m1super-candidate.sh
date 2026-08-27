#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-rockchip-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-rockchip-m1super-policy.py"
validation_config="${repo_dir}/config/validation/bananapi-rockchip-rk3528-m1super-vendor.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli}"
status_file="${output_dir}/VERIFICATION_STATUS.json"
candidate_level="$(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["candidate_level"])
PY
)"
case "${candidate_level}" in
	"L1 元件候選") verification_evidence_level=L1 ;;
	"L2 內部軟體候選") verification_evidence_level=L2 ;;
	*)
		echo "BPI-M1 Super 候選層級不在允許的狀態機內：${candidate_level}" >&2
		exit 1
		;;
esac

write_entry_state() {
	python3 - "${status_file}" "$1" "$2" "${verification_evidence_level}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail, evidence_level = sys.argv[1:]
temporary = path + ".m1super-entry.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "status": state,
            "detail": detail,
            "evidence_level": evidence_level,
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

[[ -x "${verifier}" && -x "${policy_checker}" ]] || {
	echo "找不到 BPI-M1 Super 候選驗證器或政策守門器" >&2
	exit 1
}
[[ -d "${output_dir}" ]] || {
	echo "找不到 BPI-M1 Super 候選輸出目錄：${output_dir}" >&2
	exit 1
}

write_entry_state in_progress "BPI-M1 Super 前置政策檢查執行中"
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "BPI-M1 Super 前置或完整驗證失敗"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapim1super"
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
"${policy_checker}"

export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL="${verification_evidence_level}"
"${verifier}" "$@"

entry_state_active=no
trap - EXIT
