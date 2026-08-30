#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3588-aim7-trixie-vendor-cli}"
verifier="${repo_dir}/tools/verify-bananapi-rockchip-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-rockchip-aim7-policy.py"
status_file="${output_dir}/VERIFICATION_STATUS.json"

[[ -x "${verifier}" && -x "${policy_checker}" ]] || {
	echo "找不到 BPI-AIM7 候選驗證器或政策守門器" >&2
	exit 1
}
[[ -d "${output_dir}" ]] || {
	echo "找不到 BPI-AIM7 候選輸出目錄：${output_dir}" >&2
	exit 1
}

policy_evidence_level="$(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["current_evidence_level"])
PY
)"
case "${policy_evidence_level}" in
	L1 | L2) ;;
	*)
		echo "BPI-AIM7 驗證拒絕：契約證據層級只接受 L1 或 L2。" >&2
		exit 2
		;;
esac

write_entry_state() {
	python3 - "${status_file}" "$1" "$2" "${policy_evidence_level}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail, evidence_level = sys.argv[1:]
temporary = path + ".aim7-entry.partial"
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

write_entry_state in_progress "BPI-AIM7 前置政策與固定來源檢查執行中"
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "BPI-AIM7 前置或完整驗證失敗，禁止沿用舊成功狀態"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapiaim7"
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
"${policy_checker}"

export CANDIDATE_FAMILY_NAME="Rockchip AIM7"
export VERIFY_TMP_PREFIX="bananapi-rockchip-aim7-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL="${policy_evidence_level}"
export REQUIRE_BUILD_VERIFIER_IDENTITY=yes
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
"${verifier}" "$@"

entry_state_active=no
trap - EXIT
