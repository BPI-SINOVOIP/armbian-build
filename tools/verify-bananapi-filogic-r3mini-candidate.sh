#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-filogic-candidates.sh"
validation_config="${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json"
output_dir="${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli"
status_file="${output_dir}/VERIFICATION_STATUS.json"

write_entry_state() {
	python3 - "${status_file}" "$1" "$2" "$3" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail, level = sys.argv[1:]
temporary = path + ".entry.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "status": state,
            "detail": detail,
            "evidence_level": level,
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
write_entry_state in_progress "R3 Mini 前置政策檢查執行中" L1
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "R3 Mini 前置或完整驗證失敗" "${policy_evidence_level:-L1}"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapir3mini"
export VERIFY_TMP_PREFIX="filogic-r3mini-verify"
export VERIFICATION_PRE_COMPLETE_HOOK="${repo_dir}/tools/finalize-bananapi-filogic-r3mini-verification.sh"

"${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.sh"
[[ "${PUBLIC_RELEASE:-no}" == no ]] || {
	echo "R3 Mini 候選不得要求公開發布" >&2
	exit 1
}
[[ "${HARDWARE_CLAIMS:-no}" == no ]] || {
	echo "R3 Mini 未完成實機驗證，不得要求硬體通過聲明" >&2
	exit 1
}
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
candidate_level="$(jq -r '.candidate_level' "${validation_config}")"
case "${candidate_level}" in
	"L1 元件候選") policy_evidence_level=L1 ;;
	"L2 內部軟體候選") policy_evidence_level=L2 ;;
	*)
		echo "R3 Mini 候選層級不受支援：${candidate_level}" >&2
		exit 1
		;;
esac
write_entry_state in_progress "R3 Mini ${policy_evidence_level} 完整驗證執行中" \
	"${policy_evidence_level}"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL="${policy_evidence_level}"

"${verifier}" "$@"
entry_state_active=no
trap - EXIT
