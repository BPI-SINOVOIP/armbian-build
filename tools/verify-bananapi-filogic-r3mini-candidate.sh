#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-filogic-candidates.sh"
validation_config="${repo_dir}/config/validation/bananapi-filogic-mt7986-r3mini-current.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli}"
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
for stale in VERIFICATION_STATUS.json.partial R3MINI_CALIBRATION.json \
	R3MINI_CALIBRATION.json.partial; do
	[[ ! -e "${output_dir}/${stale}" ]] || unlink "${output_dir}/${stale}"
done
write_entry_state in_progress "R3 Mini 前置政策檢查執行中" L1
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		[[ ! -e "${status_file}.partial" ]] || unlink "${status_file}.partial"
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

"${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.sh" --source-contract-only
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
export REQUIRE_BUILD_VERIFIER_IDENTITY=yes
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
export VERIFICATION_DEFER_STATUS_PROMOTION=yes

entry_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
entry_tree="$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')"
entry_validation_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"

"${verifier}" "$@"
if [[ "${policy_evidence_level}" == L1 ]]; then
	python3 "${repo_dir}/tools/inspect-bananapi-filogic-r3mini-material.py" \
		--validation "${validation_config}" --output "${output_dir}" \
		--mode calibration --status "${status_file}.partial"
else
	"${repo_dir}/tools/check-bananapi-filogic-r3mini-policy.sh" \
		--material-evidence-only --output "${output_dir}" --status "${status_file}.partial"
fi
[[ "$(git -C "${repo_dir}" rev-parse HEAD)" == "${entry_commit}" ]] || {
	echo "R3 Mini 物質驗證期間來源 HEAD 已改變" >&2
	exit 1
}
[[ "$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')" == "${entry_tree}" ]] || {
	echo "R3 Mini 物質驗證期間來源 tree 已改變" >&2
	exit 1
}
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] || {
	echo "R3 Mini 物質驗證期間來源工作樹已改變" >&2
	exit 1
}
[[ "$(sha256sum "${validation_config}" | cut -d' ' -f1)" == \
	"${entry_validation_sha256}" ]] || {
	echo "R3 Mini 物質驗證期間 validation 已改變" >&2
	exit 1
}
mv "${status_file}.partial" "${status_file}"
entry_state_active=no
trap - EXIT
echo "R3 Mini ${policy_evidence_level} 候選證據已原子閉合。"
