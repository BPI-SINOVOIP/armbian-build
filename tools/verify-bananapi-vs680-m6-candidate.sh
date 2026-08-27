#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli}"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"
status_file="${output_dir}/VERIFICATION_STATUS.json"

write_entry_state() {
	python3 - "${status_file}" "$1" "$2" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail = sys.argv[1:]
temporary = path + ".m6-entry.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "status": state,
            "detail": detail,
            "evidence_level": "L2",
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

[[ -x "${verifier}" ]] || {
	echo "找不到 BPI-M6 共用候選驗證器：${verifier}" >&2
	exit 1
}
[[ -d "${output_dir}" ]] || {
	echo "找不到 BPI-M6 候選輸出目錄：${output_dir}" >&2
	exit 1
}

write_entry_state in_progress "BPI-M6 前置政策檢查執行中"
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "BPI-M6 前置或完整驗證失敗，禁止沿用舊成功狀態"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

python3 "${repo_dir}/tools/check-bananapi-vs680-m6-policy.py" "${validation_config}"
"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapim6"
export CANDIDATE_FAMILY_NAME="VS680 M6"
export VERIFY_TMP_PREFIX="bananapi-vs680-m6-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL=L2

"${verifier}" "$@"
entry_state_active=no
trap - EXIT
