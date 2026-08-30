#!/usr/bin/env bash
# @description Fetches the Rockchip `rkbin` repository (default `armbian/rkbin`) and installs the `loaderimage` and `trust_merger` host tools into `/usr/local/bin`, reinstalling only when the git commit changes. Needed to package Rockchip U-Boot/TPL/SPL images. Override the source with `RKBIN_GIT_URL` and `RKBIN_GIT_BRANCH`.

function fetch_sources_tools__rkbin_tools() {
	local rkbin_git_source="${RKBIN_GIT_URL:-"https://github.com/armbian/rkbin"}"
	local rkbin_git_ref="${RKBIN_GIT_REF:-branch:${RKBIN_GIT_BRANCH:-master}}"
	fetch_from_repo "${rkbin_git_source}" "rkbin-tools" "${rkbin_git_ref}"
	declare -g RKBIN_GIT_SOURCE_ACTUAL="${rkbin_git_source}"
	declare -g RKBIN_GIT_REF_ACTUAL="${rkbin_git_ref}"
	declare -g RKBIN_GIT_REVISION
	RKBIN_GIT_REVISION="${checked_out_revision:-}"
	if [[ ! "${RKBIN_GIT_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
		RKBIN_GIT_REVISION="$(cd "${SRC}/cache/sources/rkbin-tools" && improved_git rev-parse HEAD)"
	fi
	[[ "${RKBIN_GIT_REVISION}" =~ ^[0-9a-f]{40}$ ]] ||
		exit_with_error "rkbin Git revision is not sane: '${RKBIN_GIT_REVISION}'"
}

function build_host_tools__install_rkbin_tools() {
	# install only if git commit hash changed
	cd "${SRC}"/cache/sources/rkbin-tools || exit
	# need to check if /usr/local/bin/loaderimage to detect new Docker containers with old cached sources
	if [[ ! -f .commit_id || $(improved_git rev-parse @ 2> /dev/null) != $(< .commit_id) || ! -f /usr/local/bin/loaderimage ]]; then
		display_alert "Installing" "rkbin-tools" "info"
		mkdir -p /usr/local/bin/
		install -m 755 tools/loaderimage /usr/local/bin/
		install -m 755 tools/trust_merger /usr/local/bin/
		improved_git rev-parse @ 2> /dev/null > .commit_id
	fi
}
