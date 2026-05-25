#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
TARGET_ROOT="${TARGET_ROOT:-${SOURCE_ROOT}/sdrootfs-pac/bpi-m2c}"
SDROOT_ROOT="${SDROOT_ROOT:-${SOURCE_ROOT}/sdrootfs/bpi-m2c}"
DATE_TAG="${DATE_TAG:-$(date +%Y%m%d)}"
BASELINE="${BASELINE:-sync-20260524-rls-25c}"
MACHINE="${MACHINE:-uis7885-2h10}"
SIGN_PROFILE="${SIGN_PROFILE:-QOGIRN6PRO_UIS7885_2H10_SEC}"
BASE_PRODUCT_DIR="${BASE_PRODUCT_DIR:-}"
SDROOT_WORKDIR="${SDROOT_WORKDIR:-}"
ROOT_UUID="${ROOT_UUID:-}"
ROOTFS_LABEL="${ROOTFS_LABEL:-sdrootfs}"
FORCE="${FORCE:-no}"
RUN_MKPAC="${RUN_MKPAC:-yes}"

usage() {
	cat <<-EOF
	Usage: $0 [options]

	Create a BPI-M2C PAC for testing eMMC/UFS signed boot plus SD-mounted
	Armbian rootfs. The script copies a vendor/hybrid product directory, changes
	dtbo.img bootargs to root=UUID=<SD rootfs>, re-signs dtbo-sign.img with the
	local UNISOC signing tool, and repacks PAC.

	Options:
	  --baseline NAME          Default: ${BASELINE}
	  --base-product-dir PATH  Product directory to copy; default: latest hybrid product
	  --sdroot-workdir PATH    SD-rootfs work directory containing build-info.txt
	  --root-uuid UUID         Override SD rootfs UUID
	  --target-root PATH       Default: ${TARGET_ROOT}
	  --no-mkpac               Prepare product directory but do not run makepac
	  --force                  Remove existing output work directory
	  -h, --help               Show this help
	EOF
}

while (($#)); do
	case "$1" in
		--baseline)
			shift
			BASELINE="${1:?missing baseline}"
			;;
		--base-product-dir)
			shift
			BASE_PRODUCT_DIR="${1:?missing base product dir}"
			;;
		--sdroot-workdir)
			shift
			SDROOT_WORKDIR="${1:?missing sdroot workdir}"
			;;
		--root-uuid)
			shift
			ROOT_UUID="${1:?missing root uuid}"
			;;
		--target-root)
			shift
			TARGET_ROOT="${1:?missing target root}"
			;;
		--no-mkpac)
			RUN_MKPAC="no"
			;;
		--force)
			FORCE="yes"
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

sanitize() {
	printf '%s' "$1" | tr -c 'A-Za-z0-9._+-' '-'
}

read_info() {
	local file="$1"
	local key="$2"

	awk -v key="${key}" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "${file}"
}

find_latest_sdroot_workdir() {
	find "${SDROOT_ROOT}" -type f -name build-info.txt -printf '%T@ %h\n' 2>/dev/null |
		sort -nr |
		awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
}

find_latest_hybrid_product() {
	find "${SOURCE_ROOT}/hybrid/bpi-m2c" -type f -name build-info.txt -printf '%T@ %h\n' 2>/dev/null |
		sort -nr |
		awk 'NR == 1 { $1=""; sub(/^ /, ""); print $0 "/product" }'
}

rewrite_bootargs_for_uuid() {
	local current="$1"
	local uuid="$2"
	local token root_seen="no" rootfstype_seen="no" rw_seen="no" rootwait_seen="no"
	local out=()

	for token in ${current}; do
		case "${token}" in
			root=*)
				out+=("root=UUID=${uuid}")
				root_seen="yes"
				;;
			rootfstype=*)
				out+=("rootfstype=ext4")
				rootfstype_seen="yes"
				;;
			ro)
				out+=("rw")
				rw_seen="yes"
				;;
			rw)
				out+=("${token}")
				rw_seen="yes"
				;;
			rootwait)
				out+=("${token}")
				rootwait_seen="yes"
				;;
			*)
				out+=("${token}")
				;;
		esac
	done

	[[ "${root_seen}" == "yes" ]] || out+=("root=UUID=${uuid}")
	[[ "${rootfstype_seen}" == "yes" ]] || out+=("rootfstype=ext4")
	[[ "${rw_seen}" == "yes" ]] || out+=("rw")
	[[ "${rootwait_seen}" == "yes" ]] || out+=("rootwait")

	printf '%s\n' "${out[*]}"
}

