#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
TARGET_ROOT="${TARGET_ROOT:-/media/pi/SMCI/bpi/unisoc/release/bpi-m2c}"
DATE_TAG="${DATE_TAG:-$(date +%Y%m%d)}"
TARGET_DIR="${TARGET_DIR:-${TARGET_ROOT}/${DATE_TAG}}"
LINK_MODE="${LINK_MODE:-hardlink}"
BOARD_ID="${BOARD_ID:-bpi-m2c}"
MACHINE="${MACHINE:-uis7885-2h10}"
SIGN_PROFILE="${SIGN_PROFILE:-QOGIRN6PRO_UIS7885_2H10_SEC}"
PAC_NAME="${PAC_NAME:-}"
BASELINES="${BASELINES:-sync-20260524-rls-25c}"

requested_baselines=()

usage() {
	cat <<-EOF
	Usage: $0 [options]

	Stage BPI-M2C UNISOC vendor PAC/core signed artifacts.

	Options:
	  --baseline NAME       Stage one baseline; may be repeated
	  --date-tag NAME       Default: ${DATE_TAG}
	  --target-dir PATH     Default: ${TARGET_DIR}
	  --link-mode MODE      hardlink or copy, default: ${LINK_MODE}
	  -h, --help            Show this help

	Default baseline:
	  sync-20260524-rls-25c

	Supported baselines:
	  sync-20260524-rls-25c
	  rls-25c-w26-05-5
	  rls-25c-w26-07-2
	  trunk-3-0-dev-w24-05-2-p1-2

	Environment:
	  SOURCE_ROOT, TARGET_ROOT, DATE_TAG, TARGET_DIR, LINK_MODE,
	  BOARD_ID, MACHINE, SIGN_PROFILE, PAC_NAME, BASELINES
	EOF
}

while (($#)); do
	case "$1" in
		--baseline)
			shift
			requested_baselines+=("${1:?missing baseline}")
			;;
		--date-tag)
			shift
			DATE_TAG="${1:?missing date tag}"
			TARGET_DIR="${TARGET_ROOT}/${DATE_TAG}"
			;;
		--target-dir)
			shift
			TARGET_DIR="${1:?missing target dir}"
			;;
		--link-mode)
			shift
			LINK_MODE="${1:?missing link mode}"
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			printf 'Unknown argument: %s\n\n' "$1" >&2
			usage >&2
			exit 2
			;;
	esac
	shift
done

if ((${#requested_baselines[@]} == 0)); then
	read -r -a baselines <<< "${BASELINES}"
else
	baselines=("${requested_baselines[@]}")
fi

tree_for_baseline() {
	local baseline="$1"

	case "${baseline}" in
		sync-20260524-rls-25c)
			printf '%s/sync-20260524/source_sync_rls_25c\n' "${SOURCE_ROOT}"
			;;
		rls-25c-w26-05-5)
			printf '%s/source_rls_25c_w26_05_5\n' "${SOURCE_ROOT}"
			;;
		rls-25c-w26-07-2)
			printf '%s/source_rls_25c_w26_07_2\n' "${SOURCE_ROOT}"
			;;
		trunk-3-0-dev-w24-05-2-p1-2)
			printf '%s/source_trunk_3_0_dev_w24_05_2_p1_2\n' "${SOURCE_ROOT}"
			;;
		*)
			printf 'unknown baseline: %s\n' "${baseline}" >&2
			return 2
			;;
	esac
}

pac_name_for_baseline() {
	local baseline="$1"

	if [[ -n "${PAC_NAME}" ]]; then
		printf '%s\n' "${PAC_NAME}"
		return
	fi

	case "${baseline}" in
		sync-20260524-rls-25c)
			printf '%s\n' "uis7885_2h10+wayland+wayland+sec+uboot22-userdebug-native_${SIGN_PROFILE}.pac"
			;;
		*)
			printf '%s\n' "uis7885_2h10+wayland+wayland+sec-userdebug-native_${SIGN_PROFILE}.pac"
			;;
	esac
}

artifact_list() {
	local baseline="$1"
	local pac_name

	pac_name="$(pac_name_for_baseline "${baseline}")"

	printf '%s\n' \
		"cp_sign/${SIGN_PROFILE}/${pac_name}" \
		"boot-sign.img" \
		"dtbo-sign.img" \
		"Image-dtb-sign.dtb" \
		"prodnv.img" \
		"recovery.img" \
		"recovery-rootfs.ext4" \
		"rootfs.ext4" \
		"sml-sign.bin" \
		"teecfg-sign.bin" \
		"tos-sign.bin" \
		"u-boot-sign.bin" \
		"u-boot-spl-16k-emmc-sign.bin" \
		"u-boot-spl-16k-ufs-sign.bin" \
		"userdata.img"
}

