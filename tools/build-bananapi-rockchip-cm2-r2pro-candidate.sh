#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
builder="${repo_dir}/tools/build-bananapi-rockchip-candidates.sh"
validation_config="${repo_dir}/config/validation/bananapi-rockchip-rk3568-cm2-r2pro-current.json"
public_release="${PUBLIC_RELEASE:-no}"

fail() {
	echo "BPI-CM2 參考板建置拒絕：$*" >&2
	exit 1
}

case "${public_release}" in
	yes | no) ;;
	*) fail "PUBLIC_RELEASE 只接受 yes 或 no" ;;
esac
[[ "${public_release}" == no ]] ||
	fail "目前只有未驗證載板的 R2 Pro 參考板契約，禁止建立公開發布候選"
python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
if config.get("evidence_level") != "L0":
    raise SystemExit("BPI-CM2 參考板目前只能是 L0")
if not config.get("donor_only_contract"):
    raise SystemExit("BPI-CM2 參考板契約旗標遺失")
if config.get("release_policy", {}).get("public_release_allowed") is not False:
    raise SystemExit("BPI-CM2 參考板公開發布政策不符")
PY

export VALIDATION_CONFIG="${validation_config}"
export OUTPUT_DIR="${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3568-cm2-r2pro-donor-trixie-current-cli"
export BOARDS="bananapicm2"
export MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-35}"

"${builder}" "$@"