patch_pac_ini() {
	local pac_ini="$1"
	local tree="$2"
	local base_product_dir="$3"
	local product_dir="$4"
	local pac_name="$5"

	BASE_PRODUCT_DIR="${base_product_dir}" PRODUCT_DIR="${product_dir}" TREE="${tree}" MACHINE="${MACHINE}" PAC_NAME="${pac_name}" \
		perl -0pi -e '
			s#\Q$ENV{BASE_PRODUCT_DIR}\E#$ENV{PRODUCT_DIR}#g;
			my $m = quotemeta($ENV{MACHINE});
			s#\./out/target/product/$m#$ENV{PRODUCT_DIR}#g;
			s#\./prebuilts#$ENV{TREE}/prebuilts#g;
			s#WORK_DIR\s*=.*#WORK_DIR = $ENV{TREE}/#g;
			s#PAC_NAME\s*=.*#PAC_NAME = $ENV{PAC_NAME}#g;
		' "${pac_ini}"
}

write_build_info() {
	local info="$1"
	local tree="$2"
	local base_product_dir="$3"
	local product_dir="$4"
	local sdroot_workdir="$5"
	local uuid="$6"
	local old_bootargs="$7"
	local new_bootargs="$8"
	local pac_name="$9"

	{
		printf 'board_id=bananapim2c\n'
		printf 'image_type=sd-rootfs-pac\n'
		printf 'baseline=%s\n' "${BASELINE}"
		printf 'machine=%s\n' "${MACHINE}"
		printf 'sign_profile=%s\n' "${SIGN_PROFILE}"
		printf 'source_tree=%s\n' "${tree}"
		printf 'base_product_dir=%s\n' "${base_product_dir}"
		printf 'product_dir=%s\n' "${product_dir}"
		printf 'sdroot_workdir=%s\n' "${sdroot_workdir}"
		printf 'rootfs_uuid=%s\n' "${uuid}"
		printf 'old_dtbo_bootargs=%s\n' "${old_bootargs}"
		printf 'new_dtbo_bootargs=%s\n' "${new_bootargs}"
		printf 'pac_name=%s\n' "${pac_name}"
		printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	} > "${info}"
}

