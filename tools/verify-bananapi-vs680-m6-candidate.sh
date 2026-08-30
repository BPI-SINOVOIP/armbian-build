#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"
policy_checker="${repo_dir}/tools/check-bananapi-vs680-m6-policy.py"
validation_config="${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json"
fixed_output_dir="${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli"
requested_output_dir="${OUTPUT_DIR:-}"

if [[ -n "${requested_output_dir}" &&
	"$(realpath -m -- "${requested_output_dir}")" != "$(realpath -m -- "${fixed_output_dir}")" ]]; then
	echo "BPI-M6 只允許固定輸出目錄：${fixed_output_dir}" >&2
	exit 2
fi
output_dir="${fixed_output_dir}"
status_file="${output_dir}/VERIFICATION_STATUS.json"
material_status="${output_dir}/M6_MATERIAL_STATUS.json"
material_evidence="${output_dir}/M6_MATERIAL_EVIDENCE.json"
calibration_evidence="${output_dir}/M6_CALIBRATION.json"
extra_status_file=""

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
	*) echo "BPI-M6 候選層級、範圍與證據等級不成對：${candidate_level}" >&2; exit 2 ;;
esac

write_entry_state() {
	python3 - "${status_file}" "$1" "$2" "${verification_evidence_level}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail, evidence_level = sys.argv[1:]
temporary = path + ".m6-entry.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "status": state,
            "detail": detail,
            "evidence_level": evidence_level,
            "public_release_allowed": False,
            "hardware_claims_allowed": False,
            "opaque_payload_redistribution_verified": False,
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

write_material_state() {
	python3 - "${material_status}" "$1" "$2" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail = sys.argv[1:]
temporary = path + ".verify-entry.partial"
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

[[ -x "${verifier}" && -x "${policy_checker}" ]] || {
	echo "找不到 BPI-M6 候選驗證器或政策守門器。" >&2
	exit 1
}
[[ -d "${output_dir}" ]] || {
	echo "找不到 BPI-M6 候選輸出目錄：${output_dir}" >&2
	exit 1
}

case "${PUBLIC_RELEASE:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M6 內部候選禁止公開發布。" >&2; exit 2 ;;
esac
case "${HARDWARE_CLAIMS:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M6 內部候選禁止硬體通過聲明。" >&2; exit 2 ;;
esac
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no

rm -f "${material_evidence}" "${calibration_evidence}" "${status_file}.partial"
write_entry_state in_progress "BPI-M6 ${verification_evidence_level} 前置政策檢查執行中"
write_material_state in_progress "BPI-M6 ${verification_evidence_level} 物質驗證執行中"
entry_state_active=yes
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		rm -f "${status_file}.partial" "${material_evidence}" "${calibration_evidence}"
		write_entry_state failed "BPI-M6 前置或完整驗證失敗，禁止沿用舊成功狀態"
		write_material_state failed "BPI-M6 物質驗證失敗"
	fi
	[[ -z "${extra_status_file}" || ! -e "${extra_status_file}" ]] || rm -f "${extra_status_file}"
	exit "${exit_status}"
}
trap finish_entry_state EXIT

"${policy_checker}" "${validation_config}" --phase source-contract
"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"

mkdir -p "${repo_dir}/.tmp"
extra_status_file="$(mktemp "${repo_dir}/.tmp/bananapi-vs680-m6-status.XXXXXX.json")"
cat >"${extra_status_file}" <<'JSON'
{
  "public_release_allowed": false,
  "hardware_claims_allowed": false,
  "opaque_payload_redistribution_verified": false
}
JSON

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapim6"
export CANDIDATE_FAMILY_NAME="VS680 M6"
export VERIFY_TMP_PREFIX="bananapi-vs680-m6-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL="${verification_evidence_level}"
export VERIFICATION_EXTRA_STATUS_JSON="${extra_status_file}"
export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes
export REQUIRE_BUILD_VERIFIER_IDENTITY=yes
export VERIFICATION_DEFER_STATUS_PROMOTION=yes

entry_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
entry_tree="$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')"
entry_validation_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"

"${verifier}" "$@"
if [[ "${verification_evidence_level}" == L1 ]]; then
	"${policy_checker}" "${validation_config}" --phase calibration \
		--evidence-source live --status "${status_file}.partial" --finalize-calibration
	"${policy_checker}" "${validation_config}" --phase calibration \
		--evidence-source live --status "${status_file}.partial"
	write_material_state calibration_complete \
		"BPI-M6 L1 校準映像已完成共用與專用唯讀驗證"
else
	"${policy_checker}" "${validation_config}" --phase material-evidence \
		--evidence-source live --status "${status_file}.partial" \
		--finalize-material-status
	"${policy_checker}" "${validation_config}" --phase material-evidence \
		--evidence-source live --status "${status_file}.partial"
fi

[[ "$(git -C "${repo_dir}" rev-parse HEAD)" == "${entry_commit}" ]] || {
	echo "BPI-M6 物質驗證期間來源 HEAD 已改變" >&2
	exit 1
}
[[ "$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')" == "${entry_tree}" ]] || {
	echo "BPI-M6 物質驗證期間來源 tree 已改變" >&2
	exit 1
}
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] || {
	echo "BPI-M6 物質驗證期間來源工作樹已改變" >&2
	exit 1
}
[[ "$(sha256sum "${validation_config}" | cut -d' ' -f1)" == \
	"${entry_validation_sha256}" ]] || {
	echo "BPI-M6 物質驗證期間 validation 已改變" >&2
	exit 1
}
mv "${status_file}.partial" "${status_file}"
rm -f "${extra_status_file}"
extra_status_file=""
entry_state_active=no
trap - EXIT
echo "BPI-M6 ${verification_evidence_level} 候選證據已原子閉合。"
