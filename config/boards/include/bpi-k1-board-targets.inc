#!/usr/bin/env bash
# 四個官方媒體候選板型共用缺省；不改動一般 Armbian 板型。

function bpi_k1_board_target_fixed_default() {
	local name="$1" expected="$2"
	# CLI 空值也屬於明確輸入，不能改成互動提問或悄悄改回固定值。
	if [[ -v "ARMBIAN_PARSED_CMDLINE_PARAMS[${name}]" ]] && [[ "${ARMBIAN_PARSED_CMDLINE_PARAMS[${name}]}" != "${expected}" ]]; then
		exit_with_error "官方媒體板型的命令列參數衝突：${name} 必須為 ${expected}" "${BOARD}"
		return 1
	fi
	if [[ -n "${!name}" && "${!name}" != "${expected}" ]]; then
		exit_with_error "官方媒體板型的參數衝突：${name} 必須為 ${expected}" "${BOARD}"
		return 1
	fi
	declare -g "${name}=${expected}"
}

function bpi_k1_board_target_defaults() {
	local values base_board branch storage camera release_id
	# 此時尚未安裝完整主機相依套件，只使用 Python 標準函式庫。
	values="$(python3 - "${SRC}" "${BOARD}" <<'PY'
import re
import sys

sys.path.insert(0, sys.argv[1] + "/tools")
from bpi_k1_board_targets import load_registry, target_for

document = load_registry()
target = target_for(sys.argv[2])
if target is None:
    raise SystemExit("官方媒體板型定義不存在")
release_id = document.get("release_id", "")
if not isinstance(release_id, str) or re.fullmatch(r"[0-9]{8}-rc[1-9][0-9]*", release_id) is None:
    raise SystemExit("官方媒體候選識別格式不符")
values = (target["base_board"], target["branch"], target["storage"], target["camera_profile"], release_id)
if any(not isinstance(value, str) or not value or any(char.isspace() for char in value) for value in values):
    raise SystemExit("官方媒體板型欄位不得為空或包含空白")
print("\t".join(values))
PY
)" || {
		exit_with_error "無法讀取官方媒體板型定義" "${BOARD}"
		return 1
	}
	IFS=$'\t' read -r base_board branch storage camera release_id <<< "${values}"
	bpi_k1_board_target_fixed_default KERNEL_CONFIGURE no
	bpi_k1_board_target_fixed_default RELEASE noble
	bpi_k1_board_target_fixed_default BRANCH "${branch}"
	bpi_k1_board_target_fixed_default BUILD_DESKTOP yes
	bpi_k1_board_target_fixed_default BUILD_MINIMAL no
	bpi_k1_board_target_fixed_default DESKTOP_ENVIRONMENT gnome
	bpi_k1_board_target_fixed_default DESKTOP_TIER minimal
	bpi_k1_board_target_fixed_default BPI_K1_OUTPUTS "${storage}"
	bpi_k1_board_target_fixed_default BPI_CM6_CAMERA_PROFILE "${camera}"
	if [[ -n "${DESKTOP_APPGROUPS_SELECTED:-}" && "${DESKTOP_APPGROUPS_SELECTED}" != "none" ]]; then
		exit_with_error "官方媒體板型不接受額外桌面套件群組" "${BOARD}"
		return 1
	fi
	if [[ -n "${CARD_DEVICE:-}" ]]; then
		exit_with_error "官方媒體建置不接受 CARD_DEVICE；燒錄須另行執行" "${BOARD}"
		return 1
	fi
	if [[ -v 'ARMBIAN_PARSED_CMDLINE_PARAMS[BPI_K1_RELEASE_ID]' && -z "${BPI_K1_RELEASE_ID}" ]]; then
		exit_with_error "官方媒體候選識別不得為空" "${BOARD}"
		return 1
	fi
	declare -g BPI_K1_RELEASE_ID="${BPI_K1_RELEASE_ID:-${release_id}}"
	declare -g BPI_K1_BOARD_TARGET="${BOARD}"
}
