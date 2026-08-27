#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli}"
verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"
status_file="${output_dir}/VERIFICATION_STATUS.json"
verification_evidence_level=L1
extra_status_file=""

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

[[ -d "${output_dir}" ]] || {
	echo "找不到 BPI-M6 候選輸出目錄：${output_dir}" >&2
	exit 1
}
for command in cat mktemp mkdir mv python3 unlink; do
	command -v "${command}" >/dev/null || {
		echo "缺少 BPI-M6 原子狀態所需命令：${command}" >&2
		exit 1
	}
done

entry_state_active=no
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "BPI-M6 前置或完整驗證失敗，禁止沿用舊成功狀態"
	fi
	[[ -z "${extra_status_file}" || ! -e "${extra_status_file}" ]] || unlink "${extra_status_file}"
	exit "${exit_status}"
}
trap finish_entry_state EXIT
write_entry_state in_progress "BPI-M6 前置政策檢查執行中"
entry_state_active=yes
for stale_evidence in VERIFICATION.tsv UBOOT_PAYLOAD_EVIDENCE.tsv FINAL_CONFIG_EVIDENCE.tsv \
	VERIFICATION.tsv.partial UBOOT_PAYLOAD_EVIDENCE.tsv.partial FINAL_CONFIG_EVIDENCE.tsv.partial; do
	[[ ! -e "${output_dir}/${stale_evidence}" ]] || unlink "${output_dir}/${stale_evidence}"
done

[[ -x "${verifier}" ]] || {
	echo "找不到 BPI-M6 共用候選驗證器：${verifier}" >&2
	exit 1
}
[[ -f "${validation_config}" ]] || {
	echo "找不到 BPI-M6 驗證契約：${validation_config}" >&2
	exit 1
}
verification_evidence_level="$(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)

pairs = {
    "L1 元件候選": ("internal-component-only", "L1"),
    "L2 內部軟體候選": ("internal-l2", "L2"),
}
expected = pairs.get(config.get("candidate_level"))
if expected is None or (config.get("candidate_scope"), config.get("current_evidence_level")) != expected:
    raise SystemExit("BPI-M6 候選層級、範圍與證據等級不成對")
print(expected[1])
PY
)"
write_entry_state in_progress "BPI-M6 ${verification_evidence_level} 前置政策檢查執行中"

case "${PUBLIC_RELEASE:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M6 內部候選禁止公開發布。" >&2; exit 2 ;;
esac
case "${HARDWARE_CLAIMS:-no}" in
	1 | true | TRUE | yes | YES) echo "BPI-M6 內部候選禁止硬體通過聲明。" >&2; exit 2 ;;
esac
export PUBLIC_RELEASE=no
export HARDWARE_CLAIMS=no
python3 "${repo_dir}/tools/check-bananapi-vs680-m6-policy.py" "${validation_config}"
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

"${verifier}" "$@"
unlink "${extra_status_file}"
extra_status_file=""
entry_state_active=no
trap - EXIT