stage_file() {
	local src="$1"
	local dst="$2"

	mkdir -p "$(dirname "${dst}")"
	rm -f "${dst}"

	case "${LINK_MODE}" in
		hardlink)
			ln "${src}" "${dst}" 2>/dev/null || cp -a "${src}" "${dst}"
			;;
		copy)
			cp -a "${src}" "${dst}"
			;;
		*)
			printf 'unknown LINK_MODE: %s\n' "${LINK_MODE}" >&2
			return 2
			;;
	esac
}

write_baseline_info() {
	local baseline="$1"
	local tree="$2"
	local dst_dir="$3"
	local info="${dst_dir}/build-info.txt"

	{
		printf 'board_id=%s\n' "${BOARD_ID}"
		printf 'baseline=%s\n' "${baseline}"
		printf 'machine=%s\n' "${MACHINE}"
		printf 'sign_profile=%s\n' "${SIGN_PROFILE}"
		printf 'source_tree=%s\n' "${tree}"
		printf 'staged_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		if [[ -d "${tree}/.repo" ]]; then
			printf 'repo_manifest_available=yes\n'
		else
			printf 'repo_manifest_available=no\n'
		fi
	} > "${info}"
}

stage_baseline() {
	local baseline="$1"
	local tree product_dir dst_dir rel src dst manifest
	local missing=0 staged=0

	tree="$(tree_for_baseline "${baseline}")"
	product_dir="${tree}/out/target/product/${MACHINE}"
	dst_dir="${TARGET_DIR}/${baseline}"
	manifest="${TARGET_DIR}/manifest.tsv"

	if [[ ! -d "${product_dir}" ]]; then
		printf '%s\t%s\tmissing-product-dir\t%s\n' "${BOARD_ID}" "${baseline}" "${product_dir}" >> "${TARGET_DIR}/missing.tsv"
		return 1
	fi

	mkdir -p "${dst_dir}"
	write_baseline_info "${baseline}" "${tree}" "${dst_dir}"

	while IFS= read -r rel; do
		src="${product_dir}/${rel}"
		dst="${dst_dir}/${rel}"
		if [[ ! -f "${src}" ]]; then
			printf '%s\t%s\tmissing-artifact\t%s\n' "${BOARD_ID}" "${baseline}" "${src}" >> "${TARGET_DIR}/missing.tsv"
			((missing += 1))
			continue
		fi

		stage_file "${src}" "${dst}"
		printf '%s\t%s\t%s\t%s\t%s\n' "${BOARD_ID}" "${baseline}" "${rel}" "$(stat -c %s "${dst}")" "${dst}" >> "${manifest}"
		((staged += 1))
	done < <(artifact_list "${baseline}")

	(
		cd "${dst_dir}"
		find . -type f ! -name 'SHA256SUMS' -print0 |
			sort -z |
			xargs -0 sha256sum > SHA256SUMS
	)

	printf '%s\t%s\tstaged\t%s\n' "${BOARD_ID}" "${baseline}" "${staged}" >> "${TARGET_DIR}/summary.tsv"
	printf '%s\t%s\tmissing\t%s\n' "${BOARD_ID}" "${baseline}" "${missing}" >> "${TARGET_DIR}/summary.tsv"
	((missing == 0))
}

main() {
	local baseline failed=0

	mkdir -p "${TARGET_DIR}"
	printf 'board\tbaseline\tartifact\tbytes\tstaged_path\n' > "${TARGET_DIR}/manifest.tsv"
	printf 'board\tbaseline\tstatus\tvalue\n' > "${TARGET_DIR}/summary.tsv"
	printf 'board\tbaseline\treason\tpath\n' > "${TARGET_DIR}/missing.tsv"

	for baseline in "${baselines[@]}"; do
		if ! stage_baseline "${baseline}"; then
			((failed += 1))
		fi
	done

	printf 'target: %s\n' "${TARGET_DIR}"
	printf 'baselines: %s\n' "${#baselines[@]}"
	printf 'failed baselines: %s\n' "${failed}"
	printf 'manifest: %s\n' "${TARGET_DIR}/manifest.tsv"
	printf 'summary: %s\n' "${TARGET_DIR}/summary.tsv"
	printf 'missing: %s\n' "${TARGET_DIR}/missing.tsv"

	((failed == 0))
}

main "$@"
