#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-filogic-candidates.sh"
output_dir="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
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
	echo "找不到 R3 Mini 候選輸出目錄：${output_dir}" >&2
	exit 1
}
write_entry_state in_progress "R3 Mini 前置政策檢查執行中"
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "R3 Mini 前置或完整驗證失敗"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

export VALIDATION_CONFIG="${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapir3mini"
export VERIFY_TMP_PREFIX="filogic-r3mini-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL=L2
export VERIFICATION_PRE_COMPLETE_HOOK="${repo_dir}/tools/finalize-bananapi-filogic-r3mini-verification.sh"

"${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.sh"
"${verifier}" "$@"
entry_state_active=no
trap - EXIT
