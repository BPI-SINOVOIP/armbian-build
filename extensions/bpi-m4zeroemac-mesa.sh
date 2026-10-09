#!/usr/bin/env bash
# 僅由操作者明確啟用；本地套件目錄須含 SHA256SUMS.txt。
# 不下載預製 Mesa；沿用 rootfs 原有同 OS 的 APT 來源解依賴，不允許降版。

function post_family_tweaks__bpi_m4zeroemac_mesa() {
	[[ "${BOARD:-}" == bananapim4zeroemac ]] || return 0
	local mesa_version mesa_os
	case "${RELEASE:-}" in
		bookworm) mesa_version='22.3.6-1+deb12u2+bpi1'; mesa_os=debian ;;
		jammy) mesa_version='23.2.1-1ubuntu3.1~22.04.4+bpi1'; mesa_os=ubuntu ;;
		*) return 0 ;;
	esac

	# 暫存路徑僅供本次呼叫與清理使用，不變更全域 trap。
	local mesa_stage='' mesa_result=0
	bpi_m4zeroemac_mesa_install "${mesa_version}" "${mesa_os}" || mesa_result=$?
	if [[ -n "${mesa_stage}" ]]; then
		run_host_command_logged rm -rf -- "${mesa_stage@Q}" || mesa_result=1
	fi
	if (( mesa_result != 0 )); then
		exit_with_error "本地 Mesa 成套安裝或驗證失敗；停止產出映像" "${BOARD}/${RELEASE}"
		return 1
	fi
}

function bpi_m4zeroemac_mesa_error() {
	printf '本地 Mesa：%s\n' "$*" >&2
	return 1
}

function bpi_m4zeroemac_mesa_inventory() {
	LC_ALL=C dpkg-query --admindir="$1/var/lib/dpkg" --show \
		--showformat='${Package}\t${db:Status-Status}\t${source:Package}\t${Version}\t${Architecture}\n'
}

