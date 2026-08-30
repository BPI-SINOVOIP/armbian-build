#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-vs680-m6-legacy}"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json}"
require_isolated_cache="${REQUIRE_ISOLATED_CACHE:-yes}"

for command in basename cut date dpkg-deb fdtget find findmnt git grep mkdir \
	mktemp mv python3 sha256sum stat tee; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "元件建置拒絕：$*" >&2
	exit 1
}

[[ -f "${validation_config}" ]] || fail "找不到驗證契約"
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "來源工作樹不是乾淨狀態"
if [[ "${require_isolated_cache}" == yes ]] &&
	[[ "$(findmnt -no FSTYPE -T "${repo_dir}/cache" 2>/dev/null || true)" != overlay ]]; then
	fail "cache 不是 OverlayFS；請使用元件隔離快取入口"
fi

"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"

mkdir -p "${output_dir}/logs" "${repo_dir}/output/debs" "${repo_dir}/.tmp"
source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
status_file="${output_dir}/COMPLETION_STATUS.json"
marker="$(mktemp "${repo_dir}/.tmp/bananapim6-components.XXXXXX.marker")"
extraction_root="$(mktemp -d "${repo_dir}/.tmp/bananapim6-components.XXXXXX.extract")"

write_status() {
	local status=$1 detail=$2
	python3 - "${status_file}" "${status}" "${detail}" "${source_commit}" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
temporary = path.with_suffix(path.suffix + ".partial")
temporary.write_text(
    json.dumps(
        {
            "status": sys.argv[2],
            "detail": sys.argv[3],
            "source_commit": sys.argv[4],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
temporary.replace(path)
PY
}

write_status in_progress "U-Boot 與 Linux 元件建置中"
remove_component_temp() {
	local target
	for target in "${marker}" "${extraction_root}"; do
		[[ -e "${target}" ]] || continue
		[[ "${target}" == "${repo_dir}/.tmp/bananapim6-components."* ]] ||
			fail "拒絕清理非 M6 專用暫存路徑：${target}"
		find "${target}" -xdev -depth -delete
	done
}
cleanup() {
	local exit_status=$?
	trap - EXIT
	remove_component_temp
	if [[ ${exit_status} -ne 0 ]]; then
		write_status failed "元件建置失敗，請檢查 logs"
	fi
	exit "${exit_status}"
}
trap cleanup EXIT

(
	cd "${repo_dir}"
	./compile.sh uboot BOARD=bananapim6 BRANCH=legacy RELEASE=trixie \
		KERNEL_CONFIGURE=no EXPERT=yes ARTIFACT_IGNORE_CACHE=yes \
		CLEAN_LEVEL=make-uboot
) |& tee "${output_dir}/logs/uboot.log"

(
	cd "${repo_dir}"
	./compile.sh kernel BOARD=bananapim6 BRANCH=legacy RELEASE=trixie \
		KERNEL_CONFIGURE=no EXPERT=yes ARTIFACT_IGNORE_CACHE=yes \
		CLEAN_LEVEL=make-kernel
) |& tee "${output_dir}/logs/kernel.log"

if find "${repo_dir}/output/images" -type f -name '*.img' -newer "${marker}" -print -quit 2>/dev/null | grep -q .; then
	fail "元件命令不應建立完整 IMG"
fi

manifest="${output_dir}/COMPONENTS.tsv"
printf 'component\tfilename\tsize\tsha256\n' >"${manifest}.partial"
while IFS= read -r -d '' package; do
	filename="$(basename "${package}")"
	case "${filename}" in
		linux-u-boot-bananapim6-legacy_*.deb) component=uboot ;;
		linux-image-legacy-vs680_*.deb) component=linux-image ;;
		linux-dtb-legacy-vs680_*.deb) component=linux-dtb ;;
		linux-headers-legacy-vs680_*.deb) component=linux-headers ;;
		linux-libc-dev-legacy-vs680_*.deb) component=linux-libc-dev ;;
		*) continue ;;
	esac
	printf '%s\t%s\t%s\t%s\n' "${component}" "${filename}" \
		"$(stat -c %s "${package}")" "$(sha256sum "${package}" | cut -d' ' -f1)" \
		>>"${manifest}.partial"
