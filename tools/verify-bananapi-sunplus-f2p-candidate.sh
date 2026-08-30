#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-sunplus-sp7021-f2p-trixie-legacy-cli}"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"
status_file="${output_dir}/VERIFICATION_STATUS.json"

write_entry_state() {
	python3 - "${status_file}" "$1" "$2" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail = sys.argv[1:]
temporary = path + ".entry.partial"
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

[[ -d "${output_dir}" ]] || {
	echo "找不到 F2P 候選輸出目錄：${output_dir}" >&2
	exit 1
}
write_entry_state in_progress "F2P 前置政策檢查執行中"
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "F2P 前置或完整驗證失敗"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

python3 "${repo_dir}/tools/check-bananapi-sunplus-f2p-source-policy.py" \
	"${validation_config}"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapif2p"
export CANDIDATE_FAMILY_NAME="Sunplus SP7021 F2P"
export VERIFY_TMP_PREFIX="bananapi-sunplus-f2p-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL=L2

"${verifier}" "$@"
entry_state_active=no
trap - EXIT
