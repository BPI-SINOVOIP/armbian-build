#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-rockchip-rk3308-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3308-trixie-current-cli}"
boards_text="${BOARDS:-bananapip2pro}"
generic_verifier="${GENERIC_CANDIDATE_VERIFIER:-${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh}"

for command in mv python3; do
	command -v "${command}" >/dev/null || {
		echo "缺少建立失敗狀態所需命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "Rockchip 驗證失敗：$*" >&2
	exit 1
}

[[ -d "${output_dir}" ]] || fail "找不到 Rockchip 候選輸出目錄：${output_dir}"
verification_status="${output_dir}/VERIFICATION_STATUS.json"
write_entry_state() {
	python3 - "${verification_status}" "$1" "$2" <<'PY'
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
write_entry_state in_progress "Rockchip 前置來源證據檢查執行中"
entry_state_active=yes
expected_manifest=""
expected_wifi_manifest=""
extra_status=""
finish_entry_state() {
	local exit_status=$?
	trap - EXIT
	[[ -z "${expected_manifest}" ]] || rm -f "${expected_manifest}"
	[[ -z "${expected_wifi_manifest}" ]] || rm -f "${expected_wifi_manifest}"
	[[ -z "${extra_status}" ]] || rm -f "${extra_status}"
	if [[ ${exit_status} -ne 0 && "${entry_state_active}" == yes ]]; then
		write_entry_state failed "Rockchip 前置或完整驗證失敗"
	fi
	exit "${exit_status}"
}
trap finish_entry_state EXIT

for command in awk cmp cut git grep mktemp mv python3 sha256sum; do
	command -v "${command}" >/dev/null || fail "缺少必要命令：${command}"
done

[[ -x "${generic_verifier}" ]] || fail "找不到共用候選驗證器：${generic_verifier}"
manifest="${output_dir}/RKBIN_EVIDENCE.tsv"
status="${output_dir}/RKBIN_STATUS.json"
[[ -s "${manifest}" && -s "${status}" ]] || fail "缺少 rkbin 來源證據"
grep -q '"status": "complete"' "${status}" || fail "rkbin 來源狀態不是 complete"

candidate_commit="$(awk -F '\t' 'NR == 2 { print $10 }' "${output_dir}/CANDIDATES.tsv")"
[[ "${candidate_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "候選來源提交格式不符"
config_relative="${validation_config#"${repo_dir}"/}"
[[ "${config_relative}" != "${validation_config}" ]] || fail "驗證設定必須位於來源倉庫"
build_config_sha256="$(git -C "${repo_dir}" show \
	"${candidate_commit}:${config_relative}" | sha256sum | cut -d' ' -f1)"
expected_commit="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["rkbin_commit"])
PY
)"

python3 - "${status}" "${candidate_commit}" "${expected_commit}" \
	"${build_config_sha256}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
expected = {
    "source_commit": sys.argv[2],
    "rkbin_commit": sys.argv[3],
    "validation_config_sha256": sys.argv[4],
}
for key, value in expected.items():
    if status.get(key) != value:
        raise SystemExit(f"rkbin 狀態欄位 {key} 不符")
PY

expected_manifest="$(mktemp "${repo_dir}/.tmp/rkbin-evidence.XXXXXX")"
extra_status="$(mktemp "${repo_dir}/.tmp/rockchip-verification-extra.XXXXXX.json")"
{
	printf 'path\tsha256\n'
	python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    blobs = json.load(stream)["rkbin_blobs"]
for path in sorted(blobs):
    print(f"{path}\t{blobs[path]}")
PY
} >"${expected_manifest}"
cmp --silent "${expected_manifest}" "${manifest}" || fail "rkbin blob 清單不符"
manifest_sha256="$(sha256sum "${manifest}" | cut -d' ' -f1)"
python3 - "${status}" "${manifest_sha256}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
if status.get("manifest_sha256") != sys.argv[2]:
    raise SystemExit("rkbin 清單雜湊不符")
PY

wifi_expected_commit="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("wifi_driver_commit", ""))
PY
)"
wifi_manifest_sha256=""
if [[ -n "${wifi_expected_commit}" ]]; then
	wifi_manifest="${output_dir}/WIFI_DRIVER_EVIDENCE.tsv"
	wifi_status="${output_dir}/WIFI_DRIVER_STATUS.json"
	[[ -s "${wifi_manifest}" && -s "${wifi_status}" ]] || fail "缺少 RTL8852BS 固定來源證據"
	grep -q '"status": "complete"' "${wifi_status}" || fail "RTL8852BS 來源狀態不是 complete"

	python3 - "${wifi_status}" "${candidate_commit}" "${wifi_expected_commit}" \
		"${build_config_sha256}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
expected = {
    "source_commit": sys.argv[2],
    "wifi_driver_commit": sys.argv[3],
    "validation_config_sha256": sys.argv[4],
}
for key, value in expected.items():
    if status.get(key) != value:
        raise SystemExit(f"RTL8852BS 狀態欄位 {key} 不符")
PY

	expected_wifi_manifest="$(mktemp "${repo_dir}/.tmp/wifi-driver-evidence.XXXXXX")"
	{
		printf 'path\tsha256\n'
		python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    files = json.load(stream)["wifi_driver_files"]
for path in sorted(files):
    print(f"{path}\t{files[path]}")
PY
	} >"${expected_wifi_manifest}"
	cmp --silent "${expected_wifi_manifest}" "${wifi_manifest}" || fail "RTL8852BS 固定來源清單不符"
	wifi_manifest_sha256="$(sha256sum "${wifi_manifest}" | cut -d' ' -f1)"
	python3 - "${wifi_status}" "${wifi_manifest_sha256}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
if status.get("manifest_sha256") != sys.argv[2]:
    raise SystemExit("RTL8852BS 清單雜湊不符")
PY
fi

python3 - "${extra_status}" "${expected_commit}" "${manifest_sha256}" \
	"${wifi_expected_commit}" "${wifi_manifest_sha256}" <<'PY'
import json
import sys

path, rkbin_commit, rkbin_manifest, wifi_commit, wifi_manifest = sys.argv[1:]
data = {
    "rkbin_commit": rkbin_commit,
    "rkbin_manifest_sha256": rkbin_manifest,
}
if wifi_commit:
    data["wifi_driver_commit"] = wifi_commit
    data["wifi_driver_manifest_sha256"] = wifi_manifest
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="${boards_text}" CANDIDATE_FAMILY_NAME="Rockchip" \
	VERIFY_TMP_PREFIX="rockchip-verify" \
	VERIFICATION_EXTRA_STATUS_JSON="${extra_status}" \
	"${generic_verifier}" "$@"
entry_state_active=no

echo "Rockchip 固定來源與映像 L2 守門全部通過。"