done < <(find "${repo_dir}/output/debs" -type f -name '*.deb' -newer "${marker}" -print0)

for required in uboot linux-image linux-dtb; do
	grep -q "^${required}"$'\t' "${manifest}.partial" ||
		fail "缺少必要元件封裝：${required}"
done
mv "${manifest}.partial" "${manifest}"

mapfile -t uboot_packages < <(find "${repo_dir}/output/debs" -type f \
	-name 'linux-u-boot-bananapim6-legacy_*.deb' -newer "${marker}" -print)
mapfile -t dtb_packages < <(find "${repo_dir}/output/debs" -type f \
	-name 'linux-dtb-legacy-vs680_*.deb' -newer "${marker}" -print)
[[ ${#uboot_packages[@]} -eq 1 ]] || fail "U-Boot 元件封裝不是唯一一份"
[[ ${#dtb_packages[@]} -eq 1 ]] || fail "DTB 元件封裝不是唯一一份"

dpkg-deb -x "${uboot_packages[0]}" "${extraction_root}/uboot"
dpkg-deb -x "${dtb_packages[0]}" "${extraction_root}/dtb"
uboot_dir="${extraction_root}/uboot/usr/lib/linux-u-boot-legacy-bananapim6"
uboot="${uboot_dir}/u-boot.bin"
tzk="${uboot_dir}/bpi-m6-tzk-4MB.bin"
uboot_config="${uboot_dir}/u-boot-config-target-1"
dtb="${extraction_root}/dtb/boot/dtb-5.4.195-legacy-vs680/synaptics/vs680-a0-bananapi-m6.dtb"
[[ -s "${uboot}" && -s "${tzk}" && -s "${uboot_config}" ]] ||
	fail "U-Boot 元件缺少受控二進位或設定證據"
[[ -s "${dtb}" ]] || fail "DTB 元件缺少精確 M6 DTB"
(( $(stat -c %s "${uboot}") >= 524288 )) || fail "元件 U-Boot 大小低於契約下限"
[[ "$(stat -c %s "${tzk}")" == 4193792 ]] || fail "元件 TZK 大小不符"
[[ "$(sha256sum "${tzk}" | cut -d' ' -f1)" == \
	"175e9b9313dffb70a97852ae21d855d3472916cc2af28f678ebcddc44828e411" ]] ||
	fail "元件封裝內 TZK 雜湊不符"
[[ "$(fdtget -t s "${dtb}" / model)" == "Banana Pi M6" ]] ||
	fail "元件 DTB model 不符"
dtb_compatibles="$(fdtget -t s "${dtb}" / compatible)"
for compatible in sinovoip,bananapi-m6 syna,vs680-evk syna,vs680; do
	[[ " ${dtb_compatibles} " == *" ${compatible} "* ]] ||
		fail "元件 DTB 缺少 compatible：${compatible}"
done
for required_string in 'Banana Pi M6' 'sinovoip,bananapi-m6'; do
	grep -aFq "${required_string}" "${uboot}" ||
		fail "元件 U-Boot 缺少身分字串：${required_string}"
done
for required_option in \
	'CONFIG_TARGET_VS680_C05=y' \
	'CONFIG_DEFAULT_DEVICE_TREE="vs680-bananapi-m6"' \
	'CONFIG_SYNA_INCLUDE_SM_FW=y'; do
	grep -Fqx "${required_option}" "${uboot_config}" ||
		fail "元件 U-Boot 設定不符：${required_option}"
done

python3 - "${output_dir}/COMPONENT_VERIFICATION.json" "${source_commit}" \
	"$(sha256sum "${dtb}" | cut -d' ' -f1)" \
	"$(sha256sum "${uboot}" | cut -d' ' -f1)" <<'PY'
import json
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "status": "complete",
            "evidence_level": "L1",
            "component_build_verified": True,
            "source_commit": sys.argv[2],
            "dtb_sha256": sys.argv[3],
            "uboot_sha256": sys.argv[4],
            "hardware_claims_allowed": False,
            "public_release_allowed": False,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
write_status complete "U-Boot 與 Linux 元件已完成，未建立 rootfs 或 IMG"
trap - EXIT
remove_component_temp
echo "BPI-M6 元件建置完成：${output_dir}"
