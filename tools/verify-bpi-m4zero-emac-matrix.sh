#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
work_dir="${WORK_DIR:-${repo_dir}/.tmp/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
expected_releases=(bookworm trixie jammy noble resolute)
expected_profiles=(cli xfce)
expected_count=$(( ${#expected_releases[@]} * ${#expected_profiles[@]} ))

for command in awk find grep ln lsblk losetup mktemp mount mountpoint \
	sha256sum sort stat sudo udevadm umount unlink wc xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

[[ -d "${output_dir}" ]] || fail "找不到交付目錄：${output_dir}"
[[ -f "${work_dir}/MATRIX.tsv" ]] || fail "找不到矩陣清單"
grep -qx 'status=complete' "${work_dir}/COMPLETION_STATUS.txt" ||
	fail "矩陣建置狀態不是 complete"
sudo -n true || fail "唯讀掛載需要免互動 sudo"

mapfile -t images < <(find "${work_dir}" -maxdepth 1 -type f -name '*.img' -print | sort)
mapfile -t archives < <(find "${output_dir}" -maxdepth 1 -type f -name '*.img.xz' -print | sort)
mapfile -t partials < <(find "${output_dir}" "${work_dir}" -maxdepth 1 -type f -name '*.partial' -print)
[[ ${#images[@]} -eq ${expected_count} ]] ||
	fail "原始映像預期 ${expected_count} 個，實際 ${#images[@]} 個"
[[ ${#archives[@]} -eq ${expected_count} ]] ||
	fail "壓縮映像預期 ${expected_count} 個，實際 ${#archives[@]} 個"
[[ ${#partials[@]} -eq 0 ]] || fail "仍有未完成的 .partial 檔案"

row_count="$(awk 'NR > 1 && NF == 8 { count++ } END { print count + 0 }' "${work_dir}/MATRIX.tsv")"
[[ ${row_count} -eq ${expected_count} ]] ||
	fail "矩陣清單預期 ${expected_count} 筆，實際 ${row_count} 筆"

for release in "${expected_releases[@]}"; do
	for profile in "${expected_profiles[@]}"; do
		count="$(awk -F '\t' -v release="${release}" -v profile="${profile}" \
			'$1 == release && $2 == profile { count++ } END { print count + 0 }' \
			"${work_dir}/MATRIX.tsv")"
		[[ ${count} -eq 1 ]] || fail "${release} ${profile} 的矩陣紀錄數不是 1"
	done
done

mapfile -t uboot_packages < <(
	find "${repo_dir}/output/debs" -type f \
		-name 'linux-u-boot-bananapim4zeroemac-current_*arm64*.deb' -print
)
[[ ${#uboot_packages[@]} -eq 1 ]] ||
	fail "預期只有一個 M4 Zero EMAC current U-Boot 套件，實際 ${#uboot_packages[@]} 個"
uboot_deb="${uboot_packages[0]}"

package_installed() {
	local root_dir=$1
	local package=$2
	awk -v package="${package}" '
		BEGIN { RS = ""; FS = "\n" }
		{
			name = ""
			status = ""
			for (field_index = 1; field_index <= NF; field_index++) {
				if ($field_index ~ /^Package: /) name = substr($field_index, 10)
				if ($field_index ~ /^Status: /) status = substr($field_index, 9)
			}
			if (name == package && status == "install ok installed") found = 1
		}
		END { exit found ? 0 : 1 }
	' "${root_dir}/var/lib/dpkg/status"
}

validate_release_profile() (
	local image=$1
	local release=$2
	local profile=$3
	local mount_dir loop_device partition codename

	mount_dir="$(mktemp -d "${repo_dir}/.tmp/m4zero-emac-matrix-verify.XXXXXX")"
	loop_device="$(sudo -n losetup --find --show --partscan --read-only "${image}")"
	cleanup_mount() {
		if mountpoint -q "${mount_dir}"; then
			sudo -n umount "${mount_dir}" || true
		fi
		sudo -n losetup -d "${loop_device}" 2>/dev/null || true
		rmdir "${mount_dir}" 2>/dev/null || true
	}
	trap cleanup_mount EXIT
	udevadm settle
	partition="$(lsblk -nrpo NAME,TYPE "${loop_device}" | awk '$2 == "part" { print $1; exit }')"
	[[ -n "${partition}" ]] || fail "${release} ${profile} 沒有可掛載分割區"
	sudo -n mount -o ro,noload "${partition}" "${mount_dir}"

	codename="$(awk -F= '$1 == "VERSION_CODENAME" { gsub(/\"/, "", $2); print $2 }' "${mount_dir}/etc/os-release")"
	[[ "${codename}" == "${release}" ]] ||
		fail "${release} ${profile} 的根檔案系統代號為 ${codename}"
	grep -qx 'BOARD=bananapim4zeroemac' "${mount_dir}/etc/armbian-release" ||
		fail "${release} ${profile} 的板型識別不符"
	grep -qx 'BRANCH=current' "${mount_dir}/etc/armbian-release" ||
		fail "${release} ${profile} 不是 current 分支"

	if [[ "${profile}" == xfce ]]; then
		package_installed "${mount_dir}" xfce4 || fail "${release} xfce 缺少 xfce4"
		for package in gstreamer1.0-tools gstreamer1.0-plugins-bad libdrm-tests; do
			package_installed "${mount_dir}" "${package}" ||
				fail "${release} xfce 缺少 ${package}"
		done
	else
		if package_installed "${mount_dir}" xfce4; then
			fail "${release} cli 不應安裝 xfce4"
		fi
	fi
	echo "發行版與映像角色通過：${release} ${profile}"
)

while IFS=$'\t' read -r release profile raw_size raw_sha256 xz_size xz_sha256 img_filename xz_filename; do
	[[ "${release}" == release ]] && continue
	image="${work_dir}/${img_filename}"
	archive="${output_dir}/${xz_filename}"
	[[ "$(stat -c %s "${image}")" == "${raw_size}" ]] || fail "${release} ${profile} 原始大小不符"
	[[ "$(stat -c %s "${archive}")" == "${xz_size}" ]] || fail "${release} ${profile} 壓縮大小不符"
	[[ "$(sha256sum "${image}" | awk '{print $1}')" == "${raw_sha256}" ]] || fail "${release} ${profile} 原始雜湊不符"
	[[ "$(sha256sum "${archive}" | awk '{print $1}')" == "${xz_sha256}" ]] || fail "${release} ${profile} 壓縮雜湊不符"
	xz -t "${archive}"
	[[ "$(xz -dc -- "${archive}" | sha256sum | awk '{print $1}')" == "${raw_sha256}" ]] ||
		fail "${release} ${profile} 解壓串流與原始映像不一致"

	ln -s "${archive}" "${image}.xz"
	ln -s "${archive}.sha" "${image}.xz.sha"
	cleanup_links() {
		[[ ! -L "${image}.xz" ]] || unlink "${image}.xz"
		[[ ! -L "${image}.xz.sha" ]] || unlink "${image}.xz.sha"
	}
	trap cleanup_links EXIT
	"${repo_dir}/tools/verify-bpi-m4zero-emac-image.sh" "${image}" "${uboot_deb}"
	cleanup_links
	trap - EXIT
	validate_release_profile "${image}" "${release}" "${profile}"
done <"${work_dir}/MATRIX.tsv"

echo "M4 Zero EMAC 十映像矩陣全部通過唯讀驗證。"
