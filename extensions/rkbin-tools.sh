#!/usr/bin/env bash
function fetch_sources_tools__rkbin_tools() {
	local rkbin_git_ref="${RKBIN_GIT_REF:-branch:${RKBIN_GIT_BRANCH:-master}}"
	fetch_from_repo "${RKBIN_GIT_URL:-"https://github.com/armbian/rkbin"}" "rkbin-tools" "${rkbin_git_ref}"
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
