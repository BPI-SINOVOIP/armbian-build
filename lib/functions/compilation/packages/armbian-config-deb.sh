#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Build official configng with the M8 installer and desktop compatibility patches.

function m8_prepare_armbian_config() {
	# Resolve main once per build, not once per consumer. Patches and assembly
	# operate on a private snapshot, never on the shared Git download cache.
	if [[ -n "${M8_CONFIGNG_SOURCE:-}" ]]; then
		[[ -f "${M8_CONFIGNG_SOURCE}/debian.conf" ]] || exit_with_error "Missing prepared configng source"
		return 0
	fi
	local cache_dir="${SRC}/cache/sources/armbian-configng-upstream"
	local fetched_revision fetched_revision_ts
	(
		GIT_FIXED_WORKDIR=armbian-configng-upstream fetch_from_repo \
			"https://github.com/armbian/configng" "armbian-configng-upstream" "branch:main"
	)
	declare -g M8_CONFIGNG_COMMIT M8_CONFIGNG_EPOCH M8_CONFIGNG_INPUT_HASH M8_CONFIGNG_VERSION
	M8_CONFIGNG_COMMIT="$(git -C "${cache_dir}" rev-parse HEAD)"
	M8_CONFIGNG_EPOCH="$(git -C "${cache_dir}" log -1 --format=%ct)"
	local -a patches=(
		"${SRC}/patch/misc/configng-sun60iw2-boot-area.patch"
		"${SRC}/patch/misc/configng-m8-desktop-packages.patch"
	)
	local patch_file
	for patch_file in "${patches[@]}"; do
		[[ -s "${patch_file}" ]] || exit_with_error "Missing M8 configng patch" "${patch_file}"
	done
	M8_CONFIGNG_INPUT_HASH="$(
		{
			printf '%s\n' "${M8_CONFIGNG_COMMIT}"
			(cd "${SRC}" && sha256sum \
				lib/functions/compilation/packages/armbian-config-deb.sh \
				lib/functions/rootfs/install-armbian-config.sh \
				lib/functions/configuration/main-config.sh \
				extensions/bpi-sun60iw2-bsp.sh \
				patch/misc/configng-{sun60iw2-boot-area,m8-desktop-packages}.patch)
		} | sha256sum | cut -d' ' -f1
	)"
	# This is an identity of this build, not a pinned upstream version.
	M8_CONFIGNG_VERSION="0~git${M8_CONFIGNG_EPOCH}.${M8_CONFIGNG_COMMIT:0:12}+m8.${M8_CONFIGNG_INPUT_HASH:0:12}"
	local snapshot
	snapshot="$(mktemp -d -t armbian-configng-m8.XXXXXXXX)"
	add_cleanup_handler "m8_cleanup_armbian_config ${snapshot@Q}"
	mkdir "${snapshot}/source"
	run_host_command_logged git -C "${cache_dir}" archive "${M8_CONFIGNG_COMMIT}" -o "${snapshot}/source.tar"
	run_host_command_logged tar -xf "${snapshot}/source.tar" -C "${snapshot}/source"
	for patch_file in "${patches[@]}"; do
		# An already-integrated patch is OK; any other upstream change must be
		# reviewed rather than silently dropping the boot-area protection.
		if (cd "${snapshot}/source" && GIT_CEILING_DIRECTORIES="${snapshot}" git apply --check "${patch_file}"); then
			(cd "${snapshot}/source" && GIT_CEILING_DIRECTORIES="${snapshot}" git apply "${patch_file}")
		elif (cd "${snapshot}/source" && GIT_CEILING_DIRECTORIES="${snapshot}" git apply --reverse --check "${patch_file}"); then
			display_alert "configng patch already present" "${patch_file##*/}" "info"
		else
			exit_with_error "Official configng changed; review M8 compatibility patch" "${patch_file##*/} @ ${M8_CONFIGNG_COMMIT}"
		fi
	done
	declare -g M8_CONFIGNG_SOURCE="${snapshot}/source"
	display_alert "Prepared official configng main for M8" "${M8_CONFIGNG_COMMIT} / ${M8_CONFIGNG_INPUT_HASH:0:12}" "info"
}

function m8_cleanup_armbian_config() {
	local snapshot="${1:?}"
	[[ "${snapshot##*/}" == armbian-configng-m8.* && -d "${snapshot}/source" ]] || return 0
	rm -rf -- "${snapshot}"
}