main() {
	require_cmd awk
	require_cmd fdtget
	require_cmd fdtput
	require_cmd find
	require_cmd perl
	require_cmd python
	require_cmd sha256sum
	require_cmd sort
	require_cmd tee

	local tree sdroot_workdir build_info uuid base_product_dir work_dir product_dir pac_ini pac_name
	local dtbo old_bootargs new_bootargs sign_bin sign_config sign_log mkpac_log actual_pac label
	local mkpac_status

	tree="$(tree_for_baseline "${BASELINE}")"
	sdroot_workdir="${SDROOT_WORKDIR:-$(find_latest_sdroot_workdir)}"
	if [[ -z "${sdroot_workdir}" || ! -f "${sdroot_workdir}/build-info.txt" ]]; then
		printf 'missing SD-rootfs build-info.txt: %s\n' "${sdroot_workdir:-<empty>}" >&2
		exit 1
	fi

	build_info="${sdroot_workdir}/build-info.txt"
	uuid="${ROOT_UUID:-$(read_info "${build_info}" rootfs_uuid)}"
	if [[ -z "${uuid}" ]]; then
		printf 'missing rootfs UUID; provide --root-uuid\n' >&2
		exit 1
	fi

	base_product_dir="${BASE_PRODUCT_DIR:-$(find_latest_hybrid_product)}"
	if [[ -z "${base_product_dir}" ]]; then
		base_product_dir="${tree}/out/target/product/${MACHINE}"
	fi
	if [[ ! -d "${base_product_dir}" ]]; then
		printf 'missing base product dir: %s\n' "${base_product_dir}" >&2
		exit 1
	fi

	label="$(sanitize "${BASELINE}-${ROOTFS_LABEL}-${uuid}")"
	work_dir="${TARGET_ROOT}/${DATE_TAG}/${label}"
	product_dir="${work_dir}/product"
	pac_name="bpi-m2c_${label}_${SIGN_PROFILE}.pac"

	if [[ -e "${work_dir}" ]]; then
		if [[ "${FORCE}" != "yes" ]]; then
			printf 'output already exists: %s (use --force)\n' "${work_dir}" >&2
			exit 1
		fi
		rm -rf "${work_dir}"
	fi

	mkdir -p "${work_dir}"
	printf 'copy product output: %s -> %s\n' "${base_product_dir}" "${product_dir}"
	cp -a --reflink=auto "${base_product_dir}" "${product_dir}"

	dtbo="${product_dir}/dtbo.img"
	if [[ ! -f "${dtbo}" ]]; then
		printf 'missing dtbo.img: %s\n' "${dtbo}" >&2
		exit 1
	fi

	old_bootargs="$(fdtget -t s "${dtbo}" /fragment@1/__overlay__ bootargs)"
	new_bootargs="$(rewrite_bootargs_for_uuid "${old_bootargs}" "${uuid}")"
	printf 'old dtbo bootargs: %s\n' "${old_bootargs}"
	printf 'new dtbo bootargs: %s\n' "${new_bootargs}"
	fdtput -t s "${dtbo}" /fragment@1/__overlay__ bootargs "${new_bootargs}"

	rm -f "${product_dir}/dtbo-sign.img"
	sign_bin="${tree}/build-unisoc-wayland/tmp-unisoc_wayland-glibc/sysroots-components/x86_64/unisoc-sign-native/usr/bin/sprd_sign"
	sign_config="${tree}/build-unisoc-wayland/tmp-unisoc_wayland-glibc/sysroots-components/x86_64/unisoc-sign-native/usr/bin/config/"
	sign_log="${work_dir}/sign-dtbo.log"
	if [[ ! -x "${sign_bin}" || ! -d "${sign_config}" ]]; then
		printf 'missing signing tool or config: %s %s\n' "${sign_bin}" "${sign_config}" >&2
		exit 1
	fi
	"${sign_bin}" sign_image --image "${dtbo}" --config_dir "${sign_config}" --algorithm rsa4096 --rsa_padding 6 --wcn_gnss_flag true 2>&1 | tee "${sign_log}"
	grep -q 'add_content_certificate() success' "${sign_log}" || {
		printf 'DTBO signing did not report success; see %s\n' "${sign_log}" >&2
		exit 1
	}
	[[ -f "${product_dir}/dtbo-sign.img" ]] || {
		printf 'missing signed dtbo: %s\n' "${product_dir}/dtbo-sign.img" >&2
		exit 1
	}

	pac_ini="${product_dir}/cp_sign/${SIGN_PROFILE}/pac.ini"
	if [[ ! -f "${pac_ini}" ]]; then
		printf 'missing pac.ini: %s\n' "${pac_ini}" >&2
		exit 1
	fi
	patch_pac_ini "${pac_ini}" "${tree}" "${base_product_dir}" "${product_dir}" "${pac_name}"
	if [[ -f "${pac_ini}.bak" ]]; then
		patch_pac_ini "${pac_ini}.bak" "${tree}" "${base_product_dir}" "${product_dir}" "${pac_name}"
	fi

	write_build_info "${work_dir}/build-info.txt" "${tree}" "${base_product_dir}" "${product_dir}" "${sdroot_workdir}" "${uuid}" "${old_bootargs}" "${new_bootargs}" "${pac_name}"

	if [[ "${RUN_MKPAC}" == "yes" ]]; then
		printf 'run makepac.py for %s\n' "${product_dir}"
		mkpac_log="${work_dir}/makepac.log"
		set +e
		(
			cd "${tree}/prebuilts/pac_script"
			python makepac.py --out "${product_dir}"
		) 2>&1 | tee "${mkpac_log}"
		mkpac_status="${PIPESTATUS[0]}"
		set -e
		actual_pac="${product_dir}/cp_sign/${SIGN_PROFILE}/${pac_name}"
		if [[ ! -f "${actual_pac}" ]]; then
			printf 'PAC was not created: %s\n' "${actual_pac}" >&2
			exit 1
		fi
		if [[ "${mkpac_status}" -ne 0 ]]; then
			if grep -q 'do packet success' "${mkpac_log}"; then
				printf 'makepac.py exited %s after PAC creation; continuing. See %s\n' "${mkpac_status}" "${mkpac_log}" >&2
			else
				printf 'makepac.py failed before PAC creation completed. See %s\n' "${mkpac_log}" >&2
				exit "${mkpac_status}"
			fi
		fi
		(
			cd "${work_dir}"
			sha256sum "product/cp_sign/${SIGN_PROFILE}/${pac_name}" product/dtbo.img product/dtbo-sign.img build-info.txt sign-dtbo.log makepac.log > SHA256SUMS
		)
	fi

	printf 'work_dir: %s\n' "${work_dir}"
	printf 'product_dir: %s\n' "${product_dir}"
	printf 'pac_name: %s\n' "${pac_name}"
	printf 'rootfs_uuid: %s\n' "${uuid}"
	printf 'dtbo_bootargs: %s\n' "${new_bootargs}"
}

main "$@"
