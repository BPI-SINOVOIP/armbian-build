#!/usr/bin/env bash

BPI_M2C_GUARD_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BPI_M2C_GUARD_REPO_ROOT="$(cd "${BPI_M2C_GUARD_SCRIPT_DIR}/.." && pwd)"
BPI_M2C_GUARD_CONTRACT="${BPI_M2C_GUARD_REPO_ROOT}/config/validation/bananapi-unisoc-uis7885-m2c-vendor.json"
BPI_M2C_GUARD_SOURCE_VERIFIER="${BPI_M2C_GUARD_SCRIPT_DIR}/verify-bananapi-unisoc-m2c-sources.sh"

bananapi_m2c_require_current_l0_contract() {
	python3 - "${BPI_M2C_GUARD_CONTRACT}" <<'PY'
import json
from pathlib import Path
import sys

contract = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
checks = [
    contract.get("candidate_scope") == "local-source-snapshot-audit",
    contract.get("current_evidence_level") == "L0",
    contract.get("public_release_allowed") is False,
    contract.get("hardware_claims_allowed") is False,
    contract.get("complete_rootfs_image_allowed") is False,
    contract.get("component_build_allowed") is False,
]
if not all(checks):
    raise SystemExit("M2C L0 本機來源快照契約狀態不符合預期")
PY
}

bananapi_m2c_require_supported_baseline() {
	local baseline="$1"

	if [[ "${baseline}" != "sync-20260524-rls-25c" ]]; then
		printf 'M2C L0 契約沒有涵蓋此舊基線，拒絕執行：%s\n' "${baseline}" >&2
		return 1
	fi
}

bananapi_m2c_source_tree_for_baseline() {
	local vendor_root="$1"
	local baseline="$2"

	bananapi_m2c_require_supported_baseline "${baseline}" || return 1
	printf '%s/sync-20260524/source_sync_rls_25c\n' "${vendor_root}"
}

bananapi_m2c_require_local_source_snapshot() {
	local source_tree="$1"
	local report

	bananapi_m2c_require_current_l0_contract || return 1
	report="$(mktemp)"
	if ! "${BPI_M2C_GUARD_SOURCE_VERIFIER}" \
		--source-root "${source_tree}" \
		--contract "${BPI_M2C_GUARD_CONTRACT}" \
		--report "${report}"; then
		printf 'M2C 本機來源快照守門失敗；未分類輸入、不可重放差異或其他阻擋尚未封閉。\n' >&2
		sed -n '1,14p' "${report}" >&2
		printf '請直接執行 %s 取得完整列舉。\n' "${BPI_M2C_GUARD_SOURCE_VERIFIER}" >&2
		rm -f "${report}"
		return 1
	fi
	rm -f "${report}"
}

bananapi_m2c_require_public_release() {
	bananapi_m2c_require_current_l0_contract || return 1
	printf 'M2C 目前只具 L0 本機來源快照稽核，契約禁止公開發布、簽署發布與發布目錄建置。\n' >&2
	return 1
}