function m8_build_armbian_config_deb() {
	m8_prepare_armbian_config
	declare -g M8_CONFIGNG_DEB="${DEB_STORAGE}/armbian-config_${M8_CONFIGNG_VERSION}_all.deb"
	[[ -f "${M8_CONFIGNG_DEB}" ]] && return 0
	local cleanup_id build_dir package_dir
	prepare_temp_dir_in_workdir_and_schedule_cleanup "deb-m8-armbian-config" cleanup_id build_dir
	package_dir="${build_dir}/package"
	mkdir "${package_dir}"
	local depends
	# Read dependencies from the same revision as debian.conf, not a hand-kept
	# subset. Stop if upstream changes its packaging schema or build entrypoint.
	depends="$(python3 - "${M8_CONFIGNG_SOURCE}/.github/workflows/maintenance-build-debian.yml" <<-'PY'
		import sys
		import yaml
		with open(sys.argv[1]) as stream:
		    metadata = yaml.safe_load(stream)["jobs"]["Debian"]["with"]
		if metadata.get("compile") != "tools/config-assemble.sh -p":
		    raise SystemExit("Unsupported configng build entrypoint")
		depends = metadata["depends"]
		if not isinstance(depends, str) or not depends.strip() or "\n" in depends or "\r" in depends:
		    raise SystemExit("Invalid configng package dependencies")
		print(depends)
	PY
	)"
	run_host_command_logged bash "${M8_CONFIGNG_SOURCE}/tools/config-assemble.sh" -p
	local source_path destination_path extra resolved_path
	while IFS=: read -r source_path destination_path extra || [[ -n "${source_path}" ]]; do
		[[ -z "${source_path}" || "${source_path}" == \#* ]] && continue
		[[ -z "${extra}" && -n "${destination_path}" && "${source_path}" != /* && "${source_path}" != *..* &&
			"${destination_path}" == /* && "${destination_path}" != *..* ]] ||
			exit_with_error "Invalid configng package mapping" "${source_path}:${destination_path}"
		resolved_path="$(realpath -e "${M8_CONFIGNG_SOURCE}/${source_path}")"
		[[ "${resolved_path}" == "${M8_CONFIGNG_SOURCE}/"* ]] || exit_with_error "configng package source escapes snapshot"
		mkdir -p "${package_dir}${destination_path}"
		cp -a -- "${M8_CONFIGNG_SOURCE}/${source_path}" "${package_dir}${destination_path}/"
	done < "${M8_CONFIGNG_SOURCE}/debian.conf"
	[[ -x "${package_dir}/usr/bin/armbian-config" && -d "${package_dir}/usr/share/armbian-config/desktops" ]] ||
		exit_with_error "Incomplete configng package layout"
	mkdir -p "${package_dir}/DEBIAN" "${package_dir}/usr/share/doc/armbian-config"
	cp "${M8_CONFIGNG_SOURCE}/LICENSE" "${package_dir}/usr/share/doc/armbian-config/copyright"
	cat <<-EOF > "${package_dir}/DEBIAN/control"
		Package: armbian-config
		Version: ${M8_CONFIGNG_VERSION}
		Architecture: all
		Maintainer: ${MAINTAINER:-BPI} <${MAINTAINERMAIL:-support@banana-pi.org}>
		Section: admin
		Priority: optional
		Depends: ${depends}
		Homepage: https://github.com/armbian/configng
		Description: Armbian configuration utility with M8 compatibility
		Installed-Size: $(du -sk --apparent-size --exclude=DEBIAN "${package_dir}" | cut -f1)
	EOF
	if [[ -d "${package_dir}/etc" ]]; then
		find "${package_dir}/etc" -type f -printf '/etc/%P\n' | LC_ALL=C sort > "${package_dir}/DEBIAN/conffiles"
	fi
	(
		cd "${package_dir}"
		find . -type f ! -path './DEBIAN/*' -print0 | LC_ALL=C sort -z | xargs -0 md5sum |
			sed 's|  \./|  |' > DEBIAN/md5sums
	)
	chmod -R go-w "${package_dir}"
	find "${package_dir}" -exec touch -h -d "@${M8_CONFIGNG_EPOCH}" {} +
	mkdir -p "${DEB_STORAGE}"
	SOURCE_DATE_EPOCH="${M8_CONFIGNG_EPOCH}" run_host_command_logged dpkg-deb --root-owner-group -Zxz --build \
		"${package_dir}" "${build_dir}/armbian-config.deb"
	mv -- "${build_dir}/armbian-config.deb" "${M8_CONFIGNG_DEB}"
	done_with_temp_dir "${cleanup_id}"
}
