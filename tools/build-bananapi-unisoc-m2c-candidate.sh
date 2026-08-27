#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/.tmp/bananapi-unisoc-m2c-source-candidate}"
MODE="${MODE:-audit}"

usage() {
	cat <<-EOF
	用法：$0 [--source-root PATH] [--output-dir PATH] [--mode audit]

	建立 Banana Pi M2C 的小型 L0 本機來源快照稽核包。此入口只允許
	audit 模式，不會編譯元件、不會建立 rootfs、不會簽署，也不會打包 PAC。
	EOF
}

while (($#)); do
	case "$1" in
		--source-root)
			shift
			SOURCE_ROOT="${1:?缺少來源路徑}"
			;;
		--output-dir)
			shift
			OUTPUT_DIR="${1:?缺少輸出路徑}"
			;;
		--mode)
			shift
			MODE="${1:?缺少模式}"
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			printf '不支援的參數：%s\n' "$1" >&2
			usage >&2
			exit 2
			;;
	esac
	shift
done

if [[ "${MODE}" != "audit" ]]; then
	printf '拒絕模式 %s：授權、簽署鏈與可重放修補集尚未封閉，只允許 audit。\n' "${MODE}" >&2
	exit 2
fi

if [[ -e "${OUTPUT_DIR}" ]]; then
	printf '輸出路徑已存在，為避免覆蓋而拒絕：%s\n' "${OUTPUT_DIR}" >&2
	exit 1
fi

contract="${REPO_ROOT}/config/validation/bananapi-unisoc-uis7885-m2c-vendor.json"
policy="${REPO_ROOT}/docs/evidence/bananapi-family-optimization/E-unisoc-m2c-source-policy-20260827.md"
source_report_tmp="$(mktemp)"
trap 'rm -f "${source_report_tmp}"' EXIT

"${SCRIPT_DIR}/verify-bananapi-unisoc-m2c-sources.sh" \
	--source-root "${SOURCE_ROOT}" \
	--contract "${contract}" \
	--report "${source_report_tmp}"

mkdir -p "${OUTPUT_DIR}"
install -m 0644 "${contract}" "${OUTPUT_DIR}/CONTRACT.json"
install -m 0644 "${policy}" "${OUTPUT_DIR}/SOURCE_POLICY.md"
install -m 0644 "${source_report_tmp}" "${OUTPUT_DIR}/SOURCE_VERIFICATION.txt"

source_commit="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
python3 - "${OUTPUT_DIR}/CANDIDATE_STATUS.json" "${source_commit}" <<'PY'
import json
from pathlib import Path
import sys

status = {
    "schema_version": 1,
    "candidate_id": "bananapi-unisoc-uis7885-m2c-vendor",
    "source_commit": sys.argv[2],
    "candidate_scope": "local-source-snapshot-audit",
    "current_evidence_level": "L0",
    "source_gate": "通過",
    "component_build": "未執行",
    "complete_rootfs_image": "未建立",
    "pac": "未建立",
    "public_release_allowed": False,
    "hardware_claims_allowed": False,
    "status": "L0 本機來源快照稽核包已建立；不得推論為可重放來源、映像或硬體證據",
}
Path(sys.argv[1]).write_text(
    json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY

(
	cd "${OUTPUT_DIR}"
	sha256sum CANDIDATE_STATUS.json CONTRACT.json SOURCE_POLICY.md SOURCE_VERIFICATION.txt >SHA256SUMS
)

printf 'M2C 來源候選證據包：%s\n' "${OUTPUT_DIR}"
printf '目前只允許標示為 L0 本機來源快照稽核。\n'
