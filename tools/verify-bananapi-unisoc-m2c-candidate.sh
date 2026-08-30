#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c}"
CANDIDATE_DIR="${CANDIDATE_DIR:-${REPO_ROOT}/.tmp/bananapi-unisoc-m2c-source-candidate}"

usage() {
	cat <<-EOF
	用法：$0 [--source-root PATH] [--candidate-dir PATH]

	唯讀驗證 M2C 來源候選證據包及其對應的本機 Unisoc 來源狀態。
	此守門通過只代表 L0 本機來源快照稽核包的固定檔案符合契約。
	EOF
}

while (($#)); do
	case "$1" in
		--source-root)
			shift
			SOURCE_ROOT="${1:?缺少來源路徑}"
			;;
		--candidate-dir)
			shift
			CANDIDATE_DIR="${1:?缺少候選路徑}"
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

[[ -d "${CANDIDATE_DIR}" ]] || {
	printf '找不到候選目錄：%s\n' "${CANDIDATE_DIR}" >&2
	exit 1
}

for file in CANDIDATE_STATUS.json CONTRACT.json SOURCE_POLICY.md SOURCE_VERIFICATION.txt SHA256SUMS; do
	[[ -f "${CANDIDATE_DIR}/${file}" && ! -L "${CANDIDATE_DIR}/${file}" ]] || {
		printf '候選缺少檔案：%s\n' "${file}" >&2
		exit 1
	}
done

mapfile -d '' candidate_entries < <(find "${CANDIDATE_DIR}" -mindepth 1 -maxdepth 1 -printf '%f\0' | sort -z)
expected_entries=(
	"CANDIDATE_STATUS.json"
	"CONTRACT.json"
	"SHA256SUMS"
	"SOURCE_POLICY.md"
	"SOURCE_VERIFICATION.txt"
)
if ((${#candidate_entries[@]} != ${#expected_entries[@]})); then
	printf '候選目錄含有未允許的額外項目。\n' >&2
	exit 1
fi
for index in "${!expected_entries[@]}"; do
	if [[ "${candidate_entries[index]}" != "${expected_entries[index]}" ]]; then
		printf '候選目錄項目不符合精確白名單：%s\n' "${candidate_entries[index]}" >&2
		exit 1
	fi
done

cmp --silent \
	"${REPO_ROOT}/config/validation/bananapi-unisoc-uis7885-m2c-vendor.json" \
	"${CANDIDATE_DIR}/CONTRACT.json" || {
	printf '候選契約與目前工作樹不同。\n' >&2
	exit 1
}

cmp --silent \
	"${REPO_ROOT}/docs/evidence/bananapi-family-optimization/E-unisoc-m2c-source-policy-20260827.md" \
	"${CANDIDATE_DIR}/SOURCE_POLICY.md" || {
	printf '候選來源政策與目前工作樹不同。\n' >&2
	exit 1
}

python3 - "${CANDIDATE_DIR}/SHA256SUMS" <<'PY'
from pathlib import Path
import sys

expected = {
    "CANDIDATE_STATUS.json",
    "CONTRACT.json",
    "SOURCE_POLICY.md",
    "SOURCE_VERIFICATION.txt",
}
lines = Path(sys.argv[1]).read_text(encoding="ascii").splitlines()
actual = set()
for line in lines:
    fields = line.split()
    if len(fields) != 2 or len(fields[0]) != 64:
        raise SystemExit("SHA256SUMS 格式不符")
    actual.add(fields[1].lstrip("*"))
if len(lines) != len(expected) or actual != expected:
    raise SystemExit("SHA256SUMS 檔案集合不符")
PY

(
	cd "${CANDIDATE_DIR}"
	sha256sum -c SHA256SUMS
)

if find "${CANDIDATE_DIR}" -type f \( -name '*.img' -o -name '*.pac' -o -name '*.xz' -o -name '*rootfs*' \) -print -quit | grep -q .; then
	printf '候選內含禁止的映像、PAC、壓縮映像或 rootfs 產物。\n' >&2
	exit 1
fi

tmp_report="$(mktemp)"
trap 'rm -f "${tmp_report}"' EXIT
"${SCRIPT_DIR}/verify-bananapi-unisoc-m2c-sources.sh" \
	--source-root "${SOURCE_ROOT}" \
	--contract "${CANDIDATE_DIR}/CONTRACT.json" \
	--report "${tmp_report}"
cmp --silent "${tmp_report}" "${CANDIDATE_DIR}/SOURCE_VERIFICATION.txt" || {
	printf '來源狀態已與候選建立時不同。\n' >&2
	exit 1
}

current_commit="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
python3 - \
	"${CANDIDATE_DIR}/CANDIDATE_STATUS.json" \
	"${CANDIDATE_DIR}/CONTRACT.json" \
	"${current_commit}" <<'PY'
import json
from pathlib import Path
import sys

status = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
contract = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
checks = [
    status["current_evidence_level"] == "L0",
    status["candidate_scope"] == "local-source-snapshot-audit",
    status["source_commit"] == sys.argv[3],
    status["public_release_allowed"] is False,
    status["hardware_claims_allowed"] is False,
    status["complete_rootfs_image"] == "未建立",
    status["pac"] == "未建立",
    contract["current_evidence_level"] == "L0",
    contract["candidate_scope"] == "local-source-snapshot-audit",
    contract["public_release_allowed"] is False,
    contract["hardware_claims_allowed"] is False,
    contract["complete_rootfs_image_allowed"] is False,
    contract["component_build_allowed"] is False,
]
if not all(checks):
    raise SystemExit("候選狀態或發布限制不符合契約")
PY

printf 'M2C L0 本機來源快照稽核包守門通過。\n'
