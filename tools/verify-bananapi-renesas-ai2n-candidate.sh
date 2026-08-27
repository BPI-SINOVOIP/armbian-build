#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-renesas-rzv2n-ai2n-trixie-legacy-cli}"
generic_verifier="${GENERIC_CANDIDATE_VERIFIER:-${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh}"
source_verifier="${repo_dir}/tools/verify-bananapi-renesas-ai2n-sources.sh"

for command in awk cmp git mktemp mv python3 sha256sum unlink; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "AI2N 候選驗證失敗：$*" >&2
	exit 1
}

PUBLIC_RELEASE="${PUBLIC_RELEASE:-no}" POLICY_ONLY=yes \
	VALIDATION_CONFIG="${validation_config}" "${source_verifier}"

manifest="${output_dir}/RENESAS_SOURCE_EVIDENCE.tsv"
status="${output_dir}/RENESAS_SOURCE_STATUS.json"
matrix="${output_dir}/CANDIDATES.tsv"
[[ -s "${manifest}" && -s "${status}" && -s "${matrix}" ]] || fail "缺少來源或候選矩陣證據"
candidate_commit="$(awk -F '\t' 'NR == 2 { print $10 }' "${matrix}")"
[[ "${candidate_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "候選來源提交格式不符"
config_relative="${validation_config#"${repo_dir}"/}"
[[ "${config_relative}" != "${validation_config}" ]] || fail "驗證設定必須位於來源倉庫"
build_config="$(mktemp "${repo_dir}/.tmp/ai2n-build-config.XXXXXX.json")"
expected_manifest="$(mktemp "${repo_dir}/.tmp/ai2n-source-evidence.XXXXXX.tsv")"
cleanup() {
	unlink "${build_config}" "${expected_manifest}" 2>/dev/null || true
}
trap cleanup EXIT
git -C "${repo_dir}" show "${candidate_commit}:${config_relative}" >"${build_config}"
build_config_sha256="$(sha256sum "${build_config}" | cut -d' ' -f1)"
manifest_sha256="$(sha256sum "${manifest}" | cut -d' ' -f1)"

python3 - "${status}" "${candidate_commit}" "${build_config_sha256}" "${manifest_sha256}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
expected = {
    "status": "complete",
    "source_commit": sys.argv[2],
    "validation_config_sha256": sys.argv[3],
    "manifest_sha256": sys.argv[4],
    "public_release_allowed": False,
    "hardware_evidence_present": False,
}
for key, value in expected.items():
    if status.get(key) != value:
        raise SystemExit(f"AI2N 來源狀態欄位 {key} 不符")
PY

{
	printf 'kind\tname\tpath_or_source\tref_or_usage\trevision\tsha256\n'
	python3 - "${build_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
for name in sorted(config["source_commits"]):
    item = config["source_commits"][name]
    print(f"source\t{name}\t{item['source']}\t{item['ref']}\t{item['revision']}\t{item['license_sha256']}")
for path in sorted(config["proprietary_assets"]):
    print(f"proprietary\tasset\t{path}\tpublic_release_blocked\t-\t{config['proprietary_assets'][path]}")
for path in sorted(config["unused_prebuilt_packaging_tools"]):
    print(f"unused-tool\ttool\t{path}\tunused\t-\t{config['unused_prebuilt_packaging_tools'][path]}")
PY
} >"${expected_manifest}"
cmp --silent "${expected_manifest}" "${manifest}" || fail "來源證據清單不符"

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="bpi-ai2n" CANDIDATE_FAMILY_NAME="Renesas RZ/V2N" \
	VERIFY_TMP_PREFIX="renesas-ai2n-verify" VERIFICATION_EVIDENCE_LEVEL="L1" \
	"${generic_verifier}" "$@"

verification_status="${output_dir}/VERIFICATION_STATUS.json"
python3 - "${verification_status}" "${manifest_sha256}" <<'PY'
import json
import os
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    status = json.load(stream)
status["renesas_source_manifest_sha256"] = sys.argv[2]
status["evidence_level"] = "L1"
status["candidate_scope"] = "internal-l1"
status["public_release_allowed"] = False
status["hardware_evidence_present"] = False
temporary = f"{path}.partial"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(temporary, path)
PY

trap - EXIT
cleanup
echo "AI2N 來源、映像與內部 L1 守門全部通過。"
