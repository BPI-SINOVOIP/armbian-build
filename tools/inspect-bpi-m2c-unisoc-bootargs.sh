#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
SDROOT_ROOT="${SDROOT_ROOT:-${SOURCE_ROOT}/sdrootfs/bpi-m2c}"
BASELINE="${BASELINE:-sync-20260524-rls-25c}"
MACHINE="${MACHINE:-uis7885-2h10}"
PRODUCT_DIR="${PRODUCT_DIR:-}"
SDROOT_WORKDIR="${SDROOT_WORKDIR:-}"
OUTPUT="${OUTPUT:-}"

usage() {
	cat <<-EOF
	Usage: $0 [options]

	Inspect BPI-M2C UNISOC boot images, DTB/DTBO bootargs, and the latest
	SD-rootfs test artifact. This is read-only unless --output is provided.

	Options:
	  --baseline NAME          Default: ${BASELINE}
	  --product-dir PATH       Vendor or hybrid product directory
	  --sdroot-workdir PATH    SD-rootfs work directory containing build-info.txt
	  --output PATH            Write the report to PATH
	  -h, --help               Show this help
	EOF
}

while (($#)); do
	case "$1" in
		--baseline)
			shift
			BASELINE="${1:?missing baseline}"
			;;
		--product-dir)
			shift
			PRODUCT_DIR="${1:?missing product dir}"
			;;
		--sdroot-workdir)
			shift
			SDROOT_WORKDIR="${1:?missing sdroot workdir}"
			;;
		--output)
			shift
			OUTPUT="${1:?missing output path}"
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

require_cmd() {
	local cmd="$1"
	command -v "${cmd}" >/dev/null 2>&1 || {
		printf 'missing required command: %s\n' "${cmd}" >&2
		exit 1
	}
}

tree_for_baseline() {
	case "$1" in
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
			printf 'unknown baseline: %s\n' "$1" >&2
			return 2
			;;
	esac
}

find_latest_sdroot_workdir() {
	find "${SDROOT_ROOT}" -type f -name build-info.txt -printf '%T@ %h\n' 2>/dev/null |
		sort -nr |
		awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
}

read_info() {
	local file="$1"
	local key="$2"

	awk -v key="${key}" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "${file}"
}

fdt_prop() {
	local image="$1"
	local node="$2"
	local prop="$3"

	fdtget -t s "${image}" "${node}" "${prop}" 2>/dev/null || true
}

extract_token() {
	local args="$1"
	local prefix="$2"

	printf '%s\n' ${args} | awk -v prefix="${prefix}" 'index($0, prefix) == 1 { print; exit }'
}

report() {
	local tree product_dir sdroot_workdir build_info root_uuid suggested_root boot_img boot_sign dtb dtbo dtbo_sign
	local base_bootargs dtbo_bootargs dtbo_bootargs_ext base_root dtbo_root

	tree="$(tree_for_baseline "${BASELINE}")"
	product_dir="${PRODUCT_DIR:-${tree}/out/target/product/${MACHINE}}"
	sdroot_workdir="${SDROOT_WORKDIR:-$(find_latest_sdroot_workdir)}"
	build_info="${sdroot_workdir}/build-info.txt"

	boot_img="${product_dir}/boot.img"
	boot_sign="${product_dir}/boot-sign.img"
	dtb="${product_dir}/Image-dtb.dtb"
	dtbo="${product_dir}/dtbo.img"
	dtbo_sign="${product_dir}/dtbo-sign.img"

	root_uuid=""
	suggested_root=""
	if [[ -f "${build_info}" ]]; then
		root_uuid="$(read_info "${build_info}" rootfs_uuid)"
		suggested_root="$(read_info "${build_info}" suggested_kernel_root)"
	fi

	base_bootargs="$(fdt_prop "${dtb}" /chosen bootargs)"
	dtbo_bootargs="$(fdt_prop "${dtbo}" /fragment@1/__overlay__ bootargs)"
	dtbo_bootargs_ext="$(fdt_prop "${dtbo}" /fragment@1/__overlay__ bootargs_ext)"
	base_root="$(extract_token "${base_bootargs}" root=)"
	dtbo_root="$(extract_token "${dtbo_bootargs}" root=)"

	cat <<-EOF
	BPI-M2C UNISOC bootargs inspection
	==================================

	baseline: ${BASELINE}
	source_tree: ${tree}
	product_dir: ${product_dir}
	sdroot_workdir: ${sdroot_workdir}

	Images:
	  boot.img: ${boot_img}
	    $(file -b "${boot_img}" 2>/dev/null || printf 'missing')
	  boot-sign.img: ${boot_sign}
	    $(file -b "${boot_sign}" 2>/dev/null || printf 'missing')
	  Image-dtb.dtb: ${dtb}
	    $(file -b "${dtb}" 2>/dev/null || printf 'missing')
	  dtbo.img: ${dtbo}
	    $(file -b "${dtbo}" 2>/dev/null || printf 'missing')
	  dtbo-sign.img: ${dtbo_sign}
	    $(file -b "${dtbo_sign}" 2>/dev/null || printf 'missing')

	Base DTB /chosen/bootargs:
	  ${base_bootargs}

	Base DTB root token:
	  ${base_root:-not found}

	DTBO /fragment@1/__overlay__/bootargs:
	  ${dtbo_bootargs}

	DTBO /fragment@1/__overlay__/bootargs_ext:
	  ${dtbo_bootargs_ext}

	DTBO root token:
	  ${dtbo_root:-not found}

	SD rootfs UUID:
	  ${root_uuid:-not found}

	Suggested SD-rootfs kernel root:
	  ${suggested_root:-not found}

	Conclusion:
	  boot.img is a raw ARM64 Linux Image, not an Android boot image. For the
	  current UIS7885 product, the effective rootfs setting is carried by the
	  signed DTBO overlay bootargs, currently ${dtbo_root:-unknown}. To boot from
	  the SD rootfs, generate a PAC with dtbo.img bootargs changed to the SD
	  rootfs UUID and then re-signed as dtbo-sign.img.
	EOF
}

main() {
	require_cmd awk
	require_cmd fdtget
	require_cmd file
	require_cmd find
	require_cmd sort

	if [[ -n "${OUTPUT}" ]]; then
		mkdir -p "$(dirname "${OUTPUT}")"
		report | tee "${OUTPUT}"
	else
		report
	fi
}

main "$@"
