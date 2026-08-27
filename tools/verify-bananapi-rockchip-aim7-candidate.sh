#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
output_dir="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3588-aim7-trixie-vendor-cli"
rockchip_verifier="${repo_dir}/tools/verify-bananapi-rockchip-candidates.sh"
generic_verifier="${repo_dir}/tools/verify-bananapi-sunxi-candidates.sh"

verify_l1_rkbin_evidence() {
	local matrix="${output_dir}/CANDIDATES.tsv"
	local manifest="${output_dir}/RKBIN_EVIDENCE.tsv"
	local status="${output_dir}/RKBIN_STATUS.json"
	local candidate_commit config_relative build_config_sha256

	for command in awk git python3 sha256sum; do
		command -v "${command}" >/dev/null || {
			echo "BPI-AIM7 L1 驗證缺少必要命令：${command}" >&2
			return 1
		}
	done
	[[ -s "${matrix}" && -s "${manifest}" && -s "${status}" ]] || {
		echo "BPI-AIM7 L1 驗證缺少 Rockchip 固定來源證據。" >&2
		return 1
	}
	candidate_commit="$(awk -F '\t' 'NR == 2 { print $10 }' "${matrix}")"
	[[ "${candidate_commit}" =~ ^[0-9a-f]{40}$ ]] || {
		echo "BPI-AIM7 L1 候選來源提交格式不符。" >&2
		return 1
	}
	config_relative="${validation_config#"${repo_dir}"/}"
	[[ "${config_relative}" != "${validation_config}" ]] || {
		echo "BPI-AIM7 驗證設定必須位於來源倉庫。" >&2
		return 1
	}
	build_config_sha256="$(
		git -C "${repo_dir}" show "${candidate_commit}:${config_relative}" |
			sha256sum | awk '{ print $1 }'
	)"

	python3 - "${validation_config}" "${manifest}" "${status}" "${candidate_commit}" "${build_config_sha256}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config_path, manifest_path, status_path, source_commit, config_sha256 = sys.argv[1:]
with open(config_path, encoding="utf-8") as stream:
    config = json.load(stream)
with open(status_path, encoding="utf-8") as stream:
    status = json.load(stream)

expected_status = {
    "status": "complete",
    "source_commit": source_commit,
    "rkbin_commit": config["rkbin_commit"],
    "validation_config_sha256": config_sha256,
}
for key, value in expected_status.items():
    if status.get(key) != value:
        raise SystemExit(f"BPI-AIM7 L1 的 RKBin 狀態欄位 {key} 不符")

manifest_bytes = Path(manifest_path).read_bytes()
if status.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest():
    raise SystemExit("BPI-AIM7 L1 的 RKBin 清單雜湊不符")
expected_lines = ["path\tsha256"]
expected_lines.extend(
    f"{path}\t{digest}" for path, digest in sorted(config["rkbin_blobs"].items())
)
if manifest_bytes.decode().splitlines() != expected_lines:
    raise SystemExit("BPI-AIM7 L1 的 RKBin 固定來源清單不符")
PY
}

python3 "${repo_dir}/tools/check-bananapi-rockchip-aim7-policy.py" "${validation_config}"
policy_evidence_level="$(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["current_evidence_level"])
PY
)"
case "${policy_evidence_level}" in
	L1)
		verify_l1_rkbin_evidence
		verifier="${generic_verifier}"
		;;
	L2)
		verifier="${rockchip_verifier}"
		;;
	*)
		echo "BPI-AIM7 驗證拒絕：契約證據層級只接受 L1 或 L2。" >&2
		exit 2
		;;
esac

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapiaim7"
export CANDIDATE_FAMILY_NAME="Rockchip AIM7"
export VERIFY_TMP_PREFIX="bananapi-rockchip-aim7-verify"
export VERIFY_ARCHIVES=yes
export VERIFICATION_EVIDENCE_LEVEL="${policy_evidence_level}"

[[ -x "${verifier}" ]] || {
	echo "找不到 BPI-AIM7 候選驗證器：${verifier}" >&2
	exit 1
}

exec "${verifier}" "$@"
