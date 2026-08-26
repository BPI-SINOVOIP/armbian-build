#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-rockchip-rk3308-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3308-trixie-current-cli}"
boards_text="${BOARDS:-bananapip2pro}"
generic_verifier="${GENERIC_CANDIDATE_VERIFIER:-${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh}"

for command in awk cmp cut git grep mktemp mv python3 sha256sum; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "Rockchip 驗證失敗：$*" >&2
	exit 1
}

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
trap 'rm -f "${expected_manifest}"' EXIT
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

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="${boards_text}" CANDIDATE_FAMILY_NAME="Rockchip" \
	VERIFY_TMP_PREFIX="rockchip-verify" \
	"${generic_verifier}" "$@"

verification_status="${output_dir}/VERIFICATION_STATUS.json"
python3 - "${verification_status}" "${expected_commit}" "${manifest_sha256}" <<'PY'
import json
import os
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    status = json.load(stream)
status["rkbin_commit"] = sys.argv[2]
status["rkbin_manifest_sha256"] = sys.argv[3]
temporary = path + ".partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(temporary, path)
PY

echo "Rockchip rkbin 與映像 L2 守門全部通過。"
