#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier="${ROCKCHIP_CANDIDATE_VERIFIER:-${repo_dir}/tools/verify-bananapi-rockchip-candidates.sh}"
validation_config="${repo_dir}/config/validation/bananapi-rockchip-rk3568-cm2-r2pro-current.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3568-cm2-r2pro-donor-trixie-current-cli}"
public_release="${PUBLIC_RELEASE:-no}"

fail() {
	echo "BPI-CM2 參考板驗證拒絕：$*" >&2
	exit 1
}

case "${public_release}" in
	yes | no) ;;
	*) fail "PUBLIC_RELEASE 只接受 yes 或 no" ;;
esac
[[ "${public_release}" == no ]] ||
	fail "目前只有未驗證載板的 R2 Pro 參考板契約，禁止建立公開發布候選"

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${output_dir}"
export BOARDS="bananapicm2"
export VERIFICATION_EVIDENCE_LEVEL="L1"

"${verifier}" "$@"

python3 - "${output_dir}/VERIFICATION.tsv" \
	"${output_dir}/VERIFICATION_STATUS.json" <<'PY'
import json
import os
from pathlib import Path
import sys

verification_path = Path(sys.argv[1])
status_path = Path(sys.argv[2])

rows = [
    line.split("\t")
    for line in verification_path.read_text(encoding="utf-8").splitlines()
]
if not rows or rows[0] != [
    "board",
    "identity",
    "read_only_content",
    "evidence_level",
]:
    raise SystemExit("BPI-CM2 參考板驗證表格式不符")
for row in rows[1:]:
    if len(row) != 4:
        raise SystemExit("BPI-CM2 參考板驗證表欄位數不符")
    row[3] = "L1"
verification_temporary = verification_path.with_suffix(
    verification_path.suffix + ".partial"
)
verification_temporary.write_text(
    "\n".join("\t".join(row) for row in rows) + "\n",
    encoding="utf-8",
)
os.replace(verification_temporary, verification_path)

with status_path.open(encoding="utf-8") as stream:
    status = json.load(stream)
status.update(
    {
        "evidence_level": "L1",
        "candidate_scope": "internal-l1-donor-only",
        "donor_only_contract": True,
        "carrier_verified": False,
        "generic_cm2_supported": False,
        "public_release_allowed": False,
        "public_redistribution_authorized": False,
        "hardware_evidence_present": False,
    }
)
status_temporary = status_path.with_suffix(status_path.suffix + ".partial")
with status_temporary.open("w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(status_temporary, status_path)
PY

echo "BPI-CM2 的 R2 Pro 參考板映像通過內部 L1 守門；此結果不是 CM2 硬體支援證據。"