function bpi_m4zeroemac_mesa_install() {
	local expected_version="$1" expected_os="$2"
	local deb_dir="${BPI_MESA_DEB_DIR:-}" root="${SDCARD:-}"
	root="${root%/}"
	[[ -n "${deb_dir}" && -d "${deb_dir}" ]] || {
		bpi_m4zeroemac_mesa_error '缺少 BPI_MESA_DEB_DIR 本地套件目錄'; return 1
	}
	[[ "${root}" == /* && -f "${root}/var/lib/dpkg/status" && -r "${root}/etc/os-release" ]] || {
		bpi_m4zeroemac_mesa_error 'rootfs 缺少套件狀態或 OS 識別'; return 1
	}
	local key value os_id='' os_release=''
	while IFS='=' read -r key value || [[ -n "${key}" ]]; do
		case "${key}" in
			ID) os_id="${value}" ;;
			VERSION_CODENAME) os_release="${value}" ;;
		esac
	done < "${root}/etc/os-release"
	# 僅移除 os-release 值的外層引號，不執行檔案內容。
	os_id="${os_id#\"}"; os_id="${os_id%\"}"; os_id="${os_id#\'}"; os_id="${os_id%\'}"
	os_release="${os_release#\"}"; os_release="${os_release%\"}"; os_release="${os_release#\'}"; os_release="${os_release%\'}"
	[[ "${os_id}" == "${expected_os}" && "${os_release}" == "${RELEASE}" ]] || {
		bpi_m4zeroemac_mesa_error 'rootfs OS 與指定發行版不符'; return 1
	}
	[[ -f "${deb_dir}/SHA256SUMS.txt" && ! -L "${deb_dir}/SHA256SUMS.txt" ]] || {
		bpi_m4zeroemac_mesa_error '缺少一般檔案 SHA256SUMS.txt'; return 1
	}

	local -A checksums=() package_files=() package_arches=() required=() installed=()
	local -a files=() selected=() apt_debs=()
	local line filename checksum actual package version arch source inventory status path
	local manifest_pattern='^([[:xdigit:]]{64}) [ *]([A-Za-z0-9][A-Za-z0-9.+_~-]*\.deb)$'
	local source_pattern='^mesa( \([0-9][^[:space:]()]*\))?$'
	while IFS= read -r line || [[ -n "${line}" ]]; do
		[[ "${line}" =~ ${manifest_pattern} ]] || {
			bpi_m4zeroemac_mesa_error 'SHA256SUMS.txt 格式錯誤；僅接受相對單層 deb 檔名'; return 1
		}
		checksum="${BASH_REMATCH[1],,}"; filename="${BASH_REMATCH[2]}"
		[[ -z "${checksums[${filename}]:-}" ]] || {
			bpi_m4zeroemac_mesa_error "清單檔名重複：${filename}"; return 1
		}
		checksums["${filename}"]="${checksum}"
		files+=("${filename}")
	done < "${deb_dir}/SHA256SUMS.txt"
	(( ${#files[@]} > 0 )) || { bpi_m4zeroemac_mesa_error 'SHA256SUMS.txt 為空'; return 1; }

	for path in "${deb_dir}"/*.deb "${deb_dir}"/.*.deb; do
		[[ -e "${path}" || -L "${path}" ]] || continue
		filename="${path##*/}"
		[[ -n "${checksums[${filename}]:-}" ]] || {
			bpi_m4zeroemac_mesa_error '目錄存在未列入清單的 deb'; return 1
		}
	done
	for filename in "${files[@]}"; do
		path="${deb_dir}/${filename}"
		[[ -f "${path}" && ! -L "${path}" ]] || {
			bpi_m4zeroemac_mesa_error "缺少一般 deb 檔案：${filename}"; return 1
		}
		actual="$(sha256sum < "${path}")" || return 1
		[[ "${actual%% *}" == "${checksums[${filename}]}" ]] || {
			bpi_m4zeroemac_mesa_error "SHA256 不符：${filename}"; return 1
		}
		package="$(dpkg-deb --field "${path}" Package)" || return 1
		version="$(dpkg-deb --field "${path}" Version)" || return 1
		arch="$(dpkg-deb --field "${path}" Architecture)" || return 1
		source="$(dpkg-deb --field "${path}" Source)" || return 1
		[[ "${package}" =~ ^[a-z0-9][a-z0-9+.-]+$ && "${source}" =~ ${source_pattern} ]] || {
			bpi_m4zeroemac_mesa_error "Package 或 Source 不是有效 Mesa 套件：${filename}"; return 1
		}
		[[ "${version}" == "${expected_version}" && ( "${arch}" == arm64 || "${arch}" == all ) ]] || {
			bpi_m4zeroemac_mesa_error "版本或架構不符：${filename}"; return 1
		}
		[[ -z "${package_files[${package}]:-}" ]] || {
			bpi_m4zeroemac_mesa_error "套件重複：${package}"; return 1
		}
		package_files["${package}"]="${filename}"
		package_arches["${package}"]="${arch}"
	done

	selected=(libgl1-mesa-dri libegl-mesa0 libglx-mesa0 libgbm1 libglapi-mesa)
	for package in "${selected[@]}"; do required["${package}"]=1; done
	inventory="$(bpi_m4zeroemac_mesa_inventory "${root}")" || return 1
	while IFS=$'\t' read -r package status source version arch; do
		[[ "${status}" == installed && "${source}" == mesa ]] || continue
		[[ "${arch}" == arm64 || "${arch}" == all ]] || {
			bpi_m4zeroemac_mesa_error "已裝 Mesa 架構不符：${package}"; return 1
		}
		if dpkg --compare-versions "${version}" gt "${expected_version}"; then
			bpi_m4zeroemac_mesa_error "拒絕 Mesa 降版：${package}"; return 1
		fi
		if [[ -z "${required[${package}]:-}" ]]; then
			selected+=("${package}")
			required["${package}"]=1
		fi
	done <<< "${inventory}"
	for package in "${selected[@]}"; do
		[[ -n "${package_files[${package}]:-}" ]] || {
			bpi_m4zeroemac_mesa_error "缺少必要套件：${package}"; return 1
		}
	done

	# 複製後重驗雜湊，避免驗證與安裝之間換入不同內容。
	mesa_stage="$(mktemp -d "${root}/tmp/bpi-m4zeroemac-mesa.XXXXXXXX")" || return 1
	run_host_command_logged chmod 0755 "${mesa_stage@Q}" || return 1
	local stage_file chroot_file
	for package in "${selected[@]}"; do
		filename="${package_files[${package}]}"
		path="${deb_dir}/${filename}"; stage_file="${mesa_stage}/${filename}"
		run_host_command_logged install -m 0644 -- "${path@Q}" "${stage_file@Q}" || return 1
		actual="$(sha256sum < "${stage_file}")" || return 1
		[[ "${actual%% *}" == "${checksums[${filename}]}" ]] || {
			bpi_m4zeroemac_mesa_error "複製後 SHA256 不符：${filename}"; return 1
		}
		chroot_file="${stage_file#"${root}"}"
		apt_debs+=("${chroot_file@Q}")
	done
	DONT_MAINTAIN_APT_CACHE=yes chroot_sdcard_apt_get \
		--no-remove --no-install-recommends \
		-o APT::Get::AllowUnauthenticated=false -o APT::Get::allow-downgrades=false \
		install "${apt_debs[@]}" || {
		bpi_m4zeroemac_mesa_error 'APT 交易失敗'; return 1
	}

	inventory="$(bpi_m4zeroemac_mesa_inventory "${root}")" || return 1
	while IFS=$'\t' read -r package status source version arch; do
		[[ "${status}" == installed && "${source}" == mesa ]] || continue
		[[ "${version}" == "${expected_version}" && ( "${arch}" == arm64 || "${arch}" == all ) ]] || {
			bpi_m4zeroemac_mesa_error "安裝後 Mesa 版本或架構不一致：${package}"; return 1
		}
		installed["${package}"]=1
	done <<< "${inventory}"
	for package in "${selected[@]}"; do
		[[ -n "${installed[${package}]:-}" ]] || {
			bpi_m4zeroemac_mesa_error "安裝後缺少必要 Mesa 套件：${package}"; return 1
		}
	done

	# 映像只保留相對檔名、SHA 與版本；不保留主機路徑或 deb。
	{
		for package in "${selected[@]}"; do
			filename="${package_files[${package}]}"
			printf '%s  %s\n' "${checksums[${filename}]}" "${filename}"
		done
	} > "${mesa_stage}/SHA256SUMS.txt" || return 1
	{
		printf '發行版：%s\n來源套件：mesa\n統一版本：%s\n\n本地套件：\n' "${RELEASE}" "${expected_version}"
		for package in "${selected[@]}"; do
			printf '%s\t%s\t%s\t%s\n' "${package}" "${expected_version}" "${package_arches[${package}]}" "${package_files[${package}]}"
		done
		printf '\n安裝後全部 Mesa 套件：\n'
		while IFS=$'\t' read -r package status source version arch; do
			[[ "${status}" == installed && "${source}" == mesa ]] || continue
			printf '%s\t%s\t%s\n' "${package}" "${version}" "${arch}"
		done <<< "${inventory}"
	} > "${mesa_stage}/packages.txt" || return 1
	local trace_dir="${root}/usr/share/bpi-m4zeroemac-mesa" trace_file
	run_host_command_logged mkdir -p -- "${trace_dir@Q}" || return 1
	for filename in SHA256SUMS.txt packages.txt; do
		stage_file="${mesa_stage}/${filename}"; trace_file="${trace_dir}/${filename}"
		run_host_command_logged install -m 0644 -- "${stage_file@Q}" "${trace_file@Q}" || return 1
	done
}
